# Local case-lifecycle contract

- Status: Implemented PWS-0102 contract with PWS-0104 and PWS-0109 extensions
- Tasks: PWS-0102, PWS-0104, PWS-0109
- Decisions: PWS-D005, PWS-D010, PWS-D011, PWS-D028, PWS-D036
- Depends on: PWS-0101 secure case workspace; PWS-0006 case manifest v1
- Source ingest: [Immutable source-ingest contract](immutable-ingest.md)
- Persistence: [Atomic manifest repository and autosave contract](manifest-repository.md)

## Scope and layers

PWS-0102 implements local create, list, open, close, and explicit delete without parsing a PDF or
image. `domain.case_lifecycle` owns path-free immutable summaries, indexes, deletion results, closed
error codes, and the deliberately limited deletion assurance. `app.case_lifecycle` serializes the
use case through a structural storage protocol and holds at most one active lease. The concrete
`storage.case_lifecycle` adapter authenticates Windows filesystem objects through
`CaseWorkspaceManager`; it never imports the application layer.

The lifecycle service has no UI, network operation, or document engine. PWS-0109 supplies the
general manifest repository used by lifecycle list/open and by PWS-0104 import commits, while case
creation keeps its create-only initial publication boundary. PWS-0111 owns the first accessible
desktop workflow, PWS-0110 owns page-operation state, and PWS-0113 owns restart-time orphan cleanup.

## Public lifecycle behavior

- `create_case()` publishes a complete empty manifest and returns its active `CaseSummary`.
- `list_cases()` returns valid current-version summaries in internal-UUID order. Canonical
  UUID-named candidates are authenticated independently; entries that are incomplete or invalid
  are returned separately as path-free unavailable issues and never hide valid cases.
- `open_case(case_id)` strictly reads the lifecycle envelope and retains an authenticated case-root
  lease. The envelope may describe an empty case or a complete PWS-0104 source/page import revision.
  Opening the already-active ID is idempotent; opening or creating a different ID requires closing
  the current lease first.
- `close_case()` releases the retained handles idempotently. An optional supplied ID must match the
  active case.
- `delete_case(case_id, confirmation)` accepts only the closed
  `REMOVE_FROM_APPLICATION_STORAGE` confirmation value and rejects an active case before storage
  mutation. Its result is `complete` or `partial`, contains counts and a stable code only, and says
  explicitly that application-storage removal does not guarantee forensic erasure.

All summaries and results use opaque UUIDs, revisions, timestamps, counts, and closed enums. They do
not carry a case path, original filename, document value, display name, or raw exception.

## Creation publication boundary

Creation uses `manifest.json` as the sole lifecycle-validity marker:

1. Provision and authenticate the fixed private UUID workspace from PWS-0101.
2. Construct the exact empty manifest-v1 document with revision zero, one UTC timestamp, empty
   collections, `combined_pdf`, and `must_be_unencrypted`.
3. Reserve case/temp bytes and one entry under the shared per-case coordination lock.
4. Exclusively create a random `temp/<uuid>.tmp`, write the exact UTF-8 bytes, flush them, and verify
   the opened regular-file identity, final path, single link, size, and stream state.
5. Rename that exact retained file handle to the fixed `manifest.json` destination without
   replacement while authenticated case-root and ancestor handles remain pinned.
6. Requery the same handle after rename, reopen the manifest with write/delete sharing denied, and
   validate the resulting lifecycle envelope before returning the active lease.

The rename is the commit point. Interruption before it leaves no authoritative manifest and the
case cannot appear in the valid index. Interruption after it leaves one complete recoverable case.
A failed pre-publication cleanup may conservatively leave a random incomplete skeleton; PWS-0113
owns bounded restart cleanup, and listing continues to classify it as unavailable.

## Source-import revisions

PWS-0104 prepares the immutable copy and source/page records; PWS-0109 now publishes them through
the shared journal transaction. Before page-operation history exists, import adds new page IDs to
both base and final sequences. With existing history, import preserves the base and records a typed
`change_sequence` import transition. Both forms preserve unrelated valid state, increment the
revision once, and fully validate the complete target before publication.

The source is published immediately before the recovery checkpoint, and `manifest.json` is replaced
last. Interruption after checkpoint publication is recovered to the exact complete source/page
graph on fresh open. An uncertain checkpoint or replacement preserves the source and recovery
evidence. An interruption before either authoritative record may leave an unreferenced random
original for PWS-0113 cleanup.

## Bounded manifest dispatch

