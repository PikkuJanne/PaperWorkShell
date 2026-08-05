# PWS-0111 keyboard-only and NVDA smoke record

Record date: 2026-08-05

Platform: Windows, Python 3.14, Qt/PySide6-Essentials 6.11.1
Scope: initial case chooser and page organizer vertical slice

## Current result

The automated accessibility and keyboard checks pass. An automation-assisted Windows scenario
also exercised organizer keyboard commands, cancellation, shutdown, and restart persistence, but
it could not enter a path in the native file chooser. A complete human keyboard-only scenario and
the required NVDA smoke are therefore not yet claimed in this record. NVDA was not installed or
discoverable in the command path or standard installation locations when checked on 2026-08-05.
The maintainer has been asked to install it. No different screen reader is being substituted.

This missing external verification keeps PWS-0111 from being marked done even though implementation
and non-NVDA validation may proceed.

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
focus target, recovered-case review status, autosave/restart
reconstruction, and unknown-error privacy.

## Automation-assisted keyboard evidence

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
2. Press `Ctrl+N` and confirm the empty organizer opens with focus on Import document.
3. Press `Ctrl+I`, enter the path to
   `tests/fixtures/synthetic/v1/pdf/render-calibration.pdf`, and press Enter.
4. Confirm phase and numeric progress text is visible. While a preview is active, use Tab to reach
   Cancel current operation and confirm it has a visible focus indicator; do not cancel this run.
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

Record pass/fail, focus anomalies, display scaling, and any control that required a pointer.

## NVDA smoke procedure

After NVDA is installed:

1. Start NVDA with its default desktop profile, then start PaperWorkShell.
2. Traverse the case chooser with Tab and Shift+Tab. Confirm the heading, case list, Create new case,
   Open selected case, and Refresh case list have useful names, roles, and states.
3. Create a case and import the synthetic PDF. Confirm import phases, numeric progress, completion,
   and a deliberately cancelled preview are announced once without exposing a path or filename.
4. Traverse Active pages. Confirm each row announces “Page N of M,” rotation, and the truthful
   preview state: not loaded, loading, ready, failed, or unavailable as applicable.
5. Exercise Rotate, Move, Remove, Undo, Redo, Change full order, and Retry saving changes using only
   keyboard controls. Confirm disabled actions are reported as unavailable and focus remains visible.
6. Trigger one synthetic safe failure and confirm the stable recovery message is announced and
   focus moves to its recovery action.
7. Repeat at 200-percent Windows text/display scaling and note clipped, unreachable, duplicated, or
   missing announcements.

## Known limitations to confirm

- The icon-mode page list relies on Qt's native list/item accessibility mapping; the first NVDA run
  must confirm that row boundaries and selection state are understandable.
- Preview pixels have no semantic document-content description. Rows announce only page position,
  rotation, and preview availability because document text and imagery must not enter UI metadata.
- JPEG and PNG previews are explicitly unavailable until PWS-0201, although those pages remain
  keyboard-organizable.
- Announcement throttling is phase/count based. The smoke must identify any repeated or missed Qt
  accessibility announcements under real worker timing.
