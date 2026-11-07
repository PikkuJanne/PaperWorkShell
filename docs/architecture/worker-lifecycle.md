# Restricted worker lifecycle contract

- Status: Implemented PWS-0107 contract with PWS-0105 inspection, PWS-0108 thumbnail, PWS-0111 desktop-composition, PWS-0112 export, PWS-0113 ACL-recovery, PWS-0201 image-intake, PWS-0202 scan-transform, PWS-0204 OCR, PWS-0206 searchable-export, PWS-0208 signed-source-admission, and PWS-0307 flattened-overlay-export extensions
- Tasks: PWS-0107, PWS-0105, PWS-0108, PWS-0111, PWS-0112, PWS-0113, PWS-0201, PWS-0202, PWS-0204, PWS-0206, PWS-0208, PWS-0307, PWS-0811
- Decisions: PWS-D007, PWS-D009, PWS-D011, PWS-D012, PWS-D013, PWS-D016, PWS-D021, PWS-D027, PWS-D028, PWS-D029, PWS-D036, PWS-D037, PWS-D038
- ADR: [ADR 0004](../adr/0004-windows-worker-isolation.md)
- Protocol: [Worker RPC protocol v1](../schemas/worker-rpc-v1.md)
- Startup recovery: [Startup recovery and sanitized diagnostics](startup-recovery-and-diagnostics.md)

## Scope

PWS-0107 implements the separate Windows document-worker host, GUI-safe client/broker, bounded
anonymous-pipe transport, operation runtime, cancellation/deadline lifecycle, forced tree
termination, and restart behavior. The Windows launcher uses a zero-capability AppContainer and a
retained Job Object. Core protocol, client, launch, and lifecycle components remain
standard-library-only and are injected behind process/transport interfaces for deterministic
tests. After bootstrap isolation succeeds, host composition may load an admitted worker-side PDF,
image, or OCR adapter from the exact staged runtime; third-party types still do not enter the core
contracts. An unavailable optional adapter is omitted rather than discovered through ambient
plugins or advertised as a capability.

PWS-0811 gives the shared desktop composition the closed internal `desktop-v1`
launch scope. It constructs and admits only `protocol.noop` and the six existing
desktop capabilities: text companion extraction, image normalization, signature
image rasterization, image transformation, PDF inspection and PDF thumbnails.
Their real adapter construction and engine checks still precede advertisement;
an unavailable adapter is omitted independently. Export and OCR handlers are not
imported into this scope, so opening an image does not load their unrelated
engines. Required PDF/PDFium adapters remain part of the desktop contract. The
default `all-v1` registry and separate export/OCR/removal compositions retain
their behavior. This changes no RPC descriptor/schema, trusted-resource
verification, process policy, source authority or user-facing command line.

The registry now contains `protocol.noop` version 1, `pdf.inspect` version 1, `pdf.thumbnail`
version 1, `image.normalize` version 1, `image.transform` version 1, `pdf.export` versions 1 and 2,
`ocr.process` version 1, and the additive `pdf.export.searchable` and `pdf.export.flattened`
version-1 capabilities when each operation's exact resources pass admission. The no-op still
proves process/protocol lifecycle without
document authority. PWS-0105 proves exact prepared-source read-handle transfer, a write-only
bounded result pipe, worker-private copy verification, pikepdf/libqpdf structural inspection, and
one-shot worker revocation. PWS-0108 adds one committed-source read handle and one fixed-size
writable volatile output section for a bounded one-page render. PWS-0112 adds a manifest-bound
batch of committed-source read handles, one read-only plan section, one bounded writable candidate
section, and one write-only report pipe for deterministic assembly and verification. No PDF
operation grants a case/directory path, persistent cache or destination writer, or general
filesystem access. See
the [PDF structural-inspection contract](pdf-structural-inspection.md),
[bounded PDF-thumbnail service contract](pdf-thumbnails.md),
[bounded JPEG/PNG intake contract](image-intake.md),
[non-destructive scan-transform contract](image-transform-pipeline.md),
[bounded local OCR worker contract](ocr-worker.md), and
[deterministic verified PDF-export contract](pdf-export.md), extended by
[verified searchable mixed-document output](searchable-mixed-output.md) and
[verified flattened visual-overlay export](flattened-overlay-export.md).

## Component ownership

