# Scan-quality findings evaluation

- Status: PWS-0207 implementation evidence
- Task: PWS-0207
- Decisions: PWS-D026, PWS-D032, PWS-D035, PWS-D037, PWS-D041
- Corpus: `pws0207-quality-findings-corpus-v1`
- Baseline target: `benchmarks/pws0207-quality-findings-v1.json` after the implementation revision
  is committed

PaperWorkShell reports scan-quality results as deterministic review signals. A finding means only
that a page crossed the documented heuristic threshold. It does not establish that a page is
unusable, that OCR text is correct, or that a checklist requirement is complete. The organizer
presents every finding as “Needs review,” with its closed **Likely** or **Suspected** title where
applicable, and the result remains subject to the user's decision.

## Closed finding set

Version 1 evaluates seven user-facing review signals:

1. likely low resolution;
2. likely blur;
3. suspected clipping;
4. likely blank page;
5. suspected sideways orientation;
6. suspected duplicate page; and
7. OCR needs review, with closed `ocr_failed` and `ocr_low_confidence` findings.

The baseline keeps those two OCR finding codes separate, so it has eight metric rows. A regression
in failure/no-text handling cannot be hidden by an improvement in confidence handling, or vice
versa.

The artifact uses short metric-kind tokens rather than treating every row name as a domain finding
code:

| Metric kind | Exact `QualityFindingCode` value |
|---|---|
| `low_resolution` | `likely_low_resolution` |
| `blur` | `likely_blur` |
| `clipping` | `suspected_clipping` |
| `blank` | `likely_blank` |
| `orientation` | `suspected_sideways_orientation` |
| `duplicate` | `suspected_duplicate` |
| `ocr_failed` | `ocr_failed` |
| `ocr_low_confidence` | `ocr_low_confidence` |

The first six consume an already validated, bounded opaque preview raster. Complex PDF/image decode
and rendering remain in the existing restricted worker; the pure analyzer never opens a source or
invokes a document engine. OCR review accepts an optional, closed, page-correlated fact containing
only terminal state, recognized-word count, and optional integer mean confidence. The evaluator
never accepts or retains OCR text, word boxes, filenames, paths, metadata values, tool output, or
native exceptions.

PWS-0207 implements a separate `ocr.review` version-1 producer for the exact current 256-pixel
sample; it does not change `ocr.process` version 1 or infer confidence from searchable-output
success. A one-shot restricted worker makes one fixed local Tesseract recognition invocation,
parses only numeric word-confidence fields from bounded stdout TSV, and returns a correlated
content-free aggregate. Text and geometry are discarded in the worker. The corpus below evaluates
the aggregate rule only; OCR producer/protocol/security behavior and recognition reachability are
proved by focused tests rather than these synthetic aggregate observations.

## Version-1 profile

All calculations are integer-only. The profile configuration SHA-256 binds every threshold below;
the profile version identifies the algorithm family, while the baseline separately binds the exact
implementation revision. Changing a threshold or algorithm requires reviewed profile/evaluation
evidence and a newly recorded baseline; changing a number only to make the corpus pass is
prohibited.

| Signal | Version-1 rule |
|---|---|
| Analysis raster | The current service requests an aspect-preserving opaque result bounded to 256 by 256 pixels. Image fitting never upscales; PDF pages are rendered into the requested bound, for which source-pixel upscaling is not a defined claim. The domain rejects more than 1,048,576 pixels or a 1,024-pixel edge and the service retains at most one sample. |
| Luminance | `Y = (77R + 150G + 29B + 128) >> 8`; a pixel is foreground when `Y <= 224`. |
| Low resolution | For image sources with authenticated source dimensions, source short edge is below 1,000 pixels **or** source area is below 1,000,000 pixels. The rule is not applied to PDF pages without source-raster dimensions. |
| Likely blank | Remove a 2-percent margin per edge, rounded up, capped at 16 pixels, and finally clamped so at least one pixel remains on a tiny axis. In that retained central region, foreground is at most 2,500 parts per million and full-page mean darkness is at most 12,000 parts per million of full scale. |
| Likely blur | On a nonblank page with at least 10,000 foreground parts per million, mean squared four-neighbour Laplacian energy is below 500. |
| Suspected clipping | Each edge band is 2% of its relevant raster dimension, rounded up, clamped to 2–16 pixels, and then capped to the actual axis length. A side must have at least 30,000 foreground parts per million on its outer line and 50,000 foreground parts per million across its band. |
| Suspected sideways orientation | On a nonblank page with at least 10,000 foreground parts per million, variance of normalized column foreground densities is at least twice variance of normalized row densities. |
| Suspected duplicate | A deterministic 64-bit difference hash over a 9-by-8 box-averaged luminance plane; aspect-ratio delta at most 10,000 parts per million, foreground delta at most 20,000 parts per million, and Hamming distance at most 4. |
| OCR needs review | `failed` for a correlated failed attempt, or a `no_text` result on a nonblank page; `low_confidence` only when a supplied fact has at least three words and integer mean confidence below 70,000 milli-confidence units. |

