# PaperWorkShell Roadmap Bundle

This bundle turns the initial PaperWorkShell product concept into a repository-ready execution contract for Codex and human maintainers.

PaperWorkShell is a local-first desktop application for organizing PDFs and images, reviewing configured packaging rules, and exporting document packages. Users review the content and submit the resulting files themselves.

## ThirdLife portfolio position

PaperWorkShell is the standalone Team A / A1 product in the ThirdLife portfolio. It does not
require ThirdLife, another portfolio application, an account, or a project-controlled server, and
current development contains no app-to-app integration lane. PaperWorkShell owns responsibility
for its document workflow, migrations, and release process; users retain control of their local
cases and documents, which are not a shared portfolio database.

A future ThirdLife Deployment and Suite Assembly (Team B / B4) project may test an exact frozen
PaperWorkShell release through documented black-box behavior such as installation, normal launch,
optional user-controlled file opening, and human guidance. Any deeper adapter remains
ThirdLife-owned, optional, version-bounded, and removable. PaperWorkShell does not add a shared SDK,
schema, service, CLI, URI, RPC command, file association, or project-transfer format solely for
hypothetical integration.

The binding portfolio boundary is recorded in
[`THIRDLIFE_ALIGNMENT.md`](THIRDLIFE_ALIGNMENT.md). Draft M8 release artifacts are indexed under
[`docs/release/`](docs/release/README.md), and non-gating assembly observations live under
[`docs/portfolio/`](docs/portfolio/THIRDLIFE_FUTURE_NOTES.md). These documents do not claim that a
ThirdLife compatibility cut or packaged PaperWorkShell release already exists.

## Development bootstrap

The repository has completed its foundation, Milestone 1 vertical-slice, Milestone 2 image/OCR,
and Milestone 3 visual-overlay gates, plus the Milestone 4 safe template loader and first complete
built-in generic application template and user-confirmed checklist/document-assignment workflow.
The typed preflight rule engine now provides exact saved-case/template authority, canonical
path-free fact projections, deterministic blocking/warning/confirmation findings, and bounded
dependency-aware re-evaluation. It now includes concrete completeness, legibility, privacy, and
compatibility policies, plus generic portal constraints, deterministic Windows-safe filename
resolution, and an accessible case-constraint editor with organizer-owned durable persistence.
Verified combined-PDF, multiple-PDF, and ZIP planning and publication are implemented (PWS-0407).
PWS-0408 connects them to the desktop review and [plain-text package summary](docs/architecture/package-summary.md);
the maintainer accepted the [first-time-user and keyboard pilot](docs/development/pws0408-result.md#maintainer-acceptance)
on 2026-09-30. The [PWS-0409 source-tree v0.1 pilot gate](docs/gates/m4-pilot-gate.md)
passed on the same date; packaged-release work remains pending.
The repository contains the
restricted worker, immutable PDF/JPEG/PNG ingest boundaries, local OCR composition, and worker-only
pikepdf, pypdfium2, Pillow,
OCRmyPDF, and Tesseract adapters. The first PySide6 Widgets organizer admits exact
PySide6-Essentials and shiboken runtime dependencies; it does not admit Qt PDF or Addons. The
repository does not yet contain an installer, cloud integration, or cryptographic-signing
creation.

JPEG/PNG pages also have an ordinary **Export pages as PDF** action (`Ctrl+Shift+E`) for a
verified derivative without OCR or a template checklist. Image originals stay unchanged;
EXIF orientation is reapplied on reopen and transparent areas become white. Unsupported color
profiles and 16-bit PNG fail explicitly. See [current limitations](KNOWN_LIMITATIONS.md),
[enforced resource limits](LOW_SPEC.md), and the [image desktop smoke](docs/development/image-intake-coverage.md).

Prerequisites:

- Git 2.45 or newer
- Standard CPython 3.14 (not the experimental free-threaded build)
- `uv` 0.11.32
- Gitleaks 8.30.1
- qpdf 12.3.2 for live verified-export tests, selected through the fixed
  `PWS_QPDF_EXECUTABLE` path

Install the pinned `uv` release with WinGet when it is not already available:

```powershell
winget install --id=astral-sh.uv --version 0.11.32 -e
```

See the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/) for other supported installation methods. Verify that `uv --version` reports 0.11.32 before bootstrapping.

