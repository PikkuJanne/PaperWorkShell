# Milestone 3 visual-overlay gate - PWS-0308

- Review status: Complete
- Gate conclusion: PASS
- Review date: 2026-08-27
- Gate evidence revision: `3ba5e978f20c00dd860f6baa6f2e798d0e8725ea`
- Full product-lane revision: `da349eb57c117956aec940c6e8a2f54e668eaba2`
- Review branch: `codex/pws-0308-m3-overlay-gate`
- Frozen decisions: PWS-D018, PWS-D020, PWS-D021, PWS-D029, PWS-D030

## Scope and decision rule

This gate reviews the Milestone 3 path from normalized overlay intent through deterministic vector
rendering, keyboard-accessible editing, signature-image placement, restricted-worker flattening,
and observation in the named Windows reader matrix. It passes only when PWS-0301 through PWS-0307
are done, the registered quality lanes and final clean validators pass, the approved overlay
goldens remain valid, the composite editor/reader demonstration passes, and no unresolved M3
defect remains. The accepted W1-W4 evidence is final and is not repeated.

The manual evidence is composite. The production overlay editor's keyboard/NVDA run, automated
production persistence/export integration, and reader review of the production flattened artifact
are separately bound below. They are not presented as one human UI-to-export session.

Authoritative sources are [ROADMAP.md](../../ROADMAP.md), [TASKS.yaml](../../TASKS.yaml),
[DECISIONS.md](../../DECISIONS.md), the
[coordinate model](../architecture/page-coordinate-model.md), the
[overlay rendering contract](../architecture/overlay-rendering.md), the
[flattened export contract](../architecture/flattened-overlay-export.md), the
[overlay editor NVDA record](../accessibility/pws-0306-overlay-editor-nvda-smoke.md), and the
[reader matrix](../compatibility/pws-0307-overlay-reader-matrix.md).

## Reviewed environment

| Item | Observation | Gate interpretation |
|---|---|---|
| Operating system | Windows 11 Professional, build 26200, AMD64 | Supported Windows-first review host |
| Python and Qt UI | CPython 3.14.6; PySide6-Essentials/Qt 6.11.1 | Pinned production development runtime |
| Screen reader | NVDA 2026.1.1 | Agent-operated speech-trace evidence, not human certification |
| PDF engines | qpdf 12.3.2; pikepdf 10.10.0; pypdfium2 5.12.1/PDFium 152.0.7947.0; Poppler independent render checks | Production worker plus independent reopen/render checks |
| W1 | Adobe Acrobat Reader/free-reduced mode 2026.001.21789 | Human-operated qualifying reader observation |
| W2 | Microsoft Edge built-in PDF viewer 151.0.4129.101 | Agent-operated qualifying reader observation |
| W3 | Google Chrome built-in PDF viewer 151.0.7922.174 | Human-operated qualifying reader observation |
| W4 | Mozilla Firefox built-in PDF viewer 154.0.1 | Human-operated qualifying reader observation |

## Prerequisite review

| Task | Status | Evidence revision | Capability reviewed |
|---|---|---|---|
| PWS-0301 | Done | `9063fffe73f86740f70c2be320ddceca7e905e38` | Coordinate spaces and transformations |
| PWS-0302 | Done | `e8bfdc03af35049a944499161f99dee792b2312a` | Eight overlay kinds and history-safe typed intent |
| PWS-0303 | Done | `7eece7e7df1e7b239b41e5aa981192f740e7a730` | Vector renderer, authenticated font, and approved goldens |
| PWS-0304 | Done | `3d645374dd4f5c979300ce7db3edb81c3e1670d2` | Core marking tools and keyboard history |
| PWS-0305 | Done | `b86047d199689ace1ba790e56e6187d352600e22` | Signature-image intake, storage, and placement |
| PWS-0306 | Done | `56e187710f0131f11fb4227efd1a75e1be6d3f1e` | Accessible overlay editor |
| PWS-0307 | Done | producer `e088bb7dd10088f3eb454763e3fd4bd203706eb4`; closure `da349eb57c117956aec940c6e8a2f54e668eaba2` | Flattened export and complete reader matrix |

## Gate criteria