| Component | Responsibility | Trust rule |
|---|---|---|
| `worker.client.client` | Dedicated coordinator-thread lifecycle, local snapshots, deadlines, cancellation, shutdown, fault and restart | GUI-process-side; no host/platform/document-adapter import |
| `worker.client.process` | Typed launcher/process/transport contracts and bounded framed transport | Hides payloads, paths, commands, and environment from representations |
| `worker.client._windows` | AppContainer profile, ACL grant lease, pipes, suspended process, Job Object, termination and cleanup | Windows-only private launcher; no UI dependency |
| `worker.client._acl_recovery` | Versioned bounded `PREPARED`/`ACTIVE`/`REVOKING` ACL ledger and atomic private journal | Legacy-v1/exact-object-v2 path-private launcher state; no case, protocol, UI, or routine-diagnostic surface |
| `worker.protocol.bootstrap` | Exact 92-byte launch grant | Launch-only; secret redacted and never extensibly decoded |
| `worker.protocol.framing` | Four-byte length framing and sanitized local transport errors | Returns bytes only; protocol decode always follows |
| `worker.protocol.peer` | Outbound validation/encoding and inbound decode/authentication/state transition | Sole conversation owner on each side |
| `worker.host` / `worker.runtime` | Isolation self-audit, negotiation, dispatch, progress, cancellation, timeout and close | No UI/application dependency; severe faults end the generation |
| `worker.operations` | Immutable exact operation-handler registry | Only registered descriptors dispatch; raw handler exceptions are mapped |
| `worker.pdf_source_inspector` | One-shot GUI-process orchestration, handle transfer, cancellation/deadline checks, termination, and result validation | Structural broker protocols only; storage and worker implementations are injected |
| `worker.pdf_operation` | Adopt one brokered source, verify/copy/hash it, invoke the PDF adapter, and write one correlated result | Host-side only; closes the case handle before native parsing |
| `worker.pdf_thumbnail_broker` | Resolve one committed page, authenticate cache hits, broker exact source/output-section handles, terminate one-shot generation, validate volatile output, and persist/publish through storage | Structural ports only; never treats a key, UUID, or path as authority |
| `worker.pdf_thumbnail_operation` | Adopt one source and fixed-size output-section handle, enforce its exact logical limit, verify/copy/hash source, render one page, and write one fixed correlated artifact | Host-side only; receives no persistent cache-file or directory capability |
| `worker.image_page_broker` | Authenticate a prepared or committed JPEG/PNG handle, broker one fixed output section, terminate the one-shot generation, and validate a correlated opaque RGBA8 artifact | Structural ports only; no source path, persistent cache writer, or decoder object crosses the boundary |
| `worker.image_normalize_operation` | Adopt one read-only immutable-source handle and one bounded output section, invoke the admitted Pillow adapter under explicit limits, and write one fixed artifact | Host-side only; worker-private raster storage is anonymous and cleaned before terminal success |
| `worker.image_transform_broker` | Reauthenticate one manifest-resolved JPEG/PNG source, broker one bounded output section, terminate one-shot processing, and validate exact source/intent/profile/result correlation | Structural ports only; no path, decoder object, manifest writer, or persistent raster writer crosses the boundary |
| `worker.image_transform_operation` | Adopt one immutable-source handle and one fixed-size output section, normalize and transform one page under the canonical eight-checkpoint profile, and write one correlated artifact | Host-side only; closes reduced authorities on success, cancellation, timeout, and failure |
| `worker.ocr_broker` | Authenticate one immutable source lease, broker one reader plus bounded output/report writers, terminate the one-shot Job, revalidate source/report/bytes, and expose a volatile candidate | Structural ports only; no source path, OCR library object, recognized text, manifest writer, or persistent output writer |
| `worker.ocr_operation` | Copy and rehash one source in private temp, invoke the exact OCR adapter, write one correlated report and volatile PDF candidate, and clean every private intermediate | Host-side only; closes the case handle before native parsing and never publishes a case or external artifact |
| `worker.client.tesseract_runtime` | Authenticate the packaged runtime-manifest fingerprint and the exact 39-file closure before launch authority | Standard-library-only admission; missing, extra, changed, linked, special, or reparse entries fail closed |
| `worker.process_creation_policy` | Install a worker-wide audit hook that permits only the exact configured qpdf/Tesseract subprocess shape | Loaded after isolation and before optional engines; shell/spawn and ambient executable discovery are denied |
| `worker.pdf_export_broker` | Authenticate one manifest-bound source batch, broker exact plan/output/report handles, terminate the one-shot generation, revalidate sources, and expose only the bounded verified candidate | Structural ports only; never receives or publishes a destination path |
| `worker.pdf_export_operation` | Adopt exact source/plan/output/report handles, copy and rehash sources in one private operation directory, assemble and verify, and write one correlated report | Host-side only; receives no case, manifest, history, or destination authority |
| `app.flattened_pdf_export` | Project visible manifest overlays, rasterize authenticated placed-signature assets sequentially, retain exact PDF sources, publish a verified derivative, and settle unchanged export history | Toolkit/engine-free coordination; no native PDF object, asset path, or persistent destination authority crosses into the worker contract |
| `worker.flattened_pdf_export_broker` | Authenticate the base source batch and operation-scoped RGBA rasters, broker exact plan/source/raster/output/report handles, terminate the one-shot generation, revalidate sources, and expose only the bounded verified candidate | Structural ports only; receives no case path, encoded signature image, manifest writer, history writer, or destination path |
| `worker.flattened_pdf_export_operation` | Adopt exact plan/source/raster/output/report handles, stage and rehash source PDFs, map exact RGBA sections read-only, flatten explicit Form XObjects, and write one correlated report | Host-side only; all native parsing/rendering and private paths remain inside the restricted worker |
| `app.searchable_pdf_export` | Project manifest-v2 intent, retain an authenticated mixed source batch, call one structural broker, reuse no-replace destination/history ports, and return the exact page report | Toolkit/engine-free; no document path or third-party object enters the pure service contract |
| `worker.searchable_pdf_export_plan` / `status` / `report` | Canonically encode/decode the bounded worker plan, content-free page outcomes, and correlated report | Host/client wire seams only; unknown members/versions, text values, and noncanonical bytes fail closed |
| `worker.operations.SearchablePdfExportOperationHandler` | Adopt the additive plan/source/output/status/report handles and invoke one injected searchable adapter | Host-side only; no case, manifest, history, cache, or destination authority |
| `worker._windows_broker` | Non-inheritable bounded anonymous result pipe and fixed-size pagefile-backed output section | GUI-process-side private primitives; no path or document value |
| `storage.prepared_source_registry` | Register/revoke one sealed pending source and lend an exact independently positioned read handle | Storage owns source identity and publication; no worker-host import |
| `storage.source_ingest` / `storage.thumbnail_cache` | Lend one manifest-bound committed PDF/JPEG/PNG handle; authenticate, stage, validate, publish, invalidate, and clean disposable thumbnails | Storage owns handle identity and cache publication; no worker implementation import |
| `pdf.pikepdf_adapter` / `pdf.qpdf_checker` | Typed structural inspection and fixed-command qpdf test seam | Third-party objects/output remain inside the PDF adapter boundary |
| `pdf.ocr_candidate_verifier` | Reopen one private OCR PDF without recovery and return only a positive page count | Narrow worker-side PDF seam; encrypted/invalid state is a closed failure and no pikepdf object crosses |
| `pdf.export_adapter` | Assemble, strictly reopen, qpdf-check, and PDFium-compare one worker-private candidate | Third-party objects, native output, and paths remain inside the restricted worker |
| `pdf.searchable_export_adapter` | Inspect page eligibility, preserve text pages, normalize/transform eligible raster pages, invoke one page-local OCR adapter call, assemble, reopen, qpdf-check, and verify every render/text fact | Restricted-worker composition only; page-private paths/text/native objects never cross the worker result boundary |
| `pdf.text_presence` | Count PDFium characters and non-whitespace characters on selected pages without constructing or returning extracted strings | Worker-only PDFium seam; content-free facts only |
| `pdf.raster_pdf_page` | Wrap one opaque transformed RGBA page as a strictly reopened private PDF at fixed image DPI or authenticated inherited physical geometry | Worker-only pikepdf seam; bounded one-page artifact and no persistent publication |
| `imaging.pillow_decoder` | Perform bounded JPEG/PNG metadata inspection, EXIF interpretation, decode, color/alpha normalization, and fit | Pillow/native objects, metadata values, and raw exceptions remain worker-local |
| `imaging.pillow_transformer` | Apply crop, quarter-turn, deskew, contrast, cleanup, fit, and opaque RGBA8 serialization to one normalized page | Pinned worker-only pixel profile; checks cancellation between bounded stages and never owns manifest intent |
| `ocr.ocrmypdf_adapter` / `ocr.tesseract_runner` | Run the fixed OCRmyPDF 17.10.0 profile through the exact Tesseract 5.5.3 executable, monitor private temp, map progress, and request narrow PDF verification | Worker-only; one absolute child, no shell/PATH/plugin discovery, bounded discarded tool output, and no third-party object or text crossing |

