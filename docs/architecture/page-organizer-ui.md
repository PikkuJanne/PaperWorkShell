# Page organizer UI architecture

PWS-0111 introduces the first desktop vertical slice. It is a guided packet organizer, not a
general PDF editor. The surface creates or opens one local case, imports supported sources, shows
bounded page previews, and records page rotation, active-sequence removal, ordering, undo, and
redo. It makes no claim about portal acceptance, legal validity, permanent redaction, or universal
document validity.

## Dependency direction

`paperworkshell.ui` owns Qt presentation only. It imports the QtCore, QtGui, and QtWidgets surfaces
from PySide6-Essentials and consumes path-free application/domain types. It does not import PDF,
image, OCR, renderer, storage, or worker-host implementations.

`paperworkshell.app.organizer.OrganizerBackend` is the toolkit-free session facade. It owns the
current validated `ManifestSnapshot`, `PageCommandHistory`, and `ManifestAutosaveService`, while
presenting only internal identifiers, positions, rotation, preview availability, revision, and
undo/redo/save state. The manifest remains authoritative; Qt settings do not duplicate organizer
state.

`paperworkshell.__main__` is the sole composition root. Its one narrow worker import is
`paperworkshell.worker.desktop_composition`, which creates the shared capability-scoped worker
client, PDF inspector, and PDF thumbnail generator. The UI and application layers cannot import
that module.

## Thread and cancellation model

All case, manifest, import, cache, and worker-client mutations run serially on one long-lived Qt
backend thread. The worker client is constructed on that thread and never called through a generic
thread pool. GUI commands cross the boundary through queued Qt signals.

Import and preview services are synchronous on the backend thread. They are single-flight from the
GUI and carry a monotonic operation identifier through start, progress, result, cancellation,
failure, status, and finish signals. A fresh `threading.Event`-based cancellation token remains
owned by that identifier until its matching finish signal. Starting an import while a preview is
running therefore cancels the actual preview token, waits for its correlated finish, and then
starts exactly one queued import; a second import request is rejected. A cancelled or failed import
resumes previews from the latest unchanged organizer state. Stale results or completion text cannot
finish or overwrite a newer operation.

The GUI's Cancel action sets the current token directly, so cancellation remains responsive while
the backend thread is inside a synchronous broker call. Progress crossing back to Qt is an existing
path-free typed record. Unknown exceptions are neither logged nor stringified; they become
`PWS_UI_UNEXPECTED` with fixed recovery text.

## Organizer state and autosave

Create and open load the current manifest and replay durable page intent. Page actions create pure
domain commands and queue only a validated candidate. The UI debounces rapid edits for 400 ms,
shows “Saving changes” text, and then flushes the latest candidate. Pending autosave suppresses new
preview work and directly cancels an in-flight preview; the queued save therefore runs as soon as
the renderer observes cancellation instead of waiting for the render timeout. Preview generation
resumes only after the backend publishes a non-pending state. A failed save retains the candidate
and exposes a keyboard-focusable Retry saving changes action.

Case switch, import, window close, and process shutdown flush queued state first. Import then
commits through immutable ingest, reloads the new manifest revision, and establishes the import as
a new non-undoable durable baseline. Restart reconstruction therefore reads only the manifest and
does not depend on process memory.

The immutable-ingest result is the import commit point. A later manifest reload or organizer-session
construction problem is reported as a typed `reopen_required` outcome, never as a retryable import
failure. The outcome states plainly that the import committed and asks the user to close and reopen
the case. The GUI then stops preview and autosave scheduling, disables import and every stale page
mutation, keeps Close case enabled, assertively announces the recovery instruction, and moves focus
to Close case. The organizer also keeps the following instruction visible beside the page surface:
“The import was saved, but this organizer must be reopened before the new pages can be used. Choose
Close case, then reopen this case.” Until reconciliation succeeds, a repeated backend import
command reloads and verifies that same committed source and its page bindings without invoking
ingest again. A
successful reconciliation publishes the authoritative manifest state; closing and reopening
instead reconstructs that same state after process restart without duplicating the committed pages.

