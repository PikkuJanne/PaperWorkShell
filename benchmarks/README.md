# Constrained-resource evidence

PaperWorkShell's low-resource contract is the central, versioned `ResourcePolicy` described in
[`docs/performance/LOW_RESOURCE_POLICY.md`](../docs/performance/LOW_RESOURCE_POLICY.md). The
constrained profile applies real limits to production composition and the canonical Milestone 1
vertical slice: worker memory and CPU, process count, workspace/cache/temporary storage, preview
demand and dimensions, queue/concurrency bounds, chunked I/O, responsiveness, cancellation, and
low-disk behavior.

Validate the closed evidence schema without running the workload:

```powershell
py -3.14 tools/dev.py validate-m1-benchmark-report
```

Run the synthetic, deterministic vertical slice under the constrained profile:

```powershell
py -3.14 tools/dev.py run-m1-vertical-slice
```

The sanitized report binds the repository revision, resource-policy version, synthetic workload,
engine identities, configured budgets, observed numeric values, deterministic result, recovery,
and explicit limitations. It omits usernames, hostnames, serial numbers, network identifiers,
document content, and full paths. Reports are not uploaded by the quality workflow.

Passing this workload proves that the tested revision stayed within the stated software budgets.
It does not emulate or certify a named CPU, RAM allocation, GPU, SSD, HDD, VM, or physical device.
Physical low-end-device testing has not been performed; any future observation is optional and
non-gating under PWS-D041.

## PWS-0207 recipe-only quality baseline

`pws0207-quality-findings-v1.json` is the canonical sanitized evaluation target for PWS-0207. The
recorder and validator prove that its 40-hex revision exists as a Git commit, that every bound
behavior-producing surface is an ordinary blob in that commit, and that the current analyzer, OCR
producer, organizer/UI correlation, worker protocol/composition, and evaluator stage-zero index
objects and raw working-file blob IDs exactly match those committed blobs. The authoritative exact
surface list is documented in the evaluation contract. This permits the baseline, task/bundle
metadata, and documentation closure to follow the implementation commit without pretending that
the baseline authenticates unlisted repository surfaces; the final task revision and full gates
bind the complete change. The artifact also binds the deterministic recipe-corpus
identity/version/SHA-256, the exact
quality-analysis profile identity/version/SHA-256, and the real constrained ResourcePolicy
profile/version. Its eight independent metric-kind rows retain TP, FP, TN, FN, precision, recall,
false-positive rate, and false-negative rate for the six visual signals plus distinct OCR-failed
and OCR-low-confidence metrics. The short metric kinds and their exact domain-code mapping are
documented in the evaluation contract.

