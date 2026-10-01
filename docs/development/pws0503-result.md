# Task result — PWS-0503: Image-heavy page analysis and staged re-encoding

**Status: `review` — implementation and focused verification complete; full Windows CI pending.**
This work starts from PWS-0502 closure commit
`3088c1d2acc01c3b5a86400c4eb6d061e01ff733` on `codex/pws-0503-image-stages`.
The attached worktree was clean before this task. PWS-0502 and PWS-0202 are done.

## Implemented behavior and boundaries

The implementation adds a pure, versioned image-analysis and stage-report model, a narrow PDF
image adapter, and an explicit restricted-worker operation. The worker assembles and verifies an ordinary
derivative, applies PWS-0502 safe optimization, analyzes image assets, and optionally applies
one selected quality/resolution stage. Existing ordinary exports retain their lossless policy.
PWS-0504 owns target-size search and final selection across stages; PWS-0505 owns affected-page
preview controls and the user-facing legibility-floor workflow.

Each stage starts from the verified baseline. An apply request binds the analyzed baseline hash,
base-plan fingerprint, selected asset IDs, and one closed profile. Images are processed one at
a time. Text/vector content, nonselected images, page geometry, and other reachable PDF objects
must retain their meanings. A valid smaller candidate requires strict reopen, structural and
render checks, and authenticated output/report bytes after worker termination.

The first profile supports page-local opaque eight-bit RGB/gray image XObjects.
Shared images across pages, inherited-resource/nonpage aliases, masks, unsupported color/filter semantics, and uncertain geometry
remain unchanged. Image resolution comes from physical placement, not image pixel dimensions
alone. Per-page stream-byte estimates are explicitly approximate; measured whole-PDF size is
reported separately. Full registered verification remains required before task closure.

## Effects

- Security and resources: retained-source authority, network denial, the existing heavy-operation
  coordinator and central ResourcePolicy remain in force. Budget derivation accounts for sources,
  baseline/candidates, report/output sections and live decoder/encoder buffers. Cancellation,
  timeout, low disk, partial writes, cleanup and retry have focused regression coverage.
- Privacy: reports use numeric facts and closed policy/outcome codes. Document values, filenames,
  paths, resource names and content-bearing digests stay out of routine representations/logs.
- Accessibility: this task supplies cancelable backend stages and concrete progress/report data.
  The dedicated compression preview UI remains PWS-0505 work.
- Migration and licensing: reuse admitted pikepdf/qpdf, Pillow and PDFium. No dependency upgrade,
  manifest/history migration, or reinterpretation of existing lossless verification claims.
- Portfolio: ordinary PaperWorkShell document processing only; no sibling dependency, shared
  service, adapter, or new public release-interface claim. Release facts remain `TBD`.

## Verification scope

- Domain/wire tests for closed versions, selections, bounds, source/plan binding and report facts.
- Synthetic image-heavy, mixed, vector-only, shared-image, masked/color/geometry and malformed PDFs.
- Reopen/structure/render verification, canonical nonimage semantics, exact unselected-image encodings, and measured
  per-page stream/whole-PDF effects for all three stages.
- Memory/decompression limits, constrained profile, cancellation, timeout, low disk/disk full,
  partial cleanup, fresh retry, and prohibited-token tests.
- Broker and live Windows AppContainer tests for handle authority, termination before trust,
  bounded report transport and unchanged source hashes.
- Registered formatting, lint, typing, import boundaries, unit/integration/security/accessibility,
  schema/docs/task/bundle/dependency/fixture validators and secret scans as applicable.
- Inspect generated synthetic before/after rendering without replacing accepted golden files.

## Review findings resolved during implementation

- Reject extra operands in placement operators; arbitrary image rotation keeps coverage unknown.
- Inventory nonstructural page-tree fields and inherited/overridden resources so indirect image
  aliases cannot escape the selected-use boundary. Such assets remain ineligible in this version.
- Admit encoded Flate bytes independently from decoded sample size. Incompressible but valid
  Flate streams may be slightly larger than their samples; expansion remains bounded.
- Preserve exact unselected image bytes and filters. The stage uses its own conservative writer;
  the image-mode graph comparator verifies these encodings and reopened per-page byte totals.
- Separate cancellation-request receipts from operation IDs. Deliver final authenticated progress
  even when a terminal pump coalesces the last progress event, and honor cancellation from that
  final callback before returning a candidate.

Additional automated agent review reproduced seven alias/encoding/Flate cases after their fixes. The native
image and existing optimization tests passed 118 cases in 24.93 seconds. Full CI evidence will
be added below after the implementation checkpoint is verified.

## Changed files