Install the pinned Gitleaks release used by the local and CI secret-scanning gate:

```powershell
winget install --id Gitleaks.Gitleaks --version 8.30.1 -e
```

Create the locked development environment from a clean checkout:

```powershell
uv sync --locked
```

Install the exact, non-default Qt PDF comparison candidate only when reproducing the PWS-0106
renderer benchmark:

```powershell
uv sync --locked --group renderer-evaluation
```

The restricted worker rejects runtime files that are hardlinked outside the approved runtime
roots. This repository therefore sets uv's install mode to `copy`. Run environment commands from a
standard, non-administrator PowerShell so the desktop broker retains current-user ACL authority.
Rebuild an existing default environment created before the copy-only policy with:

```powershell
uv sync --locked --link-mode copy --reinstall
```

If the non-default renderer-evaluation group is also needed, rebuild that locked closure with:

```powershell
uv sync --locked --group renderer-evaluation --link-mode copy --reinstall
```

If the worker still reports `config.resource_identity` because an older/elevated environment kept
Administrator-owned runtime files, recreate only the derived environment while no Python or test
process is using it:

```powershell
Remove-Item -LiteralPath .venv -Recurse -Force
if (Test-Path -LiteralPath .venv) { throw ".venv is still in use" }
uv sync --locked --group renderer-evaluation --link-mode copy
```

Ordinary bootstrap already installs the selected Essentials/shiboken UI runtime. This non-default
group adds only the broad Addons surface needed by the PWS-0106 live Qt PDF comparison; it does not
activate Qt PDF or Addons in the product runtime, source-import boundary, or shipment graph.
Use a disposable evaluation environment when possible. The upstream Addons and Essentials wheels
share PySide namespace files, so removing Addons from an in-place environment can remove their
shared bootstrap file. Recreate the environment after evaluation, or restore the default closure
with `uv sync --locked --reinstall-package pyside6-essentials --reinstall-package shiboken6`.

`pyproject.toml` restricts development to CPython 3.14 and requires the exact supported `uv` release. `uv.lock` is committed and must change only as part of an intentional dependency update.

Stable developer commands use a standard-library dispatcher and structured subprocess arguments:

`py -3.14 tools/dev.py list` is the generated command inventory. The ordered registry in
`tools/dev.py` is the single executable source of truth for both local checks and CI.

| Command | Purpose |
|---|---|
| `py -3.14 tools/dev.py bootstrap` | Synchronize the locked development environment |
| `py -3.14 tools/dev.py format` | Apply repository formatting |
| `py -3.14 tools/dev.py format-check` | Check formatting without changes |
| `py -3.14 tools/dev.py lint` | Run static lint checks |
| `py -3.14 tools/dev.py check-import-boundaries` | Validate package layout and architecture imports |
| `py -3.14 tools/dev.py typecheck` | Run strict type checking |
| `py -3.14 tools/dev.py test-unit` | Run unit tests |
| `py -3.14 tools/dev.py test-integration` | Run cross-component and command integration tests |
| `py -3.14 tools/dev.py test-security` | Run security-focused tests |
| `py -3.14 tools/dev.py test-accessibility` | Run accessibility-focused tests |
| `py -3.14 tools/dev.py generate-fixtures` | Regenerate the reviewed public synthetic PDF/PNG corpus |
| `py -3.14 tools/dev.py validate-fixtures` | Validate fixture bytes, provenance, coverage, and repository document policy |
| `py -3.14 tools/dev.py validate-schemas` | Validate every registered schema and checked-in instance |
| `py -3.14 tools/dev.py validate-dependencies` | Validate the dependency, license, provenance, and redistribution baseline |
| `py -3.14 tools/dev.py validate-pillow-runtime` | Authenticate the pinned Pillow native members and closure evidence offline |
| `py -3.14 tools/dev.py validate-task-graph` | Validate task references, gates, and the dependency DAG |
| `py -3.14 tools/dev.py validate-portfolio-boundary` | Validate standalone ThirdLife boundaries, M8 ownership, and roadmap-bundle integrity |
| `py -3.14 tools/dev.py validate-docs` | Validate local Markdown links, anchors, and security references offline |
| `py -3.14 tools/dev.py validate-m1-benchmark-report` | Validate the constrained-profile M1 evidence contract |
| `py -3.14 tools/dev.py validate-pws0208-resource-workload` | Validate the fixed constrained signed-source/OCR workload contract |
| `py -3.14 tools/dev.py validate-pdf-renderer-benchmark` | Validate the bound synthetic renderer-comparison workload |
| `py -3.14 tools/dev.py scan-secrets` | Scan reachable Git/index blobs and nonignored local source bytes with Gitleaks |
| `py -3.14 tools/dev.py quality-self-test` | Prove deliberate format/type/schema/cycle/secret violations are rejected |
| `py -3.14 tools/dev.py run-m1-vertical-slice` | Run the constrained M1 vertical slice and publish local evidence |
| `py -3.14 tools/dev.py run-pws0208-resource-workload` | Run the constrained signed-source/OCR workload and publish local evidence |
| `py -3.14 tools/dev.py benchmark-pdf-renderers` | Compare the pinned PDFium and Qt PDF candidates on Windows |
| `py -3.14 tools/dev.py pws0307-reader-test` | Prepare, guide, and record PWS-0307 reader checks |
| `py -3.14 tools/dev.py check` | Run every enabled non-mutating quality check |

