# ADR 0003: Secure Windows case-workspace primitives

- Status: Accepted
- Date: 2026-08-02
- Task: PWS-0101
- Decisions: PWS-D009, PWS-D010, PWS-D028, PWS-D029

## Context

PaperWorkShell needs a case-storage boundary before it can import or parse untrusted documents.
The boundary must keep opaque case state below the current Windows profile, apply a restrictive
access policy at directory creation, reject path aliases and redirection, enforce explicit storage
limits, and clean disposable data without following an attacker-controlled path. Python path
normalization alone cannot authorize mutation on Windows because junctions, symbolic links, hard
links, alternate data streams, device namespaces, and rename races can change what a name refers
to.

PWS-0101 establishes storage conventions and narrow primitives only. It does not implement case
lifecycle, source import, manifest transactions, journal recovery, worker capabilities, export
publication, retention, or full-case deletion.

## Decision

### Fixed location and layout

The production case root is resolved from the operating-system authenticated profile known folder,
not from an environment variable:

```text
<profile>\AppData\Local\PaperWorkShell\Cases\<canonical-uuid-v4>\
    manifest.json             reserved; not created by provisioning
    journal\
    originals\
    cache\
        thumbnails\
        previews\
    temp\
    export-history.json       reserved; not created by provisioning
```

An explicitly configured cases root must still be a dedicated descendant of the supplied profile.
UNC, remote, removable, device, extended-namespace, alternate-stream, drive-relative, and
non-ACL-persistent locations fail closed. Case directory names are canonical lowercase UUIDv4
values. Other internal components use a bounded lowercase ASCII grammar and cannot contain a
Windows reserved device stem.

`manifest.json` and `export-history.json` are names reserved for their owning tasks. Workspace
provisioning never publishes either authoritative record. The skeleton's separate `originals`,
`journal`, `cache`, and `temp` areas prevents a disposable cleanup operation from selecting an
authoritative area.

### Permissions

Each directory is created separately with `os.mkdir(path, 0o700)`. On supported Python 3.14 Windows
builds this requests a protected DACL granting inheritable full control only to SYSTEM, Builtin
Administrators, and Owner Rights. Creation success is not treated as permission evidence. A
separate opened-handle check requires:

- a fixed local volume with persistent ACL support;
- a directory owned by the current token user or token owner;
- a present, non-null, non-defaulted, protected DACL;
- exactly three ordinary allow ACEs for SYSTEM, Builtin Administrators, and Owner Rights;
- full-control masks and object/container inheritance on every ACE; and
- no inherited, deny, callback, conditional, object-specific, or unknown ACE form.

An unsupported platform or filesystem produces `WORKSPACE_PERMISSIONS_UNSUPPORTED`; an unexpected
owner or access policy produces `WORKSPACE_PERMISSIONS_INSECURE`. The implementation does not
silently accept a plausibly safe enterprise ACL that it cannot prove matches the supported policy.

### Handle-authenticated filesystem operations

Existing objects are opened with `CreateFileW`, `FILE_FLAG_OPEN_REPARSE_POINT`, and directory
backup semantics. Handles omit delete sharing while an operation pins ancestors. The adapter then
checks the opened type, reparse attribute, final normalized path, volume serial number, 128-bit file
ID, logical size, and hard-link count as applicable.

Lexical path validation is only a precheck. Authorization for inventory and cleanup comes from the
opened object and its expected identity. Inventories reject reparse points, multi-linked regular
files, named NTFS alternate data streams, unexpected top-level objects, unsafe names, and configured
entry/depth overflow. Files are measured by logical end-of-file length so sparse allocation cannot
bypass a logical-byte quota.

Deletion is restricted to the closed `cache` or `temp` target type. Cleanup first builds a complete
bounded no-follow inventory. It then reopens every ancestor and leaf, compares the terminal file
identity and logical size with the inventory, and marks that verified handle for deletion with
`SetFileInformationByHandle`. The cache root and its `thumbnails` and `previews` roots, or the temp
root, remain in place. A changed identity, alias, or deletion error returns an accurate partial
report and stops; cleanup never falls back to recursive name-based deletion. Cleanup holds the same
per-case coordination lock as publication and fails while the selected area has an active capacity
claim, so it cannot delete or misreport a cooperating in-flight write.

### Quotas and failures

