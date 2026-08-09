# ADR 0004: Restricted Windows worker isolation and launch lifecycle

- Status: Accepted
- Date: 2026-08-02
- Task: PWS-0107
- Extensions: PWS-0105 first exact document-handle operation; PWS-0108 bounded thumbnail output; PWS-0111 desktop composition startup; PWS-0112 verified PDF export; PWS-0113 persistent worker ACL recovery
- Decisions: PWS-D012, PWS-D013, PWS-D027, PWS-D028, PWS-D036, PWS-D038

## Context

PaperWorkShell must parse hostile documents outside the desktop process. A second process by itself
does not provide the required boundary: an ordinary child inherits the user's filesystem and
network authority, environment, search paths, and descendant-process behavior. A compromised
parser in such a process could still read unrelated case data, connect to a local or remote
endpoint, start an unconstrained descendant, or leak values through process arguments and errors.

The first worker operation is deliberately `protocol.noop`. It proves launch, negotiation,
progress, cancellation, timeout, shutdown, crash handling, and restart without parsing a document.
PWS-0107 therefore has to establish the operating-system boundary before later PDF, renderer, and
OCR adapters depend on it, while avoiding a premature filesystem authority design.

## Decision

### Use a zero-capability AppContainer

Every worker generation receives a newly created classic Windows AppContainer profile. The process
is created with `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`, the profile SID, and an empty
capability array. In particular it receives none of the Windows network capabilities. The worker
checks its own token before protocol negotiation and refuses to send `session.ready` unless it is
an AppContainer, its capability count is zero, it is in a job, and its environment matches the
closed allowlist.

The profile SID receives direct, non-inheritable read/execute entries only on the enumerated Python
runtime, PaperWorkShell code, and configured fixed-tool objects needed to start and operate the
host. A child created after the inventory scan receives no worker SID entry. No AppContainer
access-control entry is added to a cases root, an external source, a destination, or another user
path.

The launcher preflights a bounded resource inventory before the first ACL mutation and rejects an
absent or NULL DACL. It retains an authenticated `GENERIC_READ` handle without delete sharing for
every approved object and lexical ancestor, rejects reparse/delete-pending objects, and requires
every enumerated file's Windows link count to be exactly one. Those handles prevent an after-scan
replacement, ancestor-junction pivot, rename, or alias removal through verified cleanup. Because a
share-read handle does not itself stop an outward `CreateHardLink`, every retained file participates
in one direct, non-inheritable current-user deny ACE for `FILE_WRITE_ATTRIBUTES` before any
AppContainer grant. The launcher rechecks handle link count after the deny, after grant, before
resume, and before accepting each fixed startup resource. If the narrow pre-guard race detects a
new alias, grant fails; exact-handle cleanup intentionally revokes/removes PaperWorkShell's entries
despite that link-count mismatch so it does not strand the profile or user policy.
Because a hardlink shares its security descriptor with names outside the approved tree, a
pre-existing aliased file is rejected rather than receiving a SID entry. A new object created after
the scan receives no entry because grants are direct and non-inheritable.

SID-specific grant/revoke operations use handle-based `GetSecurityInfo`/`SetSecurityInfo` under a
protected Global mutex across Windows sessions and do not re-resolve paths or restore a stale whole
descriptor over another launch. The mutex owner is the current user; its protected DACL grants only
that user and LocalSystem; the handle is non-inheritable; and an existing object's owner/DACL must
match exactly. Concurrent profiles therefore preserve one another's direct AppContainer entries.
For hardlink protection, the first authenticated v2 lease records the existing matching-deny count
as its baseline and adds one shared guard; an overlapping `ACTIVE` v2 lease authenticates and reuses
that same baseline and guard. Cleanup retains the guard while another authenticated active lease
covers the exact object. The final cleanup removes exactly one guard and proves the original
baseline, preserving every matching ACE that predated PaperWorkShell. It also proves the temporary
profile SID absent and restores each observed object's original auto-inheritance state through its
retained exact-object handle.

