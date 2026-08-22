# ADR 0009: Use authenticated Noto Sans subsets for deterministic overlay pages

- Status: Accepted
- Proposed: 2026-08-22
- Accepted: 2026-08-22
- Task: PWS-0303
- Decisions implemented: PWS-D015, PWS-D018, PWS-D034 (preserved; no frozen-decision amendment)

## Context

The released overlay-v1 model deliberately treats `font_token` as inert saved intent. It does not
choose a font file, shape text, measure a line, embed resources, or flatten a page. PWS-D018 and
PWS-0303 require a concrete overlay-first renderer for text, dates, initials, checkmarks, crosses,
signature images, highlights, and ordinary shapes. That renderer must be deterministic, local,
bounded, Unicode-capable for representative user workflows, and replaceable behind an internal
adapter.

A font display name or a call to the Windows font registry is not sufficient. Ambient fonts vary
by Windows edition, language pack, update state, and user installation; fallback can change glyphs,
metrics, line fit, extraction, packaging, and license obligations. Some libraries also treat a
missing glyph as a warning, emit `.notdef`, and still produce a successful PDF. Such an output
would violate PWS-0303's explicit no-silent-substitution criterion and could disclose the rejected
character through a routine library log.

The generated artifact also needs an exact later composition seam. Saved positions are normalized
after intrinsic source orientation, while PDF content uses raw page user space, possibly with a
non-zero CropBox origin and `/UserUnit` other than 1. Letting a convenience overlay API infer
placement would make correct rotated/cropped placement unverifiable.

The selected stack already exists in the locked OCRmyPDF closure, but independent overlay use must
still make each direct dependency, font-data component, license route, trust boundary, resource
budget, and output invariant explicit.

## Decision

### Exact renderer and font closure

Use `paperworkshell.pdf.overlay_renderer.FpdfOverlayRenderer` as the one worker-only adapter with
this exact closure:

| Component | Selected identity | Role |
|---|---|---|
| fpdf2 | 2.8.8 | One-page vector PDF generation, shaped text cells, alpha/vector primitives, and TrueType subset embedding |
| fontTools | 4.63.0 | Authenticated font parsing, cmap/profile checks, and the subset path used by fpdf2 |
| uharfbuzz | 0.56.0 | Deterministic left-to-right Unicode shaping |
| Pillow | 12.3.0 | Parse only the adapter-created minimal PNG used by fpdf2 for a signature-image placement; never decode the hostile case asset here |
| pikepdf | 10.10.0 | Sanitize, deterministic save, strict reopen, and object-level security verification |
| qpdf | 12.3.2, embedded behind pikepdf | Native parse, rewrite, and deterministic-save behavior |
| OCRmyPDF | 17.10.0 | Authenticated distribution container for the selected font resource |
| CPython zlib module | compile/runtime `1.3.1.zlib-ng`, zlib-ng `2.2.4` | Exact deterministic compression for renderer-created PNG `IDAT` bytes |
| Noto Sans Regular | 2.007 | The only concrete `paperworkshell-default` text font |

Runtime checks bind fpdf2, fontTools, uharfbuzz, Pillow, pikepdf, qpdf, OCRmyPDF, and the CPython
zlib/zlib-ng compile/runtime identity to those exact values before rendering. The Pillow and
OCRmyPDF distribution versions are authenticated with package metadata, while qpdf is authenticated
through pikepdf's native-library identity. A
version mismatch is a stable dependency failure, not permission to continue with different output.
The pure `overlays.rendering` contract imports none of these libraries. fpdf2, fontTools, and
uharfbuzz are statically admitted only in the exact overlay-renderer module; pikepdf remains within
the established PDF adapter layer. The adapter never imports `PIL`: exact Pillow is reached only
inside fpdf2 while parsing the adapter's own minimal PNG, so PWS-0201's direct Pillow import remains
confined to the worker-only imaging layer.

The font is the exact `ocrmypdf/data/NotoSans-Regular.ttf` resource contained by the locked
OCRmyPDF distribution and separately inventoried as data:

- version metadata: `Version 2.007`;
- OpenType family and full name: `Noto Sans`;
- size: 556,216 bytes;
- SHA-256: `2ec33f84606cbaa0a1a944488e14f97faf2f6a25ecdd8354f5358f06da13c7d9`;
- embedded copyright: `Copyright 2015-2021 Google LLC. All Rights Reserved.`; and
- embedding permission: OS/2 `fsType = 0`.

