# Worker RPC protocol v1

`PWS-0008` defines the structured contract between the trusted PaperWorkShell client and one
case-scoped document worker. The authoritative structural schema is
[`schemas/worker-rpc-v1.schema.json`](../../schemas/worker-rpc-v1.schema.json). It is a closed,
self-contained JSON Schema Draft 2020-12 resource and is always evaluated offline.

This is a message contract, not a transport or sandbox implementation. PWS-0107 implements
framing, worker creation, capability provisioning, network denial, process limits, termination, and
cleanup without changing the released v1 envelope. PWS-0105 adds the first exact opened-handle
operation through the existing typed operation registry. PWS-0108 adds the exact one-page
`pdf.thumbnail` operation and staging-output reference under the same additive-operation rules.
PWS-0201 adds the exact `image.normalize` operation and bounded volatile artifact. None of these
extensions adds paths or shell command text to messages. PWS-0202 adds a separate exact
`image.transform` operation; it does not widen `image.normalize` version 1. PWS-0204 adds the
separate `ocr.process` operation for one bounded volatile OCR PDF candidate. PWS-0206 adds the
separate `pdf.export.searchable` operation for one verified mixed PDF plus a bounded content-free
page-status document; it changes neither `pdf.export` version 1 nor `ocr.process` version 1.
PWS-0207 adds the separate handle-only `ocr.review` operation for one bounded current-page raster
and an out-of-band content-free result; it changes neither `ocr.process` nor the generic envelope.
See the [worker lifecycle contract](../architecture/worker-lifecycle.md),
[PDF structural-inspection contract](../architecture/pdf-structural-inspection.md), and
[bounded PDF-thumbnail service contract](../architecture/pdf-thumbnails.md), and
[bounded JPEG/PNG intake contract](../architecture/image-intake.md), and
[advisory page-quality review contract](../architecture/quality-review.md), and
[verified searchable mixed-output contract](../architecture/searchable-mixed-output.md).

## Common envelope

Every message contains every envelope member. Objects are closed; an unknown member, message type,
version, enum value, or payload shape fails before application dispatch.

| Member | v1 rule |
|---|---|
| `protocol` | Exact string `paperworkshell.worker-rpc` |
| `protocol_version`, `message_version` | Exact integer `1` |
| `message_type`, `direction` | Closed enums; direction is also fixed per message type |
| `session_id`, `worker_instance_id`, `case_id`, `message_id` | Canonical lowercase UUIDv4 values |
| `session_capability` | Per-launch 256-bit capability encoded as 64 lowercase hexadecimal characters |
| `sender_sequence` | Sender-local integer from zero through JavaScript's maximum safe integer |
| `request_id`, `operation_id` | UUIDv4 correlation IDs or `null` only where the message contract permits |
| `payload` | Closed object selected by `message_type` |

The capability secret is authentication material, unlike the public named capability descriptors in
negotiation. It must be generated for one launch, compared without content-dependent early exit,
redacted from object representations, and omitted from logs and diagnostics. The all-zero value in
the examples is synthetic test data and must never be provisioned in production.

Session messages carry a non-null request ID and a null operation ID. All `operation.*` messages
carry both IDs. `protocol.error` may use null for either correlation ID when malformed input cannot
be correlated. `worker.fault` may correlate a request, but its operation ID is null because its
required `invalidated_operation_ids` array reports every affected operation. A protocol error may
travel in either direction; all other directions are fixed by the schema.

## Negotiation

The trusted broker creates one case-bound session, worker-instance ID, and capability secret before
messages are exchanged. Negotiation then proceeds exactly as follows:

1. The client sends `session.hello` as sender sequence zero. Its
   `supported_protocol_versions` is non-empty, unique, and must contain `1`; its
   `required_capabilities` lists exact `(capability_id, capability_version)` pairs.
2. The worker validates the complete envelope, secret, case/session/instance binding, sequence, and
   hello payload before selecting anything. Protocol v1 has no implicit downgrade or best-effort
   interpretation.