PWS-0113 extends this lease with a durable, bounded, versioned per-profile recovery record beneath
an authenticated private directory in the trusted Windows LocalAppData known folder. A new v2
record is canonical and path-private: it contains the exact profile/AppContainer SID, owner PID and
process-creation time, current-user peer SID, approved grant roots, and every resource's trusted
path, type, Windows volume serial/file ID, original auto-inheritance state, and nullable exact-deny
baseline. Directories have no guard baseline. These values are needed only to revoke an interrupted
grant; they contain no case path and never cross RPC, routine diagnostics, UI results, or
representations. Legacy v1 records remain strictly decodable and recoverable with their original
inherited-root semantics, but new grants emit only v2.

Before creating a new profile, the launcher inventories and repairs dead records under the
protected Global mutex. For a v2 grant it authenticates and opens the complete exact-object
inventory, captures identities, inheritance state, and shared-guard baselines, then flushes
`PREPARED` before the first guard or AppContainer mutation. It establishes or reuses each shared
guard, adds one direct non-inheritable AppContainer entry through every retained object handle,
restores the recorded inheritance-control bits, verifies the grant, and publishes `ACTIVE`.

Cleanup publishes `REVOKING`, removes and verifies the exact AppContainer SID on every recorded
object, and then evaluates the shared guard. An authenticated live `ACTIVE` v2 record for the same
object and baseline keeps that guard in place; otherwise cleanup removes exactly one matching guard
and proves the recorded baseline. Only after it restores and verifies every auto-inheritance state
does it delete the exact ledger and close the retained handles. Dead-owner recovery follows the
same order after reopening and authenticating every recorded object by Windows identity.

On `WAIT_ABANDONED`, recovery inventories and repairs every validated dead/interrupted entry while
retaining mutex ownership and blocks if it can repair none. Windows exposes no prior mutex-owner
identity, so this is deliberately an aggregate cutpoint invariant, not a claimed one-to-one
correlation: an abandonment before its record is pre-mutation, while an abandonment after mutation
necessarily leaves that record. Insecure, corrupt, unknown-version, peer-SID-mismatched,
allowlist-mismatched, identity-changed, conflicting-baseline, or incompletely repaired state blocks
later ACL work and worker launch. The launcher never guesses which unrecorded ACE to remove, deletes
recovery evidence to make startup pass, or treats ACL repair as worker-operation replay.

Python startup requires writable profile and temporary locations. `LOCALAPPDATA`, `TEMP`, and
`TMP` are therefore constructed as paths inside the new per-launch AppContainer profile. They are
never copied from the launching user's environment and carry no case path. This is ephemeral
sandbox storage, not case workspace authority. The broker retains the profile until the exact job
tree has exited, then revokes the temporary runtime/code grants and deletes the profile. Cleanup
failure is a stable launch-cleanup failure rather than a silent success.

The environment allowlist is limited to `SYSTEMROOT`/`WINDIR`, `LOCALAPPDATA`, `TEMP`, `TMP`, the
fixed Python encoding controls `PYTHONIOENCODING` and `PYTHONUTF8`, and the configured fixed startup
or tool inputs described below. User `PATH`, `PATHEXT`, `PYTHONPATH`, proxy variables, cloud
credentials, command-processor variables, and other inherited state do not cross the launch
boundary.

For source-tree execution from a Windows virtual environment, the constructed child environment
also carries one trusted CPython startup input: `__PYVENV_LAUNCHER__` names the separately
validated, absolute virtual-environment launcher inside an approved runtime root. This value is not
copied from the parent environment. CPython consumes and removes it during interpreter
initialization, before PaperWorkShell worker code runs, so the worker-side environment allowlist
above remains unchanged. A non-virtual-environment runtime omits the input.

