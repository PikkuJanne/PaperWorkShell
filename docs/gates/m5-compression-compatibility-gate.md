# Milestone 5 compression and compatibility gate — PWS-0507

- Review date: 2026-10-16
- Review status: Complete
- Gate conclusion: **PASS — source-tree Milestone 5**
- Product/full-suite CI revision: `480053ba3d116c9ebde5b97d21466f6d88d8e136`
- Local base revision: `868e0ef53a5743514f2ef1cf72f8399901e72326`
- Review branch: `codex/pws-0507-m5-gate`
- Frozen decisions: PWS-D019, PWS-D024, PWS-D029, PWS-D037; continuing
  privacy, resource and standalone obligations PWS-D027, PWS-D041, PWS-D042

## Scope and evidence rule

This source-tree gate reviews PDF feature inspection, safe object/stream
optimization, eligible-image stages, target-size search, explicit compression
preview and grayscale permission, the fixed engineering floor, and conservative
portal output. Authority is [DECISIONS.md](../../DECISIONS.md),
[ROADMAP.md](../../ROADMAP.md), [TASKS.yaml](../../TASKS.yaml), and
[AGENTS.md](../../AGENTS.md).

Evidence is composite: complete Windows CI for the unchanged product, fresh local
automated lanes, accepted PWS-0505 agent-operated preview/legibility observations, and the exact
PWS-0506 desktop/Android human matrix accepted on 2026-10-16. Accepted human
scenarios are retained with their original identities and are not represented as
a new gate-operated session. Separate automated agents audited the acceptance
mapping and artifacts without finding a missing required manual observation or
a gate-blocking issue in the reviewed evidence.

The starting working tree already carried PWS-0506 acceptance closure in its
task, README, ADR, profile, matrix, result and bundle documents. Those changes are
preserved. The unrelated untracked portfolio document and `tmp/` are outside
this change. No product code, engine, resource policy, schema, approved golden or
immutable corpus changes during this gate. One unit assertion is corrected as
recorded below. `git diff` against the CI revision is empty for `src`, `tools`,
`uv.lock`, `pyproject.toml`, `.github`, and `.gitleaks.toml`.

## Prerequisites and acceptance mapping

Every PWS-0507 prerequisite is done. The prior
[M4 source-tree pilot gate](m4-pilot-gate.md) remains accepted.

| Task | Evidence revision | Result record |
|---|---|---|
| PWS-0501: typed feature inventory | `3b195294e05d212c1570d7a8e608cb8b564ffff2` | [Inspection result](../development/pws0501-result.md) |
| PWS-0502: safe optimization | `9b7514830a8a89329cc994b08d42360fb8980d37` | [Optimization result](../development/pws0502-result.md) |
| PWS-0503: eligible image analysis/stages | `38b1fc86af4771a19a8d701381f1cd7f2b7281f6` | [Image-stage result](../development/pws0503-result.md) |
| PWS-0504: bounded target search | `3c3a2a407568d22fecf8491d7144ffd54bd74ed1` | [Target-size result](../development/pws0504-result.md) |
| PWS-0505: preview, grayscale and floor | `009380f57b51818b3f15907fcaa4e540fb5d21c8` | [Preview result](../development/pws0505-result.md) |
| PWS-0506: conservative profile and readers | `480053ba3d116c9ebde5b97d21466f6d88d8e136` | [Profile result](../development/pws0506-result.md) and [accepted matrix](../compatibility/pws-0506-portal-matrix.md) |

| Acceptance criterion | Evidence |
|---|---|
| Every M5 prerequisite is done | Task graph and records above |
| Compression never silently crosses the floor | Immutable policy validation, eligible-image adapter checks, grayscale permission, unsafe-target UI and accessibility tests; no below-floor override |
| Unattainable targets give alternatives without false success | Exhaustion tests for combined/separate/ZIP, unchanged existing destination and empty staging, actionable policy/UI alternatives, distinct work-limit and unsupported outcomes |
| Final outputs are reopened, structurally checked and rendered | Real constrained image-stage, target-search, preview and portal-worker scenarios; strict reopen, qpdf, every-page PDFium, profile checks and exact-byte destination receipts |
| Conservative output passes the defined readers | Five accepted human reader attempts over the exact baseline/preserve/grayscale artifacts; current artifact/provenance/attempt validator passes |

