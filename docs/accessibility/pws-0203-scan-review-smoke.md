# PWS-0203 scan-review keyboard and 200-percent scaling smoke record

Task: PWS-0203  
Scope: before-and-after JPEG/PNG scan review, tentative controls, comparison, cancellation, and
explicit Apply  
Fixture: `tests/fixtures/synthetic/v1/image/render-calibration-crop-rotate.png`

## Result record

This record was completed against the clean implementation revision below. The required layout pass
used the real Windows Qt platform at an **effective device-pixel ratio of 2.0**, supplied by a
process-local Qt multiplier calibrated against the recorded Windows base scale. Offscreen Qt and
enlarged-font-only coverage remain supplemental. The review did not change the operator's
system-wide display settings.

| Field | Result |
|---|---|
| Review date | **2026-08-14** |
| Reviewer/operator and provenance | **Codex agent using the Windows computer-use controller against the production PaperWorkShell application; the recorded workflow used keyboard input only and was not a human or NVDA review. An earlier pointer-assisted scroll observation was discarded and rerun by keyboard.** |
| Tested implementation revision | **`e25098f6064236f469f6d34b0a5b8435099e8667`** |
| Windows edition/build | **Windows 11 Pro, build 26200** |
| Python version | **3.14.6** |
| PySide6/Qt version | **PySide6 6.11.1 / Qt 6.11.1** |
| Windows base display scale / system DPI | **125% / 120 DPI** |
| Process-local Qt scale multiplier | **1.6 (`QT_SCALE_FACTOR=1.6`)** |
| Effective Qt device-pixel ratio | **2.0** |
| Windows text-size setting | **Default 100%; no `TextScaleFactor` override was present** |
| Logical application viewport | **Available screen geometry was 960 by 510 logical pixels at DPR 2.0; the production dialog used keyboard scrolling under that constraint. A separate dialog/client-area measurement was not instrumented.** |
| Keyboard-only result | **PASS — case creation, native-chooser import, review opening, full forward/reverse traversal, cancellation, retry, every adjustment category, comparison modes, Reset, rejection, Apply, reopen, Undo, and Redo were completed by keyboard.** |
| Original/adjusted visual comparison result | **PASS — Original remained the literal immutable-source preview. Quarter-turn candidates were visibly distinct at 90 and 180 degrees while Original remained unchanged.** |
| Progress/cancellation/error result | **PASS — progress and cancellation were visible and reachable; cancellation applied nothing. A deliberately terminated one-shot preview worker produced only `IMAGE_PAGE_DEPENDENCY_FAILED: A required image-processing component could not complete safely.`, focused the error, retained Original, enabled Update, and a fresh keyboard retry succeeded.** |
| 200-percent reachability result | **PASS — 25 forward and 25 reverse focus steps showed visible focus without a trap; keyboard-only PageDown moved the review scroll area and all critical actions, status, previews, and recovery controls remained reachable.** |
| Focus anomalies | **None observed** |
| Clipped/truncated/unreachable controls | **None observed; wrapped content and keyboard scrolling kept recovery and action controls reachable** |
| Residual issues | **NVDA was not run; PWS-0209 owns the complete Milestone 2 NVDA gate. The synthetic local smoke case was retained rather than deleting user-profile data without separate authorization.** |
| Final result | **PASS** |

## Prerequisites and safety

1. Use a clean build of the recorded implementation revision and the locked repository
   environment.
2. Use only the repository synthetic fixture named above. Do not import an applicant or other
   personal document and do not capture a screenshot containing one.
3. Record the fixture SHA-256 before the run:

   ```powershell
   Get-FileHash tests\fixtures\synthetic\v1\image\render-calibration-crop-rotate.png -Algorithm SHA256
   ```

4. Record Python and Qt versions without changing the environment:

   ```powershell
   .venv\Scripts\python.exe --version
   .venv\Scripts\python.exe -c "from PySide6.QtCore import qVersion; import PySide6; print(PySide6.__version__, qVersion())"
   ```

