# Deterministic verified PDF export

- Status: Binding implementation contract
- Task: PWS-0112
- Extension: PWS-0201 ordinary JPEG/PNG page export through `pdf.export` version 2
- Extension: PWS-0206 [verified searchable mixed-document output](searchable-mixed-output.md)
- Extension: PWS-0307 [verified flattened visual-overlay export](flattened-overlay-export.md)
- Extension: PWS-0502 [safe object/stream optimization](pdf-safe-optimization.md)
- Separate operation: PWS-0503 [explicit image analysis/re-encoding](pdf-image-stages.md)
- Package policy: PWS-0506 [conservative portal profile](portal-export-profile.md)
- Decisions: PWS-D009, PWS-D011, PWS-D015, PWS-D029, PWS-D037, PWS-D038

## Scope and claims

PWS-0112 exports the current manifest page sequence as a new, unencrypted PDF. It supports page
selection, ordering, and accumulated quarter-turn rotation. Active crop or overlay
intent is rejected until the task that owns its export semantics is complete. Imported originals
and existing destination names are never replacement targets.

A successful result makes only the closed claims recorded by `PdfExportCheckCode`: strict reopen,
clean pikepdf/qpdf structural checks, expected page count, no encryption, successful render of every
page, matching rendered order and rotation, and final-name destination reopen. When a source has
digital-signature markers, the result says **unsigned derivative**. It does not claim signature
validity, PDF/A, PDF/UA, portal acceptance, legal validity, permanent redaction, or universal viewer
compatibility.

PWS-0201 adds `pdf.export` version 2 for ordinary desktop **Export pages as PDF**. Its explicit
`pikepdf_image_pages_v2` assembly profile accepts PDF, JPEG, and PNG sources without OCR or a
language-pack dependency. Version 1 keeps its PDF-only `pikepdf_page_copy_v1` profile and semantic
fingerprint meaning. Both versions support selection, order, and quarter-turn rotation. The image
profile applies intrinsic EXIF orientation before that user rotation and composites alpha over
white. Active crop, deskew, contrast, background cleanup, and visible overlays fail closed on this
ordinary export path; their later explicit export workflows retain their existing contracts.

PWS-0206 composes these base checks through the additive `pdf.export.searchable` version 1
capability. It preserves `pdf.export` version 1 exactly: searchable execution has its own plan,
bounded page-status output, per-page text-presence report, and semantic fingerprint. Born-digital
pages selected by the mixed eligibility policy continue through the page-copy/render-comparison
path; OCR-required pages must independently pass the supported text-presence check before they are
counted as having extractable text. See
[Verified searchable mixed-document output](searchable-mixed-output.md) for the closed page matrix
and limited claims.

PWS-0307 likewise leaves this base capability unchanged and adds `pdf.export.flattened` version 1.
Its separate plan binds active crops, visible overlay intent, and authenticated placed-signature
RGBA metadata; its transient report wraps the unchanged structural report with exact Form-placement,
font-policy, and visual-signature terminology checks. See
[Verified flattened visual-overlay export](flattened-overlay-export.md) for the authority flow,
explicit Form-XObject placement, resource bounds, and limited compatibility claims.

## Layer ownership

| Layer | PWS-0112 responsibility |
|---|---|
| `domain.pdf_export` | Path-free immutable plan, profile, progress, check, report, outcome, and closed error models |
| `app.pdf_export` | Manifest-to-plan orchestration, retained-source lifetime, worker call, destination commit, and post-commit history settlement |
| `storage.source_ingest` | Manifest-bound, retained read handles for exact committed source identities and digests |
| `worker.pdf_export_broker` | Operation IDs, reduced handle transfer, bounded plan/output/report channels, worker lifecycle, result correlation, and post-termination source revalidation |
| `worker.pdf_export_operation` | Worker-private source copies, native-adapter composition, bounded output/report writes, and deterministic cleanup |
| `pdf.export_adapter` | pikepdf assembly, strict reopen, qpdf check, PDFium render comparison, and verified candidate report |
| `storage.export_destination` | Authenticated local destination, destination-local staging, no-replace rename, exact final-name reopen and digest verification |
| `domain.pdf_export_history` / `storage.export_history` | Path-free canonical audit record and atomic case-local persistence |

