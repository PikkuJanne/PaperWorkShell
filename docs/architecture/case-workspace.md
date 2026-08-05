# Secure case-workspace contract

- Status: Implemented PWS-0101 contract with lifecycle, ingest, manifest-repository, and thumbnail extensions
- Tasks: PWS-0101, PWS-0102, PWS-0104, PWS-0108, PWS-0109
- Decisions: PWS-D009, PWS-D010, PWS-D011, PWS-D028, PWS-D029, PWS-D036
- ADR: [ADR 0003](../adr/0003-secure-case-workspace.md)
- Lifecycle: [Local case-lifecycle contract](case-lifecycle.md)
- Source ingest: [Immutable source-ingest contract](immutable-ingest.md)
- Persistence: [Atomic manifest repository and autosave contract](manifest-repository.md)
- Thumbnails: [Bounded PDF-thumbnail service contract](pdf-thumbnails.md)

## Scope

`paperworkshell.storage` owns the authenticated Windows filesystem boundary for case-workspace
layout, private directory creation, bounded inventory, cache/temp capacity reservation, immutable
source preparation/publication, and safe disposable cleanup. It does not parse documents and does
not import UI, application, worker, PDF, image, OCR, overlay, preflight, export, or diagnostics
layers.

Package initializers remain passive. Consumers import the explicit submodule that owns a type; the
namespace does not re-export implementations.

## Fixed layout and ownership

| Area | Provisioned by PWS-0101 | PWS-0101 mutation | Owning task/boundary |
|---|---:|---|---|
| case root | yes | private creation and verification | PWS-0102 active lease and full removal |
| `manifest.json` | reserved only | none | PWS-0102 initial record; PWS-0109 validated general transactions and ingest integration |
| `journal/` | yes | no general cleanup | PWS-0109 atomic persistence and recovery |
| `originals/` | yes | no general cleanup | PWS-0104 authenticated immutable source publication and integrity checks |
| `cache/thumbnails/` | yes | authenticated child cleanup; root preserved | PWS-0108 bounded thumbnail service |
| `cache/previews/` | yes | authenticated child cleanup; root preserved | reserved for later non-thumbnail preview derivatives |
| `temp/` | yes | authenticated child cleanup; root preserved | PWS-0104 source preparation; PWS-0107/PWS-0112 operations; PWS-0113 orphan cleanup |
| `export-history.json` | reserved only | none | PWS-0112 verified export records with PWS-0109 persistence integration |

Case roots use canonical lowercase UUIDv4 names. Provisioning retries generated collisions up to a
bounded limit; an explicit existing identifier fails without mutation. Rollback removes only empty
directories created by the failed call, in reverse order, after identity revalidation.

## Public contracts

### `storage.workspace`

- `new_case_id()` returns an opaque UUIDv4.
- `validate_case_id()` and `validate_case_relative_path()` are pure lexical prechecks.
- `WorkspaceArea` is the fixed layout resolver enum.
- `CleanupTarget` is a narrower enum containing only `CACHE` and `TEMP`.
- `CaseWorkspaceManager.for_current_user(quota=...)` binds the production profile root.
- `layout()` constructs fixed paths without filesystem access.
- `resolve_case_relative()` resolves valid fixed-layout syntax but does not authorize I/O.
- `provision()` creates and independently verifies the complete directory skeleton.
- `measure_usage()` returns a bounded no-follow logical usage snapshot.
- `reserve_capacity()` binds an intended relative path and file/directory type to a process-local
  cache/temp claim against measured and outstanding use. The returned
  `WorkspaceCapacityReservation` retains that declared scope, byte/entry amounts, authenticated
  case identity, token, and liveness.
- `cleanup_disposable()` removes only an authenticated cache/temp inventory and returns a
  sanitized `CleanupReport` with complete or partial status.
- `enumerate_case_ids()` authenticates the cases root and bounds UUID-named candidates without
  creating storage; the lifecycle store authenticates each candidate independently so one damaged
  entry is reported as unavailable without hiding valid cases.
- `authenticate_case()` retains cases-root and case-root handles under shared lifecycle,
  reservation, and deletion coordination.
- `remove_case()` accepts only an authenticated delete authority, validates a complete case-tree
  inventory before mutation, invalidates `manifest.json` first, and returns sanitized complete or
  partial counts.

