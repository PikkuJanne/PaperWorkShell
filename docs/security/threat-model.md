# PaperWorkShell foundation threat model

- Status: Binding foundation model
- Task: PWS-0004
- Decisions: primary task references are recorded in `TASKS.yaml`; complete control ownership is in
  [threat-traceability.md](threat-traceability.md)
- Scope reviewed: M0 through M8 roadmap architecture

## Purpose and security objective

PaperWorkShell processes identity, address, financial, medical, employment, signature, and other
potentially sensitive document content on a Windows desktop. PDF, JPEG, PNG, template, path,
metadata, OCR, worker, and export inputs are untrusted even when a user deliberately selected
them.

The foundation objective is to preserve source integrity and confidentiality while producing only
explicitly verified derivatives. Complex document parsing stays outside the desktop/UI process,
processing remains local and bounded, routine diagnostics disclose no document content, and a
failure leaves the last valid case and original sources unchanged. The model is preventive: users
receive plain recovery choices and are never expected to understand PDF objects, revisions,
scripts, signatures, or parser internals.

The concrete flows are in [data-flows.md](data-flows.md), decision/task coverage is in
[threat-traceability.md](threat-traceability.md), and evidence expectations are in
[security-test-plan.md](security-test-plan.md).

The [permanent-redaction extension](redaction-threat-model.md) defines PWS-0601's supported
claim, adversarial corpus oracles, failure taxonomy, and planned M6 evidence. Its completion does
not enable permanent redaction; independent PWS-0608 approval remains required.

## Scope

### In scope

- The Windows desktop/UI process and toolkit-free application/domain logic.
- The UI-to-worker client, transport, protocol, and restricted worker process.
- PDF, image, OCR, renderer, export, storage, template, and diagnostics adapters.
- External source paths, the case workspace, case-scoped cache/temp data, and export destinations.
- Local external executables and resources such as qpdf, OCRmyPDF, Tesseract, renderers, fonts,
  and OCR language data once admitted by their tasks.
- Declarative templates, manifests, journals, RPC messages, generated derivatives, package
  summaries, and diagnostics.
- Development/release dependencies, the optional future update channel, installer inputs, and
  migration artifacts.
- Cancellation, crash, disk-full, low-memory, path-race, and partial-write behavior.

### Explicitly out of scope or not guaranteed

- Protection after an attacker obtains Windows administrator/kernel access, arbitrary access to
  the current OS account's files or memory, or records the screen/keyboard. Best-effort IPC and
  path-race defenses still reject an untrusted peer process, and case permissions use least
  privilege, but they are not an account-security boundary.
- Forensic erasure from SSDs, backups, filesystem journals, OS-managed crash dumps or swap, search
  indexers, antivirus products, or third-party synchronized folders.
- Legal validity, eligibility, portal acceptance, OCR certainty, PDF/UA conformance, and
  cryptographic signature creation or validation in v0.1.
- Permanent redaction before the independent PWS-0608 gate. Ordinary shapes and overlays do not
  remove source information.
- Confidentiality of a verified export after the user deliberately places it in an external
  destination controlled by another application or service.
- Availability when the host, storage device, or OS account is unavailable. The product must still
  fail without corrupting originals or the last valid case.
- Automatic support for UNC, remote, or network-backed sources and destinations. Their device,
  credential, atomicity, and privacy behavior must be explicitly classified by the owning storage
  and export tasks before any such path is opened; unsupported locations fail closed.

These exclusions limit claims; they do not relax case scoping, sanitization, or safe failure.

## Protected assets

| ID | Asset | Required property |
|---|---|---|
| AS-01 | External source documents and images | Never opened for writing, replaced, or overwritten |
| AS-02 | Case copies and source hashes | Immutable application policy, random internal names, detectable mutation |
| AS-03 | Manifest, journal, confirmations, and operation intent | Versioned, validated, atomic, recoverable, authoritative |
| AS-04 | OCR text, form values, overlays, signature images, thumbnails, and previews | Case-confidential; absent from routine logs |
| AS-05 | Worker/RPC requests, results, capabilities, and error codes | Authenticated to the local session, validated, bounded, sanitized |
| AS-06 | Case temp/cache and export staging | Case-scoped, quota-bounded, deterministic cleanup/recovery |
| AS-07 | Verified PDF/ZIP outputs and package summaries | New derivatives, expected content/order, reopened and verified before success |
| AS-08 | Logs and user-authorized diagnostic bundles | Approved fields only; exact immutable bundle snapshot previewed before creation |
| AS-09 | Templates and schema/provenance data | Declarative, versioned, non-executable, bounded, locally inspectable |
| AS-10 | Executables, dependencies, fonts, language packs, installer, and update metadata | Pinned/inventoried/authenticated under the applicable release task |
| AS-11 | Isolation, resource-limit, and network-denial policy | Cannot be weakened by document, template, filename, or environment input |
| AS-12 | User review decisions and status wording | Cannot be silently forged by automation or overstated as legal/portal certainty |