Listing, opening, and deletion read the exact authenticated manifest handle with an 8 MiB cap and
deny concurrent write/delete sharing. The standard-library decoder rejects invalid UTF-8, a BOM,
duplicate keys, non-finite constants, floating-point values, non-object roots, more than 64 JSON
levels, more than 100,000 nodes, strings over 1,048,576 code points, or lifecycle collections over
4,096 items.

Version is an exact integer dispatch value. Missing, malformed, legacy, future, or unknown versions
fail before mutation. A current envelope must have the closed top-level member set, a canonical
matching UUIDv4, a bounded integer revision, schema-shaped top-level collection/record types, and
valid ordered UTC timestamps. The fixed directory skeleton is reauthenticated before a summary is
accepted.

Lifecycle list/open and deletion now use the shared runtime boundary for complete fingerprint-bound
schema and semantic validation. List/open may promote the one fully validated recovery checkpoint;
a parseable future version fails before any mutation. Creation also passes its generated record
through the same complete validator before publication.

## Active leases and concurrency

Every manager instance for the same normalized cases root and UUID shares one process-local
coordination state. It tracks quota reservations, active lifecycle leases, the authenticated case
identity, and deletion in progress under one reentrant lock. The retained case handle denies delete
sharing, so another process also cannot acquire deletion authority while the case is open.

Application transitions hold one service lock through their storage call. A failed create or open
does not replace prior active state. Delete obtains delete access before mutation, rejects active
leases/reservations, and prevents new leases or capacity claims until its authority closes.

Manifest saves, source commits, and lifecycle recovery use the same per-case coordination identity,
process lock, and identity-derived Windows mutex. A publisher cannot commit against an older exact
manifest snapshot while another cooperating broker transaction in the same desktop session owns the
case boundary; retained-handle CAS rejects stale writers rather than losing a revision. Case-root
sharing rules continue to exclude deletion while transaction authority is retained.

## Authenticated deletion

Deletion never uses `rmtree`, a shell command, a display name, or a caller path:

1. Require the exact application-storage confirmation and an inactive internal UUID.
2. Retain authenticated cases-root and delete-capable case-root handles.
3. Complete a bounded, no-follow inventory of the whole fixed case tree. Reject unexpected
   top-level objects, reparse points, hard links, alternate streams, insecure directory ACLs,
   changed identity/type/size, or entry/depth overflow before deleting the first object.
4. Reopen the inventory-selected `manifest.json` with read/delete authority, revalidate its exact
   identity, type, size, single-link and stream state, then strictly read the matching supported
   lifecycle envelope while retaining that handle.
5. Mark that same validated manifest handle for deletion first so an interrupted removal is no
   longer reported as a valid case.
6. Delete the remaining verified inventory deepest-first and mark the exact case-root handle last.

Removal stops on the first post-invalidation error. A partial result counts only successfully
removed objects and logical bytes. Sibling cases, external sources, exports, and any object whose
identity cannot be proven are outside its authority and remain untouched.

## Residual boundaries

- Restrictive ACLs and handle pinning do not claim protection against unrestricted same-account
  malware, administrators, SYSTEM, backups, snapshots, or physical-media recovery.
- Application removal is not secure erase and does not guarantee forensic erasure.
- A power loss may leave a non-authoritative staged file or incomplete UUID skeleton; it cannot
  create a partially written valid manifest.
- PWS-0102 does not cancel a document worker because no lifecycle document operation exists yet.
  Later composition must close worker operations before releasing the active case lease.
- Deleting an unsupported-version case is intentionally blocked. A future incompatible-case
  cleanup workflow requires its own explicit reviewed contract; it may not bypass version safety.

## Verification ownership

Unit tests cover pure models, stable errors, state transitions, concurrency, canonical initial
manifest generation, strict decoding, and schema validation of the generated document. Windows
integration and security tests cover the complete lifecycle round trip, fresh-service reopen,
interrupted publication, version safety without mutation, active-lease and capacity-reservation
exclusion, resilient candidate listing, manifest-first partial deletion, whole-tree pre-inventory,
alias/stream rejection, sibling/external preservation, and prohibited-value sanitization.

PWS-0104 adds source-bearing reopen and integrity evidence. PWS-0109 adds complete validation,
checkpoint recovery, partial-write/capacity failure, stale-writer, and autosave evidence. Detailed
persistence and ingest matrices are owned by the [manifest repository](manifest-repository.md) and
[immutable source-ingest](immutable-ingest.md) contracts.