Package initializers remain passive. Application/UI code may import explicit `worker.client` and
`worker.protocol` modules but cannot import the host, Windows launcher implementation, operation
handlers, or future document adapters.

## Launch contract

### Trusted configuration

The composition root supplies absolute, local, approved paths for the Python executable,
bootstrap script, runtime roots, code roots, and working directory. A source-tree virtual
environment may additionally supply its fixed launcher path as a startup identity hint. The
executable and optional launcher must be files inside approved runtime roots; the launcher must be
distinct from the executable, and the bootstrap must be inside an approved code root. Remote,
device, relative, missing, and reparse-point locations fail before process creation.

The separate PWS-0204 OCR composition additionally requires an absolute packaged Tesseract
executable and its exact `share/tessdata` directory. Before either becomes launch authority, the
launcher authenticates the code-owned runtime manifest fingerprint and every file in its 39-file
closure by layout, type, link count, size, and SHA-256; missing, changed, reparse, or unlisted
objects, including a hardlinked or special entry, fail before process creation. The OCR Job
permits exactly two active processes—one Python
worker and one Tesseract child—while the non-OCR compositions retain their narrower process
profiles. See the [bounded local OCR worker contract](ocr-worker.md).

Before the first ACL mutation, the launcher retains an authenticated `GENERIC_READ` handle that
denies delete sharing for every approved object and every lexical ancestor. It identifies each
object from the handle, rejects reparse points and delete-pending objects, and requires every
enumerated file's Windows link count to be exactly one. A hardlink shares one security descriptor
across every name, including names outside the approved roots, so changing that descriptor would
silently expand the grant's authority. An aliased file therefore fails with a sanitized
configuration error; the launcher never repairs it by granting the individual file. The retained
handles block rename, replacement, ancestor-junction substitution, and alias removal. Each retained
file is protected by one direct current-user deny entry for `FILE_WRITE_ATTRIBUTES`, the right NTFS
requires to create another hardlink. Authenticated overlapping v2 leases share that one guard and
its recorded pre-PaperWorkShell baseline. Link count is checked before and after the guard and the
AppContainer grant, and again before resume. If an alias wins the narrow pre-guard window, no
AppContainer entry is applied; exact-handle cleanup still removes PaperWorkShell's entry despite
the changed link count, avoiding a stranded user policy or profile.
The repository configures uv's install mode as `copy` so a locked development environment creates
independently owned runtime files.