| Criterion | Result | Evidence |
|---|---|---|
| Every M3 prerequisite is done | Pass | PWS-0301 through PWS-0307 are `done`; no waiver is used |
| Coordinate and placement tests cover required geometries | Pass | Fixed-seed properties cover MediaBox/CropBox, non-zero origins, intrinsic and manifest 0/90/180/270-degree rotations, mixed page sizes, viewport/display scaling, and `/UserUnit`; the five-page production corpus exercises the required visible combinations |
| Unicode/font behavior is documented and verified | Pass | ADR 0009 binds authenticated Noto Sans 2.007, exact dependencies, fail-closed glyph handling, Type0/CIDFontType2 plus `/FontFile2`, `/ToUnicode`, and `/ActualText`; approved goldens cover representative Latin Extended, Greek, Cyrillic, and Devanagari strings |
| Overlay workflow is usable without a mouse | Pass | Automated keyboard/accessibility tests cover all eight released overlay kinds; the recorded production dialog exercised selection, coarse/fine movement and resize, snap, guides, pan, fit, zoom, focus traversal, Apply, and Escape without a pointer |
| Signature images are never represented as cryptographic signatures | Pass | Domain metadata fixes `visual_mark_not_digital_signature`; the UI says “Place signature image” and requires acknowledgment that the image does not digitally sign the PDF; export reporting preserves that distinction |
| Flattened outputs pass reopen/render and reader checks | Pass | Strict reopen, qpdf, exact Form placement, embedded-font, every-page PDFium, and independent Poppler checks passed; W1-W4 passed navigation, four zoom levels, 3/3 searches, non-interactivity, and close/reopen against one immutable artifact |
| M3 manual demonstration is recorded | Pass | Composite evidence combines the production overlay editor keyboard/NVDA run, automated production persistence/export integration, and reader review of the production flattened artifact |

## Composite demonstration

### Overlay editor keyboard and NVDA record

The production dialog exposed all eight released overlay kinds and their accessible names, state,
instructions, live geometry/status announcements, focus order, and keyboard operations. The run
used `QT_SCALE_FACTOR=1.6` and measured effective DPR 2.0; it does not claim that Windows display
settings were changed to 200 percent. NVDA was agent-operated. Apply used the probe's in-memory
callback, so it proves the dialog interaction and announcement rather than persistence. A fresh
run used Escape to exercise actual cancellation. Production persistence and export are proved by
the automated integration paths.

### Rotated, cropped, mixed-page flattened scenario

One immutable five-page PDF contains:

1. Letter page: text, date, and initials;
2. cropped small page with added 90-degree rotation: checkmark and cross;
3. cropped page with intrinsic 90-degree rotation: ellipse;
4. differently sized cropped page with added 270-degree rotation: signature-image visual mark; and
5. cropped `/UserUnit 2` page with added 180-degree rotation: highlight.

The PDF is 9,485 bytes with SHA-256
`05faf2055ccb07d85eca7afdb5f446c2b3aeba895d07c2f5fab4d769059fda3e`. Its evidence JSON has
SHA-256 `23c3ea41b4f58739c43a2ef9b805fc6b957a713e10749989b51bad5b1c6c3c21`, and the semantic
fingerprint is `162ab8bdbbefd3b91ecea8b35ae9836b63e21548dd56384622a472cebc0c2e07`.

W1, W2, W3, and W4 all derived PASS against that same PDF digest. Each qualifying run traversed
pages 1 through 5 and back, checked Fit Page/100/200/400 percent, found text/date/initials 3/3,
observed no overlay interactivity, and closed/reopened the local artifact. This proves the named
versions and protocol only, not universal reader compatibility.

The maintainer accepted the completed matrix and directed that it not be repeated. Small imagery
and a translucent comparison rectangle that was poorly centered for convenient review made the
test less efficient and less human-friendly than required for future work. Future human tests must
minimize setup, transcription, and repetition and use comfortably large, clearly aligned visual
targets unless size or offset is itself under test. This is test-ergonomics feedback, not a product
placement defect, and it does not invalidate or alter the accepted artifact.

## Coordinate, font, and flattening evidence

- The normalized coordinate model converts through CropBox/MediaBox, intrinsic rotation, manifest
  rotation, viewport scaling, and `/UserUnit` without storing viewer pixels in domain intent.
- Approved overlay-renderer goldens bind canonical intent, deterministic object/resource ordering,
  vector geometry, representative left-to-right Unicode shaping, and metadata-free signature
  placement.
- Supported shaped text uses an embedded Noto Sans Regular 2.007 subset with `/ActualText` and
  `/ToUnicode`. Arabic, Hebrew, CJK, emoji, controls, unsupported directionality, and missing glyphs
  fail closed; representative coverage is not a universal Unicode claim.
- Flattening appends ordinary page content through the restricted worker, reopens and verifies the
  derivative, renders every page independently, and commits only after destination verification.
  “Flattened” describes PaperWorkShell overlay appearances; it is not a claim that arbitrary source
  PDF interactivity was universally sanitized.

## Failure, privacy, cleanup, and resource evidence