5. Record the Windows base DPI without changing it. Run the real Windows Qt platform at an
   effective device-pixel ratio of exactly 2.0. If the Windows session is not already at 200%, use
   a process-local `QT_SCALE_FACTOR` chosen so `QScreen.devicePixelRatio()` reports `2.0`; record
   both the base DPI and multiplier. Do not count an offscreen platform run as this manual result.
6. Resize the application or exact production dialog to a 960 by 540 logical-pixel viewport where
   practical. Record the
   actual logical size if Windows or the window manager constrains it.

## Keyboard-only review procedure

Do not use the pointer during this section. Record the first step that cannot be completed by
keyboard as a failure.

1. Start PaperWorkShell from the locked environment:

   ```powershell
   uv run --locked --no-sync paperworkshell
   ```

2. Create a case with `Ctrl+N`, then import the synthetic PNG with `Ctrl+I`. Complete the native
   chooser by keyboard and wait for import plus the bounded organizer preview to finish.
3. Select the synthetic image page with the arrow keys. Press `Ctrl+Shift+A`, or Tab to **Review
   scan adjustments** and activate it. Record the actual visible action text and shortcut if it
   differs.
4. Confirm focus enters the scan-review workflow on a safe, visible control. Confirm the dialog
   states that the original remains unchanged, that the adjusted image is a preview, and that
   nothing has been applied.
5. Traverse forward and backward with Tab and Shift+Tab. Record whether all of the following are
   reachable with visible focus:

   - Original preview and its textual state;
   - Adjusted preview and its textual state;
   - comparison mode/action;
   - crop left, top, right, and bottom edge controls;
   - clockwise quarter-turn control;
   - fine deskew control;
   - contrast control;
   - background-cleanup control;
   - update/retry preview action, if separately exposed;
   - reset action;
   - preview cancellation while work is active;
   - reject/cancel review action; and
   - Apply adjustments.

6. Adjust each crop value using only the keyboard. Keep a positive crop inside the page. Confirm
   the adjusted result is visibly/textually marked out of date and Apply is unavailable until the
   exact new preview completes.
7. Select each clockwise quarter-turn value using the keyboard. Confirm 90 degrees rotates in the
   documented clockwise direction and that the Original preview does not rotate with the draft.
8. Change deskew in both directions, contrast below and above neutral, and background cleanup above
   zero. Confirm visible values include understandable units and remain within the documented
   bounds.
9. Start the adjusted preview. While progress is active, confirm the visible phase/count and
   cancellation action remain reachable. Cancel by keyboard. Confirm the terminal message says no
   adjustment was applied, the Original preview remains available, and focus returns to an
   actionable update/retry control.
10. Retry and wait for the exact current adjusted preview. Exercise every offered comparison mode
    by keyboard and confirm Original and Adjusted remain distinguishable without relying on color.
11. Activate **Reset to original** (accessible name **Reset adjustments to original**). Confirm
    controls return to neutral source-relative values, the
    suggestion state clears, and the adjusted preview again becomes out of date. Reject the dialog
    with Escape or the visible Cancel action.
12. Reopen scan review and confirm the manifest revision and persisted page intent are unchanged by
    the earlier preview, reset, cancellation, and dialog rejection. This check may use the
    application-visible page state plus the automated manifest-invariance evidence; do not expose a
    case path in the record.
13. Enter a non-neutral candidate again, produce its exact adjusted preview, then choose **Apply
    adjustments** by keyboard. Confirm completion is announced as queued/saved intent rather than
    “document improved,” “check complete,” or another quality claim.
14. Wait for autosave, close and reopen the case, and confirm the applied transform intent remains.
    Exercise organizer Undo and Redo and confirm the reviewed change participates in normal
    history. Confirm the imported source hash remains the same as its pre-review hash using the
    automated immutable-source evidence; do not copy an internal case path into this record.