The hosted Windows workflow also refuses to use the shared `setup-python` tool-cache tree as
worker authority. Before bootstrap it copies that pinned interpreter into a fresh runner-private
physical directory, proves the copied executable reports that directory as `sys.base_prefix`, and
sets uv's exact `UV_PYTHON` interpreter selection before creating `.venv`. The live production
composition tests then apply the unchanged exact-object, link-count, ACL-authority, and retained-
handle checks to the copy-owned base runtime. This CI staging does not relax launcher admission for
shared, hardlinked, or foreign-authority runtime trees.

The command is a fixed structured argument array, never a shell string:

```text
<approved-python> -I -S -B -X utf8 -X pycache_prefix=<logical-LocalAppData>\Temp\PyCache <approved-host-bootstrap>
```

The full-trust broker resolves the current user's logical `FOLDERID_LocalAppData` through the
Windows Known Folder API; it never trusts an inherited environment value. The raw `-X` value and
the constructed `LOCALAPPDATA`, `PWS_PROFILE_ROOT`, `TEMP`, and `TMP` use that logical spelling so
AppContainer path virtualization maps it exactly once. Separately, the broker authenticates,
creates, guards, samples, and later removes the corresponding physical per-launch AppContainer
`AC\Temp` tree. The cache guard is the physical `AC\Temp\PyCache` directory. The worker self-audit
binds the raw logical `-X` value to `PWS_PROFILE_ROOT\Temp\PyCache`, binds the active cache namespace
to Windows' rewritten `TEMP\PyCache`, requires `TEMP == TMP == LOCALAPPDATA\Temp`, and requires `-B`
plus an empty cache directory before negotiation. A live parent/child handshake proves a held child
temporary file is visible only in physical `AC\Temp`, the old double-virtualized `AC\Packages`
namespace is absent, the full-trust user's real `LocalAppData\Temp` remains untouched, and cache
mutation is denied. No filename, template value, OCR/document text, case path, or RPC value can
alter the array.

The PWS-0111 desktop composition launches the approved base CPython executable directly. A Windows
virtual-environment `python.exe` is itself a launcher that would create a second process, which the
product's `maximum_processes=1` Job policy intentionally denies. When the active runtime is a
virtual environment, the broker places its separately validated launcher path in the constructed
child environment as CPython's `__PYVENV_LAUNCHER__` startup hint. CPython consumes and removes the
hint while initializing the single base-interpreter process, before PaperWorkShell imports or runs
the worker isolation audit. The audit allowlist is unchanged, and an installed/global runtime
omits the hint entirely.

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
   or interrupted runtime/code grant whose recorded roots and objects remain inside the current
   approved access-root allowlist. Any invalid or unprovable state blocks launch.
3. Create an ephemeral AppContainer profile with zero capability SIDs.
4. Open and authenticate the complete bounded runtime/code inventory through retained no-delete
   handles; reject reparse points and hardlinked non-directory resources; capture each v2 Windows
   object identity, auto-inheritance state, current-user peer SID, and shared-guard baseline. Mutable
   `__pycache__` directories and `.pyc`/`.pyo` cache leaves are not worker authority. The fixed
   profile-private `pycache_prefix` prevents source-tree cache lookup, while `-B` prevents cache
   writes, so the worker uses the authenticated source/runtime objects. For OCR, the complete
   Tesseract runtime closure is part of this exact object set. Durably
   publish `PREPARED` before mutation.