3. A compatible worker responds to the same request with `session.ready` as its sender sequence
   zero. It selects protocol version `1`, reports a unique capability list, advertises at most
   65,536 message bytes, and advertises exactly one in-flight operation.
4. Every reported capability must exist in the client's explicit local operation registry, and
   every required capability must appear with the exact required version. Unknown worker-advertised
   names are rejected before UI or logging. No ready response means no operation may start. An
   incompatible or malformed negotiation produces only a sanitized protocol failure and the session
   is not usable.

The initial foundation registry contained `protocol.noop` version 1. Its canonical start has empty
`inputs` and `parameters`; its only progress stage is `processing`. It proves version negotiation,
typed round trips, state, and cancellation without parsing a document.

PWS-0105 adds `pdf.inspect` version 1. Negotiation still requires the client to request that exact
descriptor and the worker to advertise it from its local registry. Its start has exactly one
`source_read` input with content kind `pdf`, a non-null expected byte size and SHA-256, plus exactly
two distinct positive integer parameters named `source_handle` and `result_handle`. Those integers
are worker-local values created by the trusted Windows broker after ready; the corresponding kernel
handles carry reduced read-source and write-result-pipe access. They are not paths, caller-selected
object identifiers, or a general handle namespace. The operation reports only the existing
`processing` stage and returns no protocol output reference.

PWS-0108 adds `pdf.thumbnail` version 1. Its start has exactly one authenticated PDF
`source_read` input; two distinct positive worker-local handle parameters; source-page index,
bounded maximum width/height, CropBox/media test-profile token, and quarter-turn rotation
parameters; plus distinct internal page and cache-object UUIDs. A succeeded result has exactly one
bounded `operation_staging` output with content kind `pdf-thumbnail-rgba`, exact operation/cache
correlation, byte size, and SHA-256. Other outcomes have no outputs. The worker reports this
capability only when the hello requested it, so adding it does not break an older peer's ready
allowlist.

PWS-0201 adds `image.normalize` version 1. Its start has exactly one authenticated JPEG or PNG
`source_read` input; distinct positive worker-local source and output-section handle parameters;
a distinct output UUID; bounded maximum width/height; and a quarter-turn of 0, 90, 180, or 270
degrees. A succeeded result has exactly one bounded `operation_staging` output with content kind
`image-normalized-rgba-v1`, exact operation/output correlation, byte size, and SHA-256. The worker
receives no path or persistent cache authority. Other outcomes retain the released generic v1
failure/timeout contract and have no outputs.

PWS-0202 adds `image.transform` version 1. Its start has the same single authenticated JPEG or PNG
source and reduced source/output handles, but carries a closed integer transform intent: normalized
crop millionths, accumulated clockwise quarter turns, signed deskew millidegrees, contrast factor
millionths, background-cleanup strength millionths, and bounded output dimensions. A succeeded
result contains one `image-transformed-rgba-v1` staging artifact whose header binds the canonical
intent, engine profile, cache key, operation/output identities, layout, and pixel digest. The
worker receives no manifest, path, external-source writer, or persistent cache authority.

PWS-0204 adds `ocr.process` version 1. Its start has exactly one authenticated PDF, JPEG, or PNG
`source_read` input; distinct positive worker-local source, output-section, and result-pipe handle
parameters; a distinct output UUID; a bounded page count; and opaque trusted language-set and OCR
profile identifiers. A succeeded result has exactly one bounded `operation_staging` output with
content kind `ocr-pdf-candidate-v1`, exact operation/output correlation, byte size, and SHA-256.
The separate canonical result frame carries only IDs, counts, fixed profile/version tokens, and
digests. It carries no recognized text, filename, path, raw engine message, or command argument.