## Compression, failure and resource evidence

The [target policy](../architecture/target-size-export.md) tries safe optimization,
then 300 dpi/quality 90, 240 dpi/quality 85 and 200 dpi/quality 80. Each image stage
starts from the same verified baseline. Actual sizes, including ZIP overhead,
determine the first satisfactory stage; encoded size need not be monotonic.
Explicit grayscale permission selects the versioned grayscale schedule with the
same work bound and floor. The [review contract](../architecture/compression-review.md)
requires successful changed-page preview and explicit acknowledgement before
publishing the retained exact bytes. Review has no timeout and retains no worker.

The floor is an engineering bound, not proof that text or color meaning remains
readable. Both effective DPI axes of changed assets must meet the bound; already below-floor assets
remain unchanged. Vector/text pages are not rasterized merely to reduce size.
All attempts stay within the central versioned
[ResourcePolicy](../performance/LOW_RESOURCE_POLICY.md).

The native target-search scenario checks achievable combined PDF, separate PDFs
and ZIP results with the first safe attempt over target and Preserve 300 selected.
It verifies output hashes, expected text/vector content, portal checks, immutable
source/manifest bytes, process/Job memory ceilings, maximum heavy-operation
concurrency one, stopped workers and clean temporary inventory. Native image-stage
coverage also checks stale binding, cancellation, timeout and fresh retry. The
portal-worker tests check success and rejection of retained nested metadata.

Impossible-target evidence uses production orchestration with deterministic
synthetic candidate producers, plus pure policy, review-service and UI tests;
it is not described as a fresh native whole-ladder measurement. The combined,
separate and ZIP cases exhaust four measured candidates, publish nothing, preserve
an existing file and offer alternatives. Budget skips or shortened work limits
remain distinct from proven unattainability. Storage tests exercise low-disk,
disk-full, cancellation, failed writes, destination collisions, cleanup and retry.

## Automated verification

### Complete unchanged-product CI

[Windows CI run 36979077905](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36979077905)
passed both canonical groups at `480053ba3d116c9ebde5b97d21466f6d88d8e136`:
`python tools/dev.py check --ci-group core` and `--ci-group integration`.
It passed 5,557 unit, 733 security, 74 accessibility and 551 integration tests
(6,915 passes), with seven existing integration prerequisite skips. Five skips
concern unstaged exact OCR environments/runtimes and two concern the private
PWS-0208 artifact stage. No PWS-0506 scenario skipped. All canonical validators,
formatting, lint, typing, import boundaries, fixture/secret scans and five deliberate
quality self-tests passed. This CI result is reused for its exact product bytes;
it is not a new CI run for gate documentation.

### Fresh local gate execution

The local environment was Windows 11 build `10.0.26300` x64, CPython 3.14.6,
pikepdf 10.10.0, pypdfium2 5.12.1, Pillow 12.3.0, PySide6-Essentials 6.11.1 and
pytest 9.1.1, using the locked environment and admitted qpdf 12.3.2. This describes
the test environment without certifying a physical hardware configuration.

| Lane | Result | Duration |
|---|---|---|
| Full unit rerun after the test correction | 5,557 passed, no skips; 1,209 subtests passed | 598.38 seconds |
| Full security | 727 passed, 6 capability skips; 512 subtests passed | 303.94 seconds |
| Full accessibility | 74 passed, no skips | 3.54 seconds |
| Selected M5 integration | 106 passed, no skips | 293.39 seconds |
| Corrected wire-plan module | 26 passed, no skips | 0.45 seconds |

Each local test command uses `uv run --locked --no-sync python -m pytest -q`,
its lane directory or the integration selection below, and `--tb=short`.
The full lanes additionally record `--durations=15` and JUnit XML. Durations are
separate lane measurements, not one whole-packet benchmark. The six security
skips concern this account's unavailable symbolic-link privileges; unchanged
equivalent tests passed in the complete Windows CI above.

Fresh registered formatting, lint, strict typing (664 files), import boundaries
(13 layers, 229 source files), dependency inventory, Pillow runtime and all three
resource/renderer evidence validators passed. Resource-definition validators
authenticate existing schemas/workloads; fresh native bounded-behavior evidence
comes from the integration scenarios above. Registered schema, task-graph,
documentation and portfolio/bundle validators and all five deliberate quality
self-tests passed for the review snapshot. The privacy and metadata boundary is
recorded below.