5. Establish or reuse one shared hardlink guard per file, add the profile SID's direct,
   non-inheritable read/execute entry to every exact object, restore inheritance-control bits, and
   verify every fixed startup resource before publishing `ACTIVE`. The optional virtual-environment
   launcher hint, qpdf closure, and OCR-only Tesseract closure remain fixed, validated resources.
6. Construct an environment containing only the approved Windows/Python names and configured fixed
   virtual-environment/qpdf/Tesseract hints. OCR adds only the exact executable and tessdata paths;
   it does not add `PATH`, `PATHEXT`, or a user plugin/profile search path. Resolve the trusted
   logical `LocalAppData` Known Folder in the broker and spell `LOCALAPPDATA`, `PWS_PROFILE_ROOT`,
   `TEMP`, and `TMP` from it so AppContainer maps them once into the authenticated physical
   per-launch profile; never inherit those values.
7. Create three anonymous pipes and make only the worker ends inheritable.
8. Build an explicit process-attribute handle list and zero-capability security descriptor.
9. Create the fixed process suspended, with no window and no shell.
10. Configure and assign the Job Object before resume.
11. Close broker duplicates of the worker-only pipe ends; write and close the exact bootstrap
    grant; resume the primary thread.
12. Before negotiation or descendant launch, clear and re-query `HANDLE_FLAG_INHERIT` on all
    three worker endpoints.
13. Retain the process, job, profile, and broker pipe ends for the full generation.

Runtime/code ACL changes are SID-specific and serialized across sessions by a protected Global
mutex. Its owner is the current user, its DACL grants only that user and LocalSystem, its handle is
non-inheritable, and an existing object's owner/DACL must match exactly. The broker rejects an
absent or NULL DACL and applies/removes only the launch SID's direct entry through retained object
handles. Overlapping live profiles therefore preserve one another's SID entries and share one
authenticated current-user hardlink guard per exact file. Cleanup proves the temporary SID absent,
retains the guard while another authenticated `ACTIVE` v2 lease covers the same object/baseline,
and otherwise removes exactly one guard to restore the recorded matching-deny count. It then
restores and verifies each object's original auto-inheritance state without replaying a stale whole
descriptor.

PWS-0811 optionally retains only the validated installed desktop's immutable runtime/code
lease for one active case. Its ordinary exporters explicitly borrow the same fixed
owner, with unchanged per-launch tool admission, environment, handler scopes and Job
limits. Borrowers must match fixed runtime/code identity and recovery authority; all
actual engine resources must already belong to the original pinned inventory. There
is no lazy grant expansion or concurrent borrowed generation. Default/development,
package and qualified redaction launchers retain the
per-operation lifetime above. Every operation still has a fresh parser, Job, pipes,
bootstrap, capability and session, and the complete tree must stop before output is
read or validated. No case, source, output, cache or destination authority survives.
After tree/transport closure all profile-storage handles close; profile deletion must
report success and its exact filesystem location must be absent. The next generation
requires newly created storage (S_OK, never already-existing), actual returned/derived
SID equality and fresh guarded empty PyCache. The documented new-datastore contract
includes registry storage and is exercised with native registry/file canaries.
Cancellation, timeout, fault and consuming-broker output rejection retire static
access; uncertain cleanup is sticky, while safe exact cleanup remains possible.
Case close settles autosave and releases static access before the case lock; shutdown
uses the same owner. Existing recovery also covers the ACTIVE interval with profile
storage absent. [ADR 0020](../adr/0020-exact-directory-acl-updates.md) records API
qualifications and required native/installed evidence. This is not persistent parser
reuse and does not relax the one-shot output-authority rules below.

Warm admission may replace only its exact own live ACTIVE record's path reopening
with an opaque lease witness. Under the same mutex, the protected journal is read
again, the full record and current owner/thread/profile/inventory must match, and
every retained resource/ancestor identity, current DACL control, exact own SID ACE
and file guard count is revalidated. Missing/mismatched own records poison reuse.
Foreign/default/dead/non-ACTIVE records keep full recovery; a foreign repair triggers
another own validation. Publication attempts invalidate witness eligibility first.
Final revocation always uses full existing recovery and current ACL state.