On non-Windows development hosts, replace `py -3.14` with the equivalent CPython 3.14 command. Toolchain rationale and Windows implications are recorded in [`docs/adr/0001-python-uv-toolchain.md`](docs/adr/0001-python-uv-toolchain.md).

The GitHub Actions workflow runs the same `bootstrap` and `check` entry points on Windows. It uses
read-only permissions and does not upload source, fixtures, caches, scan reports, benchmark
reports, or artifacts. Secret scanning respects `.gitignore`, so ignored local cases and other
sensitive local state are not traversed. Full behavior, negative tests, and constrained-resource
instructions are documented in
[`docs/development/quality-gates.md`](docs/development/quality-gates.md), with the architectural
record in [`docs/adr/0002-local-quality-gates.md`](docs/adr/0002-local-quality-gates.md).

Low-resource compatibility is enforced by a central, versioned software policy, bounded
production composition, deterministic constrained-profile tests, and numeric gate evidence. See
[`docs/performance/LOW_RESOURCE_POLICY.md`](docs/performance/LOW_RESOURCE_POLICY.md) and
[`docs/adr/0007-low-resource-budgets.md`](docs/adr/0007-low-resource-budgets.md). These checks do
not emulate or certify a particular CPU, RAM allocation, GPU, SSD, HDD, VM, or physical machine;
physical-device observations are useful but optional and non-gating.

## Architecture baseline

Run the first desktop organizer from the locked environment with:

```powershell
uv run paperworkshell
```

Its keyboard controls, autosave/threading model, bounded import/preview behavior, and current NVDA
verification status are documented in
[`docs/architecture/page-organizer-ui.md`](docs/architecture/page-organizer-ui.md) and
[`docs/accessibility/pws-0111-nvda-smoke.md`](docs/accessibility/pws-0111-nvda-smoke.md).

The completed image/OCR milestone, its gate-owned mixed searchable-output scenario, constrained
resource evidence, and current agent-operated keyboard/NVDA smoke are recorded in
[`docs/gates/m2-image-ocr-gate.md`](docs/gates/m2-image-ocr-gate.md) and
[`docs/accessibility/pws-0209-m2-nvda-smoke.md`](docs/accessibility/pws-0209-m2-nvda-smoke.md).

The pure normalized page coordinate model maps exact manifest millionths through intrinsic PDF
orientation, MediaBox/CropBox selection, manifest crop/quarter-turn, actual raster edges, logical
viewports, and explicit display scaling. Its codec, formulas, clipping policies, `/UserUnit`
distinction, tolerances, and synthetic golden evidence are documented in
[`docs/architecture/page-coordinate-model.md`](docs/architecture/page-coordinate-model.md).

The source tree separates pure domain rules, toolkit-free application orchestration, worker
lifecycle, document adapters, storage, export, templates, preflight, overlays, and sanitized
diagnostics. Admitted document engines are confined to worker-side PDF adapters, while package
initializers remain passive and UI-facing layers do not import those implementations. The concrete
`ui` layer alone may import the fully qualified PySide6 QtCore, QtGui, and QtWidgets modules.

