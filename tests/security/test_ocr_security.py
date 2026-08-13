"""PWS-0204 privacy, layering, and process-creation security evidence."""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.ocr import (
    OcrError,
    OcrErrorCode,
    OcrProcessRequest,
    OcrSourceEncryptionState,
    OcrSourceKind,
    OcrSourceSignatureState,
    OcrWorkerReport,
    decode_ocr_worker_report,
    reject_ocr,
)
from paperworkshell.worker.process_creation_policy import (
    WorkerProcessCreationAuditHook,
    WorkerProcessCreationDenied,
    configured_process_authorities,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "paperworkshell"


def _uuid(number: int) -> UUID:
    return UUID(f"86000000-0000-4000-8000-{number:012x}")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _render_exception(error: BaseException) -> str:
    rendered: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        rendered.extend((repr(current), str(current), repr(current.args)))
        current = current.__cause__ or current.__context__
    return "\n".join(rendered)


def test_domain_ocr_contract_has_only_pure_standard_library_imports() -> None:
    imports = _imports(SOURCE_ROOT / "domain" / "ocr.py")
    allowed_roots = {
        "__future__",
        "dataclasses",
        "enum",
        "hashlib",
        "json",
        "re",
        "typing",
        "uuid",
    }

    assert {name.split(".", maxsplit=1)[0] for name in imports} <= allowed_roots


def test_native_ocr_engines_are_imported_only_by_adapter_and_worker_layers() -> None:
    offenders: list[Path] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(SOURCE_ROOT)
        if relative.parts[0] in {"ocr", "worker"}:
            continue
        imports = _imports(path)
        if any(
            name == "ocrmypdf"
            or name.startswith("ocrmypdf.")
            or name == "paperworkshell.ocr"
            or name.startswith("paperworkshell.ocr.")
            for name in imports
        ):
            offenders.append(relative)

    assert offenders == []


def test_ocr_process_creation_exists_only_in_the_fixed_runner_and_never_uses_shell() -> None:
    ocr_files = (
        SOURCE_ROOT / "domain" / "ocr.py",
        SOURCE_ROOT / "ocr" / "ocrmypdf_adapter.py",
        SOURCE_ROOT / "ocr" / "tesseract_runner.py",
        SOURCE_ROOT / "worker" / "ocr_operation.py",
        SOURCE_ROOT / "worker" / "ocr_broker.py",
    )
    sites: list[tuple[Path, ast.Call]] = []
    for path in ocr_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr in {"Popen", "run", "call", "check_call", "check_output"}
            ):
                sites.append((path, node))

    assert [
        (path.relative_to(SOURCE_ROOT), cast(ast.Attribute, node.func).attr) for path, node in sites
    ] == [(Path("ocr/tesseract_runner.py"), "Popen")]
    shell_keywords = [
        keyword.value
        for _path, node in sites
        for keyword in node.keywords
        if keyword.arg == "shell"
    ]
    assert len(shell_keywords) == 1
    assert isinstance(shell_keywords[0], ast.Constant)
    assert shell_keywords[0].value is False


def test_worker_process_audit_hook_allows_only_exact_fixed_executable_arrays(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "approved" / "tesseract.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"synthetic")
    normalized = os.path.normcase(os.path.abspath(executable))
    hook = WorkerProcessCreationAuditHook((normalized,))
    environment = {
        "SYSTEMROOT": os.fspath(tmp_path),
        "TEMP": os.fspath(tmp_path),
        "TMP": os.fspath(tmp_path),
        "TESSDATA_PREFIX": os.fspath(tmp_path),
        "OMP_THREAD_LIMIT": "1",
    }
    valid = (
        normalized,
        (normalized, "--version"),
        os.path.dirname(normalized),
        environment,
    )

    hook("subprocess.Popen", valid)

    canary = os.path.abspath(tmp_path / "private-applicant-name.exe")
    denials = (
        (canary, (canary,), os.path.dirname(canary), environment),
        (normalized, "private-unbound-command --version", os.path.dirname(normalized), environment),
        (normalized, (normalized,), os.fspath(tmp_path), environment),
        (normalized, (normalized,), os.path.dirname(normalized), {**environment, "PATH": canary}),
    )
    for arguments in denials:
        with pytest.raises(WorkerProcessCreationDenied) as raised:
            hook("subprocess.Popen", arguments)
        assert canary not in _render_exception(raised.value)

    for event in ("os.system", "os.spawn", "os.posix_spawn"):
        with pytest.raises(WorkerProcessCreationDenied):
            hook(event, ())


def test_process_authority_configuration_rejects_relative_and_duplicate_values() -> None:
    with pytest.raises(WorkerProcessCreationDenied):
        configured_process_authorities({"PWS_TESSERACT_EXECUTABLE": "tesseract.exe"})
    absolute = os.path.abspath("synthetic-tool.exe")
    with pytest.raises(WorkerProcessCreationDenied):
        configured_process_authorities(
            {
                "PWS_QPDF_EXECUTABLE": absolute,
                "PWS_TESSERACT_EXECUTABLE": absolute,
            }
        )


def test_ocr_models_errors_and_logs_exclude_content_canaries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    private_language = "applicant-private-name-v1"
    private_profile = "private-address-v1"
    private_digest = "a" * 64
    request = OcrProcessRequest(
        case_id=_uuid(1),
        source_id=_uuid(2),
        source_kind=OcrSourceKind.PDF,
        source_byte_size=128,
        source_page_count=1,
        source_sha256=private_digest,
        signature_state=OcrSourceSignatureState.NONE_DETECTED,
        encryption_state=OcrSourceEncryptionState.UNENCRYPTED,
        language_set_id=private_language,
        profile_id=private_profile,
    )
    report = OcrWorkerReport(
        operation_id=_uuid(3),
        source_id=_uuid(2),
        output_object_id=_uuid(4),
        source_kind=OcrSourceKind.PDF,
        page_count=1,
        processed_page_count=1,
        language_set_id=private_language,
        profile_id=private_profile,
        ocrmypdf_version="17.10.0",
        tesseract_version="5.5.3",
        engine_profile_sha256="b" * 64,
        output_byte_size=64,
        output_sha256="c" * 64,
    )
    logging.getLogger("paperworkshell.tests.ocr_privacy").debug(
        "typed OCR state: %r %r", request, report
    )

    raw = RuntimeError("C:\\Applicants\\private-name\\source.pdf OCR-CONTENT-CANARY")
    try:
        raise raw
    except RuntimeError:
        with pytest.raises(OcrError) as raised:
            reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.engine")

    rendered = "\n".join(
        (repr(request), repr(report), caplog.text, _render_exception(raised.value))
    )
    for canary in (
        private_language,
        private_profile,
        private_digest,
        "private-name",
        "OCR-CONTENT-CANARY",
    ):
        assert canary not in rendered


def test_malformed_report_drops_private_payload_and_exception_chain() -> None:
    canary = b"C:\\Applicants\\private-name\\source.pdf OCR-CONTENT-CANARY"

    with pytest.raises(OcrError) as raised:
        decode_ocr_worker_report(canary)

    rendered = _render_exception(raised.value)
    assert canary.decode("ascii") not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
