## Task result — PWS-0606: Implement multi-method redaction verification suite

**Status:** done — the private verifier, adversarial corpus and required reader/save/resave evidence are complete. Permanent-removal export remains disabled pending PWS-0607 integration and PWS-0608 independent approval.

### Implemented

- Bound closed, versioned method reports to exact candidate/reference/intent/probe/profile inputs. Missing, failed, forged, foreign or future evidence fails closed without exposing document values.
- Independently checked physical objects, revisions, metadata, attachments, forms, comments and actions; complete page/resource graphs, unused images, fonts and CMaps; text/search/glyph geometry, extracted images and exact 300 dpi pixels.
- Repeated checks after a bounded fresh full save; cancellation, deadlines, input mutation, disk-full and cleanup failures cannot yield successful verification.
- Kept `redaction-verification-v1` canonical verification strict. Added explicit `redaction-viewer-resave-verification-v1` inspection of original viewer bytes: complete two-xref linearization coverage, closed numeric hint tables, exact inert Adobe metadata, redundant page settings and narrowly proved decimal truncation. Real earlier revisions, unknown carriers and auxiliary aliases still fail closed. A final narrow correction also detects known removed values split across trailer ID strings; its source and regression-test delta is inventoried separately from the earlier broad QA snapshot.
- Corrected the independently unit-proven reconstruction broker result-before-cancellation-acknowledgement ordering without changing cancellation, memory, timeout or protocol budgets. This was not the observed broad native failure: its earlier ACK assertions passed, and its final private-temp measurement was incomplete with unknown cause. The original FAIL remains recorded. A fresh exact-node retry on unchanged source passes all assertions; deterministic ACK regressions and the later corrected native run supply separate evidence.
- Preserved all five accepted human observations, ten actual saved files, original preparation/guidance and historical canonical failures. New append-only receipts pass built-in methods on all ten originals and their internal full resaves. Existing independent OCR remains valid for the exact ten actual files; a fresh validation checks all 160 artifact-pages and current identities. External OCR/viewer methods on internal qpdf bytes remain unknown unless independently run.
- Made the stable test entry point fast noninteractive status. The accepted human recorder stays byte-identical. AGENTS 9.2 and the human-testing policy require an easy whole session, total action/time assessment and mechanical automation before future handoff. The near-40-step workload must not be reused as a human-friendly design; accepted observations need no repeat.
- Retained the earlier bounded secret-scanner correction: only completed exact-path/content mirror work is reused. History, raw/path/alias checks and change detection remain required; budgets and finding rules are unchanged.

### Changed files

| File(s) | Reason |
|---|---|
| `src/paperworkshell/domain/redaction_verification.py` | Closed typed methods, request/report versioning and distinct explicit profiles |
| `src/paperworkshell/pdf/redaction_verification.py` | Private method orchestration, bounded full resave, cleanup and input identity checks |
| `src/paperworkshell/pdf/redaction_verification_structure.py`, `redaction_verification_linearized.py` | Complete original physical envelope/object/revision/carrier checks and exact auxiliary roles |
| `src/paperworkshell/pdf/redaction_verification_resources.py`, `redaction_verification_viewer_metadata.py` | Authenticated complete resources/fonts/CMaps and closed inert viewer bookkeeping |
| `src/paperworkshell/pdf/redaction_verification_visual.py` | Extraction, geometry, images and rendered pixels |
| `src/paperworkshell/worker/redaction_reconstruction_broker.py`, `tests/unit/worker/test_redaction_reconstruction_worker.py` | Preserve pending cancellation ACK handling before terminating the private worker, with ordering/deadline regressions |
| `tests/unit/test_redaction_verification*.py`, `test_pws0606_redaction_verification_harness.py`, `test_pws0606_viewer_resave.py` | Model, method, carrier, envelope, resource, mutation, limit, cleanup and retained-evidence regressions |
| `tests/security/test_redaction_verification_corpus.py`, `test_redaction_viewer_verification.py` | All fourteen adversarial inputs, unsafe controls and viewer-specific attacks |
| `tests/integration/test_redaction_verification_pipeline.py`, `test_redaction_verification_ocr_worker_windows.py` | Alternate renderer, independent native OCR and actual restricted worker pipeline |
| `tools/pws0606_redaction_verification.py`, `tools/pws0606_viewer_resave.py`, `tools/dev.py` | Frozen historical recorder, append-only automated evidence and stable entry point |
| `tools/dev.py`, `tests/security/test_secret_scanning.py` | Bounded completed-mirror scanner reuse with negative regressions |
| `docs/architecture/redaction-verification.md`, `docs/compatibility/pws-0606-redaction-viewer-resave-matrix.md` | Trust boundaries, distinct profiles and exact executed reader evidence |
| `AGENTS.md`, `docs/development/human-testing.md`, `human-test-environment-v1.json` | Standing human-workload policy and provenance-bound unchanged versions |
| `docs/security/redaction-threat-model.md`, `README.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json`, this report | Traceability, evidence and task readiness |

