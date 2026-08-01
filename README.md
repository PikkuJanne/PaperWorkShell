# PaperWorkShell Roadmap Bundle

This bundle turns the initial PaperWorkShell product concept into a repository-ready execution contract for Codex and human maintainers.

PaperWorkShell is a local-first desktop application that converts mixed PDFs and images into a complete, organized, legible, privacy-safe, portal-ready document package. The first release is deliberately narrower than a general PDF editor.

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

After the repository bootstrap creates stable commands, the project should expose task and schema validation. Before that, a maintainer can validate with Python and PyYAML/jsonschema using logic equivalent to:

```python
from pathlib import Path
import json
import yaml
from jsonschema import validate

root = Path(".")
data = yaml.safe_load((root / "TASKS.yaml").read_text(encoding="utf-8"))
schema = json.loads((root / "TASKS.schema.json").read_text(encoding="utf-8"))
validate(data, schema)
```

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
