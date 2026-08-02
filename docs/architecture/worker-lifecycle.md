# Restricted worker lifecycle contract

- Status: Implemented PWS-0107 contract
- Task: PWS-0107
- Decisions: PWS-D012, PWS-D013, PWS-D036, PWS-D038
- ADR: [ADR 0004](../adr/0004-windows-worker-isolation.md)
- Protocol: [Worker RPC protocol v1](../schemas/worker-rpc-v1.md)

## Scope

PWS-0107 implements the separate Windows document-worker host, GUI-safe client/broker, bounded
anonymous-pipe transport, operation runtime, cancellation/deadline lifecycle, forced tree
termination, and restart behavior. The Windows launcher uses a zero-capability AppContainer and a
retained Job Object. Protocol and lifecycle components remain standard-library-only and are
injected behind process/transport interfaces for deterministic tests.

The only registered operation is `protocol.noop` version 1. It reports numeric progress, responds
to cooperative cancellation, and produces no document output. It proves the process and protocol
boundary; it does not prove PDF/image parsing or case-file access.

## Component ownership

| Component | Responsibility | Trust rule |
|---|---|---|
| `worker.client.client` | Dedicated coordinator-thread lifecycle, local snapshots, deadlines, cancellation, shutdown, fault and restart | GUI-process-side; no host/platform/document-adapter import |
| `worker.client.process` | Typed launcher/process/transport contracts and bounded framed transport | Hides payloads, paths, commands, and environment from representations |
| `worker.client._windows` | AppContainer profile, ACL grant lease, pipes, suspended process, Job Object, termination and cleanup | Windows-only private launcher; no UI dependency |
| `worker.protocol.bootstrap` | Exact 92-byte launch grant | Launch-only; secret redacted and never extensibly decoded |
| `worker.protocol.framing` | Four-byte length framing and sanitized local transport errors | Returns bytes only; protocol decode always follows |
| `worker.protocol.peer` | Outbound validation/encoding and inbound decode/authentication/state transition | Sole conversation owner on each side |
| `worker.host` / `worker.runtime` | Isolation self-audit, negotiation, dispatch, progress, cancellation, timeout and close | No UI/application dependency; severe faults end the generation |
| `worker.operations` | Immutable exact operation-handler registry | Only registered descriptors dispatch; raw handler exceptions are mapped |

Package initializers remain passive. Application/UI code may import explicit `worker.client` and
`worker.protocol` modules but cannot import the host, Windows launcher implementation, operation
handlers, or future document adapters.

## Launch contract

### Trusted configuration

The composition root supplies absolute, local, approved paths for the Python executable,
bootstrap script, runtime roots, code roots, and working directory. The executable must be inside
an approved runtime root; the bootstrap must be inside an approved code root. Remote, device, and
relative locations fail before process creation.

The command is a fixed structured argument array, never a shell string:

```text
<approved-python> -I -S -B -X utf8 <approved-host-bootstrap>
```

No filename, template value, OCR/document text, case path, or RPC value can alter that array.

`WindowsProcessLauncher` is the sole process-creation broker in the GUI process. Every future
GUI-side process launch must use the same broker and creation lock. That lock spans creation of
the first temporarily inheritable pipe endpoint through confirmed closure or de-inheritance of
every worker-only broker copy. Acquisition is bounded, and a cleanup failure quarantines retained
handle authority and makes later launch attempts fail with a stable error. A source-architecture
test rejects direct subprocess/multiprocessing entry points, common Python spawn aliases, and the
Windows CreateProcess/ShellExecute/WinExec families outside this broker.

### Ordered Windows launch

One generation is created in this order:

1. Generate a fresh generation number, session UUIDv4, worker-instance UUIDv4, and 256-bit
   capability; bind them to one logical case UUIDv4.
2. Create an ephemeral AppContainer profile with zero capability SIDs.
3. Grant its SID read/execute access to only the approved runtime and code roots.
4. Construct an environment containing only the approved Windows/Python names. Construct
   `LOCALAPPDATA`, `TEMP`, and `TMP` beneath that per-launch profile rather than inheriting them.