| Area | Files and purpose |
|---|---|
| Pure contracts | `src/paperworkshell/domain/pdf_image_stages.py`: versioned analysis, selection, profile, geometry and impact models |
| PDF/image adapters | `src/paperworkshell/pdf/image_stage_analysis.py`, `image_stage_encoder.py`, `image_stage_adapter.py`; `src/paperworkshell/imaging/pdf_image_encoder.py`: bounded inventory, bytes-only imaging boundary, sequential encoding and verification |
| Existing graph verifier | `src/paperworkshell/pdf/optimization_adapter.py`: optional authenticated selected-image projection; default lossless semantics retained |
| Worker path | `src/paperworkshell/worker/pdf_image_stage_broker.py`, `pdf_image_stage_composition.py`, `pdf_image_stage_operation.py`, `pdf_image_stage_wire.py`: one-shot composition, authority transfer, quotas, authenticated result/evidence and transient candidate |
| Runtime protocol | `src/paperworkshell/worker/protocol/image_stage.py`, `models.py`, `codec.py`, `state.py`; `src/paperworkshell/worker/host.py`: additive requested capability and exact operation validation |
| Offline protocol | `tools/validate_worker_rpc.py`; `schemas/examples/worker-rpc/v1/pdf-image-stage-*.json` (five examples); `tests/fixtures/worker-rpc/v1/invalid/pdf-image-stage-*.json` (six negative fixtures): schema-compatible admission and rejection evidence |
| Native/domain tests | `tests/unit/test_pdf_image_stages.py`, `test_pdf_image_stage_adapter.py`; `tests/security/test_pdf_image_stage_safety.py`: profiles, corpus, selection closure, preservation, bounds and failure recovery |
| Broker/worker tests | `tests/unit/worker/pdf_image_stage_fixtures.py`, `test_pdf_image_stage_broker.py`, `test_pdf_image_stage_composition.py`, `test_pdf_image_stage_operation.py`, `test_pdf_image_stage_wire.py`; `tests/security/test_pdf_image_stage_broker_security.py`, `test_pdf_image_stage_transport_security.py`: wire, authority, cancellation, quotas and cleanup |
| Compatibility tests | `tests/unit/worker/test_client_lifecycle.py`, `test_pdf_inspect_protocol.py`, `test_pdf_thumbnail_protocol.py`: explicit complete-capability test inventories; `tests/unit/test_validate_worker_rpc.py`, `test_validate_worker_rpc_image_stage.py`: offline/runtime contract checks |
| Existing corpus provenance | `benchmarks/pws0207-quality-findings-v1.json`, `docs/development/scan-quality-evaluation.md`: rerun unchanged quality evaluation and bind its identical measurements to the changed worker registration/protocol surfaces; `tests/unit/test_worker_protocol_codec.py`: round-trip all 20 checked-in RPC examples |
| Live worker test | `tests/integration/test_pdf_image_stage_worker_windows.py`: constrained real AppContainer analysis, re-encoding, stale binding, cancellation, timeout, cleanup and retry |
| Documentation/evidence | `README.md`, `KNOWN_LIMITATIONS.md`, `docs/architecture/pdf-export.md`, `pdf-safe-optimization.md`, `pdf-image-stages.md`, `docs/schemas/worker-rpc-v1.md`, `docs/adr/0011-staged-pdf-image-reencoding.md`, this report, `TASKS.yaml`, `BUNDLE_MANIFEST.json`: behavior, limits and task evidence |

The released RPC schema, dependency lock, ResourcePolicy, imported originals and accepted goldens
are unchanged. The imaging helper remains inside an existing package; the registered import
boundary check passes without an allowlist exception.

## Local verification before CI

- Registered `typecheck`: pass, 631 source files.
- Registered `check-import-boundaries`: pass, 13 layers and 220 application source files.
- Registered `lint`, `validate-schemas`, `validate-task-graph`, `validate-docs`, and
  `validate-dependencies`: pass. Offline RPC corpus: 20 positive and 33 negative fixtures.
- Domain selector through registered `test-unit`: 80 passed.
- Native image-stage and existing optimization corpus: 118 passed in 24.93 seconds.
- Offline RPC validator selector through registered `test-unit`: 61 passed in 7.93 seconds,
  including 42 new operation-specific tests.
- Additional automated agent hostile-broker tests through registered `test-security`: 29 passed.
- Final worker/protocol/security batch: 236 passed in 1.87 seconds, including 35 transport-security
  cases and 29 hostile-broker cases.
- Registered `test-integration` with `PYTEST_ADDOPTS='-k pdf_image_stage_worker_windows --tb=short
  --durations=5'`: 1 passed, 504 deselected in 121.89 seconds on the final runtime (118.53 seconds
  test body, 1.04 seconds setup, 0.42 seconds cleanup). This covers real AppContainer analysis,
  re-encoding, stale-baseline rejection, active cancellation, timeout, identical retry, central
  constrained memory ceilings, and unchanged original/case/manifest bytes and file inventory.
- Registered `format` and `format-check`: 631 files unchanged/formatted. No formatting suppression
  or import-boundary exception was introduced.

Registered command prefix: `uv run --locked --no-sync python tools/dev.py`.
Focused suites use the recorded `PYTEST_ADDOPTS` selectors; they do not substitute for full CI.
The live Windows test uses the previously staged copy-owned CPython runtime and virtual
environment. No OS policy, DLL admission, ACL, dependency or resource budget was weakened.