A journal-recovered load crosses the UI boundary only as a path-free `recovery_review_required`
flag. The organizer heading displays “recovered; review needed,” and an assertive accessibility
announcement asks the user to review the page sequence without exposing a path or manifest value.

## Import and preview behavior

PDF, JPEG, and PNG selection enters the existing authenticated, bounded immutable-ingest path.
PDF structure and page count are inspected in the isolated document worker. Image headers and
dimensions have already been bounded by the prechecker; the router records one image page without
decoding pixels in the GUI process.

The organizer places a prominent, word-wrapped guidance notice immediately above the active-page
surface whenever no committed page can yet be shown. This prevents an empty case or a long-running
import from looking like a blank organizer at enlarged text or display scaling. The notice is
path-free plain text and follows the correlated import state:

| State | Visible organizer guidance |
|---|---|
| Empty case | “No pages yet. Choose Import document (Ctrl+I) to add a PDF, JPEG, or PNG.” |
| Import queued behind preview cancellation | “Import queued. PaperWorkShell is stopping the current preview before import starts. Keep PaperWorkShell open.” |
| Import running | “Importing document” plus the latest announced phase and completed/total count when available, followed by the instruction to keep PaperWorkShell open and press Escape to cancel safely. |
| Import cancelled | “Import cancelled. No source was committed. Choose Import document (Ctrl+I) to try again.” |
| Import failed | “Import did not finish,” followed by the visible Action-error direction when that error is still present, and the Import document (`Ctrl+I`) retry action. |
| Import committed but organizer reopen required | The close-and-reopen instruction described above. |
| Window close while import is active | “Closing PaperWorkShell safely,” retained through import completion while backend cleanup runs; a shutdown refusal replaces it with the focused save/cleanup retry path. |

The global operation-status surface remains the canonical visible and accessibility-announced
operation record. The inline notice is a non-focusable summary of current organizer state and the
next safe action; it does not include source paths or filenames and disappears after authoritative
state contains committed pages unless a terminal import outcome still needs attention.

Active PDF pages receive bounded 240 by 320 pixel RGBA8 preview requests on demand. After a
debounced list-layout pass, the GUI considers intersecting rows, the keyboard-selected row, and two
overscan rows on either side. It retains at most 12 detached Qt pixmaps and requests at most eight
pages per backend batch. Rows are prioritized deterministically, but every emitted subset remains
in authoritative organizer order. Scrolling or keyboard selection can load another bounded batch;
least-recently-used pixmaps outside the current window are evicted while the existing case-scoped
disk cache remains reusable.

The toolkit-free organizer validates each preview subset as an exact tuple of unique, active UUIDv4
page IDs within the eight-page cap before constructing a worker request. Its outer shared deadline
scales only with PDF pages actually requested. The worker renders and validates those pages through
pypdfium2 and the existing thumbnail cache, and the GUI accepts only exact ordered result
correlation before converting fixed RGBA8 snapshots to detached `QImage`/`QPixmap` objects.
Supported rows distinguish “not loaded,” “loading,” “ready,” and “failed”; eviction and cancellation
return rows to “not loaded,” while a failed batch remains “failed” instead of automatically looping.
Rotation invalidates retained pixels before autosave suppresses new preview work. JPEG and PNG
preview decode belongs to PWS-0201, so those rows remain explicitly “Preview unavailable.”

The initial product limits selected at the composition root are 512 MiB per source, 1,000 pages,
32,768 pixels per image dimension, 268,435,456 image pixels, 4 GiB per case, 512 MiB cache, and
1 GiB temporary case storage. Document-worker operations run with one child process, a 768 MiB
process limit, a 1 GiB job limit, bounded CPU/wall time, no network capability, and case-scoped
brokered handles.

## Keyboard and accessibility contract

The page list has drag and drop disabled. Core actions have visible text buttons and keyboard
shortcuts:

| Action | Shortcut | Non-drag control |
|---|---|---|
| Create case | `Ctrl+N` | Create new case |
| Open selected case | `Ctrl+O` | Open selected case |
| Import source | `Ctrl+I` | Import document |
| Move selected page earlier/later | `Alt+Up` / `Alt+Down` | Move earlier / Move later |
| Rotate clockwise | `Ctrl+R` | Rotate clockwise |
| Remove from active packet | `Delete` | Remove from packet |
| Change complete order | `Ctrl+Shift+O` | Keyboard page-order dialog |
| Undo / redo | `Ctrl+Z` / `Ctrl+Y` | Undo / Redo |
| Cancel import or preview | `Escape` | Cancel current operation |

Qt widget roles provide accessible names and descriptions. Page rows announce position, rotation,
and truthful textual preview state. Phase changes, bounded progress checkpoints, completion,
cancellation, and errors use
`QAccessibleAnnouncementEvent`; progress retains visible numeric text, and no state is conveyed by
color alone. Ordinary state and autosave publications preserve the user's current enabled control;
focus moves to a visible safe control before Cancel or Retry is hidden. Window shortcuts delegate
to the same enabled-state, edge, history, active-case, and shutdown guards as visible buttons, so a
disabled action cannot bypass its predicate.

The main window imposes no 720 by 560 minimum. One resizable outer workflow scroll area contains
the chooser or organizer plus the global status surface, so enlarged text and a constrained 960 by
540 logical viewport can reach every chooser, organizer, status, Cancel, and Retry control without
clipping or nested workflow scrolling. Controls retain flexible height policies. The explicit page
order dialog disables Move earlier/later at the corresponding edge, refreshes those states after
selection and movement, and publishes visible accessible position confirmation after each move.
Focus returns to a recovery action after failure.

One global operation-status surface sits outside the case-chooser/organizer page stack. Startup,
case-list, and create/open failures are therefore both visibly available and announced while the
chooser is active, using the same path-free status and error labels as the organizer. If no chooser
action is enabled after startup failure, focus moves to the safe error text; clearing that error
moves focus to the global status text rather than leaving focus on a hidden widget.

Preview generation keeps the PWS-0108 one-shot isolation boundary: every disk-cache miss launches
and terminates a fresh restricted worker for one page, and each untrusted render retains its
independent 60-second worker wall-time limit. The application thumbnail service also has one
monotonic deadline covering the complete serial batch, including trusted worker launch and
revocation. The organizer's default outer budget is therefore 120 seconds per PDF page in the
validated subset, capped at the service maximum of 3,600 seconds. An explicit caller timeout is not
rescaled. This prevents normal trusted Windows isolation setup for a later requested page from
exhausting a one-page default while leaving the UI responsive, cancelable, and bounded; it does not
grant a longer untrusted render.

Application exit never stops the backend event loop ahead of backend shutdown. The fallback wait
processes GUI-thread completion/refusal signals for ten seconds. A timeout leaves the backend loop
running and returns to the application event loop until the bounded operation completes or shutdown
is refused for recoverable save handling. The worker schedules deletion on its owner thread before
that thread quits; the GUI then joins and releases the finished thread deterministically.

## Verification ownership

Automated unit, integration, security, and accessibility tests cover domain/UI synchronization,
guarded keyboard shortcuts, non-drag ordering and edge states, focus order, visible progress and
errors, the accessible committed-import reopen lock, cancellation/failure terminal preview states,
a 1,000-page bounded visible/selected preview
window, Qt pixmap eviction, stale-rotation invalidation, constrained 960 by 540 layout with
200-percent text, operation correlation on a real `QThread`, preview preemption before autosave,
preview resumption after cancelled import, recovered-state review announcements, orderly shutdown
timeout behavior, autosave restart recovery, unknown-error sanitization, and import-source hash
preservation. The dated keyboard-only and NVDA procedure and its current evidence are recorded in
[`../accessibility/pws-0111-nvda-smoke.md`](../accessibility/pws-0111-nvda-smoke.md).
