# Restricted worker lifecycle contract

- Status: Implemented PWS-0107 contract with PWS-0105 inspection, PWS-0108 thumbnail, PWS-0112 export, and PWS-0113 ACL-recovery extensions
- Tasks: PWS-0107, PWS-0105, PWS-0108, PWS-0112, PWS-0113
- Decisions: PWS-D012, PWS-D013, PWS-D027, PWS-D028, PWS-D036, PWS-D038
- ADR: [ADR 0004](../adr/0004-windows-worker-isolation.md)
- Protocol: [Worker RPC protocol v1](../schemas/worker-rpc-v1.md)
- Startup recovery: [Startup recovery and sanitized diagnostics](startup-recovery-and-diagnostics.md)

## Scope

PWS-0107 implements the separate Windows document-worker host, GUI-safe client/broker, bounded
anonymous-pipe transport, operation runtime, cancellation/deadline lifecycle, forced tree
termination, and restart behavior. The Windows launcher uses a zero-capability AppContainer and a
retained Job Object. Core protocol, client, launch, and lifecycle components remain
standard-library-only and are injected behind process/transport interfaces for deterministic
tests. After bootstrap isolation succeeds, host composition may load the admitted worker-side PDF
adapter from the exact staged runtime; third-party types still do not enter the core contracts.

The registry now contains `protocol.noop` version 1, `pdf.inspect` version 1, `pdf.thumbnail`
version 1, and `pdf.export` version 1. The no-op still proves process/protocol lifecycle without
document authority. PWS-0105 proves exact prepared-source read-handle transfer, a write-only
bounded result pipe, worker-private copy verification, pikepdf/libqpdf structural inspection, and
one-shot worker revocation. PWS-0108 adds one committed-source read handle and one fixed-size
writable volatile output section for a bounded one-page render. PWS-0112 adds a manifest-bound
batch of committed-source read handles, one read-only plan section, one bounded writable candidate
section, and one write-only report pipe for deterministic assembly and verification. No PDF
operation grants a case/directory path, persistent cache or destination writer, or general
filesystem access. See
the [PDF structural-inspection contract](pdf-structural-inspection.md) and
[bounded PDF-thumbnail service contract](pdf-thumbnails.md), plus the
[deterministic verified PDF-export contract](pdf-export.md).

## Component ownership