The corpus is reconstructed in memory from fixed recipes. The baseline contains no page raster,
thumbnail, perceptual fingerprint, OCR text, word box, filename, path, metadata value, native tool
output, or user/machine identity. It is neither a golden document/render, an OCR-recognition
benchmark, a real-document corpus, nor hardware-performance evidence. Small synthetic classes and
the deliberately retained confounders make false positives and false negatives visible; they do
not establish general accuracy. Exact current results and limitations are documented in
[scan-quality findings evaluation](../docs/development/scan-quality-evaluation.md#baseline-metrics).

Recompute current content-free metrics without writing a file:

```powershell
python tools/pws0207_quality_evaluation.py evaluate
```

Validate a recorded baseline against the current corpus/profile/metrics, an existing expected Git
commit, and clean matching evaluated implementation surfaces:

```powershell
python tools/pws0207_quality_evaluation.py validate benchmarks/pws0207-quality-findings-v1.json --revision <recorded-40-lowercase-hex>
```

Baseline creation is an explicit maintainer action. It uses a same-directory fsynced temporary,
exclusive no-replace publication, and cleanup on failure; it refuses to overwrite an existing
artifact:

```powershell
python tools/pws0207_quality_evaluation.py record --revision <40-lowercase-hex> --output benchmarks/pws0207-quality-findings-v1.json
```

## PWS-0208 incremental mixed-job evidence

[`pws0208-searchable-mixed-workload-v1.json`](pws0208-searchable-mixed-workload-v1.json) is the
canonical content-free workload descriptor for PWS-0208. It binds three public synthetic sources,
12 pages, the source-scoped unsigned-derivative confirmation code, one active full-resolution page
buffer/result, the 50-millisecond sampling interval, and success/cancellation/limit/fresh-retry
scenarios. The `pdf-signed-marker` source is a structural-marker fixture, not evidence that a
cryptographic signature exists or is valid.
Its exact SHA-256 is
`ce04fcca1b67fa212d790ddda5074baf82f2d55e8212fc5467f879fbab8dd70a`.

Validate a canonical `pws0208-resource-evidence` version-1 report without running the workload:

```powershell
python -m tools.pws0208_resource_evidence <report>
```

Validate the exact committed workload descriptor through the stable developer entry point:

```powershell
py -3.14 tools/dev.py validate-pws0208-resource-workload
```

Run the admitted Windows workload and publish the fixed local report and derivative under
`output/pdf/`:

```powershell
py -3.14 tools/dev.py run-pws0208-resource-workload
```

The workload command is the live runner: it invokes the focused supported-Windows integration
harness, uses the production worker/Job boundary and exact workload descriptor, and publishes the
report only after the run passes. The separate `python -m tools.pws0208_resource_evidence <report>`
command is validator-only: it checks a previously produced report's closed model, canonical
encoding, workload/policy identity, numeric budgets, sampled private-temp completeness,
cancellation bound, cleanup, and retry outcomes without running the workload. Both commands keep
the report local and sanitized; the validator never prints its contents.

The model binds the verified derivative byte size and SHA-256, and records sampled
complete-process-tree RSS separately from worker-process and Windows Job
commit peaks. It also records the sampled worker-private-temp peak/count/interval/completeness,
active full-resolution page buffers and page results, cancellation acknowledgement, the closed
limit failure, cleanup, retry, unchanged originals, expected confirmation/history-only manifest
mutations, and manifest stability across cancel/limit failures. Passing these bounded
software checks does not certify a named processor, memory size, disk, VM, or physical device.
The active-buffer/result values are observed in a separate focused production-adapter scheduler
probe over the exact workload and carry scope
`focused-production-adapter-instrumentation`; the AppContainer run supplies process, Job, and
private-temp measurements. The report does not imply those counters crossed worker RPC.
Exact OCRmyPDF, Tesseract, pikepdf, qpdf, pypdfium2, and PDFium versions plus preparation,
success, cancellation, limit-failure, retry, and total durations are bound as content-free facts.
Durations are observations, not heterogeneous-machine performance gates.

## PDF renderer comparison

`pdf-renderer-windows-v1.json` binds the historical PWS-0106 comparison to a versioned Windows workload,
15 independently calculated renderer goldens, two semantic cases, three stable failure outcomes,
and exact pypdfium2/PDFium and Qt PDF identities. It is a record-only selection workload, not a
product latency target.

PWS-0601 refreshed only the current workload's fixture-index hash after adding redaction sources.
Its selected fixtures, goldens, policies, and expected pixel outputs are unchanged. Historical
benchmark evidence remains tied to the workload and index hashes recorded for its original run;
this binding update adds no performance measurements or claims and does not rerun the comparison.

Validate every workload, fixture, golden, artifact, and outcome binding without loading
either native renderer:

```powershell
py -3.14 tools/dev.py validate-pdf-renderer-benchmark
```

Qt PDF is an evaluation-only candidate in a non-default dependency group. Prepare the exact locked
candidate closure before running the comparison:

```powershell
uv sync --locked --group renderer-evaluation
```

The Windows CI job also synchronizes this non-default group solely so the PWS-0106 comparison
tests can exercise both live adapters. Installing that group for tests does not grant Qt PDF a
source-import boundary, product runtime scope, or enabled shipment relationship.

The comparison requires a clean repository revision. Its accepted run remains historical renderer
selection evidence; current low-resource qualification comes from the central constrained profile,
not operator hardware attestations.

The harness copies only its pinned Python runtime subset, exact renderer files, and hash-bound
public synthetic corpus into a fresh authenticated fixed-drive stage. Tracked project code and
data come from bounded `git cat-file` reads of the exact reported revision rather than from the
mutable worktree. Source ancestors and staged objects remain held by authenticated Windows handles
while the closure is built. Every selected renderer file must match both the SHA-256 and size in
that installed distribution's exact `RECORD`; the stage manifest then cryptographically binds the
repository revision, every staged file, and every byte count. Git must report the same clean `HEAD`
before staging, after staging, and immediately before report creation.

Each renderer/mode runs in a fresh zero-capability AppContainer under the production kill-on-close
Job Object and bounded framed transport. The parent has already audited the retained stage before
launch and seals the root plus every staged directory with a retained, reversible DACL lease. The
current user keeps read/traverse access but is denied ordinary file creation, subdirectory
creation, deletion, and rename operations; the distinct AppContainer SID receives inherited
read/execute only. The seal is reverified before and after AppContainer access leasing. The
operating system must load the staged interpreter, fixed bootstrap, and minimal
isolation/protocol modules before the child can receive the parent-provided manifest digest; the
bootstrap verifies the complete manifest before it imports the benchmark harness or either native
renderer. The stage is read-only to the child and the child has no network capability or live
repository access.

Normal cleanup revalidates and deletes only exact identities retained by the creating process.
Directory policies remain sealed while their known children are removed; the parent restores and
byte-verifies each original DACL immediately before deleting that exact retained directory, with
the root restored last. If initial authority setup fails after the empty root is authenticated,
the parent deletes only that exact empty identity; a failure before authentication leaves the root
untouched and records cleanup failure rather than attempting unauthenticated recursive deletion. An
uncertain worker close, AppContainer launch cleanup, or post-revocation seal check permanently
withdraws cleanup authority for that stage. The process retains its handles and namespace-deny
DACLs and refuses every later cleanup or child-launch attempt; it does not restore directory
policies or retry deletion. This can intentionally leave the bounded public-only stage after the
benchmark process exits. An abrupt parent-process or machine termination can likewise leave at
most the 768 MiB bounded stage, which
contains only copied runtime/project code and public synthetic fixtures—not user documents. A later
process does not treat a filename or self-written token as deletion authority, so such residue is
preserved for explicit maintenance instead of risking deletion through an attacker-swapped path.

The sanitized JSON report distinguishes the workload's declared published-wheel hashes from the
observed installed `RECORD` and stage-closure hashes. It also records corpus/workload identities,
isolated-worker readiness, imports, document-open and page-render timings, nearest-rank warm
p50/p95/max values, and a parent-observed monotonic launch-through-exit envelope that bounds the
strictly positive child timing evidence,
Job-reported process/job memory peaks, sampled private temporary-storage high-water, cooperative
cancellation at the documented between-render checkpoint, idle-worker Job-tree termination, and
stable malformed/high-dimension outcomes. It contains no source path, filename, page pixels,
document content, user or machine identity, or native exception text. The benchmark does not claim
that cooperative cancellation interrupts an in-flight native render or certify its host as a
particular physical device.

### Accepted PWS-0106 selection run

The accepted record-only local-smoke run used clean revision
`74c1c7aae799195a87608a797d7136b88e80cc87`. Its authenticated 1,015-file stage had closure digest
`f78dffe61e4b86e6596f1080097a7a23aed90cc0ca6eca9023d67fb2521e3da8`. Both candidates matched all
15 analytical goldens and all three malformed/high-dimension outcomes. pypdfium2 also passed both
semantic cases; Qt PDF passed the missing-font case but its public API could not perform the
required distinct MediaBox mode.

The complete sanitized JSON result was reviewed locally and is intentionally not committed.
[ADR 0005](../docs/adr/0005-pdf-renderer-selection.md) records the exact warm, cold, memory,
cancellation, and Job-termination measurements and the functional, packaging, and license
rationale for selecting pypdfium2/PDFium. This local smoke remains selection evidence only; it is
not a product target or physical-device qualification.