PWS-0207 adds `ocr.review` version 1. Its start has no logical `inputs` and exactly two distinct
positive worker-local handle parameters: a read-only bounded volatile `sample_handle` and a
write-only `result_handle`. The remaining closed parameters bind one page UUID, manifest revision,
RGBA8 layout/byte count, `sha256-`-prefixed sample and presentation digest tokens, one of the three
registered OCR language-set tokens, and exact profile `quality-ocr-review-v1`. It accepts one page
with at most a 256-pixel edge and 65,536 pixels. A terminal protocol result has no output objects;
the separate bounded canonical report pipe carries only correlated IDs/digests, closed
configuration/version/status, recognized-word count, and optional integer mean confidence. It
carries no raster, OCR text, geometry, path, filename, metadata, or native tool message.

PWS-0206 adds `pdf.export.searchable` version 1. Its start has one authenticated canonical plan
followed by one or more authenticated PDF, JPEG, or PNG `source_read` inputs; four distinct
positive worker-local plan, output-section, status-section, and result-pipe handles; distinct PDF
and status output UUIDs; and a bounded status capacity. A succeeded result has exactly two ordered
`operation_staging` outputs: `searchable-pdf-v1`, then `searchable-pdf-page-status-v1`. The
separate bounded canonical report frame correlates both outputs and embeds only the base structural
verification report plus fixed engine/profile facts. No document or OCR text crosses this
boundary.

## Messages and lifecycle

| Message | Direction | Payload |
|---|---|---|
| `session.hello` | client to worker | `supported_protocol_versions`, `required_capabilities` |
| `session.ready` | worker to client | `selected_protocol_version`, `capabilities`, `max_message_bytes`, `max_in_flight_operations` |
| `operation.start` | client to worker | `operation_kind`, `operation_version`, `inputs`, `parameters`, `requested_limits` |
| `operation.accepted` | worker to client | matching operation kind/version and `effective_limits` |
| `operation.progress` | worker to client | registered stage `processing`, integer `completed_units`, integer-or-null `total_units`, and a unit |
| `operation.cancel` | client to worker | reason `user_requested`, `shutdown`, or `superseded` |
| `operation.cancel_ack` | worker to client | `requested`, `already_requested`, or `already_terminal` |
| `operation.result` | worker to client | terminal outcome, bounded outputs, and outcome-dependent error |
| `session.shutdown` | client to worker | shutdown reason |
| `session.closed` | worker to client | close reason |
| `protocol.error` | either | one protocol-category sanitized error |
| `worker.fault` | worker to client | one restart-required sanitized error and affected operation IDs |

Each direction starts at sender sequence zero and increments by exactly one. Message IDs are unique
within the session. A duplicate ID, repeated sequence, gap, wrong sender, or cross-session/case/
worker binding is a protocol violation; messages are never replayed for side effects. Request IDs
correlate a request and its direct response. Operation IDs remain stable from start through the one
terminal result, while a cancellation request and acknowledgement use their own request ID.

After ready, v1 permits at most one active operation. A valid start transitions that operation from
requested to accepted; progress is optional and never terminal. When `total_units` is known,
`completed_units` cannot exceed it. Exactly one `operation.result` terminates the operation:

- `succeeded` has a null error and may return verified staging outputs;
- `cancelled` has no outputs and a null error; and
- `failed` or `timed_out` has no outputs and exactly one sanitized error.

Cancellation is cooperative and race-safe. `requested` or `already_requested` acknowledges intent,
not completion; the eventual terminal result is authoritative and may still be `succeeded` if work
won the race. `already_terminal` reports that the result won the race. Unknown operation IDs are
protocol violations rather than acknowledgements that create state. A cancel acknowledgement is
not a terminal result. Shutdown stops accepting new work and proceeds to `session.closed` with the
correlated reason `shutdown_complete`; unsolicited close messages are protocol violations. A
protocol error or severe worker fault terminates the session and requires a new worker. Session
state, sequences, and capabilities are never resumed or replayed across worker restart.
A failed or timed-out operation result whose error requests `restart_worker` also faults the
session immediately; a non-restart result leaves the negotiated session ready for later work.

