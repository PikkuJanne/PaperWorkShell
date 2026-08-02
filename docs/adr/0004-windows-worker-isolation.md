# ADR 0004: Restricted Windows worker isolation and launch lifecycle

- Status: Accepted
- Date: 2026-08-02
- Task: PWS-0107
- Decisions: PWS-D012, PWS-D013, PWS-D036, PWS-D038

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

The profile SID receives inherited read/execute access only to the approved Python runtime and
PaperWorkShell code roots needed to start the fixed host. No AppContainer access-control entry is
added to a cases root, an external source, a destination, or another user path.

The launcher preflights a bounded resource inventory before the first ACL mutation and rejects an
absent or NULL DACL. SID-specific grant/revoke operations use a protected Global mutex across
Windows sessions and do not restore a stale whole descriptor over another launch. The mutex owner
is the current user; its protected DACL grants only that user and LocalSystem; the handle is
non-inheritable; and an existing object's owner/DACL must match exactly. Concurrent profiles
therefore preserve one another's entries; after the final lease closes, the temporary profile SID
is absent and each observed object's original auto-inheritance state is restored without writing a
stale retained descriptor over concurrent entries.

`WAIT_ABANDONED` means a prior GUI may have died during an ACL mutation. The detecting process puts
the acquired mutex and ownership in a fail-closed in-process quarantine, does not intentionally
release them before exit, blocks later launches, and reports maintenance-required cleanup rather
than entering the critical section. PWS-0107 does not claim durable cold-restart orphan recovery;
PWS-0113 owns a persistent inventory/repair workflow.

Python startup requires writable profile and temporary locations. `LOCALAPPDATA`, `TEMP`, and
`TMP` are therefore constructed as paths inside the new per-launch AppContainer profile. They are
never copied from the launching user's environment and carry no case path. This is ephemeral
sandbox storage, not case workspace authority. The broker retains the profile until the exact job
tree has exited, then revokes the temporary runtime/code grants and deletes the profile. Cleanup
failure is a stable launch-cleanup failure rather than a silent success.

The environment allowlist is limited to `SYSTEMROOT`/`WINDIR`, `LOCALAPPDATA`, `TEMP`, `TMP`, and
the fixed Python encoding controls `PYTHONIOENCODING` and `PYTHONUTF8`. User `PATH`, `PATHEXT`,
`PYTHONPATH`, proxy variables, cloud credentials, command-processor variables, and other inherited
state do not cross the launch boundary.

### Create suspended, contain, then resume

The trusted launcher uses an absolute approved Python executable, an absolute approved bootstrap
script, an approved working directory, and this fixed argument vector:

```text
<python.exe> -I -S -B -X utf8 <host_bootstrap.py>
```

It uses `CreateProcessW` directly with no shell. The worker is created suspended and without a
window. Before its first instruction can run, the broker assigns it to a newly configured Job
Object. The job applies per-process and whole-job memory limits, process and job CPU-time limits,
an active-process limit, and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The launcher does not enable a
breakaway policy. It retains the process and job handles so timeout, fault, forced termination, and
cleanup address the complete descendant tree rather than only the first Python process.

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
access. The current `protocol.noop` operation has no inputs, outputs, or document handles. Its
AppContainer profile storage is intentionally writable and approved runtime/code roots receive
read/execute grants; no broader filesystem-authority claim follows from those facts.

Future document operations must resolve each logical `CaseObjectReference` in a trusted storage
broker, authenticate the already-opened object and requested access, and transfer only the exact
least-privilege handle out of band. They must not add a case path to RPC, arguments, or environment;
grant an AppContainer SID broad access to a case directory; or treat a lexical path as authority.
Until that handle broker and its denial tests exist, PaperWorkShell makes no claim that the worker
can read or write active-case files.

### Require real containment evidence

Task completion requires live Windows evidence, not launcher flags or this ADR alone:

- a real Python host negotiates inside a zero-capability AppContainer;
- independent IPv4 and IPv6 TCP and UDP probes from the worker and a descendant cannot reach a
  controlled listener that a non-isolated control process can reach;
- closing/terminating the retained job ends the worker and every descendant without breakaway;
- the worker observes only the allowlisted constructed environment;
- the inherited handle inventory contains exactly the two RPC endpoints and bootstrap endpoint,
  all child copies are made non-inheritable before negotiation, and an inheritance-permitting
  descendant receives none of them;
- path opens for another case, case-authoritative records, external source/destination, and user
  temporary locations fail; and
- runtime/code access grants and the per-launch profile are removed only after the job tree exits.

Successful case-object handle brokerage is not PWS-0107 evidence because `protocol.noop` receives no
such handle. The first later operation that introduces the broker must separately prove that only
its deliberately brokered handles work.

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

### Experimental process-sandbox API

Rejected for the stable implementation. An experimental API with an unpublished/unstable policy
format and incompatible inherited-handle behavior cannot support a reviewable release boundary.
Classic documented AppContainer, process-attribute, and Job Object APIs provide the required
controls on the supported Windows target.

## Consequences and residual risks

- The production worker launcher is Windows-specific and uses a narrow standard-library `ctypes`
  adapter. Pure protocol, host, and client state remain testable with injected adapters.
- Starting source-tree Python requires temporary read/execute ACL entries on approved runtime/code
  roots. Their exact scope and revocation are security evidence. Packaged builds should narrow this
  to app-owned immutable runtime resources.
- The AppContainer profile has intentionally writable ephemeral storage needed by Python. It is not
  a guarantee of a write-free process, secure erasure, or crash-artifact absence.
- Zero network capability is an OS policy claim whose supported behavior must be rechecked on the
  packaged Windows build and representative descendants. It is not inferred solely from a Boolean
  launch record.
- Job limits bound memory, CPU time, and process count. Operation-specific wall time, page/pixel,
  temporary-storage, and output limits remain independently enforced by registered handlers and
  brokers.
- AppContainer and private case ACLs do not protect against Windows kernel compromise,
  administrators, SYSTEM, or unrestricted malware in the launching account.
- This task adds no document parser, renderer, OCR engine, UI, case mutation, or publish operation.
  Later adapters must preserve this boundary and add handle-broker contract tests.
- Stable plain-language client failures support later accessible announcements and recovery
  actions. PWS-0111 still owns the UI focus and announcement behavior.

## Implementation references

- [Microsoft: Implementing an AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
- [Microsoft: Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [Microsoft: Creating Processes](https://learn.microsoft.com/en-us/windows/win32/procthread/creating-processes)
- [Microsoft: UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)