Duplicate review keeps one small private fingerprint per already analyzed page, compares the
current page with prior pages in manifest order, checks cancellation at least every 32 comparisons,
and stops at the earliest prior suspected match. The request model has a hard 4,096-page cap, while
both shipped ResourcePolicy profiles currently limit imported cases to 1,000 pages. This is bounded
but quadratic in the worst case; the total operation deadline remains an authoritative stop.
Fingerprints and page-image digests are document-derived values and must not appear in routine logs,
representations, errors, accessibility properties, or baseline evidence.

## Recipe-only labeled corpus

The corpus is code, not a directory of committed documents. Fixed standard-library recipes create
small opaque RGBA observations in memory. Each case carries an opaque case token, source dimensions,
an exact truth/applicability state for every unary finding, and an optional content-free OCR
observation. Duplicate truth is recorded separately as labeled case pairs. No generated raster is
written to the repository, diagnostics, a test artifact, or the baseline file.

The recipe runner calls the pure analyzer directly. It does not exercise preview-worker transport,
PDF rendering or aspect-ratio fitting, image resampling, or OCR-engine recognition; those adapter
and integration properties require their focused tests rather than an inference from these metrics.

The matrix includes clear positives and negatives plus deliberately difficult confounders. Exact
strict/inclusive numeric boundaries are tested separately in the pure-domain unit suite; the
labeled corpus does not claim a threshold-boundary recipe for every configured field. Notable
corpus cases are:

- pure white, faint scanner-noise, sparse-mark, and light-form blank-page cases;
- sharp, blurred, motion-softened, and sharp-low-contrast line pages;
- left- and bottom-clipped lines plus an ordinary margin and a full-bleed photo-like confounder;
- 0, 90, 180, and 270 degree line layouts;
- exact, brightness-shifted, similar-but-distinct, and unrelated duplicate pairs; and
- failed, zero-word nonblank, low-confidence, high-confidence, and zero-word blank OCR observations.

Every recipe is deterministic and its construction/evaluation uses no source document, download,
randomness, clock, locale, username, machine path, font, OCR text, or subprocess. Baseline
record/validation separately invokes only fixed read-only local Git queries for revision
provenance. This preserves the public-synthetic-only rule without changing the already
evidence-bound `paperworkshell-public-synthetic-v1` corpus.

## Baseline metrics

The revision-bound baseline is canonical ASCII JSON. It binds the corpus version and SHA-256,
finding profile version and SHA-256, the actual constrained ResourcePolicy version/profile, and the
verified implementation revision. The recorder and validator require that the 40-hex object exists
as a Git commit, that every bound analyzer, OCR producer, organizer/UI correlation, worker
protocol/composition, and evaluator surface is present exactly once as an ordinary blob in that
commit, that every stage-zero index mode/object matches, and that bounded ordinary working files
have the same raw filter-free Git blob IDs. The authoritative exact list is
`QUALITY_IMPLEMENTATION_SURFACES` in the evaluator and currently comprises:

- `src/paperworkshell/__main__.py`;
- `src/paperworkshell/app/organizer.py`,
  `src/paperworkshell/app/quality_analysis.py`, and
  `src/paperworkshell/app/quality_ocr_review.py`;
- `src/paperworkshell/domain/quality_analysis.py` and
  `src/paperworkshell/domain/quality_ocr_review.py`, plus the directly evaluated
  `src/paperworkshell/domain/resource_policy.py`;
- `src/paperworkshell/ocr/tesseract_runner.py`;
- `src/paperworkshell/ui/backend.py`, `src/paperworkshell/ui/models.py`, and
  `src/paperworkshell/ui/window.py`;
- `src/paperworkshell/worker/_windows_broker.py`,
  `src/paperworkshell/worker/_windows_runtime.py`,
  `src/paperworkshell/worker/client/_windows.py`,
  `src/paperworkshell/worker/desktop_composition.py`,
  `src/paperworkshell/worker/host.py`,
  `src/paperworkshell/worker/ocr_composition.py`,
  `src/paperworkshell/worker/protocol/codec.py`,
  `src/paperworkshell/worker/protocol/models.py`, and
  `src/paperworkshell/worker/protocol/operation_scope.py`, and
  `src/paperworkshell/worker/protocol/state.py`;
