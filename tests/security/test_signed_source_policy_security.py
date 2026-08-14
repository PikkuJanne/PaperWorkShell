"""PWS-0208 signed-source privacy and authority-boundary evidence."""

from __future__ import annotations

import ast
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.app.signed_source_policy import (
    SignedSourceConfirmationChoice,
    SignedSourceConfirmationService,
)
from paperworkshell.domain.manifest_repository import (
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepository,
    ManifestSaveResult,
    ManifestSnapshot,
)
from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationSource,
    SignedSourcePolicyError,
    SignedSourcePolicyErrorCode,
    SignedSourceSignatureState,
)

CASE_ID = UUID("28000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("28000000-0000-4000-8000-000000000002")
PRIVATE_CANARY = "PRIVATE APPLICANT C:/Cases/private-source.pdf"
PRIVATE_DIGEST = "a" * 64
PRIVATE_CAS_TOKEN = "private-signed-source-cas-token"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "paperworkshell"


def _snapshot() -> ManifestSnapshot:
    document: dict[str, object] = {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": 1,
        "updated_at": "2026-08-13T10:00:00Z",
        "confirmations": [],
    }
    return ManifestSnapshot(
        CASE_ID,
        2,
        1,
        document,
        PRIVATE_DIGEST,
        PRIVATE_CAS_TOKEN,
    )


class _RawSaveFailureRepository:
    def load(self, case_id: UUID) -> ManifestLoadResult:
        assert case_id == CASE_ID
        return ManifestLoadResult(ManifestLoadStatus.CURRENT, _snapshot())

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        del candidate, expected_revision, expected_sha256, expected_cas_token
        assert case_id == CASE_ID
        raise OSError(PRIVATE_CANARY)


def test_raw_persistence_failure_does_not_leak_document_values_or_exception_chain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    repository = _RawSaveFailureRepository()
    assert isinstance(repository, ManifestRepository)
    service = SignedSourceConfirmationService(repository)
    sources = (
        SignedSourceModificationSource(
            SOURCE_ID,
            SignedSourceSignatureState.MARKERS_DETECTED,
        ),
    )
    review = service.review(CASE_ID, sources)

    with pytest.raises(SignedSourcePolicyError) as raised:
        service.decide(
            review,
            SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
        )

    rendered = "\n".join(
        (
            repr(review),
            repr(raised.value),
            str(raised.value),
            repr(raised.value.args),
            caplog.text,
        )
    )
    assert raised.value.code is SignedSourcePolicyErrorCode.SAVE_FAILED
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    for private in (
        PRIVATE_CANARY,
        "private-source.pdf",
        PRIVATE_DIGEST,
        PRIVATE_CAS_TOKEN,
    ):
        assert private not in rendered


def test_signed_source_trust_boundary_has_no_path_network_process_or_logging_surface() -> None:
    relative_paths = (
        Path("domain/signed_source_policy.py"),
        Path("app/signed_source_policy.py"),
    )
    prohibited_import_roots = {
        "logging",
        "os",
        "pathlib",
        "socket",
        "subprocess",
        "traceback",
    }
    prohibited_calls = {"breakpoint", "open", "print"}

    for relative in relative_paths:
        path = SOURCE_ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
        imports: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module.partition(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.add(node.func.id)
        assert imports.isdisjoint(prohibited_import_roots), relative
        assert calls.isdisjoint(prohibited_calls), relative
