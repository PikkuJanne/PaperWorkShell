# PaperWorkShell low-resource engineering policy

This document is binding for document-processing changes. The current policy version is **1** and the constrained profile is **constrained-v1**. Its source-of-truth values live in `paperworkshell.domain.resource_policy`; production composition consumes those values rather than duplicating them.

## What the policy proves

The constrained profile tests software behavior and numeric resource budgets using synthetic workloads and injectable failures. It does **not** emulate or certify a particular processor, RAM configuration, GPU, SSD, HDD, virtual machine, or physical computer. Actual completion time varies with document complexity and hardware. Optional physical-device tests may improve confidence but do not block the roadmap.

## Conservative defaults

Normal and constrained profiles are both bounded. Core functionality requires no discrete GPU; acceleration may only be an optional optimization with a software fallback. Missing or invalid profile configuration fails closed and never selects an unbounded mode.

The initial constrained budgets are:

| Resource | Budget |
|---|---:|
| Heavy document-operation concurrency | 1 |
| Preview prefetch radius | 1 page before and after the visible range |
| Combined thumbnail/preview cache | 134,217,728 bytes total |
| Case-scoped disposable thumbnail share | 67,108,864 bytes |
| Decoded preview memory-cache share | 67,108,864 bytes |
| Preview pixel area | 4,000,000 pixels per page |
| Import copy/hash chunk | 1,048,576 bytes |
| Canonical M1 process-tree peak resident memory | 1,342,177,280 bytes |
| Export free-space safety reserve | 268,435,456 bytes |
| Controlled UI heartbeat gap | 250 ms |
| Cancellation acknowledgement | 2,000 ms or the next documented safe page/stage boundary |
| Active full-resolution page buffers | 1 |
| Resource sampling interval | 50 ms |

The PWS-0204 constrained OCR composition derives these additional operation limits without
changing policy version 1:

| OCR resource | Constrained-v1 limit |
|---|---:|
| Worker plus OCR child | 2 active processes |
| OCRmyPDF page concurrency | 1 job |
| Worker process memory | 536,870,912 bytes |
| Complete Job memory | 805,306,368 bytes |
| Job CPU time | 120,000 ms |
| OCR operation wall time | 300,000 ms |
| Direct Tesseract call | 180 seconds |
| Complete worker-private temporary storage | 536,870,912 bytes |
| Volatile PDF candidate | 134,217,728 bytes |
| OCR request pages | 1,000 maximum |
| OCR raster pixel limit | 134,217,728 pixels |

The output limit is the smaller of 128 MiB and one quarter of the selected policy's temporary
limit. The pixel limit is the smaller of 268,435,456 pixels and one quarter of worker-process
memory bytes. These formulas are production composition, not separate profile constants.

PWS-0206 adds only bounded transport/model ceilings around those central execution budgets; it
does not change policy version 1:

| Searchable mixed-output resource | Contract ceiling |
|---|---:|
| Sources in one semantic plan | 255 |
| Output pages in one semantic plan | 4,096 |
| Read-only plan section | 2,097,152 bytes |
| Content-free page-status section | 2,097,152 bytes |
| Simultaneously active private page-local OCR results | 1 |
| Simultaneously live full-resolution page rasters under constrained-v1 | 1 |

The plan/page ceilings bound semantic and transport collections; they are not permission to exceed
any selected central byte/pixel/time budget. Each searchable-worker OCR invocation is fixed to one
private page and inherits the admitted OCR output, pixel, temporary-storage, Job, and deadline
limits. The coordinator rejects an unschedulable request before native work. Final searchable-PDF
capacity continues to use the selected base-export output/temp policy.

The authorized 128-MiB thumbnail-plus-preview cache ceiling is a combined hard gate, not two
independent 128-MiB allowances. The constrained profile divides it evenly between the reproducible
case-scoped thumbnail cache and the decoded in-memory preview LRU; the canonical measurement sums
their contemporaneous high-water values before applying the ceiling.

The memory budget applies to the canonical synthetic M1 workload, not every hostile input. Existing byte, page, pixel, CPU, timeout, Job, and temporary-storage limits reject oversized or pathological inputs.