## Effective 200-percent Windows scaling review

With the real Windows Qt platform still reporting an effective device-pixel ratio of `2.0`:

1. Repeat dialog opening, full forward/backward focus traversal, one adjustment, preview
   cancellation/retry, Reset, Cancel, and Apply.
2. Use the review area's keyboard scrolling where content exceeds the viewport. Confirm the action
   controls, status, progress, error/retry, and both preview states remain reachable.
3. Confirm labels and critical messages wrap instead of truncating essential recovery text.
4. Confirm no control is hidden behind a fixed minimum size and no nested scroll surface traps
   keyboard focus.
5. Confirm focus remains visibly distinguishable at every step and moves to a visible safe target
   before the dynamic **Cancel preview** control is hidden.
6. Record every clipped label, overlapping widget, unreachable control, unexpected focus jump, or
   pointer-only dependency. Any critical-control reachability failure makes the result **FAIL**.

## Optional screen-reader observation

PWS-0209 owns the complete Milestone 2 NVDA gate, so this section is useful but not a substitute for
that gate. If NVDA Speech Viewer is used, record its version and whether it announces control names,
units, preview state, “Needs review” suggestion wording, bounded progress, cancellation, failure,
and Apply completion without source names, paths, metadata, hashes, or document content.

NVDA result: **NOT RUN — PWS-0209 owns the complete Milestone 2 NVDA gate.**

## Automated companion evidence

The clean implementation revision passed the following companion evidence:

```text
.venv\Scripts\python.exe tools/dev.py check
PASS in 4704.2 seconds: 394 format/type surfaces; 13 import layers across 143 source files;
3098 unit, 388 integration with 7 disclosed skips, 331 security with 6 disclosed skips,
and 31 accessibility tests; fixtures, schemas, dependencies, task/docs/resource validators,
the bounded repository secret scan, and all 5 quality self-tests passed.

.venv\Scripts\python.exe -m pytest -q tests/unit/test_scan_review_service.py
tests/unit/test_scan_review_dialog.py tests/unit/test_scan_review_window.py
tests/unit/test_scan_review_backend_bridge.py tests/accessibility/test_scan_review_accessibility.py
tests/security/test_scan_review_privacy.py
PASS: 69 tests in 2.77 seconds.

.venv\Scripts\python.exe -m pytest -q tests/unit/test_organizer_backend.py -k scan_review
PASS: 7 tests, 21 deselected, in 0.35 seconds.

.venv\Scripts\python.exe -m pytest -q
tests/integration/test_organizer_ui_windows.py::test_live_scan_review_preserves_original_and_reopens_only_applied_intent
PASS: 1 test in 420.52 seconds.

.venv\Scripts\python.exe -m pytest -q
tests/integration/test_organizer_ui_windows.py::test_live_composition_imports_and_renders_bounded_organizer_previews
PASS: 1 test in 419.29 seconds.

.venv\Scripts\python.exe -m pytest -q
tests/integration/test_m1_vertical_slice_windows.py::test_m1_vertical_slice_is_deterministic_recoverable_and_private
PASS: 1 test in 1264.35 seconds.
```

Automated tests must cover doubled application text with a constrained 960 by 540 logical viewport,
but that result remains separate from the manually inspected real-Windows 200-percent record above.

## Sign-off

Reviewer statement: **The agent-operated production-app review completed the required keyboard,
effective-DPR-2.0, progress/cancellation/error, comparison, reset/reject, Apply/persistence, and
Undo/Redo checks using only the named public synthetic fixture. No applicant data was used. This is
not a human usability or screen-reader certification.**

Final conclusion and rationale: **PASS — every PWS-0203 acceptance criterion was exercised on the
clean implementation revision. Original bytes and the approved golden index remained unchanged;
tentative work did not mutate the manifest, exact Apply persisted and reopened normally, failure
and cancellation recovered safely, and all critical controls remained keyboard-reachable at an
effective Qt DPR of 2.0.**