PWS-0112 may add `PWS_QPDF_EXECUTABLE` as one separately validated absolute qpdf path. The value is
constructed from launcher configuration, never inherited or PATH-discovered, and its bounded
non-reparse executable/DLL/resource closure is included in the same exact-object grant and recovery
inventory. The worker still invokes qpdf with a fixed argument vector and no shell.

### Create suspended, contain, then resume

The trusted launcher uses an absolute approved base Python executable, an absolute approved
bootstrap script, an approved working directory, and this fixed argument vector:

```text
<python.exe> -I -S -B -X utf8 <host_bootstrap.py>
```

It uses `CreateProcessW` directly with no shell. The worker is created suspended and without a
window. Before its first instruction can run, the broker assigns it to a newly configured Job
Object. The job applies per-process and whole-job memory limits, process and job CPU-time limits,
an active-process limit, and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The launcher does not enable a
breakaway policy. It retains the process and job handles so timeout, fault, forced termination, and
cleanup address the complete descendant tree rather than only the first Python process.

PWS-0111 preserves the product's active-process limit of one by launching the base interpreter
directly. It does not execute the virtual-environment `python.exe`, because that launcher would
need to create a second process before the Job could run the host. The consumed startup input gives
the one base-interpreter process the approved virtual-environment identity without weakening the
Job limit. Access preflight and post-grant verification include both the base executable and the
optional launcher file.

Only after job assignment succeeds does the broker provision the one-shot grant and resume the
primary thread. A failure before resume removes the profile/grants only after the broker proves the
partial process tree terminated. Missing or unconfirmed process authority is retained in a
fail-closed quarantine instead of revoking resources from a process that might still exist.

`WindowsProcessLauncher` is the sole process-creation broker in the GUI process. A bounded lock
covers the complete interval from creating a temporarily inheritable pipe end until every broker
copy is closed or made non-inheritable. All future GUI-side process creation must enter this broker;
a source guard rejects direct subprocess/multiprocessing entry points, common Python spawn aliases,
and Windows CreateProcess/ShellExecute/WinExec families outside the one approved call.
Partial-construction or release failures retain handle/profile authority in quarantine, set a
stable launch-cleanup fault, and make concurrent or later launch attempts reject after a bounded
wait. Job limit values are checked for the Windows ABI before any multiplication or structure
assignment.

### Inherit exactly three anonymous-pipe handles

`PROC_THREAD_ATTRIBUTE_HANDLE_LIST` restricts inheritance to exactly three worker endpoints:

| Standard slot | Worker authority | Broker authority |
|---|---|---|
| standard input | read framed client-to-worker RPC | write framed RPC |
| standard output | write framed worker-to-client RPC | read framed RPC |
| standard error | read one fixed bootstrap grant | write one fixed bootstrap grant |

The broker ends are explicitly non-inheritable. The third slot is not a general diagnostic or raw
stderr channel; routine errors cross only as registered protocol/client codes. No endpoint name,
handle value, session capability, case path, or document value appears in the command line or
environment.

Windows requires each initial source handle in the explicit list to carry `HANDLE_FLAG_INHERIT`.
The inherited child copies would otherwise remain eligible for re-inheritance. Before protocol
negotiation or any descendant launch, the worker clears that flag on stdin, stdout, and stderr and
re-queries all three handles. Failure is a stable isolation fault. A parser/tool descendant
launched with inheritance enabled therefore cannot retain or race the RPC and one-shot bootstrap
channels.

The bootstrap grant is exactly 92 bytes and is read before isolation negotiation:

| Field | Bytes | Encoding |
|---|---:|---|
| magic | 8 | fixed `PWSWRK1` identifier plus terminator |
| bootstrap version | 4 | unsigned big-endian integer |
| session ID | 16 | UUID bytes |
| worker-instance ID | 16 | UUID bytes |
| case ID | 16 | UUID bytes |
| session capability | 32 | raw 256-bit per-launch secret |

