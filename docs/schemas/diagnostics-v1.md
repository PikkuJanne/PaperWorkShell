# Sanitized diagnostic record and summary v1

- Status: Implemented PWS-0113 codec/model contract
- Task: PWS-0113
- Decisions: PWS-D027, PWS-D039
- Architecture: [Startup recovery and sanitized diagnostics](../architecture/startup-recovery-and-diagnostics.md)

## Purpose

Diagnostic record v1 is a closed safe-data contract for routine local events. It is intentionally
not a general logging envelope: a caller cannot add a message, context mapping, path, document
value, exception, traceback, or arbitrary key. The Python model validates the complete event/field
combination before the canonical encoder emits one bounded ASCII JSON line.

This contract does not define a support-bundle format. PWS-0805 separately owns the exact
snapshot, preview, confirmation, and local bundle-writer schema.

## Canonical record object

Every encoded record contains exactly these keys, sorted lexically by the canonical JSON encoder:

| Key | Type | Contract |
|---|---|---|
| `case_id` | string or `null` | Canonical internal UUIDv4; required only for case-bound events |
| `counts` | array | Zero through 32 unique closed `{kind, value}` objects |
| `event` | string | One closed `DiagnosticEventCode` value |
| `failure_code` | string or `null` | Closed generic code; required only for `partial`, `failed`, or `timed_out` outcomes |
| `occurred_at` | string | UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` timestamp |
| `operation_id` | string or `null` | Canonical internal UUIDv4; required only for operation events |
| `operation_kind` | string or `null` | Closed kind; required only for operation events |
| `outcome` | string or `null` | Closed outcome; required only for terminal events |
| `record_version` | integer | Exact value `1` |
| `resources` | array | Zero through 32 unique closed `{kind, value}` objects |
| `versions` | array | Zero through 16 unique closed `{components, kind}` objects |

The encoder uses ASCII with escaped non-ASCII data, rejects non-finite or non-model values, sorts
object keys and category entries deterministically, and emits no insignificant whitespace. One
record is at most 16,384 bytes before the logger adds its single line-feed delimiter.

## Event field matrix

| Event | Case ID | Operation ID/kind | Outcome | Additional rule |
|---|---:|---:|---:|---|
| `APPLICATION_STARTED` | forbidden | forbidden | forbidden | Start event only |
| `APPLICATION_STOPPED` | forbidden | forbidden | required | Terminal application fact |
| `RECOVERY_SCAN_STARTED` | forbidden | forbidden | forbidden | Aggregate scan start |
| `RECOVERY_SCAN_COMPLETED` | forbidden | forbidden | required | Aggregate scan result |
| `CASE_RECOVERY_COMPLETED` | required | forbidden | required | One internal case only |
| `CLEANUP_COMPLETED` | required | forbidden | required | One internal case only |
| `WORKER_STARTED` | required | forbidden | forbidden | Worker lifecycle start |
| `WORKER_STOPPED` | required | forbidden | required | Worker lifecycle terminal |
| `WORKER_FAILED` | required | forbidden | required | Outcome must be `failed` or `timed_out` |
| `OPERATION_STARTED` | required | required | forbidden | One closed operation kind |
| `OPERATION_COMPLETED` | required | required | required | One closed operation kind |

Terminal outcomes are `success`, `partial`, `failed`, `cancelled`, or `timed_out`. A generic
failure code is required exactly for `partial`, `failed`, and `timed_out`; it is forbidden for
`success` and `cancelled`. This prevents a caller from encoding a contradictory successful failure
or an unexplained failed result.

## Closed categories

Operation kinds are `protocol.noop`, `pdf.inspect`, `pdf.thumbnail`, and `pdf.export`.

Version kinds are application, Python runtime, manifest schema, export-history schema, worker
protocol, pikepdf, qpdf, pypdfium2, and PDFium. A version has one through four integer components;
each component is from 0 through 999,999,999. Free-form library version text is not accepted.

Count kinds are cases scanned/recoverable/recovered/isolated/unavailable, operations completed,
pages processed, files removed, directories removed, cache entries removed, and warnings.

Resource kinds are duration milliseconds, CPU milliseconds, peak memory bytes, temporary-storage
bytes, and case-storage bytes released by recovery. Count and resource values are integers from 0
through 9,007,199,254,740,991.

Failure codes are the closed generic recovery-failure, recovery-attention, manifest, cache,
cleanup, worker launch/protocol/termination, operation, resource, storage, and internal categories defined by
`DiagnosticFailureCode`. They carry no rejected input, native status, path, tool output, or
exception message.

## Logger snapshot

`SanitizedDiagnosticLogger` accepts only a fully validated record. It makes an independent copy,
encodes that copy once, and retains both the model and exact JSON line under one lock. The default
history is 256 records and the configurable maximum is 4,096. Displaced-record and binary-sink
failure counts are numeric facts.

An optional sink must be an already-open binary stream supplied by trusted composition. The logger
does not accept a sink path, open or close a file, or use Python's general-purpose logging API. A
short write, flush failure, or ordinary sink exception increments the safe failure count and
raises a closed diagnostic error after the safe record remains buffered; the source exception is
not retained. Process-control exceptions deliberately propagate.

## Human-readable summary v1

The summary is an independently validated immutable snapshot, not an unbounded aggregation query.
It contains exactly:

- summary version `1` and a UTC generation timestamp;
- record, displaced-record, and sink-failure counts;
- at most 16 unique closed numeric versions;
- at most 4,096 unique internal operation UUIDv4 values;
- at most 32 unique closed count values;
- at most 32 unique generic failure codes; and
- at most 32 unique closed resource values.

The renderer revalidates the snapshot, sorts every category deterministically, and emits only fixed
trusted headings/labels and those typed values. An empty category renders `None`. It does not read
the logger, recollect a case, open a file, or include internal case IDs.

The pure summary builder consumes one frozen tuple of at most 4,096 validated records. Repeated
count and cumulative-resource categories are summed, peak memory takes the maximum observation,
operation IDs and generic failure codes are deduplicated, and conflicting versions fail closed. The bounded
logger exposes an atomic summary snapshot that captures its retained records plus numeric
displacement and sink-failure counters under the same lock.

## Prohibited data and forward compatibility

Record and summary v1 have no field for OCR or document text, form values, names, addresses,
original or destination filenames, full paths, metadata values, hashes or digests, thumbnails,
signature images, source/derivative bytes, free-form messages, native output, or exception/stack
values. Tests present distinct hostile values at every public typed seam and require that failures,
representations, JSON lines, and rendered summaries neither retain nor print them.

Adding a category is a reviewed closed-registry change with tests. Changing record or summary
meaning incompatibly requires a new version; callers may not smuggle new data through an existing
kind or numeric field. PWS-0805 must freeze and preview its own exact bundle-entry snapshot rather
than treating this routine record schema as permission to collect arbitrary files.