## Two-stage operation validation

Validation is intentionally split:

1. The envelope/schema layer accepts only the bounded v1 grammar and typed payload families.
2. The operation registry selects an exact `(operation_kind, operation_version)` codec and applies
   its input, parameter, output, limit, and state rules before dispatch.

Consequently, a structurally valid but unregistered operation fails closed as unsupported. The
registered pairs are now `protocol.noop` version 1, `pdf.inspect` version 1, `pdf.thumbnail`
version 1, `pdf.export` version 1, `image.normalize` version 1, `image.transform` version 1,
`ocr.process` version 1, `ocr.review` version 1, and `pdf.export.searchable` version 1.
Each document operation's exact source reference, handle parameters, correlations, and
limits are validated before host dispatch; any missing, extra, mistyped, nonpositive, aliased, or
out-of-policy value fails closed.

Adding an operation does not loosen generic dictionaries: it requires a named capability, a
versioned typed codec, semantic validation, positive and negative fixtures, and bounded adapter
tests. A changed operation meaning receives a new operation version.

## Logical case-object references

RPC messages never contain a drive, UNC path, device path, relative path, filename, URI, command,
environment value, or shell fragment. A `CaseObjectReference` carries only version 1, an opaque
object UUID, a closed area, an operation scope, a safe content-kind token, and optional expected
byte size/SHA-256 integrity values.

The allowed areas are `source_read`, `asset_read`, `operation_cache`, `operation_temp`, and
`operation_staging`. Source and asset references always have a null reference operation ID;
operation-owned references always have a UUID. Start inputs are limited further to source/asset
read references. Successful result outputs are limited to `operation_staging`, must name the
envelope's operation ID, and must include both expected byte size and SHA-256. Failed, timed-out,
and cancelled operations return no outputs.

Schema validity does not authorize a file. PWS-0105's trusted prepared-source broker is the first
implementation: it matches the complete sealed-source summary, reopens the retained exact object
without a path lookup, revalidates identity/type/size/link/reparse/sparse/alternate-stream state,
and duplicates only reduced read rights into the worker. Its separate result pipe grants only
write rights. PWS-0108 resolves a current committed-page mapping and exact source handle, reserves
one broker-only random cache stage, and creates a fixed-size pagefile-backed output section
before it emits the corresponding logical references. No reference, identifier, key, or integer
substitutes for those retained kernel-object authorities. Future reference areas still require
their own trusted resolution, containment,
object type, size/digest, alias/race, quota, lifetime, and access checks before use.

### `pdf.inspect` out-of-band result

The ordinary `operation.result` is still the sole protocol terminal message. For a succeeded
`pdf.inspect`, it has no outputs and only establishes that the host handler wrote and closed one
out-of-band result frame before returning. The broker terminates the one-shot worker tree, then
reads that frame through EOF and independently validates its length, report version, closed typed
fields, operation UUID, and source-object UUID. RPC success with a missing, malformed, oversized,
surplus, future, or misbound frame is failure.

The bounded frame contains no path or native object. Its two UUID correlations plus canonical
structural report are carried over the exact anonymous result-pipe handle created for this
operation. This specialized result channel does not change the released protocol-v1 message schema
and is not permission for another operation to invent an unregistered side channel.

### `pdf.thumbnail` cache-stage result

`pdf.thumbnail` version 1 uses the generic protocol output reference rather than the inspection
result pipe. Its exact start contains one authenticated PDF `source_read` reference and this closed
parameter set:

- positive distinct integer `source_handle` and `cache_handle`;
- UUID `cache_object_id` and `page_id`;
- non-negative integer `source_page_index`;
- positive `maximum_width_pixels` and `maximum_height_pixels` whose product fits
  `requested_limits.pixel_count`;
- token `page_box` equal to `crop` or explicit adapter/test profile `media`; and
- integer `additional_rotation` equal to 0, 90, 180, or 270.

