# PWS-0111 keyboard-only and NVDA smoke record

Record date: 2026-08-09

Platform: Windows, Python 3.14, Qt/PySide6-Essentials 6.11.1, NVDA 2026.1.1
Scope: initial case chooser and page organizer vertical slice

## Current result

**PASS.** The automated accessibility and keyboard checks pass. The rebuilt current-user
environment also passes the production restricted-worker composition, and the maintainer completed
the full human keyboard-only and NVDA Speech Viewer procedures at actual 100-percent and
200-percent Windows display/text scaling on 2026-08-09.

An agent-operated Windows smoke on 2026-08-09 completed the native chooser import with UI
Automation assistance, exercised organizer keyboard commands, cancellation, clean close/reopen,
and a full process restart while NVDA 2026.1.1 was installed and running. This is supplemental tool
evidence, not a human keyboard-only or NVDA Speech Viewer result. Windows Settings and Control
Panel were not targetable through the agent's Windows automation surface, so the agent did not
perform or claim an actual 200-percent Windows scaling run.

The maintainer reported no focus, pointer-dependency, clipping, reachability, role/state,
announcement, cancellation, recovery, or scaling issue. PWS-0111's required human accessibility
gate is therefore complete.

## Maintainer-operated keyboard and NVDA result — 2026-08-09

- **Provenance:** human maintainer-operated run; not agent automation.
- **Environment:** Windows 11 build 26200, Python 3.14.6, PySide6/Qt 6.11.1, NVDA 2026.1.1 with
  Speech Viewer.
- **Scaling:** actual 100-percent and 200-percent Windows display/text scaling.
- **Document:** repository synthetic `render-calibration.pdf`; no applicant document was used.
- **Keyboard-only result:** PASS. The native chooser import, prominent empty/running guidance,
  two-page completion, rotation, movement, undo/redo, full-order dialog, removal/restoration,
  cancellation, save, close/reopen, process restart, and persisted organizer state completed
  without pointer use or focus anomaly.
- **NVDA result:** PASS. Names, roles, enabled/disabled states, page position/rotation/preview state,
  progress, cancellation, completion, safe failure/recovery, and action availability were
  understandable. No missing, duplicate, path-bearing, or filename-bearing announcement was
  observed.
- **200-percent result:** PASS. Guidance, page rows, status, Cancel, Retry, and every organizer
  action remained visible or keyboard/scroll reachable; no clipping or unreachable control was
  observed.
- **Known limitations:** the four documented limitations below were reviewed; none caused a failure
  in the tested organizer workflow.

## Automated evidence

Run from the repository root with the default locked environment:

```text
uv run --no-sync python tools/dev.py test-accessibility
uv run --no-sync pytest tests/unit/test_organizer_ui_model.py
uv run --no-sync pytest tests/integration/test_organizer_ui_windows.py
uv run --no-sync pytest tests/integration/test_organizer_ui_lifecycle.py
uv run --no-sync pytest tests/security/test_page_organizer_privacy.py
```

The tests cover widget names/descriptions, guarded keyboard shortcuts, focus traversal, truthful
textual preview state, cancel focus, non-drag ordering and edge-button state, a scroll-reachable 960
by 540 logical viewport with 200-percent text, authoritative-state synchronization, bounded
visible/selected preview batches and Qt pixmap eviction, operation-correlated cancellation on a real
Qt thread, autosave-before-preview ordering, the committed-import close/reopen recovery lock and
focus target, prominent path-free empty/importing/cancelled/failed/reopen-required organizer
guidance, recovered-case review status, autosave/restart
reconstruction, explicit failed-preview retry with a fresh correlated operation (`Ctrl+Shift+R`),
actionable import-failure focus, visible error/status focus treatment, and unknown-error privacy.

## Agent-operated production smoke — 2026-08-09

The production exact-object inventory authenticated 12,091 resources from the rebuilt current-user
environment. The following live composition test then passed both cases in 258.52 seconds:

```text
uv run --locked --no-sync pytest -q tests/integration/test_organizer_ui_windows.py
```

With NVDA 2026.1.1 installed and running, the agent launched PaperWorkShell and created a case.
`Ctrl+I` opened the native Windows chooser; the separate dialog was targeted through UI Automation
and the repository synthetic `render-calibration.pdf` path was supplied. The import completed with
two pages. Keyboard input exercised organizer rotation and movement, undo and redo, removal and
restoration, and cancellation of active preview work.

The agent cleanly closed and reopened the case, then terminated and relaunched the complete
PaperWorkShell process. The reopened organizer preserved manifest revision 8, both pages, and Page
2 at 90-degree rotation. This closes the earlier agent-tool native-chooser gap but does not prove
human keyboard-only operation, NVDA announcements, or Speech Viewer output.

The agent could not target Windows Settings or Control Panel through the available Windows
automation surface. No actual 200-percent Windows display/text scaling result is claimed; the
automated constrained-viewport coverage described above is separate evidence.

## Earlier automation-assisted keyboard evidence — 2026-08-05

On 2026-08-05 the Windows desktop application was exercised with the repository synthetic
two-page render-calibration PDF. The run used keyboard shortcuts to open the case, rotate a page,
move it later, undo and redo, use the full-order dialog, remove and restore a page, cancel active
preview work, close during worker cleanup, exit, restart, and reopen the same internal case. Focus
returned to the active-page list, and revision 8 reopened with the expected 90-degree and 0-degree
rotations.

