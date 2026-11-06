# Task result — PWS-0603: Implement full-page sanitized raster reconstruction

**Status:** done

## Implemented

- Added a separately scoped, handle-only worker operation for one affected PDF page.
  The exact request binds case/revision, immutable source identity, page, authored
  removal marks, crop and quarter-turn. Normal workers do not advertise the private
  capability; the composition grants no external child executable.
- Rendered pixels use the fixed 300 dpi profile and opaque white background. The
  rectangle union is rounded outward with a one-pixel guard and burned into renderer
  rows before they reach scratch storage. Crop/quarter-turn operate on sanitized
  disk-backed pixels. No source text, image, font, form or page resource is copied.
- The existing fresh raster-page builder produces a lossless RGB page at the exact
  physical crop size. Reconstruction reopens the PDF, checks closed generated
  resources, decoded-image hashes and the independently rendered pixel hash, then
  rechecks source and output identity.
- Candidates report only `awaiting_verification` and always identify an unsigned
  derivative. No saved mark is promoted and no final export/history authority is
  added. OCR, derivative sanitization, multi-method verification, export gating and
  independent release approval remain PWS-0604 through PWS-0608 work.

See the [reconstruction contract](../architecture/redaction-reconstruction.md) for
supported geometry, nominal resolution, pixel-boundary fidelity, source-input scope,
resource accounting and publication limits.

## Changed files

| Paths | Purpose |
|---|---|
| `src/paperworkshell/domain/redaction_reconstruction.py` | Closed versioned request/report and exact intent fingerprint |
| `src/paperworkshell/pdf/redaction_reconstruction.py` | Bounded render/burn/crop/rotate/fresh assembly and reconstruction checks |
| `src/paperworkshell/worker/redaction_reconstruction_broker.py` | Source-lease authority, termination, cancellation and exact output authentication |
| `src/paperworkshell/worker/redaction_reconstruction_composition.py` | Explicit private launch scope and least-privilege runtime composition |
| `src/paperworkshell/worker/redaction_reconstruction_operation.py` | Restricted handle ownership, private staging, resource enforcement and output transfer |
| `src/paperworkshell/worker/redaction_reconstruction_wire.py` | Canonical bounded request and correlated result records |
| `src/paperworkshell/worker/protocol/redaction_reconstruction.py` | Closed start/result authority and storage validation |
| `src/paperworkshell/worker/protocol/codec.py`, `models.py`, `operation_scope.py`, `state.py`; `src/paperworkshell/worker/host.py` | Additive capability, private registry/dispatch and correlated progress/result validation |
| `tests/unit/test_redaction_reconstruction_models.py` | Versions, fingerprint, invalid/forged records, gate closure and sanitized representations |
| `tests/unit/test_redaction_reconstruction_adapter.py` | Pixel transforms, bounded failure, cancellation, destination race, disk-full cleanup/retry and verification failures |
| `tests/unit/worker/test_redaction_reconstruction_worker.py` | Wire, private advertisement, grants, injected low disk, terminal cleanup and report tampering |
| `tests/security/test_redaction_reconstruction.py` | Independent canary/pixel/object/revision/source-preservation and geometry oracles |
| `tests/integration/test_redaction_reconstruction_worker_windows.py` | Actual constrained AppContainer cancellation, timeout, termination, limits, full-page reconstruction and recovery |
| `docs/architecture/redaction-reconstruction.md`, `docs/schemas/worker-rpc-v1.md` | Primitive scope, pixel/resource policy, transport and remaining gate ownership |
| `README.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json`, this report | Accurate capability/status/evidence and task readiness |
| `benchmarks/pws0207-quality-findings-v1.json`, `docs/development/scan-quality-evaluation.md` | Replay unchanged quality evaluation and bind its identical measurements to the new shared worker registry revision |

These task changes preserve the inherited M5/PWS-0601 working changes and unrelated
local files. No fixture bytes, golden, dependency, frozen decision, released schema
meaning, resource-policy limit or existing public export behavior was changed.