Inherited M5/M6 changes and unrelated local files are preserved on `codex/pws-0606-redaction-verification`. Native suites run serially in the owned accepted-file clone under `output/pws0606/verification`; the original checkout's existing ownership and Git index are preserved.

### Verification

Verification has two explicit source bindings. The original broad QA snapshot is `frozen-files.json`, SHA-256 `7f356856595ab4e79bb6a56917d2b6d88994e547d37296323ad270d9be44f32f`, with the separately admitted bundle-only refresh. Its unit run passed 6,276 cases and exposed one stale bundle-hash check; the targeted metadata-only retry passed that remaining case. That original run, failure and retry remain unchanged. Its full integration run failed exactly `tests/integration/test_redaction_reconstruction_worker_windows.py::test_native_private_reconstruction_cancel_timeout_limits_termination_and_recovery` at the final private-temp `measurement_complete` assertion. The observed snapshot had `measurement_complete=False`; all earlier cancellation/ACK and process-memory assertions passed. An incomplete sample cannot establish complete temporary-storage coverage, and its root cause remains unknown. The original observed FAIL, both actual exit codes of one, full log and traceback remain unchanged. `failure-analysis-v1.json`, SHA-256 `031d560af18119d4214a83d038e49bcefb4515f44099a1f48a33494b5745a4d2`, binds that observed symptom and the successful exact-node unchanged-source retry to the original snapshot. The retry proves a fresh complete measurement; it does not repair or erase the earlier unknown. The independently unit-proven ACK ordering bug is separate. The failed broad suite is not relabeled PASS.

After the broad integration observer finished and released the unchanged snapshot, four existing source/test files received the separately reviewed trailer-ID and cancellation-ACK corrections. `source-correction.json`, SHA-256 `40495702abb1bed931d90f6fb9577880fdb04c395c3f34df34d0164376f7bd75`, records their exact before/after delta and completed observer binding. `corrected-files.json`, SHA-256 `71cca08229f66d972e56d8e1930e47d97a6da4adfc50525c3bd749eca6f57c1e`, inventories the same 1,095 paths afterward. Current whole unit, security and accessibility suites, static/validation/history checks, and the exact six-case native selection pass against this corrected inventory. The six include all five redaction-verification cases and the literal reconstruction node that failed earlier; its verbose current `PASSED` outcome is required. Thus the original broad coverage and corrected affected-case evidence are combined explicitly. The full integration suite is not represented as passing or as running on corrected bytes. New viewer-verification epoch evidence rechecks all ten unchanged actual reader files and their internal full resaves with the corrected inspector. Count-only receipts and logs retain both source epochs under `output/pws0606-adobe/`. Final documentation/task/bundle updates receive repeated schema/graph/docs/portfolio checks and the same bounded raw/path/alias secret checks.

Original broad snapshot results:

| Command | Result |
|---|---|
| `python tools/dev.py format-check` | pass |
| `python tools/dev.py lint` | pass |
| `python tools/dev.py typecheck` | pass |
| `python tools/dev.py check-import-boundaries` | pass |
| `python tools/dev.py test-unit` | 6,276 passed; sole stale-bundle test passed on targeted retry (6,277 unit cases verified) |
| `python tools/dev.py test-security` | 927 passed, 6 skipped in 407.69s (0:06:47) |
| `python tools/dev.py test-integration` | FAIL retained: 1 failed, 572 passed, 6 skipped in 5420.40s (1:30:20) |
| `python tools/dev.py test-accessibility` | 101 passed in 10.50s |
| `python tools/dev.py validate-fixtures` | pass |
| `python tools/dev.py scan-secrets` | pass |
| `python tools/dev.py validate-schemas` | pass |
| `python tools/dev.py validate-task-graph` | pass |
| `python tools/dev.py validate-docs` | pass |
| `python tools/dev.py validate-portfolio-boundary` | pass |
| `python tools/dev.py validate-dependencies` | pass |
| `python tools/dev.py validate-pillow-runtime` | pass |
| `python tools/dev.py validate-m1-benchmark-report` | pass |
| `python tools/dev.py validate-pws0208-resource-workload` | pass |
| `python tools/dev.py validate-pdf-renderer-benchmark` | pass |
| `python tools/dev.py quality-self-test` | pass |

Corrected source results:

| Command | Result |
|---|---|
| `python tools/dev.py format-check` | pass |
| `python tools/dev.py lint` | pass |
| `python tools/dev.py typecheck` | pass |
| `python tools/dev.py check-import-boundaries` | pass |
| `python tools/dev.py test-unit` | 6284 passed in 951.01s (0:15:51) |
| `python tools/dev.py test-security` | 968 passed, 6 skipped in 407.11s (0:06:47) |
| `python tools/dev.py test-integration` with the exact six-case native selection and verbose node outcomes | 6 passed, 573 deselected in 164.76s (0:02:44) |
| `python tools/dev.py test-accessibility` | 101 passed in 6.35s |
| `python tools/dev.py validate-fixtures` | pass |
| `python tools/dev.py scan-secrets` | pass |
| `python tools/dev.py validate-schemas` | pass |
| `python tools/dev.py validate-task-graph` | pass |
| `python tools/dev.py validate-docs` | pass |
| `python tools/dev.py validate-portfolio-boundary` | pass |
| `python tools/dev.py validate-dependencies` | pass |
| `python tools/dev.py validate-pillow-runtime` | pass |
| `python tools/dev.py validate-m1-benchmark-report` | pass |
| `python tools/dev.py validate-pws0208-resource-workload` | pass |
| `python tools/dev.py validate-pdf-renderer-benchmark` | pass |
| `python tools/dev.py quality-self-test` | pass |
| `python -m tools.pws0606_viewer_resave verify` | All ten actual saved files pass all five built-in methods, including full save/resave; strict canonical packet recheck passes |
| `python tools/dev.py pws0606-redaction-test` | Fast status passes all five retained slots without prompting or PDF parsing |
| Native OCR receipt revalidation | 160 actual artifact-pages and all 13 supported source identities validated; original OCR proof: 14 visual source positives, 158 bounded calls, complete cleanup |

The long integration suite was independently observed through retained process handles. Only its waiting outer supervisor was paused to prevent an undersized one-hour wrapper timeout; pytest and workers continued normally, both actual command exits were one, and the supervisor was resumed only after completion. The original one-node FAIL remains unchanged and is linked to failure-analysis-v1.json, the successful unchanged-source exact-node retry, and the corrected native six-case run. The original private-temp measurement remains incomplete with unknown cause; The observer retains source identities and the full log hash; no product or test budget changed.

The original OCR receipt is `output/pws0606-human-complete/saved-file-ocr-v1.json`, SHA-256 `318bd196d643bdaac98be46b025c70d69ec511a6b39677f5aa70f60f0f7749d7`. The new actual-file method matrix SHA-256 is `ce4aa34996c723966bf7f497ce3ae5ab6c90dadcda4ef96c454ede264f86a267`. Historical unsupported-Adobe failures remain retained and accurately labeled; no original was normalized or modified to obtain a pass. The frozen helper remains SHA-256 `3e5b01954745fb1611a2eec35a4bdb6e1afebda76296caa7462dd1f0eb682381`.

Final source inventory: `output/pws0606-adobe/source-inputs.json`, 1,092 files, SHA-256 `4f4bd6e064370fb2453cd532c0dffd2de8c3ebb2fb107266b8e72307b99d226b`. Only task/bundle/completion-report metadata is excluded. The final preservation audit binds both tested source inventories, the exact declared correction, accepted files, all actual saved bytes, human attempts, original failed receipts and unchanged protected inputs. Earlier snapshots and receipts remain historical evidence; corrected-source test counts above replace no old record.

### Security/privacy/accessibility notes

All fixtures and test artifacts are synthetic. No canary/document text appears in routine reports. Original imports, manifests, reader saves and accepted observations remain immutable. Resource limits, native runtime pins and worker constraints are unchanged; regressions cover success, hostile input, cancellation, timeouts, disk-full, cleanup and recovery. No product UI, worker operation or publication authority is added. Results prove bounded software behavior, not hardware certification, universal absence, OCR certainty, forensic erasure, PDF/UA or portal acceptance.

### Portfolio boundary and release-interface notes

The verifier has standalone PaperWorkShell testing/security value. PaperWorkShell retains ownership of documents, OCR, overlays and publication authority. No sibling repository/service/adapter, shared schema, new dependency, migration, data location or proved release-interface fact changes. Frozen decisions, fixtures/goldens, released schemas, dependency pins and resource budgets are preserved.

### Residual risks or deferrals

- Viewer inspection deliberately supports only the proved closed envelopes and metadata grammar; other variants fail closed.
- The original broad native private-temp sample was incomplete for an undiagnosed reason. The unchanged-source exact-node retry and corrected-source six-case run pass the original measurement assertions. The initial FAIL/unknown remains retained; sampler code, resource budgets and failure rules are unchanged.
- Human and OCR evidence covers the exact actual named-reader outputs, not arbitrary or later generated files. Internal qpdf bytes do not inherit that external testimony.
- PWS-0607 owns worker/export integration and sanitized user/audit reports. PWS-0608 owns independent release approval. Permanent-removal export remains disabled.

### Task graph

- Updated PWS-0606 to done after required verification and evidence succeeded.
- Next ready task: PWS-0607. Its other dependencies PWS-0602 and PWS-0408 are done. No later implementation was started.
