# Core visual form-mark tools

- Status: Implemented by PWS-0304; full-page geometry extension implemented by PWS-0306
- Decisions: PWS-D011, PWS-D018, PWS-D030
- Depends on: PWS-0301 coordinates, PWS-0302 overlay intent, PWS-0303 font strategy

## Scope

PWS-0304 supplies creation, selection, movement, resizing, property editing, removal, undo, and
redo for text, date, checkmark, cross, and initials overlays. It uses the released manifest-v1/v2
`overlays` array and `overlay_version: 1`; it adds no schema member, page-operation payload, worker
RPC operation, native PDF form behavior, or executable content.

The implementation has three narrow layers:

| Layer | Responsibility |
|---|---|
| `paperworkshell.overlays.commands` | Pure validated commands, compact reversible deltas, exact overlay-state replay, and candidate construction |
| `paperworkshell.app.core_overlay_tools` | Revision/page binding, ephemeral selection, deterministic defaults, keyboard movement/resizing, content-free labels and summaries |
| `paperworkshell.ui.overlay_tools` | A keyboard-complete full-page projection, native list, exact properties, and display controls with no document parsing or persistence authority |

PWS-0306 now extends this same authority with the [accessible overlay
editor](accessible-overlay-editor.md): selection and geometry for all released kinds, visual
selection outlines, bounded pointer hit testing and drag, a resize handle, dashed guides,
normalized snapping, fixed zoom, named pan alternatives, preview invalidation, and meaningful
assistive geometry. The PWS-0304 exact list/property path remains authoritative.

## Command and history contract

The command set is closed: create one core overlay, replace one normalized box for any released
overlay kind, replace a same-kind core payload, or delete one core overlay. A non-core box delta
must retain exact identity, page, kind, payload, z-index, and version. Selection is transient UI
state and is neither persisted nor
undoable. Every command produces one immutable before/after delta; history does not retain a copy
of the whole overlay array for every step.

Session history is bounded to 4,096 owned command deltas as well as the released 4,096-overlay
ceiling. Identifier generation makes at most 32 attempts and checks the complete manifest identifier
namespace. IDs allocated by commands
remain reserved after undo and after a new branch clears redo, so a session never reuses a discarded
identity. Create appends the record and assigns the next available top layer for its page without
renumbering existing sparse layers. Update retains the original array position and layer. Delete
records the original position, and undo/redo restores the exact ID, array order, layer, box, payload,
and user-entered display value.

Candidate construction accepts only a current overlay array that is an exact state previously
produced by the history. An unknown concurrent overlay change fails with `OVERLAY_CONCURRENT_CHANGE`.
At the organizer boundary, a submitted history must also replay from a separately reconstructed,
backend-private opening history and every owned delta must affect only the editor's bound page.
The backend retains only independent scalar authority plus that unshared history; reflective
mutation of the GUI-returned draft cannot mutate the authority baseline. Copying the session
authority onto a history for another known page is rejected before autosave.
The candidate is a defensive copy of the latest manifest and replaces only `overlays`; pages,
assets, page operations, assignments, constraints, confirmations, export-history references,
timestamps, and revisions remain owned by their existing services. The complete overlay collection
and identifier namespace are revalidated before a candidate is returned.

Only final overlay intent is durable. The undo/redo command ledger and selection are bounded
process-local editor state and reset after the dialog is applied, cancelled, or reconstructed from
a saved case. This preserves the released schema; durable autosave revisions and the recovery
journal remain the crash-recovery mechanism.

## Defaults and value handling

New textual overlays use the approved deterministic `paperworkshell-default` font, 12 points, and
opaque black `#000000ff`. Checkmarks and crosses use the same opaque black. Each kind has a fixed
positive initial full-page normalized box. The editor exposes exact integer-millionth coordinates,
so a keyboard user can reach every valid saved position without quantization through display pixels.

Text, initials, and dates are inert strings of at most the released 4,096 code points. In particular,
a date is stored exactly as entered. No parser trims it, selects a locale, supplies the current date,
or converts it into another display format. Shell metacharacters, markup-like text, paths, URLs, and
words associated with signatures or redaction remain ordinary inert display content.

The Qt field reserves enough UTF-16 capacity to preserve 4,096 supplementary Unicode characters
and admit one over-limit code point for explicit domain rejection; it does not silently shorten a
4,097-code-point edit into an apparently valid value. If a released saved string contains a Python
surrogate that Qt cannot represent, unrelated property edits are blocked until the user explicitly
replaces that value, so the existing wire value is never silently changed.

Representations, stable errors, list rows, status messages, accessibility announcements, and routine
logs never include a text, date, or initials value. Rows identify only the kind, ordinal, and normalized
geometry. Editor announcements and accepted presentation error codes use closed vocabularies;
presentation recovery messages are fixed rather than trusted from a typed payload. Errors carry a
closed code, structural context, and plain-language safe message.

## Keyboard and accessibility contract

The organizer exposes **Edit form marks…** with `Ctrl+Shift+F`. The modal dialog contains a
keyboard-selectable, non-draggable overlay list; an explicit five-kind add control; value, font,
named-color, and exact geometry properties; and visible Apply properties, Remove, Undo, Redo,
Apply form marks, and Cancel actions.

Within the dialog:

- arrow keys select list rows through standard list behavior;
- `Alt+Arrow` moves by one percent of the full page;
- `Alt+Shift+Arrow` changes width or height by one percent;
- exact geometry controls provide the page-edge and fine-placement path;
- `Ctrl+Enter` or `Ctrl+Return` applies property fields, `Delete` removes, and `Ctrl+Z`/`Ctrl+Y`
  undo/redo; and
- invalid edge movement is rejected rather than clipped, with a textual, focusable announcement.

Every control has an accessible name and description, labels are associated with their fields,
color choices have names, status is not color-only, and the content is inside a resizable scroll
surface. Apply persists the complete local draft; Cancel changes neither manifest nor autosave state.

## Persistence ownership

The organizer owns the only autosave coordinator. Opening the overlay dialog first flushes queued
page edits, then binds the draft to the exact case, revision, and active page. While the modal is
open, other organizer mutations are disabled. A hidden per-opening session authority prevents a
cancelled same-revision draft from being accepted by a later editor. Apply rechecks the binding,
queues one candidate on the existing autosave service, and flushes it before reporting success. A
stale revision, concurrent overlay state, save failure, or malformed result leaves the last valid
manifest authoritative. After a save failure, the dialog retains its draft for an exact retry; that
retry replaces the pending candidate even if local undo returned to the opening overlay state. If
the retained draft is edited again, shutdown queues that latest exact state before draining autosave.
Generic autosave flush/retry calls are rejected while the editor owns this exclusive phase, so they
cannot publish an older candidate behind the modal. If shutdown itself is refused, the stale modal
is dismissed and the existing main save/cleanup retry surface receives focus.

The Qt bridge publishes any revision created by the opening flush before it publishes the bound
editor. Apply completion must then match the exact current case, bound page, non-pending autosave
state, and permitted revision transition in both the backend bridge and window. A malformed or exact
but uncorrelated terminal payload leaves the dialog open on a sanitized retry path. If the returned
start presentation itself is malformed, the bridge uses a state-free abort operation so the rejected
object is never trusted to release backend authority.

This exclusive phase avoids two whole-manifest candidates silently replacing each other. A future
full editor may introduce a reviewed unified temporal journal, but PWS-0304 does not invent one or
overload the released `page_operations` wire.