## CI reconciliation

The first [Windows CI run](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36835599588)
at `b42b56fa610eed2c202f8d3286e94a6993780a8f` passed formatting, lint, import boundaries and
typing, then reported 5,187 unit passes and two failures in 929.56 seconds. Both failures exposed
existing evidence inventories that needed to account for the additive operation:

- The codec round-trip test expected 15 examples; this task adds five schema-valid examples.
  Its exact count is now 20, with every example still decoded and round-tripped. The five
  focused codec tests pass.
- The scan-quality baseline binds 27 existing source surfaces, including four worker
  registration/protocol files changed here. The unchanged evaluator and exact-revision recorder
  were rerun at the implementation checkpoint. Only the recorded revision changed; all eight
  metric rows, corpus/profile digests and policy fields remain identical. The binding inventory,
  validator and approved visual goldens remain unchanged. See
  [quality evaluation provenance](scan-quality-evaluation.md).
  The registered quality-evaluation selector passed all 40 tests in 57.55 seconds.

These corrections change tests and evidence only. The image-stage runtime remains identical to
the first implementation checkpoint. A complete replacement CI run is required for closure.

The additional local `test-accessibility` run passed all 71 tests in 4.87 seconds. Pillow runtime,
M1 report, PWS-0208 workload and renderer-workload validators passed; the quality self-test
rejected all five deliberate violations.

Local `validate-fixtures` reported `FIXTURE_UNINDEXED_DOCUMENT` because its all-ref scan also
includes two pre-existing Codex turn-diff refs retaining an older PWS-0307 synthetic PDF and
temporary render outputs. All 11 offending blobs are absent from this branch's history and from
every `origin` remote ref. They belong to a local tree snapshot, not a published commit ancestor.
The snapshot, fixture validator and allowlist are preserved. Clean published-history CI supplies
the required fixture gate for this task; this local snapshot finding is not hidden by a skip.

## Adjacent issue requiring separate follow-up

Code inspection found the same cancellation-receipt comparison in several pre-existing PDF,
image, preview and OCR brokers. `WorkerClient._request_cancel` returns a fresh request UUID;
those older call sites compare it with the operation UUID. The new image-stage broker is
corrected and has a regression with distinct IDs. Older brokers were not changed in this task;
their cancellation may be reported as an invalid result. This is a separate result-classification
follow-up, not evidence that a canceled stage can publish output. See
[worker cancellation](../../src/paperworkshell/worker/client/client.py) and the existing
[PDF export broker](../../src/paperworkshell/worker/pdf_export_broker.py).

## Synthetic visual inspection (adapter scope)

The local review helper generated a two-page document for both RGB and grayscale. Page one
contains a 600×480 raster with high-frequency diagonal gradients, a born-digital text label and
a vector border; page two contains only text and a vector rectangle. Each stage starts from the
same baseline. The helper ran the installed qpdf 12.3.2 checker and the adapter's PDFium checks,
then rendered each PDF page to an 800×640 PNG for inspection.

| Raster | Stage | Whole baseline bytes | Whole selected bytes | Selected raster | Selected image bytes |
|---|---|---:|---:|---|---:|
| RGB | 300 dpi / q90 | 865,313 | 79,082 | 300×240 | 77,752 |
| RGB | 240 dpi / q85 | 865,313 | 46,301 | 240×192 | 44,971 |
| RGB | 200 dpi / q80 | 865,313 | 30,080 | 200×160 | 28,750 |
| Gray | 300 dpi / q90 | 289,314 | 32,487 | 300×240 | 31,156 |
| Gray | 240 dpi / q85 | 289,314 | 19,875 | 240×192 | 18,544 |
| Gray | 200 dpi / q80 | 289,314 | 13,875 | 200×160 | 12,544 |

Visual inspection shows increasing image softness and edge artifacts at lower resolution/quality,
with preserved placement, color mode and vector border. Extracted text, text-region pixels, and
the complete vector-only page PNGs match their baseline in all six outputs. Original PDF hashes
remain unchanged. This supports the selected adapter behavior on this synthetic corpus; it does
not establish a universal legibility threshold or typical compression ratio. The raw-sample
baseline intentionally exercises the adapter independently of PWS-0502. The live worker test
separately uses a high-quality JPEG and verifies safe optimization before successful re-encoding.

Commands: local ignored `output/pws0503-visual-review/review.py` with the repository Python path,
followed by independent PDFium text extraction and PNG equality checks. Generated artifacts remain
under ignored `output/pws0503-visual-review/review-c4ca5eeb/`. This final run uses the writer that
preserves unselected image encodings; all 16 PNGs are byte-identical to the initially inspected
renderings. No approved golden was refreshed.

## Task graph

PWS-0503 is `review` while full Windows CI is pending. Completion will unblock PWS-0504.
No later task is implemented here.