| Path | Result |
|---|---|
| Invalid/unsupported overlay intent | Stable validation errors; no raw library exception or case mutation |
| Unsupported font/glyph/direction | Fail closed before publication; no silent substitution |
| Signature image intake | Bounded pixel/byte checks, metadata removal, opaque internal storage, and visual-mark-only terminology |
| Worker cancellation/timeout/protocol failure | Bounded cancellation and process-tree termination with deterministic private-temp cleanup |
| Export verification failure | No committed destination/history publication; originals remain unchanged |
| Routine privacy | Models, progress, errors, evidence, and logs exclude document text, OCR text, names, paths, filenames, metadata values, and signature pixels |
| Resource policy | Central policy bounds input, decoded pixels, temporary bytes, process memory, concurrency, and operation lifetime; constrained-profile tests pass |
| Gate-discovered history false positive | Current unit source constructs the synthetic PDF signature from octets; one exact reviewed historical source-recipe SHA-256 is admitted, every neighboring digest still fails closed, and no history rewrite or broad exclusion is used |

## Automated evidence and provenance

The full product lanes ran on `da349eb57c117956aec940c6e8a2f54e668eaba2`. The only subsequent
changes in `3ba5e978f20c00dd860f6baa6f2e798d0e8725ea` are the exact historical recipe-source digest
exception, its positive/neighbor/current-source regressions, and the adjacent fixture policy/ADR
record. No product, worker, renderer, export, UI, schema, dependency, or release-interface code
changed between those revisions.

| Command/evidence | Result |
|---|---|
| `tools/dev.py format-check`, `lint`, `check-import-boundaries`, `typecheck` | Pass; 516 files and 13 layers across 180 source files valid |
| `tools/dev.py test-unit` | Pass; 4,142 tests on `da349eb`; includes deterministic coordinate/property and renderer tests |
| `tools/dev.py test-integration` | Pass; 409 tests with 9 disclosed Windows-link/Tesseract-private-stage skips on `da349eb`; all M3 production persistence, flattening, golden, signature, PDFium, containment, cancellation, and cleanup paths passed |
| `tools/dev.py test-security` | Pass; 500 tests with 6 disclosed Windows link-capability skips on exact gate evidence revision `3ba5e97` |
| `tools/dev.py test-accessibility` | Pass; 50 tests on `da349eb` |
| Schema, dependency, task-graph, docs, portfolio, benchmark, renderer, and PWS-0208 workload validators | Pass |
| Clean `tools/dev.py validate-fixtures` on `3ba5e97` | Pass; 46 fixtures, 15 analytical renderer references, 6 image-transform and 3 overlay references, 2,341 historical blobs, 751 index blobs, and 779 working files |
| Clean `tools/dev.py scan-secrets` on `3ba5e97` | Pass; 2,341 historical blobs, 256 historical commit/tag objects, 2,374 historical path/blob pairs, 751 index blobs, 779 tracked working files, and zero nonignored untracked files |
| Clean `tools/dev.py quality-self-test` on `3ba5e97` | Pass; all 5 deliberate violations detected without retaining test data |
| PWS-0307 W1-W4 matrix | Pass; one immutable production artifact in all four named Windows readers |

The nine integration skips are capability disclosures rather than M3 omissions: unavailable
Windows link privilege and optional private Tesseract stages. Tesseract is now installed locally,
but the M3 overlay paths do not depend on OCR and all M3 integration cases ran.

## Known limitations and claims not made

- The NVDA smoke was agent-operated, not a human screen-reader certification.
- Effective DPR 2.0 is not proof of a Windows 200-percent display configuration.
- The manual editor page was unrotated; rotation, crop, mixed-size, and `/UserUnit` proof comes from
  automated geometry tests and the production reader corpus.
- The three successful searches prove only the named limited extraction checks, not tagged-PDF
  reading order or PDF/UA.
- Font support is the documented representative left-to-right subset, not all Unicode.
- Compatibility is limited to the four named Windows viewers and exact recorded versions.
- No portal acceptance, legal validity, cryptographic signing, permanent redaction, PDF/A, PDF/UA,
  secure-erasure, release-package, or physical-hardware claim is made.

## Portfolio boundary and release interface

Milestone 3 remains independently useful and fully operable as standalone PaperWorkShell. Cases,
originals, overlays, signature-image assets, derivatives, and manifests remain locally owned by
PaperWorkShell/the user. No sibling repository, shared runtime, service, schema, database, adapter,
release synchronization, or app-to-app interface was introduced. The human-readable release
interface remains unchanged, and no ThirdLife future note is warranted.

## Gate conclusion

**PASS.** Every prerequisite is done. Normalized overlay intent renders deterministically across
the required geometries, representative supported text embeds and extracts through the documented
font path, every released overlay kind is operable without a mouse, signature images remain
explicit visual marks, flattened derivatives reopen/render/verify, and the complete W1-W4 reader
matrix passed one immutable artifact. The automated, composite manual, privacy, failure, cleanup,
and exact-history evidence satisfies PWS-0308 without broadening product claims.