The reviewed upstream copy is bound through OCRmyPDF commit
`a2fee443247bf43dab8ad77e14c98b0018a58d08`, while the retained OFL text is bound to the Noto
Latin/Greek/Cyrillic repository revision `0c7e5ee3312638800f9cc2162cc621a82695d02e`.
At runtime the adapter resolves the resource only inside the exact installed distribution, rejects
symlinks/path escape, verifies size and hash while creating one immutable in-memory byte snapshot,
and detects a file change across the read. It constructs both fontTools and HarfBuzz from that same
private snapshot, so neither engine retains or reopens the distribution path and a later path
replacement cannot change shaping or embedding. It performs no Windows/system-font discovery and
has no fallback.

### Fail-closed Unicode profile

The font's reviewed best cmap contains 3,053 entries. Tests prove representative Latin Extended,
Greek, Cyrillic, and Devanagari strings. Those samples define tested coverage, not a
claim that the font covers all Unicode or every orthographic sequence. Arabic, Hebrew, CJK, emoji,
and any other missing code point fail with `OVERLAY_RENDER_GLYPH_UNSUPPORTED`.

The v1 renderer accepts one left-to-right line only. Unicode control/format categories and
right-to-left or Arabic-number directional classes fail with
`OVERLAY_RENDER_TEXT_UNSUPPORTED`. The renderer does not normalize user text, insert a replacement
character, transliterate, wrap, shrink, or search for another font. A shaped line that exceeds its
saved box by its conservative emitted-glyph ink envelope fails explicitly rather than clipping
silently. Empty text remains a valid no-paint placement.

fpdf2 shapes the visible line through HarfBuzz and writes it through its cell path. The adapter
surrounds each line with marked content whose `/ActualText` is the exact original UTF-16 value.
After generation, structural verification requires one subset Type 0 font with a CIDFontType2
descendant, an embedded TrueType `/FontFile2`, and `/ToUnicode`. This gives supported shaped text a
stable extraction route while preserving scalable vector display. It does not assert tagged PDF,
PDF/UA conformance, bidirectional layout, or a complete screen-reader reading order.

Checkmarks and crosses use vector paths, so they do not depend on absent symbol glyphs. For text, the
adapter measures and emits one identical unfragmented forced-LTR HarfBuzz sequence, preserving
combining clusters that fpdf2's normal bidi fragmentation could split. It unions positioned glyph
outline extents, including side bearings, overhangs, and combining-mark offsets. When fpdf2 emits an
absolute offset/forced-advance `Tm`, the adapter expands affected glyphs by half of the 0.01-point
formatting quantum on both axes. It keeps the saved logical left origin when that conservative
envelope fits, shifts only enough to contain leading or trailing ink, and vertically centres the
envelope. Origin and baseline lie on the emitted grid. If no grid placement contains the envelope,
rendering fails. The painted box encloses its outward rounding; ascent/descent metrics describe
conservative ink relative to the exact baseline rather than global line metrics. Signature pixels
are aspect-fit and centred; shapes and highlights use explicit RGBA vector primitives. Partially
visible records retain their complete saved box and are clipped to the active-crop intersection,
never stretched to that intersection.

Every record pairs an fpdf2 local graphics context outside its crop clip. The public context keeps
fpdf2's cached font/color/opacity/line-width state synchronized with emitted PDF `q`/`Q` operators,
including when consecutive records request the same non-default style.

### Authenticated signature-image boundary

The overlay renderer does not accept or decode hostile JPEG/PNG case assets. PWS-0305 must first
authenticate the immutable encoded asset against the manifest `AssetRecord`, decode it inside the
bounded worker image boundary, remove source metadata, and construct the fixed v1 raster layout:
top-down tightly packed RGBA8 in red/green/blue/alpha byte order, straight (unassociated) alpha, and
unprofiled DeviceRGB sample interpretation. `OverlayRasterAsset` binds the same case asset UUID,
dimensions, row stride, byte count, and pixel SHA-256.

The encoded record remains authority for the stored source bytes; the raster record is authority
for the exact render pixels. The bridge must authenticate both and preserve their ID relationship.
PWS-0303 takes one bounded snapshot of exactly the required sources and immutable raster metadata,
then uses only that snapshot while rechecking pixel length/hash, rejecting over-return from each
bounded read before buffer growth, restoring each caller-owned stream position, and creating a
minimal deterministic PNG with only `IHDR`, `IDAT`, and `IEND` through the exact gated zlib-ng
compressor. fpdf2 and
the exact gated Pillow 12.3.0 parser therefore see adapter-generated metadata-free PNG bytes, not
the untrusted original encoding.

### Deterministic intermediate PDF

Render exactly one transparent overlay page for one selected source page. Its dimensions are the
complete logical page in physical typographic points after intrinsic `/Rotate` and before manifest
crop or accumulated rotation. Visible projections paint in ascending z-order. The artifact is not
a derivative and is never written to a user-selected path by this adapter.