The fresh M5 integration selection is:

```text
tests/integration/test_pdf_inspection_worker_windows.py
tests/integration/test_pdf_feature_inspection_storage_windows.py
tests/integration/test_pdf_image_stage_worker_windows.py
tests/integration/test_target_size_worker_windows.py
tests/integration/test_target_size_package_export.py
tests/integration/test_compression_review_worker_windows.py
tests/integration/test_compression_review_service.py
tests/integration/test_prepared_package_export.py
tests/integration/test_portal_pdf_worker_windows.py
tests/integration/test_export_package_destination_windows.py
tests/integration/test_package_export.py
```

Local pytest uses `uv run --locked --no-sync python -m pytest`, the documented
equivalent for this host's Application Control restriction on the console
launcher. No host policy, timeout, resource budget, assertion or exclusion is
weakened. Live-worker lanes run serially to preserve authenticated staging/ACL
ownership. Ignored working evidence is under `output/pws0507-gate/`.

The initial full unit run passed 5,556 tests and 1,209 subtests but failed one
wire-plan privacy assertion: the default opaque object's random memory address
contained `900`, which was also the fixture's handle number. The representation
did not disclose the handle. The test now requires exact equality with
`object.__repr__(value)`, rejecting field-bearing output without searching random
address digits. All 26 wire-module tests passed immediately after this correction.
The final full unit rerun is recorded above. Product behavior is unchanged.

The corrected file's SHA-256 is
`c4c0dc694d91cd03bac293d6618aee06b5a9eb75cf5589b6d6743966c01f94ff`.
The local base revision at the top identifies the parent of this working change;
it is not presented as a commit containing the new test or gate report.

### Privacy validation and closure boundary

The registered fixture and secret commands passed serially in an isolated,
full-history, single-branch copy inside `output/pws0507-gate/validation-checkout/`.
The copy included the selected branch plus the exact current gate, test and
preserved PWS-0506 closure files. It excluded unrelated untracked documents,
`tmp/`, and unrelated local capture refs. Nothing was deleted or suppressed in
the original workspace. The locked environment was reused without synchronization.

| Command | Result | Duration |
|---|---|---|
| `python tools/dev.py validate-fixtures` | 46 synthetic fixtures; 15 analytical, 6 image-transform and 3 overlay references; 2,976 historical blobs, 947 index blobs and 978 working files; pass | 218.67 seconds |
| `python tools/dev.py scan-secrets` | 2,976 historical blobs, 307 commit/tag objects, 3,011 historical path/blob pairs, 947 index blobs, 977 tracked working files and one untracked gate report; pass | 321.11 seconds |

The scan checkpoint report SHA-256 was
`8878fe9d5c1dd7e9f4cab13fcfe3ccf3926e84a8e2704f439768c5c16766b62b`.
Final closure records scan results, gate status, readiness, the README reference
and bundle hashes; it changes no product or test bytes. The registered
`validate-schemas`, `validate-task-graph`, `validate-docs` and
`validate-portfolio-boundary` commands and `git diff --check` are rerun across
this metadata boundary. Working logs and JUnit/provenance records remain ignored;
no generated PDF, case, machine inventory or raw log is added to Git.

## Accepted preview, legibility and accessibility observations

The [PWS-0505 record](../development/pws0505-result.md) preserves the earlier
agent-operated computer-use/visual review of actual worker renders and the
production review dialog at `QT_SCALE_FACTOR=2`, 520 by 360
logical pixels. Keyboard loading, cancellation/retry, focus scrolling through
both images, acknowledgement and export intent passed. The captured safe baseline
was 192,427 bytes against a 96,311-byte target; the grayscale 300 dpi/quality 90
candidate was 25,757 bytes, with matched 915 by 732 previews. Primary labels
were readable; small print softened and raster color cues were lost. The blue
vector border was unchanged. These observations explain the human review
requirement without promising general readability.

The review widget, review domain model, target policy, preview worker and review
export service are unchanged from the accepted PWS-0505 revision above. Fresh
native preview/export coverage verifies their composition with the current portal
profile; the earlier visual observations retain their own original artifact scope.