Size, magic, version, UUID version, and capability are validated before the host constructs its
protocol peer. The capability and bootstrap bytes are redacted from representations and are never
logged. Every restart generates a new grant; a previous grant cannot resume a conversation.

### Keep RPC bounded and state-owned

RPC uses anonymous pipes with a four-byte unsigned big-endian length followed by one payload of 1
through 65,536 bytes. Zero, oversized, truncated, stalled, or nonconforming frames fail before
message dispatch. The lifecycle coordinator only adds validated payloads to a bounded outbound
queue; a private writer performs blocking I/O so a peer that stops reading cannot pin the
coordinator before a deadline is armed. Protocol v1 then performs strict decoding,
capability/session/worker/case binding,
sequence/replay checks, operation-registry checks, and state transition validation.

One coordinator actor owns each side's `ProtocolPeer` and mutable lifecycle. Reader, operation, and
watchdog work can report bounded events, but they do not mutate conversation state directly. A
protocol violation permits at most one sanitized `protocol.error`, faults the generation, and
requires termination/restart.

The GUI-safe client exposes cached, immutable state and never puts paths, the session secret, raw
payloads, a command line, environment values, or raw process errors in its snapshot. Its lifecycle
uses monotonic handshake, operation, cancellation-grace, and shutdown-grace deadlines. A valid
terminal message processed before an explicit deadline event wins the race, including at the same
observed monotonic timestamp. Once a deadline event wins, the job is terminated and later events
from that generation are stale. A cancellation acknowledgement records intent only; the terminal
result remains authoritative and may still be successful if completion won the race.

The host records a handler's terminal result and monotonic occurrence time in a separate one-slot
mailbox before entering the bounded mixed event queue. A completion at or before the operation
deadline therefore wins older queued cancel/progress traffic; a completion recorded later yields
the restart-required timeout. Queue scheduling cannot rewrite that terminal ordering.

The client is GUI-process-side, not GUI-event-thread code. It runs on one dedicated lifecycle
coordinator because OS-confirmed process-tree shutdown, profile deletion, and cross-process ACL
serialization are synchronous and may take materially longer than an event-loop callback. The UI
receives immutable snapshots through application message delivery; it never invokes launch,
termination, or cleanup directly.

Restart never replays an operation. It disposes the old transport/process/job/profile and creates a
fresh generation, session ID, worker-instance ID, capability, pipe set, peer state, and sequence
space. An unexpected EOF or process exit before a validated `session.closed` becomes a stable
client failure; it does not terminate the desktop process or expose the raw exit code.

If any termination or cleanup step cannot prove closure, that uncertainty is sticky for the
client's lifetime: no retry, restart, or second generation is permitted. Retained job authority
still provides kill-on-close protection at application exit, and the failure remains a stable
maintenance condition rather than being converted into a clean stop by a later idempotent call.
Preallocated or adopted Job, process, pipe, mutex, ACL, and profile cleanup authorities are retained
until the corresponding Windows release succeeds. Once the job reports no active processes, profile
and grant cleanup proceeds even if an exited process/job handle itself needs quarantine. Bounded
retries can drain quarantine, but they cannot retroactively report the earlier lifecycle as clean.

### Broker case objects by handle, never by path

Case binding in the bootstrap and RPC identifies the active logical case but grants no filesystem
access. The PWS-0107 `protocol.noop` operation has no inputs, outputs, or document handles. Its
AppContainer profile storage is intentionally writable, and only the authenticated exact objects
in the approved runtime/code/fixed-tool inventory receive direct read/execute grants; no broader
filesystem-authority claim follows from those facts.

PWS-0105 is the first narrow fulfillment of this rule. The trusted storage broker registers an
already sealed and independently verified pending PDF under its complete path-free summary. A
borrow uses `ReOpenFile` on the retained authenticated object, rechecks identity/size/type/link/
reparse/sparse/alternate-stream state, and obtains an independent read pointer without a lexical
path lookup. `DuplicateHandle` transfers only `FILE_READ_DATA`, `FILE_READ_ATTRIBUTES`, and
`SYNCHRONIZE` into the ready worker with inheritance disabled.

