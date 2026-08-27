from __future__ import annotations

import builtins
import copy
import hashlib
import http.client
import importlib
import json
import os
import socket
import subprocess
import tempfile
import traceback
import unittest
import urllib.request
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import (
    TemplateArtifactEvidence,
    TemplateByteSnapshot,
    TemplateKey,
    TemplateLoadError,
    TemplateLoadErrorCode,
    TemplateSignatureStatus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_TEMPLATE_PATH = PROJECT_ROOT / "schemas/examples/template-v1-minimal.json"
AS_OF = date(2026, 8, 1)
CANARY = "SYNTHETIC_TEMPLATE_SECURITY_CANARY_7F4C"
PRIVATE_PATH = rf"C:\synthetic-private\{CANARY}\case-manifest.json"


def _canonical_document() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(CANONICAL_TEMPLATE_PATH.read_text(encoding="utf-8")),
    )


def _encoded(document: dict[str, object]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _template_key(document: dict[str, object]) -> TemplateKey:
    return TemplateKey(
        cast(str, document["template_id"]),
        cast(str, document["template_version"]),
    )


def _byte_snapshot(
    document: dict[str, object],
    *,
    key: TemplateKey | None = None,
    sha256: str | None = None,
    signature_status: TemplateSignatureStatus = TemplateSignatureStatus.NOT_PROVIDED,
) -> TemplateByteSnapshot:
    payload = _encoded(document)
    selected_key = _template_key(document) if key is None else key
    return TemplateByteSnapshot(
        key=selected_key,
        content=payload,
        evidence=TemplateArtifactEvidence(
            byte_size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest() if sha256 is None else sha256,
            catalog_verified_on=AS_OF,
            signature_status=signature_status,
        ),
    )


class _RecordingRepository:
    def __init__(
        self,
        snapshot: TemplateByteSnapshot,
        *,
        case_state: dict[str, object] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.case_state = case_state
        self.read_calls = 0
        self.save_like_calls = 0

    def read(self, key: TemplateKey) -> TemplateByteSnapshot:
        self.read_calls += 1
        if key != self.snapshot.key:
            raise AssertionError("unexpected template key")
        return self.snapshot

    def save(self, *_args: object, **_kwargs: object) -> None:
        self._unexpected_save()

    def write(self, *_args: object, **_kwargs: object) -> None:
        self._unexpected_save()

    def commit(self, *_args: object, **_kwargs: object) -> None:
        self._unexpected_save()

    def _unexpected_save(self) -> None:
        self.save_like_calls += 1
        if self.case_state is not None:
            self.case_state["template_ref"] = "unexpected mutation"
        raise AssertionError("template loading attempted a save-like operation")


class _ExplodingRepository:
    def read(self, _key: TemplateKey) -> TemplateByteSnapshot:
        raise RuntimeError(f"repository leaked {CANARY} at {PRIVATE_PATH}")


class TemplateLoaderSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Authenticate and cache the packaged schema before tests replace generic file/import APIs.
        document = _canonical_document()
        repository = _RecordingRepository(_byte_snapshot(document))
        SafeTemplateLoader(repository).load(_template_key(document), as_of=AS_OF)

    def assert_error_code(
        self,
        loader: SafeTemplateLoader,
        key: TemplateKey,
        expected: TemplateLoadErrorCode,
        *,
        as_of: date = AS_OF,
    ) -> TemplateLoadError:
        with self.assertRaises(TemplateLoadError) as captured:
            loader.load(key, as_of=as_of)
        self.assertIs(expected, captured.exception.code)
        self.assertIsNone(captured.exception.__context__)
        self.assertIsNone(captured.exception.__cause__)
        return captured.exception

    def assert_canary_absent(self, value: object) -> None:
        rendered = "\n".join(
            (
                str(value),
                repr(value),
                repr(getattr(value, "args", ())),
                (
                    "".join(traceback.format_exception(value))
                    if isinstance(value, BaseException)
                    else ""
                ),
            )
        )
        self.assertNotIn(CANARY, rendered)
        self.assertNotIn(PRIVATE_PATH, rendered)

    def test_hostile_allowed_text_and_https_provenance_remain_inert(self) -> None:
        document = _canonical_document()
        instructions = cast(list[dict[str, object]], document["instructions"])
        instructions[0]["text"] = (
            f"Display $(powershell {PRIVATE_PATH}); && <script>{CANARY}</script>; "
            f"[open](file:///{PRIVATE_PATH}); __import__('os').system('{CANARY}') as plain text."
        )
        provenance = cast(dict[str, object], document["provenance"])
        provenance["source_url"] = (
            f"https://127.0.0.1:65535/templates/{CANARY}?next=file%3A%2F%2Fsynthetic"
        )
        repository = _RecordingRepository(_byte_snapshot(document))
        loader = SafeTemplateLoader(repository)

        targets: list[tuple[object, str]] = [
            (builtins, "eval"),
            (builtins, "exec"),
            (builtins, "compile"),
            (builtins, "__import__"),
            (builtins, "open"),
            (importlib, "import_module"),
            (Path, "open"),
            (Path, "read_bytes"),
            (Path, "read_text"),
            (Path, "write_bytes"),
            (Path, "write_text"),
            (os, "open"),
            (os, "system"),
            (os, "popen"),
            (subprocess, "Popen"),
            (subprocess, "run"),
            (subprocess, "call"),
            (subprocess, "check_call"),
            (subprocess, "check_output"),
            (socket, "create_connection"),
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (urllib.request, "urlopen"),
            (http.client.HTTPConnection, "connect"),
            (http.client.HTTPConnection, "request"),
        ]
        if hasattr(os, "startfile"):
            targets.append((os, "startfile"))

        mocks: list[Mock] = []
        with ExitStack() as stack:
            for owner, attribute in targets:
                mocked = stack.enter_context(
                    patch.object(
                        owner,
                        attribute,
                        side_effect=AssertionError(f"unexpected side effect: {attribute}"),
                    )
                )
                mocks.append(mocked)
            loaded = loader.load(_template_key(document), as_of=AS_OF)

        loaded_instructions = cast(list[dict[str, object]], loaded.document["instructions"])
        loaded_provenance = cast(dict[str, object], loaded.document["provenance"])
        self.assertIn(CANARY, cast(str, loaded_instructions[0]["text"]))
        self.assertIn(CANARY, cast(str, loaded_provenance["source_url"]))
        self.assertEqual(1, repository.read_calls)
        for mocked in mocks:
            mocked.assert_not_called()

    def test_prohibited_members_fail_at_nested_boundaries_without_disclosure(self) -> None:
        root_script = _canonical_document()
        root_script["script"] = f"$(powershell {PRIVATE_PATH}) {CANARY}"

        nested_import = _canonical_document()
        nested_instructions = cast(list[dict[str, object]], nested_import["instructions"])
        nested_instructions[0]["IMPORT"] = f"synthetic.module.{CANARY}"

        nested_path = _canonical_document()
        nested_provenance = cast(dict[str, object], nested_path["provenance"])
        nested_provenance["file.path"] = PRIVATE_PATH

        eligibility = _canonical_document()
        requirements = cast(list[dict[str, object]], eligibility["requirements"])
        requirements[0]["eligible-if"] = f"synthetic condition {CANARY}"

        trust = _canonical_document()
        output = cast(dict[str, object], trust["output"])
        output["is.trusted"] = CANARY

        cases = (
            ("root-script", root_script, TemplateLoadErrorCode.EXECUTABLE_MEMBER),
            ("nested-import", nested_import, TemplateLoadErrorCode.EXECUTABLE_MEMBER),
            ("nested-path", nested_path, TemplateLoadErrorCode.EXECUTABLE_MEMBER),
            ("eligibility", eligibility, TemplateLoadErrorCode.ELIGIBILITY_MEMBER),
            ("trust", trust, TemplateLoadErrorCode.TRUST_ASSERTION_MEMBER),
        )
        for label, document, expected in cases:
            with self.subTest(label=label):
                repository = _RecordingRepository(_byte_snapshot(document))
                error = self.assert_error_code(
                    SafeTemplateLoader(repository),
                    _template_key(document),
                    expected,
                )
                self.assert_canary_absent(error)
                self.assertEqual(1, repository.read_calls)
                self.assertEqual(0, repository.save_like_calls)

    def test_hostile_values_paths_and_digests_are_absent_from_errors_and_repr(self) -> None:
        unknown = _canonical_document()
        unknown[CANARY] = PRIVATE_PATH
        repository = _RecordingRepository(_byte_snapshot(unknown))
        error = self.assert_error_code(
            SafeTemplateLoader(repository),
            _template_key(unknown),
            TemplateLoadErrorCode.SCHEMA_INVALID,
        )
        self.assert_canary_absent(error)

        document = _canonical_document()
        document["title"] = f"Synthetic title {CANARY}"
        provenance = cast(dict[str, object], document["provenance"])
        provenance["publisher"] = f"Synthetic publisher {CANARY}"
        snapshot = _byte_snapshot(document)
        loaded = SafeTemplateLoader(_RecordingRepository(snapshot)).load(
            _template_key(document),
            as_of=AS_OF,
        )
        for value in (
            snapshot,
            snapshot.evidence,
            loaded,
            loaded.presentation,
            *loaded.presentation.fields,
        ):
            self.assert_canary_absent(value)
        self.assertNotIn(snapshot.evidence.sha256, repr(snapshot))
        self.assertNotIn(snapshot.evidence.sha256, repr(snapshot.evidence))
        self.assertNotIn(snapshot.evidence.sha256, repr(loaded))

        exploding = SafeTemplateLoader(_ExplodingRepository())
        repository_error = self.assert_error_code(
            exploding,
            _template_key(document),
            TemplateLoadErrorCode.REPOSITORY_UNAVAILABLE,
        )
        self.assert_canary_absent(repository_error)

        malformed_payload = (
            b'{"schema_version":1,"title":"' + CANARY.encode("ascii") + b'","unterminated":'
        )
        malformed_snapshot = TemplateByteSnapshot(
            key=_template_key(document),
            content=malformed_payload,
            evidence=TemplateArtifactEvidence(
                byte_size=len(malformed_payload),
                sha256=hashlib.sha256(malformed_payload).hexdigest(),
                catalog_verified_on=AS_OF,
            ),
        )
        malformed_error = self.assert_error_code(
            SafeTemplateLoader(_RecordingRepository(malformed_snapshot)),
            malformed_snapshot.key,
            TemplateLoadErrorCode.JSON_INVALID,
        )
        self.assert_canary_absent(malformed_error)

    def test_failed_loads_leave_case_sentinel_and_state_unchanged_without_save_calls(self) -> None:
        sentinel_bytes = f"synthetic case sentinel {CANARY}".encode()
        initial_state: dict[str, object] = {
            "revision": 11,
            "template_ref": None,
            "sentinel": CANARY,
        }

        checksum_document = _canonical_document()
        checksum_snapshot = _byte_snapshot(checksum_document, sha256="0" * 64)

        script_document = _canonical_document()
        script_document["command"] = f"type {PRIVATE_PATH} & echo {CANARY}"
        script_snapshot = _byte_snapshot(script_document)

        expired_document = _canonical_document()
        lifecycle = cast(dict[str, object], expired_document["lifecycle"])
        lifecycle.update(
            {
                "verified_on": "2025-01-01",
                "review_due_on": "2025-06-01",
                "expires_on": "2025-12-31",
            }
        )
        expired_snapshot = _byte_snapshot(expired_document)

        cases = (
            ("checksum", checksum_snapshot, TemplateLoadErrorCode.INTEGRITY_MISMATCH),
            ("script", script_snapshot, TemplateLoadErrorCode.EXECUTABLE_MEMBER),
            ("expired", expired_snapshot, TemplateLoadErrorCode.EXPIRED),
        )
        with tempfile.TemporaryDirectory() as temporary:
            case_root = Path(temporary) / "synthetic-case"
            case_root.mkdir()
            manifest_path = case_root / "manifest.json"
            manifest_path.write_bytes(sentinel_bytes)
            for label, template_snapshot, expected in cases:
                with self.subTest(label=label):
                    state = copy.deepcopy(initial_state)
                    repository = _RecordingRepository(template_snapshot, case_state=state)
                    error = self.assert_error_code(
                        SafeTemplateLoader(repository),
                        template_snapshot.key,
                        expected,
                    )
                    self.assert_canary_absent(error)
                    self.assertEqual(initial_state, state)
                    self.assertEqual(0, repository.save_like_calls)
                    self.assertEqual(sentinel_bytes, manifest_path.read_bytes())

    def test_caller_owned_ambient_exception_is_suppressed_from_rendered_traceback(self) -> None:
        ambient_canary = rf"AMBIENT_{CANARY} C:\private\case\manifest.json"
        rendered = ""
        suppress_context = False
        try:
            raise RuntimeError(ambient_canary)
        except RuntimeError:
            try:
                SafeTemplateLoader(
                    _RecordingRepository(_byte_snapshot(_canonical_document()))
                ).load(
                    _template_key(_canonical_document()),
                    as_of=date(2026, 7, 31),
                )
            except TemplateLoadError as caught:
                suppress_context = caught.__suppress_context__
                rendered = "".join(traceback.format_exception(caught))
            else:  # pragma: no cover - explicit failure oracle
                self.fail("expected an invalid lifecycle error")

        self.assertTrue(suppress_context)
        self.assertNotIn(ambient_canary, rendered)
        self.assertNotIn(CANARY, rendered)


if __name__ == "__main__":
    unittest.main()