`Ctrl+I` opened the native Windows file chooser and `Alt+F4` cancelled it, but the automation bridge
could not target that separate native dialog to type the synthetic path. This is tool evidence, not
a substitute for the human keyboard-only import-selection step below. No pointer-only success and
no completed manual keyboard scenario are claimed.

## Keyboard-only scenario

Use only the keyboard and a repository synthetic PDF; do not use applicant documents.

1. Start `uv run paperworkshell` and confirm focus begins in the case chooser.
2. Press `Ctrl+N` and confirm the empty organizer opens with focus on Import document. Confirm the
   prominent organizer guidance says there are no pages yet and identifies Import document
   (`Ctrl+I`) as the next action without requiring the workflow scrollbar.
3. Press `Ctrl+I`, enter the path to
   `tests/fixtures/synthetic/v1/pdf/render-calibration.pdf`, and press Enter.
4. Confirm the guidance changes to Importing document, says to keep PaperWorkShell open, and adds
   the current phase plus completed/total count as progress arrives. Keep the application open until
   the import completes and the two page rows appear; do not close it merely because the page area
   is still empty. Confirm the global Operation status remains visible as the canonical detailed
   status. If a preview must stop first, confirm the inline notice temporarily reports Import queued
   before changing to Importing document. While a preview is active, use Tab to reach Cancel current
   operation and confirm it has a visible focus indicator; do not cancel this run. For the deliberate
   safe preview failure later in the scenario, use `Ctrl+Shift+R` or activate Retry selected preview
   and confirm a fresh attempt starts for only the selected failed row.
5. In Active pages, use arrow keys to select the first page. Press `Ctrl+R` and confirm the row text
   reports 90-degree rotation.
6. Press `Alt+Down` and confirm the selected page moves one position later. Press `Ctrl+Z` and
   `Ctrl+Y` and confirm the exact sequence changes each time.
7. Press `Ctrl+Shift+O`; use Tab plus `Alt+Up`/`Alt+Down` in the order dialog, then accept it without
   dragging. Confirm Move earlier is unavailable at the first position, Move later is unavailable at
   the last position, and each move reports the new position in visible status text.
8. Press Delete, confirm the selected page leaves the active packet, then press `Ctrl+Z` to restore
   it.
9. Wait for “All organizer changes saved,” close the case, exit, restart, reopen the same internal
   case, and confirm order and rotation persist.
10. At 200-percent display/text scaling with a constrained 960 by 540 logical viewport, use Tab and
    the workflow scrollbar to reach every chooser/organizer action plus status, Cancel, and Retry.
    Create one more empty case and confirm its no-pages guidance remains visible without scrolling;
    start the synthetic import and confirm the importing guidance remains visible while page rows
    have not yet appeared.

Record pass/fail, focus anomalies, display scaling, and any control that required a pointer.

## NVDA smoke procedure

After NVDA is installed:

1. Start NVDA with its default desktop profile, then start PaperWorkShell.
2. Traverse the case chooser with Tab and Shift+Tab. Confirm the heading, case list, Create new case,
   Open selected case, and Refresh case list have useful names, roles, and states.
3. Create a case and confirm NVDA can reach the empty-case meaning through “Page organizer
   guidance.” Import the synthetic PDF, keep PaperWorkShell open until it completes, and confirm the
   visible guidance covers importing phase/count while the global status announces import phases,
   numeric progress, and completion without exposing a path or filename. Deliberately cancel one
   later preview and confirm its announcement occurs once.
4. Traverse Active pages. Confirm each row announces “Page N of M,” rotation, and the truthful
   preview state: not loaded, loading, ready, failed, or unavailable as applicable.
5. Exercise Rotate, Move, Remove, Undo, Redo, Change full order, Retry selected preview, and Retry
   saving changes using only keyboard controls. Confirm disabled actions are reported as unavailable
   and focus remains visible.
6. Trigger one synthetic safe failure and confirm the stable recovery message is announced and
   focus moves to its recovery action.
7. Repeat at 200-percent Windows text/display scaling and note clipped, unreachable, duplicated, or
   missing announcements.

If a run reaches a terminal import outcome, also record whether the inline guidance is accurate:
cancellation must say that no source was committed; failure must point to Action error and identify
Import document as the retry action while Action error remains visible, then retain a truthful retry
instruction if another status clears that error; a committed `reopen_required` outcome must say that
the import was saved and direct the user to Close case and reopen it. If the window is closed during
import, confirm the guidance says that safe cancellation and secure cleanup are still in progress
until shutdown completes, or until a focused retry action explains that shutdown was refused.
These inline notices supplement the global status, which remains the canonical announced operation
record.

## Known limitations to confirm

- The icon-mode page list relies on Qt's native list/item accessibility mapping; the first NVDA run
  must confirm that row boundaries and selection state are understandable.
- Preview pixels have no semantic document-content description. Rows announce only page position,
  rotation, and preview availability because document text and imagery must not enter UI metadata.
- This historical PWS-0111 run preceded PWS-0201, so JPEG and PNG previews were unavailable then.
  PWS-0201 now routes validated image previews through the same keyboard-operable rows, progress,
  cancellation, focus, and byte-budgeted cache behavior; the next milestone smoke should include
  one synthetic image page without reinterpreting the earlier manual evidence.
- Announcement throttling is phase/count based. The smoke must identify any repeated or missed Qt
  accessibility announcements under real worker timing.