The source-object, page, cache-object, and operation UUIDs are pairwise distinct. The source input
supplies expected size and SHA-256; handle values are assigned only by the trusted broker after
ready. `source_handle` carries reduced file-read rights. Despite its released compatibility name,
`cache_handle` carries only section map-write rights to an unnamed volatile section
created from `requested_limits.output_bytes`; the adapter enforces that exact logical cap, while
Windows may page-round volatile backing by less than one page. It is not a cache-file handle. These values are not
persistent IDs and never appear in a result or routine log.

On success, the sole output has area `operation_staging`, object ID equal to the requested cache
object, operation ID equal to the envelope operation, content kind `pdf-thumbnail-rgba`, and a
non-null exact size/SHA-256 within the operation output limit. The digest covers the complete fixed
header plus raw RGBA8 payload. Cancelled, failed, and timed-out results have no output.

The ordinary terminal result establishes only the worker's claim that it closed a complete
volatile output. The broker first confirms whole-tree termination, then copies only the claimed
bounded byte count through its read-only section view and verifies the output-reference digest and
every header/key/pixel correlation. Only then does the broker write those validated bytes to the
broker-only cache stage and ask storage for no-replace publication. An unconfirmed worker tree is
never allowed to populate persistent staging; its section remains volatile and fixed-size.
This uses existing protocol-v1 message and reference semantics; it does not add a result-byte field
or authorize the worker to choose a path.

### `image.normalize` volatile artifact result

`image.normalize` version 1 uses the same generic protocol output-reference semantics. Its exact
start contains one authenticated `source_read` reference whose content kind is `jpeg` or `png`,
exact byte count, and SHA-256, plus this closed parameter set:

- distinct positive integer `source_handle` and `output_handle` values;
- UUID `output_object_id`, distinct from the case, operation, and source UUIDs;
- positive `maximum_width_pixels` and `maximum_height_pixels` within the requested pixel limit; and
- integer `additional_rotation` equal to 0, 90, 180, or 270 degrees clockwise.

The broker duplicates only reduced file-read authority for the immutable committed source and
map-write authority for one fixed-size anonymous output section. Neither handle is a path or a
persistent case writer. The worker validates source type, size, digest, JPEG/PNG grammar, dimensions,
decompression, metadata, memory, temporary storage, and output bounds before returning success.

On success, the sole output has area `operation_staging`, object ID equal to the requested output
object, operation ID equal to the envelope operation, content kind `image-normalized-rgba-v1`, and
an exact non-null size and SHA-256 within the operation output limit. The digest covers the fixed,
versioned image-page header and its packed opaque RGBA8 payload. Cancelled, failed, and timed-out
results have no output and continue to use the released protocol-v1 terminal/error forms.

The broker accepts the output only after confirming whole-tree termination and then revalidates the
bounded artifact's version, operation/output IDs, source facts, dimensions, stride, byte counts,
orientation, color/alpha policy, pixel digest, and whole-artifact digest. Validated pixels may enter
only the GUI-owned byte-budgeted disposable preview cache; the worker never receives cache or
manifest publication authority. See [Image intake and page normalization](../architecture/image-intake.md).

### `image.transform` volatile artifact result

`image.transform` version 1 is an additive operation rather than a changed interpretation of
`image.normalize`. It accepts one authenticated JPEG or PNG `source_read` reference, distinct
positive `source_handle` and `output_handle` integers, distinct page and output UUIDs, bounded maximum
width/height, and these exact integer intent values: crop x/y/width/height in
`normalized_page_millionths_v1`, quarter turns in 0–3, deskew in −15,000–15,000 clockwise
millidegrees, contrast in 0–4,000,000 millionths, and background cleanup in 0–1,000,000
millionths. The four crop values must form a positive box inside the logical full page.

