# PWS-0209 Milestone 2 keyboard and NVDA smoke record

Record date: 2026-08-21

Platform: Windows 11 build 26200, Python 3.14.6, Qt/PySide6-Essentials 6.11.1,
NVDA 2026.1.1, Tesseract 5.5.3

Scope: OCR-language management, JPEG import, scan-adjustment review, quality review,
cancellation, retry, enlarged UI, and operation announcements

## Current result

**PASS for the PWS-0209 agent-operated smoke.** Codex computer-use drove the production
PaperWorkShell window by keyboard and inspected visible state while NVDA 2026.1.1 ran with a
private temporary configuration and recorded speech output. The workflow used only a generated
synthetic RGB JPEG. No applicant document, document text, source name, filesystem path, or OCR
text is recorded in this report.

This result is an agent-operated product smoke, not human screen-reader certification. The host
used 125-percent Windows display scaling plus `QT_SCALE_FACTOR=1.6`, producing an effective
approximately 200-percent Qt UI scale. The observed PaperWorkShell window was 698 by 607 display
pixels. This is not claimed as an actual 200-percent Windows display/text setting or as a logical
viewport measurement.

## Scenario result

| Area | Observed result |
|---|---|
| Launch and import | The production app launched, `Ctrl+I` opened the native chooser, and a generated synthetic RGB JPEG imported successfully as Page 1 of 1. Preview progress and completion remained visible and announced. |
| OCR languages | `Ctrl+Shift+L` opened Manage OCR languages. English was checked and announced as Installed and verified; German was unchecked and announced as Not installed. The dialog stated that checks use local files only, no data is downloaded, and offline installation instructions were available. |
| Scan adjustments | Review scan adjustments exposed Original, Adjusted, and Side by side views plus keyboard-adjustable provisional controls. Wording stated that the original is never changed. Escape requested cancellation while preview work was active. The final state said previews were cancelled/not produced and adjustments were not applied; controls recovered. |
| Quality cancellation | `Ctrl+Shift+Q` started the revision-bound check. Escape announced that cancellation was requested and that PaperWorkShell was waiting for safe stop. Completion said no review results were applied, and Check page quality became available again. |
| Quality retry | Retrying the same saved revision progressed through previewing, OCR reviewing, analyzing, and complete. The UI reported one `Needs review` low-source-resolution signal and said automatic suggestions do not complete a requirement. |
| Navigation and focus | The finding list exposed keyboard selection and Enter/Go to selected page navigation. Cancel and retry controls were reachable, visible focus remained present, and no pointer-only action was needed. |
| Enlarged layout | Language, scan-review, quality-review, finding, and operation-status content remained scroll/keyboard reachable at the effective enlarged scale. Long text wrapped or was horizontally scrollable; no control became unreachable. |
| Cleanup | PaperWorkShell closed with `Alt+F4`; NVDA then closed. No PaperWorkShell worker, Tesseract, or NVDA process remained, and the persistent worker-ACL recovery ledger was empty. |

## NVDA announcement evidence

The speech trace included useful names, roles, states, descriptions, and operation transitions for:

- PaperWorkShell workflow and operation status, Active pages, and Page 1 of 1;
- Manage OCR languages, the language actions group, English checked/installed/verified, local-only
  checking, and completion with one available language;
- Review scan adjustments, the safe Cancel default, preview preparation, cancellation requested,
  and cancellation complete without applying changes;
- Page quality review, Check page quality, Cancel current operation, previewing, OCR reviewing,
  analyzing, complete, cancellation requested, and cancelled with no results applied;
- the completed Page 1 `Needs review` signal, its navigation action, and the statement that the
  automatic suggestion does not complete a requirement.

No missing-language state was presented as an engine failure, no quality result was announced as
certainty, and no cancellation was announced as complete before cleanup finished.

## Automated companion evidence

The exact review revision also passed:

```text
.venv\Scripts\python.exe tools\dev.py test-accessibility
.venv\Scripts\python.exe -m pytest -q tests\integration\test_quality_ocr_review_worker_windows.py tests\integration\test_pws0209_mixed_searchable_export_gate_windows.py
```

The automated accessibility lane contains 39 passing tests for labels, descriptions, keyboard
shortcuts, focus recovery, scrolling/reachability, cancellation wording, advisory finding
semantics, missing-language recovery, and signed-derivative confirmation. The two live gate tests
prove content-free quality-OCR success/cancellation/retry and the mixed searchable export in
restricted workers.

## Limitations

- This was Codex computer-use with an NVDA speech trace, not a human NVDA usability assessment.
- Effective approximately 200-percent Qt scaling is not an actual 200-percent Windows
  display/text-setting result. The earlier PWS-0111 maintainer record retains the product's human
  100-percent and 200-percent Windows organizer evidence.
- The desktop does not yet expose searchable-PDF export as a window action. The mixed export gate
  evidence is a production application/worker integration scenario; PWS-0408 owns guided export.
- The smoke proves discoverability, state, cancellation, focus, and wording for the tested
  synthetic workflow. It does not certify OCR accuracy, PDF/UA, legal validity, portal acceptance,
  or every screen-reader/user combination.
