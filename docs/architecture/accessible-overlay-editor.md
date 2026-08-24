# Accessible overlay editor

- Status: Implemented by PWS-0306
- Depends on: PWS-0301 coordinates, PWS-0302 intent, PWS-0304 core tools,
  PWS-0305 signature-image assets, and PWS-0111 accessibility baseline
- Decisions: PWS-D018, PWS-D020, PWS-D030

## Scope and authority

PWS-0306 extends the existing revision-, page-, and session-bound
`CoreOverlayToolsState`; it does not create a second persistence path. The editor opens only after
the organizer has flushed pending page intent. Apply still builds a candidate from authenticated
`OverlayCommandHistory`, writes through the atomic manifest repository, reopens the saved revision,
and reports success only after the existing correlation checks pass. Cancel discards only a local
draft that has not crossed the persistence boundary. A failed apply retains the exact draft for
retry. The backend retains a separately reconstructed, never-presented opening history and scalar
case/revision/page/session authority; mutating a GUI-returned frozen object by reflection therefore
cannot mutate backend authority. If a start result is unusable, a state-free backend abort releases
that private authority without trusting the rejected presentation object.

The full-page canvas is a disposable projection. Normalized millionth intent remains authoritative.
Zoom, pan, guide visibility, guide spacing, pointer-drag state, and snap-toggle state are never
written to the manifest or command history.

## Capabilities by overlay kind

| Overlay kind | Select and inspect | Move/resize | Payload edit | Remove in this editor |
| --- | --- | --- | --- | --- |
| Text, date, initials | Yes | Yes | Value, approved font size, named color | Yes |
| Checkmark, cross | Yes | Yes | Named color | Yes |
| Signature image | Yes | Yes | No | No; use the explicit organizer cleanup workflow |
| Highlight, ordinary shape | Yes | Yes | No | No in this release |

All eight released `OverlayKind` values appear in the native list with type, ordinal, left, top,
width, and height. Ordinary shape/highlight rows also name the fixed primitive, such as Rectangle,
Ellipse, or Line. Each row states whether its full saved box is fully shown, partly shown, or outside
the active crop. Rows and announcements never contain textual values, signature pixels, asset
identifiers, hashes, filenames, or paths. Signature wording says that the placement is a visual
image, not a digital or qualified electronic signature. Highlight and shape wording describes an
ordinary visual mark and does not claim permanent removal or redaction.

`ReplaceOverlayBoxCommand` accepts geometry-only changes for every released overlay kind. The
history authenticator requires the same overlay ID, page, kind, payload, z-index, and version on
both sides of a non-core box delta. Core creation, payload replacement, and deletion remain
restricted to the five core types. In particular, generic deletion cannot bypass signature asset
retention/removal confirmation.

## Page projection and coordinates

The canvas receives only GUI-thread presentation facts:

- the already validated, bounded organizer `QPixmap`, when available;
- a pure `PageCoordinateTransform` containing the active normalized crop and accumulated
  quarter-turn; and
- an explicit flag stating whether pointer mapping is safe.

Painting calls `visible_part()` before mapping a box. A partial box is painted as its crop
intersection, but a move or resize always changes the original unprojected box. An outside-crop
overlay remains selectable and exactly editable in the native list even though it has no canvas
rectangle. Hit testing is topmost-first in reverse stack order. A pointer move is transient while
the pointer is held and produces at most one history command on release; it cannot exhaust history
with mouse-move events. Click-only and sub-threshold gestures are exact no-ops, even when pointer
snapping is enabled. Resize preserves the press-to-handle offset, and snap feedback is emitted only
when a pointer move actually changed an origin to a guide. Refresh, zoom, disable, and hide paths
cancel a live drag and release Qt mouse capture deterministically. A visible square handle offers
pointer resize only for an entirely visible box. Exact fields and named resize controls remain
available for every box.

`PageCoordinateTransform` deliberately does not model image deskew. For a non-zero saved deskew,
the window does not pass the transformed pixel preview to the editor and disables pointer mapping.
The canvas displays a labeled schematic instead. The list, exact fields, keyboard movement,
keyboard resize, and snapping remain fully operational, so the UI never approximates deskew and
saves a falsely aligned box. When pixels are intentionally suppressed, their bounded displayed
dimensions are retained as a geometry-only aspect fact; otherwise a crop/quarter-turn-aware
schematic aspect is used. Extreme narrow or wide pages are never stretched to a minimum short edge.

