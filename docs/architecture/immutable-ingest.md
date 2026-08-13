# Immutable source-ingest contract

- Status: Implemented PWS-0104 contract with PWS-0109 transaction integration
- Tasks: PWS-0104, PWS-0109
- Decisions: PWS-D009, PWS-D010, PWS-D011, PWS-D028, PWS-D036
- Depends on: PWS-0102 local case lifecycle; PWS-0103 bounded import prechecks
- Data flow: [DF-IMPORT](../security/data-flows.md#df-import--select-precheck-copy-inspect-and-commit-source)
- Image inspection: [Bounded JPEG/PNG intake](image-intake.md)

## Scope and ownership

PWS-0104 copies a prechecked external source into one case, computes and verifies its SHA-256
digest, publishes it under a random internal identity, and commits its complete manifest source and
page records. It also reports advisory content duplicates and verifies the later integrity of a
committed case copy.

`domain.source_ingest` carries path-free source metadata, prepared/committed results, duplicate
signals, integrity status, progress, cooperative monotonic deadlines, and closed sanitized errors.
Application orchestration depends on structural preparation, commit, and integrity protocols. The
concrete storage adapter owns the authenticated Windows handles, bounded streaming I/O, case
publication, and construction of the complete next manifest. The shared PWS-0109 repository owns
checkpoint and manifest publication. Storage does not import the application layer or parse
PDF/image structure, and no package initializer re-exports an implementation.

PWS-0104 itself does not add a document engine, page-edit command model, UI, network behavior, or
runtime dependency. PWS-0105 supplies complex PDF inspection through the restricted worker without
moving parsing into storage. PWS-0109 now supplies general manifest transactions, autosave, and
recovery; PWS-0110 owns page-operation construction and replay.

## Two-phase prepare and commit

An import is deliberately split so untrusted bytes can be inspected without making them an
authoritative source.

### Phase 1 — quarantine and verify bytes

1. PWS-0103 opens and prechecks the external source through one retained read-only handle. The
   selected path is never reopened for the copy. Before staging, the workspace reserves the
   bounded source size against both logical case/temp quotas and the policy's physical free-space
   reserve; insufficient space fails before copying begins.
2. The operation allocates distinct random pending and source UUIDv4 identities. While the lease is
   live, the ingest adapter streams the exact default stream into the pending case file and
   calculates SHA-256. Reads and writes are bounded; cancellation and the immutable monotonic
   deadline are checked between chunks.
3. The pending file is flushed and reauthenticated as a regular single-link file with no reparse
   point or named stream. Its retained handle rewinds and independently rehashes the written bytes
   to verify the exact size and digest.
4. Control returns normally through the PWS-0103 context so its terminal source identity, size,
   timestamps, link count, final path, and ancestor checks complete before preparation succeeds.
5. The resulting `PreparedSourceSummary` contains only pending, case, and source IDs, verified byte
   facts, and preliminary media state. The surrounding storage lease, not the summary, retains the
   case-scoped authority. The summary is not a source record and cannot be resolved from an
   external path.

Cancellation, timeout, a changed or short source, storage failure, staged-copy mismatch, or failed
terminal source check leaves no authoritative source record. The external source is never opened
for writing and its display path does not become case authority. Deterministic integration evidence
now covers low-space preflight and a disk-full failure after a bounded copy chunk: the partial stage
is removed, the external original remains byte-identical, and the authoritative manifest is
unchanged.

### Inspection between phases

Commit requires complete trusted metadata for the exact prepared copy:

- detected PDF, JPEG, or PNG media type;
- binding to the already verified byte size and SHA-256 digest in the prepared summary;
- a positive bounded page count;
- media-appropriate signature and encryption states; and
- a bounded, normalized display name for protected presentation only.

JPEG and PNG prechecks provisionally identify one source page and use `not_applicable`
signature/encryption states, but PWS-0201 must completely decode the exact pending copy before
those facts can be committed. A PDF precheck intentionally has no page count and reports both
states as `not_checked`. Those preliminary PDF values cannot be promoted into a source record. PWS-0105 inspects the same
prepared case copy through an exact brokered read-only handle and returns the bounded page count,
encryption state, and conservative signature-marker state required before PDF commit. `unknown`
remains a real inspection result; it is never fabricated to bypass `not_checked`.

After the pending copy is sealed and independently rehashed, storage registers its complete
`PreparedSourceSummary` with the retained pending-handle authority. A borrow matches every summary
field, uses `ReOpenFile` on that already authenticated kernel object to obtain an independent file
pointer, and rechecks identity, size, regular/single-link/reparse/sparse state, and alternate
streams. It never resolves the random pending name again. The trusted worker broker duplicates only
read-data/read-attributes/synchronize rights into one isolated generation; a stale or mismatched
summary fails. Registration is revoked before pending publication, deletion, or close.

For PDF inspection, the exact case handle is bounded-copy rehashed into worker-private ephemeral
storage and closed before pikepdf parses the stream. One separately brokered write-only anonymous
pipe returns a length-bounded result correlated to both operation and pending IDs. The broker
terminates the one-shot worker tree before the prepared-source borrow is released, then validates
the complete frame and typed result. No case path, write-capable source handle, pikepdf object, raw
tool output, or exception crosses into application/storage code. See the
[PDF structural-inspection contract](pdf-structural-inspection.md).

For JPEG/PNG inspection, the broker transfers the same reduced read authority plus one fixed-size
anonymous output-section authority. The worker rehashes and fully decodes the immutable pending
copy, writes a one-pixel correlated normalization artifact, closes all authorities, and ends before
the trusted broker accepts the one-page inspection result. It receives no source-publication or
manifest authority. See the [bounded image-intake contract](image-intake.md).

Worker output is treated as untrusted typed input. The trusted application/storage boundary checks
its case/source binding and limits before commit. The worker cannot publish an original, manifest,
journal, source record, or page record.

### Phase 2 — publish and commit metadata

After complete metadata validates:

The commit holds the source-byte reservation plus complete manifest and checkpoint reservations
under the shared per-case transaction guard through the following ordered publications.

1. The prepared source ID determines the only storage name, `<source-id>.bin`. Identifier
   collisions against the validated manifest are retried within a fixed bound; any destination
   object collision fails without overwrite.
2. The exact verified pending file is atomically published beneath `originals/` while authenticated
   case and parent handles remain pinned. The same sealed exact handle remains live through manifest
   publication; its broker writer rejects every post-seal write and is not exposed outside storage.
3. Duplicate detection compares the verified SHA-256 digest with committed source records under the
   same serialized commit authority. Matching source IDs are returned as an advisory signal.
   Duplicates still receive distinct source IDs, case copies, display names, and page identities;
   PWS-0104 performs no silent deduplication.
4. The ingest adapter adds one complete source record and exactly `page_count` new page records in
   zero-based source-page order. With no operation history, it appends the IDs to both base and
   final sequences. With history, it preserves the base and appends one `change_sequence` operation
   whose reason is `import`, before/after states are exact, and sequence number is contiguous.
5. The complete current and target manifests pass the fingerprint-bound runtime schema and semantic
   validator. The target preserves unrelated state, increments the revision once, and uses an
   ordered UTC timestamp.
6. The shared transaction writes and handle-revalidates random manifest and checkpoint stages,
   publishes the exact source through its authenticated pre-checkpoint callback, publishes or
   replaces `journal/latest.json`, then atomically replaces `manifest.json`.

The fixed complete checkpoint makes interruption after source publication recoverable. The ingest
adapter still cannot save arbitrary UI state or coalesce edits itself; those operations use the
same repository through its public save/autosave contracts. Schema migration remains out of scope.

## Cancellation, deadlines, failure, and restart boundary

`SourceIngestService` arms one immutable monotonic deadline for the complete import or integrity
operation. Import defaults to 300 seconds and integrity verification defaults to 60 seconds. A
caller may supply another finite positive duration up to the hard maximum of 86,400 seconds (24
hours); invalid, non-finite, zero, negative, or larger values fail before storage work. These are
cooperative deadlines checked at application phase boundaries and between bounded storage chunks;
case-coordination lock acquisition uses 50-millisecond timed waits and checks the same probe again
immediately after acquisition. Deadlines do not preempt a Win32 call or an already entered critical
section; exact resource release and outcome settlement still run to completion.

Cancellation and deadline expiry are checked through the last pre-publication boundary. Before
source publication, either condition returns its stable closed failure, retains the previous valid
manifest, and removes only a staged object whose exact identity it still proves. Cleanup stops
rather than following a replaced name, link, stream, or reparse target.

Publishing the immutable copy necessarily precedes the manifest commit. A crash, interruption, or
unresolved manifest failure in that interval can therefore leave a random case-scoped file in
`originals/` that no valid manifest references. It is an orphan, not a source record. Normal failure
handling may remove the exact retained object; PWS-0113 owns bounded [restart discovery and
cleanup](startup-recovery-and-diagnostics.md) when immediate proof or cleanup is unavailable.

Lease cleanup is one-shot and attempts the pending handle, source/manifest/checkpoint reservations,
and retained case authority independently. Cleanup faults cannot replace the original precommit
failure. After an authoritative manifest commit, cleanup faults likewise cannot turn the committed
import into a failure response; any unreleased process-local bookkeeping remains fail-safe capacity
until process restart rather than permitting an unsafe second write.

A confirmed failure before checkpoint publication permits exact cleanup of the new source. An
unknown checkpoint or manifest outcome conservatively preserves the random published bytes and its
recovery evidence; the store does not downgrade uncertainty to permission to delete. A delayed
process-control interruption is propagated only after those artifacts are preserved. Fresh open
fully validates current and checkpoint state, promotes an exact next revision when possible, and
otherwise makes no new valid-record claim. PWS-0113 owns explicit
[orphan resolution](startup-recovery-and-diagnostics.md).

After source publication begins, commit code stops consulting cancellation and deadline probes and
settles the manifest outcome by exact destination identity. Once manifest replacement is confirmed,
late cancellation, deadline expiry, interruption, or response loss cannot roll the import back or
report a precommit failure. A fresh open observes the complete committed source/page graph, and a
retry can report the prior content through the ordinary duplicate advisory. No failure path rewrites
the external original or a previously committed case copy.

## Integrity verification

Integrity verification resolves only a validated source record and opaque storage name beneath its
authenticated case. It classifies an absent case copy as `missing` and unsafe reparse, directory,
hard-linked, alternate-stream, replaced, or otherwise out-of-scope objects as `mismatch`. For an
admissible object it streams the default contents within the recorded size bound and compares both
size and SHA-256 with the manifest.

`SourceIntegrityResult` has exactly three `SourceIntegrityStatus` values: `verified`, `missing`, and
`mismatch`. `mismatch` covers unsafe object identity/type/link/stream/reparse state as well as a size
or digest difference. A failed or unknown check never repairs bytes, updates the digest, or mutates
the manifest. Export and later document work must block when required integrity is not `verified`.

This detects accidental or application-model mutation. It is not authentication against malware,
an administrator, or another principal able to rewrite both the case copy and its protected
manifest.

## Privacy and diagnostics

The external path is never persisted. The display name is persisted only inside the protected
manifest; during import it is transient validated application/storage input, not a storage/temp
name, RPC path, cache key, command argument, result representation, or log field. Routine
diagnostics also exclude source/case paths, file content, metadata values, SHA-256 digests, raw
exceptions, and Win32 messages. They may contain only opaque internal IDs, versions, numeric
counts/resource observations, duration, stable error codes, and sanitized context tokens.

Named streams attached to an external source are not document content and are neither enumerated nor
copied. Case pending files and immutable originals must have only the default stream. Random names,
restrictive case permissions, and handle authentication limit disclosure but do not create a secure
erasure or same-account-malware claim.

## Public domain and progress contracts

- `PreparedSourceSummary` binds the pending/case/source IDs to media type, byte size, and hidden
  SHA-256 after preparation.
- `CompletedSourceInspection` binds a versioned complete page count and media-appropriate signature
  and encryption states to the pending ID.
- `SourceImportResult` returns the committed case/source/page IDs, media type, size, hidden digest,
  manifest revision, and one `DuplicateContentSignal` whose matching source IDs are sorted.
- `SourceIntegrityResult` carries only case/source IDs and the closed integrity status.
- `SourceImportProgress` reports monotonic numeric units for `copying`, `verifying_copy`,
  `inspecting`, `committing`, or `verifying_integrity`; it contains no display or path value.
- Each import/integrity call uses one immutable monotonic deadline; its probe crosses every adapter
  boundary and is checked cooperatively until the documented publication settlement boundary.
- `SourceIngestError` exposes only one closed `SourceIngestErrorCode`, stable context token, and safe
  message. Its string and representation never append a path, display name, digest, or raw failure.

`app.source_ingest.SourceIngestService` orders preparation, inspection, and commit inside one
`PreparedSourceLease`, then validates every returned case/pending/source/media/size/digest/page
binding before exposing a result. `SourceIngestStore` is the narrow prepare/integrity port;
`SourceInspector` supplies complete typed metadata; `DeadlineProbe`, `CancellationProbe`, and
`ProgressSink` cross the same typed ports. Cancellation/progress callbacks and monotonic-clock
failures become sanitized closed errors. The application layer never opens a source, pending file,
original, or manifest itself.

## Verification ownership

Unit tests cover boundary-sized streaming, independent source/staged hashes, short reads, staged
readback mismatch, cooperative cancellation/deadline phase checks, ID collisions, duplicate
advisories, complete source/page manifest construction, imports before and after page-operation
history, recoverable-checkpoint conflicts, immutable results, and sanitized errors.

Windows integration and security tests cover the real single source lease, unchanged external
content/size/modification time/file identity, a link-count source change during copy, quota
rejection, atomic manifest outcome states, fresh-service reopen, simultaneous stale commits,
pending-checkpoint recovery before the next import, post-publication cancellation/interruption
settlement before and after checkpoint publication, random-name privacy, stored-copy hard links,
sibling/outside preservation, and later missing/size/digest mutation. All document fixtures are
synthetic and boundary carriers are generated on demand. Physical-volume exhaustion,
end-to-end alternate-stream/reparse identity races, power-loss durability, and restart orphan
cleanup are not claimed; their mapped later/adjacent owners retain those scenarios.
