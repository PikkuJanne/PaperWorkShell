# Removal-mark intent and accessible review

- Task: PWS-0602
- Decisions: PWS-D009, PWS-D010, PWS-D022, PWS-D023, PWS-D030, PWS-D036
- Status: Intent editing only; permanent removal remains disabled
- Schema: [case manifest v3](../schemas/case-manifest-v3.md)
- Security contract: [redaction threat model](../security/redaction-threat-model.md)

## User workflow and meaning

Select a page and choose **Permanently remove information…** (`Ctrl+Shift+P`).
The separate editor records full-page regions for later review. Its only save action is
**Save unapplied marks**. Saving intent removes no information and produces no derivative.
The region list, preview, status text, and accessible descriptions all say **Unapplied** and
explain that information is still present. Ordinary highlights and shapes remain in the
visual-overlay editor and cannot become removal marks or verified removal.

The editor explains before saving that future application creates a new derivative, may
rasterize affected pages, and may lose interactive forms and accessibility structure. A
modified digitally signed source will be an unsigned derivative. External and imported
originals remain unchanged. Users must review every occurrence across all affected pages;
this feature does not find repeated values automatically.

The four review terms are distinct: unapplied intent, an applied but unverified candidate,
a verified exact derivative, and a failed candidate whose export is blocked. Only unapplied
intent is serializable or reachable in PWS-0602. The closed transition function rejects
every pipeline-state transition. The other descriptions explain future results; no test,
manual flag, ordinary shape, or saved status can claim pipeline approval.

## Geometry, keyboard and preview

The native list and four named spin boxes provide the full workflow without a pointer.
Positions use integer full-page millionths before manifest crop and rotation; 1000000
equals 100 percent. Add, update, select, remove intent, undo, redo, save and cancel all have
keyboard paths. Pending numeric text is interpreted and validated before a change, save,
or shutdown handoff. Invalid geometry retains the exact draft and focuses recovery.

The canvas paints the existing bounded organizer preview with dashed crossed outlines and
a separate UNAPPLIED legend. It never paints a filled covering over the page. If the summed
contrast-outline footprint exceeds the visible page area, a visibly and programmatically
disclosed dense preview shows only the selected region's visible part. With no selection it
asks the user to select a listed region. Every region remains in the native list; the preview
does not change or discard intent. This avoids a dense set of unapplied outlines appearing
as an opaque removed area. Pointer drag is an optional
one-command region creation path; outward quantization includes the selected edges. The
existing crop/quarter-turn transform provides mapping. Outside-crop marks remain listed and
editable at their complete saved geometry. A deskewed page uses a labeled schematic with
pointer selection disabled rather than an unsafe approximate inverse. Missing preview pixels
also disable pointer selection; exact editing remains available.

The preview shares the existing QPixmap and does no source reads, decoding, rendering, worker
calls or filesystem access. It retains one bounded display projection of at most the schema's
4096 regions. Three existing domain crop-corner mappings define an affine Qt projection;
clipping precedes projection. This display cache never provides persistence or pointer inverse
authority. The existing domain inverse still maps pointer input. Record, viewport, font and
preview changes invalidate the projection as applicable. Pointer state is discarded on hide,
disable, resize or a font change. A native scroll
area provides text enlargement; Save and Cancel stay outside it, and the initial window
fits the current screen. Focus cycles through enabled controls, status and fixed buttons.
Announcements use a closed content-free vocabulary, never text, filenames, paths or IDs.

## Authority, persistence and failure

`app.redaction_tools.RedactionToolsState` owns a bounded 256-edit local intent history tied
to one case, manifest revision, page and editor session. It cannot change source, overlay,
other-page or verification state. The organizer retains separate primitive opening facts;
presentation-object mutation cannot rewrite that authority. Saving reconstructs and validates
the complete candidate through the existing atomic manifest/autosave repository, then publishes
the reopened correlated state. A failed or uncertain save retains its draft for retry and
cannot be discarded as though no write happened. Shutdown queues that exact retained draft
before closing. Cancel before a save request releases the editor and changes no case intent.

History stores one opening collection, at most 256 before/after edit deltas and one derived
current collection. Validation replays the complete journal, including its redo suffix, in
O(opening marks + edits); it does not retain or repeatedly scan a whole-case snapshot per edit.
Undo preserves global mark order, and a new edit after undo discards the redo suffix. Selection
retains unchanged native list rows instead of rebuilding them. Derived caches are recomputed
and checked against backend-private opening facts before saving.

At the edit limit, numeric and pointer mutations stop while list review, Save, Undo and Cancel
remain available. Undo makes room for a new branch. Capacity errors have distinct recovery
wording and cannot masquerade as invalid geometry or trap a pending field edit. Once saving
has started, failure recovery continues to retain the draft and never offers unavailable
Cancel, including after Undo or a capacity error.

Supported-limit tests bound work deterministically. Synthetic offscreen measurements at 4096
marks and 256 edits observed 32–77 ms state transitions; final UI heartbeat gaps were 124 ms at
DPR 1, 148 ms at DPR 2, and 145 ms at DPR 2 with doubled text. These are below the unchanged
constrained-v1 250 ms budget. They demonstrate bounded software behavior on this run, not a
hardware-support certification. The task report links the detailed evidence.

The modal lifecycle excludes other mutations and preview scheduling. Queued page operations
flush before opening. Worker-to-window responses must match case, revision, page, session,
pending operation and closed announcement vocabulary. Stale or malformed results fail closed.

Normal PDF, searchable PDF, flattened PDF, package and compression entry points reject active
removal marks with a stable sanitized failure before source materialization/publication. A
marked inactive page does not block exporting other pages, but restoring it restores the block.
No path can silently ignore a user's active removal intent. PWS-0603 through PWS-0608 must
implement and prove reconstruction, OCR exclusion, sanitization, verification, final export
authority and independent approval before enabling actual removal.

## Schema and product boundary

Manifest v3 introduces a separate required `redaction_marks` collection. Frozen v1/v2 schema
meanings are unchanged. Validated copy-only upgrades preserve original hashes, pages, operations,
overlays, assignments, constraints, confirmations and export history, and add an empty mark
collection through one journaled transaction. Future versions fail before mutation. Old shapes
are never inferred as removal intent. No new third-party dependency or worker RPC is added.

PaperWorkShell owns these private local marks and migrations. Standalone operation and data
ownership remain unchanged; no sibling repository, service, adapter or content access is needed.
Draft release-interface facts remain unproved. This task creates no release-redaction claim.

## Verification

Domain/schema tests cover valid/invalid/future versions, namespace collisions, states and
blocked promotions, lossless historical upgrades and gate closure. Application/storage tests
cover exact saved intent, stale/reflected/cross-page drafts, exclusive sessions, cancel, failed
save/retry and recovery. Export/security tests exercise bypass paths and prohibited output tokens.
Native accessibility tests cover roles, names, keyboard geometry, list state, tab order, failed
save focus, crop/rotation, deskew suppression, pointer cleanup and enlarged text. The repeatable
synthetic screen-reader entry point is `tests/helpers/redaction_editor_manual_probe.py`; the
interactive result is recorded separately in the task completion report.