Every manager requires a caller-supplied `WorkspaceQuota`. PWS-0101 does not invent product limits.
The quota bounds total logical case bytes, cache bytes, temp bytes, inventory entries, and inventory
depth. The entry limit must accommodate the six fixed descendants, and the supported recursive
inventory depth is 2 through 64. Directory enumeration consumes at most the remaining global entry
budget plus one rejection sentinel before sorting. Capacity reservations bind an intended
case-relative cache/temp path and object type, reject excessive depth before mutation, and serialize
measured use and outstanding claims by authenticated case identity across manager instances inside
one trusted storage-broker process. They are conservative claims, not filesystem allocation
guarantees. Fresh authenticated inventory, claim creation, publication coordination, and release use
one per-case lock so a publish-and-release cannot race a stale usage snapshot. The returned
reservation retains the case identity, declared path/type, byte and entry claims, and liveness. A
future owning write adapter must hold its `publication_guard` while publishing from the same opened
case identity; mismatched scope, excessive amounts, case replacement, and use after release fail
closed.

Failures use the closed `WorkspaceErrorCode` registry, a fixed context token, and a plain safe
message. Paths, filenames, account names, SIDs, raw Win32 errors, document values, and original
exceptions are not included in routine error strings or cleanup reports.

The concrete API and ownership boundaries are documented in
[Secure case-workspace contract](../architecture/case-workspace.md).

## Security, privacy, accessibility, and packaging effects

- The workspace uses no new third-party or packaged dependency. Its operating-system adapter is a
  narrow standard-library `ctypes` boundary.
- Random internal names and hidden path fields in object representations reduce accidental path or
  applicant-name disclosure. They do not make local case state anonymous.
- Originals and authoritative records are outside the only cleanup API. PWS-0104 must add immutable
  copy-and-hash publication; PWS-0102 owns the valid initial manifest and PWS-0109 owns subsequent
  transactional manifest/journal behavior.
- Stable codes and plain messages are suitable for later accessible UI recovery. PWS-0102 owns
  lifecycle orchestration, and PWS-0111 owns accessible UI announcements; this task adds no UI or
  timeout.
- Restrictive ACLs protect against ordinary access by other non-administrative accounts. They do
  not claim protection against the same account, administrators, SYSTEM, malware, backups,
  indexing, volume snapshots, or forensic recovery.

## Alternatives considered

### Path normalization followed by recursive deletion

Rejected. A path can be replaced or redirected after normalization, and ordinary recursive APIs
can follow an unsafe namespace or act on a different identity.

### Create with inherited permissions and tighten afterward

Rejected. That creates an avoidable interval in which case state can inherit broader access. The
supported Python 3.14 creation mode applies the private DACL at creation and is independently
verified before the directory is accepted.

### Accept any ACL with no obvious broad trustee

Rejected. Correctly evaluating arbitrary Windows access-control semantics requires more policy than
this task owns. Unknown ACLs fail closed rather than being guessed safe.

### General case-tree mutation API

Rejected. Callers receive fixed areas and typed cache/temp cleanup. Lifecycle, import, manifest,
journal, export, and retention tasks must add narrower operations with their own invariants.

### Native handle-relative creation through undocumented NT interfaces

Rejected for this task. Python and documented Win32 do not provide a directory-relative creation
primitive with the required creation-time ACL contract. Random names, delete-share-denying ancestor
handles, exclusive creation, immediate no-follow reopen, identity checks, and the stated same-account
attacker exclusion provide the supported boundary without adding an undocumented native interface.

## Consequences and residual risks

- Supported storage is deliberately Windows-only, fixed-local, and persistent-ACL capable. A
  stable limitation is preferable to weaker remote, removable, or ACL-less semantics.
- Permission and identity verification are point-in-time checks. The same account, an administrator,
  or SYSTEM can change case state after verification.
- `os.mkdir` cannot return the created directory handle. There is a narrow create-to-reopen interval;
  the threat model's exclusion of unrestricted same-account and administrator compromise is
  therefore material.
- Capacity reservations are process-local and do not stop another process from consuming disk
  space. Worker restrictions and operation lifecycle remain PWS-0107 work; broker-enforced import,
  cache, persistence, and export writes remain PWS-0104, PWS-0108, PWS-0109, and PWS-0112 work.
- Cleanup accurately reports partial completion but does not promise transactionality, recoverable
  deletion, secure erasure, or full-case deletion. Lifecycle cleanup, retention, and assisted
  deletion remain PWS-0102, PWS-0113, PWS-0705, and PWS-0706 work.
- Source immutability/hash verification, manifest/journal recovery, page operations, export
  staging/history, signed-source policy, and sanitized diagnostics remain assigned to PWS-0104,
  PWS-0109, PWS-0110, PWS-0112, PWS-0113, and their downstream tasks.

## Implementation references

- [Python 3.14 `os.mkdir`](https://docs.python.org/3.14/library/os.html#os.mkdir)
- [Microsoft `CreateFileW`](https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-createfilew)
- [Microsoft `FILE_ID_INFO`](https://learn.microsoft.com/windows/win32/api/winbase/ns-winbase-file_id_info)
- [Microsoft `GetSecurityInfo`](https://learn.microsoft.com/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo)
- [Microsoft `SetFileInformationByHandle`](https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)
