# ADR 0007: Replace mandatory physical low-spec hardware gate with enforceable resource budgets

- Status: Accepted
- Proposed: 2026-08-12
- Accepted: 2026-08-12
- Task: PWS-0115
- Decisions affected: PWS-D004 (narrow low-resource consequence amended), PWS-D013 (preserved), PWS-D041 (new binding decision)

## Context

The original Milestone 1 gate required evidence from an exact low-spec Windows environment: two processors, 4 GiB assigned RAM, integrated graphics, a bounded work volume, and physical rotational storage or independently validated emulation. That external environment is unavailable to this project and cannot be attested honestly. Retaining it would permanently block otherwise complete worker, import, organizer, export, recovery, privacy, and accessibility work.

The project still needs a strong low-resource engineering contract. Removing all performance checks, relying on developer impressions, or relabeling an ordinary workstation or SSD-backed virtual machine as equivalent would weaken that contract and create a misleading claim.

The maintainer explicitly approved this narrow frozen-decision change on 2026-08-12. The ADR was proposed and accepted within the same authorized change. No unrelated product boundary is changed.

## Decision

Physical low-spec, integrated-GPU, and HDD validation becomes optional and non-gating. PaperWorkShell retains Windows-first delivery and a low-resource compatibility target through a central, immutable, versioned resource policy enforced by production composition and exercised by deterministic automated tests.

The constrained profile enforces and reports bounded memory, temporary storage, caches, queues, heavy-operation concurrency, preview demand and dimensions, chunked I/O, UI responsiveness, cancellation, and low-disk behavior. The canonical M1 vertical slice remains mandatory and must pass under that profile. Reports contain only software identity, numeric resource evidence, stable results, and explicit limitations; they contain no document content or physical-hardware equivalence claim.

Existing PWS-D013 worker CPU, memory, timeout, page-count, pixel, filesystem, network, and temporary-storage requirements remain mandatory. Security, privacy, accessibility, immutable originals, atomic persistence, crash recovery, and verified export are unchanged.

## Initial constrained profile

- Heavy document-operation concurrency: 1
- Preview prefetch radius: at most one adjacent page in each direction
- Combined thumbnail/preview cache: 128 MiB by byte cost, divided into a 64-MiB
  case-scoped disposable-thumbnail share and a 64-MiB decoded-preview LRU
- Preview pixel area: at most 4 megapixels per page
- Import copy/hash chunk: 1 MiB (permitted range 256 KiB–4 MiB)
- Canonical M1 process-tree peak resident-memory budget: 1.25 GiB
- Export free-space safety reserve: 256 MiB
- Controlled UI heartbeat gap: at most 250 ms
- Cancellation acknowledgement: at most 2 seconds or the next documented safe page/stage boundary
- Active full-resolution page buffers: 1 unless a separately documented stage proves a need for 2
- Resource sampling interval: 50 ms, with short-lived-process and worker-private temporary-storage sampling limitations disclosed; worker operation/Job limits remain enforced

Numeric changes require evidence and review. A budget must not be raised merely to make a regression pass.

## Alternatives considered

### Buy or borrow an HDD or low-end computer

Rejected as a mandatory gate because the environment is unavailable and external procurement cannot be a permanent development prerequisite. Such testing remains welcome as optional evidence.

### Use an SSD-hosted VM with unvalidated throttling

Rejected because it does not prove rotational-media behavior or physical equivalence. A VM can still run the machine-independent constrained suite, but it receives no hardware-certification claim.

### Build and independently validate an HDD emulator

Rejected as disproportionate infrastructure work unrelated to PaperWorkShell's document-safety objectives. Deterministic slow-I/O and disk-fault injection provide repeatable software evidence without claiming media emulation.

### Remove performance checks entirely

Rejected because unbounded memory, cache, queue, process, and temporary-storage behavior would be a correctness and denial-of-service risk.

### Retain manual developer impressions only

Rejected because impressions are not reproducible, machine-readable, or suitable for regression gates.

## Impacts

### Security

The change strengthens resource-exhaustion controls and does not relax AppContainer isolation, network denial, ACL validation, hostile-input limits, qpdf identity checks, or export verification. Resource exhaustion fails with stable sanitized errors and cannot modify originals or publish a successful-looking corrupt derivative.

### Privacy

Resource evidence is a closed, sanitized model. It excludes filenames, paths, OCR or document text, form values, metadata, images, signatures, names, and addresses. Existing diagnostic canary scans remain mandatory.

### Accessibility

Controlled heartbeat and cancellation evidence become hard software-budget checks. Progress remains incremental and accessible, and long storage/document operations remain outside the GUI event loop.

### Performance

Budgets test bounded software behavior on ordinary development and CI systems. Duration is recorded but is not a cross-machine pass/fail target. Actual completion time still varies with document complexity and hardware.

### Packaging and licensing

No runtime dependency is added. No GPU runtime is required. Existing pinned renderer, qpdf, Qt, and worker packaging rules remain unchanged.

### Migration and task graph

PWS-0115 implements the policy and becomes a dependency of PWS-0114. PWS-0114 remains the M1 gate but uses the constrained resource report instead of hardware attestation. PWS-0209, PWS-0409, and PWS-0807 use the same policy family for later workloads. PWS-0201 becomes ready only after the revised M1 gate passes.

## Honest-claims policy