For PDFs, normalized space already follows the intrinsic effective page box; the organizer's
additional quarter-turn is the display rotation. For image pages, the transform also receives the
saved normalized crop. Display scaling and device-pixel ratio affect Qt painting only and are not
multiplied into normalized intent.

## Accessible interaction

The canvas is a named, non-focusable accessible graphic. It is pointer convenience, not a hidden
control surface. The native list and property panel provide the complete primary workflow:

- add any of the five core visual marks;
- select every released overlay type;
- read exact type and geometry;
- edit supported payload properties;
- move or resize by named 1% or 0.1% controls and shortcuts;
- enter exact millionths in spin boxes with equivalent percentage descriptions;
- snap the selected origin to a named normalized grid;
- undo, redo, apply, or cancel.

Zoom uses the fixed bounded choices 25%, 50%, 75%, 100%, 125%, 150%, 200%, 300%, and 400%, plus
Fit page. Named directional and Center buttons are the keyboard alternative to scroll bars. Zoom
and pan announce explicitly that saved positions did not change.

Guides use dashed lines, a visible text label, a named Show guides checkbox, and a native snap
checkbox. Snap feedback names the selected grid spacing in text and an accessibility announcement;
color is supplementary. A selected overlay uses a solid heavy outline, a content-free type label,
and a square handle, while unselected overlays use dashed outlines.

Opening focus goes to the selected native row or the Add type control when no record is selected.
Removing a core record moves focus and selection to the next/previous native row, or to Add type
when the page becomes empty. Preview painting and zoom do not steal focus. Apply failures focus the
existing visible status/recovery surface, and closing returns focus to the organizer's Edit visual
overlays button. A dialog-owned forward/reverse focus traversal closes one logical cycle across the
nested preview/control scroll area, fixed Apply/Cancel buttons, and focusable status surface. Hidden
or disabled payload controls are skipped. Accessible property, Apply, and removal descriptions are
recomputed for the selected kind, so signature/highlight/shape selections never claim that hidden
payload controls are editable.

## Privacy, hostile input, and resources

The UI imports no PDF parser, image decoder, storage adapter, signature rasterizer, or worker
implementation. It never reads a case asset or source path. Stored signature pixels are represented
by a labeled geometry box; only the existing page preview may be painted behind it.

The editor shares the organizer model's existing implicitly shared `QPixmap`; it does not copy it,
request a full-resolution document, or retain a second decoded page. The preview cache remains
bounded by the versioned `ResourcePolicy`. Missing or evicted pixels produce a labeled placeholder
without disabling exact editing. The canvas has fixed 25–400% zoom bounds, at most 99 vertical and
99 horizontal 1% guide lines, and at most the manifest's bounded overlay count. Repainting creates
no command and performs no I/O.

Every state/result remains correlated through the existing case, manifest revision, page, and
editor-session authority. Organizer page-fact changes invalidate model previews before reuse, and
a case change clears all cached pixels even if the next case reuses a page UUID. Preview scheduling
is suppressed while this modal is opening or active. The window suppresses a deskewed preview
rather than exposing a projection it cannot invert safely. While Apply is pending, display controls
and shortcuts cannot overwrite the persistent operation status; durable completion is announced
once from the main window.

## Verification boundary

Automated evidence covers all eight overlay kinds, coarse and fine keyboard geometry, native
selection/value accessibility interfaces, exact snapping, crop/rotation/deskew display facts,
partial and outside-crop behavior, topmost pointer selection and one-command drag, zoom/pan intent
invariance, focus/tab order, 200%-font constrained layout, and prohibited surface/log tokens. The
interactive NVDA and application-effective 200% Qt scaling scenario is recorded in
`docs/accessibility/pws-0306-overlay-editor-nvda-smoke.md`.

PWS-0306 adds no schema, migration, dependency, worker RPC, export format, or release interface.
PaperWorkShell remains standalone and owns its local cases, overlay intent, assets, previews, and
save decisions. No sibling repository, service, schema, or runtime is involved.
