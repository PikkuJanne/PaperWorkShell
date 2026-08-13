"""PWS-0204 host capability admission tests."""

from __future__ import annotations

import platform
from dataclasses import dataclass

import pytest

from paperworkshell.worker import host
from paperworkshell.worker.protocol.models import OCR_PROCESS_CAPABILITY


@dataclass(frozen=True, slots=True)
class _AdmittedOcrHandler:
    descriptor = OCR_PROCESS_CAPABILITY


def test_host_caches_verified_windows_family_without_command_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "_uname_cache", None)

    def forbidden_probe(*_arguments: object) -> tuple[str, str, str]:
        raise AssertionError("ambient command probe must not run")

    monkeypatch.setattr(platform, "_syscmd_ver", forbidden_probe)

    host._cache_windows_platform_family()

    assert platform.system() == "Windows"


def test_host_registers_only_an_admitted_ocr_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.ocr_operation.build_ocr_process_operation_handler",
        lambda: _AdmittedOcrHandler(),
    )

    registry = host._build_operation_registry()

    assert OCR_PROCESS_CAPABILITY in registry.capabilities


def test_host_omits_ocr_when_engine_admission_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.ocr_operation.build_ocr_process_operation_handler",
        lambda: None,
    )

    registry = host._build_operation_registry()

    assert OCR_PROCESS_CAPABILITY not in registry.capabilities


def test_host_omits_ocr_when_engine_admission_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash() -> None:
        raise RuntimeError("private engine path")

    monkeypatch.setattr(
        "paperworkshell.worker.ocr_operation.build_ocr_process_operation_handler",
        crash,
    )

    registry = host._build_operation_registry()

    assert OCR_PROCESS_CAPABILITY not in registry.capabilities
