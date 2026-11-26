# PWS-0806 documentation continuation

## 2026-11-26 source documentation draft

PWS-0806 started from the clean PWS-0803 handoff at
`304e11f5340c3aaf81f7fdb5bdd852ad92e92ac7`. The linked worktree is
`output/pws0806/documentation-worktree` on `codex/pws-0806-documentation`. The original
checkout had unrelated local changes and was left in place.

The [user guide](../user/USER_GUIDE.md) follows the TRACE workbench. The
[care and recovery guide](../user/CARE_AND_RECOVERY.md) covers assisted use,
recovery, data retention, support bundles, setup, updates and removal. The
[security policy](../../SECURITY.md) records the internal private reporting route
and draft vulnerability handling. The [release interface](../release/RELEASE_INTERFACE.md),
[evidence index](../release/RELEASE_EVIDENCE.md), and
[standalone checklist](../release/STANDALONE_RELEASE_CHECKLIST.md) link this guidance.
Exact candidate identity, supported editions and versions, and a private route for public
reports still need approval or exact evidence. The checklist retains its previous verification state.

| Engineering artifact rehashed | Bytes | SHA-256 |
| --- | ---: | --- |
| TRACE B8 setup installer, unsigned | 512,966,979 | `6b2dce059dc2ef2f02e8fc5206e5aacf285af472dfd281d7785881c9c809d33e` |
| PWS-0803 engineering R1 bootstrap installer, unsigned | 513,022,912 | `d4dc66045e4ee89be881e5c32a93ed8c9ff279b2bd53ce6a03dae3a3db1b018c` |
| PWS-0803 engineering R2 runtime ZIP, isolated test signing | 515,298,153 | `33bed87aaa04be6f382305d66420fd087ab14380977b20ac1464a7d038030960` |

The exact [TRACE B8 setup verification](pws0814-automated-setup.md) and
[R1/R2 update verification](pws0803-authenticated-updates.md) bound the engineering prose.
The M6 and M7 source evidence retains its original scope. The draft was checked against the
current TRACE command groups, dialogs, and source storage and installer contracts. The
TRACE **Settings** menu contains both **Keyboard guide** and **Create support bundle…**.

| Verification | Result |
| --- | --- |
| `tools/dev.py validate-docs`, `validate-task-graph`, `validate-schemas`, `validate-portfolio-boundary` | Passed, including 87 tasks and 258 dependencies. |
| `tools/dev.py format-check`, `lint`, `typecheck` | Passed. Formatting and typing each checked 995 Python files. |
| Documentation, task and portfolio validator unit tests | 89 passed, plus 145 subtests. |
| Focused keyboard, export, assisted-use, support-bundle, uninstall and update accessibility and security tests | 175 passed, including invalid update and diagnostic-content rejection. |
| Windows support-bundle and update-installation integration tests | 23 passed. |
| Documentation audit and `git diff --check` | Passed. Required terms, added-text privacy and claims, draft markers, and the complete pre-existing checklist state were checked. |

This step prepared the source-grounded drafts. No new packaged interaction, independent
novice or support-person observation, or public distribution was performed.

## Installed documentation walkthroughs

On 2026-11-26, independent agent desk reviews followed the novice and support guides against
current source and the prior engineering records. Codex then exercised both roles in the
disconnected Windows 11 Pro x64 26300.9711 guest on its genuine standard-user desktop. The
retained [B8 setup evidence](pws0814-automated-setup.md) binds the installed runtime marker
`c5499f269b524a169848cc27681c1cee019cbfb75f04f6fbe30979a07c1363a3` to the exact unsigned
installer above. The desktop probe checked the marker, foreground process, and token. This was
an agent-operated documentation check using keyboard input and UI Automation. Human usability
and NVDA evidence retain their earlier scope.

| Walkthrough | Observation |
| --- | --- |
| Novice, successful route | From the installed chooser, Ctrl+N created a synthetic case. Ctrl+I imported the checked-in `born-digital.pdf` and the saved case reached one active page. Ctrl+Shift+E reached the same export dialog as **Export 1-page PDF…**. A fresh filename produced a verified PDF, with **Open PDF** available and the saved result recorded in case history. |
| Novice, recovery route | Export first refused an occupied destination with `PDF_EXPORT_DESTINATION_EXISTS` and asked for another name. The file stayed intact. The fresh-name retry succeeded. The 744-byte output (`e255e4aa2fe57f955cb9096490e31b1801f3a97ac568870991f7a6ad0fb957cf`) reopened strictly with one page and no syntax findings. Poppler text extraction and its 120 dpi render each matched the input. The external original and immutable case copy retained their original SHA-256. |
| Support, preview and fallback | The Settings popup query did not locate its item through the probe. The alternate keyboard route, Alt+H then S in English, opened the exact-file preview. Save was disabled before consent and enabled afterward. Two attempts at new names in distinct fixed local folders returned the safe-save error. No ZIP or unexpected file appeared in the walkthrough folders. A description-only report was drafted using the B8 and Windows build, synthetic steps, actual result, and error information. No report was sent. |

The guides now explain the page-count action, filename-conflict retry, alternate support-menu
route, description-only reporting, recovery for damaged versus future-version cases, and the
scope of uninstall deletion. The [known limitations](../../KNOWN_LIMITATIONS.md) and
[release evidence](../release/RELEASE_EVIDENCE.md) retain the B8 support ZIP write failure.
The previously verified source support writer and synthetic ZIP stay scoped to their evidence.
This walkthrough established the safe reporting fallback. PWS-0807 later verified the
successful save and overwrite refusal on [exact unsigned R1](pws0807-diagnostics-investigation.md#packaged-verification-on-unsigned-r1).

The local evidence under `output/pws0806/trace-b8-walkthrough` includes the desktop receipts,
independent PDF inspection, synthetic report draft, and case-preservation receipts. After the
synthetic case closed with all changes saved, the owned pre-walkthrough checkpoint
`PWS0806-before-documentation-walkthrough-20261126` was restored for cleanup. The exact B8 marker
and aggregate digest of all 353 pre-existing case files matched the baseline.

## Next step

PWS-0806's engineering documentation and walkthrough are complete. The release sheets remain
draft for the exact unsigned product-completion candidate. Public support commitments and the
reporting route remain open. The [PWS-0807 matrix](pws0807-rc-matrix.md) carries forward the
B8 failure, exact unsigned R1 diagnostics result, and pending packaged checks.