| Component | Responsibility | Trust rule |
|---|---|---|
| `worker.client.client` | Dedicated coordinator-thread lifecycle, local snapshots, deadlines, cancellation, shutdown, fault and restart | GUI-process-side; no host/platform/document-adapter import |
| `worker.client.process` | Typed launcher/process/transport contracts and bounded framed transport | Hides payloads, paths, commands, and environment from representations |
| `worker.client._windows` | AppContainer profile, ACL grant lease, pipes, suspended process, Job Object, termination and cleanup | Windows-only private launcher; no UI dependency |
| `worker.client._acl_recovery` | Canonical bounded `PREPARED`/`ACTIVE`/`REVOKING` ACL ledger and atomic private journal | Path-private Windows-launcher state; no case, protocol, UI, or routine-diagnostic surface |
| `worker.protocol.bootstrap` | Exact 92-byte launch grant | Launch-only; secret redacted and never extensibly decoded |
| `worker.protocol.framing` | Four-byte length framing and sanitized local transport errors | Returns bytes only; protocol decode always follows |
| `worker.protocol.peer` | Outbound validation/encoding and inbound decode/authentication/state transition | Sole conversation owner on each side |
| `worker.host` / `worker.runtime` | Isolation self-audit, negotiation, dispatch, progress, cancellation, timeout and close | No UI/application dependency; severe faults end the generation |
| `worker.operations` | Immutable exact operation-handler registry | Only registered descriptors dispatch; raw handler exceptions are mapped |
| `worker.pdf_source_inspector` | One-shot GUI-process orchestration, handle transfer, cancellation/deadline checks, termination, and result validation | Structural broker protocols only; storage and worker implementations are injected |
| `worker.pdf_operation` | Adopt one brokered source, verify/copy/hash it, invoke the PDF adapter, and write one correlated result | Host-side only; closes the case handle before native parsing |
| `worker.pdf_thumbnail_broker` | Resolve one committed page, authenticate cache hits, broker exact source/output-section handles, terminate one-shot generation, validate volatile output, and persist/publish through storage | Structural ports only; never treats a key, UUID, or path as authority |
| `worker.pdf_thumbnail_operation` | Adopt one source and fixed-size output-section handle, enforce its exact logical limit, verify/copy/hash source, render one page, and write one fixed correlated artifact | Host-side only; receives no persistent cache-file or directory capability |
| `worker.pdf_export_broker` | Authenticate one manifest-bound source batch, broker exact plan/output/report handles, terminate the one-shot generation, revalidate sources, and expose only the bounded verified candidate | Structural ports only; never receives or publishes a destination path |
| `worker.pdf_export_operation` | Adopt exact source/plan/output/report handles, copy and rehash sources in one private operation directory, assemble and verify, and write one correlated report | Host-side only; receives no case, manifest, history, or destination authority |
| `worker._windows_broker` | Non-inheritable bounded anonymous result pipe and fixed-size pagefile-backed output section | GUI-process-side private primitives; no path or document value |
| `storage.prepared_source_registry` | Register/revoke one sealed pending source and lend an exact independently positioned read handle | Storage owns source identity and publication; no worker-host import |
| `storage.source_ingest` / `storage.thumbnail_cache` | Lend one manifest-bound committed PDF handle; authenticate, stage, validate, publish, invalidate, and clean disposable thumbnails | Storage owns handle identity and cache publication; no worker implementation import |
| `pdf.pikepdf_adapter` / `pdf.qpdf_checker` | Typed structural inspection and fixed-command qpdf test seam | Third-party objects/output remain inside the PDF adapter boundary |
| `pdf.export_adapter` | Assemble, strictly reopen, qpdf-check, and PDFium-compare one worker-private candidate | Third-party objects, native output, and paths remain inside the restricted worker |

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
2. Under the protected ACL mutex, authenticate the private recovery journal and repair every dead
   or interrupted runtime/code grant whose exact recorded resources remain inside the current
   approved access-root allowlist. Any invalid or unprovable state blocks launch.
3. Create an ephemeral AppContainer profile with zero capability SIDs.
4. Grant its SID read/execute access to only the approved runtime and code roots.
5. Construct an environment containing only the approved Windows/Python names. Construct
   `LOCALAPPDATA`, `TEMP`, and `TMP` beneath that per-launch profile rather than inheriting them.
6. Create three anonymous pipes and make only the worker ends inheritable.
7. Build an explicit process-attribute handle list and zero-capability security descriptor.
8. Create the fixed process suspended, with no window and no shell.
9. Configure and assign the Job Object before resume.
10. Close broker duplicates of the worker-only pipe ends; write and close the exact bootstrap
   grant; resume the primary thread.
11. Before negotiation or descendant launch, clear and re-query `HANDLE_FLAG_INHERIT` on all
    three worker endpoints.
12. Retain the process, job, profile, and broker pipe ends for the full generation.

Runtime/code ACL changes are SID-specific and serialized across sessions by a protected Global
mutex. Its owner is the current user, its DACL grants only that user and LocalSystem, its handle is
non-inheritable, and an existing object's owner/DACL must match exactly. The broker preflights the
complete bounded resource tree before mutation, rejects an absent or NULL DACL, and removes only
the launch SID's entries during cleanup. Overlapping live profiles therefore retain one another's
access; cleanup proves the temporary SID absent and restores each observed object's original
auto-inheritance state without replaying a stale whole descriptor.

PWS-0113 makes interruption recovery durable. Beneath the trusted Windows LocalAppData known
folder, the launcher creates or authenticates one non-reparse recovery directory whose protected
DACL grants full inherited access only to the current user and LocalSystem. Each profile receives
one bounded canonical journal record containing the profile ID/name, exact AppContainer SID,
owner PID plus process-creation time, fixed approved grant roots, every resource's absolute trusted
path and filesystem identity/type, and its original auto-inheritance state. Those paths are needed
only to revoke an interrupted grant; they never enter RPC, routine diagnostics, UI results, or
representations.