`tools/check_import_boundaries.py` statically validates the required package layout, rejects unapproved third-party imports, prevents domain and reverse-UI dependencies, and stops application code from reaching PDF, imaging, or OCR adapters directly. The binding policy and its evolution procedure are documented in [`docs/architecture/dependency-boundaries.md`](docs/architecture/dependency-boundaries.md).

The binding foundation threat model, trust boundaries, lifecycle data flows, control ownership, and
planned security evidence are indexed in [`docs/security/README.md`](docs/security/README.md).

The implemented Windows case-workspace boundary uses opaque UUIDv4 case names, verified private
ACLs, handle-authenticated containment, caller-supplied quotas, and cache/temp-only cleanup. Its API,
fixed layout, deferrals, and failure behavior are documented in
[`docs/architecture/case-workspace.md`](docs/architecture/case-workspace.md), with the operating
system mechanism and residual risks recorded in
[`docs/adr/0003-secure-case-workspace.md`](docs/adr/0003-secure-case-workspace.md).

The local case-lifecycle service creates an atomic empty manifest, lists only valid current-version
internal IDs, retains one active authenticated lease, and performs explicit manifest-first
case-scoped deletion with honest partial reporting and no forensic-erasure claim. Its domain,
application, storage, concurrency, parsing, and failure contracts are documented in
[`docs/architecture/case-lifecycle.md`](docs/architecture/case-lifecycle.md).

The bounded Windows import precheck identifies PDF, JPEG, and PNG by content while retaining one
authenticated read-only source handle, enforcing caller-supplied byte/image/expansion budgets, and
returning only typed preliminary state or sanitized errors. Its exact lease, format checks, limits,
and worker deferrals are documented in
[`docs/architecture/import-prechecks.md`](docs/architecture/import-prechecks.md).

Immutable ingest streams that same live source handle into a random case-scoped pending copy,
verifies independent SHA-256 values, waits for complete typed inspection metadata, and then commits
a distinct random original plus its complete source/page manifest graph. Duplicate content is an
advisory rather than silent deduplication; source publication, checkpoint, and manifest replacement
form one recoverable transaction; later integrity checks detect missing, changed, or unsafe case
copies. The two-phase flow, privacy rules, orphan recovery handoff, and page-operation boundary are
documented in
[`docs/architecture/immutable-ingest.md`](docs/architecture/immutable-ingest.md).

The current-version manifest repository performs complete fingerprint-bound validation before
save/open, compare-and-swap atomic replacement, one-snapshot checkpoint recovery, and toolkit-free
autosave coalescing. Its persistence and interruption contract is documented in
[`docs/architecture/manifest-repository.md`](docs/architecture/manifest-repository.md).

Third-party admission, exact pin/hash evidence, Windows redistribution, renderer comparison, and
SBOM inputs are governed by
[`docs/development/dependency-policy.md`](docs/development/dependency-policy.md). The checked-in
inventory is a policy baseline, not evidence of the exact files in a release package.

The accepted renderer selection is pypdfium2 5.12.1 with default PDFium 152.0.7947.0 behind the
worker-only renderer-neutral adapter. The exact same-corpus Windows evidence, limits, functional
comparison with Qt PDF, and residual staging-cleanup behavior are recorded in
[`docs/adr/0005-pdf-renderer-selection.md`](docs/adr/0005-pdf-renderer-selection.md). Qt PDF remains
an evaluation-only test candidate and is not a product dependency.

The desktop UI runtime selection, exact import surface, LGPL route, and file-by-file Windows
packaging obligations are recorded in
[`docs/adr/0006-pyside6-ui-runtime.md`](docs/adr/0006-pyside6-ui-runtime.md). PySide6-Addons and the
convenience PySide6 meta-package remain outside the ordinary runtime dependency closure.

The bounded PDF-thumbnail service resolves current internal page IDs, authenticates immutable
sources even on cache hits, renders one page per restricted worker generation, and publishes only
fully validated fixed-layout RGBA8 artifacts under case-scoped internal cache names. Its cache key,
RPC/handle correlation, atomic publication, cancellation/quarantine, cleanup, progress, limits, and
privacy contracts are documented in
[`docs/architecture/pdf-thumbnails.md`](docs/architecture/pdf-thumbnails.md).