## Incremental processing

- Process pages one at a time or in a very small bounded batch.
- Never retain all full-resolution page images for a packet.
- Stream source hashing and copying in the configured chunk size; whole-file `read_bytes`-style processing is prohibited for untrusted or potentially large inputs.
- Write exports incrementally where the adapter supports it.
- Release page buffers when the downstream stage finishes and avoid duplicate encoded/decoded copies.
- The constrained profile permits one active full-resolution buffer. A stage that needs two must document and test the overlap before the policy changes.

PWS-0202 scan transforms obey the same one-page incremental boundary. Their planner must charge
every raster simultaneously live during intrinsic orientation, crop, quarter-turn, deskew,
contrast, background cleanup, and terminal fit/serialization, plus metadata/scratch state and
native-allocation headroom. A no-op stage may reuse its input, but a profile label may not hide an
additional buffer. The worker must release each superseded raster at the earliest deterministic
stage boundary. If the pinned adapter cannot satisfy the selected central policy for the requested
geometry, the request fails before publication; the resource policy is not raised to admit it.

PWS-0204 sets OCRmyPDF `jobs=1`, Tesseract `OMP_THREAD_LIMIT=1`, one worker plus one child, and the
same application-wide single-heavy-operation gate. This prevents process/page fan-out and overlap
with another heavy document operation. OCRmyPDF may retain bounded multi-page private
intermediates, so PWS-0204 claims the complete operation temp/Job ceilings and cleanup behavior,
not an unevidenced per-page resident-memory profile.

PWS-0206 composes mixed output incrementally. Eligibility and final text presence are recorded for
one output page at a time. A page-copy path does not create a raster merely to share an OCR path;
an OCR path may hold one central-policy full-resolution raster and one bounded private page result.
The result is selected into the current output page or rejected, then the complete authenticated
page directory is removed before the next page begins. Page-result bytes, decoded rasters, and
extracted strings are never accumulated for the packet. Only the bounded content-free page-status
tuple persists until final verification.

PWS-0208 measures that incremental contract with workload
`pws0208-searchable-mixed-incremental` version 1 and report family
`pws0208-resource-evidence` version 1. The constrained workload must demonstrate
`peak_active_full_resolution_page_buffers <= 1`, `peak_active_page_results <= 1`, the selected
process-tree/Job-memory and private-temp ceilings, the configured sampling interval, a complete
private-temp measurement, and no unbounded page-result retention.
`peak_worker_job_commit_bytes` is Windows Job commit accounting and must not be described as RSS;
`peak_complete_process_tree_rss_bytes` is the separately sampled complete-tree RSS fact.
`sampled_worker_private_temp_peak_bytes`, `worker_private_temp_sampling_interval_ms`,
`worker_private_temp_sample_count`, and `worker_private_temp_measurement_complete` state the bounded
sampled-temp claim explicitly. A mandatory close may still prove safe cleanup after an earlier
tree-stop call fails, but that fallback cannot retroactively make the pre-close temp observation
complete or make the pre-stop Job sample terminal. Such a run retains incomplete sampled-temp
facts, discards the nonterminal Job sample, and cannot satisfy this evidence gate. Cancellation
evidence additionally records
`cancellation_acknowledgement_ms`, complete worker-tree termination, authenticated cleanup, and a
successful fresh retry.
The Windows AppContainer run produces the process, Job, and private-temp facts. The active-buffer
and active-result peaks are independently observed at the production adapter's scheduler seam
under the same exact workload and are labeled
`focused-production-adapter-instrumentation`; they are not claimed to cross worker RPC v1.
The repository-owned recipe is
[`pws0208-searchable-mixed-workload-v1.json`](../../benchmarks/pws0208-searchable-mixed-workload-v1.json).
`python -m tools.pws0208_resource_evidence <report>` validates a report after a focused Windows
integration harness produces it; that validation command does not execute or simulate the live
workload.

## Lazy work

- Opening a document does not render every page.
- Visible pages have priority; prefetch is limited to the configured adjacent radius.
- Obsolete preview requests are cancelled or ignored by generation correlation.
- OCR engines, language data, renderers, and optional components load only when required.
- Small manifest changes do not justify rescanning an entire case.