- `src/paperworkshell/worker/quality_ocr_review_broker.py`,
  `src/paperworkshell/worker/quality_ocr_review_buffers.py`,
  `src/paperworkshell/worker/quality_ocr_review_composition.py`, and
  `src/paperworkshell/worker/quality_ocr_review_operation.py`;
- `src/paperworkshell/worker/searchable_pdf_export_composition.py`; and
- `tools/pws0207_quality_evaluation.py`.

This is the closed set of task-owned production/evaluator surfaces plus the directly evaluated
resource-policy authority. It is deliberately not a transitive dependency closure: reused worker
client, handle-transfer, private-temp, dispatch, process-policy, OCR-language, and OCR-service
modules remain governed by their owning tasks and the repository's full registered gates. The
final task revision binds the complete implementation commit; this baseline does not authenticate
an unlisted surface.

This intentionally permits the baseline, `TASKS.yaml`, bundle metadata, and closure evidence to be
added in a later commit without misrepresenting the implementation that was evaluated. Git queries
use fixed argument arrays, no shell, a ten-second timeout, bounded or discarded query output, no
prompt or lazy object fetch, disabled repository file-system monitors/untracked caches, no
replacement objects, and no diff, text conversion, or clean/smudge filter. Each source is capped at
4 MiB and the complete evaluated set at 16 MiB before raw hashing. A caller-supplied 40-hex string
alone is not accepted as provenance.

The baseline records the following values independently for every metric kind:

- applicable samples;
- true positives, false positives, true negatives, and false negatives;
- precision and recall in parts per million;
- false-positive and false-negative rates in parts per million.

Rates use integer half-up division. A rate is `null` when its denominator is zero. Counts are the
primary evidence; rates cannot hide a small or missing class. The validator rejects duplicate JSON
keys, floats, noncanonical encoding, unknown fields or kinds, invalid totals/rates, an unknown
version, a profile/corpus mismatch, any free-form content field, a missing/non-commit revision, a
missing/non-blob evaluated surface, changed evaluated surfaces, or relevant uncommitted/untracked
state.

An exact baseline is a regression oracle, not an accuracy guarantee. A deliberate heuristic or
corpus change must explain the changed false-positive/false-negative counts and record a newly
reviewed baseline rather than refreshing the file merely to pass tests.

The frozen recipe/profile evaluation recorded against implementation revision
`a55c5b7a3866fac5c54640e5de06eaee9fe86339` is:

- corpus SHA-256:
  `36fad4f6a94e06971ea9e8e3226df18f92050e8f6f14ba1a1dd5b5b4056bfd8e`;
- profile `scan-quality-v1`, version 1, SHA-256:
  `ac8a174ad72892f7a519090d0497d6382c4e67d34fb1f5b6638fb82f420901b8`;
- constrained ResourcePolicy version 1.

The PWS-0201 review reran the production evaluator and its exact-revision recorder before
updating this baseline. Nine of the 27 bound surfaces changed: desktop entry point, organizer,
backend bridge, window, worker composition, host registration, and protocol codec, models, and
state. The other 18 surfaces, including the quality algorithms, corpus/evaluator, profile,
resource policy, and OCR-review machinery, remained byte-identical. The separately recorded and
validated candidate differs from the previous canonical baseline only in `revision`; all eight
metric rows, corpus/profile digests, and resource-policy fields are unchanged. The original
baseline remains in Git history, and the exact-revision validation rules remain unchanged.

| Metric kind | Applicable | TP | FP | TN | FN | Precision ppm | Recall ppm | FPR ppm | FNR ppm |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `low_resolution` | 18 | 2 | 0 | 16 | 0 | 1,000,000 | 1,000,000 | 0 | 0 |
| `blur` | 18 | 2 | 1 | 15 | 0 | 666,667 | 1,000,000 | 62,500 | 0 |
| `clipping` | 18 | 2 | 1 | 15 | 0 | 666,667 | 1,000,000 | 62,500 | 0 |
| `blank` | 19 | 3 | 0 | 16 | 0 | 1,000,000 | 1,000,000 | 0 | 0 |
| `orientation` | 18 | 2 | 0 | 15 | 1 | 1,000,000 | 666,667 | 0 | 333,333 |
| `duplicate` | 4 | 2 | 1 | 1 | 0 | 666,667 | 1,000,000 | 500,000 | 0 |
| `ocr_failed` | 5 | 2 | 0 | 3 | 0 | 1,000,000 | 1,000,000 | 0 | 0 |
| `ocr_low_confidence` | 5 | 1 | 0 | 4 | 0 | 1,000,000 | 1,000,000 | 0 | 0 |

