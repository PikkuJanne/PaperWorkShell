# Worker RPC protocol v1

`PWS-0008` defines the structured contract between the trusted PaperWorkShell client and one
case-scoped document worker. The authoritative structural schema is
[`schemas/worker-rpc-v1.schema.json`](../../schemas/worker-rpc-v1.schema.json). It is a closed,
self-contained JSON Schema Draft 2020-12 resource and is always evaluated offline.

This is a message contract, not a transport or sandbox implementation. Framing, endpoint naming,
Windows ACLs, worker creation, capability-secret provisioning, case-object resolution, opened-handle
handoff, network denial, process limits, termination, and cleanup are deliberately deferred to
`PWS-0107`. That task must preserve this contract rather than adding paths or shell command text to
messages.

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
4. Every required capability must appear with the exact required version. No ready response means
   no operation may start. An incompatible or malformed negotiation produces only a sanitized
   protocol failure and the session is not usable.

The initial registry contains one capability and operation: `protocol.noop` version 1. Its canonical
start has empty `inputs` and `parameters`. It proves version negotiation, typed round trips, state,
and cancellation without parsing a document.

## Messages and lifecycle

| Message | Direction | Payload |
|---|---|---|
| `session.hello` | client to worker | `supported_protocol_versions`, `required_capabilities` |
| `session.ready` | worker to client | `selected_protocol_version`, `capabilities`, `max_message_bytes`, `max_in_flight_operations` |
| `operation.start` | client to worker | `operation_kind`, `operation_version`, `inputs`, `parameters`, `requested_limits` |
| `operation.accepted` | worker to client | matching operation kind/version and `effective_limits` |
| `operation.progress` | worker to client | `stage_code`, integer `completed_units`, integer-or-null `total_units`, and a unit |
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

## Two-stage operation validation

Validation is intentionally split:

1. The envelope/schema layer accepts only the bounded v1 grammar and typed payload families.
2. The operation registry selects an exact `(operation_kind, operation_version)` codec and applies
   its input, parameter, output, limit, and state rules before dispatch.

Consequently, a structurally valid but unregistered operation fails closed as unsupported. The only
registered pair in this foundation task is `protocol.noop` version 1, with no inputs, parameters,
or outputs.
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

The trusted case-workspace broker will map these identifiers to constrained objects in `PWS-0107`.
Schema validity does not authorize a file. Resolution must still verify the case/area/operation
binding, containment, object type, size and digest, resistance to path/reparse-point races, and the
worker's access rights before use.

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
| Operation/error parameters | 64 |
| Remembered message IDs in one session | 4,096 |
| Remembered terminal operation IDs in one session | 1,024 |

Only strict UTF-8 JSON is accepted. Duplicate keys, byte-order marks, non-finite numbers, trailing
data, nonstandard constants, oversized integers, and inputs exceeding any structural budget fail
closed. These parsing bounds do not replace the operation resource limits or the OS containment
owned by `PWS-0107`.

## Sanitized errors

An error is a closed version-1 object containing a registered stable code, a closed category, a
closed retry action, an exactly corresponding restart flag, and zero or more allowlisted tagged
parameters. `restart_required` is true if and only if `retry_action` is `restart_worker`.
`protocol.error` requires category `protocol` and a restart-worker action because it terminates the
session; `worker.fault` also requires a restart-worker action. The public v1 code registry is
closed to `RPC_MESSAGE_INVALID`, `WORKER_OPERATION_FAILED`, `WORKER_OPERATION_TIMED_OUT`, and
`WORKER_INTERNAL_FAULT`. Detailed parser or adapter failures are mapped to one of those public
codes before crossing the RPC boundary; an unknown code is never forwarded to UI or logging.

Error parameters are not an arbitrary context map. The allowlist is:

- integer: `attempted_version`, `supported_version`, `minimum`, `maximum`, `observed`, `limit`,
  `count`, `capability_version`, `operation_version`, `protocol_version`, `sequence`,
  `expected_sequence`, `observed_sequence`;
- UUIDv4: `request_id`, `operation_id`, `message_id`.

Parameter names are unique within one error. Boolean and token error parameters and free-form message,
detail, exception, traceback, path, filename, metadata, OCR/document text, form values, or library
error fields are forbidden. UI/application code maps stable codes and safe parameters to localized
plain-language recovery text. Raw third-party exceptions stay behind the worker adapter boundary.

## Validation and examples

[`tools/validate_worker_rpc.py`](../../tools/validate_worker_rpc.py) validates strict decoding
budgets, the self-contained schema/reference policy, exact version pre-dispatch, correlations,
references, progress/results, the operation registry, error allowlists, and cross-field invariants
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
message types fail before mutation or dispatch. A backward-compatible new operation can use the v1
envelope only when it has a new explicit capability/operation codec and preserves all envelope,
error, reference, and limit invariants. A changed envelope, correlation rule, or existing message
meaning requires a new protocol/message schema, dispatch path, fixtures, compatibility tests, and
an approved migration/evolution record. No implementation may silently default missing fields or
treat an unknown field as harmless.