The journal publishes `PREPARED` before the first SID ACL mutation, `ACTIVE` after the grant and
inheritance-state restoration are verified, and `REVOKING` before cleanup begins. A new launch
inventories the journal before creating a profile. An `ACTIVE` entry whose exact owner process is
still live is retained; a dead owner or interrupted phase is repaired only after the current
configuration allowlist and every recorded object identity are revalidated under the mutex. Repair
removes that exact SID from the recorded roots, restores each object's recorded auto-inheritance
state, proves the SID absent across the inventory, and only then deletes the exact journal record.

Every grant/revoke mutation publishes its own `PREPARED` or `REVOKING` record first while holding
the mutex. On `WAIT_ABANDONED`, recovery inventories and repairs all validated dead/interrupted
entries while retaining mutex ownership; if it can repair none, launch blocks conservatively.
Windows does not expose the prior mutex owner, so this is an aggregate cutpoint invariant rather
than a claimed one-to-one abandonment/entry correlation: an abandonment before its record is
pre-mutation, while one after mutation necessarily leaves that record. An insecure/corrupt/unknown
entry, PID-identity uncertainty, allowlist mismatch, resource identity change, incomplete SID
removal, or journal cleanup failure blocks the new grant and launch. Abandonment is never converted
into permission to remove an unrecorded ACE or restore an entire stale DACL. Numeric Job Object and
recovery limits are range-checked before conversion to Windows ABI values.

At launch the child receives exactly three handles: standard input for client-to-worker RPC,
standard output for worker-to-client RPC, and standard error as a one-shot bootstrap reader. It
receives no inherited case, source, cache, manifest, journal, destination, diagnostic, or logging
handle. After ready, a registered operation may receive only its explicit reduced-right handles via
`DuplicateHandle`; those are not inherited launch authority and do not change the exact-three
launch claim.

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

For `pdf.inspect`, RPC success additionally means that the worker has written and closed one
bounded out-of-band result frame. It does not make the frame trusted. The GUI-side broker must
terminate and confirm the one-shot job tree, read through pipe EOF, validate frame size/version,
operation/source correlations and closed typed values, and only then expose the result. Any
operation or frame failure leaves the prepared source non-authoritative.

For `pdf.thumbnail`, RPC success additionally binds one `operation_staging` output reference to the
requested operation/cache-object IDs, content kind, exact artifact byte count, whole-artifact
SHA-256, and output limit. It still does not make the volatile output trusted. The GUI-side broker
must terminate and confirm the one-shot job tree before it reads the bounded section, validates the
reference plus fixed header/key/pixel contract, and writes the broker-only persistent stage for
no-replace cache publication. If tree termination is unconfirmed, no persistent writer exists and
the empty stage is safely discarded without parsing or publication.

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
The concrete Windows adapter stops and confirms the live Job tree before joining transport-close
threads, so a worker blocked in anonymous-pipe I/O cannot exhaust the bounded close deadline.

After a whole desktop-process interruption, a later launcher instance may repair only the durable
runtime/code ACL lease described above. That repair restores host resource access policy; it does
not recover a process, reconnect a pipe, accept an old result, or replay the interrupted document
operation. Case manifest/export/temp settlement remains the separate PWS-0113 startup-recovery
boundary.

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
`protocol.noop` remains handle-free.

PWS-0105 proves the first deliberately brokered access for one sealed, non-authoritative pending
PDF. Storage authenticates the complete prepared summary and retained object, obtains an
independently positioned read handle with `ReOpenFile`, and rechecks identity, size, regular-file,
single-link, reparse/sparse, and alternate-stream state. The Windows client duplicates only
read-data/read-attributes/synchronize rights into the worker, with inheritance disabled. A second
anonymous-pipe handle carries only write-data/synchronize rights for the result. The source and
result handle values are distinct, positive, worker-local RPC parameters for this one registered
operation; they are not paths or a general handle API.