Callers must not perform security-sensitive I/O merely because `resolve_case_relative()` returned a
path. An owning broker operation must authenticate the opened handle immediately before use.

### PWS-0104/PWS-0109 publication extensions

The immutable-ingest storage boundary accepts one live PWS-0103 source lease, streams to a random
authenticated pending file, verifies the staged bytes, and publishes only the exact retained
identity under the derived `<source-id>.bin` name. The PWS-0109 repository holds shared case
coordination through source, checkpoint, and manifest publication, so deletion or a second
publisher cannot operate on an older exact case snapshot.

The general repository validates complete current and candidate manifests, publishes the one-snapshot
checkpoint, atomically replaces the manifest, and recovers an interrupted next revision. Import
adds a complete source/page catalogue and either extends an untouched base/final sequence or records
a typed import transition after existing operations. Content inspection is supplied as typed
metadata; storage never calls a PDF or image engine. Exact behavior is defined by the
[immutable source-ingest](immutable-ingest.md) and
[manifest-repository](manifest-repository.md) contracts.

### PWS-0108 thumbnail-cache extension

The thumbnail boundary adds no general cache writer. For each request, storage first authenticates
the current manifest page-to-PDF mapping and exact immutable source identity, size, SHA-256,
single-link/non-sparse state, and alternate-stream state. That same retained read handle can then
be duplicated with reduced rights into one worker generation. Cache hits are attempted only after
this source authentication, so disposable state cannot conceal a changed or missing original.

`storage.thumbnail_cache` derives a final entry name only from the validated internal page UUID and
canonical cache-key digest. A miss creates one distinct random UUID-named `.tmp` stage after
reserving its maximum header-plus-pixel size against the cache quota. The returned stage exposes a
one-shot bounded write method only to the trusted broker; it retains authenticated case, directory,
reservation, and publication coordination through validation and disposition. The broker gives the
worker only a separate fixed-size volatile section and never exposes a persistent cache-file,
cache-path, or directory handle.

After confirmed worker-tree termination, the broker validates the volatile bytes before writing the
stage. The stage is then sealed and read back. Storage validates the fixed artifact against the
expected key, operation UUID, cache-object UUID, dimensions, layout, profile, length, and digests
before an atomic no-replace rename to the final internal name. A valid publication collision is
reused; a corrupt exact entry is removed and rebuilt. Unconfirmed worker termination leaves the
broker-only stage empty and disposable; only an indeterminate persistent publication quarantines a
random stage rather than guessing that it is trusted or safely disposable.

The store supports exact-key invalidation, bounded page-ID invalidation, corrupt-entry removal, and
bounded stale-stage cleanup. Every deletion authenticates the thumbnail root and exact regular-file
identity before marking it for deletion and returns only content-free counts/status. The cache is
never referenced by the manifest and remains safe to lose or regenerate. The complete key,
artifact, brokerage, termination, privacy, and cleanup rules are defined by the
[bounded PDF-thumbnail service contract](pdf-thumbnails.md).

### `storage.permissions`

`PermissionAdapter` is the narrow creation/verification seam. The production
`WindowsPrivateDirectoryPermissions` implementation creates one directory with the supported
private mode, reopens it without following a final reparse point, and returns its file identity only
after the exact ACL is verified. Test doubles may implement the protocol but production code must
not substitute an adapter that weakens the accepted policy.

### `storage.quotas`

`WorkspaceQuota` requires positive bounded caller values for complete-case, cache, and temp bytes;
at least six entries for the empty skeleton; and an inventory depth from 2 through 64. `QuotaArea`
contains only cache and temp. `WorkspaceCapacityReservation` is a context manager and also supports
idempotent explicit release. A caller must provide the intended case-relative path and whether its
terminal object is a directory, then keep the claim until its owning bounded write either publishes
or removes its output.

Quota maxima are inclusive. Inventory stops before accepting an entry beyond the configured count
and consumes no more than the remaining budget plus one rejection sentinel. It rejects a directory
deeper than the configured maximum before descending. Reservations for the same authenticated case
identity share one book across manager instances in the broker process, and conflicting policies
fail closed. One per-case coordination lock covers fresh authenticated inventory, adding a claim,
publication guards, and release; a publish-and-release cannot leave the next claim using an older
snapshot. File accounting uses logical length, not allocated clusters.