5. Create three anonymous pipes and make only the worker ends inheritable.
6. Build an explicit process-attribute handle list and zero-capability security descriptor.
7. Create the fixed process suspended, with no window and no shell.
8. Configure and assign the Job Object before resume.
9. Close broker duplicates of the worker-only pipe ends; write and close the exact bootstrap
   grant; resume the primary thread.
10. Before negotiation or descendant launch, clear and re-query `HANDLE_FLAG_INHERIT` on all
    three worker endpoints.
11. Retain the process, job, profile, and broker pipe ends for the full generation.

Runtime/code ACL changes are SID-specific and serialized across sessions by a protected Global
mutex. Its owner is the current user, its DACL grants only that user and LocalSystem, its handle is
non-inheritable, and an existing object's owner/DACL must match exactly. An abandoned mutex retains
ownership in an in-process quarantine and is not intentionally released before exit because the
interrupted ACL state is unknown. The broker preflights the complete bounded resource tree before
mutation, rejects an absent or NULL DACL, and removes only the launch SID's entries during cleanup.
Overlapping profiles therefore retain one another's access; cleanup proves the temporary SID absent
and restores each observed object's original auto-inheritance state without replaying a stale whole
descriptor. Numeric Job Object limits are range-checked before conversion to the Windows ABI.

The child receives exactly three handles: standard input for client-to-worker RPC, standard output
for worker-to-client RPC, and standard error as a one-shot bootstrap reader. It receives no case,
source, cache, manifest, journal, destination, diagnostic, or logging handle.

### Bootstrap grant

The fixed grant is 92 bytes: an eight-byte magic, four-byte big-endian version, three 16-byte UUID
identities, and the raw 32-byte capability. The worker performs an exact blocking read, validates
the complete grant, and then audits its AppContainer token, zero capabilities, Job membership,
environment, and non-inheritable standard endpoints before it accepts `session.hello`.

A bootstrap failure exits with a stable numeric host code and emits no raw stderr. Standard error
cannot become a document-content side channel because it is occupied by the one-shot read pipe.

## Transport and protocol contract

Each RPC frame contains a four-byte unsigned big-endian payload length followed by exactly 1 to
65,536 bytes. The reader:

- returns clean EOF only between frames;
- accepts fragmented reads and preserves coalesced frame boundaries;
- rejects a zero or excessive declared size before a body read;
- rejects partial headers/bodies and nonconforming reads; and
- never includes payload bytes or a source exception in its stable error text.

The lifecycle coordinator validates and enqueues outbound bytes into a bounded queue; a private
writer thread performs the blocking pipe write and flush. A worker that stops reading therefore
cannot pin the coordinator before its lifecycle deadline is armed. The writer completes only
writes that make bounded forward progress and does not retry a zero/blocked write indefinitely.
Bounded inbound and outbound queues prevent an unconsumed peer from creating unbounded in-process
storage; a queue or writer failure faults the transport and generation.

Framing validity is not message validity. Every payload passes the strict protocol-v1 codec and
`ProtocolPeer` before dispatch. Binding covers capability, session, worker instance, case,
direction, sequence, message/request/operation IDs, negotiated capability, and conversation state.
An incompatible version, malformed message, replay, cross-generation value, or invalid transition
faults the session before operation work.

## Client lifecycle

`WorkerClient` is an actor owned by the thread that constructs it. Because Windows launch,
tree-termination confirmation, AppContainer cleanup, and serialized ACL mutation are synchronous,
the owner must be a dedicated lifecycle-coordinator thread in the GUI process, never the GUI event
thread. All mutating calls and accepted transport events run on that coordinator. UI/application
integration delivers immutable snapshots and commands across a message boundary; it does not call
the client directly from the event loop. PWS-0111 owns that accessible UI integration. Its phases
are:

| Phase | Meaning | Permitted next outcomes |
|---|---|---|
| `stopped` | No live generation authority | start a fresh generation |
| `starting` | Process launched; `session.ready` pending | ready, handshake fault, terminate |
| `ready` | Negotiated and idle | start one operation, graceful shutdown, terminate |
| `busy` | One operation started | progress, result, cancel, timeout/fault |
| `cancelling` | Cooperative cancel requested; result still pending | result, grace timeout/fault |
| `stopping` | Valid `session.shutdown` sent | validated `session.closed`, shutdown timeout/fault |
| `faulted` | Generation authority discarded; restart required or available | terminate or fresh restart |

The public lifecycle supports start, local query, event pumping, one operation start, cancellation,
graceful shutdown, forced termination, restart, process-exit notification, and explicit deadline
checks. `query()` returns cached immutable state only: it never polls the process, reads a pipe, or
contacts the worker. Snapshots may contain safe IDs, phase, PID, negotiated descriptors, numeric
progress, terminal outcome/error code, and one stable local failure. They never contain the
capability, RPC bytes, path, command, environment, filename, document value, or raw exception.

Each start has a bounded handshake deadline. Each operation has the requested wall deadline on
both broker and host. Cancellation and graceful shutdown have separate trusted grace periods.
Deadlines use monotonic time. `pump()` accepts at most one transport event before evaluating a
deadline, so ordering is explicit rather than a thread race.

## Operation, cancellation, and timeout rules

1. The client may start only an exactly negotiated/registered descriptor and only while idle.
2. The host sends `operation.accepted` before executing the registered handler. At most one
   operation is active.
3. Worker progress is numeric, registered, monotonic, bounded, and nonterminal.
4. `operation.cancel` sets a cooperative event. `operation.cancel_ack` says whether the request was
   first, repeated, or already terminal; it is never completion.
5. Exactly one valid `operation.result` is authoritative. A result may still be `succeeded` after a
   cancel request when completion won the race.
6. The host publishes one terminal record and its monotonic occurrence time before enqueueing it.
   A record at or before the host deadline wins older queued cancel/progress traffic; a later
   record times out. At the client, a valid terminal payload processed before an explicit deadline
   event wins, including at the same timestamp. Once the client deadline event wins, the job is
   terminated and later events cannot change terminal state.
7. Host operation timeout emits the closed `WORKER_OPERATION_TIMED_OUT` result with
   `restart_worker`, then exits. Broker operation, cancellation-grace, handshake, and shutdown
   timeout also terminate the complete job tree and fault the generation.
8. A handler exception becomes `WORKER_OPERATION_FAILED` with no raw exception or output. A severe
   protocol/worker fault requires restart.

Graceful shutdown during active work first requests cancellation, waits for the terminal result and
any pending acknowledgement correlation, then sends `session.shutdown`. Only the correlated,
validated `session.closed` yields a normal stopped state. Forced termination does not wait for
protocol close and always targets the retained Job Object.

## Crash and restart rules

Unexpected pipe EOF, process exit, malformed data, transport failure, isolation failure, or
restart-required result becomes a stable client failure. The desktop process remains alive, local
case state is not mutated, and raw exit status/stderr/exception values are not exposed.

Restart performs no retry and replays no operation. It first terminates and closes the old job tree,
transport, process handles, ACL grant lease, and AppContainer profile. It then creates a new
generation with fresh session/worker UUIDs, capability, pipes, profile, job, peer state, message
IDs, and sender sequences. Events tagged with any earlier generation are rejected as stale.

Handle, ACL, mutex, profile, and partial-launch cleanup retain authority until Windows confirms the
release. Bounded retries may physically release a resource, but an earlier unconfirmed lifecycle
remains sticky to the client. Unresolved authorities are quarantined, and another launch is
rejected rather than treating a later idempotent call as proof that the earlier generation was
clean. After the Job Object proves the process tree empty, profile/grant cleanup is independent of
whether an exited process or Job handle itself closes on the first bounded attempt.

## Stable failures

The GUI-safe client maps failures into a closed code/context pair and a trusted recovery action:

| Failure family | Examples | Recovery |
|---|---|---|
| caller/configuration | invalid state, wrong owner thread, stale generation, invalid policy | none; caller defect or ignored stale event |
| launch/isolation | launch failure, insufficient AppContainer/job/handle/environment evidence | retry only when safe; insufficient isolation does not run |
| transport/protocol | frame/pipe failure, malformed/incompatible/replayed message | terminate and restart worker |
| deadline | handshake, operation, cancellation-grace, or shutdown timeout | terminate and restart worker |
| process | unexpected exit or restart-required result | restart worker |
| termination/cleanup | job tree or launch resources could not be confirmed closed | block every later generation in that client; application restart/maintenance; never report clean stop |

Messages are selected from trusted code, not worker text. Host exits likewise use a closed numeric
registry for clean close, bootstrap/isolation failure, transport close, protocol/internal fault,
and operation timeout. Routine UI/logging must not append raw Windows, Python, handler, path,
environment, process, or payload values.

## Filesystem authority and current claim

Case binding is logical, not filesystem authority. No case path appears in the bootstrap, RPC,
command line, environment, or snapshot. The AppContainer SID is not added to case-workspace ACLs.

`protocol.noop` has empty inputs/parameters/outputs and receives no document handle. The current
worker therefore proves lifecycle and containment only. It does not yet claim access to an active
case, immutable original, cache/temp/staging object, manifest, journal, export history, external
source, or destination.

A later operation that needs an object must preserve all of these rules:

1. Resolve a logical `CaseObjectReference` only in the trusted storage broker.
2. Authenticate the already-opened handle's case, area, operation, identity, type, access, size,
   digest, containment, reparse/link/alias state, and retained lifetime.
3. Transfer only that exact least-privilege handle through a typed out-of-band broker channel.
4. Keep authoritative manifest/journal/source/export publication in the trusted broker.
5. Close/revoke every operation handle on result, cancellation, timeout, crash, and restart.

No later task may substitute a case path, general directory capability, or broad AppContainer ACL.

## Required verification

PWS-0107 is complete only with real supported-Windows evidence for:

- fixed executable, arguments, working directory, no shell, and constructed minimal environment;
- exact three-handle inheritance, child-side de-inheritance, descendant non-propagation, and the
  exact 92-byte one-shot grant;
- live host negotiation only after AppContainer/zero-capability/job/environment self-audit;
- frame boundaries, strict protocol validation, replay/cross-generation rejection, and bounded
  reader/write failure;
- successful no-op progress/result plus cancellation, timeout, malformed message, crash,
  graceful shutdown, forced job-tree termination, and fresh restart;
- IPv4/IPv6 TCP and UDP egress attempts from the worker and a descendant, with a reachable
  non-isolated control, proving no listener receives isolated traffic;
- descendant termination and no breakaway after fault/job close;
- environment canaries absent, profile bootstrap-storage writes succeeding, and tested
  case/application/user locations remaining denied;
- path-based attempts against cases, authoritative records, external locations, inherited user
  temp, and unrelated profile data denied.

The current no-op must not fake handle evidence with a test-only production RPC operation. Tests
may inject fixed launch/operation adapters, but release claims require the real launcher and host.
Successful case-object handle brokerage is not a PWS-0107 completion criterion; the first later
operation that introduces it must prove that only its exact deliberately brokered handles work.
Any required skipped probe, leaked canary, surviving descendant, unexpected writable path, or
cleanup failure leaves the task/gate open.

## Explicit deferrals

- PWS-0104: immutable source copy/hash publication and retained import lease consumption.
- PWS-0105/PWS-0106: PDF inspection and renderer operations behind worker adapters.
- PWS-0108: exact thumbnail cache handle brokerage, bounded output, and cache lifecycle.
- PWS-0109: authoritative manifest/journal persistence and recovery.
- PWS-0112: export staging handle brokerage, verification, and destination publication.
- PWS-0113: startup recovery, orphan cleanup, routine logging, and diagnostics baseline.
- PWS-0204: external OCR descendant/resource policy and cancellation integration.
