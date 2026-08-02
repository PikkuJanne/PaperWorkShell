# Immutable source-ingest contract

- Status: Implemented PWS-0104 contract
- Task: PWS-0104
- Decisions: PWS-D009, PWS-D010, PWS-D028
- Depends on: PWS-0102 local case lifecycle; PWS-0103 bounded import prechecks
- Data flow: [DF-IMPORT](../security/data-flows.md#df-import--select-precheck-copy-inspect-and-commit-source)

## Scope and ownership

PWS-0104 copies a prechecked external source into one case, computes and verifies its SHA-256
digest, publishes it under a random internal identity, and commits its complete manifest source and
page records. It also reports advisory content duplicates and verifies the later integrity of a
committed case copy.

`domain.source_ingest` carries path-free source metadata, prepared/committed results, duplicate
signals, integrity status, progress, and closed sanitized errors. Application orchestration depends
on structural preparation, commit, and integrity protocols. The concrete storage adapter owns the
authenticated Windows handles, bounded streaming I/O, case publication, and the narrow import-only
manifest append. Storage does not import the application layer or parse PDF/image structure, and no
package initializer re-exports an implementation.

This task does not add a document engine, general manifest repository, autosave journal, page-edit
operation, UI, network behavior, or runtime dependency. PWS-0105 and later image adapters own
complex inspection. PWS-0109 owns general manifest transactions, autosave, and recovery; PWS-0110
owns page operations and replay after the initial import catalogue exists.

## Two-phase prepare and commit

An import is deliberately split so untrusted bytes can be inspected without making them an
authoritative source.

### Phase 1 — quarantine and verify bytes

1. PWS-0103 opens and prechecks the external source through one retained read-only handle. The
   selected path is never reopened for the copy.
2. The operation allocates distinct random pending and source UUIDv4 identities. While the lease is
   live, the ingest adapter streams the exact default stream into the pending case file and
   calculates SHA-256. Reads and writes are bounded and cancellation is checked between chunks.
3. The pending file is flushed and reauthenticated as a regular single-link file with no reparse
   point or named stream. Its retained handle rewinds and independently rehashes the written bytes
   to verify the exact size and digest.
4. Control returns normally through the PWS-0103 context so its terminal source identity, size,
   timestamps, link count, final path, and ancestor checks complete before preparation succeeds.
5. The resulting `PreparedSourceSummary` contains only pending, case, and source IDs, verified byte
   facts, preliminary media state, and case-scoped authority. It is not a source record and cannot
   be resolved from an external path.

Cancellation, a changed or short source, disk-full, a partial write, flush failure, staged-copy
mutation, or failed terminal source check leaves no authoritative source record. The external source
is never opened for writing and its display path does not become case authority.

### Inspection between phases

Commit requires complete trusted metadata for the exact prepared copy:

- detected PDF, JPEG, or PNG media type;
- verified byte size and SHA-256 digest;
- a positive bounded page count;
- media-appropriate signature and encryption states; and
- a bounded, normalized display name for protected presentation only.

JPEG and PNG prechecks establish one source page and use `not_applicable` signature/encryption
states. A PDF precheck intentionally has no page count and reports both states as `not_checked`.
Those preliminary PDF values cannot be promoted into a source record. PWS-0105 must inspect the
same prepared case copy through an exact brokered read-only handle and return a trusted page count
and conservative inspection states before PDF commit. `unknown` remains a real inspection result;
it is never fabricated to bypass `not_checked`.

Worker output is treated as untrusted typed input. The trusted application/storage boundary checks
its case/source binding and limits before commit. The worker cannot publish an original, manifest,
journal, source record, or page record.

### Phase 2 — publish and commit metadata

After complete metadata validates:

The commit holds both source-byte and manifest-growth reservations under the shared per-case
publication guard through the following ordered publications.

1. The prepared source ID determines the only storage name, `<source-id>.bin`. Generated
   collisions are retried within a fixed bound and never overwrite an existing object.
2. The exact verified pending file is atomically published beneath `originals/` while authenticated
   case and parent handles remain pinned. A read-only handle to that published identity remains live
   through manifest publication.
3. Duplicate detection compares the verified SHA-256 digest with committed source records under the
   same serialized commit authority. Matching source IDs are returned as an advisory signal.
   Duplicates still receive distinct source IDs, case copies, display names, and page identities;
   PWS-0104 performs no silent deduplication.
4. The narrow manifest appender adds one complete source record and exactly `page_count` new page
   records in zero-based source-page order. It appends the new page IDs in the same order to both
   `page_sequence_base` and `page_sequence` and creates no page operation.
5. The appender requires the import-only replay shape: the current manifest is supported and fully
   valid, `page_operations` is empty, and its base and final page sequences match before the append.
   It preserves every unrelated member, increments the revision once, uses ordered UTC timestamps,
   and validates the complete target manifest before publication.
6. A random staged manifest is written and flushed, then atomically replaces `manifest.json`.
   `ManifestReplaceResult` distinguishes `committed`, `not_committed`, and `unknown`; a confirmed
   replacement is the import commit point. The committed manifest and published source together are
   authoritative.

The appender is intentionally narrower than the PWS-0109 repository. It cannot save arbitrary UI
state, coalesce edits, migrate schemas, publish a journal, or append imports after PWS-0110 page
operations exist. PWS-0109 must absorb this validated append behavior into the general transaction
boundary without weakening its version, identity, schema, or semantic checks.

## Cancellation, failure, and restart boundary

Cancellation is cooperative before the manifest replacement commit point. Before that point, the
operation returns a cancelled result, retains the previous valid manifest, and removes only a
staged or published object whose exact identity it still proves. Cleanup stops rather than following
a replaced name, link, stream, or reparse target.

Publishing the immutable copy necessarily precedes the manifest commit. A crash, cancellation, or
manifest failure in that interval can therefore leave a random case-scoped file in `originals/`
that no valid manifest references. It is an orphan, not a source record. Normal failure handling may
remove the exact retained object; PWS-0113 owns bounded restart discovery and cleanup when immediate
proof or cleanup is unavailable.

`not_committed` permits exact cleanup of the new source. An `unknown` replacement outcome preserves
the random published bytes because the manifest may reference them; it returns a stable
failure/review state and makes no new valid-record claim until a fresh open resolves authority.
PWS-0113 may remove the copy only if recovery proves it remains unreferenced.

Once manifest replacement is confirmed, late cancellation or response loss cannot roll the import
back or report a precommit cancellation. A fresh open observes the complete committed source/page
graph, and a retry can report the prior content through the ordinary duplicate advisory. No failure
path rewrites the external original or a previously committed case copy.

## Integrity verification

Integrity verification resolves only a validated source record and opaque storage name beneath its
authenticated case. It opens the case copy read-only, rejects a missing, reparse, directory,
hard-linked, alternate-stream, replaced, or otherwise out-of-scope object, streams its default
contents within the recorded size bound, and compares both size and SHA-256 with the manifest.

`SourceIntegrityResult` has exactly three `SourceIntegrityStatus` values: `verified`, `missing`, and
`mismatch`. `mismatch` covers unsafe object identity/type/link/stream/reparse state as well as a size
or digest difference. A failed or unknown check never repairs bytes, updates the digest, or mutates
the manifest. Export and later document work must block when required integrity is not `verified`.

This detects accidental or application-model mutation. It is not authentication against malware,
an administrator, or another principal able to rewrite both the case copy and its protected
manifest.

## Privacy and diagnostics

The external path is never persisted. The display name exists only inside the protected manifest;
it is not a storage/temp name, RPC path, cache key, command argument, result representation, or log
field. Routine diagnostics also exclude source/case paths, file content, metadata values, SHA-256
digests, raw exceptions, and Win32 messages. They may contain only opaque internal IDs, versions,
numeric counts/resource observations, duration, stable error codes, and sanitized context tokens.

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
- `SourceIngestError` exposes only one closed `SourceIngestErrorCode`, stable context token, and safe
  message. Its string and representation never append a path, display name, digest, or raw failure.

`app.source_ingest.SourceIngestService` orders preparation, inspection, and commit inside one
`PreparedSourceLease`, then validates every returned case/pending/source/media/size/digest/page
binding before exposing a result. `SourceIngestStore` is the narrow prepare/integrity port;
`SourceInspector` supplies complete typed metadata; `CancellationProbe` and `ProgressSink` are
wrapped so callback failures become sanitized closed errors. The application layer never opens a
source, pending file, original, or manifest itself.

## Verification ownership

Unit tests cover boundary-sized streaming, independent source/staged hashes, short reads, write and
flush faults, cancellation points, ID collisions, duplicate advisories, complete source/page
manifest construction, the import-only replay precondition, commit-point behavior, immutable
results, and sanitized errors.

Windows integration and security tests cover the real single source lease, unchanged external
content and modification time, source mutation during copy, disk-full, atomic manifest replacement,
fresh-service reopen, concurrent imports, post-publication interruption, random-name privacy,
hard-link/alternate-stream/reparse races, sibling/outside preservation, and later case-copy
mutation. All document fixtures are synthetic and large boundary carriers are generated on demand.
