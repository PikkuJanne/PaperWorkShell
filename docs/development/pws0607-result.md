## Task result — PWS-0607: Block unsafe redaction export and generate user/audit reports

**Status:** done

### Implemented

- Added a separately scoped private worker pipeline from authenticated original and exact removal intent through masked reconstruction, sanitization and canonical multi-method verification. The broker authenticates operation/output identities, provenance and exact candidate bytes, and rejects invented independent OCR/viewer passes.
- Added final export coordination that repeats current manifest/CAS, source, detached evidence and buffer checks. Failed, unknown, stale or forged evidence blocks commit. Retries use fresh candidates; the existing no-replace destination preserves prior output. All-pass evidence cannot open the independent release gate.
- Added closed versioned user/audit reports with affected internal page IDs, all seven methods, statuses/codes and unsigned derivative status. User/audit projections exclude document values, filenames, paths, probes and correlation digests. Actual retained-source cancellation, timeout and manifest-conflict errors retain their correct recovery outcomes.
- Added an accessible result dialog and integration with existing blocked-export flows. Review marks returns to current unapplied intent; release-pending and uncertain outcomes do not offer automatic retry. Package review releases its lease before another workflow opens. Saved marks remain unapplied.
- Resolved the native restricted-worker scratch mismatch through a sealed retained directory pin, without weakening default ancestry validation, worker ACLs or resource budgets. Added consumed-pin lifetime and cleanup regressions.

### Changed files

The baseline comparison identifies **34 task surfaces: 14 modified files and 20 added files**, with no deletion. Inherited changes from earlier tasks are excluded from this list.

| File(s) | Reason |
|---|---|
| `src/paperworkshell/app/redaction_export.py` | Exact reviewed-intent preparation, verification/release gates, retry and final no-replace publication coordination |
| `src/paperworkshell/domain/redaction_export.py`, `src/paperworkshell/domain/redaction_export_candidate.py` | Closed sanitized outcomes and authenticated reconstruction/sanitization/verification provenance |
| `src/paperworkshell/domain/redaction_verification.py`, `src/paperworkshell/pdf/redaction_verification.py` | Closed scratch-access diagnostic and sealed scoped scratch authority; ordinary ancestry checks remain strict |
| `src/paperworkshell/pdf/redaction_export.py` | Bounded affected-source inventory and composed reconstruction/sanitization/verifier adapter |
| `src/paperworkshell/worker/redaction_export_broker.py`, `src/paperworkshell/worker/redaction_export_composition.py`, `src/paperworkshell/worker/redaction_export_operation.py`, `src/paperworkshell/worker/redaction_export_scratch.py`, `src/paperworkshell/worker/redaction_export_wire.py` | Private worker lifecycle, authenticated handle/frame/buffer transfer, retained scratch pin and deterministic cleanup |
| `src/paperworkshell/worker/host.py`, `src/paperworkshell/worker/protocol/codec.py`, `src/paperworkshell/worker/protocol/models.py`, `src/paperworkshell/worker/protocol/operation_scope.py`, `src/paperworkshell/worker/protocol/state.py`, `src/paperworkshell/worker/protocol/redaction_export.py` | Closed operation registration, scope, authority, resource and result correlation |
| `src/paperworkshell/ui/redaction_export.py`, `src/paperworkshell/ui/window.py`, `src/paperworkshell/app/organizer.py` | Accessible result/recovery dialog and truthful current-mark projection in blocked export flows |
| `tests/unit/test_redaction_export_report.py`, `tests/unit/test_redaction_export_service.py`, `tests/unit/test_redaction_export_window.py`, `tests/unit/worker/test_redaction_export_worker.py` | Model, method, provenance, bypass, retry, source-error, suppression, pin lifetime and UI regressions |
| `tests/security/test_redaction_export_pipeline.py`, `tests/integration/test_redaction_export_worker_windows.py`, `tests/accessibility/test_redaction_export_accessibility.py` | Real private pipeline, constrained native lifecycle/resources, hostile scratch authority and native keyboard/status/scaling checks |
| `docs/architecture/redaction-export-gate.md`, `docs/architecture/redaction-verification.md` | Support limits, authority, reports and scratch trust boundary |
| `benchmarks/pws0207-quality-findings-v1.json` | Rebind the existing quality baseline to the exact local source checkpoint; only `revision` changes, with corpus, profile and every measurement preserved |
| `README.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json`, `docs/development/pws0607-result.md` | Task/readiness/evidence and completion metadata; final closure updates remain pending |