## Actors and attacker capabilities

### Legitimate actors

- An independent user preparing a local packet.
- An assisted user and an authorized helper sharing the current desktop session.
- A maintainer producing reviewed dependencies, builds, templates, and updates.

### Adversarial or failure actors

- A malicious or malformed PDF/image/container author.
- A malicious template or template distributor.
- A local same-user process racing paths, files, environment values, or destinations.
- A compromised worker, parser, renderer, OCR executable, or other child process.
- A compromised dependency, build input, release artifact, mirror, or update channel.
- A destination filesystem, removable drive, synchronized folder, or archive consumer that fails,
  races, normalizes names differently, or exposes output.
- Ordinary crashes, power loss, disk-full, low-memory, timeouts, and cancellation at every boundary.
- A well-intentioned user who misunderstands signatures, redaction, OCR, heuristics, deletion, or
  a validation claim.

An attacker may control full input bytes, declared extensions, filenames, Unicode, metadata,
object graphs, compression ratios, page/image dimensions, templates, archive names, destination
state, timing, and strings that reach errors. Inputs may change between checks. The model assumes
no input is safe because it came from the user, has a familiar extension, opens in another viewer,
or was previously processed.

The model does not assume an unprivileged input can break Windows kernel security. It does assume
a parser can be compromised and therefore treats worker output and child-process output as
untrusted. A same-user peer without arbitrary account-file or memory access is addressed by
capability-bound IPC and handle-based path checks; malware with unrestricted access to the current
account remains outside the confidentiality/integrity guarantee. In particular, the Windows worker
runtime guard denies the ordinary same-user path-race peer the file right required to create a new
hardlink during a lease. Malware able to rewrite account-owned DACLs and remove that guard is the
unrestricted same-account case outside this guarantee.

## Trust boundaries

| ID | Boundary | Crossing rule |
|---|---|---|
| TB-01 | Person / Windows shell to desktop UI | Treat selected paths, typed values, drag data, clipboard data, and confirmations as untrusted input |
| TB-02 | Desktop application to worker client/RPC transport | Fresh zero-capability AppContainer generation; explicit anonymous-pipe handles and fixed one-shot capability grant; versioned typed messages bound to worker, case, operation, sequence, and request; size limits, replay rejection, and fail-closed decoding |
| TB-03 | Restricted worker to active case workspace | No path or directory capability; PWS-0105 proves one exact broker-authenticated prepared-source read handle plus a write-only result pipe, while future object types require their own least-privilege profiles; workers cannot publish manifests, journals, source records, or export history |
| TB-04 | Worker adapter to external executable/resource | Approved executable/resource identity, fixed working directory and argument array, controlled DLL/plugin/config/font/language search, minimal environment, bounded I/O/time/resources and descendants, no shell interpolation |
| TB-05 | Import/storage adapter to external source | Classify local/device/network policy before open; use one read-only handle for bounded streaming copy/hash; never treat a partial copy as a source record |
| TB-06 | Export staging to external destination | Classify destination policy, commit exact verified bytes through a destination-local temporary file, verify size/digest, reject original/prior-output aliases, then atomically replace only that new derivative name |
| TB-07 | Diagnostics collector to user-selected bundle destination | Freeze an allowlisted snapshot/entry manifest, preview and confirm that exact snapshot, then write it locally without recollection or implicit attachment/upload |
| TB-08 | Template store to template loader | Opaque catalog key and same-handle fixed-local byte snapshot; external exact-size/SHA/signature status before strict data-only version/schema/semantic/lifecycle validation; explicit claim/trust display; no template path, imports, expressions, commands, case writer, or file/network dereference |
| TB-09 | Optional updater to package acquisition and installation | No document worker or case access, independently trusted metadata and exact package bytes, freshness and channel policy, user consent and recoverable activation |
| TB-10 | Case storage to other local users/processes | Random internal names, restrictive permissions, lock/retention policy, no promise against account/admin compromise |

