"""PWS-0506 versioned policy survives transport, retries, and package selection."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from paperworkshell.domain.pdf_export import (
    DEFAULT_PDF_EXPORT_PROFILE,
    IMAGE_PDF_EXPORT_PROFILE,
    PORTAL_PDF_EXPORT_PROFILE,
    PdfExportCheckCode,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportProfile,
    PdfExportVerificationReport,
    build_pdf_export_plan,
)
from paperworkshell.domain.pdf_export_history import (
    build_pdf_export_history_record_for_commit,
    build_pdf_export_history_retry_receipt,
)
from paperworkshell.domain.pdf_export_retry import (
    PdfExportRetryJournalEntry,
    PdfExportRetryPhase,
    canonical_pdf_export_retry_entry_bytes,
    decode_pdf_export_retry_entry,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportError,
    SearchablePdfExportPlan,
    SearchablePdfExportProfile,
    SearchablePdfVerificationReport,
)
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
)
from paperworkshell.worker.flattened_pdf_export_plan import (
    decode_flattened_pdf_worker_plan,
    encode_flattened_pdf_worker_plan,
)
from paperworkshell.worker.pdf_export_broker import _require_report
from paperworkshell.worker.pdf_export_plan import (
    decode_pdf_export_worker_plan,
    encode_pdf_export_worker_plan,
)
from paperworkshell.worker.pdf_export_report import (
    PdfExportWorkerReport,
    decode_pdf_export_worker_report,
    encode_pdf_export_worker_report,
)
from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    PORTAL_PDF_EXPORT_PLAN_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
)
from paperworkshell.worker.searchable_pdf_export_plan import (
    decode_searchable_pdf_worker_plan,
    encode_searchable_pdf_worker_plan,
)
from tests.unit.test_package_pdf_export import (
    IMAGE_PAGE_ID,
    PDF_PAGE_ID,
    _planned,
    _Stack,
)
from tests.unit.test_package_pdf_export import (
    _document as _package_document,
)
from tests.unit.test_package_pdf_export import (
    _snapshot as _package_snapshot,
)
from tests.unit.test_pdf_export import _document, _snapshot, _source_record
from tests.unit.test_target_size_pdf_export import _TargetStack
from tests.unit.worker.flattened_pdf_export_fixtures import synthetic_flattened_plan
from tests.unit.worker.pdf_image_stage_fixtures import StageSystem
from tests.unit.worker.test_pdf_export_protocol import _payload
from tests.unit.worker.test_searchable_pdf_export_wire import (
    _outcome,
)
from tests.unit.worker.test_searchable_pdf_export_wire import (
    _plan as _searchable_plan,
)


def _with_profile(plan: PdfExportPlan, profile: PdfExportProfile) -> PdfExportPlan:
    return PdfExportPlan(
        case_id=plan.case_id,
        manifest_revision=plan.manifest_revision,
        manifest_sha256=plan.manifest_sha256,
        manifest_cas_token=plan.manifest_cas_token,
        sources=plan.sources,
        pages=plan.pages,
        profile=profile,
        plan_version=profile.profile_version,
    )


def _portal_plan() -> PdfExportPlan:
    return build_pdf_export_plan(_snapshot(_document()), profile=PORTAL_PDF_EXPORT_PROFILE)


def _report(plan: PdfExportPlan, *, optimized: bool = False) -> PdfExportVerificationReport:
    report = PdfExportVerificationReport.for_verified_plan(
        plan, output_sha256="d" * 64, output_byte_size=80
    )
    if not optimized:
        return report
    return replace(
        report,
        report_version=4 if plan.profile.is_portal else 2,
        optimization=PdfOptimizationReport(
            PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
            PdfOptimizationOutcome.OPTIMIZED,
            100,
            80,
            80,
            plan.page_count,
        ),
    )


@pytest.mark.parametrize(
    "profile", (DEFAULT_PDF_EXPORT_PROFILE, IMAGE_PDF_EXPORT_PROFILE, PORTAL_PDF_EXPORT_PROFILE)
)
def test_supported_plan_versions_round_trip_and_bind_distinct_fingerprints(
    profile: PdfExportProfile,
) -> None:
    legacy = build_pdf_export_plan(_snapshot(_document()))
    plan = _with_profile(legacy, profile)
    payload = encode_pdf_export_worker_plan(
        plan, {source.source_id: 100 + index for index, source in enumerate(plan.sources)}
    )
    assert decode_pdf_export_worker_plan(payload).plan == plan
    assert plan.profile.is_portal is (profile.profile_version == 3)
    assert (plan.semantic_fingerprint == legacy.semantic_fingerprint) is (profile == legacy.profile)
    for version in (0, 1, 2, 3, 4, True):
        if type(version) is int and version == profile.profile_version:
            continue
        with pytest.raises(PdfExportError):
            replace(profile, profile_version=version)
        document = json.loads(payload)
        document["plan_version"] = version
        with pytest.raises(PdfExportError):
            decode_pdf_export_worker_plan(json.dumps(document).encode("ascii"))


def test_portal_manifest_accepts_image_sources_and_authenticates_media_type() -> None:
    document = _document()
    _source_record(document, 1).update(
        detected_media_type="image/png",
        signature_state="not_applicable",
        encryption_state="not_applicable",
    )
    plan = build_pdf_export_plan(_snapshot(document), profile=PORTAL_PDF_EXPORT_PROFILE)
    payload = encode_pdf_export_worker_plan(
        plan, {source.source_id: 100 + index for index, source in enumerate(plan.sources)}
    )
    assert decode_pdf_export_worker_plan(payload).plan == plan
    tampered = json.loads(payload)
    tampered["sources"][0]["media_type"] = "image/jpeg"
    with pytest.raises(PdfExportError):
        decode_pdf_export_worker_plan(json.dumps(tampered).encode("ascii"))


def test_portal_operation_requires_its_exact_capability_and_plan_kind() -> None:
    legacy = _payload()
    portal = replace(
        legacy,
        operation_version=3,
        inputs=(
            replace(legacy.inputs[0], content_kind=PORTAL_PDF_EXPORT_PLAN_CONTENT_KIND),
            replace(legacy.inputs[1], content_kind="png"),
        ),
    )
    DEFAULT_OPERATION_REGISTRY.validate_start(portal)
    for version in (1, 2, 4):
        with pytest.raises(ProtocolError):
            DEFAULT_OPERATION_REGISTRY.validate_start(replace(portal, operation_version=version))


@pytest.mark.parametrize("portal", (False, True))
@pytest.mark.parametrize("optimized", (False, True))
def test_report_versions_round_trip_and_reject_future_or_missing_policy_evidence(
    portal: bool, optimized: bool
) -> None:
    plan = _portal_plan()
    if not portal:
        plan = _with_profile(plan, DEFAULT_PDF_EXPORT_PROFILE)
    report = _report(plan, optimized=optimized)
    worker = PdfExportWorkerReport(uuid4(), uuid4(), report)
    payload = encode_pdf_export_worker_report(worker)
    assert decode_pdf_export_worker_report(payload) == worker
    assert report.report_version == (3 if portal else 1) + int(optimized)
    assert report.portal_profile_verified is portal
    document = json.loads(payload)
    document["report"]["report_version"] = 5
    with pytest.raises(PdfExportError):
        decode_pdf_export_worker_report(json.dumps(document).encode("ascii"))
    if portal:
        with pytest.raises(PdfExportError):
            replace(report, report_version=2 if optimized else 1)
        with pytest.raises(PdfExportError):
            replace(report, checks=report.checks[:-1])


@pytest.mark.parametrize("optimized", (False, True))
def test_broker_rejects_stripped_portal_report_even_with_matching_fingerprint(
    optimized: bool,
) -> None:
    plan = _portal_plan()
    portal = _report(plan, optimized=optimized)
    stripped = replace(portal, checks=portal.checks[:-1], report_version=2 if optimized else 1)
    worker = PdfExportWorkerReport(uuid4(), uuid4(), stripped)
    output = CaseObjectReference(
        ReferenceArea.OPERATION_STAGING,
        worker.output_object_id,
        worker.operation_id,
        "pdf",
        stripped.output_byte_size,
        stripped.output_sha256,
    )
    with pytest.raises(PdfExportError) as caught:
        _require_report(
            worker,
            plan=plan,
            operation_id=worker.operation_id,
            output_object_id=worker.output_object_id,
            output=output,
        )
    assert caught.value.code is PdfExportErrorCode.REPORT_INVALID


def test_searchable_nested_portal_profile_round_trip_and_report_binding() -> None:
    legacy = _searchable_plan()
    plan = SearchablePdfExportPlan(
        case_id=legacy.case_id,
        manifest_revision=legacy.manifest_revision,
        manifest_sha256=legacy.manifest_sha256,
        manifest_cas_token=legacy.manifest_cas_token,
        sources=legacy.sources,
        pages=legacy.pages,
        profile=SearchablePdfExportProfile(pdf_profile=PORTAL_PDF_EXPORT_PROFILE),
    )
    payload = encode_searchable_pdf_worker_plan(plan, {plan.sources[0].source_id: 101})
    assert decode_searchable_pdf_worker_plan(payload).plan == plan
    for version in (1, 2, 4, True):
        document = json.loads(payload)
        document["profile"]["pdf_profile_version"] = version
        with pytest.raises(SearchablePdfExportError):
            decode_searchable_pdf_worker_plan(json.dumps(document).encode("ascii"))
    report = SearchablePdfVerificationReport.for_verified_plan(
        plan, output_sha256="d" * 64, output_byte_size=80, page_outcomes=(_outcome(),)
    )
    assert report.base_report.portal_profile_verified
    stripped = replace(report.base_report, checks=report.base_report.checks[:-1], report_version=1)
    with pytest.raises(SearchablePdfExportError):
        replace(report, base_report=stripped)


def test_flattened_nested_portal_profile_round_trip_and_report_binding() -> None:
    legacy = synthetic_flattened_plan()
    plan = FlattenedPdfExportPlan(
        base_plan=_with_profile(legacy.base_plan, PORTAL_PDF_EXPORT_PROFILE),
        pages=legacy.pages,
        assets=legacy.assets,
    )
    payload = encode_flattened_pdf_worker_plan(
        plan, {plan.base_plan.sources[0].source_id: 101}, {plan.assets[0].asset_id: 103}
    )
    assert decode_flattened_pdf_worker_plan(payload).plan == plan
    report = FlattenedPdfExportVerificationReport.for_verified_plan(
        plan, output_sha256="d" * 64, output_byte_size=80
    )
    assert report.base_report.portal_profile_verified
    stripped = replace(report.base_report, checks=report.base_report.checks[:-1], report_version=1)
    with pytest.raises(FlattenedPdfExportError):
        replace(report, base_report=stripped)


@pytest.mark.parametrize("optimized", (False, True))
@pytest.mark.parametrize("phase", tuple(PdfExportRetryPhase))
def test_portal_history_retry_recovers_only_the_exact_legacy_facts(
    optimized: bool, phase: PdfExportRetryPhase
) -> None:
    plan = _portal_plan()
    report = _report(plan, optimized=optimized)
    record = build_pdf_export_history_record_for_commit(
        plan,
        export_id=uuid4(),
        report=report,
        recorded_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
    )
    assert PdfExportCheckCode.PORTAL_PROFILE_VERIFIED not in record.check_codes
    receipt = build_pdf_export_history_retry_receipt(plan, record, report)
    payload = canonical_pdf_export_retry_entry_bytes(PdfExportRetryJournalEntry(phase, receipt))
    recovered = decode_pdf_export_retry_entry(payload).receipt
    assert recovered.record_payload == receipt.record_payload
    assert recovered.report.output_sha256 == report.output_sha256
    assert recovered.report.semantic_fingerprint == report.semantic_fingerprint
    assert recovered.report.report_version == 1
    assert not recovered.report.portal_profile_verified
    assert recovered.report.optimization is None
    assert b"PORTAL_PROFILE_VERIFIED" not in payload


@pytest.mark.parametrize("page_id", (PDF_PAGE_ID, IMAGE_PAGE_ID))
def test_package_selected_pipeline_and_rebuilt_plan_keep_portal_profile(page_id: UUID) -> None:
    stack = _Stack()
    with stack.producer.produce(
        _package_snapshot(_package_document(overlays=page_id == PDF_PAGE_ID)),
        _planned(page_id),
        deadline=lambda: False,
        cancellation=lambda: False,
    ) as candidate:
        assert candidate.report.base_report.portal_profile_verified


def test_target_size_plain_image_retains_searchable_fallback() -> None:
    stack = _TargetStack()
    document = _package_document(overlays=False)
    document["page_operations"] = []
    document["page_sequence"] = document["page_sequence_base"]
    with stack.candidate(_package_snapshot(document), _planned(IMAGE_PAGE_ID)) as candidate:
        assert candidate.report.pipeline == "searchable"
        assert candidate.report.base_report.portal_profile_verified
    assert stack.processor.requests == []


def test_image_stage_broker_rejects_matching_legacy_header_for_portal_request() -> None:
    system = StageSystem()
    system.plan = _with_profile(system.plan, PORTAL_PDF_EXPORT_PROFILE)
    system.worker.plan = system.plan
    system.baseline = replace(
        system.baseline, semantic_fingerprint=system.plan.semantic_fingerprint
    )
    with pytest.raises(PdfExportError) as caught:
        system.process()
    assert caught.value.code is PdfExportErrorCode.REPORT_INVALID
    assert caught.value.context == "image_stage.header_binding"
    assert system.worker.terminated
    assert system.output.close_calls == system.evidence.close_calls == 1
    assert system.heavy.snapshot().active_operations == 0
