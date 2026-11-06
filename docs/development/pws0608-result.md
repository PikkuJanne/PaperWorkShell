## Task result — PWS-0608: Run independent permanent-redaction release gate

**Status:** review — **gate FAIL; release approval withheld**. Permanent removal remains disabled.

### Implemented

- Audited all seven completed prerequisites, the actual export composition, adversarial verification and retained reader/OCR evidence. Three separate automated reviewers withheld approval.
- Recorded three unresolved findings: unavailable exact-candidate OCR/viewer evidence, absent desktop invocation of the export service, and stale retained viewer-inspection provenance. See the [gate report](../gates/m6-permanent-redaction-gate.md) and [independent review](../security/pws0608-independent-review.md).
- Ran fresh checks in an owned clone with a new locked offline environment. Preserved accepted human observations and actual reader saves without repeating the session.
- Updated only gate/evidence documentation, README, task state and bundle metadata. No production code, test, policy, schema, dependency or release switch changes.

### Changed files

- `docs/gates/m6-permanent-redaction-gate.md`: failed gate, acceptance mapping, evidence boundaries and next work.
- `docs/security/pws0608-independent-review.md`: independent findings and resolution requirements.
- `docs/development/pws0608-result.md`: this command and completion record.
- `docs/compatibility/pws-0606-redaction-viewer-resave-matrix.md`: current gate/evidence notice above preserved historical results.
- `README.md`: distinguish historical reader evidence from the failed current gate.
- `TASKS.yaml`: PWS-0608 in review, completion unset; findings and verified evidence recorded.
- `BUNDLE_MANIFEST.json`: version 0.2.45, matching file hashes and no newly ready task.

### Verification

Fresh bootstrap: `uv sync --locked --offline --all-groups` — PASS; 48 locked packages installed into a new environment, Python 3.14.6. The optional renderer-evaluation group was already pinned; no dependency was added.

The 1,115-file pre-closure snapshot is `output/pws0608/qa-files.json`, SHA-256 `6eb9ea7525d478e7699a47852bd165d72e0a3166b26e307809f7f54211b1a27d`. Each command authenticates root/clone bytes before and after execution. Receipts and full logs are `output/pws0608/<command>.json` and `.log`. Supervisor duration includes command startup; pytest durations below are the actual reported test durations.

| Fresh command | Result |
|---|---|
| `python -m tools.dev format-check` | PASS; supervisor 1.132s |
| `python -m tools.dev lint` | PASS; supervisor 0.858s |
| `python -m tools.dev typecheck` | PASS; supervisor 28.629s |
| `python -m tools.dev check-import-boundaries` | PASS; supervisor 4.136s |
| `python -m tools.dev test-unit` | 927 passed, 5561 deselected in 83.65s (0:01:23); supervisor 85.812s |
| `python -m tools.dev test-security` | 973 passed, 6 skipped in 384.88s (0:06:24); supervisor 387.402s |
| `python -m tools.dev test-integration` | 19 passed, 561 deselected in 910.52s (0:15:10); supervisor 912.119s |
| `python -m tools.dev test-accessibility` | 108 passed in 7.18s; supervisor 8.493s |
| `python -m tools.dev validate-schemas` | PASS; supervisor 3.392s |
| `python -m tools.dev validate-task-graph` | PASS; supervisor 1.150s |
| `python -m tools.dev validate-docs` | PASS; supervisor 1.365s |
| `python -m tools.dev validate-portfolio-boundary` | PASS; supervisor 4.888s |
| `python -m tools.dev validate-dependencies` | PASS; supervisor 1.263s |
| `python -m tools.dev validate-pillow-runtime` | PASS; supervisor 0.698s |
| `python -m tools.dev validate-m1-benchmark-report` | PASS; supervisor 0.969s |
| `python -m tools.dev validate-pws0208-resource-workload` | PASS; supervisor 0.697s |
| `python -m tools.dev validate-pdf-renderer-benchmark` | PASS; supervisor 0.767s |
| `python -m tools.dev quality-self-test` | PASS; supervisor 11.510s |
| `python -m tools.dev validate-fixtures` | PASS; supervisor 240.215s |
| `python -m tools.dev scan-secrets` | PASS; supervisor 352.134s |

The integration selection is exactly `PYTEST_ADDOPTS='-vv -k redaction'`, covering every collected redaction integration case through the stable entry point. The fresh unit selection is `PYTEST_ADDOPTS='-q -k "redaction or pws0606"'`. These are focused runs, not full unrelated unit/integration reruns. The six security skips are the existing Windows link-capability skips; no redaction method is waived. Static checks cover 758 Python files and 13 import layers across 272 production files.

The accepted PWS-0607 full unit result (6,488 passes) is reused on unchanged implementation/test bytes and remains explicitly historical. It was not rerun or relabeled for this task. Final unchanged-source inventory: `output/pws0608/source-inputs.json`, 1,111 files, SHA-256 `e47f775854d3645165477b098b7a65e975d1a96fd65a4ddf8086eef3ffb53db7`; the four closure metadata paths are excluded. The new report files are separate gate metadata.

Read-only evidence audit: `output/pws0608/retained-evidence-audit-v2.json`, SHA-256 `a4374e4c697b4745c5fb7bd7c8f2dcff8a3278a306800b9e1292b5fbdaf472f1` — preservation/diagnosis PASS, gate FAIL. All five observations, ten actual saved files, eleven OCR implementation hashes, thirteen source fixtures, forty Tesseract files and relevant environment/evidence bindings match. The original v1 audit-helper FAIL remains preserved; its dictionary-versus-digest comparison was corrected only in the separate v2 helper.

`python -m tools.pws0606_viewer_resave status` — **FAIL, exit 1**; direct diagnosis `PWS0606_GENERATION_CHANGED`. Historical observations remain valid for their original artifacts, while the current automated inspection epoch is stale. No old pass is transferred to a composed candidate.

Before publication, the staged exact closure bytes receive `validate-schemas`, `validate-task-graph`, `validate-docs`, `validate-portfolio-boundary`, bounded existing raw/path/alias secret checks on all seven changed files, and `git diff --check`. Their outcomes and final hashes are recorded in `output/pws0608/closure-validation.json`; publication requires each check to pass. Original HEAD/index and all unrelated accepted files are checked again. No failed result is relabeled.

### Security/privacy/accessibility notes

Only synthetic fixtures and admitted local tooling are exercised. Original documents, accepted observations, fixture/golden bytes and saved intent are preserved. Unknown or forged methods still block export. Native tests exercise constrained memory/temp limits, cancellation, timeout, termination, cleanup and recovery; budgets are unchanged. Unsigned derivative and rasterization/accessibility-loss warnings remain accurate. No new UI or human/NVDA observation is claimed.

### Portfolio boundary and release-interface notes

Standalone operation and PaperWorkShell data ownership are unchanged. No sibling repository, adapter, shared service, migration, data location or runtime interface is introduced. Packaged release-interface facts remain draft/TBD. There is no hardware, portal-acceptance, legal-validity, forensic-erasure, OCR-certainty or PDF/UA claim.

### Residual risks or deferrals

M6-R1, M6-R2 and M6-R3 remain unresolved and release blocking. There is no demonstrated new recovery exploit, but no complete approved release path either. Resolve the supported production/evidence path, refresh inspection without regenerating accepted artifacts, then repeat independent review. Do not enable the feature or waive required methods.

### Task graph

- PWS-0608 is **review**, not done; `completed_at` remains null. All seven prerequisite states and historical evidence are preserved.
- Next dependency-ready unfinished task: **PWS-0608**, for remediation and re-review. No later task becomes ready and none was started.
