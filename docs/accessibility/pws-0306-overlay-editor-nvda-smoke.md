# PWS-0306 overlay editor NVDA and effective-200% smoke record

- Date: 2026-08-24
- Platform: Windows
- NVDA: 2026.1.1
- Operator: Codex through the computer-use accessibility bridge (interactive, not a human assessment)
- Implementation revision: `56e187710f0131f11fb4227efd1a75e1be6d3f1e`
- Result: Pass

## Scope and data provenance

The production `CoreOverlayToolsDialog` was launched through
`tests/helpers/overlay_editor_manual_probe.py`. The probe constructs its case state from the
repository's synthetic representative manifest and paints a synthetic in-memory page preview. No
applicant document, external path, signature pixels, asset identifier, or other user content was
used.

NVDA ran from a disposable repository-local configuration with first-run prompts disabled and the
Speech Viewer enabled. The disposable profile was removed after the run; the user's NVDA profile
and Windows display configuration were not changed.

The dialog was launched with `QT_SCALE_FACTOR=1.6`. Its measured Qt title reported effective DPR
`2.00` and logical DPI `96.0`. An unobstructed captured image measured 1536 by 864 pixels. This
records an application-effective 200% Qt scaling scenario; it does not claim that the global
Windows display setting was changed to 200%.

## NVDA observations

- Opening announced “Edit visual overlays for selected page,” the complete-list/property-panel
  workflow, and that no pointer is required.
- The native overlay list announced the selected type, ordinal, left, top, width, height, active-crop
  visibility, and the available property path without announcing payload text or identifiers.
- Arrow-key selection was exercised for all eight released kinds: Text, Signature image, Rectangle
  highlight, Date, Checkmark, Cross, Initials, and Ellipse shape.
- Signature selection announced that position and size are editable, explicit asset cleanup uses the
  organizer removal action, and the placement is a visual image rather than a digital signature.
- Highlight and shape selection announced the fixed Rectangle/Ellipse primitive, read-only visual
  content, editable position and size, and that the ordinary visual mark is not permanent removal.
- Textual and symbol rows distinguished value/format/color capabilities from geometry-only kinds.
- `Alt+Arrow`, `Alt+Shift+Arrow`, `Ctrl+Alt+Arrow`, and
  `Ctrl+Alt+Shift+Arrow` produced distinct spoken move/resize results. The inspected shape moved from
  top 50% to 51%, grew from width 20% to 21%, moved left by the 0.1% fine step, and shrank from
  height 15% to 14.9%.
- `Ctrl+G` announced: “Snap feedback: selected mark aligned to the nearest 5 percent dashed guides.”
  The row then exposed left 50% and top 50%.
- The named Pan down button announced that the preview panned and saved overlay positions did not
  change. Fit page announced a centered 50% preview with saved positions and sizes unchanged. Zoom
  in announced 75% with saved positions and sizes unchanged.
- Reverse Tab traversed from the list through Add/type, Center and directional pan, snap, guide
  spacing, snap/guide checkboxes, Fit page, 100%, zoom in/out, and the zoom preset. Reverse Tab from
  the zoom preset wrapped to the focusable status surface; forward Tab wrapped back to the zoom
  preset.
- The fixed Cancel button announced that it discards the local draft without requesting a case
  change and exposed Escape. The fixed Apply button announced that it requests saving the complete
  local visual-overlay draft. Apply was activated with Enter and closed the synthetic dialog after
  the probe's in-memory callback simulated caller-confirmed apply success; this was not a persistence
  claim. A fresh run was closed through Escape to exercise Cancel.

## Effective-200% layout observations

- The full-page preview remained scrollable and did not become the only interaction surface.
- Zoom, guides, snap, pan, Add, and the native overlay list remained legible and reachable.
- The native list row retained exact geometry and crop text at the constrained window size.
- Apply and Cancel remained fixed and visible at the bottom while the property content scrolled.
- The accessibility tree continued to expose the focusable status surface plus the Apply and Cancel
  buttons in the clean 1536-by-864 capture.
- Visible focus remained present during keyboard traversal; no control required hover, drag, or
  color alone.

## Companion evidence and limits

This smoke run used an unrotated, non-deskewed synthetic page and intentionally used no pointer.
Automated PWS-0306 tests cover crop and quarter-turn projection, the non-zero-deskew schematic and
pointer suppression, partial/outside-crop records, topmost pointer hit testing, one-command drag,
sub-threshold no-op behavior, all exact fields, all eight overlay kinds, constrained 200%-font
layout, and zoom/pan intent invariance.

This interactive Codex/computer-use run validates the production accessibility surface and observed
NVDA Speech Viewer output. It is not a human NVDA assessment, a claim of PDF/UA conformance, or a
claim of compatibility with every screen reader.