### Verification

The corrected accepted source inventory is `output/pws0607/corrected-qa-files.json`, **1,115 files**, SHA-256 `533cfe1cf77a4608f4d825330ceb1032ffffe829ee129a572961319a4c24d272`. Its serial QA runner authenticates original and owned-clone bytes before and after each command, retaining `corrected-*.json` receipts and logs under `output/pws0607/`. The original inventory, `qa-files.json`, SHA-256 `6cc6a6cdfef5565cac38791eefaad8f56467b6cd9d591b72b324202e396b7a03`, and all original results remain unchanged. Results apply to their exact receipt source binding; no pending or earlier failed run is represented as a current pass.

| Command/evidence | Corrected-source result and retained receipt |
|---|---|
| `python -m tools.dev format-check` | **PASS** — command passed; supervisor 0.605 seconds; `output/pws0607/corrected-format-check.json` and `.log` |
| `python -m tools.dev lint` | **PASS** — command passed; supervisor 0.518 seconds; `output/pws0607/corrected-lint.json` and `.log` |
| `python -m tools.dev typecheck` | **PASS** — command passed; supervisor 1.493 seconds; `output/pws0607/corrected-typecheck.json` and `.log` |
| `python -m tools.dev check-import-boundaries` | **PASS** — command passed; supervisor 2.936 seconds; `output/pws0607/corrected-check-import-boundaries.json` and `.log` |
| `python -m tools.dev test-unit` | **PASS** — 6,488 passed; actual pytest 669.20 seconds; supervisor 671.610 seconds; `output/pws0607/corrected-test-unit.json` and `.log` |
| `python -m tools.dev test-security` | **PASS** — 973 passed, six existing Windows link-capability skips; actual pytest 373.20 seconds; supervisor 374.941 seconds; `output/pws0607/corrected-test-security.json` and `.log` |
| `python -m tools.dev test-accessibility` | **PASS** — 108 passed; actual pytest 6.85 seconds; supervisor 8.174 seconds; `output/pws0607/corrected-test-accessibility.json` and `.log` |
| `python -m tools.dev test-integration` | **PASS** — 8 passed, no skips, 572 deselected; actual pytest 164.32 seconds; exact composed native node PASSED; supervisor 165.956 seconds; `output/pws0607/corrected-test-integration.json` and `.log` |
| `python -m tools.dev validate-schemas` | **PASS** — command passed; supervisor 3.263 seconds; `output/pws0607/corrected-validate-schemas.json` and `.log` |
| `python -m tools.dev validate-task-graph` | **PASS** — command passed; supervisor 1.072 seconds; `output/pws0607/corrected-validate-task-graph.json` and `.log` |
| `python -m tools.dev validate-docs` | **PASS** — command passed; supervisor 1.352 seconds; `output/pws0607/corrected-validate-docs.json` and `.log` |
| `python -m tools.dev validate-portfolio-boundary` | **PASS** — command passed; supervisor 4.930 seconds; `output/pws0607/corrected-validate-portfolio-boundary.json` and `.log` |
| `python -m tools.dev validate-dependencies` | **PASS** — command passed; supervisor 1.322 seconds; `output/pws0607/corrected-validate-dependencies.json` and `.log` |
| `python -m tools.dev validate-pillow-runtime` | **PASS** — command passed; supervisor 0.771 seconds; `output/pws0607/corrected-validate-pillow-runtime.json` and `.log` |
| `python -m tools.dev validate-m1-benchmark-report` | **PASS** — command passed; supervisor 0.969 seconds; `output/pws0607/corrected-validate-m1-benchmark-report.json` and `.log` |
| `python -m tools.dev validate-pws0208-resource-workload` | **PASS** — command passed; supervisor 0.732 seconds; `output/pws0607/corrected-validate-pws0208-resource-workload.json` and `.log` |
| `python -m tools.dev validate-pdf-renderer-benchmark` | **PASS** — command passed; supervisor 0.731 seconds; `output/pws0607/corrected-validate-pdf-renderer-benchmark.json` and `.log` |
| `python -m tools.dev quality-self-test` | **PASS** — command passed; supervisor 11.192 seconds; `output/pws0607/corrected-quality-self-test.json` and `.log` |
| `python -m tools.dev validate-fixtures` | **PASS** — 60 fixtures, 15 analytical, six transform and three overlay references; 3,249 historical blobs, 1,084 index blobs and 1,115 working files; `output/pws0607/corrected-validate-fixtures.json` and `.log` |
| `python -m tools.dev scan-secrets` | **PASS** — 3,249 historical blobs, 320 commit/tag objects, 3,286 historical path/blob pairs, 1,084 index blobs and 1,115 working files; `output/pws0607/corrected-scan-secrets.json` and `.log` |
| Original `python -m tools.dev test-unit` | **FAIL retained**: 1 failed, 6,479 passed, eight setup errors; actual pytest 630.39 seconds; original binding `6cc6a6…`; `output/pws0607/test-unit.json` and `.log` |
| Focused service regressions | 54 passed, including nine actual `SourceIngestError` cases that failed before their mapping fix; all are included in the successful corrected whole-unit run |
| Historical native scratch-correction epoch | PASS in its exact earlier snapshot: `output/pws0607/native-attempt4-composed-receipt.json` and `.log`; 1 passed, 579 deselected; corrected affected integration above repeats the exact native node |
| Quality baseline record/validate and exact comparison | PASS; `output/pws0607/quality-binding-correction.json`: both commands exited zero and only `revision` differs |
| Preservation audit | PASS: `output/pws0607/preservation-audit.json`; 1,081 inherited accepted files outside 34 declared task surfaces unchanged; 1,111 corrected frozen nonmetadata files matched; previous audit retained separately |

