# Task result — PWS-0601: redaction threat model and adversarial corpus

**Status:** done, 2026-10-16.

## Scope and plan

PWS-0601 is selected explicitly by the maintainer. Its PWS-0507, PWS-0004 and
PWS-0009 prerequisites are done. The starting checkout at `868e0ef` already
contains M5 closure changes and unrelated untracked local material. Work is on
`codex/pws-0601-redaction-corpus`, without creating a commit. Those changes
are preserved; unrelated documents and temporary material are outside this task.

The smallest coherent change is a threat-model extension, an exhaustive future
verification matrix and blocking taxonomy, plus deterministic adversarial source
fixtures and an authenticated canary inventory in the existing fixture workflow.
Source tests prove that relevant recovery methods can find the canaries and that
ordinary visual covering retains information. No production redaction path,
manifest/RPC change, migration, dependency, or feature enablement is included.

Verification will use the registered formatting, lint, typing, unit, security,
accessibility, fixture, schema, task, documentation, portfolio and secret checks,
plus relevant fixture/inspection integration entry points. Generated raster and
geometry targets will be inspected. Unknown or failed future removal methods
remain blocking in the plan, and independent PWS-0608 approval remains mandatory.

## Implemented

- Added the [redaction threat model](../security/redaction-threat-model.md), with
  twelve attack/failure scenarios, eighteen planned verification contracts, three
  manual protocols, eighteen planned blocking codes, and all ROADMAP M6 criteria
  mapped to owners and automated/manual evidence. Future implementation and
  independent approval remain explicit; visual-only proof is prohibited.
- Added fourteen deterministic source artifacts and eighteen unique synthetic
  canaries. The [canary inventory](../../tests/fixtures/redaction/v1/canaries.json)
  binds actual source hashes, carriers, recovery methods, page regions and outside
  preservation controls. Sources cover every PWS-0601 required class, including
  actual embedded files, hidden optional content, previous content-stream revisions,
  hidden OCR, and all four rotations with nonzero media/crop origins.
- Reused `generate-fixtures` and `validate-fixtures`, the existing v1 schema,
  closed no-input recipes and bounded authenticated file primitives. The corpus
  now has sixty sources. All previous forty-six source bytes and every approved
  golden are unchanged. Only the current renderer workload's index binding changed;
  old benchmark evidence and selected cases remain bound to their original runs.
- Added independent PDFium text/render and pikepdf carrier/object recovery checks,
  decoded-raster glyph recognition independent of the generator's glyph table,
  target/preservation geometry checks, malformed rejection, and a visual-cover
  negative control. Native Tesseract/OCR-engine validation remains future work.
- Independent review reproduced two new sidecar ancestry races using real NTFS
  junctions. Generation now reauthenticates the full path immediately before the
  final write and anchors containment to the repository. Validation reauthenticates
  the sidecar after enumeration and requires unchanged bytes. Both regressions
  failed before their fixes and pass afterward without capability skips.

## Changed files

| Files | Task change |
|---|---|
| `tools/redaction_corpus.py` | Closed recipes, source expectations and canonical canary inventory |
| `tools/generate_synthetic_fixtures.py`, `tools/validate_synthetic_fixtures.py` | Existing command integration, authenticated inventory and ancestry-race fixes |
| `tests/fixtures/synthetic/v1/index.json`, fourteen new `pdf/redaction-*.pdf` / `image/redaction-scan.png` artifacts | Additive exact-byte public corpus; all file paths/hashes appear in the index |
| `tests/fixtures/redaction/v1/canaries.json` | Eighteen canaries, source/region/carrier/preservation oracles and explicit disabled/no-removal-claim state |
| `tests/unit/test_redaction_corpus.py`, `tests/security/test_redaction_source_recovery.py`, `tests/security/test_redaction_fixture_generation.py` | Provenance, inventory/version/tamper/link rejection, native recovery and real junction regressions |
| `tests/unit/test_generate_synthetic_fixtures.py`, `tests/unit/test_validate_synthetic_fixtures.py`, `tests/unit/test_pikepdf_adapter.py` | Updated exact inventories and explicit new source outcomes; Gray8 layout checks |
| `docs/security/redaction-threat-model.md`, `threat-model.md`, `threat-traceability.md`, `security-test-plan.md` in the same directory | M6 threat/verification extension and foundation links |
| `docs/development/synthetic-fixture-policy.md`, this report | Corpus policy, scope, evidence and remaining implementation boundaries |
| `benchmarks/pdf-renderer-windows-v1.json`, `benchmarks/README.md` | Current index hash only, with historical evidence explicitly retained |
| `README.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json` | Task orientation, verified completion/readiness and refreshed canonical hashes |

## Verification

Commands use the locked environment and existing `tools/dev.py` registry.

