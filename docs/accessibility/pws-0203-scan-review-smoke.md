# PWS-0203 scan-review keyboard and 200-percent scaling smoke record

Task: PWS-0203  
Scope: before-and-after JPEG/PNG scan review, tentative controls, comparison, cancellation, and
explicit Apply  
Fixture: `tests/fixtures/synthetic/v1/image/render-calibration-crop-rotate.png`

## Result record

These fields must remain pending until the procedure below is performed on a clean implementation
revision. The required layout pass uses the real Windows Qt platform at an **effective device-pixel
ratio of 2.0**. That may be supplied by a dedicated Windows session already configured at 200%, or
by a process-local Qt multiplier calibrated against the recorded Windows base scale. Offscreen Qt
and enlarged-font-only coverage remain supplemental. The review must not change the operator's
system-wide display settings without explicit authorization.

| Field | Result |
|---|---|
| Review date | **PENDING** |
| Reviewer/operator and provenance | **PENDING** |
| Tested implementation revision | **PENDING** |
| Windows edition/build | **PENDING** |
| Python version | **PENDING** |
| PySide6/Qt version | **PENDING** |
| Windows base display scale / system DPI | **PENDING** |
| Process-local Qt scale multiplier | **PENDING — `none` when Windows is already at 200%** |
| Effective Qt device-pixel ratio | **PENDING — must equal 2.0** |
| Windows text-size setting | **PENDING** |
| Logical application viewport | **PENDING** |
| Keyboard-only result | **PENDING** |
| Original/adjusted visual comparison result | **PENDING** |
| Progress/cancellation/error result | **PENDING** |
| 200-percent reachability result | **PENDING** |
| Focus anomalies | **PENDING** |
| Clipped/truncated/unreachable controls | **PENDING** |
| Residual issues | **PENDING** |
| Final result | **PENDING — PASS or FAIL only after every required step** |

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

NVDA result: **PENDING / NOT RUN**

## Automated companion evidence

Record command results and exact test counts after implementation converges:

```text
.venv\Scripts\python.exe -m pytest -q tests/unit/test_scan_review_service.py tests/unit/test_scan_review_dialog.py
.venv\Scripts\python.exe -m pytest -q tests/unit/test_organizer_backend.py -k scan_review
.venv\Scripts\python.exe -m pytest -q tests/accessibility/test_scan_review_accessibility.py
.venv\Scripts\python.exe -m pytest -q tests/security/test_scan_review_privacy.py
.venv\Scripts\python.exe tools/dev.py test-accessibility
```

Automated tests must cover doubled application text with a constrained 960 by 540 logical viewport,
but that result remains separate from the manually inspected real-Windows 200-percent record above.

## Sign-off

Reviewer statement: **PENDING**

Final conclusion and rationale: **PENDING**