The original whole-unit observer finished before correction. Its sole assertion failure was `test_checked_in_baseline_is_canonical_and_matches_current_corpus`: the quality baseline still bound revision `9d382b2b2a8ae5cfb70dbf6fb466e7ec8873040d`, while PWS-0607 had changed seven of its 27 implementation surfaces. The exact observed rejection was **“implementation index does not match the recorded revision”** in the owned clone. All 27 inherited baseline hashes matched HEAD; no inherited implementation difference caused this stale binding. The remaining eight entries were setup errors in existing native renderer-benchmark tests because the owned clone lacked the already declared, pinned `renderer-evaluation` development group (`pyside6-addons==6.11.1`). Neither category is relabeled a pass.

After that observer released its snapshot, five task-owned worker/protocol files received canonical CRLF-to-LF conversion to match their committed Git blob bytes; `source-line-ending-correction.json` binds each before/after hash and confirms no other byte change. An isolated accepted-tree source checkpoint, `daca054081816982da229a5c024c39cca37412e4`, was created in the owned clone and fetched only to `refs/codex/pws0607-verification-source`. The original branch and index remain unchanged. The existing evaluator recorded and validated the checkpoint-bound benchmark; exact JSON comparison proves every field except `revision` unchanged. The clone installed the existing locked renderer-evaluation group, with no dependency declaration, version, lockfile or product dependency change. Fresh whole unit, security and accessibility suites plus the exact affected native integration selection have now passed on the corrected inventory. Fixture/privacy validation and full-history secret scanning also passed. The native affected selection retains verbose exact node outcomes; no full integration-suite pass is claimed.

Earlier native failures remain retained. Attempt 1 failed the automated-verification assertion; attempt 2 added closed method/status/code diagnostics and failed with `RV_METHOD_FAILED`. The subsequent diagnostic-only epoch proved `RV_SCRATCH_ACCESS_DENIED` for every method inside the actual restricted worker (`native-attempt3-composed-receipt.json`, with the exact causal probe under `native-causal-probe-test.py`). These are distinct observed epochs, not passes on corrected bytes.

