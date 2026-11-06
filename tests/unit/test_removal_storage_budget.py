"""Desktop removal composition reserves bounded source, plan and output storage."""

from dataclasses import replace
from pathlib import Path
from typing import Never
from uuid import UUID

import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.domain.pdf_export import (
    IMAGE_PDF_EXPORT_PROFILE,
    PdfExportError,
    PdfExportErrorCode,
)
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
    ResourcePolicy,
)
from paperworkshell.worker.desktop_composition import PdfExportWorkerCompositionConfig
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    REDACTION_EXPORT_CAPABILITY,
    REDACTION_EXPORT_PLAN_CONTENT_KIND,
    CaseObjectReference,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)
from paperworkshell.worker.protocol.redaction_export import redaction_export_binding
from tests.unit.test_pdf_export_entrypoint import _synthetic_export_config

_MIB = 1024 * 1024
_PLAN_BYTES = 4096
_OUTPUT = UUID("96000000-0000-4000-8000-000000000001")


def _payload(limits: ResourceLimits, *, source_bytes: int = 1024) -> OperationStartPayload:
    return OperationStartPayload(
        REDACTION_EXPORT_CAPABILITY.capability_id,
        1,
        (
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                UUID("96000000-0000-4000-8000-000000000002"),
                None,
                REDACTION_EXPORT_PLAN_CONTENT_KIND,
                _PLAN_BYTES,
                "a" * 64,
            ),
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                UUID("96000000-0000-4000-8000-000000000003"),
                None,
                "pdf",
                source_bytes,
                "b" * 64,
            ),
        ),
        tuple(
            TaggedScalar(name, ScalarValueType.INTEGER, number)
            for number, name in enumerate(
                ("source_handle", "plan_handle", "output_handle", "result_handle"), 1
            )
        )
        + (TaggedScalar("output_object_id", ScalarValueType.UUID, _OUTPUT),),
        limits,
    )


@pytest.mark.parametrize(
    ("policy", "injected_temp", "injected_output"),
    (
        (NORMAL_RESOURCE_POLICY_V1, None, None),
        (CONSTRAINED_RESOURCE_POLICY_V1, None, None),
        (
            replace(CONSTRAINED_RESOURCE_POLICY_V1, maximum_temporary_storage_bytes=64 * _MIB),
            None,
            None,
        ),
        (
            replace(CONSTRAINED_RESOURCE_POLICY_V1, maximum_temporary_storage_bytes=8 * _MIB),
            None,
            None,
        ),
        (CONSTRAINED_RESOURCE_POLICY_V1, 8 * _MIB, 4 * _MIB),
        (CONSTRAINED_RESOURCE_POLICY_V1, None, 1 * _MIB),
    ),
)
def test_removal_builder_real_storage_binding_and_lower_budgets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: ResourcePolicy,
    injected_temp: int | None,
    injected_output: int | None,
) -> None:
    central_temp = policy.maximum_temporary_storage_bytes
    resources = desktop.create_desktop_application_resource_context(policy)
    # The real production limit selection is the regression's starting point.
    limits = desktop.build_windows_pdf_export_limits(policy, profile=IMAGE_PDF_EXPORT_PROFILE)
    if injected_temp is not None:
        limits = replace(limits, temporary_storage_bytes=injected_temp)
    if injected_output is not None:
        limits = replace(limits, output_bytes=injected_output)
    synthetic = _synthetic_export_config(tmp_path)
    base = replace(synthetic, export_policy=replace(synthetic.export_policy, limits=limits))

    def configuration(**_kwargs: object) -> PdfExportWorkerCompositionConfig:
        return base

    def admitted_resources(*_args: object) -> tuple[Path, Path]:
        return tmp_path / "engine.exe", tmp_path

    monkeypatch.setattr(desktop, "build_windows_pdf_export_worker_config", configuration)
    monkeypatch.setattr(desktop, "_resource_bound_ocr_resources", admitted_resources)
    observed: list[PdfExportWorkerCompositionConfig] = []

    def capture(config: PdfExportWorkerCompositionConfig, **_kwargs: object) -> object:
        observed.append(config)
        return object()

    monkeypatch.setattr(desktop, "create_redaction_export_worker_composition", capture)
    desktop.build_windows_redaction_export_worker_composition(resources)
    config = observed[0]
    selected = config.export_policy.limits
    # This real protocol call fails before the cap fix, matching the native route.
    assert redaction_export_binding(_payload(selected)) == _OUTPUT
    expected_output = min(
        limits.output_bytes, central_temp // 4, limits.temporary_storage_bytes // 4
    )
    assert selected == replace(limits, output_bytes=expected_output)
    assert config.resource_policy.process_memory_bytes == base.resource_policy.process_memory_bytes
    assert config.resource_policy.job_memory_bytes == base.resource_policy.job_memory_bytes
    assert selected.temporary_storage_bytes == limits.temporary_storage_bytes
    # The remaining quarter is real source/plan headroom, not authority to oversubscribe.
    excessive_source = (
        selected.temporary_storage_bytes - 3 * selected.output_bytes - _PLAN_BYTES + 1
    )
    with pytest.raises(ProtocolError) as rejected:
        redaction_export_binding(_payload(selected, source_bytes=excessive_source))
    assert rejected.value.code == "RPC_INVALID_OPERATION"
    assert rejected.value.location == "payload.requested_limits"