## Bounded caches

- Cache limits are expressed in bytes, not only item counts.
- In-memory previews use deterministic least-recently-used eviction by byte cost.
- Low-resolution thumbnails remain separate from full preview/export intermediates.
- Export intermediates never become an unbounded persistent cache.
- Cache artifacts are disposable and reproducible from immutable sources plus manifest intent.
- Normal exit, startup recovery, and low-disk paths clean only authenticated disposable artifacts.

## Bounded concurrency and backpressure

The constrained profile allows one heavy document operation at a time. Worker transports and runtime event queues retain explicit capacities. The UI keeps one correlated long operation and defers or rejects excess work rather than building an unlimited page queue. Active-view work outranks speculative prefetch. PaperWorkShell never creates one process or thread per page.

## Responsive GUI

Parsing, rendering, OCR, compression, structural validation, and export remain outside the GUI process. Long file operations do not run synchronously in the GUI event loop. Progress is incremental, accessible, and coalesced for announcements. Cancellation is checked at page or stage boundaries. Controlled responsiveness tests inject storage delay and count heartbeat violations against the policy; they do not claim to emulate an HDD.

## Storage efficiency and temporary-space formula

Each case has one authenticated `temp` area and separate byte quotas for disposable cache and temporary work. Before a bounded case write, the reservation system requires:

```text
current case bytes + outstanding reservations + requested bytes <= case quota
current area bytes + outstanding area reservations + requested bytes <= area quota
```

Before export destination publication, the trusted capacity probe requires:

```text
available bytes >= expected output bytes + 256 MiB safety reserve
```

Before a worker copies an immutable source into its authenticated AppContainer-private temp root,
the worker volume probe also requires:

```text
available bytes >= complete operation temporary-storage limit + 256 MiB safety reserve
```

Worker render/export planning then proves that staged sources plus the maximum raster or candidate
fit that complete operation limit. The single-heavy-operation coordinator prevents two such claims
from overlapping. Private files are opened only through the typed temp-storage seam and are closed
and removed on write, render, cancellation, or verification failure. The canonical report records
case/cache/temp bounds separately from configured limits. Strict no-follow inventories authenticate
the quiescent workspace before and after export; while export mutates disposable stages, one bounded
content-free observer records every active quota claim and its high-water mark under the quota lock.
The reported workspace peak is the conservative sum of the larger quiescent inventory and the
claim high-water, so it may overstate physical bytes but cannot omit a short-lived reserved stage in
the controlled M1 export. Worker-private bytes remain prospectively bounded rather than separately
sampled. Boundary and just-over-boundary tests are mandatory. Obsolete temporary artifacts are
removed promptly, but cleanup uncertainty preserves evidence rather than risking an authoritative
file or immutable original.

The image-transform operation applies the same private-volume rule before decode or temporary
raster creation. Its prospective amount includes the maximum complete transform-stage overlap and
fixed result artifact. Tests accept the exact capacity boundary, reject one byte less before
decoder work, force a disk-full failure after an identity-matched partial write, prove cleanup and
reservation release, and then complete a fresh retry. Cancellation and timeout keep the original
and authoritative manifest unchanged. These tests demonstrate bounded software behavior; they do
not emulate rotational storage or certify a VM or physical device.

The OCR operation applies the private-volume rule before source staging or OCR processing. Its
prospective amount is the complete selected temporary-storage limit, and the broker additionally
proves that the immutable source plus maximum volatile PDF candidate fit inside it. The worker
copies and hashes the source beneath one pinned random operation root, closes the case-source
handle, forces OCRmyPDF/Python/Tesseract temp below that root, and monitors the complete tree at
50-millisecond intervals while also checking strict inventories at stage boundaries. The monitor
tolerates only a private entry that disappears during legitimate engine churn; reparse/special
entries, excess bytes/entry count, or other inventory uncertainty fail closed. Candidate bytes
move only to a fixed volatile section, never a persistent case stage. Success, cancellation,
timeout, engine failure, and output rejection all require pinned-tree cleanup; uncertainty makes
the operation fail and no candidate is published.