The [dependency-boundary policy](../architecture/dependency-boundaries.md) enforces code-level
direction. PWS-0008 defines the closed protocol and PWS-0107 implements TB-02 for the original
no-document worker. PWS-0101 establishes private case storage. PWS-0105 is the first owning
operation to prove one narrow TB-03 handle path; `protocol.noop` remains handle-free and the
AppContainer still has no access-control entry on a case root.

### Implemented PWS-0107 worker boundary

Each worker generation uses a new classic Windows AppContainer profile with zero capability SIDs,
a Job Object assigned while the process is suspended, and an explicit inherited-handle list
containing only client-to-worker RPC, worker-to-client RPC, and a one-shot bootstrap reader. The
bootstrap is a fixed 92-byte versioned grant containing only session/worker/case UUIDs and the
per-launch capability. It is not an argument, environment value, endpoint name, or log field.

The launch environment is constructed from a closed allowlist. The broker resolves the logical
`LocalAppData` Known Folder itself and derives `LOCALAPPDATA`, `TEMP`, and `TMP` from it rather than
inheriting user values; AppContainer virtualization maps those spellings once into the physical
per-launch profile that the broker independently authenticates and samples. That ephemeral sandbox
storage is writable so Python can start; it is not case authority. The launcher
authenticates a bounded runtime/code/fixed-tool inventory and grants the profile SID direct,
non-inheritable read/execute access only on those exact objects. Each file has one current-user
hardlink guard shared by authenticated overlapping v2 leases. A protected versioned journal
supports legacy-v1 inherited-root cleanup and v2 exact-object/SID/guard-baseline recovery after a
crash. Mutable Python bytecode caches are excluded from new authority, and every worker generation
uses a freshly authenticated empty `AC\Temp\PyCache` namespace, a raw logical `pycache_prefix`, and
`-B`, so an ambient valid source-tree cache is neither read nor replaced. Old records remain
exact-identity fail-closed because a missing/replacement cache path does not prove that the old
object was not renamed with residual authority. The exact job tree exits before a live lease's
grants, final shared guard, and profile are removed. See the
[worker lifecycle contract](../architecture/worker-lifecycle.md) and
[Windows isolation ADR](../adr/0004-windows-worker-isolation.md).

These mechanisms define the intended boundary but do not replace evidence. Required Windows tests
must demonstrate real IPv4/IPv6 TCP and UDP denial for the worker and descendants, whole-tree kill,
minimal environment, exact handle inheritance, denial of path-based case/external/user-temp access,
and success only for deliberately brokered handles. A required skipped or simulated probe leaves
PWS-0107 or the consuming gate open.

### Implemented PWS-0105 PDF handle boundary

The first real document operation is deliberately one-shot. Storage registers an already sealed
PWS-0104 pending PDF and lends an independently positioned handle only when the complete path-free
summary and retained object identity/size/type/link/reparse/sparse/stream checks still match. The
worker client duplicates reduced read-data/read-attributes/synchronize rights into a fresh ready
generation. It also duplicates only write-data/synchronize rights for a new bounded anonymous
result pipe. Both child copies are non-inheritable; the broker closes its local writer and grants no
case directory ACL.

The worker copies and rehashes the expected bytes into bounded AppContainer-private temporary
storage, closes the exact case handle, and only then invokes pikepdf/libqpdf. The parser reports
closed counts/states/markers and discards library objects, warning strings, command output, and raw
exceptions. Its correlated result frame is capped at 4,132 bytes. RPC success is insufficient on
its own: the trusted broker terminates and confirms the one-shot job tree, reads through pipe EOF,
and validates frame length/version plus operation/source UUIDs before accepting the typed result.

