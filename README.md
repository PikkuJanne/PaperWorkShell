# PaperWorkShell Roadmap Bundle

This bundle turns the initial PaperWorkShell product concept into a repository-ready execution contract for Codex and human maintainers.

PaperWorkShell is a local-first desktop application that converts mixed PDFs and images into a complete, organized, legible, privacy-safe, portal-ready document package. The first release is deliberately narrower than a general PDF editor.

## Development bootstrap

The repository has completed its foundation gate and is implementing the Milestone 1 architecture
proof. It does not yet contain the PySide6 UI or any PDF, image, OCR, renderer, installer, cloud,
or signing dependency.

Prerequisites:

- Git 2.45 or newer
- Standard CPython 3.14 (not the experimental free-threaded build)
- `uv` 0.11.32
- Gitleaks 8.30.1

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
| `py -3.14 tools/dev.py validate-task-graph` | Validate task references, gates, and the dependency DAG |
| `py -3.14 tools/dev.py validate-docs` | Validate local Markdown links, anchors, and security references offline |
| `py -3.14 tools/dev.py validate-reference-profile` | Validate the low-spec Windows environment/workload profile |
| `py -3.14 tools/dev.py scan-secrets` | Scan reachable Git/index blobs and nonignored local source bytes with Gitleaks |
| `py -3.14 tools/dev.py quality-self-test` | Prove deliberate format/type/schema/cycle/secret violations are rejected |
| `py -3.14 tools/dev.py benchmark-reference` | Run the record-only generated-I/O baseline workload |
| `py -3.14 tools/dev.py check` | Run every enabled non-mutating quality check |

On non-Windows development hosts, replace `py -3.14` with the equivalent CPython 3.14 command. Toolchain rationale and Windows implications are recorded in [`docs/adr/0001-python-uv-toolchain.md`](docs/adr/0001-python-uv-toolchain.md).

The GitHub Actions workflow runs the same `bootstrap` and `check` entry points on Windows. It uses
read-only permissions and does not upload source, fixtures, caches, scan reports, benchmark
reports, or artifacts. Secret scanning respects `.gitignore`, so ignored local cases and other
sensitive local state are not traversed. Full behavior, negative tests, and reference-profile
instructions are documented in
[`docs/development/quality-gates.md`](docs/development/quality-gates.md), with the architectural
record in [`docs/adr/0002-local-quality-gates.md`](docs/adr/0002-local-quality-gates.md).

## Architecture baseline

The source tree separates pure domain rules, toolkit-free application orchestration, worker lifecycle, document adapters, storage, export, templates, preflight, overlays, and sanitized diagnostics. Product modules currently use only the Python standard library; optional UI and document engines are not installed or imported by package initializers.

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
advisory rather than silent deduplication; manifest replacement is the cancellation commit point;
later integrity checks detect missing, changed, or unsafe case copies. The two-phase flow, privacy
rules, orphan recovery handoff, and PWS-0109/PWS-0110 boundaries are documented in
[`docs/architecture/immutable-ingest.md`](docs/architecture/immutable-ingest.md).

Third-party admission, exact pin/hash evidence, Windows redistribution, renderer comparison, and
SBOM inputs are governed by
[`docs/development/dependency-policy.md`](docs/development/dependency-policy.md). The checked-in
inventory is a policy baseline, not evidence of the exact files in a release package.

The version-1 case manifest contract, canonical synthetic examples, semantic validation rules, and
migration policy are documented in
[`docs/schemas/case-manifest-v1.md`](docs/schemas/case-manifest-v1.md). The authoritative manifest
stores only completed immutable-source records and opaque case-internal references; external source
paths are never persisted in it.

The declarative template v1 contract, synthetic example, provenance/lifecycle fields, packaging
rules, and non-executable boundary are documented in
[`docs/schemas/template-v1.md`](docs/schemas/template-v1.md). Repository validation is strict,
bounded, offline, and clock-independent; runtime loading, trust display, and freshness warnings
remain owned by `PWS-0401`.

The version-1 worker RPC envelope, lifecycle, capability negotiation, logical case-object
references, bounded decoding rules, and sanitized error contract are documented in
[`docs/schemas/worker-rpc-v1.md`](docs/schemas/worker-rpc-v1.md). The schema and semantic validator
reject unknown versions, unregistered operations, path-bearing messages, and raw exception fields.

The PWS-0107 Windows worker boundary launches a fresh zero-capability AppContainer inside a retained
Job Object, inherits only two RPC handles plus a fixed 92-byte bootstrap handle, constructs a
minimal per-launch environment, and supports bounded negotiation, progress, cancellation, timeout,
shutdown, forced tree termination, and fresh restart. Its current no-op operation receives no
document handles and does not claim case filesystem access. The implementation contract and live
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
| `README.md` | This guide |
| `BUNDLE_MANIFEST.json` | Bundle version, file inventory, and checksums |

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
