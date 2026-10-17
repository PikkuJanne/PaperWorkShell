# Private OCR regeneration of reconstructed pages

- Task: PWS-0604
- Decisions: PWS-D009, PWS-D012, PWS-D013, PWS-D016, PWS-D022, PWS-D027, PWS-D029
- Status: Internal candidate stage; permanent-removal export remains disabled
- Prerequisite: [sanitized raster reconstruction](redaction-reconstruction.md)
- Security contract: [redaction threat model](../security/redaction-threat-model.md)

## Exact input and exclusion profile

The `redacted-page-ocr-v1` profile accepts one immutable PDF source page, separately
authored removal marks, crop, quarter-turn and a closed OCR language-set identifier.
It first runs the fixed 300 dpi reconstruction. Only the authenticated fresh,
image-only PDF reaches OCRmyPDF. Its source text, hidden OCR, source image objects,
fonts and earlier revisions are absent. Every marked source pixel has already been
replaced by opaque black, including outward rounding and the reconstruction's
one-pixel guard. This opaque pixel mask is the recognition-input exclusion strategy.
There is no source-text, cached OCR or original-page fallback.

The adapter independently validates the reconstruction's fresh page structure,
decoded raster identity, exact physical geometry and render before recognition.
It then uses the admitted OCRmyPDF/Tesseract stack, one page and one recognition job
at a time, with the existing fixed 300 dpi engine settings. Rotation inference,
deskew, cleanup, optimization and external plugins remain disabled. Supported
language selections are English, German, and the canonical English/German set;
each selected native language file must pass the existing runtime admission.
The distinct profile binds this prerequisite and exclusion policy even though it
reuses the admitted OCR engine's ordinary fixed options internally.

OCRmyPDF's incremental graft is serialized into a fresh private PDF before it is
eligible as a candidate. Its preceding revision contains only the already masked
input; no original source revision is admitted. This narrow OCR normalization
does not replace the complete derivative sanitizer. After OCR, the page must
retain the same decoded RGB image, physical geometry and rendered pixels.
Text bounds are checked against the exact mask after crop and
quarter-turn; a recognized word or character overlapping an excluded region fails
closed. Geometry, extraction, rendering, limits or engine errors return no candidate.
This check rejects ambiguous results; it does not silently drop text or weaken
the mask. Searchability remains subject to ordinary OCR limits, particularly for
degraded, sideways or incomplete text. Synthetic positive controls demonstrate
recognition before canary absence is accepted. OCR completion does not establish
that every retained word is correct or recognized.

The stage also inherits reconstruction's exact-render rejection behavior. Some
fractional physical crop sizes can fail before OCR; recognition never bypasses
that check or silently changes geometry to make the request fit. Native evidence
proves only the exercised geometry and fidelity cases.

## Authority and evidence

`pdf.redaction_ocr_private` version 1 has a separate launch scope and a closed
request/report record under the existing RPC envelope. The request fingerprint
binds the complete reconstruction request, selected language set, profile and
version. The report binds the reconstruction evidence, exact final output size
and digest, admitted engine versions and language set. Its only status is
`awaiting_verification`, and it always describes an **unsigned derivative**.

The broker receives a retained read-only source lease and transfers only reduced
source/request/output/result handles. It authenticates source identity, request,
operation, process generation, report and output bytes. One processor permits one
outstanding bounded candidate, which must close before another request. Closing
the composition revokes processing and releases the candidate and worker.
Normal workers do not advertise this private operation. This stage has no export
destination, manifest-mutation, mark-promotion or export-history authority.

## Resources and failure recovery

Limits derive from the central versioned ResourcePolicy and the admitted operation
limits. Reconstruction and OCR run sequentially under the same heavy-operation
gate. The OCR scope admits only the worker plus one Tesseract child. Its minimal
environment, admitted executable/language closure, AppContainer network denial,
memory, CPU, deadline and process limits remain required. The operation accounts
for source staging, parent-owned request/output sections, reconstruction, OCR
scratch and final output. No whole-packet raster, unbounded queue or cache is added.

Cancellation, timeout, termination, low storage, disk-full, unknown language,
engine failure, changed source, forged report and output mismatch discard the
candidate and clean private scratch. Missing installed language data has the
stable `PDF_EXPORT_OCR_LANGUAGE_UNAVAILABLE` error and directs the user to review
OCR language settings. No image-only success fallback exists. A retry starts from
the immutable source; failure cannot verify intent or replace a previous export.
Routine progress and errors contain closed codes and numeric counts, never text,
filenames, paths, native exception values or image samples.

## Remaining gates and product boundary

PWS-0605 owns complete derivative sanitization, PWS-0606 independent multi-method
verification, PWS-0607 final publication and reports, and PWS-0608 independent
release approval. This stage does not replace any of those gates. Ordinary and
alternate export blockers remain in force. Input-mask tests and search controls
support only the exercised profile and samples; no universal OCR, secure-erasure,
PDF/UA, cryptographic-signature or portal-acceptance claim follows.

The capability serves PaperWorkShell's own standalone document workflow. Its
private data, migrations and publication authority remain PaperWorkShell-owned.
No sibling repository, service, adapter or shared schema is required, and draft
release-interface facts remain unchanged.
