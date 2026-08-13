# Atomic manifest repository and autosave contract

- Status: Implemented PWS-0109 contract with PWS-0202 schema migration
- Tasks: PWS-0109, PWS-0202
- Decisions: PWS-D010, PWS-D011, PWS-D036
- Depends on: PWS-0006 case-manifest v1; PWS-0101 secure case workspace; PWS-0102 lifecycle
- Related boundary: [Immutable source-ingest contract](immutable-ingest.md)

## Scope and layers

PWS-0109 makes complete case-manifest persistence a shared runtime boundary. The pure `domain`
package owns path-free snapshots, result and error types, the repository protocol, defensive JSON
copying, and the canonical schema/semantic validator. `app.manifest_repository` owns synchronous
autosave coalescing. `storage.manifest_repository` owns bounded decoding, compare-and-swap,
authenticated Windows staging, the fixed recovery checkpoint, atomic replacement, and recovery.

The repository does not parse documents, render pages, import UI or PDF libraries, or claim power-
loss durability. It owns the single reviewed v1-to-v2 structural migration; PWS-0110/PWS-0202 own
page-operation construction and replay;
PWS-0113 owns the ordered [startup recovery](startup-recovery-and-diagnostics.md) boundary for
unrelated orphaned staging objects after authoritative manifest and export-receipt settlement.

## Full runtime validation

Every proposed save is converted to deterministic UTF-8 JSON and fully validated before storage is
opened for mutation. Every current manifest and recovery checkpoint is fully validated before it
is returned or promoted. Validation includes:

- the 8 MiB manifest byte limit and strict JSON rules for UTF-8, duplicate names, numeric values,
  nesting, node count, string size, and collection size;
- exact top-level schema-version dispatch after the 8 MiB JSON parse bound but before applying
  current-version float, depth, node, string, schema, or semantic rules;
- packaged, self-contained copies of the frozen case-manifest v1 and current v2 schemas, each with
  its own pinned canonical fingerprint;
- the structural schema rules and all semantic ID, reference, source-page, sequence replay,
  operation, assignment, confirmation, and constraint checks; and
- matching canonical case UUID, ordered timestamps, and bounded revision metadata.

The validator uses only the standard library at runtime. A parseable future-version current
manifest is never treated as corruption and never triggers downgrade recovery. Stable errors carry
only closed codes and safe contexts; snapshots hide document contents, digests, and CAS tokens from
their representations.

## Journal and load behavior

The journal is one bounded, complete checkpoint at `journal/latest.json`, not an append-only event
log. Its content is the exact candidate bytes that were validated for the corresponding manifest
revision. Keeping one checkpoint bounds case growth while retaining the only state needed to settle
an interrupted replacement.

Loading holds the shared per-case transaction guard while it validates both authoritative paths:

- A valid current manifest with no checkpoint or a stale same-case checkpoint is returned as
  `current`.
- A fully valid checkpoint exactly one revision ahead of a valid current manifest is atomically
  promoted and returned as `recovered`.
- A fully valid checkpoint restores a missing, truncated, or otherwise invalid current manifest.
- A checkpoint more than one revision ahead is a recovery gap and is rejected.
- A corrupt checkpoint never replaces a valid current manifest.
- A future-version current manifest or checkpoint fails before any recovery mutation; neither is
  downgraded or overwritten by current-version state.

After those recovery rules settle one valid authoritative snapshot, a v1 result is copied and
validated as the unique v2 migration candidate. Only `schema_version` and the next `revision`
change. The repository publishes that candidate through the same checkpoint/replace transaction
before returning it. Direct migration reports `migrated`; promotion of an interrupted migration
reports `recovered_migrated`. Public load never returns a v1 snapshot, and the next load returns the
same v2 state as `current`.

Recovery writes and flushes a new random temp file, verifies it through a read-only handle reopened
from the authenticated writer, atomically publishes or replaces `manifest.json`, closes the writer,
and reopens the authoritative record before returning.

## Save transaction and compare-and-swap