The reservation is not itself filesystem authority. The manager does not expose a general write
method; the owning broker operation must compare its exact target and object
type and actual byte/entry amounts with the retained reservation scope, authenticate and retain the
opened case/parent/target identity, and hold `publication_guard` through authoritative publication.
The guard rejects inactive, excessive, mismatched, or replacement-case use and coordinates with
other reservations. The caller keeps the claim until success or cleanup, then releases it outside
the guard.

### `storage.errors`

`WorkspaceError` contains only a closed `WorkspaceErrorCode`, a stable internal context token, and a
plain safe message. Code that presents or logs a failure must use those fields and must not append a
path, SID, account, original filename, document value, raw exception, or Win32 message.

## Filesystem authorization sequence

For a security-sensitive operation, the manager:

1. lexically rejects unsafe Windows namespaces and components before mutation;
2. requires a fixed local volume with persistent ACLs;
3. opens and pins the profile, cases-root, case, and selected-area directories without delete
   sharing and without following a final reparse point;
4. verifies directory type, final path, reparse state, file identity, owner, and exact protected
   DACL;
5. inventories children in deterministic case-insensitive name order within explicit entry/depth
   limits;
6. rejects reparse points, hard-linked regular files, alternate data streams, unsafe names, or
   changed identities; and
7. for cleanup only, reopens and identity-checks every inventory component before handle-based
   deletion.

This sequence protects the selected object; lexical containment by itself is never the authority.

## Failure and cleanup behavior

Provision failure never publishes manifest or export-history state. Best-effort rollback stops when
an identity cannot be proven or a created directory is no longer empty. Leaving a random empty
directory is safer than deleting an unverified object.

Disposable cleanup is intentionally not all-or-nothing. It validates the complete initial inventory
before deletion, removes deepest entries first, and stops on the first changed or failed object. Its
report counts only successful removals and revalidated logical bytes released before that point. The
report does not contain names or paths. Cleanup shares per-case coordination with reservations and
publication; an active claim in the selected cache/temp area fails cleanup before deletion. Callers
must present partial status accurately and may offer a later retry after active operations close.
The authenticated case handle is retained across the complete cleanup, and the selected-area reopen
must match that case identity before inventory begins.

PWS-0102 adds a separate whole-case path after exact confirmation. It rejects active leases and
reservations, validates the supported matching manifest and complete authenticated inventory before
mutation, removes the manifest validity marker first, then deletes the remaining inventory
deepest-first and the exact case root last. A post-invalidation failure is reported as partial;
sibling cases and external objects are never in the inventory. This is application-managed removal,
not a forensic-erasure claim. The full sequence is specified in the
[local case-lifecycle contract](case-lifecycle.md).

PWS-0104/PWS-0109 cleanup is similarly identity-scoped. A confirmed failure before checkpoint
publication leaves the previous manifest authoritative and removes only a pending or published
source whose retained identity still matches. A committed checkpoint preserves the source for
fresh-open recovery. Unknown outcomes preserve recovery evidence; PWS-0113 owns bounded cleanup of
objects that no valid current or checkpoint state references. Confirmed publication wins over late
cancellation rather than attempting an unsafe rollback.

PWS-0108 cache publication is independent of the manifest. A cancelled/failed render after
confirmed worker termination removes only its exact trusted random stage. An unconfirmed worker
has no handle to that persistent stage, so the empty broker-only stage is safely discarded; only an
indeterminate persistent rename quarantines it as non-authoritative. A final cache name appears only
through no-replace publication of a completely validated stage. Cleanup may honestly report a
partial result and retain an unsafe/changed entry; it never widens deletion merely to reclaim space.
Earlier valid cache entries and all authoritative case state remain unchanged.

## Explicit deferrals

- Manifest schema migration remains deferred to its later versioned task; PWS-0109 implements
  current-version authoritative transactions, autosave, and checkpoint recovery.
- PWS-0112: export staging, assembly, reopen, verification, record, and destination commit.
- PWS-0113: crash recovery, orphan-temp cleanup, sanitized logs, and diagnostic baseline.
- PWS-0705/PWS-0706: retention consent, expiry, lock, inventory review, and assisted deletion.

No caller may fill one of these gaps with direct broad filesystem access through the PWS-0101
manager.
