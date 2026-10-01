"""Synthetic stage broker authorities, with explicit worker-exit ordering."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import cast
from uuid import UUID

from paperworkshell.domain.pdf_export import PdfExportVerificationReport
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImagePageAnalysis,
    PdfImagePageImpact,
    PdfImageStageOutcome,
    PdfImageStageReport,
    PdfImageStageRequest,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.worker.client.models import WorkerOperationResultSnapshot
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_export_broker import PdfExportSourceBatchLease
from paperworkshell.worker.pdf_image_stage_broker import (
    BrokeredPdfImageStageProcessor,
    PdfImageStageBrokerPolicy,
    VerifiedPdfImageStageCandidate,
)
from paperworkshell.worker.pdf_image_stage_wire import (
    PdfImageStageWorkerResult,
    decode_pdf_image_stage_plan,
    encode_pdf_image_stage_evidence,
    encode_pdf_image_stage_result,
)
from paperworkshell.worker.protocol.image_stage import pdf_image_stage_binding
from paperworkshell.worker.protocol.models import (
    PDF_IMAGE_STAGE_CAPABILITY,
    PDF_IMAGE_STAGE_EVIDENCE_CONTENT_KIND,
    PDF_IMAGE_STAGE_OUTPUT_CONTENT_KIND,
    CaseObjectReference,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
)
from tests.unit.worker.test_pdf_export_broker import (
    _OutputBuffer,
    _plan,
    _PlanBuffer,
    _ResultPipe,
    _SourceBatch,
    _Worker,
)


class StageWorker(_Worker):
    def __init__(self, system: StageSystem) -> None:
        super().__init__(system.plan, system.output, system.events)
        self.system = system
        self.capabilities = (PDF_IMAGE_STAGE_CAPABILITY,)

    def start_operation(
        self, payload: OperationStartPayload, *, operation_id: UUID | None = None
    ) -> UUID:
        assert operation_id is not None
        self.payload = payload
        self.progress_total = payload.requested_limits.page_count
        self.operation_id = operation_id
        output_id, evidence_id, capacity = pdf_image_stage_binding(payload)
        self.output_object_id = output_id
        assert capacity == self.system.policy.evidence_capacity_bytes
        assert self.system.plan_buffer is not None
        decoded = decode_pdf_image_stage_plan(self.system.plan_buffer.payload)
        assert decoded.base.plan == self.plan
        self.system.decoded_request = decoded.request
        evidence = self.system.evidence
        self.terminal_result = WorkerOperationResultSnapshot(
            generation=self.generation,
            operation_id=operation_id,
            outcome=self.outcome,
            outputs=(
                CaseObjectReference(
                    ReferenceArea.OPERATION_STAGING,
                    output_id,
                    operation_id,
                    PDF_IMAGE_STAGE_OUTPUT_CONTENT_KIND,
                    len(self.output.payload),
                    hashlib.sha256(self.output.payload).hexdigest(),
                ),
                CaseObjectReference(
                    ReferenceArea.OPERATION_STAGING,
                    evidence_id,
                    operation_id,
                    PDF_IMAGE_STAGE_EVIDENCE_CONTENT_KIND,
                    len(evidence.payload),
                    hashlib.sha256(evidence.payload).hexdigest(),
                ),
            )
            if self.outcome is OperationOutcome.SUCCEEDED
            else (),
            error_code=None,
            error_category=None,
        )
        header = PdfImageStageWorkerResult(
            operation_id,
            output_id,
            evidence_id,
            self.system.baseline,
            len(self.output.payload),
            hashlib.sha256(self.output.payload).hexdigest(),
            len(evidence.payload),
            hashlib.sha256(evidence.payload).hexdigest(),
        )
        self.system.pipe.raw_frame = encode_pdf_image_stage_result(header)
        return operation_id


class StageSystem:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.plan = _plan()
        self.policy = PdfImageStageBrokerPolicy(
            ResourceLimits(
                wall_time_ms=30_000,
                cpu_time_ms=20_000,
                memory_bytes=32 * 1024 * 1024,
                temporary_storage_bytes=16 * 1024 * 1024,
                output_bytes=2 * 1024 * 1024,
                page_count=10,
                pixel_count=1024 * 1024,
            )
        )
        self.output = _OutputBuffer(
            b"%PDF synthetic stage baseline\n%%EOF\n", self.policy.limits.output_bytes, self.events
        )
        digest = hashlib.sha256(self.output.payload).hexdigest()
        size = len(self.output.payload)
        base = PdfExportVerificationReport.for_verified_plan(
            self.plan, output_sha256=digest, output_byte_size=size
        )
        self.baseline = replace(
            base,
            report_version=2,
            optimization=PdfOptimizationReport(
                PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
                PdfOptimizationOutcome.NO_REDUCTION,
                size,
                None,
                size,
                0,
            ),
        )
        self.analysis = PdfImageAnalysis(
            digest,
            size,
            self.plan.page_count,
            (),
            tuple(PdfImagePageAnalysis(index, (), 0, 0) for index in range(self.plan.page_count)),
        )
        self.report = PdfImageStageReport(
            profile=None,
            outcome=PdfImageStageOutcome.UNCHANGED,
            semantic_fingerprint=self.plan.semantic_fingerprint,
            baseline_sha256=digest,
            baseline_byte_size=size,
            candidate_byte_size=None,
            output_sha256=digest,
            output_byte_size=size,
            page_count=self.plan.page_count,
            assets=(),
            pages=tuple(
                PdfImagePageImpact(index, (), 0, 0, 0) for index in range(self.plan.page_count)
            ),
            verified_page_count=self.plan.page_count,
        )
        self.evidence = _OutputBuffer(
            encode_pdf_image_stage_evidence(self.analysis, self.report),
            self.policy.evidence_capacity_bytes,
            self.events,
        )
        self.evidence.write_handle_value = 204
        self.pipe = _ResultPipe(self.plan, self.output, self.events)
        self.worker = StageWorker(self)
        self.worker.progress_total = self.policy.limits.page_count
        self.pipe.worker = self.worker
        self.sources = _SourceBatch(self.plan, self.events)
        self.sources.worker = self.worker
        self.plan_buffer: _PlanBuffer | None = None
        self.decoded_request: PdfImageStageRequest | None = None
        self.heavy = HeavyOperationCoordinator(1)
        self._buffer_calls = 0
        self.processor = BrokeredPdfImageStageProcessor(
            self.worker,
            self.policy,
            self.heavy,
            pipe_factory=lambda: self.pipe,
            plan_buffer_factory=self.make_plan_buffer,
            output_buffer_factory=self.make_output_buffer,
        )

    def make_plan_buffer(self, payload: bytes) -> _PlanBuffer:
        self.plan_buffer = _PlanBuffer(payload, self.events)
        return self.plan_buffer

    def make_output_buffer(self, capacity: int) -> _OutputBuffer:
        self._buffer_calls += 1
        result = self.output if self._buffer_calls == 1 else self.evidence
        assert result.capacity == capacity
        return result

    def process(
        self, request: PdfImageStageRequest | None = None
    ) -> VerifiedPdfImageStageCandidate:
        return self.processor.process(
            self.plan,
            cast(PdfExportSourceBatchLease, self.sources),
            PdfImageStageRequest() if request is None else request,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