The domain has no filesystem, UI, worker, storage, or document-engine dependency. The application
layer depends only on structural ports. PDF libraries remain worker-side. The worker never receives
a case path, destination path, manifest writer, history writer, or directory capability.

Desktop composition keeps export authority separate from the always-live organizer worker.
`PdfExportWorkerComposition` owns an on-demand client that requires only the `pdf.export` capability,
admits one explicitly validated qpdf executable, permits at most the worker plus that fixed child,
and terminates on its construction thread. Closing the composition permanently revokes every
previously exposed reference to its broker, so shutdown cannot be followed by a fresh worker
launch. The application root wires its broker to the concrete
manifest, committed-source, destination, history, and retry adapters through
`build_windows_pdf_export_service`. The selected versioned resource policy drives worker memory,
CPU, temporary storage, and output limits rather than relying on scattered constants. The normal
profile permits 1 GiB temporary storage and 512 MiB output; the constrained profile permits
512 MiB temporary storage and 256 MiB output. Both retain bounded wall time, page count, and
verification pixels. Exceeding any bound fails closed before destination commit.

The image profile additionally derives its source-pixel admission ceiling from the selected
`ResourcePolicy` memory budget. It shares the existing single-heavy-operation coordinator,
qpdf-only launcher, retained-source batch, output section, report, and publication path. OCR tools
and language packs are neither configured nor consulted by ordinary image-page export.

## Deterministic plan

`build_pdf_export_plan` accepts one validated manifest snapshot and produces versioned, immutable
intent. Source entries contain internal IDs, exact size/digest/page-count facts, and closed
signature/encryption states. Page entries contain output index, source ID, source-page index, and
the accumulated rotation. Source order is canonical first-use order.

Plan version and profile version must match exactly: version 1 permits PDF sources, while version
2 permits PDF/JPEG/PNG. Image sources have exactly one page and `not_applicable` signature and
encryption states. The v2 semantic projection additionally binds each source media type. The
worker-plan envelope stays version 1, with the nested plan version and exact profile selecting
the explicit semantics. Unknown future versions and mismatched version/profile pairs fail before
native parsing or destination publication.

The semantic fingerprint hashes only the versioned semantic source/page/profile projection. Case
IDs, revisions, timestamps, CAS tokens, and source catalogue ordering do not change the fingerprint.
They still bind authority and freshness and are checked separately. The same immutable sources,
page intent, profile, and admitted engine versions therefore produce the same semantic result;
tests additionally bind byte-identical output for the supported synthetic representative fixture.

## Handle-only worker operation

The trusted broker performs the following sequence:

1. Validate the plan and retained source batch against the same case, manifest revision, manifest
   digest, CAS token, source IDs, media types, sizes, page counts, digests, and policy states.
2. Allocate a read-only plan section, one fixed-capacity output section, and one bounded report
   pipe. Generate distinct operation/object IDs and bind all of them into the exact negotiated
   `pdf.export` operation version under the unchanged RPC v1 envelope.
3. Start the zero-capability AppContainer worker and duplicate only reduced source-read,
   plan-section-read, output-section-write, and report-pipe-write handles.
4. Pump validated progress until a terminal result, then terminate the complete one-shot job tree.
5. Only after confirmed termination, revalidate every retained source and authenticate the report,
   output reference, byte size, digest, page count, checks, and semantic fingerprint.
6. Expose the bounded candidate stream to the trusted destination adapter. Closing the candidate
   revokes its volatile output authority.

The worker authenticates its AppContainer-private temp root and requires the complete operation
temporary-storage bound plus a fixed 256 MiB volume reserve before creating its per-operation
directory. It then copies each exact source handle through the typed private-temp seam, rehashes it,
and closes the case-source handle before pikepdf, qpdf, or PDFium parses the copy. Source totals plus
the maximum candidate are arithmetically bounded by the operation limit. A partial source or
candidate created before a disk-full failure is closed and removed with the pinned operation
directory; a fresh operation can recover without worker-profile cleanup. Temporary source and
candidate paths never cross RPC. The fixed qpdf executable, when explicitly configured by the
launcher, is the only additional environment value admitted to the worker; no PATH search or shell
is used.