PWS-0113 makes interruption recovery durable. Beneath the trusted Windows LocalAppData known
folder, the launcher creates or authenticates one non-reparse recovery directory whose protected
DACL grants full inherited access only to the current user and LocalSystem. Each new profile
receives one bounded canonical v2 journal record containing the profile ID/name, exact AppContainer
SID, owner PID plus process-creation time, current-user peer SID, fixed approved grant roots, and
every resource's absolute trusted path, type, Windows volume serial/file ID, original
auto-inheritance state, and nullable guard baseline. Directories have no guard baseline. Those
paths are needed only to revoke an interrupted grant; they never enter RPC, routine diagnostics,
UI results, or representations. Strictly decoded legacy v1 records remain recoverable with their
original inherited-root behavior, but the launcher emits only v2.

The launcher publishes `PREPARED` before the first shared-guard or AppContainer mutation, `ACTIVE`
only after every direct grant and inheritance state is verified, and `REVOKING` before cleanup.
Before creating a new profile it inventories the journal. A live exact owner with `ACTIVE` state is
retained. A dead owner or interrupted phase is repaired only after the current allowlist and every
recorded object identity are revalidated under the mutex. Exact-object v2 repair revokes and
verifies the AppContainer SID on every retained object first. It then preserves a shared guard
covered by another authenticated live v2 ledger or removes exactly one guard and proves the
recorded baseline, restores/verifies inheritance state, deletes the exact record, and closes the
retained handles. Legacy v1 repair revokes only its recorded inherited root entries and verifies
their original bounded inventory.

Pre-exclusion v2 journals retain the same strict exact-identity rule, including for recorded Python
cache objects. A missing or replacement cache path is not proof that the recorded object vanished:
the old object may have been renamed while retaining its profile SID entry or peer guard. Recovery
therefore keeps the profile/journal evidence and blocks launch on any such mismatch instead of
guessing that cleanup is complete. New journals avoid this compatibility trap by never recording
mutable cache objects.

On `WAIT_ABANDONED`, recovery inventories and repairs all validated dead/interrupted entries while
retaining mutex ownership; if it can repair none, launch blocks conservatively. Windows does not
expose the prior mutex owner, so this is an aggregate cutpoint invariant rather than a claimed
one-to-one abandonment/entry correlation: an abandonment before its record is pre-mutation, while
one after mutation necessarily leaves that record. An insecure/corrupt/unknown-version entry,
PID-identity uncertainty, peer-SID/allowlist mismatch, conflicting guard baseline, resource
identity change, incomplete SID/guard/inheritance restoration, or journal cleanup failure blocks
the new grant and launch. Abandonment is never converted into permission to remove an unrecorded
ACE or restore an entire stale DACL. Numeric Job Object and recovery limits are range-checked before
conversion to Windows ABI values.

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

### Signed-source admission before lifecycle authority

PWS-0208's reason-aware unsigned-derivative confirmation stays in the trusted application/UI
process and precedes worker lifecycle authority. For a marker-present or unknown-signature source,
the presentation shows the exact **Create unsigned derivative** action and a safe/default
**Cancel** action. Cancel or Escape returns a rejected presentation result: the coordinator must
not create a worker generation, enqueue an operation, broker a source/output handle, reserve a
destination, or write confirmation state.

An affirmative dialog result is also not a worker capability. The trusted application must bind
it to validated source-scoped confirmation state and re-run the signed-source policy gate before
starting work. Only an admitted content-free plan crosses the worker boundary; warning prose,
document values, actor text, and confirmation UI state do not enter RPC or routine diagnostics.
Confirmation never permits OCR or a pixel transform for a marker-present or unknown-signature PDF
page. The worker retains that visual page under the existing fail-closed policy, and any permitted
modified aggregate remains labeled **unsigned derivative** after verification.

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

For `image.transform`, RPC success likewise proves only that the worker claims to have filled the
requested bounded volatile object. The source is reauthenticated before launch and after confirmed
one-shot termination. The broker validates the fixed artifact, exact source and output identities,
canonical transform-intent digest, transform and normalization profiles, output geometry/stride,
complete pixel digest, opaque-alpha invariant, and RPC correlation before returning typed pixels.
Cancellation, timeout, malformed output, disk/capacity refusal, or source mutation returns no
authoritative result and never changes the manifest or immutable source.

For `ocr.process`, RPC success proves only that the worker claims to have filled one bounded
volatile PDF section and written one content-free correlated report. It does not prove recognized
text, searchability, case publication, or export validity. The broker first terminates and confirms
the complete two-process Job, reauthenticates the retained immutable source, then validates the
closed report, exact OCRmyPDF/Tesseract versions and profile digest, operation/source/output IDs,
page count, size, and an independently recomputed candidate SHA-256. Only a validated single-pass
candidate is exposed, and closing it releases the volatile section. Cancellation, timeout, crash,
engine/language absence, malformed output, source mutation, or cleanup uncertainty returns no
candidate and never changes the manifest or immutable source.

