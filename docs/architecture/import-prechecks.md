# Bounded import-precheck contract

- Status: Implemented PWS-0103 contract
- Task: PWS-0103
- Decisions: PWS-D007, PWS-D013, PWS-D038
- Data flow: [DF-IMPORT](../security/data-flows.md#df-import--select-precheck-copy-inspect-and-commit-source)

## Scope

PWS-0103 admits a selected external file to the next quarantine step only after a narrow,
deterministic precheck. It identifies PDF, JPEG, and PNG by bytes; applies caller-supplied byte,
image, expansion, and inspection budgets; and returns typed preliminary state or a sanitized stable
error. It does not copy a source, create a source record, write a case manifest, decode pixels,
parse PDF objects, invoke a document engine, or launch a process.

The adapter is intentionally split between pure domain contracts and a Windows storage boundary.
`paperworkshell.domain.import_precheck` contains values, states, limits, results, and error codes.
`paperworkshell.storage.source_precheck` authenticates a source handle and applies bounded framing
checks. The private `storage._source_windows` module contains only the narrow Win32 handle adapter.
No third-party dependency is introduced.

## Live source lease

`WindowsSourcePrechecker.open()` is a context manager that yields `PrecheckedSource`. The lease:

- opens one drive-qualified file on a fixed local Windows volume with read access only;
- does not grant write or delete sharing and does not inherit the handle;
- opens and retains the drive root and every ancestor without following reparse points before it
  opens the final component;
- requires every retained handle to remain on the same fixed-volume identity and requests
  no-recall behavior from Windows;
- rejects directories, reparse points, hard-linked files, delete-pending files, path aliases, and
  offline/recall-on-access sources;
- reads every checked byte through that same authenticated handle; and
- refreshes identity, final path, size, link count, timestamps, attributes, and reparse state before
  the context can finish successfully.

The result alone is not authorization to reopen a path. PWS-0104 must consume the still-live lease
when it streams a pending immutable source; otherwise the precheck and copy could observe different
file identities. Authoritative publication must occur only after the context exits successfully and
its terminal identity checks pass. The lease closes deterministically on success and every failure
path. A source already open for writing, or one that cannot remain unchanged, fails with a stable
source error.

UNC, device-namespace, extended-namespace, drive-relative, removable, mapped-network, cloud
placeholder, control-character, reserved-name, trailing-dot/space, and explicit named-stream paths
are outside this first supported boundary. Shell metacharacters that Windows permits remain inert
filename data because the precheck launches no executable and builds no command string.

Path validation is bounded before any native call: the path may contain at most 64 components,
each component is limited to 240 UTF-16 code units, and the complete path is limited to 32,767
UTF-16 code units. The retained ancestor chain therefore has a fixed handle, query, memory, and
cleanup ceiling.

The selected path addresses only the unnamed/default file stream. An existing named alternate data
stream is neither opened nor interpreted as document content. PWS-0104 must likewise copy only the
same default stream from the retained handle; it must not enumerate or persist external named
streams.

## Content policy

An extension never establishes media type. A missing extension is allowed when the bytes identify a
supported format. A recognized wrong extension or any other suffix is rejected as a mismatch; this
prevents familiar-looking names from overriding detected content.

The checks are deliberately preliminary:

| Format | Checks performed here | Explicitly deferred |
|---|---|---|
| PDF | `%PDF-` at byte zero, a defined PDF 1.0–1.7 or 2.0 header line, and bounded trailing `%%EOF` line framing | object structure, page count, encryption, signature markers, attachments, actions, and rendering |
| PNG | exact signature; first `IHDR` length/type/CRC; legal dimensions, color/depth, methods, exact trailing `IEND`, and Adam7 expansion estimate | IDAT decompression, pixel decode, intervening chunks, and metadata |
| JPEG | exact SOI; bounded marker/segment walk to a supported SOF and SOS; frame and scan component grammar; bounded trailing EOI | entropy decode, complete scan validation, color interpretation, and metadata |

PDF tokens such as `/Count`, `/Encrypt`, or `/Sig` are never scanned heuristically. A successful PDF
result therefore reports page count as unknown and encryption/signature as not checked. The typed
PDF encryption contract can represent `encrypted`, `unencrypted`, `not_checked`, and
`not_applicable`, but this precheck emits only `not_checked` for PDF. PWS-0105 owns the observation
that produces distinct malformed, encrypted, signed-marker, and ordinary structural outcomes in the
restricted worker. Image results remain `needs_worker_inspection` until an owning image adapter
decodes them.

Unsupported content, truncated framing, malformed headers, excessive resources, and unavailable or
changed sources are distinct typed failure families. No precheck success means that content is safe
or complete; it means only that the same retained byte stream is eligible for bounded quarantine.

## Caller-supplied limits

`ImportLimits` has no product defaults. Every caller must provide positive bounded integers for:

- source bytes and later trusted PDF page count;
- image width, height, and total pixels;
- estimated decompressed bytes;
- decompression warning and hard ratios;
- total preliminary header bytes read; and
- JPEG structure records examined.

The header budget must be from 2,048 through 1,048,576 bytes, the JPEG structure-record budget may
not exceed 256, and the warning ratio cannot exceed the hard ratio. Limits are inclusive: an
observation equal to a maximum is accepted, while maximum plus one fails. Source size is checked
before the first content read. Every random-access read is exact, individually bounded by the same
one-mebibyte adapter cap, and charged against one cumulative header budget. Preliminary parsing also
has a fixed 1,024-read-operation ceiling. Every JPEG marker and repeated marker-fill byte consumes
the structure-record budget, so redundant fill cannot amplify one record into unbounded native I/O.

PNG decompression estimates conservatively cover scanline filter bytes, Adam7 passes when present,
palette expansion, and possible `tRNS` alpha in an expanded pixel buffer. APNG aggregate frames and
compressed ancillary metadata remain part of the required image decode/metadata inspection. JPEG
estimates use frame dimensions, components, and precision. An estimate above either the absolute
byte maximum or hard expansion ratio is rejected. An estimate above only the warning ratio produces
the typed `IMPORT_DECOMPRESSION_NEEDS_REVIEW` signal; it does not claim corruption.

The PDF page maximum is retained in the same policy model but cannot be observed safely from header
tokens. `require_observed_page_count()` applies it later to a trusted worker result.

## Errors, privacy, and state

`ImportPrecheckError` contains only a closed `ImportPrecheckErrorCode`, a preliminary state, a stable
internal context token, and a plain-language safe message. Raw Python, parser, or Win32 exception
text is discarded. Paths, original filenames, file bytes, metadata, and user-controlled values do
not enter the result, exception text, or object representation.

The precheck has no case-workspace write capability. Rejection cannot create a pending original,
manifest entry, journal record, cache object, or partial valid document. PWS-0104 owns copy/hash and
pending-file cleanup; PWS-0105 owns PDF structural classification; PWS-0107 owns restricted worker
process resources; and PWS-0201 owns full JPEG/PNG decode and normalization.

## Verification evidence

Unit tests use path-free byte sources to prove valid and mismatched signatures, strict framing,
limit boundaries, warning behavior, parser read budgets, and sanitized immutable contracts.
Windows integration tests exercise committed synthetic PDF/PNG files, deterministic task-local JPEG
bytes, immutable source behavior, exact-handle sharing, hard-link races, path replacement, and unsafe
locations. Security tests exercise command-injection filenames, raw-error leakage, parent-junction
aliases, alternate streams, and rejected-import case non-mutation.
