# Secure case-workspace contract

- Status: Implemented PWS-0101 contract
- Task: PWS-0101
- Decisions: PWS-D009, PWS-D010, PWS-D028, PWS-D029
- ADR: [ADR 0003](../adr/0003-secure-case-workspace.md)

## Scope

`paperworkshell.storage` owns the authenticated Windows filesystem boundary for case-workspace
layout, private directory creation, bounded inventory, cache/temp capacity reservation, and safe
disposable cleanup. It does not parse documents and does not import UI, application, worker, PDF,
image, OCR, overlay, preflight, export, or diagnostics layers.

Package initializers remain passive. Consumers import the explicit submodule that owns a type; the
namespace does not re-export implementations.

## Fixed layout and ownership

| Area | Provisioned by PWS-0101 | PWS-0101 mutation | Future owner |
|---|---:|---|---|
| case root | yes | private creation and verification | PWS-0102 lifecycle |
| `manifest.json` | reserved only | none | PWS-0102 initial record; PWS-0109 repository |
| `journal/` | yes | no general cleanup | PWS-0109 atomic persistence and recovery |
| `originals/` | yes | no general cleanup | PWS-0104 immutable import |
| `cache/thumbnails/` | yes | authenticated child cleanup; root preserved | PWS-0108 bounded thumbnail service |
| `cache/previews/` | yes | authenticated child cleanup; root preserved | PWS-0106/PWS-0108 rendering work |
| `temp/` | yes | authenticated child cleanup; root preserved | PWS-0107/PWS-0112 operations; PWS-0113 orphan cleanup |
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
  `WorkspaceCapacityReservation` retains that declared scope.
- `cleanup_disposable()` removes only an authenticated cache/temp inventory and returns a
  sanitized `CleanupReport` with complete or partial status.

Callers must not perform security-sensitive I/O merely because `resolve_case_relative()` returned a
path. An owning broker operation must authenticate the opened handle immediately before use.

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
fail closed. File accounting uses logical length, not allocated clusters.

The reservation is not itself filesystem authority. The PWS-0101 manager does not expose a general
write method; the later broker operation that owns a write must compare its exact target and object
type with the retained reservation scope, authenticate the opened parent/target, and keep the claim
until success or cleanup.

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
report counts only successful removals and logical bytes released before that point. The report does
not contain names or paths. Callers must present partial status accurately and may offer a later
retry after active operations close.

## Explicit deferrals

- PWS-0102: create/open/close lifecycle, ownership of active operations, and full case removal.
- PWS-0104: single-open external source copy, content hash, immutable publication, and original-file
  enforcement.
- PWS-0107: worker host/client lifecycle, active-case capability, cancellation, limits, and initial
  filesystem/network restrictions.
- PWS-0108: bounded thumbnail writes, cache keys, cancellation, invalidation, and cache cleanup.
- PWS-0109: authoritative manifest transactions, autosave journal, validation, and recovery.
- PWS-0110: deterministic page operations and undo/redo history.
- PWS-0112: export staging, assembly, reopen, verification, record, and destination commit.
- PWS-0113: crash recovery, orphan-temp cleanup, sanitized logs, and diagnostic baseline.
- PWS-0705/PWS-0706: retention consent, expiry, lock, inventory review, and assisted deletion.

No caller may fill one of these gaps with direct broad filesystem access through the PWS-0101
manager.