The worker reauthenticates the immutable source, applies the fixed transform profile, and writes
only to worker-private temporary rasters and the anonymous bounded output section. On success the
sole staging output has content kind `image-transformed-rgba-v1`; the artifact binds the exact
operation/page/output UUIDs, canonical transform-intent digest, engine-profile digest, cache-key digest,
output geometry, and pixel digest. Cancellation, timeout, malformed input, resource exhaustion,
and invalid output return no reference. The trusted broker confirms whole-tree termination and
revalidates every binding before exposing a path-free typed result.

### `ocr.review` handle-only content-free result

`ocr.review` version 1 is an additive one-page operation over an already validated current quality
raster. It has an empty `inputs` array: it receives no `CaseObjectReference`, source object, PDF,
JPEG, PNG, path, or persistent artifact authority. Its exact closed parameter set is:

- distinct positive integer `sample_handle` and `result_handle` values;
- UUID `page_id`, distinct from the envelope case and operation UUIDs;
- integer `manifest_revision` from zero through the protocol safe-integer maximum;
- positive integer `width_pixels` and `height_pixels`, each at most 256 and with product at most
  65,536;
- integer `row_stride_bytes` from `width_pixels * 4` through that value plus 4,096, and
  `sample_byte_size` exactly equal to `row_stride_bytes * height_pixels`;
- token `sample_sha256` and `presentation_sha256`, each exactly
  `sha256-` followed by 64 lowercase hexadecimal characters;
- token `language_set_id` equal to `latin-default-v1`, `german-fast-v1`, or
  `english-german-fast-v1`; and
- token `profile_id` exactly equal to `quality-ocr-review-v1`.

The broker creates the sample as a fixed-size anonymous pagefile-backed section and duplicates only
read authority; the result handle carries only bounded write authority to an anonymous pipe. The
requested limits require exactly one page, a pixel bound covering the sample, output bytes from one
through 1,048,576, memory covering the sample bytes, and temporary storage covering the sample,
one packed PPM (`32 + width * height * 3` bytes), and the full requested output bound. Production
uses the exact sample pixel count, a 1 MiB output cap, and a 4 MiB temporary-storage cap. Progress,
when present, is only `processing/pages` with exact total one and completed units zero or one.

Every terminal `operation.result` is object-free: `outputs` must be empty even on success. The
worker instead writes one canonical ASCII report of at most 8,192 bytes to the result pipe. That
private frame binds report version, operation/case/page UUIDs, manifest revision, raw 64-hex sample
and presentation digests, the exact language/profile tokens, Tesseract version, terminal
`succeeded`/`no_text`/`failed` status, recognized-word count from zero through 32,768, and optional
integer mean confidence from zero through 100,000 milli-units. `succeeded` requires at least one
word and a confidence value; `no_text` and `failed` require zero words and null confidence. No
text, word boxes, geometry, page raster, path, filename, metadata, command, stderr, or native
exception is representable.

The broker terminates the complete one-shot worker Job before it reads the frame, then validates
every request/report binding. Cancellation, timeout, malformed/oversized report, wrong digest,
wrong configuration, output object, capability loss, or uncertain termination exposes no fact.
An unavailable engine/capability becomes an application-level failed fact; missing selected
language or recognition failure can return a correlated `failed` report that becomes the same
advisory fact. Neither path claims that recognition inspected text or that text is correct.
An older peer that does not request and advertise `ocr.review` never negotiates it.

### `ocr.process` volatile PDF-candidate result

`ocr.process` version 1 accepts one exact immutable PDF, JPEG, or PNG source reference plus this
closed parameter set:

- distinct positive integer `source_handle`, `output_handle`, and `result_handle` values;
- UUID `output_object_id`, pairwise distinct from the case, operation, and source UUIDs;
- positive `page_count`, equal to the requested page limit and equal to one for JPEG/PNG; and
- token `language_set_id` and `profile_id` values selected from trusted local configuration.

The source bytes plus the complete output bound must fit the operation temporary-storage limit.
The worker receives reduced read authority for the source, map-write authority for one fixed-size
anonymous output section, and write-only authority for one bounded report pipe. It receives no case
path, manifest handle, cache writer, external destination, raw OCR text sink, language path, or
caller-supplied command fragment.