| Command | Observed result |
|---|---|
| `python tools/dev.py generate-fixtures` | Passed; sixty deterministic sources and fifteen analytical references regenerated; previous source/golden bytes unchanged. |
| Focused pytest: six corpus/generator/validator/pikepdf/source-recovery/junction modules | 111 passed, 162 subtests; 17.89 seconds. No capability skip. |
| `python tools/dev.py format-check`, `lint`, `typecheck` | Passed; 668 formatted/typechecked Python files. |
| `python tools/dev.py test-unit` | 5,571 passed; 890.42 seconds. No skips. |
| `python tools/dev.py test-security` | Serial final run: 744 passed, six existing symlink-capability skips; 288.99 seconds. Every new test and required junction scenario passed. |
| `python tools/dev.py test-accessibility` | 74 passed; 3.55 seconds. |
| Focused integration: `test_pdf_inspection_worker_windows.py`, `test_quality_entry_points.py` | 5 passed; 65.39 seconds. No skip. |
| `python tools/dev.py check-import-boundaries` | Passed; thirteen layers and 229 source files. |
| `validate-schemas`, `validate-task-graph`, `validate-docs`, `validate-portfolio-boundary`, `validate-dependencies` | Passed; existing schema/decision meanings retained. Final metadata checks are rerun at closure. |
| `validate-pillow-runtime`, `validate-m1-benchmark-report`, `validate-pws0208-resource-workload`, `validate-pdf-renderer-benchmark` | Passed; admitted runtime and unchanged software-budget/workload contracts validated. The additive index hash was refreshed without changing selected renderer cases. |
| Focused `tests/unit/test_pdf_renderer_benchmark.py` | 66 passed, 350 subtests; 181.20 seconds. |
| `python tools/dev.py validate-fixtures` in the isolated checkpoint below | Passed; sixty fixtures, fifteen analytical, six transform and three overlay references; 1,561,281 corpus bytes; 2,976 historical blobs, 947 index blobs and 999 working files. |
| `python tools/dev.py scan-secrets` in the isolated checkpoint below | Passed; 2,976 historical blobs, 307 commit/tag objects, 3,011 historical path/blob pairs, 947 index blobs, 977 tracked and 22 nonignored untracked working files. |
| `python tools/dev.py quality-self-test` | All five deliberate violations detected; test data cleaned up. |
| `git diff --check` | Passed at implementation checkpoints; repeated at closure. |

The fixture/privacy commands run serially in an isolated full-history single-branch
checkout under ignored `output/pws0601/validation-checkout`, following the prior M5
evidence workflow. It contains exact tracked working bytes, preserved M5 closure
and the explicitly selected new task artifacts. An ignored manifest authenticates
all 999 copied files. Unrelated untracked documents, `tmp/` and unrelated local
capture refs are excluded from that task snapshot; nothing is removed or suppressed
in the original checkout. The existing locked environment is reused without sync.
The broader original snapshot still reports its pre-existing unindexed-document
finding, so its privacy scan is not presented as a pass.

The final scan checkpoint manifest SHA-256 is
`731bc916c4be5611236b81ca5a6bc69f41efbc79e21eeef731b5c0822c4127b1`.
All implementation, test, benchmark and corpus bytes match that checkpoint;
later changes only close this report, task metadata and bundle hashes.

The first full security run overlapped the unit suite's native renderer cases.
It produced 741 passes, six existing skips and three `access.recovery_allowlist`
failures. Source review found the fixed global ACL recovery mutex and per-user
journal intentionally reject leases with a different approved runtime-root set;
an existing integration regression explicitly requires that behavior. After the
unit process exited and cleaned up, the complete registered security command was
rerun serially and passed, including all three containment/isolation tests.
No journal was deleted and no worker guard, test, or product source was changed
to obtain that result. The failed attempt remains in ignored local evidence.

The renderer workload and README binding note were refreshed after the fixture
scan and before the final secret scan; source/golden/canary bytes did not change
across that metadata boundary. Final closure records results, task readiness and
bundle hashes without altering implementation/tests/corpus. Complete integration
and native OCR/viewer/redaction release gates are not claimed by this task.

The generated 1024-by-320 raster was visually inspected at native scale. PDFium
renders of all four crop/rotation targets and the opaque-cover control were also
inspected. Targets and outside preservation text are legible and separated; the
covered source renders black while independent text extraction still recovers
its canary. Review images remain ignored local artifacts, with no golden refresh.

## Security/privacy/accessibility notes

The corpus contains only fixed synthetic canaries and algorithmic assets. Source
parsing occurs only in developer tests, with no GUI-process change. Existing
ResourcePolicy and production behavior are unchanged. No new UI control or
accessibility claim is introduced; later review protocols explicitly cover
unsigned derivatives and accessibility-structure warnings.

## Portfolio boundary and release-interface notes

PaperWorkShell remains standalone and owns its documents, cases and migrations.
This test infrastructure adds no sibling dependency, adapter or public product
interface. Draft release-interface facts remain unchanged and unproved facts
remain `TBD`.

## Residual risks or deferrals

PWS-0602 through PWS-0608 own marking, reconstruction, OCR, sanitization,
verification, export enforcement and independent approval. Detectable source
canaries and a complete test plan do not prove that information has been removed.

## Task graph

PWS-0601 is done after verification. PWS-0602 is now ready and is the next
dependency-ready task. PWS-0603 through PWS-0608 remain planned;
PWS-0608 remains unapproved and permanent redaction remains disabled.