Retained original and proposed PDFs independently match SHA-256
`96f065f1611a094f0eb6fbd9c1a908bcbe49f2b14ef42da04e14eece3019259e`
and `ac847b9eeb76b2532733c9d390a9cb4a91dbb32c9f8481980d14d50d3c1eec03`.
No accepted human scenario is repeated, no new NVDA session is claimed, and no
golden is refreshed. Current automated accessibility covers names/descriptions,
tab order, focus, cancellation/recovery announcements, safe defaults and 200%
text enlargement.

## Accepted reader and constraint matrix

The [tracked PWS-0506 matrix](../compatibility/pws-0506-portal-matrix.md) contains
the exact reader/OS versions, timestamps and attempt hashes for Adobe Reader/free
mode, Edge, Chrome, Firefox and Adobe Acrobat Reader for Android. All five latest
attempts identify a human operator and pass all six required observation groups.
The maintainer accepted completion on 2026-10-16.

The current status validator authenticates all seven pinned artifact hashes,
all 22 local constraint checks and every latest human attempt. Evidence SHA-256 is
`b046984e5ddee6a30704932cf9220a884e8d1b2343ee599541b793b9ee0aef52`;
the implementation binding is
`a3741d06357b3aa5eeae66dba047f6f53731798c2f827fc8b528c29bec0ae6a3`,
prepared from base revision `ce4868cf3bbc0f86d524420324c02fa3f5ecfc47`.

The three-page baseline is 1,102,218 bytes; preserve 240 dpi/quality 85 is
362,313 bytes; grayscale 240 dpi/quality 85 is 301,124 bytes. Human checks cover
baseline navigation, fit/enlarged visual/text checks, inert overlays, close/reopen
and both compressed scan variants. Unchanged pages have native pixel comparisons;
separate PDFs/ZIP members have exact-byte checks. Local constraint tests cover
actual byte/page/file boundaries, ZIP overhead/CRC/members, unsafe names and
blocking mode/extension/encryption observations.

Remote uploads remain optional and **NOT RUN**. Local checks and reader
observations do not establish acceptance by a portal.

## Security, product boundary and remaining limitations

Untrusted processing remains inside restricted, network-denied workers; third-party
objects stay behind adapters. Imported originals are immutable. Routine logs and
diagnostics retain prohibited-content checks. Unsupported retained interactivity or features block according to
the [conservative profile](../architecture/portal-export-profile.md); visual
overlays do not remove underlying information. Modified signed sources require
consent and remain unsigned derivatives. Permanent redaction stays outside M5.

Remaining limits are recorded in [KNOWN_LIMITATIONS.md](../../KNOWN_LIMITATIONS.md):
eligible ordinary-PDF image assets only; source-font substitution; manual review
of small print and color meaning; recipient-specific requirements; and development
runtime preparation delays before active-worker deadlines/cancellation. Constrained
tests prove bounded software behavior, not physical-device or packaged-release
certification, PDF/UA, universal reader coverage or portal acceptance.

PaperWorkShell remains independently useful with ThirdLife and every sibling
absent. This gate adds no dependency, adapter, shared state, public interface or
content access. Cases and migrations remain product-owned. M8 release-interface
facts remain `TBD`; no ThirdLife compatibility or v1.0 release approval follows.

## Disposition

PWS-0507 is **done / PASS**. Every prerequisite is done and the required
compression, floor, honest-failure, verification and reader evidence is complete.
No gate-blocking defect remains. Existing limitations above remain explicit.
PWS-0601 is marked **ready** and is the next dependency-ready task: create the
redaction-specific threat model and adversarial corpus. No M6 implementation or
permanent-redaction enablement is included in this change.

## Gate change inventory

| File | Gate change |
|---|---|
| This report | Prerequisite, acceptance, native, failure, resource, visual, reader and verification evidence |
| `tests/unit/worker/test_pdf_image_stage_wire.py` | Deterministic field-free representation assertion; no product change |
| `README.md` | Source-tree M5 gate reference |
| `TASKS.yaml` | Gate result/evidence and next-task readiness, preserving existing PWS-0506 closure |
| `BUNDLE_MANIFEST.json` | Bundle 0.2.33, 38 canonical files, hashes and readiness summary |