The bounded JPEG/PNG intake path content-detects and immutably copies each source before complete
worker-only decode, preserves all eight EXIF orientations as intrinsic intent, normalizes CMYK and
alpha-bearing images to correlated opaque RGBA8 previews, and feeds the organizer's byte-budgeted
pixmap cache without rewriting the original or publishing a worker-writable raster cache. Its
limits, one-shot RPC/handle boundary, Pillow dependency, failure semantics, and privacy rules are
documented in
[`docs/architecture/image-intake.md`](docs/architecture/image-intake.md).

The deterministic PDF-export service turns validated manifest page order and rotation intent into
a new derivative inside the restricted worker, strictly reopens it, runs pikepdf/qpdf structure
checks, compares every planned page with a PDFium render, then publishes only the verified bytes
through an authenticated no-replace destination commit. Its exact claims, handle boundary,
resource limits, post-commit history state, and failure semantics are documented in
[`docs/architecture/pdf-export.md`](docs/architecture/pdf-export.md).

Desktop startup now runs bounded case recovery before worker composition, promotes only
authenticated manifest checkpoints, reconciles exact export-history/retry evidence, cleans only
proven disposable artifacts, and surfaces a path-free recovery result. The typed diagnostics
baseline accepts only closed codes, internal IDs, versions, counts, and numeric resource values.
The exact ordering, interruption behavior, privacy schema, and intentionally deferred diagnostic
bundle are documented in
[`docs/architecture/startup-recovery-and-diagnostics.md`](docs/architecture/startup-recovery-and-diagnostics.md).

The version-1 case manifest contract, canonical synthetic examples, semantic validation rules, and
migration policy are documented in
[`docs/schemas/case-manifest-v1.md`](docs/schemas/case-manifest-v1.md). The authoritative manifest
stores only completed immutable-source records and opaque case-internal references; external source
paths are never persisted in it.

The declarative template v1 contract, synthetic example, provenance/lifecycle fields, packaging
rules, and non-executable boundary are documented in
[`docs/schemas/template-v1.md`](docs/schemas/template-v1.md). The implemented runtime loader binds
one opaque catalog key to exact bytes and external checksum/signature evidence, applies a packaged
fingerprint-bound schema with strict bounded validation, rejects unsafe/future/expired content, and
returns immutable data plus explicitly labeled provenance, verification, and review state without
file/network/subprocess or case-write authority. Its trust and Windows storage boundary is
documented in [`docs/architecture/template-loading.md`](docs/architecture/template-loading.md).
The separately versioned
[`generic-portal-application` 1.1.0 artifact](templates/builtin/generic-portal-application-v1.1.0.json)
provides jurisdiction-neutral category, ordering, filename, output-limit, and user-review guidance.
Its generic planning defaults, synthetic complete/missing/excessive packet mappings, and explicit
non-goals are documented in
[`docs/templates/generic-portal-application-v1.md`](docs/templates/generic-portal-application-v1.md).

The packet checklist now projects the template categories as explicit Missing, Added, Needs review,
and Ready text, while keeping source names, paths, and document content outside the GUI state.
Keyboard buttons cover add, confirm or accept, reject or remove, category moves, document/page
ordering, and local undo/redo; UUID-only internal drag-and-drop is an optional shortcut. Adding or
dragging never confirms a document, and automation can only create a Needs review suggestion. The
exclusive editor, manifest confirmation coupling, save/retry authority, privacy boundary, and
non-acceptance claims are documented in
[`docs/architecture/checklist-assignment.md`](docs/architecture/checklist-assignment.md).

The version-1 worker RPC envelope, lifecycle, capability negotiation, logical case-object
references, bounded decoding rules, and sanitized error contract are documented in
[`docs/schemas/worker-rpc-v1.md`](docs/schemas/worker-rpc-v1.md). The schema and semantic validator
reject unknown versions, unregistered operations, path-bearing messages, and raw exception fields.