These small synthetic classes are deliberately not accuracy claims. In particular, one negative
duplicate pair produces a 500,000 ppm false-positive rate because that class contains only two
negative pairs.

The non-mutating verification command recomputes current metrics and validates the checked-in
baseline:

```text
python tools/pws0207_quality_evaluation.py evaluate
python tools/pws0207_quality_evaluation.py validate benchmarks/pws0207-quality-findings-v1.json --revision <recorded-40-lowercase-hex>
```

The artifact was recorded explicitly after the implementation revision existed and every evaluated
surface matched it. For a future deliberately reviewed replacement, the recorder fsyncs a
same-directory temporary file, publishes it through an exclusive no-replace hard link, removes the
temporary name, and refuses to overwrite an existing baseline. A failed write/publication leaves no
destination and can be retried:

```text
python tools/pws0207_quality_evaluation.py record --revision <40-lowercase-hex> --output benchmarks/pws0207-quality-findings-v1.json
```

## Known limitations

- Blur, clipping, blank, orientation, and duplicate evidence is calculated from the bounded
  at-most-256-pixel presentation, not from a full-resolution source or final export raster.
  Downsampling, interpolation, cleanup, contrast changes, and extreme aspect-ratio fitting can
  create or suppress edges, texture, projection structure, and perceptual similarity. The version-1
  labeled corpus uses square 256-by-256 observations; it does not measure portrait/landscape fit,
  tiny-axis behavior, renderer differences, or full-resolution legibility.
- Encoded pixel dimensions are only a proxy for effective scan DPI. A small readable receipt can
  be flagged, while a large upscaled raster or a small retained crop from a high-resolution source
  can evade the signal because version 1 does not derive effective cropped-source dimensions.
- Laplacian energy is sensitive to halftones, compression texture, handwriting, and low-contrast
  material. It does not measure human legibility. The current corpus's sparse-mark negative is a
  blur false positive.
- Edge contact cannot distinguish clipped text from full-bleed photographs, borders, stamps, or
  intentional content at a page edge. The corpus retains a full-bleed negative confounder and the
  baseline records any resulting false positive.
- Projection variance can identify line-like content rotated 90 or 270 degrees. It cannot reliably
  distinguish upright from 180-degree content without a separately admitted orientation model.
  The corpus labels 180 degrees as non-upright, so this limitation remains visible as a false
  negative rather than being removed from evaluation.
- A 64-bit perceptual fingerprint can collide and can miss duplicates after material crop,
  annotation, contrast, or layout changes. Every match is only suspected. The similar-but-distinct
  pair is a false positive under the current profile.
- Tesseract confidence is engine/profile/language dependent and is not a probability of correctness.
  Production OCR review uses the same bounded 256-pixel resampled presentation, fixed page
  segmentation mode 6, and the selected authenticated process-session language set; small text,
  unusual layout, resampling, or a different language can change or eliminate recognized words.
  The deterministic baseline evaluates the closed aggregate rule, not OCR recognition accuracy or
  worker integration. A `no_text` fact on a page independently found blank is suppressed rather
  than producing a second OCR-failed finding.
- Synthetic regression results do not represent real applicant documents, named hardware,
  PDF/UA conformance, portal acceptance, or general human-usability certification.

## Security and resource verification

Focused evidence must prove model/repr/log privacy canaries; exact threshold-minus-one and threshold
boundary behavior; canonical corpus reconstruction; correct confusion-matrix arithmetic; the
4,096-page request cap and stricter live case policy; cancellation before each page and on the 32nd
comparison in a greater-than-32 prior-page scan; earliest-prior duplicate selection; timeout and
preview failure without a partial snapshot; stale page/revision rejection; hostile OCR aggregate
and report rejection; and no checklist or manifest completion from findings alone.

The implemented OCR producer remains governed by the central `ResourcePolicy`, one heavy operation
at a time, network denial, exact volatile-section/result-pipe authority, one-page worker lifecycle,
fixed Tesseract arguments, shared bounded stdout/stderr capture, whole-tree termination before
report authentication, private-PPM cleanup, and fresh retry after capacity failure. Runtime or
language unavailability produces a failed review fact, not a claim that OCR inspected the page.
The recipe evaluator invokes neither this worker operation nor Tesseract and weakens none of those
controls.
