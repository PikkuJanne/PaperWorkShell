# PDF structural-inspection contract

- Status: Implemented PWS-0105 contract
- Task: PWS-0105
- Decisions: PWS-D012, PWS-D015, PWS-D019, PWS-D021, PWS-D029, PWS-D038
- Worker boundary: [Restricted worker lifecycle contract](worker-lifecycle.md)
- Import boundary: [Immutable source-ingest contract](immutable-ingest.md)
- Protocol: [Worker RPC protocol v1](../schemas/worker-rpc-v1.md)

## Scope and ownership

PWS-0105 inspects the exact sealed PDF copy prepared by immutable ingest. It returns a path-free,
versioned result for open state, page and object counts, encryption, structural state, signature
markers, attachments, interactive content, actions, scripts, and XFA. It does not render pages,
validate a cryptographic signature, remove active content, repair a malformed file, accept a
password, publish a source record, or claim that a structurally clean PDF is safe or suitable for a
portal.

`domain.pdf_inspection` owns the pure result, limit, error, and correlated-frame contracts. The
`pdf` package alone imports pikepdf and hides every pikepdf/qpdf object and exception behind those
contracts. Worker composition receives the concrete adapter after the restricted host has audited
its isolation. The storage boundary retains authoritative pending/source/manifest state and lends
only one exact read authority; application and UI code receive typed metadata, never a PDF object,
case path, native handle, tool output, or raw exception.

## Typed result contract

`PdfInspectionResult` report version 1 is closed. Its states are deliberately conservative:

| State | Required result facts | Meaning and limit |
|---|---|---|
| `opened` | Positive bounded page/object counts; `unencrypted`; structural `clean` or `warnings`; every feature is `detected` or `not_detected` | The configured structural checks completed for this copy; this is not content safety, signature validity, or portal acceptance |
| `encrypted` | No page/object counts; encryption `encrypted`; feature states `unknown`; structural `failed` | Empty-password open did not provide inspectable content; PaperWorkShell does not guess, retain, or request a password in this operation |
| `malformed` | No page/object counts; encryption `unknown`; feature states `unknown`; structural `failed` | Strict non-recovery open or structural traversal could not establish an inspectable PDF |

Feature states cover only the registered structural markers. A signature marker means that a
signature-related PDF structure was observed; it does not prove that a signature is present,
authentic, valid, trusted, or current. `not_detected` is not a general proof that arbitrary hidden
content is absent. Later profile and preflight tasks decide whether a detected or unknown feature
blocks a specific operation.

The internal error registry is also closed. Invalid models or sources, changed bytes, limits,
cancellation, timeout, missing/failed dependencies, invalid handles/reports, worker faults, and
unexpected internal failures become stable codes and trusted messages. Protocol v1 deliberately
maps a worker-side operation failure to its smaller public error registry. Neither boundary carries
a library message, PDF value, path, filename, digest, command output, traceback, or handle number
into routine UI/logging.

## Exact prepared-source authority

The prepared source remains non-authoritative until inspection and commit finish. Storage registers
only an already sealed pending file whose full `PreparedSourceSummary` matches its retained case,
pending/source IDs, media type, byte size, and SHA-256. A borrow proceeds without a path lookup:

1. `ReOpenFile` creates an independently positioned read handle from the already authenticated
   kernel object. Storage rechecks file identity, size, regular-file type, single-link state,
   reparse/sparse state, and absence of alternate streams.
2. The trusted worker client duplicates that handle into the ready worker with only
   `FILE_READ_DATA`, `FILE_READ_ATTRIBUTES`, and `SYNCHRONIZE`. The child copy is explicitly
   non-inheritable and has no write or delete right.
3. The AppContainer SID receives no ACL entry on the case root or pending file. No case/source path
   enters RPC, command arguments, environment, or a result.
4. The registration is revoked before the owning pending handle is published, deleted, or closed;
   a stale or mismatched summary cannot obtain a later borrow.

Sharing read/write/delete when `ReOpenFile` creates the borrow keeps the owning storage handle's
existing cleanup authority compatible. Sharing does not grant the borrower either write or delete
access. The storage and worker tests exercise a denied write through the borrowed/duplicated handle
and verify that the synthetic source bytes do not change.

## One operation and two out-of-band handles

`pdf.inspect` version 1 is a one-shot worker capability. Its RPC start has exactly one
`source_read` reference with content kind `pdf`, expected byte size, and SHA-256. It has exactly two
distinct positive integer parameters: the worker-local source-handle value and the worker-local
result-pipe-handle value. The values are created by the trusted broker for that worker process;
they are neither paths nor permission to resolve another object.

The result channel is a new anonymous pipe. The broker retains its non-inheritable read end and
duplicates the write end into the worker with only `FILE_WRITE_DATA` and `SYNCHRONIZE`, also
non-inheritable. It then closes its local writer. The worker cannot read or replace a broker result
and receives no general cache, staging, manifest, journal, or directory authority.

After adopting the source handle, the worker verifies that it is non-inheritable and describes a
regular file of the expected size. It copies exactly the expected number of bytes into
worker-private ephemeral storage while recomputing SHA-256, rejects a short/surplus/mismatched
source, and closes the case handle before pikepdf sees the stream. Temporary bytes, page/object
staging, page/object traversal, wall time, cancellation, and result bytes use the explicit
operation limits; memory, CPU time, process count, and descendant cleanup are additionally enforced
by the retained Job Object. These controls bound the supported operation but do not claim that a
native parser call is cooperatively preemptible at every instruction.

The worker encodes one canonical result as:

```text
4-byte big-endian payload length
+ 16-byte operation UUID
+ 16-byte source-object UUID
+ canonical ASCII JSON result (at most 4,096 bytes)
```

The complete frame is at most 4,132 bytes. The result writer closes its exact pipe handle after
writing one bounded frame, and RPC success is emitted only after that write returns. The broker
terminates and confirms the one-shot worker tree before releasing the prepared-source borrow or
consuming the pipe through EOF. It then enforces the frame cap, exact length, current report
version, closed fields and values, and both UUID correlations before trusting the typed result. A
missing, truncated, oversized, surplus, duplicate-key, future-version, or misbound report is
failure and cannot commit a source record.

This order is intentional: a compromised or stalled worker cannot keep case authority after the
operation is settled, and an EOF-delimited result is consumed only after every possible writer in
the contained job tree is gone. Handles are never remotely closed from the broker because a target
process can reuse a numeric handle value; whole-tree termination is the safe revocation boundary.

## pikepdf and libqpdf inspection

The production adapter opens the verified worker-private stream with an empty password,
`attempt_recovery=False`, and warning text suppressed. It does not accept a caller-controlled
password or recovery mode. After bounded page and indirect-object counts, it walks every indirect
object rather than only catalog-reachable objects. Depth, collection size, total visited nodes,
cancellation, and the monotonic deadline are checked throughout the walk. This all-object policy is
what detects the deliberately orphaned script-marker fixture.

The registered marker scan recognizes signature structures, embedded-file/file-spec structures,
annotations/forms/widgets, action dictionaries, JavaScript markers, and XFA markers. The adapter
then calls pikepdf's `check_pdf_syntax()` against the same verified stream. That call uses the
libqpdf 12.3.2 embedded in the admitted pikepdf wheel. Warning strings are discarded; only
`clean` versus `warnings` crosses the adapter. Strict open/parser failures become the typed
`malformed` result, password-required input becomes `encrypted`, and resource/cancellation/deadline
conditions remain closed operation errors.

## Standalone qpdf no-shell seam

PWS-0105 also keeps an explicit standalone qpdf 12.3.2 command adapter for structural-check and
security-contract evidence. It is distinct from the production pikepdf-stream path above. The
checker requires a trusted absolute executable and launches this fixed argument vector directly:

```text
<approved-qpdf.exe> --check --suppress-recovery --no-warn -- <absolute-source-path>
```

`shell=False`, the `--` separator, a fixed executable working directory, closed standard input,
`close_fds=True`, and a minimal allowlisted environment keep a metacharacter-bearing test filename
as one data argument. The checker never searches `PATH` or retains stdout/stderr. Two concurrent
drainers count and discard output under one shared byte budget; documented qpdf return codes map to
`clean`, `warnings`, or `failed`, while any other code is a sanitized dependency failure. Timeout,
cancellation, excessive output, or drainer failure terminates and, if necessary, kills the process;
the restricted worker Job Object contains descendants.

CI obtains the official qpdf Windows ZIP only through the pinned version/hash workflow documented
in the [dependency policy](../development/dependency-policy.md). The standalone seam proves safe
command construction and command-boundary cleanup. The production inspection operation does not
invoke this path-based seam; any later product use must stage a worker-private input rather than
pass a case path. The seam does not replace the handle-brokered production stream contract.

## Failure, privacy, and lifecycle invariants

- Complex parsing occurs only after the prepared case copy exists and only in the network-denied
  worker. The desktop process never imports pikepdf or parses full PDF structures.
- Cancellation, timeout, parser/tool failure, worker crash, invalid RPC, invalid result frame, or
  failed cleanup produces no source/manifest publication. The external original and last valid
  manifest remain unchanged.
- The one-shot worker generation is terminated on success as well as failure so no parser state,
  brokered source authority, or result writer survives into another operation.
- AppContainer ephemeral storage can hold the worker-private copy until the generation is cleaned
  up. This is bounded application cleanup, not a forensic-erasure guarantee.
- Routine diagnostics may record internal IDs, engine/report versions, numeric counts/duration/
  resource use, and stable codes only. They exclude report bytes, feature-source values, tool
  output, paths, filenames, hashes, handles, and raw exceptions.
- `structural_state=clean` is one concrete check. Export still requires the complete PWS-0112
  reopen, page/policy, render, and commit-verification pipeline.

## Verification ownership

Adapter contract tests exercise every applicable synthetic PDF: ordinary, malformed, password
required, signed marker, attachment, annotation, action-only, script, incremental, image-only,
mixed, geometry, duplicate, and Unicode-named cases. Boundary tests cover page, object, byte,
object-graph, cancellation, and timeout limits plus discarded warning values and sanitized source
failures.

Worker/storage tests cover the closed `pdf.inspect` protocol, exact input/parameter rules,
reduced-right non-inheritable handle duplication, independent read position, denied writes,
source size/digest verification, bounded correlated result frames, and cleanup ordering. Security
tests cover metacharacter filenames, fixed argv/environment, no shell, qpdf return-code mapping,
concurrent output flooding, timeout escalation, cancellation cleanup, and prohibited raw output.
Dependency capability tests bind the selected pikepdf/libqpdf native closure. Required supported-
Windows live evidence uses the real AppContainer/Job worker; mocks or skipped required probes are
not passing evidence.

PWS-0501 owns the later expanded metadata/privacy inventory. PWS-0112 owns assembly and complete
export verification. PWS-0208 owns modification confirmation and unsigned-derivative policy for
signed-marker inputs. PWS-0106 owns rendering; this task makes no rendering or visual-fidelity
claim.
