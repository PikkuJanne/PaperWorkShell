# Task result — PWS-0705: Assisted-use consent, lock, and retention

**Status: done** (2026-11-02). Approved preservation and actual root gates passed; historical failures and scoped source/native evidence remain.

Receipts are under `output/pws0705-resume/`; full r3 narratives/before bytes remain in `metadata-update/candidates/review-v6-20261102-final-r3/`. Original evidence remains in `output/pws0705/`.

## Implemented

- Explicit unchecked consent, translated visible labels/buddies, masked matching local passphrases and fixed retention. Policy stays private metadata; expiry quarantines document work without deleting files. PWS-0706 owns deletion after verified export.
- Reopening inspection retains an isolated locked context, not an access grant. Checkpoint failure keeps that context; successful release clears it. Scheduler checks cannot select a released or different case.
- Five-minute idle, foreground loss and restart/reopen locking hide workflow, menus and owned content dialogs. All seven assisted chooser routes use Qt-owned dialogs with existing options; there is no inactivity exemption.
- Dedicated check/close acknowledgements bound work. Close guards preview/autosave/retry; manual Lock, recovery and handover focus remain available. Typed stale checklist lock refusals cannot become misleading unlocked recovery text.
- Correlated successful unlock completion uses accepted current state and a live grant before announcing the existing opened-page count or recovery-review message. Cancelled/stale/relocked/expired/closing requests cannot announce success.

## Changed files

- `src/paperworkshell/app/assisted_use.py`, `src/paperworkshell/ui/{assisted_use,assisted_session,backend,window}.py`: assisted-use corrections.
- `tests/unit/test_assisted_{use,organizer,session,file_dialogs,pending_bounds,reopen_feedback}.py`, `tests/accessibility/test_assisted_use_accessibility.py`, `tests/integration/test_worker_windows_containment.py`: regressions and Qt locale guard.
- This result, [audit](../accessibility/pws-0705-assisted-use-audit.md), [architecture](../architecture/assisted-use.md), `TASKS.yaml` and `BUNDLE_MANIFEST.json`. Original inventory/policy: r3 and [ADR 0016](../adr/0016-assisted-use-local-policy.md).

## Verification

V6 source: `0fb2f9f549e718eaa25ec3dbfcd312d86c9be8e7`; baseline-only evidence is bound in the QA summary.

`v6-final-qa-summary.json`: all 19 commands/guards PASS; ACL journals empty. Counts have no skips; selected lanes are not full suites.

| Final v6 scope | Result |
|---|---|
| Static/self-test/accessibility: 16 commands | PASS; format 817 files; lint/type/import and registered validators; all 5 deliberate violations detected; full accessibility 314 passed, 28.32s |
| Selected unit | 174 passed / 6818 deselected, 17.80s |
| Selected integration | 94 passed / 560 deselected, 14.69s |
| Selected privacy | 19 passed / 1001 deselected, 3.98s |

`v5-final-qa-summary.json`: full unit 6980 passed (797.81s), selected integration 96/558 (714.97s), privacy 19/1001 (4.09s), no skips. Two real-worker cases retain v5 evidence. `v5-v6-two-file-and-controller-scope-proof.json`: only one method/new 12-case test differ; other package bytes/methods identical. No full-v6 suite or whole-v6 native result is inferred.

V5 NVDA policy observations retain their bindings. V6 `a01-corrected-reopen.json` separately observes wrong-phrase refusal and visible/spoken “Case opened with 0 active pages.” after the prior closed status; `a01-close-normal-exit.json` records handover focus/exit 0. Receipts are in `native-final/sessions/*/operator-observations/`; the audit owns phase/binding limits. No human/package/hardware certification is claimed.

Prototype receipts retain 3 feedback failures, then 12 product/502 broader pure-Qt passes and initial invalid expectations. Counts are not summed.

### Historical outcomes remain unchanged

- Root full unit: 6778 passed / 2 failed, mixed epochs. Earlier frozen full unit: 6780 passed / 7 failed / 1 error; isolated unchanged renderer later passed 8 methods / 307 subtests.
- Earlier security: 1010 passed / 3 failed / 6 skipped; the exact 3 later passed alone. Integration: 552 passed / 25 failed / 25 skipped; serial retry 19 passed / 5 failed / 1 skipped / 577 deselected. A common cause for all failures was not proved.
- V4 full unit: 6968 passed. Security: 1014 passed / 6 skipped (499.54s). Integration: 576 passed / 1 failed / 25 skipped (4664.32s). The full run stays FAIL for a benign `QLocale.system()` AST false positive, not containment failure. Final 61 guard cases pass; v2 audit-prototype red collection against v1: 14 failed / 47 passed.
- `WORKER_WINDOWS_CONFIGURATION_INVALID` at `config.resource_identity`: root read-only diagnosis found `WRITE_DAC` error 5/owner mismatch; stage-owned copy authenticated 12866 resources, without root ACL change. This does not explain all old failures. The unapproved historical 4079-byte PNG/provenance rejection remains recorded.

### Actual closure gates

Earlier r3: four validators PASS; fixture FAIL `FIXTURE_UNINDEXED_DOCUMENT`22.47s; secret FAIL `SECRET_SCAN_INPUT_TOO_LARGE`282.03s at unchanged 512 MiB (`review-metadata-closure.json`). Prepublication PASS298.78s/fixture FAIL22.48s and replica PASS346.650s keep their scopes. Compact scan later PASS279.75s (`compact-review-metadata-closure.json`); failures remain historical.

Approved preservation retained 25 current tmp files/15,775,963 bytes and independently preserved historical closure; only the authorized internal checkpoint ref changed, without delete/GC/ACL/scanner change (`approved-preservation-execution.json`; peer closure retains stale-phase failure/updated-phase pass).

Actual root fixture PASS 266.80s / privacy PASS 246.75s: `approved-preservation-validate-fixtures.json`, `approved-preservation-scan-secrets.json`. Frozen completion-metadata terminal results are owned by `completion-metadata-terminal-closure.json`; this report is not rewritten after those checks.

## Security/privacy/accessibility notes

Synthetic tests preserve originals; parser/worker authority, budgets, schemas and dependencies are unchanged. This lock does not encrypt files, erase memory or defeat control of the same Windows account. Expiry is not deletion/forensic erasure; Windows is offline time authority. The audit retains native binding/timing limits.

All three apps exited 0. V5/v6 retain 9/7 profile files, one target policy update and six unchanged seed files each. Ordinary NVDA quit succeeded without force/global change; its earlier DateTime/loader refusal remains recorded. `native-final-no-owned-process-closure.json` confirms stable profiles, no owned processes and empty ACL journal.

## Portfolio boundary and release-interface notes

Standalone PaperWorkShell owns its data; no sibling dependency/shared service/schema/adapter. Packaged locations/uninstall facts remain release responsibilities.

## Residual risks or deferrals

No remaining PWS-0705 implementation work. Slow previews prove no render/export/hardware qualification. Pending-close Lock uses bound Qt tests, not invented native activation; OS-clock correction was not performed. PWS-0706 owns verified-export deletion.

## Task graph

PWS-0705 is **done**; PWS-0701–0704 are done. All PWS-0706 dependencies are done: it is **ready**, the next task. Bundle stays 0.2.51.