For `pdf.export.searchable`, RPC success binds one bounded plan, an authenticated PDF/JPEG/PNG
source batch, one PDF output section, one separately bounded page-status section, and one report
pipe. The capability is additive: neither `ocr.process` v1 nor `pdf.export` v1 accepts those extra
handles or gains searchable-output meaning. The broker must terminate the complete one-shot Job,
reauthenticate every source, and validate the output and status object IDs, capacities, content
kinds, correlations, exact page order/count, digests, eligibility/outcome codes, and base reopen/
render checks before exposing a candidate. Extracted text never appears in RPC or the status
section. A malformed or oversized page-status result, page-local mismatch, OCR failure, cancel,
timeout, source race, cleanup uncertainty, or unconfirmed tree returns no authoritative aggregate
claim and cannot mutate an original, manifest, history record, or destination.

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

PWS-0202 uses the same authority shape for a complete scan-transform request, not a broader image
grant. Storage resolves the current manifest page to one exact JPEG/PNG source; the broker transfers
only source-read and fixed output-section map-write authority. The worker performs strict bounded
normalization, applies the canonical eight-checkpoint transform profile, and closes the source
before terminal output. The worker result remains volatile until whole-tree termination and strict
typed artifact validation. PWS-0811 optionally lets the trusted broker publish that bounded opaque
RGBA artifact into the same case-private disposable cache and central CACHE quota as PDF thumbnails.
A separate versioned predecode lookup key binds the authenticated immutable source hash/size/type,
case/source/page IDs, complete transform intent, requested dimensions, engine profile and artifact
version. A hit must also validate the native artifact's full decoded-geometry key, current snapshot
versions, exact opaque pixel layout and digest; source, transform, size or engine changes cannot reuse
the old entry. Corrupt entries are removed and rebuilt. No image decoder runs in the GUI. No transform
operation receives a case path, manifest/journal handle, persistent cache writer, or permission to
replace a source; only the trusted broker can validate and atomically publish disposable cache state.
See the [non-destructive scan-transform contract](image-transform-pipeline.md).

PWS-0204 follows the same one-shot authority rule for one authenticated PDF/JPEG/PNG source. The
broker transfers only source-read, bounded output-section map-write, and bounded result-pipe write
authority. The worker copies and rehashes the source under an operation-scoped private temp limit,
closes the case handle, and permits one exact Tesseract child. The output remains broker-owned,
volatile, and disposable; neither OCRmyPDF nor Tesseract receives a case path, manifest/journal
handle, persistent candidate writer, external destination, or source-write authority. The complete
profile, validation, and signed-source fail-closed rules are in the
[bounded local OCR worker contract](ocr-worker.md).

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

The PWS-0201 image profile negotiates `pdf.export` version 2 with `pdf-export-plan-v2` and exact
PDF/JPEG/PNG source media references. Version 1 remains PDF-only. The new version uses the same
handle grant, private-temp lifecycle, bounded candidate/report, source revalidation, and no-replace
publication. JPEG/PNG decoding stays inside that worker through the pinned normalization adapter;
one opaque raster and one fixed-300-DPI PDF page are processed and removed at a time. Intrinsic
orientation and user quarter turns are supported; active crop/cleanup/overlay intent is rejected.
Source copies, candidate capacity, and image scratch must jointly fit the operation temp limit.
The qpdf-only launcher admits no OCR executable or language pack for this operation.

The standalone qpdf executable is a fixed launcher resource, never a PATH-discovered command. An
explicit packaged/test executable adds only its exact absolute path to the constructed worker
environment. The launcher proves AppContainer read/execute access across the bounded non-reparse
qpdf directory closure, including adjacent DLLs/resources, without weakening the existing runtime
and code-root grant lifecycle. The worker still uses a fixed argument vector and no shell. See the
[deterministic verified PDF-export contract](pdf-export.md).

## Required verification

PWS-0107 is complete only with real supported-Windows evidence for:

- fixed executable, arguments, working directory, no shell, and constructed minimal environment;
- product-like virtual-environment startup through the direct base interpreter, with the fixed
  hint consumed before audit and a one-process Job limit preserved;
- rejection of a hardlinked runtime file before ACL mutation, with no path or alias disclosed in
  the stable failure;
- retained-handle rejection of an after-scan hardlink replacement and ancestor-junction pivot, plus
  verified handle-based cleanup while path alias removal is blocked;
- an outward `CreateHardLink` race before guard application fails the launch without receiving an
  AppContainer entry and still permits exact-handle guard/profile cleanup; and
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