This prospective check protects against an operation beginning when the private volume cannot
honor its configured ceiling. It does not reserve physical disk space or guarantee that an
unrelated process cannot consume capacity afterward. Focused OCR tests reject a prospective
capacity shortfall before staging, inject disk-full after a partial private source write, prove
pinned-tree cleanup, and then complete a fresh retry. Exact/over-limit adapter coverage remains
part of the boundary suite. The PWS-0208 constrained workload measures the complete Job and
parent-observed worker-private temp root; it does not replace the prospective admission checks or
make a physical-device claim.

For searchable mixed output, the destination and worker-private capacity probes additionally cover
the final PDF, bounded plan/status sections, the current staged source/page, and the largest
simultaneously live private page-result/raster overlap permitted by the selected policy. They do
not add every possible per-page result size together because page directories must not coexist. A low-space
refusal occurs before OCR or destination publication; a later disk-full, cancellation, deadline,
page-local OCR failure, malformed status, or native verification failure closes the current page
files, releases the output/status sections and reservations, removes only authenticated
disposable stages, and leaves the original, immutable source, manifest v2, export history v1, and
prior derivatives unchanged. Focused fault-injection must then prove a fresh retry succeeds.

## Deterministic slow-I/O and low-disk testing

Test adapters may delay read, write, flush, rename, and directory checkpoints and may report controlled free-space or disk-full results. They prove responsive orchestration, atomic autosave, cancellation, and recovery without claiming SSD/HDD equivalence. Tests cover preflight rejection, exact-boundary success, disk full during import/export/temp work, cleanup, restart recovery, and original/manifest preservation.

## Graceful resource failure

At minimum, stable sanitized failures cover insufficient space before import/export, disk full during copy/export, temporary quota exceeded, cache allocation rejected, worker memory budget exceeded, timeout, termination, and destination permission failure. A failure must not modify an original, publish corrupt success, corrupt the authoritative manifest, expose content in logs, leave the UI permanently busy, or require terminating the entire application to regain control.

## Measurement and regression discipline

The canonical resource report contains only:

- Workload, policy/profile, schema, revision, and dependency versions
- Input/output byte and page counts
- Peak relevant process-tree/Job resident memory
- Peak cache and temporary workspace bytes
- Maximum heavy-operation concurrency
- Phase and total duration
- Cancellation acknowledgement when the scenario exercises cancellation
- UI heartbeat violation count and maximum observed gap
- Worker exit/recycle and recovery facts
- Individual budget results and overall run status

The PWS-0208 report also binds workload/report versions, repository revision and policy version,
exact OCRmyPDF/Tesseract/pikepdf/qpdf/pypdfium2/PDFium identities, phase and total durations,
verified derivative byte size and SHA-256, complete-tree RSS, worker-process and worker-Job commit
peaks, sampled private-temp facts, peak
active full-resolution buffers/page results, cancellation acknowledgement, limit failure,
cleanup, and retry results. Those fields use numeric or closed values and remain content-free.

It never contains original filenames, full paths, OCR/document text, form values, extracted metadata, page or signature images, names, or addresses. `peak_complete_process_tree_rss_bytes` sampling may miss extremely short-lived processes; the separately recorded `peak_worker_job_commit_bytes` is Windows Job commit accounting and is not relabeled as RSS. Case-workspace peak evidence combines strict quiescent inventories with event-driven active quota-claim high-water; it is a conservative upper bound and may exceed the instantaneous physical byte count. PWS-0208's trusted parent samples the owned AppContainer temporary root on each pump and retains the final observation after termination. The report must mark that measurement incomplete if required observations cannot be made; a partial sample series cannot pass the temp budget. These limitations are stated in the current versioned report contract.

Duration is recorded but is not a hard gate across heterogeneous developer machines. The
PWS-0208 workflow is headless, so UI-heartbeat facts remain in the canonical UI-bearing M1 report
rather than being invented for this narrower operation report. Memory, cache, concurrency, disk,
responsiveness, cancellation, privacy, and correctness are hard gates. A budget may not be raised
merely to make a regression pass: first find the source of the regression, optimize or reject
oversized work, and document any evidence-based amendment through an ADR.
