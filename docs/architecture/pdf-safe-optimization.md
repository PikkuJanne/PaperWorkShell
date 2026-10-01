# Safe PDF object and stream optimization

- Task: PWS-0502
- Decisions: PWS-D009, PWS-D013, PWS-D015, PWS-D024, PWS-D029, PWS-D036
- Profile: `safe_object_stream_v1`

## Selection and verification

The ordinary PDF/image, flattened-overlay, and searchable export workers first assemble and
verify their normal derivative. In the same restricted worker job, they then attempt one smaller
private candidate. The imported originals and verified baseline remain unchanged. Only the
selected file is copied into the existing bounded output section, validated by the broker after
worker termination, and passed to the existing no-replace destination commit.

The fixed profile uses the admitted pikepdf/qpdf implementation to compress unfiltered streams
losslessly and generate object streams. It disables content normalization, linearization,
metadata-version rewriting, generalized stream decoding, and Flate recompression. It does not
downsample, rasterize, change image quality or color, remove metadata, strip attachments, or
implement permanent redaction. Source-feature handling during assembly retains its earlier
documented policy.

All reachable resource entries are retained, including entries not invoked by a page content
stream. No content-parser heuristic decides that a reachable resource is disposable. The writer
may omit objects unreachable from the trailer roots. Cross-reference representation and file
identifiers may change; these are excluded from semantic comparison. This is ordinary derivative
rewriting and makes no claim about secure deletion or removal of sensitive information.

A smaller candidate must pass all of these checks:

1. Strict reopen, expected page count, no encryption, bounded object inventory, and syntax checks.
2. A canonical comparison of the complete trailer-reachable object graph, including ordered page
   content, text/font structures, overlays, annotations, metadata, attachments, and shared/cyclic
   references. Object numbers and storage-only trailer keys are normalized. Unfiltered streams
   and single Flate streams without decode parameters are compared by their decoded bytes;
   other filters and encoded bytes remain exact. Incremental Flate decoding bounds each chunk
   and rejects expansion beyond the semantic budget (using one sentinel byte to detect overflow).
3. The existing fixed qpdf structural check without recovery or warnings.
4. Sequential PDFium rendering and exact raster/geometry comparison of every baseline/candidate
   page using the existing bounded verification profile.
5. Final candidate and baseline size/hash checks.

These checks preserve the existing limited rendering claims. They do not establish universal
reader compatibility, PDF/UA, portal acceptance, signature validity, or a target-size guarantee.
The stream/object semantics follow the admitted library's
[save interface](https://pikepdf.readthedocs.io/en/stable/api/main.html#pikepdf.Pdf.save).
Tests exercise the pinned local version; online documentation is supporting context.

## Outcomes and failure handling

| Outcome | Selected bytes | Evidence |
|---|---|---|
| `optimized` | The separately verified, strictly smaller candidate | Baseline, candidate, selected sizes and verified page count |
| `no_reduction` | Exact verified baseline | Candidate reached the smaller-than-baseline byte cap; no candidate verification is claimed |
| `skipped_budget` | Exact verified baseline | Existing temporary-storage quota could not reserve a complete attempt; no candidate was created |

The candidate stream is capped at baseline size minus one, so an ineffective rewrite cannot grow
without bound. A cap-exceeded result records no completed candidate size. An invalid smaller
candidate fails export. Disk-full, low-disk, cancellation, timeout, semantic-budget, parser, and
cleanup failures also fail export with existing sanitized error codes; they never become a
successful optimization claim. Failed attempts remove their private candidate. The operation's
existing authenticated cleanup removes remaining private files on completion or failure.

The pinned writer can rewrite ASCIIHex streams to Flate even with generalized decoding disabled.
This profile rejects that representation change rather than introducing another decoder into the
semantic comparator. The regression corpus records this conservative compatibility limitation.
Streams that reference external files through `/F`, `/FFilter`, or `/FDecodeParms` are rejected.

## Resource accounting

The worker inventories current private operation files with bounded object count and traversal
depth, rejecting reparse points, links, and non-regular files. It accounts for the baseline,
staged sources, any remaining page scratch, transferred output-section capacity, and additional
live raster/status sections. A candidate is attempted only if its complete smaller-than-baseline
extent fits the existing `ResourceLimits.temporary_storage_bytes`, derived from the central
`ResourcePolicy`. A free-space check retains the existing disk reserve.

The graph byte budget is at most one quarter of the operation's memory budget, leaving space for
native document objects, bookkeeping, and raster verification. Native parser allocations remain
subject to the existing worker Job Object memory/CPU limits and broker deadline. Rendering is
sequential and discards pixel buffers after hashing. The task creates no extra worker, queue,
cache, concurrency, or larger resource policy. The existing runtime-preparation delay described
in [known limitations](../../KNOWN_LIMITATIONS.md) remains applicable before worker launch.

## Reports and compatibility

`PdfOptimizationReport` version 1 contains only a closed profile/outcome, numeric sizes, and the
number of pages verified for a selected candidate. Base PDF export report version 2 adds this
required record and binds its selected size and page count to the enclosing verification report.
Legacy report version 1 retains its exact fields and decoder behavior. Future/invalid versions,
extra fields, impossible size relationships, and inconsistent page counts are rejected.

Existing flattened/searchable envelopes carry the versioned base report. Their plan versions,
operation names, transport schema, overlay checks, and OCR page outcomes retain their meanings.
The new fixed optimization stage has no user-selected parameters and is recorded in the current
report. Existing legacy reports never acquire an optimization claim by inference.

Current result claims and package summaries state the measured before/after sizes, or explain why
the baseline was retained. History and retry-journal version 1 continue to store the final
size/digest and original verification checks. A history-only recovery after restart therefore
does not recreate transient optimization details or claim that it repeated the optimization.
No case/history schema migration is introduced.

## Standalone boundary

This is local export behavior with PaperWorkShell-owned data. It introduces no sibling dependency,
shared service, portfolio handoff, public CLI, or release compatibility claim. Existing public
release facts remain `TBD` until the owning packaged-release tasks provide evidence.
[PWS-0503 image analysis/re-encoding](pdf-image-stages.md) uses this verified baseline in a
separate explicitly requested operation. Target-size search remains PWS-0504 work.