Generation uses fixed metadata; sanitation removes the information dictionary plus generated
catalogue navigation/metadata state and saves with deterministic identifiers. Strict reopen then
requires one unencrypted page; an exact full-canvas MediaBox and either no CropBox or an exact
full-canvas CropBox; absent or default-zero
rotation; absent or default-one UserUnit; no BleedBox, TrimBox, or ArtBox; a bounded recursively
traversed object graph; exact expected font and deduplicated image resources; and no actions,
scripts, annotations, forms/XFA,
signatures, attachments, embedded
files, external links/launches, rich media, or redaction annotations. A structure failure blocks
the write. Stable results contain no user text, pixels, filename, font path, or native exception.
The caller supplies an empty seekable/truncatable operation sink. The adapter verifies the exact
written extent and makes a best-effort attempt to truncate the sink to empty after any write, flush,
cancellation, extent, or result-construction failure. Because the sink itself can reject rollback,
the caller must discard or quarantine the complete operation sink after every exception; publication
remains a later caller-owned step allowed only after the renderer returns its metadata result.

The pinned fpdf2 output and fontTools subsetting logger namespaces are disabled only around the
serialized output/subsetting call, and every prior logger state is restored immediately afterward.
Cmap preflight occurs first and fpdf2's post-output missing-glyph state is checked afterward, so
suppression prevents user characters and content-derived glyph names from entering routine logs but
never converts a missing glyph into success.

### Explicit raw-page composition seam

`OverlayPageGeometry` records a matrix from the emitted page/Form's normal bottom-left PDF
coordinates into the source page's raw user space. It accounts for selected MediaBox/CropBox,
non-zero origins, intrinsic quarter-turn, and `/UserUnit`; top-left authoring boxes are never passed
directly through this matrix.

PWS-0307 must import the verified page with pikepdf
`as_form_xobject(handle_transformations=False)` and invoke that Form through
the exact recorded matrix. It must not use `add_overlay()` and accept inferred placement. Source
content and the Form then receive the same manifest crop and accumulated rotation. PWS-0303 does
not change PWS-0112 export semantics or flatten anything itself.

### Resource contract

Every limit derives from the central versioned `ResourcePolicy`. The page/model ceilings are
combined with memory-derived raster, output, text, object, I/O-chunk, and wall-time limits. The
validated shared peak equation is:

```text
10 × maximum_total_raster_bytes + 6 × maximum_output_bytes
    <= 3/4 × worker_process_memory_bytes
```

The remaining quarter is reserved for fonts, engines, and other operation state. The multipliers
cover simultaneous decoded RGBA, deterministic PNG encoding, fpdf2 image state, raw PDF,
pikepdf input/output, and immutable result copies. The adapter uses bounded streams, no path-owned
temporary file, a cancel-aware serialized render lock, and cancellation/deadline probes around
expensive stages and chunk loops. A limit is never raised merely to fit a fixture.

### Golden approval

The initial `paperworkshell-overlay-renderer-v1` set contains three public-synthetic cases:
`primitives`, `unicode-shaping`, and `signature-image`. The production `FpdfOverlayRenderer`
generates each PDF; the admitted pypdfium2 5.12.1/PDFium 152.0.7947.0 renderer captures exact
pixels at the fixed 480 × 600 profile. The index binds PDF, stored PNG, raw-pixel, font, and engine
hashes/identities. Poppler independently rendered the candidate PDFs, and the resulting pages were
visually inspected for legibility, clipping, placement, alpha, aspect fit, and complex-script
shaping before approval.

`automatic_refresh` is false. The repository validator rebuilds through production code and
requires exact pixel/index bytes. Candidate indexes remain `candidate-requires-human-review` until
a distinct promotion command supplies the closed approval date, reviewer role, and rationale.
Promotion rejects linked, reparse, special, oversized, changed, or non-exact candidate entries and
compares exact sizes/bytes in bounded chunks. A future change must create candidates outside the
repository, explain the semantic difference, repeat PDFium and Poppler review, and record explicit
approval; goldens are never regenerated merely to make a test pass.

## Alternatives considered

### Use Windows fonts or a system fallback chain

Rejected. Availability, version, metrics, glyph choice, shaping, and licensing would depend on the
host. Silent fallback would make line fit and output differ across otherwise identical cases.

### Use PDF Base 14 fonts

Rejected. They do not provide the required representative Unicode coverage or an authenticated,
self-contained TrueType embedding strategy.

### Convert all text to vector outlines