The operation also creates a new anonymous result pipe and transfers only
`FILE_WRITE_DATA | SYNCHRONIZE` for its worker end. The broker keeps the read end, closes its local
writer after duplication, and never grants the AppContainer SID a case-directory ACL. The closed
`pdf.inspect` RPC codec binds the two distinct worker-local handle values to one exact
`source_read` object reference, expected size/digest, operation UUID, and resource limits. No case
path appears in RPC, arguments, environment, or result.

The worker copies and rehashes the exact source into bounded AppContainer-private temporary
storage, then closes the case handle before native parsing. It writes one capped frame correlated
to operation and source IDs and closes the result writer before sending RPC success. The broker
terminates and confirms the one-shot job tree before releasing its source/pipe authorities, reads
through EOF, and validates length, version, correlations, and closed typed values. It does not
remotely close a target-process handle number because Windows may reuse that number for another
object; whole-tree termination is the revocation boundary.

This extension proves read access only to one pending PDF plus write access only to one result
pipe. It grants no directory, committed-original, manifest, journal, cache, staging, export, or
destination authority. Future document operations must repeat the trusted resolution,
authentication, least-privilege transfer, correlation, termination, and denial evidence for their
own object types; they cannot generalize PWS-0105's handle parameters into arbitrary authority.

### Keep PWS-0108 thumbnail output volatile until worker revocation

PWS-0108 selects an unnamed pagefile-backed Windows file-mapping object as the one-page thumbnail
output primitive. The trusted broker calls `CreateFileMappingW` with `INVALID_HANDLE_VALUE`,
`PAGE_READWRITE`, no name, and the exact logical header-plus-requested-pixels limit. It retains one
read view and duplicates only `SECTION_MAP_WRITE` into the ready worker with inheritance disabled.
The released RPC parameter remains named `cache_handle`, but it carries no cache-file, path,
directory, synchronization, query, delete, rename, or publication authority.

`FILE_MAP_WRITE` produces a read/write view under the Windows API. That does not expose another
object: a new paging-file mapping starts zeroed and contains only bytes this operation writes. The
production adapter maps the exact logical output limit and bounds every copy against it. Windows
may round the volatile section backing to the containing memory page; live tests prove a view past
that page-rounded bound fails, while the broker refuses to read even one byte past the logical
limit. The residual slack is less than one page and can never extend a file or consume persistent
cache storage.

The worker writes the fixed artifact, flushes and unmaps its view, closes the section handle, and
reports only exact size, SHA-256, operation/cache-object correlation, and content kind over RPC.
The broker treats that claim and the shared bytes as untrusted. It first confirms complete Job-tree
termination, then reads only the claimed byte count, verifies the whole-artifact digest and every
header/key/pixel correlation, and only then calls the trusted storage stage's one-shot bounded
writer. Storage seals, reads back, validates again, and alone performs no-replace publication.

The persistent random stage is quota-reserved before launch but never opened to the worker. A
cancelled, failed, malformed, or unconfirmed worker therefore cannot retain a persistent writer;
the empty broker-only stage is safely disposable. Quarantine remains only for an indeterminate
persistent publication operation. This preserves the PWS-0107 sticky cleanup-failure rule without
turning uncertain worker termination into authority over a cache file.

### Require real containment evidence

Task completion requires live Windows evidence, not launcher flags or this ADR alone:

- product-like virtual-environment startup through the direct base interpreter, with the fixed
  startup input consumed before audit and the active-process limit still set to one;
- fail-closed rejection of a hardlinked approved-runtime file before any ACL grant;
- fail-closed blocking of an after-scan hardlink replacement, an ancestor-junction pivot, and alias
  removal before exact-handle ACL cleanup;
- fail-closed detection and complete exact-handle cleanup when an outward hardlink wins the narrow
  pre-guard window, with no AppContainer ACE applied;
