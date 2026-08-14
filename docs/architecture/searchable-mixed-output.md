# Verified searchable mixed-document output

- Status: PWS-0206 implementation and evidence contract
- Task: PWS-0206
- Decisions: PWS-D016, PWS-D021, PWS-D029, PWS-D037
- OCR boundary: [Bounded local OCR worker contract](ocr-worker.md)
- Base export boundary: [Deterministic verified PDF export](pdf-export.md)
- Trust flows: [DF-OCR and DF-EXPORT](../security/data-flows.md#df-ocr--local-cleanup-and-recognition)
- Resource policy: [Low-resource engineering policy](../performance/LOW_RESOURCE_POLICY.md)

## Scope and limited claims

PWS-0206 adds `pdf.export.searchable` version 1 as a separately versioned searchable mixed-output
contract. It combines PDF, JPEG, and PNG pages in current manifest order, preserves an eligible
born-digital PDF page without unnecessary rasterization, and applies local OCR only to an eligible
image or pixel-transformed page. It does not reinterpret the released `ocr.process` version 1 or
`pdf.export` version 1 operations. It also does not change
[case manifest v2](../schemas/case-manifest-v2.md) or
[export history v1](../schemas/export-history-v1.md); the searchable plan and verification report
are transient, separately versioned projections over an authenticated manifest snapshot.

The text-presence claim is deliberately narrow: under
`pdfium-non-whitespace-text-v1`, the supported worker-side extractor found at least one
non-whitespace character on a named output page. That fact does not establish OCR correctness,
reading order, language, accessibility, PDF/UA, portal acceptance, legal validity, or signature
validity. A page without verified extractable text is retained with a page-local “Needs review”
outcome. It does not make the whole operation fail and is never counted as searchable.

## Layer and authority ownership

| Component | Responsibility | Boundary |
|---|---|---|
| `domain.searchable_pdf_export` | Versioned path-free plan, profile, eligibility, per-page outcome, report, outcome, fingerprint, and closed errors | Standard-library-only; no worker, storage, UI, or native engine |
| `app.searchable_pdf_export` | Load one manifest snapshot, retain exact sources, call a structural broker, publish through the existing destination/retry/history ports, and return the exact report | Toolkit/engine-free; does not run OCR or parse a document |
| `app.source_ingest` / `storage.source_ingest` | Lend one manifest-bound PDF/JPEG/PNG batch with exact identities, sizes, page counts, and digests | Storage owns handles and source reauthentication; no worker/PDF engine import |
| `worker.protocol` plus `worker.searchable_pdf_export_*` | Add `pdf.export.searchable` v1 and canonical bounded plan/status/report wire documents | Existing operations remain unchanged; no path or document/OCR text field |
| `worker.operations.SearchablePdfExportOperationHandler` | Adopt the exact reduced handles and invoke one injected adapter | Restricted host only; no manifest/history/destination authority |
| `pdf.searchable_export_adapter` | Inspect eligibility, preserve or rasterize one page, invoke admitted page-local OCR, assemble, reopen, qpdf-check, render-compare, and verify text presence | Worker-only operation composition; page-private native objects and paths never cross |
| `pdf.text_presence` | Return bounded PDFium character/non-whitespace counts for selected pages | Never constructs or returns an extracted string |
| `pdf.raster_pdf_page` | Wrap one transformed RGBA page in a strictly reopened private PDF at fixed image DPI or the authenticated PDF page's physical geometry | One-page bounded worker-private artifact only |

The searchable worker receives only plan/source readers and bounded PDF/status/report writers. It
has no case root, manifest writer, export-history writer, cache publisher, external destination, or
original-write handle. The trusted application sees only the authenticated candidate stream and
content-free report after confirmed complete-Job termination and source revalidation.

## Pure plan and page policy

`domain.searchable_pdf_export` owns path-free, standard-library-only source, page, profile, plan,
eligibility, per-page outcome, verification-report, export-outcome, and closed-error models. The
plan binds the current case ID, manifest revision/digest/CAS token, immutable source identity and
digest facts, exact page order, every supported transform intent, the base PDF export profile,
the admitted OCR engine-profile digest, and these policy identifiers:

| Policy | Versioned identifier |
|---|---|
| OCR eligibility | `mixed-page-ocr-eligibility-v1` |
| Text presence | `pdfium-non-whitespace-text-v1` |
| Local language sets | `latin-default-v1`, `german-fast-v1`, or `english-german-fast-v1` |

The semantic fingerprint covers only the deterministic semantic projection; authority and
freshness facts remain separately authenticated. An active overlay is rejected because PWS-0206
does not silently add new overlay-export semantics. Encryption must be confirmed absent for every
PDF. JPEG and PNG sources are one-page inputs.

Each language-set identifier resolves through the closed product mapping and is checked against
the authenticated local runtime before source authority is borrowed. A missing selected pack fails
with stable language-unavailable remediation; it does not silently fall back to English, download
data, or mutate the case. The optional German bytes are admitted only by the packaged fingerprinted
catalog described in the [OCR worker contract](ocr-worker.md). If no explicit export profile is
provided, the service atomically captures the app-scoped process-session selection and builds the
otherwise-default profile around that exact language-set ID. The same path-free runtime-authority
digest must be present on both the app context and worker composition before the service can run.

Eligibility and terminal outcomes are closed and one-to-one where required:

| Eligibility code | Required worker fact | Admitted terminal outcome |
|---|---|---|
| `preserve_extractable_text` | PDF page already has supported extractable text and has no pixel-transform intent | `existing_text_preserved` |
| `ocr_required_image_source` | JPEG or PNG source | `ocr_text_verified`, `ocr_no_text_retained`, or `ocr_failed_retained` |
| `ocr_required_image_only_pdf` | Signature-clear PDF page has no supported extractable text and no pixel-transform intent | `ocr_text_verified`, `ocr_no_text_retained`, or `ocr_failed_retained` |
| `ocr_required_pixel_transform` | Signature-clear PDF page has crop, deskew, contrast, or background-cleanup intent | `ocr_text_verified`, `ocr_no_text_retained`, or `ocr_failed_retained` |
| `ocr_blocked_signature_state` | PDF signature-marker state is marker-present or unknown and the page cannot be safely OCR-modified | `ocr_blocked_signed_source_retained` |

The corresponding searchability facts are exact:

- `existing_text_preserved` means `extractable_text_present_existing` and has no failure code.
- `ocr_text_verified` means `extractable_text_present_ocr` and has no failure code.
- `ocr_no_text_retained` means `not_verified` plus `no_extractable_text`.
- `ocr_failed_retained` means `not_verified` plus `ocr_engine_failed`.
- `ocr_blocked_signed_source_retained` means `not_verified` plus
  `signature_state_blocked`.

Every report contains exactly one outcome in output-page order and binds each one to the planned
page UUID and index. Aggregate counts are derived from that bounded tuple; a whole-document
“searchable” claim is permitted only when every page has one of the two extractable-text facts.

## Born-digital preservation and OCR composition

Eligibility inspection and text-presence verification happen inside the restricted worker. The
worker never returns extracted document text. It returns only page IDs/indices, closed codes,
counts, booleans, versions, and numeric resource facts through bounded validated structures.

For `preserve_extractable_text`, assembly copies the original PDF page through the base pikepdf
page-copy path and applies only supported lossless page intent. It does not render the page to a
bitmap and feed it through OCR. The base PDFium comparison still proves the expected visible page,
and the page-local extractor must find non-whitespace output text before
`existing_text_preserved` can be reported.

For an OCR-required page, the searchable worker creates one private one-page base PDF and invokes
the same admitted OCRmyPDF/Tesseract adapter and `searchable-pdf-v1` profile used by PWS-0204. It
does not invoke the `ocr.process` RPC or transfer that operation's broker-owned candidate back into
the worker. The private OCR result remains page-scoped under the searchable operation root. Engine
success is not itself searchability evidence: the result must visibly match the base render and
independently pass the output text-presence profile. If the local engine reports its closed
recognition failure, or if the result has no verified text, the visual base page is retained and
receives its exact page-local review outcome. One page failure does not erase a successful result
for another page and does not become an unqualified aggregate success claim.

## Signed-source fallback

`markers_detected` and `unknown` are conservative structural states, not cryptographic
verification. A page that already has supported extractable text may be preserved; OCR or a pixel
transform is never applied to a marker-present or unknown-signature PDF page. A blocked page is
retained visually and reported as `ocr_blocked_signed_source_retained`.

Any exported derivative whose plan includes a marker-present or unknown-signature PDF source is
labeled **unsigned derivative**, including a derivative that preserves the marked page while
combining or reordering content. The imported original and immutable case copy remain unchanged.
PWS-0208 adds a reason-aware presentation contract before that modifying export: it distinguishes
detected markers from a marker state that could not be confirmed, shows the **unsigned derivative**
label and consequence, and requires the exact action **Create unsigned derivative**. **Cancel** is
the initial and default action; Escape cancels. The presentation does not validate a signature,
persist confirmation, or grant worker authority by itself. The trusted application must bind the
explicit choice to its validated source-scoped confirmation state before it creates an operation.

Confirmation never turns a marker-present or unknown state into permission for OCR or a pixel
transform. The PWS-0206 fail-closed page policy remains authoritative, so the marked visual page
is retained and reported with its exact blocked outcome. The standalone dialog is intentionally
not coupled to a destination or worker: cancelling it creates no output and starts no document
operation.

## Reopen, render, and searchability verification

No candidate reaches destination publication until the worker has completed the base PWS-0112
verification profile and the page-local text profile:

1. Reopen without recovery using pikepdf; reject encryption, malformed structure, warning state,
   wrong page count, and unsupported structural state.
2. Run the admitted qpdf 12.3.2 structural check through its fixed no-shell argument boundary.
3. Render every planned source/output page with PDFium and compare geometry, rotation, and visible
   pixels under the fixed base-export profile.
4. Use PDFium text-page extraction only inside the worker to record whether each output page has
   non-whitespace text. Extracted strings are discarded immediately and never cross RPC, enter a
   routine log, or become a case artifact.
5. Revalidate all immutable source copies and the complete candidate before issuing the correlated
   content-free report.

The trusted destination adapter then performs the existing no-replace, readback, final-name reopen,
and digest checks. Searchable-specific details remain in the separately versioned transient report;
the committed audit record keeps the released export-history v1 meaning.

## Incremental resource and cleanup contract

PWS-0206 uses the central versioned `ResourcePolicy` and the application-wide single-heavy-
operation gate. It does not create a second unbounded queue, cache, executor, or page fan-out.

- Plan and report collections are bounded by the admitted source/page limits before native work.
- At most one full-resolution page raster and one private page-local OCR result are live at a time under
  the constrained profile.
- A selected or rejected page-local OCR result is closed and its authenticated page directory is
  removed before the next page begins; result bytes are never accumulated for a packet or persisted
  as a cache.
- Source copying/hashing and destination publication remain chunked. Worker-private PDF assembly
  may use bounded native state, but never a whole-document collection of full-resolution rasters.
- Candidate, report, render-pixel, temporary-storage, Job memory/CPU, wall-time, and destination
  capacity checks use the selected central policy and fail before publication.
- Cancellation is checked before each page, around page-local OCR creation/selection, during native
  verification, and before irreversible destination commit. Low-disk, disk-full, cancellation,
  timeout, parser/OCR failure, and malformed-report paths remove authenticated disposable state,
  release volatile sections and reservations, and preserve originals, manifest v2, export history,
  and prior derivatives.

PWS-0208 adds the measured large mixed-job scheduler/process-tree evidence under the separately
identified `pws0208-resource-evidence` version-1 report. PWS-0206 proves the bounded page-local
composition and cleanup semantics; neither report infers a named-hardware claim from synthetic
constrained-profile tests. The exact source/page/scenario recipe is bound by
[`pws0208-searchable-mixed-workload-v1.json`](../../benchmarks/pws0208-searchable-mixed-workload-v1.json).

## Deterministic synthetic evidence

The repository-owned generator creates three closed fixtures without a system font, external
asset, real document, or personal data. The token raster uses a fixed 5-by-7 stroke-glyph table,
integer scaling, packed one-bit rows, deterministic PDF objects, and a deterministic PNG encoder.
`tests/fixtures/synthetic/v1/index.json` binds their exact recipes, assertions, byte sizes, SHA-256
digests, and algorithmic provenance (`source_material: none`, `contains_personal_data: false`,
review decision PWS-D035):

| Fixture ID | Purpose | SHA-256 |
|---|---|---|
| `png-ocr-raster-text` | One 720×240 one-bit PNG with only the pixel token `SCAN PAGE 206` | `f2976a85d04b992bd690d8a2b70faf8444197d87ce58a7a5687a778c97f3fd82` |
| `pdf-ocr-raster-text` | One-page image-only PDF embedding those exact packed pixels and no text-showing operator | `3057e335b09d8721538be4a64306e8da552af4cbe9bf72b032cb80c188642562` |
| `pdf-ocr-readable-mixed` | Two-page PDF: born-digital token `DIGITAL PAGE 206`, then the same raster-only token page | `27cd473b273d4b7fe6ea8183e39a5b4523f66021b5b3208fad5499084fc02f3a` |

Generator tests prove the PNG bit depth/color type, the absence of accidental raw raster-token text
in the image-only artifacts, the intended PDF text/image operators and page ordering, and exact
identity between decompressed PNG pixels and both PDF image streams. The validator regenerates the
complete closed inventory in a temporary tree and compares bytes plus canonical index metadata,
so a recipe, provenance, or hash drift fails review instead of silently refreshing evidence.

These fixtures establish deterministic input semantics. Completion evidence uses the locked real
OCR/PDF closure for born-digital preservation, raster-PDF and PNG OCR text-presence success,
pikepdf/qpdf reopen, PDFium render and text-presence checks, and destination round-trip. The live
claim is only that non-whitespace text is extractable; it does not claim that OCR reproduced either
pixel token accurately. Focused fault tests prove exact page-local OCR failure, signed-marker
fallback, privacy-canary, low-disk, cancellation, and cleanup behavior. A skipped required engine
or viewer is not a passing PWS-0206 or PWS-0209 claim.
