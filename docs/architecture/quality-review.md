# Advisory page-quality review architecture

- Status: PWS-0207 implementation contract
- Task: PWS-0207
- Decisions: PWS-D026, PWS-D032, PWS-D037, PWS-D041
- Organizer surface: [Page organizer UI architecture](page-organizer-ui.md)
- Preview boundary: [DF-PREVIEW](../security/data-flows.md#df-preview--render-bounded-thumbnailspreviews)
- Security flow: [DF-QUALITY-REVIEW](../security/data-flows.md#df-quality-review--derive-ephemeral-advisory-findings)
- Evaluation: [Scan-quality findings evaluation](../development/scan-quality-evaluation.md)

## Scope and claim boundary

The user starts **Check page quality** explicitly for one exact saved organizer revision. The
review covers the complete active page sequence and its current rotation/scan-transform intent.
It produces deterministic, explainable suggestions for likely low source resolution, likely blur,
suspected clipping, likely blank pages, suspected sideways orientation, suspected duplicates, and
optional OCR failure or low aggregate confidence.

These are advisory signals, not page facts. A result always uses **Likely**, **Suspected**, or
**Needs review** language. No-signal output means only that the listed version-1 checks did not
cross their configured thresholds; it does not prove legibility, correct orientation, OCR
accuracy, completeness, validity, or portal acceptance. A finding cannot apply an edit, remove a
page, satisfy a checklist item, or confirm a requirement.

`QualityReviewSnapshot` is disposable presentation state. It binds one internal case UUID,
manifest revision, exact ordered active-page UUID tuple, canonical finding tuple, profile ID, and
profile SHA-256. The snapshot itself is not written to the manifest, journal, history, cache,
diagnostics, export, or checklist. Any case/revision/page/intent change, pending autosave, case
switch, or close clears the visible snapshot and cancels matching work.

## Layer and data-lifetime contract

| Component | Responsibility | Boundary rule |
|---|---|---|
| `domain.quality_analysis` | Validate bounded RGBA8 samples and content-free OCR facts; compute integer evidence, typed findings, and private fingerprints | Pure standard-library logic; no Qt, document parser, renderer, OCR engine, storage, or filesystem dependency |
| `app.quality_analysis` | Bind one saved revision, obtain one current page sample at a time, enforce total deadline/cancellation, compare prior fingerprints, and return one exact snapshot | No manifest mutation, pixel persistence, filename/path, OCR text, native exception, or checklist authority |
| `domain.quality_ocr_review` and `app.quality_ocr_review` | Bind the exact sample, presentation, selected session language set, and terminal content-free OCR observation | Recognized text and geometry never enter an application model; unavailable local OCR becomes a failed review fact rather than a claim that recognition ran |
| `app.organizer` | Project the complete authoritative active-page order and current PDF/image intent; reject pending, stale, or changed state before returning a result | Revalidates case, revision, history identity, page tuple, and save state after analysis |
| Existing preview service and worker | Authenticate each source and produce one fixed-layout opaque preview through the existing bounded worker flow | Parsing/rendering remains outside the desktop process; reuse may populate only the existing bounded preview cache |
| `worker.quality_ocr_review_*` and `ocr.tesseract_runner` | Transfer the exact current sample through a read-only volatile section, run one page-scoped `ocr.review` v1 operation, inspect private bounded TSV, and return one correlated content-free report | One-shot restricted worker; fixed local Tesseract arguments; no source/case path, document object, OCR text result, persistent writer, or network authority |
| `ui.backend` and `ui.models` | Correlate operation/generation/state and expose only closed finding presentation | Never convert quality samples to UI images or retain fingerprints/pixels |

For a PDF page, the service requests one aspect-preserving preview bounded to 256 by 256 opaque
RGBA8 pixels with the current organizer rotation. For a JPEG or PNG page, it first requests one
1-by-1 metadata preview to obtain validated encoded source dimensions, releases that result, and
then requests one aspect-preserving 256-by-256 transformed preview using the complete current
intent. Intrinsic EXIF orientation is applied by the existing image pipeline; it is not itself
reported as an orientation defect.

Complex PDF/image parsing, decode, rendering, and transformation remain in the restricted preview
worker. The analyzer performs only pure integer calculations on the already validated opaque plane.
Page processing remains serial: it releases the current page sample and luminance plane before it
requests the next page and retains one small private fingerprint per earlier page only until the
review returns or fails. When OCR review is available, that same current sample also has bounded
transient copies in a broker-owned volatile section and the one-shot worker; this is still one-page
sequencing, not a claim that only one physical buffer exists across processes. The result contains
no pixels or fingerprint. Fingerprints and any pixel-derived or source digests are document-derived
values and are prohibited from routine logs, errors, representations, accessibility metadata, and
evaluation baselines. The non-content profile configuration SHA-256 is deliberately carried for
reproducibility.

The existing preview service remains governed by the selected versioned `ResourcePolicy`, worker
isolation, network denial, handle authentication, render limits, and one application-wide heavy
operation. Reuse of that flow may populate its existing bounded disposable preview cache; PWS-0207
adds no quality-specific cache or persistence surface. Its OCR extension uses a bounded pagefile
section, an at-most-256-by-256 opaque sample, a one-page operation, a 1 MiB shared stdout/stderr
budget, a 4 MiB temporary-storage limit, a 60-second worker-operation wall limit, and one worker plus
at most one Tesseract child concurrently. The worker writes only one private PPM input, captures TSV
from bounded stdout, discards separately bounded stderr, and attempts PPM removal before
termination; cleanup uncertainty is a terminal failure and cannot publish a fact.
PWS-0207 also adds a 256-pixel semantic analysis size, a maximum
1,024-pixel/1,048,576-pixel domain input guard, at most eight findings per page, the existing
4,096-page model cap, enforcement of the selected policy's stricter live page limit (currently
1,000), and one 3,600-second monotonic whole-review deadline. Cancellation is checked around every
preview, OCR, and analysis phase and at least every 32 prior-page duplicate comparisons. A
cancellation, timeout, malformed result, or state change exposes no partial snapshot and changes no
authoritative state.

## Exact version-1 profile

The current profile is `scan-quality-v1`, profile version `1`, with canonical configuration
SHA-256
`ac8a174ad72892f7a519090d0497d6382c4e67d34fb1f5b6638fb82f420901b8`.

The hash is computed from canonical ASCII JSON containing `profile_id`, `profile_version`, and
every threshold field below, with keys sorted and compact separators. It identifies the exact
configuration carried by a snapshot; it is not a document digest, authentication signature, or
hash of the analyzer implementation. Evaluation evidence therefore binds the profile hash and the
implementation revision separately. A reviewed threshold or semantic change requires updated
profile/evaluation evidence rather than silently refreshing a baseline.

All ratios use integer parts per million (ppm) with half-up rounding. Luminance is
`(77R + 150G + 29B + 128) >> 8`; pixels with luminance at most 224 count as foreground.

| Signal or guard | Exact version-1 rule |
|---|---|
| Source-pixel resolution | Image-only rule: flag when encoded short edge is `< 1,000` pixels, otherwise when encoded area is `< 1,000,000` pixels. PDF previews have no proven source-raster dimensions, so this signal is not applied to PDF pages. |
| Likely blank | Central foreground is `<= 2,500 ppm` and full-page mean darkness is `<= 12,000 ppm`. The central region removes a 2-percent margin per edge, rounded up and capped at 16 pixels, then clamps the bounds so at least one pixel remains on a tiny axis. |
| Likely blur | Nonblank page with foreground `>= 10,000 ppm`; mean squared four-neighbour Laplacian energy is `< 500`. This is a texture/edge heuristic, not a legibility measure. |
| Suspected clipping | Edge band is 2 percent of the relevant dimension, rounded up, clamped to 2–16 pixels, then capped to the actual axis length. Flag a side when its outer-line foreground is `>= 30,000 ppm` and band foreground is `>= 50,000 ppm`. |
| Suspected sideways orientation | Nonblank page with foreground `>= 10,000 ppm`; normalized column-projection variance divided by row-projection variance is `>= 2,000,000 ppm` (two times). |
| Suspected duplicate | 64-bit difference hash from a 9-by-8 box-averaged luminance grid; relative aspect delta `<= 10,000 ppm`, foreground delta `<= 20,000 ppm`, and Hamming distance `<= 4`. |
| OCR failure | Optional exact fact with status `failed`, or status `no_text` for a nonblank page. `no_text` on a likely blank page is suppressed rather than reported as a second failure signal. |
| OCR low confidence | Optional exact successful fact with at least 3 recognized words and integer mean confidence `< 70,000` milli-confidence units. |

A likely-blank result suppresses blur, clipping, and orientation checks for that page. Duplicate
comparison walks earlier pages in active manifest order, attaches at most one finding to the later
page, and stops at the earliest prior page that meets all three thresholds. The scan is bounded by
the page cap and deadline but remains quadratic in the worst case. A match is never authority to
remove either page.

The orientation projection can suggest line-like content rotated 90 or 270 degrees. It cannot
reliably distinguish upright content from a 180-degree rotation, infer reading direction, or
classify a photograph. PDF rotation metadata and image EXIF/current transform intent are already
reflected in the sampled presentation; the finding describes only that presentation.

Source pixel dimensions are a proxy rather than effective DPI. A small readable receipt may be
flagged and a large upscaled image may not be. PDF pages intentionally receive no low-resolution
finding because a 256-pixel rendered thumbnail cannot establish the resolution of an embedded
raster. The other visual signals describe only the bounded, resampled current presentation:
downsampling, interpolation, cleanup, contrast, and extreme aspect-ratio fitting can create or
suppress their evidence. Blur, clipping, blank, orientation, and duplicate heuristics have the
additional false-positive/false-negative limits recorded in the evaluation document.

## OCR fact boundary

The analyzer optionally accepts one fact per selected page containing only its internal UUID, a
closed `succeeded`/`no_text`/`failed` state, a bounded word count, and an optional integer mean
confidence. It accepts no recognized text, word boxes, page geometry, language strings, filenames,
paths, metadata, tool output, or native OCR exceptions.

PWS-0207 implements a separate additive `ocr.review` version-1 producer; it does not change
`ocr.process` version 1 or infer confidence from searchable-output success. If the selected
process-session language set is authenticated and available, the first explicit quality review
constructs and adopts the capability-scoped composition on the backend owner thread after checking
cancellation and the whole-review deadline. Backend startup performs no optional OCR admission.
The authenticated local inventory/admission work is bounded in scope but has no elapsed-time
guarantee, so the deadline is checked again immediately afterward rather than described as an
interruptible timed step. The composition is retained for the session, while every page starts and
terminates a fresh restricted worker before the broker reads its report.

Each one-shot worker repeats fixed Tesseract `--version` and `--list-langs` capability-admission
probes, then makes one recognition invocation for the exact current 256-pixel sample using the
closed selected language-set mapping, `--psm 6`, and TSV on stdout. Stdout and discarded stderr
share the exact 1 MiB live byte budget. The worker parses only bounded numeric word confidence and
tests the final field for non-whitespace presence; recognized bytes and geometry are discarded and
never cross the result pipe. `succeeded` requires at least one word and an integer mean confidence;
zero recognized words is only `no_text`. The canonical report binds operation, case, revision,
page, sample and presentation digests, language/profile tokens, status, count, confidence, and the
fixed Tesseract version. The protocol result itself is object-free.

An expected local engine/runtime absence, an unavailable selected language, or an engine-reported
recognition failure yields a correlated `failed` fact and therefore an OCR **Needs review** signal
without claiming inspection or confidence. Only that expected unavailable state is latched for the
rest of the application session so expensive admission is not repeated for every page or review.
Authority mismatch, isolation, launch, process, transport, protocol, resource-limit, cleanup, and
malformed-result failures abort the exact review and publish no snapshot. Restarting PaperWorkShell
creates a fresh runtime authority and admission attempt after the expected local-unavailable latch.
The worker may temporarily inspect private in-memory TSV and write the bounded private PPM, but no
TSV file, OCR text, page image, report, or finding is intentionally or authoritatively persisted by
the review flow.

## UI lifecycle and accessibility

The organizer always exposes a **Page quality review** group with a focusable textual status,
**Check page quality** (`Ctrl+Shift+Q`), a virtual single-selection findings list, and **Go to
selected page**. Activating a finding with Enter or the button selects, centers, and focuses the
referenced organizer page and announces its page number and “Needs review” title. Duplicate rows
name both page positions. Page cards expose textual `not checked`, `limited checks found no review
signals`, or `Needs review` state, so meaning never depends on color.

The check is a cancelable global long operation with visible numeric progress and accessible
queued, active, completed, cancelled, failed, and stale states. If an ordinary thumbnail request
is active, the UI cancels it and retains exactly one pending quality request for the same saved
revision; the quality request starts only after the preview drains. Escape or the visible Cancel
control can cancel the pending or active request. Import, scan review, package review/export,
mutation, autosave, case switch, reopen-required state, shutdown, and another quality operation
cannot overlap it. Ordinary preview scheduling resumes after the quality operation finishes.

The desktop disables **Check page quality** when the saved case has no active pages, so an empty
case cannot launch this UI workflow or be displayed as a completed clean review. The application
service still defines an exact empty-page request as an empty snapshot after its initial
cancellation/deadline check, with no preview, OCR, or progress callback. That total service model
does not override the UI prevention rule.

The UI accepts a snapshot only when operation ID, generation, case UUID, manifest revision, exact
ordered page tuple, and unchanged backend/UI state all match. Failure or cancellation clears the
finding list and says that no review results were applied; an incomplete operation is never shown
as a clean review. Saving a new process-session OCR language selection immediately clears any
accepted quality snapshot and findings and returns page quality state to **Not checked**. There is
deliberately no Dismiss, Resolve, Apply, or Mark complete action.

Keyboard, focus, accessible-name/description, announcement, virtual-list, page navigation, and
200-percent scaling coverage complement the pure-domain threshold, malformed-model, stale-state,
cancellation, resource, privacy, and recipe-only labeled-corpus tests. The corpus baseline records
false positives and false negatives rather than converting synthetic performance into an accuracy
or hardware claim.