For image assembly, temporary admission reserves all source copies plus the maximum candidate,
then divides the remainder into bounded scratch for one image page. The RGBA raster and its
one-page PDF are removed after each page. Limits cover decoded dimensions, pixels, metadata,
decompressed bytes, output extent, memory, and time; exceeding the bound rejects the operation
instead of reducing source resolution. Assembly and source-side verification each normalize
one image at a time inside the same private operation directory.
Pikepdf retains compressed output page objects until the final save. Before each generated image
page is appended, its complete private PDF size is added to a running total that must fit the
existing candidate-byte capacity. This is a conservative admission bound, since temporary page
wrappers add overhead. The worker Job remains the hard memory limit for native output objects;
the sequential raster/scratch policy does not imply that the assembled PDF has no retained state.

## Assembly and verification

The worker adapter validates all sources before assembly, creates one exclusively new candidate,
and materializes inherited page media/crop/resource/rotation attributes before copying pages in
plan order. It uses pikepdf deterministic IDs and fixed save options. Candidate size, source bytes,
page/object counts, render pixels, bitmap bytes, memory, temporary storage, wall time, and CPU time
are bounded before or during expensive work.

Before a report exists, the adapter must complete all of these checks:

1. Reopen the candidate with pikepdf recovery disabled and reject encrypted, malformed, warning,
   wrong-page-count, excessive-object, or rotation-mismatch state.
2. Run the fixed qpdf 12.3.2 `--check --suppress-recovery --no-warn -- <path>` command without a
   shell; any non-clean, unknown, timeout, cancellation, or output-budget state fails closed.
3. Render each planned source page with its additional rotation and the corresponding output page
   with PDFium under the fixed verification profile. Dimensions, stride, pixel count/digest, crop
   box, pixel format, background, annotation policy, and effective rotation must match.
4. Revalidate all source copies, then remeasure the candidate. Any change between measurements
   removes the candidate and blocks a report.

Raw native warnings, exceptions, paths, PDF values, document text, filenames, and tool output are
not returned. Failures are stable `PdfExportErrorCode` plus closed context.

The v2 image branch reuses the pinned Pillow normalization adapter and `RasterPdfPageAdapter`.
It embeds lossless opaque DeviceRGB samples in a single PDF image at the existing fixed 300 DPI
geometry (`pixels × 72 / 300` points); embedded source DPI is not a physical-size authority.
The raster wrapper strictly reopens and checks its geometry and complete decoded RGB digest.
Final candidate verification freshly rebuilds each authenticated source image page and compares
its PDFium rendering with the corresponding output page after the requested quarter turn. No
text layer is created, and no searchability check is claimed.

## Destination publication

The GUI process accepts only a local, absolute Windows destination with a validated parent and
safe final component. It rejects UNC/device/ADS/reserved/reparse paths and an already-existing
destination before reading candidate bytes. Before worker launch, the application conservatively
estimates the derivative from validated source sizes; the destination adapter authenticates the
parent and requires that estimate plus the selected policy's 256 MiB free-space reserve. An
insufficient or failed capacity probe produces a stable path-free failure before expensive work.
It then:

1. opens and authenticates the parent directory;
2. creates an exclusive random stage in that exact directory;
3. streams only fixed-size bounded chunks while checking cancellation, byte count, and SHA-256;
4. flushes, rewinds, and readback-verifies the retained stage identity, size, and digest;
5. performs a native same-directory no-replace rename;
6. resolves the retained file identity at the final name and reopens that exact final name to
   verify identity, size, and digest again.

Only the exact `COMMITTED` state becomes a successful application outcome. `DESTINATION_EXISTS`
never overwrites the existing file. An indeterminate post-rename identity is preserved and reported
as unknown rather than deleted or described as verified. Destination denial, disk full, short
write, corruption, cancellation before rename, race, or final reopen failure cannot produce a
success result.

## Commit and history boundary

Source handles and the volatile worker candidate stay retained until final-name publication ends.
After worker verification but before destination publication, the application reads one validated
UTC instant and prepares a bounded, versioned, path-free retry receipt containing the exact
canonical export-history record. The prepared receipt remains private unless destination commit
and reopen succeed. A clock, record-validation, or serialization failure therefore occurs before
the irreversible destination boundary.

Before worker or destination work begins, identifier allocation excludes the complete validated
manifest namespace and every durable export-history record, including records whose manifest link
is still pending. If history IDs cannot be read safely, export stops before creating a derivative.

The path-free receipt is durably published in the protected case journal as `PREPARED` before the
destination call. The corresponding `COMMITTED` phase is prewritten but cannot become replayable
until exact destination publication and reopen succeed. A known pre-commit destination failure
removes only the exact preparation; an indeterminate destination state preserves it because the
application cannot safely infer whether the external rename occurred.