Callers save exactly the next revision using a snapshot's revision, SHA-256 digest, and identity-
derived CAS token. The transaction reopens the current manifest with retained read/delete authority
and compares the exact expected state again under the case guard. A process-local reentrant lock and
an identity-derived Windows mutex serialize cooperating broker processes in the same desktop
session. A stale caller receives a stable revision-conflict result; two writers cannot silently
overwrite one another.

For an accepted candidate the repository:

1. reserves two random temp entries and enough configured capacity for both complete copies;
2. exclusively creates, writes, flushes, and handle-verifies the manifest and checkpoint stages;
3. reopens both retained writer handles read-only and repeats complete validation;
4. refuses a different valid next-revision checkpoint, then runs the optional authenticated
   source-publication callback used by immutable ingest;
5. publishes or atomically replaces the fixed checkpoint;
6. atomically replaces `manifest.json` against the retained expected handle; and
7. closes all replacement handles and reopens the current manifest for exact verification before
   another local transaction can enter.

A partial staged write, flush failure, quota failure, validation failure, or confirmed pre-checkpoint
failure leaves the previous manifest authoritative. If interruption occurs after checkpoint
publication but before manifest replacement, fresh load promotes that exact next revision. An
unknown rename outcome is reported as uncertain and its recovery evidence is preserved rather than
being guessed away.

## Autosave behavior

`ManifestAutosaveService.queue()` defensively freezes an edit and replaces the previous pending
edit. Queueing performs no I/O, so any number of rapid edits before a flush become one save of the
latest state. `flush()` serializes saves, assigns the next revision and a nondecreasing UTC update
time, and continues if another edit was queued during the save. `close()` first rejects new edits,
drains the final pending state, and only then seals the service.

Pending state remains available after a failed save. A stale or uncertain commit invalidates the
cached snapshot so the next attempt must reload authoritative state. Every queued full document
carries its base revision; first load and retry reject a mismatch instead of rebasing stale content
over a concurrent writer. A newer edit queued during this service's own in-flight save is advanced
only across that exact successful revision. This service-owned transition remains available until
`flush()` returns or its completion notification finishes, so edits at the post-save notification
boundary behave like edits queued during the write; it is then discarded. Completion callbacks run
only after internal locks are released, and callback failures cannot turn a durable save into an
uncommitted state. Scheduling a debounce or calling `flush()` at UI lifecycle points belongs to the
host application; the domain/application service has no toolkit or background-timer dependency.

## Immutable-ingest integration

PWS-0104 source imports now use the same manifest transaction. Preparation first resolves any
recoverable checkpoint, and the transaction repeats the pending-checkpoint check while holding the
cross-process case guard. The sealed source copy is published by the authenticated pre-checkpoint
callback, then the checkpoint and manifest are committed. A manifest replacement can therefore be
recovered without losing the already published source reference. Before page-operation history
exists, import extends both base and final page sequences. When history exists, import preserves
the base sequence and appends a typed `change_sequence` operation with reason `import`, so canonical
replay still produces the final sequence. PWS-0113 retains responsibility for a source orphaned
before either authoritative record references it.

## Residual boundaries

- File flush and atomic rename evidence covers process interruption; it does not claim survival of
  every storage-controller or power-loss failure.
- The one-snapshot journal is recovery state, not user-visible version history.
- Restrictive ACLs, retained handles, link/stream/reparse checks, and exact identities do not defend
  against unrestricted same-account malware, administrators, SYSTEM, or offline disk access.
- Frozen v1 is supported only as validated migration input. Current saves require v2, and unknown
  future versions remain blocked before mutation.

## Verification ownership

Unit tests cover the path-free contracts, defensive copying, complete canonical validation,
exact token termination, sanitized failures, deterministic serialization, stale autosave conflict,
and rapid autosave coalescing. Windows integration and security tests cover partial writes,
injected storage-write failure, configured capacity exhaustion, interruption before and after
checkpoint publication, corrupt-current recovery, malformed/future-checkpoint handling,
stale-writer and cross-process serialization, real-repository autosave, full representative graph
save/load equivalence, authenticated journal publication, and prohibited-value sanitization.
Source-ingest tests prove recovery before import, exact pre-checkpoint cleanup, preservation of full
existing manifest state, and compatibility with post-operation imports.