The worker verifies the source handle, copies exactly the expected bytes while recomputing SHA-256
into bounded AppContainer-private temporary storage, and closes the case handle before invoking
pikepdf. It writes one length-prefixed result containing the operation UUID, prepared-source UUID,
and closed JSON report, then closes the result writer. The broker terminates the one-shot job tree
before releasing either local authority and validates the frame only after EOF. It never remotely
closes a target-process handle number because Windows can reuse that number for another object.

PWS-0108 adds a second narrow profile for one current committed PDF page and one fixed-size
volatile output section. Storage authenticates the current manifest mapping, exact immutable-source
identity, size, SHA-256, containment, and retained lifetime before lookup or generation. On
generation it reserves the worst-case artifact capacity and creates an exact regular broker-only
stage beneath `cache/thumbnails/`; the broker separately creates an unnamed pagefile-backed section
from the exact logical operation output limit. Windows may page-round the volatile backing by less
than one page, while the adapter enforces the exact logical cap. The Windows client duplicates only
read-data/read-attributes/synchronize for the source and section map-write for the output.
The worker receives no cache-file or directory handle and cannot read, extend, rename, delete, or
publish persistent cache state.

The worker copies and rehashes the exact source into AppContainer-private temporary storage,
closes the case-source handle, renders one page, writes one fixed-layout header plus raw RGBA8
payload to the bounded section, flushes/unmaps it, and closes its handle. Only after confirmed
whole-tree termination may the trusted broker read and validate the section, write the persistent
stage, validate it again, and atomically publish it. Unconfirmed termination leaves the persistent
stage empty; only indeterminate publication quarantines it. The complete rules are in the
[bounded PDF-thumbnail service contract](pdf-thumbnails.md).

These are operation-specific handle claims only. The worker still has no general access to an
active case, originals directory, cache directory, manifest, journal, export history, external
source, or destination. A later operation that needs another object must preserve all of these
rules:

1. Resolve a logical `CaseObjectReference` only in the trusted storage broker.
2. Authenticate the already-opened handle's case, area, operation, identity, type, access, size,
   digest, containment, reparse/link/alias state, and retained lifetime.
3. Transfer only that exact least-privilege handle through a typed out-of-band broker channel.
4. Keep authoritative manifest/journal/source/export publication in the trusted broker.
5. Close/revoke every operation handle on result, cancellation, timeout, crash, and restart.

No later task may substitute a case path, general directory capability, or broad AppContainer ACL.

### PWS-0112 verified PDF-export grant

`pdf.export` is another one-shot consuming operation. The broker authenticates a manifest-bound
batch of retained committed-source handles and transfers only source read-data/read-attributes/
synchronize handles, one read-only bounded plan section, one write-only bounded candidate section,
and one write-only bounded report pipe. The worker receives neither case nor destination paths.

The worker copies and rehashes each source into generation-private temporary storage and closes all
case-source handles before invoking pikepdf, qpdf, or PDFium. It assembles and verifies the private
candidate, copies only the exact verified byte count into the output section, and writes one closed
correlated report. After confirmed whole-tree termination, the broker revalidates every retained
source before it reads either report or output. Only then can the trusted desktop destination
adapter stream the candidate into an authenticated, no-replace external commit.

The standalone qpdf executable is a fixed launcher resource, never a PATH-discovered command. An
explicit packaged/test executable adds only its exact absolute path to the constructed worker
environment. The launcher proves AppContainer read/execute access across the bounded non-reparse
qpdf directory closure, including adjacent DLLs/resources, without weakening the existing runtime
and code-root grant lifecycle. The worker still uses a fixed argument vector and no shell. See the
[deterministic verified PDF-export contract](pdf-export.md).

## Required verification

PWS-0107 is complete only with real supported-Windows evidence for:

- fixed executable, arguments, working directory, no shell, and constructed minimal environment;
- exact three-handle inheritance, child-side de-inheritance, descendant non-propagation verified by
  kernel-object identity rather than reusable process-local `HANDLE` numbers, and the exact 92-byte
  one-shot grant;
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