The PWS-0107 Windows worker boundary launches a fresh zero-capability AppContainer inside a retained
Job Object, inherits only two RPC handles plus a fixed 92-byte bootstrap handle, constructs a
minimal per-launch environment, and supports bounded negotiation, progress, cancellation, timeout,
shutdown, forced tree termination, and fresh restart. Document operations receive only the exact
reduced-right handles they need after ready: prepared sources plus, as applicable, a read-only plan
section, bounded writable output section, and write-only result pipe. Thumbnail and export workers
receive no persistent cache/destination writer, and no worker receives a case or directory path.
The implementation contract and live
egress/job/environment/filesystem evidence requirements are documented in
[`docs/architecture/worker-lifecycle.md`](docs/architecture/worker-lifecycle.md), with the operating
system decision in
[`docs/adr/0004-windows-worker-isolation.md`](docs/adr/0004-windows-worker-isolation.md).

The public synthetic document corpus, exact generated provenance, on-demand hostile recipes, and
outside-repository restricted bug-sample procedure are documented in
[`docs/development/synthetic-fixture-policy.md`](docs/development/synthetic-fixture-policy.md).
Committed PDFs and PNGs are reconstructed byte-for-byte without external document input; valid
encrypted and complex-script/font fixtures remain explicitly assigned to their owning engine tasks.

## Bundle contents

| File | Purpose |
|---|---|
| `ROADMAP.md` | Main binding product, architecture, milestone, release, risk, and gate roadmap |
| `DECISIONS.md` | Frozen product and engineering decisions that implementation must not silently change |
| `TASKS.yaml` | Machine-readable dependency graph with task-level deliverables, acceptance criteria, and verification |
| `TASKS.schema.json` | JSON Schema for validating the task graph after YAML-to-JSON parsing |
| `AGENTS.md` | Codex operating contract: task selection, architecture, security, testing, state changes, and reporting |
| `CODEX_START_PROMPT.md` | Copy-paste first-session prompt and reusable task, review, security, gate, and status prompts |
| `THIRDLIFE_ALIGNMENT.md` | Standalone-product and late-binding ThirdLife portfolio boundary |
| `README.md` | This guide |
| `BUNDLE_MANIFEST.json` | Bundle version, file inventory, and checksums |

M8 release-interface/evidence/checklist templates are kept in
[`docs/release/`](docs/release/README.md). Supplier traceability and explicitly non-gating future
assembly observations are kept in [`docs/portfolio/`](docs/portfolio/SOURCE_TRACEABILITY.md).

## Binding order

When files conflict, use this order:

1. Current explicit maintainer instruction
2. `DECISIONS.md`
3. `ROADMAP.md`
4. The selected entry in `TASKS.yaml`
5. `AGENTS.md`
6. Accepted tests and public interfaces
7. Other documentation

The task graph is executable detail, but it may not weaken a frozen decision or roadmap gate.

## First use

1. Copy the bundle files into the root of the PaperWorkShell repository.
2. Commit the planning bundle as the project baseline.
3. Open `CODEX_START_PROMPT.md`.
4. Paste **First repository session** into Codex.
5. Let Codex execute only `PWS-0001`.
6. Review the completion report and task-state update before starting another task.

The first prompt intentionally prevents feature work before the toolchain, architecture boundaries, schemas, threat model, and test corpus have a stable home.

## Task workflow

Each task in `TASKS.yaml` has:

- A unique `PWS-####` ID
- Milestone and priority
- Dependency list
- Frozen decision references
- Objective and deliverables
- Acceptance criteria
- Verification expectations
- Risk tags
- State and evidence fields

The normal state flow is:

```text
planned -> ready -> in_progress -> review -> done
                    |             |
                    +-> blocked <-+
```

Codex should work on one dependency-ready task at a time. The selection algorithm is defined in `AGENTS.md`.

## Release map

| Milestone | Result |
|---|---|
| M0 | Repository foundation, schemas, threat model, dependency policy, synthetic corpus |
| M1 | Reliable vertical slice: case, PDF import, page organization, export, verification, recovery |
| M2 | JPEG/PNG intake, scan cleanup, local OCR, quality warnings |
| M3 | Visual form overlays and flattened export |
| M4 | Generic templates, checklist, preflight, multi-output export — **v0.1 pilot** |
| M5 | Target-size compression, compatibility, sanitization inspection |
| M6 | Separately gated permanent redaction |
| M7 | Accessibility, localization, assisted retention workflow |
| M8 | Installer, authenticated updates, SBOM, migrations, documentation — **v1.0 candidate** |

## Important release boundary