- a real Python host negotiates inside a zero-capability AppContainer;
- independent IPv4 and IPv6 TCP and UDP probes from the worker and a descendant cannot reach a
  controlled listener that a non-isolated control process can reach;
- closing/terminating the retained job ends the worker and every descendant without breakaway;
- the worker observes only the allowlisted constructed environment;
- the inherited handle inventory contains exactly the two RPC endpoints and bootstrap endpoint,
  all child copies are made non-inheritable before negotiation, and an inheritance-permitting
  descendant receives none of them; live tests compare kernel-object identity rather than raw
  process-local `HANDLE` numbers, which Windows may reuse for unrelated objects;
- path opens for another case, case-authoritative records, external source/destination, and user
  temporary locations fail;
- runtime/code access grants and the per-launch profile are removed only after the job tree exits;
- a real helper owner is forcibly terminated while retaining the mutex at durable `PREPARED`,
  partial-grant, `ACTIVE`, and `REVOKING` cutpoints; separate-process recovery observes
  `WAIT_ABANDONED`, repairs the exact-object grant before a new profile/grant, and verifies SID
  absence, guard baseline, and auto-inheritance before journal deletion;
- overlapping live v2 ledgers share one authenticated hardlink guard, interrupted cleanup retains
  it while another `ACTIVE` lease covers the object, and final cleanup restores the recorded
  pre-PaperWorkShell baseline; and
- legacy v1 recovery remains bounded, while insecure, hardlinked, corrupt, unknown-version,
  peer-SID/allowlist-mismatched, conflicting-baseline, or identity-changed state blocks launch
  without widening authority.

Successful case-object handle brokerage is not PWS-0107 evidence because `protocol.noop` receives no
such handle. PWS-0105 is the first consuming operation and separately proves that only its
deliberately brokered source and result handles work. Its source-rights tests prove writes are
denied; the unchanged PWS-0107 live containment evidence continues to prove path-based case access
is denied. PWS-0108 separately proves that its `SECTION_MAP_WRITE` duplicate maps in the
AppContainer, file APIs and a separate read-only mapping right fail, the page-rounded section stays
bounded, and persistent cache bytes appear only after worker-tree termination and trusted
validation.

A skipped or simulated required probe is not passing evidence. Failure of any required property
leaves PWS-0107 or its consuming milestone gate open.

## Alternatives considered

### Ordinary subprocess plus protocol validation

Rejected. Protocol authentication does not remove the child token's ambient filesystem/network
authority or contain compromised descendants.

### Job Object without AppContainer

Rejected as the complete boundary. A job supplies resource and tree-lifecycle controls but does not
remove network or ordinary user filesystem access.

### Firewall rule, socket monkeypatch, or proxy environment

Rejected. Per-executable firewall changes can require administration and affect unrelated Python
processes; library hooks and proxy variables are bypassable by compromised native code. The
supported boundary uses the AppContainer token's lack of network capabilities.

### Broad AppContainer access to the active case directory

Rejected. It would weaken the exact private case DACL and expose unrelated originals,
authoritative manifests/journals, or other operation state after a parser compromise. Exact opened
handles are the required authority unit.

### Put grant data or case paths in arguments/environment

Rejected. Process inspection, inherited state, crash reporting, and command construction would
create unnecessary disclosure and injection channels. A fixed-size inherited pipe provisions only
the launch binding; document authority remains handle-only.

### Give the thumbnail worker a persistent cache-stage file handle

Rejected. Post-termination size and quota checks do not prevent a compromised live worker from
extending the file, consuming disk, or retaining a writer after cleanup becomes uncertain. A
broker-only persistent stage populated only after revocation removes that authority.

### Carry the complete thumbnail through the anonymous result pipe

Rejected for version 1. The existing result pipe is intentionally small. A multi-megabyte raster
can fill a bounded pipe before terminal worker-tree shutdown, forcing the broker either to read
untrusted content concurrently or risk deadlock. The fixed-size pagefile section permits bounded
production followed by revocation-before-read without a path or persistent file.