The scoped worker could use its private operation directory while default verification ancestry inspection required metadata access to an unavailable ancestor. `native-scratch-authority-correction.json` records the exact change to a sealed capability backed by a retained native directory identity; request records cannot supply this authority. The default verifier still rejects unsafe/unproved ancestry. Attempt 4 then passed the original composed cancellation, deadline, forced termination, one-byte output exhaustion, same-lease retry, automated verification, complete temporary sampling and unchanged memory/temp bounds. Original failure receipts/logs remain unchanged.

Independent review subsequently found a consumed-pin lifetime bug. The exact regression failed before the correction and passed afterward: `pin-lifetime-before.log`, `pin-lifetime-after.log` and `pin-lifetime-correction.json` retain both epochs. The helper now consumes ownership once, permits idempotent repeated exit and refuses inactive re-entry without closing a potentially reused numeric handle. Final QA runs against the later fixed helper.

The actual retained-source cancellation/timeout/manifest-conflict mapping also received nine regressions across all three revalidation points. They demonstrated nine pre-fix failures (`RESULT_INVALID` instead of the intended recovery status); all 54 service tests passed after the closed mapping correction. Candidate/source exception suppression, nested report mutation and candidate alteration cannot fabricate publication success.


The private synthetic candidate and rendered preview are retained in `output/pws0607/visual-inspection/private-synthetic-candidate.pdf` and `private-synthetic-preview.png`. Visual inspection confirmed the non-redacted control remained legible and the intended region was masked. This is supplementary fidelity evidence, not visual-only proof of removal: the composed built-in methods passed, while independent OCR/viewer methods remained UNKNOWN for these exact private bytes.

Final implementation input inventory: `output/pws0607/source-inputs.json`, 1,111 files, SHA-256 `d7995f513f66b38dd78d61ffb4a1c07c343b0973f97d3ad0f25925dd8a1cd7d8`. It matches the corrected QA inventory after excluding four declared completion metadata paths. Final status/report/bundle updates receive repeated schema, graph, documentation and portfolio validation plus unchanged bounded raw/path/alias secret checks. The original branch and index, inherited work, accepted observations, fixtures and goldens remain preserved.

### Security/privacy/accessibility notes

- Originals, saved intent, previous exports and accepted reader/human observations are preserved. Synthetic fixtures only; no human evidence was repeated. Routine user/audit output has no source values or internal correlation digests.
- One heavy operation and one restricted worker remain bounded by the central resource policy. Graph traversal, decoded source payloads, source/reference/candidate/resave storage and output transfer are bounded; failed or unknown checks block publication.
- Native read-only report controls expose names, roles, descriptions, focus and announcements. Keyboard, recovery order and doubled-text/small-window cases have automated coverage; no new NVDA observation is claimed.
- `PERMANENT_REDACTION_ENABLED` remains false. Trusted fake ports and a test-only simulated approved build exercise the final publication branch; that simulation provides no release approval or public-export claim.

### Portfolio boundary and release-interface notes

PaperWorkShell remains standalone and owns its private documents, OCR, marks and publication authority. No sibling repository, service, adapter, cross-product content access, dependency upgrade, released manifest meaning, migration, data-location change or proved packaged-release capability is introduced. Draft release-interface facts remain draft. No hardware certification, portal acceptance, digital-signature validity, OCR certainty, forensic erasure or PDF/UA claim is made.

### Residual risks or deferrals

- The composed private profile supports **one affected PDF page**. It rejects multiple active pages, active overlays and unsupported cleanup intent rather than omitting them. Affected content/image/Form fingerprints require the proved bounded unfiltered/single-Flate stream grammar; JPEG/DCT streams and unsupported decode parameters fail closed.
- This profile does not regenerate OCR or promise searchable output. Independent OCR and viewer results are unknown for its exact new bytes; historical artifact-specific observations cannot be reused. The broker rejects invented external-method passes, so the actual worker's candidate remains blocked even with the test-only approval simulation.
- PWS-0608 owns independent security review, supported release claims and release approval. Permanent-removal export remains disabled.

### Task graph

- Updated PWS-0607 through review to done after all required source checks passed.
- Marked PWS-0608 ready; every dependency is done. It remains the independent permanent-redaction release gate.
- No later task was started and permanent-removal publication remains disabled.