Passing the constrained suite demonstrates that the tested revision obeyed the stated software budgets for the approved synthetic workload. It does not emulate or certify a particular CPU, RAM configuration, GPU, SSD, HDD, or physical device. Optional future device observations must identify the actual environment and remain non-gating unless a later explicit decision changes this contract.

## Affected surfaces

- Governance and task graph: `DECISIONS.md`, `ROADMAP.md`, `TASKS.yaml`,
  `BUNDLE_MANIFEST.json`, `AGENTS.md`, and `CODEX_START_PROMPT.md`. PWS-D004's
  Windows-first contract is preserved and its former physical-gate consequence is amended by new
  PWS-D041; PWS-D013 is preserved. The exact task changes are revised PWS-0003; new PWS-0115; and
  revised PWS-0114, PWS-0208, PWS-0209, PWS-0409, and PWS-0807.
- Product code: `src/paperworkshell/__main__.py`, `src/paperworkshell/app/pdf_export.py`,
  `src/paperworkshell/domain/pdf_export.py`, `src/paperworkshell/domain/resource_policy.py`,
  `src/paperworkshell/export/pdf_destination.py`, `src/paperworkshell/pdf/pdfium_renderer.py`,
  `src/paperworkshell/storage/export_destination.py`, `src/paperworkshell/storage/quotas.py`,
  `src/paperworkshell/storage/workspace.py`, `src/paperworkshell/ui/models.py`,
  `src/paperworkshell/ui/window.py`, `src/paperworkshell/worker/desktop_composition.py`,
  `src/paperworkshell/worker/heavy_operations.py`,
  `src/paperworkshell/worker/pdf_export_broker.py`,
  `src/paperworkshell/worker/pdf_source_inspector.py`, and
  `src/paperworkshell/worker/pdf_thumbnail_broker.py`.
- Tools, workloads, and schemas: `tools/dev.py`, `tools/validate_schemas.py`,
  `tools/m1_vertical_slice_benchmark.py`, `tools/m1_vertical_slice_live_runner.py`,
  `tools/pdf_renderer_benchmark.py`, `benchmarks/pdf-renderer-windows-v1.json`, the immutable
  historical `schemas/m1-vertical-slice-benchmark-report-v1.schema.json`, and current
  `schemas/m1-vertical-slice-benchmark-report-v2.schema.json`. The obsolete
  `tools/reference_profile.py`, `benchmarks/reference-windows-v1.json`, and
  `schemas/reference-profile.schema.json` are removed together with their hardware-attestation
  command surface.
- Automated evidence: `tests/helpers/storage_delay.py`;
  `tests/unit/test_resource_policy.py`, `tests/unit/test_storage_delay.py`,
  `tests/unit/test_m1_vertical_slice_benchmark.py`,
  `tests/unit/test_m1_vertical_slice_live_runner.py`,
  `tests/unit/test_pdf_renderer_benchmark.py`, `tests/unit/test_organizer_ui_model.py`,
  `tests/unit/test_pdf_export_entrypoint.py`, `tests/unit/test_pdf_export_service.py`,
  `tests/unit/test_dev_commands.py`, `tests/unit/test_validate_schemas.py`,
  `tests/unit/test_validate_tasks.py`, `tests/unit/worker/test_client_process.py`,
  `tests/unit/worker/test_desktop_composition.py`,
  `tests/unit/worker/test_heavy_operations.py`,
  `tests/unit/worker/test_pdf_export_broker.py`,
  `tests/unit/worker/test_pdf_export_composition.py`,
  `tests/unit/worker/test_pdf_source_inspector.py`, and
  `tests/unit/worker/test_pdf_thumbnail_broker.py`; `tests/integration/test_case_workspace_windows.py`,
  `tests/integration/test_export_destination_windows.py`,
  `tests/integration/test_m1_vertical_slice_windows.py`,
  `tests/integration/test_manifest_repository_windows.py`,
  `tests/integration/test_organizer_ui_lifecycle.py`,
  `tests/integration/test_organizer_ui_windows.py`,
  `tests/integration/test_pdf_export_worker_windows.py`,
  `tests/integration/test_pdf_inspection_worker_windows.py`,
  `tests/integration/test_pdf_thumbnail_worker_windows.py`,
  `tests/integration/test_quality_entry_points.py`,
  `tests/integration/test_source_ingest_windows.py`, and
  `tests/integration/test_thumbnail_cache_storage_windows.py`; and
  `tests/security/test_m1_vertical_slice_benchmark_security.py` plus
  `tests/security/test_page_organizer_privacy.py`. The retired
  `tests/unit/test_reference_profile.py` is removed.
- Documentation: `README.md`, `benchmarks/README.md`,
  `docs/performance/LOW_RESOURCE_POLICY.md`, `docs/adr/0002-local-quality-gates.md`,
  `docs/adr/0005-pdf-renderer-selection.md`, this ADR,
  `docs/architecture/case-workspace.md`, `docs/architecture/immutable-ingest.md`,
  `docs/architecture/page-organizer-ui.md`, `docs/architecture/pdf-export.md`,
  `docs/architecture/pdf-thumbnails.md`, `docs/development/dependency-policy.md`,
  `docs/development/quality-gates.md`, `docs/security/security-test-plan.md`,
  `docs/gates/README.md`, `docs/gates/m0-foundation-gate.md`, and
  `docs/gates/m1-vertical-slice-gate.md`.

## Rollback

A rollback would restore the prior hardware gate only through another explicit frozen-decision change. It cannot reinterpret constrained-profile evidence as physical-device certification.