Permanent redaction is not part of v0.1. It is a Milestone 6 security subsystem. A visual black rectangle, annotation, or overlay must never be presented as permanent removal. Redacted export remains blocked until adversarial verification proves the supported reconstruction approach.

## Product terminology

Use these terms consistently:

- **PaperWorkShell** — user-facing product name
- `paperworkshell` — acceptable lowercase internal package identifier
- **Place signature image** — a visual mark; not cryptographic signing
- **Digital signature** — certificate-based PDF signature detected but not created in v0.1
- **Qualified electronic signature** — external regulated trust-service concept; not implied by a signature image
- **Permanently remove information** — reserved for the gated redaction feature
- **Needs review** — heuristic/OCR/quality signal that requires human confirmation
- **Unsigned derivative** — an edited output based on a digitally signed input

## Validating the bundle

After bootstrapping, run all validation layers:

```powershell
py -3.14 tools/dev.py validate-schemas
py -3.14 tools/dev.py validate-dependencies
py -3.14 tools/dev.py validate-task-graph
py -3.14 tools/dev.py validate-portfolio-boundary
py -3.14 tools/dev.py validate-docs
```

Schema validation uses safe YAML loading with duplicate-key rejection and offline Draft 2020-12
JSON Schema validation with date-format checking. Dependency inventories, case manifests,
declarative templates, and worker-RPC messages also use bounded strict parsers and deterministic
semantic validators. Template examples are canonical JSON; the schema can describe an equivalent
YAML-decoded data model, but this foundation validator does not load YAML tags, aliases, or merge
keys.
Documentation validation checks
supported local Markdown links/anchors and decision, task, and security references without retrieving
network content.

Portfolio-boundary validation uses structured TOML, lock, dependency-inventory, Python AST,
runtime-schema, Markdown-metadata, task-graph, and bundle-manifest inspection. It rejects a
ThirdLife runtime or production interface, missing or wrong-stage portfolio documents, lost M8
release ownership, and stale roadmap-bundle hashes without reading a sibling repository.

Graph validation must additionally confirm:

- Every dependency refers to an existing task.
- No task depends on itself.
- The graph contains no cycles.
- Every milestone gate exists.
- A gate depends on the milestone's required work.
- Every decision reference exists in `DECISIONS.md`.
- Task IDs and milestone assignments are unique and valid.

## Updating task state

A completed implementation change should update its task entry:

```yaml
status: done
evidence:
  completed_at: 2026-07-14
  revision: "<commit or working-tree identifier>"
  commands:
    - "<command and result>"
  notes:
    - "<concise acceptance evidence>"
```

Do not fabricate a commit hash before a commit exists. Use an empty value or a clear working-tree marker according to repository policy.

When dependencies become complete, downstream tasks may be marked `ready`. A task marked `done` must satisfy `AGENTS.md` and every acceptance criterion, not merely have an implementation stub.

## Decision changes

Codex must not rewrite frozen scope to make a task easier. A change requires:

1. A proposed ADR
2. Exact affected decision IDs
3. Evidence and alternatives
4. Security, privacy, accessibility, licensing, packaging, and migration analysis
5. Maintainer approval
6. Coordinated updates to decisions, roadmap, graph, schemas, prompts, and tests

The procedure is defined in `DECISIONS.md`.

## Recommended repository additions during M0

The roadmap bundle is planning-only. Initial tasks should add:

```text
pyproject.toml
src/paperworkshell/
schemas/
tests/
docs/adr/
docs/gates/
tools/
packaging/
```

Do not add real documents, signing keys, certificates, OCR language-data bundles, cache files, local cases, or build artifacts to source control.

## Maintenance cadence

- Update task state after every accepted task.
- Run graph validation whenever dependencies or task IDs change.
- Review decision pressure at milestone gates.
- Update dependency licenses and hashes whenever dependencies change.
- Preserve superseded decisions and schema fixtures for history and migration tests.
- Regenerate `BUNDLE_MANIFEST.json` whenever the planning bundle itself changes.

## Starting point

The first task is `PWS-0001`. The first architectural proof is M1:

> Create case → import PDFs → preview pages → organize non-destructively → export a new PDF → reopen and verify it → recover after interruption.

OCR, overlays, templates, compression, and redaction build on that proof rather than bypassing it.