After a successful destination reopen, the application publishes the `COMMITTED` marker and
creates a `COMMITTED_HISTORY_PENDING` outcome carrying that receipt. It then writes the exact
record and CAS-links it in the manifest. Successful linkage upgrades the outcome to
`COMMITTED_RECORDED`; the outcome retains its receipt value while the exact durable retry marker is
removed only after history/link success is independently established.

A history, manifest-link, or post-commit progress failure never relabels or recreates the already
verified output. A fresh history service can use the receipt with an independently selected,
exactly matching case ID to append and link the exact original record even after the manifest
advances; that retry cannot invoke source, worker, or destination code.

On restart, PWS-0113 first validates stable history against every manifest reference independently
of receipt inventory. One authenticated retired predecessor may restore an absent stable name;
unbound `.tmp` candidates are never promoted, and ambiguous retirement state is preserved before
generic temp cleanup. It then replays `COMMITTED` receipts through history and manifest services
only. A `PREPARED` receipt is commit-ambiguous and stays inert unless the exact record already present in
case history or its exact manifest reference independently proves the downstream commit. Invalid
journal entries are not replayed, and their parseable IDs remain reserved. Receipt settlement
precedes general case-temp cleanup. The complete order, limits, and diagnostic result are defined
by [Startup recovery and sanitized diagnostics](startup-recovery-and-diagnostics.md). The
destination path and filename are deliberately absent from the record and receipt; see [Export
history v1](../schemas/export-history-v1.md).

When a saved case opens, TRACE reads the latest manifest-linked history entry. The Exported
view presents its saved page count and recorded checks once the record version and canonical
digest match the manifest reference. The user selects a local PDF before TRACE checks its exact
byte size and SHA-256. A match enables Open exported PDF, which repeats the check before handing
the file to the desktop viewer. The selected path lives in memory for the open case. Page mapping,
recipe status, and OCR findings require evidence beyond history v1.

Searchable output reuses this publication/retry lifecycle without changing released manifest v2 or
export-history v1 meaning. Searchable eligibility and per-page outcomes remain in the separately
versioned transient verification report; they are not smuggled into an existing history field or
operation-history payload. A future durable representation requires an explicit schema transition
with old/current/future/invalid fixtures rather than an in-place reinterpretation.

## Failure and cleanup invariants

- Cancellation and deadline probes run before planning side effects, around worker lifecycle,
  native checkpoints, candidate transfer, and destination publication. Cancellation is not
  observed after a confirmed native commit.
- Crash, malformed RPC/report, wrong correlation, partial output, failed termination, or failed
  source revalidation prevents candidate exposure.
- Candidate verification failure cleans only worker-private operation artifacts. Destination
  failure removes only the authenticated stage when its identity is known not to be the final
  output.
- Originals, immutable case copies, prior verified exports, the last valid manifest, and unrelated
  paths remain unchanged on every failure path.
- Routine errors and representations contain no source filename, destination, full path, digest,
  document text, or native exception detail.

## Verification evidence

Unit and supported-Windows integration tests cover multi-source reorder/rotation, repeated export,
strict reopen, qpdf failure, page-count/rotation mismatch, render failure and pixel mismatch,
encryption, corrupt input/output, size/resource/cancellation/deadline limits, worker crash and
termination failure, source change, destination collision/denial/disk-full/short-write/readback
corruption, final-name reopen, history retry, and source-hash preservation. The live integration
uses the restricted worker with real pikepdf/libqpdf, qpdf, and PDFium and requires no skip for an
installed release dependency.

`test_image_pdf_export.py` covers both formats, all eight intrinsic EXIF orientations, exact
embedded RGB samples and white alpha composition, an additional quarter turn, original hashes,
scratch cleanup, capacity rejection, and old/current/future/invalid version behavior.
Cancellation after scratch allocation and a rendered-digest mismatch both remove the candidate
and scratch, preserve the source hash, and permit a fresh successful export.
`test_image_pdf_export_worker_windows.py` imports JPEG and PNG with real worker inspection, closes
and reopens the case, then exports and records the verified derivative using a launcher with no
OCR resources. It checks candidate reopen, seven performed verification checks, immutable external
and committed source hashes, termination, and cleanup.
