# Page operation, replay, undo, and redo contract

- Status: Implemented PWS-0110 contract with PWS-0202 and PWS-0301 extensions
- Tasks: PWS-0110, PWS-0202, PWS-0301
- Decisions: PWS-D010, PWS-D011
- Depends on: PWS-0006 case-manifest v1; PWS-0109 atomic manifest repository

## Scope and ownership

PWS-0110 owns pure, path-free organizer intent. `domain.page_operations` defines typed page
references, normalized crop intent, versioned page-operation payloads, deterministic replay,
organizer projections, commands, and session undo/redo history. It accepts only a validated
PWS-0109 `ManifestSnapshot` and produces a complete candidate document for the existing autosave
and repository boundary.

The module performs no filesystem access, parsing, rendering, PDF manipulation, worker RPC, UI
work, logging, timing, or background scheduling. It uses no third-party package. PWS-0111 owns
accessible organizer controls; rendering and export tasks consume the recorded intent through their
own adapters. PWS-0202 adds commands and projection fields for crop, deskew, contrast, and
background cleanup without moving rendering or worker concerns into the domain layer.
PWS-0301 moves the released normalized-box codec into the pure
[normalized page coordinate model](page-coordinate-model.md) and explicitly re-exports that exact
type here, preserving existing imports and crop serialization without changing either manifest
schema.

## Canonical operation replay

Replay begins with ordered `page_sequence_base` identities and applies `page_operations` in exact,
contiguous sequence order. Every identifier is canonical UUIDv4, every page resolves through the
closed page catalogue, every collection is duplicate-free and bounded to 4,096 members, and every
page enters the replay baseline or exactly one valid import transition.

Sequence reasons retain the released manifest-v1 meaning:

- `remove` is a strict subset and preserves the relative order of every survivor;
- `import` and `restore` are strict supersets and preserve prior relative order, while import adds
  only never-introduced pages and restore adds only previously introduced inactive pages; and
- `reorder`, `split`, and `combine` preserve identical membership while recording their distinct
  organizer intent.

Rotate, crop, group, deskew, contrast, and background-cleanup operations may address an introduced
inactive page, so removing a page does not destroy its non-destructive intent. Clockwise quarter-
turns accumulate modulo four. Crop, deskew, contrast, and background cleanup use the latest value.
Their neutral projected values are respectively no crop, zero millidegrees, one-million contrast
millionths, and zero cleanup millionths. A repeated group identity updates that group
deterministically while retaining first-introduction group order and payload page order. The final
replayed sequence must equal the authoritative manifest `page_sequence`.

Replay is deliberately media-neutral. Applicability is checked by each rendering consumer: the
PWS-0202 raster profile consumes JPEG/PNG pages, while PDF preview/export currently accept only
quarter-turn and fail closed on crop, deskew, non-neutral contrast, or cleanup rather than silently
ignoring recorded intent.

Legacy organizer payloads retain `operation_version: 1`. Deskew, contrast, and background cleanup
require `operation_version: 2`; either version paired with the other version's payload fails
closed. Their exact fields and ranges are defined by the
[case-manifest v2 contract](../schemas/case-manifest-v2.md). A manifest-v1 snapshot cannot contain
version-2 operations.

## Commands and reversible session history

Loaded operations are a durable baseline and cannot be undone by a newly opened editing session.
Commands append a typed operation to the session suffix. Sequence-change commands support reorder,
remove, restore, split, and combine; import remains owned by immutable ingest and cannot be created
by an organizer command. Rotate, crop, deskew, contrast, background-cleanup, and set-group commands
use the same replay validation before a new history value is returned.

Undo removes only the latest session operation. Redo restores that exact operation identity and
payload. Executing after undo clears the transient redo path, but discarded identifiers remain
reserved for the session so a generated identity is never silently reused. Identifier generation
is injected for deterministic tests and makes at most 32 collision-bounded attempts. Restarting
from a saved snapshot intentionally begins a new session with no redo stack.

All history values and projections are immutable. Caller-owned commands, manifest containers, and
snapshot documents are never retained or mutated.

## Manifest candidate and concurrency rules

Candidate construction starts from a defensive copy of the latest supplied snapshot and replaces
only `page_operations` and `page_sequence`. Case identity, schema version, revision, creation and
update timestamps, source/page records, assets, overlays, assignments, constraints, confirmations,
export history, and any other released member are preserved. Revision and timestamp advancement
remain solely owned by `ManifestAutosaveService`.

The current snapshot must have the same case, exact page catalogue, exact replay baseline, and exact
durable operation prefix as the history origin. A current suffix is accepted only when every
operation is an exact session-owned operation and its complete operation-ID state was previously
produced by that history. This permits autosave followed by undo, redo, or a new branch while
rejecting a concurrent import, page-catalogue change, unknown operation, or silently rebased page
edit. Namespace checks are repeated against the latest unrelated manifest records before output,
so a concurrently introduced overlay, assignment, or other identity cannot collide with an active
operation or group identity.

The PWS-0109 repository performs full canonical schema and semantic validation again before any
candidate can reach storage.

## Errors, privacy, and limits

Failures use a closed page-operation error registry and fixed safe contexts/messages. Exception
text and representations never include page IDs, manifest values, document text, display names,
paths, hashes, or raw exceptions. Invalid models, unsupported versions, missing or duplicate pages,
invalid sequence transitions, invalid rotations/groups, history conflicts, unavailable undo/redo,
capacity exhaustion, and identifier exhaustion remain distinguishable without disclosure.

Replay and history are bounded by the released 4,096-page and 4,096-operation limits. Integers
exclude booleans, normalized boxes must remain inside one million millionths, and generated
property tests use fixed seeds and synthetic UUIDs only.

## Verification ownership

Unit and generated-property tests cover every sequence reason, replay determinism, rotations,
inactive transforms, crop preservation, group updates, malformed and colliding identities,
undo/redo paths, execution after undo, candidate preservation, concurrent-state rejection, and
serialize/reload equivalence. Integration tests save a representative candidate through the real
PWS-0109 repository, reopen it, and prove an equivalent projection. Import-boundary tests continue
to reject UI, worker, storage, renderer, PDF, and third-party dependencies from the domain layer.