PWS-0204 adds required supported-Windows evidence for one real OCRmyPDF 17.10.0/Tesseract 5.5.3
operation through the production AppContainer/Job launcher. The test must authenticate the exact
39-file Tesseract closure, negotiate `ocr.process`, transfer only the three reduced operation
handles, preserve the external and committed source hashes plus the authoritative manifest, and
receive a structurally reopened one-page volatile PDF candidate after confirmed two-process Job
termination. Focused tests separately cover progress, cancellation, timeout, worker crash, missing
engine/language, malformed request/report/output, injection strings, decoy runtime/plugin/search
paths, output/temp/tool-output boundaries, prospective capacity refusal before OCR, partial
disk-full cleanup followed by a fresh retry, direct-child timeout terminate-to-kill escalation,
strict no-recovery/unencrypted PDF reopen, runtime hardlink/special-entry refusal, and prohibited
log canaries. Generic
PWS-0107 live containment tests remain the evidence that the real worker and descendants cannot
egress or escape the Job; unit doubles alone do not prove that property.

PWS-0113 adds required supported-Windows evidence for the persistent ACL-recovery boundary:

- a canonical v2 `PREPARED` record with peer SID, exact Windows object identities, inheritance
  state, and per-file guard baselines is flushed before the first guard/SID ACL mutation, while
  phase updates are published atomically beneath the authenticated private LocalAppData directory;
- a real helper process is forcibly terminated while retaining the protected mutex at `PREPARED`,
  partial-grant, `ACTIVE`, and `REVOKING` cutpoints; separate-process recovery observes
  `WAIT_ABANDONED`, authenticates the dead PID plus creation time, removes the exact SID from every
  v2 object, restores the shared-guard baseline and auto-inheritance, clears the journal only after
  complete verification, and permits a replacement grant;
- overlapping live v2 leases authenticate and share one guard per object; recovery keeps it while
  another `ACTIVE` ledger covers the object, and the final cleanup proves the recorded baseline;
- canonical legacy v1 ledgers remain bounded and recover with inherited-root semantics rather than
  being reinterpreted as v2 exact-object grants;
- an insecure recovery-directory policy, corrupt/noncanonical record, unknown entry, current
  configuration peer-SID/allowlist mismatch, conflicting baseline, hardlinked/special resource, or
  resource identity mismatch blocks new ACL work without deleting evidence or widening authority;
- new inventories omit mutable Python cache objects and reject a cache object as an explicit root,
  while a cache-path identity mismatch in an old record remains strict, retains evidence, and makes
  no unproved ACL mutation; and
- the complete live worker/descendant containment suite still passes with pre-launch recovery in
  the production launcher path.

These tests prove cold-restart repair of recorded runtime/code AppContainer grants only. They do
not prove replay of worker operations, general ACL repair, case-workspace access, recovery from an
unrecorded mutation, or automatic cold deletion of abandoned AppContainer profiles and their local
temporary trees.

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

PWS-0204's live OCR proof additionally stages only the locked OCRmyPDF Python closure and the
manifest-authenticated Tesseract runtime. It must use the same production host, process audit hook,
two-process Job, zero-capability token, minimal environment, and exact-object ACL grant path; a
developer `PATH` installation or unrestricted direct adapter run is useful diagnosis but is not
the supported-Windows isolation claim.

PWS-0205 extends runtime admission with a separately fingerprinted optional-language catalog. A
locally prestaged German model is available only when its regular-file identity, exact byte size,
SHA-256, and unnamed-stream-only state match that catalog; missing data stays unavailable, while
changed, redirected, offline/recall, alternate-stream, or unlisted data invalidates the runtime
before authority is granted. Capability discovery first requires a mount-manager-registered
`DRIVE_FIXED` NTFS/ReFS volume with persistent ACLs whose drive and volume-GUID names map to the
same `\\Device\\HarddiskVolumeN`, so UNC/device spellings, network-drive mappings, unregistered DOS
aliases, removable volumes, and provider mappings that do not meet that OS-visible contract are
not enumerated. This check does not attest physical medium or lower-layer storage transport;
iSCSI-, SAN-, or VHD/VHDX-backed volumes can satisfy it. Discovery never downloads packs and does not change
the worker's zero-capability network policy. Close and restart PaperWorkShell after an offline pack
change. PWS-0801/PWS-0802 retain the exact release staging path, elevation, removal, notices, and
detached-signature verification evidence.

The PWS-0107 no-op must not fake handle evidence with a test-only production RPC operation. Tests
may inject fixed launch/operation adapters, but release claims require the real launcher and host.
Successful case-object handle brokerage was not a PWS-0107 completion criterion. PWS-0105 is the
first consuming operation and must prove that only its exact deliberately brokered handles work.
Any required skipped probe, leaked canary, surviving descendant, unexpected writable path, invalid
result correlation, or cleanup failure leaves the consuming task/gate open.

## Explicit deferrals

- PWS-0801: release-grade packaged-content/SBOM/license-notice and release-input verification.
- PWS-0802: exact installer path, privilege, repair/removal, and restart behavior.