PWS-0105 adds required supported-Windows evidence that the exact prepared PDF handle and result
writer work while a write through the source handle and path-based sibling/case access remain
denied; the result is correlated, bounded, and sanitized; cancellation/timeout/failure terminate
the one-shot tree before storage revokes the borrow. This evidence belongs to the consuming
operation and does not retroactively change PWS-0107's no-document completion claim.

PWS-0108 adds required supported-Windows evidence that one current committed PDF read handle and
one fixed-size section map-write handle work with their exact reduced rights while a separate
read-only mapping right, unbounded/oversized mapping, file I/O, directory, sibling, manifest,
source-write, cache-file access, and
path-based case access remain denied. The worker output must bind operation/source/page/cache IDs,
fixed layout, size, and digests; every success/failure/cancel/timeout path must confirm one-shot tree
termination before trusted section readback or persistent stage write. An unconfirmed tree must
leave no persistent writer and block another generation rather than manufacture cleanup evidence.

PWS-0113 adds required supported-Windows evidence for the persistent ACL-recovery boundary:

- a canonical `PREPARED` record is flushed before the first SID ACL mutation and phase updates are
  published atomically beneath the authenticated private LocalAppData recovery directory;
- a real helper process is forcibly terminated while retaining the protected mutex at `PREPARED`,
  partial-grant, `ACTIVE`, and `REVOKING` cutpoints; separate-process recovery observes
  `WAIT_ABANDONED`, authenticates the dead PID plus creation time, removes the exact SID, restores
  auto-inheritance, clears the journal only after complete verification, and permits a replacement
  grant;
- an insecure recovery-directory policy, corrupt/noncanonical record, unknown entry, current
  configuration allowlist mismatch, hardlinked/special resource, or resource identity mismatch
  blocks new ACL work without deleting evidence or widening authority; and
- the complete live worker/descendant containment suite still passes with pre-launch recovery in
  the production launcher path.

These tests prove cold-restart repair of recorded runtime/code AppContainer grants only. They do
not prove replay of worker operations, general ACL repair, case-workspace access, recovery from an
unrecorded mutation, or automatic cold deletion of an abandoned AppContainer profile and its local
temporary tree.

Live containment tests copy a bounded minimum CPython runtime, worker source, and fixed synthetic
probes into a temporary physical tree at the root of CPython's fixed drive before launch. The
fixed-drive location gives AppContainer descendants stable ancestor-traverse semantics even when a
hosted runner's per-user temporary hierarchy is private. This keeps developer and hosted-runner
junctions or interpreter aliases outside the approved resource tree without weakening production:
the copy rejects nested reparse points and special entries, omits `site-packages`, receives fresh
ACLs, and must pass the same unchanged resource validator used by the real launcher. A real canary
file remains present immediately outside the approved code roots so denial evidence cannot pass
only because the target is absent.

PWS-0105's live test extends that disposable staging root with only the pinned pikepdf, embedded
libqpdf, Pillow, lxml, and packaging runtime directories needed by the real operation. The isolated
bootstrap adds only the staged interpreter's exact `Lib/site-packages` directory to `sys.path`; it
does not enable ordinary `site` startup, `.pth` execution, user packages, or ambient dependency
discovery. The copied engine files remain inside the validated runtime tree and receive the same
temporary AppContainer runtime grant.

The PWS-0107 no-op must not fake handle evidence with a test-only production RPC operation. Tests
may inject fixed launch/operation adapters, but release claims require the real launcher and host.
Successful case-object handle brokerage was not a PWS-0107 completion criterion. PWS-0105 is the
first consuming operation and must prove that only its exact deliberately brokered handles work.
Any required skipped probe, leaked canary, surviving descendant, unexpected writable path, invalid
result correlation, or cleanup failure leaves the consuming task/gate open.

## Explicit deferrals

- PWS-0106: renderer operations behind a replaceable worker adapter.
- PWS-0109: authoritative manifest/journal persistence and recovery.
- PWS-0204: external OCR descendant/resource policy and cancellation integration.