## Verification

Verification uses an owned, exact-byte copy inside `output/pws0603/verification`,
with the locked existing `renderer-evaluation` dependency group installed offline.
The task-only source checkpoint is `4ac4db5feb3fd7e1b03cd72f4d9e6a903c4ae9a0`;
the 1040-file accepted-source inventory is `output/pws0603/snapshot-files.json`
(SHA-256 `fa7b9712a53c0215085c1413a3c4f16879936b6ac8051978289d4acefcb77e26`).
The checkpoint identifies this task's committed implementation; the inventory also
binds the preserved inherited working files and pending evidence metadata used by
the full suite. No unrelated local document or `tmp/` content enters the snapshot.

`uv run --locked --no-sync python tools/dev.py check` passed every registered gate:

| Registered command | Result |
|---|---|
| `format-check`, `lint`, `typecheck` | Pass; 694 formatted/typed files |
| `check-import-boundaries` | Pass; 13 layers, 240 source files |
| `test-unit` | 5760 passed, no skips, 647.03 seconds |
| `test-integration` | 553 passed, ten existing skips, 4244.33 seconds |
| `test-security` | 779 passed, six existing skips, 312.64 seconds |
| `test-accessibility` | 101 passed, no skips, 6.88 seconds |
| `validate-fixtures` | Pass; 60 synthetic sources, 15 analytical, six transform and three overlay references; 3088 historical blobs, 986 index blobs and 1040 working files |
| `validate-schemas`, `validate-task-graph`, `validate-docs`, `validate-portfolio-boundary` | Pass; 82 tasks, 243 dependencies, 45 bundled canonical files |
| `validate-pillow-runtime`, all three registered benchmark/workload validators | Pass; admitted runtime and unchanged constrained workloads |
| `scan-secrets` | Pass; accepted working/index files and reachable selected-branch history, including commit/tag and path bytes |
| `quality-self-test` | Pass; all five deliberate violations detected, temporary data removed |

The ten existing integration skips require unstaged admitted OCR/private workload
environments or unavailable Windows symbolic-link privileges. The six existing
security skips require unavailable Windows link privileges. The new reconstruction
test and all 31 reconstruction security cases passed without skips, including the
final candidate limit and measured process/job memory and temporary-storage
assertions. `validate-dependencies` also passed separately. All 1040 accepted
files remained byte-identical through the complete registered run; its log is
`output/pws0603/verified-check.log`, with the identity audit in
`output/pws0603/source-verification.json`.

The metadata closure changes this report, task status/evidence and the bundle
manifest; executable source, tests, fixtures and policies stay unchanged. Its
accepted-file inventory is
`output/pws0603/snapshot-delivery-files.json`. The registered schema, graph,
documentation, portfolio and dependency gates are used again for
that closure; results are retained in `output/pws0603/closure-check.log`.
Earlier focused checks passed 20 domain, 21 adapter and 31 independent security
cases, plus worker/protocol/client contracts.
The [security tests](../../tests/security/test_redaction_reconstruction.py) prove
source detectability before checking candidate absence; retained visible ink is
checked separately, so a blank output cannot pass the fidelity oracle.

### Failure and review findings

- Review found a destination race: ownership had been claimed before the page
  builder's exclusive open. Ownership now transfers only after a successful build;
  a raced-in prior file remains byte-for-byte unchanged. A regression covers this.
- Native cancellation exposed the existing client contract returning a cancellation
  request ID, while a reused normal-export wait helper expected an operation ID.
  The new private broker now has its own bounded wait loop and leaves the unrelated
  normal-export implementation unchanged. The private client registry is explicit;
  older default capability requests remain unchanged.
- A native failure-then-retry sequence exposed the shared cursor of duplicated file
  handles. Each new operation now seeks to zero before authenticating its complete
  source copy. The same retained lease succeeds after output-limit failure.