Rejected. Outlines would sacrifice ordinary extraction and `/ActualText`/`ToUnicode` semantics,
increase output complexity, and weaken later accessibility inspection. They are not needed for the
approved tested scripts.

### Rasterize text or the entire overlay page

Rejected. Raster text scales poorly, increases memory/output demand, loses direct text semantics,
and makes small alignment changes harder to verify. Only the signature-image payload is raster.

### Ship a broad multi-font fallback catalogue now

Rejected. Every font adds provenance, license, packaging, metrics, fallback-order, and testing
obligations. The exact tested Noto profile plus explicit unsupported-glyph recovery is the smallest
coherent PWS-0303 strategy. A future user need can propose another versioned, pinned profile.

### Pass original JPEG/PNG bytes directly to fpdf2

Rejected. That would move hostile image parsing and source metadata into the font/PDF adapter and
blur PWS-0305's authenticated intake boundary.

### Let pikepdf `add_overlay()` position the artifact

Rejected for PWS-0307. Automatic page placement cannot substitute for the tested matrix that
preserves CropBox origin, intrinsic rotation, and `/UserUnit` semantics.

## Impacts

### Security

All font/PDF processing remains worker-only and network-denied. Exact dependency and font identity,
pre-parse hashing, bounded resources, cancellation, sanitized exceptions, deterministic image
encoding, strict PDF reopen, and prohibited-object inspection reduce parser, resource-exhaustion,
dependency-substitution, and active-content risk. No source original is modified.

### Privacy

Routine errors, results, logs, and golden metadata exclude user text, image bytes, source metadata,
filenames, paths, storage names, and native details. Scoped fpdf2/fontTools logger suppression
prevents missing-glyph warnings and subset glyph names from disclosing content. Test assets are
synthetic.

### Accessibility

Vector text scales cleanly; `/ActualText`, `/ToUnicode`, and an embedded font retain extraction
semantics for supported strings. Explicit unsupported and does-not-fit outcomes are recoverable
rather than silent. PWS-0306 still owns keyboard UI, focus, labels, and announcements; PWS-0307
still owns the supported-reader matrix. No PDF/UA claim is introduced.

### Licensing and packaging

Noto Sans Regular 2.007 uses OFL-1.1, which permits document embedding and subsetting under its
conditions. The exact source font is a separately inventoried data component, and shipment must
retain its copyright and the complete local
[`NotoSans-Regular-2.007-OFL-1.1.txt`](../../packaging/licenses/NotoSans-Regular-2.007-OFL-1.1.txt)
notice. fontTools uses MIT; uharfbuzz and qpdf use the selected Apache-2.0 routes; Pillow uses
MIT-CMU; pikepdf uses MPL-2.0; fpdf2 uses the selected LGPL-3.0-only route and includes a separately
inventoried ICC profile. PWS-0801 must reconcile exact packaged files,
notices, corresponding source/relinkable layout where required, hashes, and SBOM relationships.
This development admission is not a claim that the release package is complete.

### Migration and compatibility

No case-manifest, overlay wire, template, RPC, or migration meaning changes. A syntactically valid
non-default font token remains valid stored intent and fails only at concrete rendering until a
reviewed profile supports it. PWS-0307 must prove flattened placement and reader compatibility;
PWS-0308 owns the complete M3 gate.

### Standalone portfolio boundary

The selection is entirely local and offline. It adds no ThirdLife/sibling dependency, shared
runtime, service, database, adapter, schema, data ownership, release coupling, or release-interface
surface. PaperWorkShell alone owns its case, overlay, font, and derivative behavior.

## Affected surfaces

- Pure and concrete implementation: `src/paperworkshell/overlays/rendering.py`,
  `src/paperworkshell/pdf/overlay_renderer.py`, and the PDF-page geometry bridge.
- Dependency and license evidence: `pyproject.toml`, `uv.lock`,
  `packaging/dependency-inventory-v1.json`, the local Noto OFL notice, and dependency validation.
- Architecture: the overlay domain, page-coordinate, dependency-boundary, and overlay-rendering
  contracts.
- Verification: unit/security/import-boundary tests, the synthetic multilingual PDF fixture, and
  the approved `tests/golden/overlay-renderer-v1` production-renderer references.

No released schema, migration, case-storage layout, worker RPC, UI, export/publication behavior,
installer, file association, or portfolio interface is changed by this ADR.

## Revisit criteria

A font version/hash, engine version, shaping direction, fallback rule, glyph profile, subset or
embedding structure, ActualText strategy, pixel-decoding boundary, or placement-seam change needs
fresh dependency/license/security review, updated synthetic candidates and extraction/visual
evidence, and a superseding ADR where it replaces this selection. Quiet system fallback or golden
refresh is not a compatible change.
