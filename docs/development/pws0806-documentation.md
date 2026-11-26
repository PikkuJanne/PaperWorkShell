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
Public release identity, trust, supported editions and versions, and a private route for public
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

## Next step

Run one bounded PWS-0806 documentation walkthrough. Start with the two draft guides and
the [human-testing policy](human-testing.md). Prepare a short, keyboard-accessible novice route
from first launch to an ordinary PDF and a support-person route from a synthetic problem to the
support-bundle preview and a private report draft. Use the existing `born-digital.pdf` fixture and
a fresh external destination. Reuse the existing TRACE B8 setup and R1/R2 operation results
only within their recorded scopes. Check current UI labels, safe failure recovery, and the guide's
instructions for what may be shared. Record who or what actually performed each observation.
Make guide corrections disclosed by that walkthrough and run the documentation, graph and
portfolio checks. Public reporting contact, qualified candidate claims and publication remain
separate release work. STOP.