On success, the staging output names `ocr-pdf-candidate-v1` and binds the operation/output UUID,
exact size, and digest. A separate canonical report frame binds those facts to source UUID/kind,
page counts, opaque profile IDs, fixed OCRmyPDF/Tesseract version tokens, and an engine-profile
digest. The broker terminates the complete worker Job—including its sole Tesseract descendant—then
re-hashes the retained original, validates the report and output bytes, and only then exposes a
closeable volatile candidate. PWS-0204 does not publish it, mutate the manifest, claim that it is a
final searchable export, or override signed-source review; those are later gated workflows.

The worker launches Tesseract only through its fixed authenticated absolute executable, an argument
array, a minimal environment, a fixed tessdata root, and `shell=False`. The Job permits exactly the
Python worker and one Tesseract descendant. OCR progress remains numeric `processing/pages`; the
broker translates it into content-free prepare/recognize/validate/complete application phases.
Cancellation, timeout, malformed output, engine failure, or uncertain cleanup returns no reference.

### `pdf.export.searchable` mixed-output result

`pdf.export.searchable` version 1 accepts an authenticated canonical plan input with content kind
`searchable-pdf-export-plan-v1`, followed by one or more exact immutable PDF, JPEG, or PNG source
references. The plan is limited to 2,097,152 bytes and binds the case/manifest facts, semantic
fingerprint, source identities and digests, output page order, transform intent, signature and
encryption states, fixed eligibility/text/OCR profiles, and the reduced source handles. Its exact
start parameter set is:

- distinct positive integer `plan_handle`, `output_handle`, `status_handle`, and `result_handle`
  values;
- distinct UUID `output_object_id` and `status_object_id`, neither aliased with a case, operation,
  plan, or source identity; and
- positive `status_capacity_bytes` no greater than 2,097,152 bytes.

The authenticated source total, requested PDF-output capacity, and page-status capacity must fit
the requested temporary-storage limit. The worker receives read authority for the plan and exact
sources, map-write authority for two fixed-size anonymous output sections, and write-only authority
for one report pipe. It receives no manifest/history writer, cache publisher, case root, external
destination, path, filename, extracted-text sink, or caller-supplied command fragment.

On success, `operation.result` contains exactly two outputs in fixed order. The first names
`searchable-pdf-v1`; the second names `searchable-pdf-page-status-v1`. Both use
`operation_staging`, bind their requested object and operation UUIDs, and include an exact bounded
byte size and SHA-256. The canonical status document binds its own version, operation/status
identities, plan semantic fingerprint, and exactly one ordered content-free eligibility/outcome/
searchability/failure tuple per output page. It contains no extracted characters.

The separate report pipe is limited to one 4,096-byte canonical frame. It correlates the operation,
both output objects, status size/digest, base PDF verification report, fixed OCRmyPDF/Tesseract
versions, and OCR engine-profile digest. The broker terminates the complete worker Job, revalidates
every source, authenticates both output sections and the report, and checks the status document
against the exact plan order before exposing the PDF candidate. Cancellation, timeout, malformed or
oversized plan/status/report data, source mutation, output mismatch, failed reopen/qpdf/render/text
verification, or cleanup uncertainty returns no authoritative candidate.

This operation is an additive registry codec under the unchanged generic worker-RPC v1 envelope
schema. The schema already represents capability tokens, bounded start inputs/parameters, and
ordered staging outputs; no released JSON Schema meaning is widened. An older peer that does not
request or advertise `pdf.export.searchable` never negotiates it, while any changed searchable
operation meaning requires a new operation version.

## Limits and bounded decoding

Every start requests versioned positive integer limits for wall time, CPU time, memory, temporary
storage, output bytes, page count, and pixel count. Acceptance returns the effective limits that the
worker will enforce; acceptance is not permission to exceed broker or product policy.

Before schema or model construction, strict decoding enforces these protocol-wide caps:

| Resource | Maximum |
|---|---:|
| Encoded message bytes | 65,536 |
| JSON nesting depth | 32 |
| JSON nodes (every value plus every object member name) | 4,096 |
| Code points in one string | 4,096 |
| Members in one collection | 256 |
| Operation parameters | 64 |
| Public error context values | 0 |
| Remembered message IDs in one session | 4,096 |
| Remembered terminal operation IDs in one session | 1,024 |

Only strict UTF-8 JSON is accepted. Duplicate keys, byte-order marks, non-finite numbers, trailing
data, nonstandard constants, oversized integers, and inputs exceeding any structural budget fail
closed. These parsing bounds do not replace the operation resource limits or the OS containment
owned by `PWS-0107`.

## Sanitized errors

An error is a closed version-1 object containing a registered stable code, a closed category, a
closed retry action, an exactly corresponding restart flag, and an empty `parameters` array.
`restart_required` is true if and only if `retry_action` is `restart_worker`.
`protocol.error` requires category `protocol` and a restart-worker action because it terminates the
session; `worker.fault` also requires a restart-worker action. The public v1 code registry is
closed to `RPC_MESSAGE_INVALID`, `WORKER_OPERATION_FAILED`, `WORKER_OPERATION_TIMED_OUT`, and
`WORKER_INTERNAL_FAULT`. Detailed parser or adapter failures are mapped to one of those public
codes before crossing the RPC boundary; an unknown code is never forwarded to UI or logging.
Message context narrows that registry further: failed results use `WORKER_OPERATION_FAILED`,
timed-out results use `WORKER_OPERATION_TIMED_OUT`, `protocol.error` uses
`RPC_MESSAGE_INVALID` with category `protocol`, and `worker.fault` uses
`WORKER_INTERNAL_FAULT` with category `internal`.

Public protocol-v1 errors intentionally carry no worker-controlled context values. Boolean, integer,
token, and UUID parameters and free-form message, detail, exception, traceback, path, filename,
metadata, OCR/document text, form values, or library error fields are forbidden. UI/application code
maps stable codes and trusted local state to localized plain-language recovery text. Raw third-party
exceptions stay behind the worker adapter boundary.

## Validation and examples

[`tools/validate_worker_rpc.py`](../../tools/validate_worker_rpc.py) validates strict decoding
budgets, the self-contained schema/reference policy, exact version pre-dispatch, correlations,
references, progress/results, the operation registry, the closed error registry, and cross-field invariants
that JSON Schema cannot express.
Diagnostics contain stable codes, repository-relative fixture labels, and JSON locations; they do
not echo values, secrets, paths, payload text, or raw exceptions.

Canonical synthetic messages live under `schemas/examples/worker-rpc/v1`; start with
[`session-hello.json`](../../schemas/examples/worker-rpc/v1/session-hello.json). There is one example
for every message type and a separate result example for each terminal outcome. Invalid fixtures cover
malformed JSON, oversized input, unknown versions/types, direction and correlation errors,
unsupported operations, unsafe references, invalid result/error shapes, and path-like members.

Run the offline gate with:

```powershell
py -3.14 tools/dev.py validate-schemas
```

## Evolution

Protocol and message version 1 meanings are immutable once released. Unknown future versions and
message types fail before mutation or dispatch. A new operation can use the v1 envelope only when
both peers install its explicit capability/operation codec, the client requests that exact capability
in `session.hello`, and the worker reports it only in response to that request. Workers never advertise
additive capabilities to an older peer. The operation must also use the existing `processing` stage,
the four public error codes with empty context, and every envelope, reference, limit, and lifecycle
invariant unchanged. A new progress stage, public error code/context field, unsolicited capability,
changed envelope, correlation rule, or changed existing-message meaning requires a new protocol/
message schema, dispatch path, fixtures, compatibility tests, and an approved migration/evolution
record. No implementation may silently default missing fields or treat an unknown field as harmless.