### Use a named mapping or worker-private temporary output path

Rejected. A named kernel object adds namespace and ACL discovery risk, while a path requires
filesystem authorization and leaves cleanup tied to worker-controlled persistent state. The
selected section is unnamed, handle-only, volatile, and bounded by its page-rounded allocation.

### Experimental process-sandbox API

Rejected for the stable implementation. An experimental API with an unpublished/unstable policy
format and incompatible inherited-handle behavior cannot support a reviewable release boundary.
Classic documented AppContainer, process-attribute, and Job Object APIs provide the required
controls on the supported Windows target.

## Consequences and residual risks

- The production worker launcher is Windows-specific and uses a narrow standard-library `ctypes`
  adapter. Pure protocol, host, and client state remain testable with injected adapters.
- Starting source-tree Python requires temporary direct read/execute ACL entries on the exact
  objects enumerated beneath approved runtime/code/fixed-tool roots. Their scope and revocation are
  security evidence. Runtime files must be independent objects rather than hardlinks; the
  development environment uses uv copy mode, and packaged builds should narrow this further to
  app-owned immutable runtime resources.
- Persistent ACL recovery necessarily stores approved runtime/code paths and object identities in
  a protected current-user/LocalSystem launcher journal. That path-private security state is not a
  case artifact, routine log, or diagnostic bundle input. If it cannot be authenticated, launch
  fails closed rather than bypassing recovery.
- The AppContainer profile has intentionally writable ephemeral storage needed by Python. It is not
  a guarantee of a write-free process, secure erasure, or crash-artifact absence.
- The PWS-0108 section is volatile and page-rounded. The adapter and broker enforce the exact
  logical byte limit; the remaining sub-page slack is a bounded memory cost, not persistent cache
  authority or byte-for-byte secure erasure.
- Zero network capability is an OS policy claim whose supported behavior must be rechecked on the
  packaged Windows build and representative descendants. It is not inferred solely from a Boolean
  launch record.
- Job limits bound memory, CPU time, and process count. Operation-specific wall time, page/pixel,
  temporary-storage, and output limits remain independently enforced by registered handlers and
  brokers.
- AppContainer and private case ACLs do not protect against Windows kernel compromise,
  administrators, SYSTEM, or unrestricted malware in the launching account.
- PWS-0107 itself added no document parser, renderer, OCR engine, UI, case mutation, or publish
  operation. The PWS-0105 extension adds only worker-side structural inspection of one prepared PDF
  and cannot publish case state. PWS-0108 adds bounded one-page rendering, but trusted storage alone
  publishes its disposable cache. PWS-0112 adds exact source/plan/candidate/report authorities for
  one verified export, but only the trusted desktop adapter can publish to an external destination.
  Later adapters must preserve this boundary and add their own handle-broker contract tests.
- Stable plain-language client failures support later accessible announcements and recovery
  actions. PWS-0111 still owns the UI focus and announcement behavior.

## Implementation references

- [PDF structural-inspection contract](../architecture/pdf-structural-inspection.md)
- [Bounded PDF-thumbnail service contract](../architecture/pdf-thumbnails.md)
- [Deterministic verified PDF-export contract](../architecture/pdf-export.md)
- [Startup recovery and sanitized diagnostics](../architecture/startup-recovery-and-diagnostics.md)
- [Microsoft: Implementing an AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
- [Microsoft: CreateFileMappingW](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-createfilemappingw)
- [Microsoft: MapViewOfFile](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-mapviewoffile)
- [Microsoft: File-mapping security and access rights](https://learn.microsoft.com/en-us/windows/win32/memory/file-mapping-security-and-access-rights)
- [Microsoft: Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [Microsoft: Creating Processes](https://learn.microsoft.com/en-us/windows/win32/procthread/creating-processes)
- [Microsoft: UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)