Cancellation, deadline, protocol/worker failure, invalid frame, and success all terminate the
generation before the source borrow and pipe are released. The broker never remotely closes a
worker handle number because the target can reuse it. This PWS-0105 evidence is limited to one
pending-source reader and one result writer. See
[DF-PDF-INSPECTION](data-flows.md#df-pdf-inspection--broker-inspect-correlate-and-revoke) and the
[PDF structural-inspection contract](../architecture/pdf-structural-inspection.md).

PWS-0108 separately proves one current committed-PDF reader and one fixed-size volatile-output
section writer. The broker authenticates source and cache authority, but duplicates only source
read plus section map-write rights. The worker receives no persistent cache-file handle; its
logical output is exact-capped by the adapter and its page-rounded volatile backing remains bounded
to less than one memory page of slack. After confirmed one-shot
worker-tree termination, the broker validates the fixed raw-RGBA artifact and alone writes and
publishes the cache entry. The worker still cannot access an originals/cache directory, manifest,
journal, export, destination, or path. Unconfirmed termination leaves the broker-only persistent
stage empty and safely disposable; quarantine remains reserved for indeterminate publication. See
[DF-PREVIEW](data-flows.md#df-preview--render-bounded-thumbnailspreviews) and the
[bounded PDF-thumbnail service contract](../architecture/pdf-thumbnails.md). Other authority
profiles remain inaccessible until their owning tasks provide separate evidence.

### Implemented PWS-0207 advisory review boundary

PWS-0207 extends the existing TB-02/TB-03/TB-04 worker boundary with one narrowly registered
`ocr.review` version-1 capability. Its explicit quality review first reuses the authenticated
preview flow and receives an already validated, fixed-layout opaque RGBA8 plane. Pure
application/domain logic calculates bounded integer evidence from that plane; it does not parse or
decode a PDF, JPEG, or PNG in the desktop process. The OCR broker grants a fresh one-shot worker
only read access to an exact bounded volatile sample section and write access to one bounded result
pipe. It grants no document/source handle, case path, document object, cache/persistent-stage
writer, external destination, or publication authority. The worker may start only the admitted
local Tesseract executable, under the existing zero-capability AppContainer, Job, network denial,
process, output, memory/CPU/time, and private-temp controls.

Page work is serial. The preview/analyzer releases page-scoped sample and luminance state before
the next page, while the OCR broker/worker may hold bounded transient copies of the same current
sample, one private PPM input, and at most 1 MiB of shared stdout/stderr capture. Private TSV is
parsed for numeric aggregate facts and discarded; recognized bytes and geometry never cross the
report pipe. The complete one-shot worker tree terminates before the broker reads and validates the
object-free report. Small prior-page fingerprints live only for the duration of one call and are
treated as case-confidential AS-04 values even though they are not persisted. No quality-specific
cache or authoritative artifact is created.

The accepted output is an AS-05 typed snapshot bound to one case, saved manifest revision, exact
ordered page tuple, profile identity, and closed findings. Operation/generation and unchanged-state
checks reject late or cross-revision output before display. The snapshot is presentation state, not
AS-03 manifest/history/checklist authority, and a state change clears it. Closed wording preserves
AS-12: every finding is only likely, suspected, or in need of review, while no-signal output is not
a certification. The OCR aggregate is bound to operation/case/revision/page/sample/presentation,
the closed selected session language/profile, terminal state, word count, confidence, and engine
version; recognized text is not accepted or retained. Optional OCR admission is not a backend
startup gate. The first explicit review adopts the authenticated composition after control checks;
fixed-work local admission has no elapsed-time guarantee and is rechecked afterward. Only an
expected local-unavailable result is latched for that application session; authority, isolation,
launch, process, transport, protocol, resource-limit, cleanup, and malformed-result failures abort
the review. Restart creates a fresh authority/admission attempt. See
[DF-QUALITY-REVIEW](data-flows.md#df-quality-review--derive-ephemeral-advisory-findings) and the
[advisory quality-review contract](../architecture/quality-review.md).

## Mandatory mitigation principles

1. Preserve originals: external sources are never opened for writing, and every output uses a new
   derivative path.
2. Quarantine before complex parsing: only bounded signature/size checks occur before a random,
   case-scoped pending copy exists; the desktop process never parses full untrusted documents.
3. Minimize worker authority: use a zero-capability AppContainer, minimal constructed environment,
   fixed executables/resources, capability-bound RPC, and retained Job Object for whole-tree
   limits/kill/restart. A trusted broker alone may grant an exact authenticated read-only input or
   operation-specific output handle and publish source records, manifest/journal revisions, and
   exports. A logical case binding or path is never filesystem authority.
4. Bound all work: bytes, pages, dimensions, pixels, decompression, nesting, memory, CPU/time,
   temporary storage, messages, and captured child output.
5. Treat every boundary result as untrusted: validate worker output, canonical bounded preview
   buffers, generated files, paths, archive entries, tool results, manifests, templates, and
   update metadata before use. Validate and consume the same opened handle/snapshot.
6. Commit only verified state: manifest/source records and exports become authoritative only after
   their complete validation succeeds; previous valid state remains intact on failure.
7. Keep content out of routine diagnostics: use internal IDs, counts, versions, durations,
   resource values, stable codes, and sanitized stacks only.
8. Separate wording from proof: visual marks are not redaction, visual signature images are not
   digital signatures, and machine checks do not imply legal or portal acceptance.
9. Make cleanup scope provable: resolve internal IDs beneath one case root and reject traversal,
   device, ADS, reparse, symlink, hardlink/file-identity alias, archive, remote-location, and
   destination escapes before mutation and again at the opened handle.
10. Authenticate shipped code and updates: pin, inventory, hash/sign, review licenses, prevent
    rollback where policy requires it, and keep update networking separate from processing.

### Optional application updates

PWS-0803 treats the download source, package and accompanying metadata as untrusted until
verification against a product- and channel-specific authority provided independently of the
offer. The local verifier checks a detached signature over exact bounded bytes, validates the
trusted channel and freshness policy, and checks the signed size and SHA-256 of the supplied
package stream. It does not open local files, choose its trust authority, install an update or
make a network request. The proposed installation flow adds Windows publisher verification and
binds the verified bytes to what actually runs. [Update trust and recovery design](../adr/0022-authenticated-application-updates.md).

A distributor may substitute or withhold an offer. A compromised signer may issue fresh-looking
offers or try to establish a replacement key. The update must reject unauthorized signing keys,
expired or forged metadata, incorrect channel/target and replay at or below the trusted sequence
floor. A compromised key needs a separate recovery authority. The issuer must never learn case
or document data. If the installer fails before, during or after switching versions, the exact
working build must remain launchable or recoverable without losing case state or weakening
normal downgrade policy. Those installation and recovery controls need native packaged evidence.

## Threat catalogue

Impact describes the worst credible confidentiality, integrity, availability, or user-safety
effect before mitigations. Every threat is mapped in
[threat-traceability.md](threat-traceability.md).

| ID | Threat scenario | Impact | Required security outcome |
|---|---|---|---|
| TM-001 | A malformed PDF/image exploits a parser, renderer, OCR engine, or inspection library | High | Compromise is contained to a restartable, network-denied worker with only exact brokered case handles; severe faults terminate its whole job tree |
| TM-002 | Extreme size, page count, pixels, compression, recursion, memory, CPU, or temp use exhausts the host | High | Prechecks and worker quotas stop bounded work with a stable recoverable error |
| TM-003 | UI/application code directly parses complex document content or reaches low-level engine APIs | High | Import boundaries and process architecture keep full parsing in worker adapters |
| TM-004 | A compromised worker or tool sends document/case content over the network | High | Processing egress is denied and tested; update networking is a separate component |
| TM-005 | Import, editing, or export modifies/overwrites an external original or immutable case copy | High | Sources are read-only, copied/hashed once, integrity-checked, and never export targets |
| TM-006 | Traversal, device/UNC paths, ADS, symlink/reparse/hardlink aliases, permissions, or random-name failures escape/expose a case | High | Local-location policy, opened-handle identity/containment, random internal names, restrictive permissions, and case-scoped cleanup fail closed |
| TM-007 | A partial/corrupt manifest, journal, migration, or rollback becomes authoritative | High | Version/schema validation and atomic transactional recovery preserve the last valid state |
| TM-008 | A hostile local peer or spoofed, malformed, oversized, replayed, cross-case, or incompatible RPC message controls the worker/client | High | Explicit inherited pipes plus a one-shot per-launch grant bind versioned envelopes to worker/case/operation/sequence; limits, replay rejection, and protocol-fault termination fail closed |
| TM-009 | Filenames, template/OCR/document text, environment data, arguments, working directory, or search paths inject commands or substitute code/resources | High | Fixed approved executables/resources, working directory, search paths, and argument arrays run without a shell in a minimal environment; limits/egress denial cover descendants |
| TM-010 | A template executes code, dereferences files/network, escapes paths, impersonates trusted guidance, or silently makes eligibility decisions | High | Templates are declarative schema-validated data with visible provenance/trust state; unsafe/future/tampered content is rejected before case mutation |
| TM-011 | Routine logs, errors, application-controlled crash reports, or metrics disclose text, values, names, paths, metadata, thumbnails, or signatures | High | Application diagnostics contain only the documented allowlist and pass prohibited-token tests; platform artifact limitations are disclosed |
| TM-012 | Temp files, caches, OCR output, thumbnails, or abandoned partial outputs persist or cross case boundaries | High | Random case scope, quotas, lifecycle cleanup, crash recovery, and explicit retention state contain leftovers |
| TM-013 | Shared-computer access, stale sessions, or unclear retention exposes another person's case | High | Restrictive case access, consent, lock, expiry, handover, and explicit deletion controls protect assisted use |
| TM-014 | A truncated, corrupt, wrong-page, unrenderable, encrypted, or partial export is reported as successful | High | New staging output is reopened, parsed, structurally/page/policy checked, rendered, then exact size/digest bytes are committed through a destination-local temporary file |
| TM-015 | Destination races, traversal, reserved names, aliases, network semantics, collisions, overwrite, or ZIP entry names escape the intended output | High | Destination policy, opened-handle identity, entry normalization, containment, collision policy, rechecks, and no-original/no-good-output overwrite |
| TM-016 | Editing silently invalidates a digital signature or a visual mark is presented as cryptographic/qualified signing | High | Detect signatures before change, require derivative warning, preserve original, and label output unsigned |
| TM-017 | A visual cover or flawed removal is represented as permanent redaction while content remains recoverable | High | Redaction stays disabled until M6 reconstruction, multi-method verification, export blocking, and independent gate |
| TM-018 | A diagnostic bundle includes prohibited content, changes after preview, or is created/uploaded without informed opt-in | High | Freeze and digest the exact allowlisted entry snapshot, preview/confirm it, write only that snapshot locally, and scan produced bundles for canaries |
| TM-019 | Deletion removes data outside one case, reports false forensic erasure, or leaves an ambiguous partial state | High | Resolve by internal case ID, inventory/confirm scope, report partial failure, and state erasure limitations |
| TM-020 | A dependency, CI/build action, executable, font, language pack, build input, or release artifact is compromised | High | Admission, pinning, hashes/signatures, licensing, inventory, SBOM, provenance, and release verification are gated |
| TM-021 | An update is unsigned, tampered, stale, for the wrong channel or product, replayed, raced, interrupted, signed with a compromised key, or leaks case data | High | Verify independently trusted metadata and exact package bytes, separate update trust from cases, require a separate revocation/recovery authority, enforce freshness and version policy, and preserve a working installation on failure |
| TM-022 | Worker/tool output or raw third-party exceptions corrupt state, steer paths, or leak hostile values to UI/logs | High | Typed result validation and stable sanitized error mapping precede any display or mutation |
| TM-023 | A source, case/cache file, template, preview, or destination changes or aliases protected data between validation, reading, hashing, display, and commit | High | Same-handle file-identity/digest checks, hardlink/reparse defenses, immutable snapshots, transactional staging, and terminal rechecks detect races |
| TM-024 | Automation or wording silently marks requirements complete or overclaims OCR, validity, privacy, or acceptance | Medium | User confirmation and concrete limited claims remain authoritative; heuristic results say “Needs review” |
| TM-025 | Scripts, XFA, actions, attachments, encryption, forms, archives, or incremental revisions hide active/retained content | High | Inspect conservatively, report unknown states, block/flatten/remove only under verified profiles, and use safe ZIP names |
| TM-026 | Original filenames, metadata, display names, signature assets, summaries, or internal names disclose identity/content | High | Random storage names, manifest-only display names, metadata policy, intended-output review, and log prohibition |
| TM-027 | Compression or grayscale policy silently makes text, stamps, signatures, or images illegible merely to meet a size target | High | Staged previewable policy stops at a measured legibility floor and reports unsafe or unattainable targets for user review |
| TM-028 | Real applicant or sensitive support material enters fixtures, repository history, CI artifacts, or routine bug reports | High | Synthetic/anonymized corpus policy, provenance review, secret/content scans, and an explicit out-of-repository sensitive-sample process are enforced |

## Misuse and abuse cases

| Case | Expected product behavior |
|---|---|
| A user renames an executable/container to `.pdf` | Detect content, reject unsupported type, and never invoke it |
| A PDF opens in a viewer but expands into extreme images/objects | Enforce precheck and worker limits; explain that the file exceeds supported processing limits |
| A template includes a command, expression, import, or executable/file `..` path | Reject the template as invalid data before it affects the case |
| A template includes a schema-permitted provenance URL | Keep it inert, show its trust/provenance role, and never dereference it in the loader |
| A filename contains shell metacharacters, a device name, separators, or an archive escape | Treat it as data, normalize/reject it, and never build a shell command |
| A signed PDF is rotated, OCRed, filled, or combined | Preserve the source, warn before change, and call the result an unsigned derivative |
| A black rectangle covers text before M6 | Treat it only as an ordinary shape; never claim information was removed |
| Export verification fails after a previous successful export | Keep the previous verified export and report the failed attempt without replacing it |
| A helper requests diagnostics for support | Preview the exact safe inventory; never ask for applicant documents or content-bearing routine logs |
| A user deletes a case on an SSD/shared computer | Delete only the scoped case after confirmation and do not claim forensic erasure |
| A worker crashes or violates protocol | Terminate/restart it, preserve the last valid manifest/originals, and present a stable recovery action |
| An update service is unavailable while a case is open | Continue core work offline; processing never depends on update connectivity |
| OCR/classification looks confident | Present a review signal; only the user confirms substantive completeness |
| A target-size request would cross the legibility floor | Stop and explain that the target is unsafe or unattainable; do not silently degrade further |
| A bug report can be reproduced only with a sensitive document | Keep it outside the repository/CI and use the approved restricted handling process or a synthetic minimization |

## Residual-risk and review rules

- This model records required controls, not their completion. The linked task or gate remains the
  evidence authority.
- A mitigation that cannot be demonstrated must leave the consuming task or milestone gate open;
  documentation alone is not evidence.
- Unknown inspection or verification state is not equivalent to absence or success.
- Parser compromise can expose every exact object handle deliberately brokered to that operation
  and the worker's ephemeral AppContainer storage. Handle minimization, network denial, job-tree
  termination, updates, and isolation reduce blast radius rather than promising perfect sandboxing.
- SHA-256 records detect accidental or unsanctioned source changes under the application model;
  they do not authenticate state against malware able to rewrite both case files and manifests.
- Random names and filesystem ACLs reduce accidental disclosure and peer-process access but are
  not authorization against unrestricted same-account or administrator access.
- Application cleanup governs application-created files. OS crash dumps, swap, filesystem
  journals, indexers, antivirus products, backups, and synchronized folders can retain platform
  artifacts; v0.1 provides best-effort, user-driven deletion and makes no forensic-erasure claim.
- Template schema validity proves safe structure, not authenticity or suitability. PWS-0401 binds
  exact bytes to a trusted local catalog checksum/signature status before decoding, but cannot
  authenticate an attacker able to rewrite both application and catalog. Embedded provenance stays
  labeled as a claim; external trust state, lifecycle dates, and review-due state remain visible.
- Resource-limit values are provisional until owning tasks measure and record boundary evidence.
  Incomplete worker isolation or egress denial is gate-blocking, not an accepted residual risk.
- Signed-source detection is not cryptographic signature validation and may return unknown.
- Supply-chain controls are staged: admission begins in PWS-0005 and packaged inventory/SBOM and
  update evidence mature in PWS-0801 through PWS-0808. Until then, dependency compromise remains
  residual exposure; the project does not claim continuous vulnerability monitoring.
- PWS-0107 selects AppContainer, suspended process creation, explicit inherited pipes, and a Job
  Object for the worker. Their live egress, environment, filesystem, descendant, and cleanup
  behavior must be re-evidenced for packaged builds. PWS-0105 proves only its exact prepared-PDF
  read/result-pipe brokerage; every later case object and access profile remains unclaimed until
  separately implemented and tested. Installer primitives remain future work.
- M6 extends this model with redaction-specific adversarial evidence. M8 extends it for the final
  updater, installer, dependencies, migrations, diagnostics, and release artifacts.

Review this model whenever a trust boundary, input/output type, parser, external executable,
template capability, diagnostic field, update mechanism, storage location, or supported security
claim changes.