- Each reconstructor permits only one outstanding private candidate. A second request
  is rejected until it closes, and composition shutdown closes retained bytes. This
  bounds parent-owned sections as well as active worker operations.
- The first full unit run detected the existing quality baseline's older worker
  provenance binding. A local task-only implementation checkpoint (`4ac4db5`) and
  replay of the unchanged exact-revision recorder corrected that binding. JSON
  comparison proved that only the revision changed; all measurements, corpus/profile/
  resource fields, thresholds and golden bytes remain unchanged.
- The initial owned runtime omitted the repository's existing optional
  `renderer-evaluation` group, causing eight benchmark setup errors. Installing
  that already-locked group offline restored the admitted renderer; no dependency
  or lockfile was added or changed.
- Source-position/native I/O failures are sanitized with no exception cause/context.
  Disk-full injection during both raw-row and PDF writes discards temporary output,
  preserves the source and permits a clean retry. Low-disk admission rejects before
  parsing source data and consumes every transferred authority.
- A changed final pixel hash, stale/forged report, source digest/length/page-count
  mismatch, malformed source, cancellation, timeout, output exhaustion or forced
  termination produces no usable candidate. Late cancellation is checked again
  after worker termination and output authentication.

### Visual evidence

The approved born-digital synthetic source and reconstructed PDF were rendered and
inspected. The marked canary area is opaque black and the separate retained text
remains visible. Ignored synthetic evidence is in `output/pws0603/`:
`visual-source.png`, `visual-reconstructed.png` and `visual-reconstructed-page.pdf`.
Visual covering is not treated as proof of removal; independent decoded pixels,
source-positive extraction and object/revision checks supply reconstruction evidence.
No golden was refreshed.

## Security/privacy/accessibility notes

The worker remains network-denied and page-at-a-time. Memory, pixels, source length,
page count, temporary storage, output bytes, object count and deadlines remain bounded.
The plan/output sections and staged source are included in storage accounting. Mask
union construction is bounded by the existing 4096-mark limit; rows are written once.
No entire packet or unbounded raster cache is materialized. Reports/representations
hide content, names, paths, geometry and digests from routine diagnostics.

Original sources remain unchanged. A reconstructed page is an unsigned derivative;
signature-marker detection does not validate a cryptographic signature. No GUI control
or user-facing workflow is added, and existing accessible intent review is unchanged.
No software-budget measurement is a hardware certification.

## Portfolio boundary and release-interface notes

Reconstruction serves PaperWorkShell's standalone document workflow. Private marks,
source content, migration and final publication authority remain PaperWorkShell-owned.
No sibling repository, service, adapter, account, shared data or suite release coupling
is introduced. The operation is an internal implementation seam. Draft release-interface
facts and the absence of a ThirdLife compatibility claim remain unchanged.

## Residual risks or deferrals

- The intermediate accepts PDF input and supported crop/quarter-turn only. Image-input
  normalization, visual-overlay composition, deskew policy and packet assembly need
  explicit later orchestration; unsupported intent is never silently dropped.
- Source text/searchability and accessibility structure are lost on the reconstructed
  page. PWS-0604 owns OCR regeneration and region exclusion. PWS-0605/PWS-0606 own the
  complete derivative sanitizer and adversarial verifier, including viewer/resave tests.
- The supplied request fingerprint does not authorize current-manifest publication.
  PWS-0606/PWS-0607 must recheck exact current intent and candidate identity before any
  final commit. Existing normal/alternate export blockers remain unchanged.
- Permanent redaction remains disabled until independent PWS-0608 approval.
- The previously documented ownership mismatch on an inherited source file remains
  unchanged; final Windows verification uses an owned copy of the accepted bytes.

## Task graph

- Updated PWS-0603 to `done` after successful verification.
- Updated PWS-0604 and PWS-0605 from `planned` to `ready`; later M6 tasks remain planned.
- Next ready task: PWS-0604, OCR regeneration with redaction-region exclusion.
