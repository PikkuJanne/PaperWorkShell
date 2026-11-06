# PWS-0705 assisted-use accessibility audit

**Status: done.** Scoped QA/native and root gates complete; prior failures/source qualifications remain.

Receipt names below are relative to `output/pws0705-resume/`; the [task result](../development/pws0705-result.md) indexes full outcomes and preserved failures.

## Bound QA

V6 source: `0fb2f9f549e718eaa25ec3dbfcd312d86c9be8e7`.
`v6-final-qa-summary.json`: all 19 commands PASS; format checked 817 files; complete accessibility 314 passed. Selected unit: 174 passed/6818 deselected, 17.80s; integration: 94 passed/560 deselected, 14.69s; privacy: 19 passed/1001 deselected, 3.98s; no skips. These are not full v6 unit/integration/security runs.

`v5-v6-two-file-and-controller-scope-proof.json` confines the delta to the controller completion method and its test. Unchanged v5 component evidence is retained, including real-worker checks; it is not relabeled as a whole-v6 run. Feedback changes no grant/uncover action.

## Native evidence and limits

Original speech/focus probes failed; `output/pws0705/` retains them. No PWS-0702 pass reused. Seven pre-fix chooser-option failures are transcribed tool observations without a saved pre-fix source hash.

Attempt 1/2 raw bindings were overwritten; notes remain, none reconstructed. `native-observations-3.json` retains its exact `native-source-attempt3.json` binding: protected reopen, actual worker import, Qt chooser, foreground hiding/unlock and handover. This is v3 evidence, not a final-source import/export pass.

V3 stale-checklist/`CASE_ACTIVE_NONE` observations remain; no race cause was correlated. Unit tests cover delayed branches.

`native-final/sessions/final-v5/operator-observations/` retains consent, masked recovery, isolation/manual lock, picker/foreground recovery, handover, protected reopen, idle, expiry, clock refusal and normal exits. The navigation error and misleading closed-status feedback remain recorded. Windows time was not changed; actual OS-clock correction and native Lock activation during prolonged close-pending were not exercised. Bound unit tests cover pending/recovery branches.

`native-final-idle-context-attempt01.json`: active-app backend cover at GUI idle 299.863s; tick at 300.845s found it already locked. Activity origins differ: no exact-300s GUI transition claim. The earlier 415.489s protected-inspection event is excluded.

V6 `native-final/sessions/final-v6/operator-observations/a01-corrected-reopen.json` separately records wrong-phrase refusal, then restored Import focus and visible/actual NVDA speech: "Case opened with 0 active pages." `a01-close-normal-exit.json` records handover focus and normal exit. No reimport.

`native-final-no-owned-process-closure.json` and linked inventory retain all three exits 0, profiles/bindings and empty ACL journal. `nvda-final-ownership-readonly.json` preserves the first DateTime-comparison/supporting-loader refusal; no quit was sent. Later `nvda-final/nvda-v5-3265e9ad70-verified-quit.json` verifies ordinary quit 0 and owned reader/loader absence, without force or global configuration change.

Agent-operated synthetic evidence is not human/package/hardware or whole-v6 native certification.

## Root closure

R3 retained four validators PASS, fixture FAIL22.47s and scan FAIL282.03s `SECRET_SCAN_INPUT_TOO_LARGE`, unchanged 512 MiB (`review-metadata-closure.json`). Compact scan later PASS279.75s (`compact-review-metadata-closure.json`). Failures are not rewritten.

Approved preservation retained 25 current tmp files/15,775,963 bytes and independently preserved historical closure (`approved-preservation-execution.json`; peer closure retains stale-phase failure/updated-phase pass), without delete/GC/ACL/scanner change. Actual fixture PASS 266.80s / privacy PASS 246.75s: `approved-preservation-validate-fixtures.json`, `approved-preservation-scan-secrets.json`. Frozen completion-metadata terminal results live in `completion-metadata-terminal-closure.json`, without circular edits.