@pytest.mark.parametrize("temporary_storage_bytes", (1, 2, 3))
def test_removal_builder_rejects_tiny_central_temp_before_admission(
    monkeypatch: pytest.MonkeyPatch, temporary_storage_bytes: int
) -> None:
    policy = replace(
        CONSTRAINED_RESOURCE_POLICY_V1, maximum_temporary_storage_bytes=temporary_storage_bytes
    )
    resources = desktop.create_desktop_application_resource_context(policy)

    def forbidden(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError(
            "No runtime selection or composition is permitted for an empty removal budget"
        )

    monkeypatch.setattr(desktop, "_resource_bound_ocr_resources", forbidden)
    monkeypatch.setattr(desktop, "build_windows_pdf_export_worker_config", forbidden)
    monkeypatch.setattr(desktop, "create_redaction_export_worker_composition", forbidden)
    with pytest.raises(PdfExportError) as rejected:
        desktop.build_windows_redaction_export_worker_composition(resources)
    assert rejected.value.code is PdfExportErrorCode.LIMIT_EXCEEDED


@pytest.mark.parametrize("temporary_storage_bytes", (2, 3))
def test_removal_builder_rejects_tiny_injected_base_temp_before_engine_admission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, temporary_storage_bytes: int
) -> None:
    resources = desktop.create_desktop_application_resource_context(CONSTRAINED_RESOURCE_POLICY_V1)
    synthetic = _synthetic_export_config(tmp_path)
    limits = replace(
        synthetic.export_policy.limits,
        temporary_storage_bytes=temporary_storage_bytes,
        output_bytes=1,
    )
    base = replace(synthetic, export_policy=replace(synthetic.export_policy, limits=limits))

    def configuration(**_kwargs: object) -> PdfExportWorkerCompositionConfig:
        return base

    def admitted_resources(*_args: object) -> tuple[Path, Path]:
        return tmp_path / "engine.exe", tmp_path

    monkeypatch.setattr(desktop, "build_windows_pdf_export_worker_config", configuration)
    monkeypatch.setattr(desktop, "_resource_bound_ocr_resources", admitted_resources)

    def forbidden(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("No engine composition is permitted for an empty removal budget")

    monkeypatch.setattr(desktop, "create_redaction_export_worker_composition", forbidden)
    with pytest.raises(PdfExportError) as rejected:
        desktop.build_windows_redaction_export_worker_composition(resources)
    assert rejected.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
