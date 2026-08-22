# Deterministic visual-overlay rendering contract

- Status: Implemented PWS-0303 contract
- Task: PWS-0303
- Decisions: PWS-D015, PWS-D018, PWS-D034
- Depends on: PWS-0105 PDF structure; PWS-0106 renderer evidence; PWS-0301 normalized page
  coordinates; PWS-0302 visual-overlay intent
- Font decision: [ADR 0009](../adr/0009-overlay-font-rendering.md)

## Scope and ownership

PWS-0303 turns a validated stack of visible overlay intent into one bounded, transparent,
self-contained PDF page. The page is an intermediate worker artifact for later flattening, not a
published derivative. It never modifies the source PDF, case manifest, or encoded image asset, and
never opens or writes a user-selected external path. Verified bytes go only to the caller-provided
intermediate destination stream.

`paperworkshell.overlays.rendering` owns pure request, geometry, limit, stream, cancellation,
placement, result, and closed-error contracts. It imports no PDF, font, image, UI, storage, or
worker implementation. `paperworkshell.pdf.overlay_renderer` is the exact worker-only adapter that
owns the fpdf2/fontTools/uharfbuzz/Pillow and pikepdf/qpdf execution paths, font-file access,
deterministic image encoding, and PDF object inspection. It does not import `PIL`; fpdf2 alone
invokes the exact Pillow dependency for the adapter-created PNG. No third-party object or native
exception crosses that seam.

The complete output-affecting production closure is fail-closed:

| Component | Exact identity | Output role |
|---|---|---|
| fpdf2 | 2.8.8 | Vector page generation, shaped cells, primitives, and embedded subsets |
| fontTools | 4.63.0 | Font authentication, metrics, and subsetting |
| uharfbuzz | 0.56.0 | Left-to-right shaping |
| Pillow | 12.3.0 | Parse the renderer-created minimal signature PNG inside fpdf2 |
| pikepdf | 10.10.0 | Sanitize, save, reopen, and inspect the PDF |
| qpdf | 12.3.2 | Native PDF parse/rewrite engine embedded behind pikepdf |
| OCRmyPDF | 17.10.0 | Distribution container for the exact Noto resource |
| CPython zlib module | compile/runtime `1.3.1.zlib-ng`, zlib-ng `2.2.4` | Deterministic signature-PNG `IDAT` compression |
| Noto Sans Regular | Version 2.007 and the digest below | Concrete overlay font |
| pypdfium2/PDFium | 5.12.1 / 152.0.7947.0 | Approved golden-pixel renderer |

The runtime gate authenticates every software identity, including the compile/runtime zlib-ng
identity, before rendering; font loading then authenticates the Noto file. The golden index records
all of them plus the exact font and PDFium identities. Missing or changed identity is a closed
dependency failure.

The initial adapter handles all PWS-0302 overlay variants:

| Intent | Rendering rule |
|---|---|
| Text, date, initials | One unfragmented forced-left-to-right line fitted by a conservative emitted-glyph ink envelope; no wrap, fallback, or automatic shrink |
| Checkmark, cross | Vector strokes independent of symbol-font coverage |
| Signature image | Authenticated metadata-free top-down RGBA8 pixels with straight alpha and DeviceRGB interpretation, aspect-fit and centred without upending the saved box |
| Highlight, ordinary shape | RGBA vector rectangle, ellipse, or line; it remains an ordinary visual overlay and never claims permanent removal |

Lower `z_index` values paint first. A partially visible overlay is painted at its original full-page
record box under a clip to the derived crop intersection. It is never resized to the visible
portion. Empty text is a valid no-paint placement with zero content-free text metrics.

Each record uses an fpdf2 `local_context()` outside its crop clip. This pairs the emitted PDF
graphics-state save/restore with fpdf2's own cached font, color, opacity, and line-width state, so
consecutive records with the same non-default style still emit and paint that style independently.

## Request and result boundary

One `OverlayPageRenderRequest` contains an internal page UUID, full logical page geometry, active
manifest crop, and an already ordered tuple of visible/partially-visible projections. Construction
rechecks page identity, strictly increasing z-order, and the exact crop intersection. Outside-crop
or inactive projections never reach the renderer.

Callers also supply:

- a `ResourcePolicy`-derived `OverlayRenderLimits` value;
- one exact caller-owned `OverlayRasterSource` for every referenced signature-image UUID, and no
  extras;
- a caller-owned, empty, seekable, truncatable operation-scoped staging stream rather than a path;
  the caller publishes it only after a result and discards it on failure; and
- cancellation and deadline probes owned by the restricted worker operation.

The renderer restores every raster stream's original position and writes only after generation,
sanitization, and strict reopen verification succeed. It verifies the exact final staging extent and
makes a best-effort attempt to truncate it back to empty on any write, flush, cancellation, extent,
or result-construction failure. A hostile or failed sink can also reject that rollback, so the caller
must discard or quarantine the entire operation-scoped sink after every exception and must publish it
only after a result is returned. The result exposes page/canvas identity,
the raw-PDF placement matrix, content-free placement boxes and numeric text metrics, byte count,
PDF SHA-256, embedded-font count, and signature-image placement count. It contains no text value,
pixel data, filename, path, storage name, font path, or native diagnostic.

Failures use closed `OVERLAY_RENDER_*` codes and bounded structural contexts. Text, code points,
asset hashes, image bytes, PDF bytes, and dependency exception details never appear in the error or
its representation.

## Font and text semantics

The only concrete token in this renderer version is `paperworkshell-default`. It resolves to the
exact Noto Sans Regular 2.007 file contained in the locked OCRmyPDF 17.10.0 distribution:

- relative resource: `ocrmypdf/data/NotoSans-Regular.ttf`;
- byte size: 556,216;
- SHA-256: `2ec33f84606cbaa0a1a944488e14f97faf2f6a25ecdd8354f5358f06da13c7d9`;
- OpenType family/full name: `Noto Sans`;
- embedded version string: `Version 2.007`; and
- OpenType embedding permission `fsType = 0`.

The adapter resolves the file inside that exact installed distribution, rejects a symlink or path
escape, authenticates its size and digest while creating one immutable in-memory byte snapshot, and
detects a size/mtime change across the read. Both fontTools and HarfBuzz are then constructed from
that same private snapshot; neither engine retains or reopens the distribution path. A later path
replacement therefore cannot substitute a different shaped or embedded font. The adapter never asks
Windows for a font and has no fallback chain. An unavailable token, changed file, wrong engine
version, missing glyph, or unsupported text property fails closed before fpdf2 output.

The reviewed cmap has 3,053 entries. Approved tests exercise representative Latin Extended,
Greek, Cyrillic, and Devanagari text; that is evidence for those strings, not a promise
that every language or every sequence in those scripts works. Arabic, Hebrew, CJK, emoji, and any
other missing code point are unsupported in this profile. All Unicode control/format characters
and right-to-left or Arabic-number directional classes are also rejected. No normalization,
transliteration, replacement character, `.notdef` glyph, ambient fallback, or silent substitution
is permitted.

fpdf2 2.8.8 delegates shaping through uharfbuzz 0.56.0 and uses fontTools 4.63.0 for the embedded
subset path. Every non-empty line is wrapped in marked content whose `/ActualText` is the exact
original UTF-16 string. The generated font is then structurally required to be a Type 0 font with
one CIDFontType2 descendant, embedded TrueType `/FontFile2`, and `/ToUnicode`. This preserves
extractable text through shaped clusters without converting visible text to outlines. It does not
claim tagged PDF, PDF/UA conformance, correct bidirectional layout, or a complete assistive reading
order.

The adapter shapes and emits the same single, unfragmented forced-LTR HarfBuzz sequence, so fpdf2's
bidi fragmenter cannot split a combining cluster into a different painted sequence. It unions every
positioned glyph outline extent, including side bearings, overhangs, advances, and combining-mark
offsets. fpdf2 formats an absolute glyph-position `Tm` to 0.01 point for an offset glyph or after a
forced advance reset; each affected glyph receives a closed half-quantum envelope on both axes so
the fit cannot accept ink moved outward by numeric formatting. The adapter retains the saved logical
left origin when this envelope fits there; otherwise it shifts only far enough to contain a leading
or trailing overhang. The emitted-ink envelope is vertically centred. Origin and baseline are on the
exact 0.01-point grid, and the line fails with `OVERLAY_RENDER_TEXT_DOES_NOT_FIT` when no grid
placement contains the envelope. The reported painted box rounds that envelope outward; ascent and
descent metrics are conservative ink distances from the emitted baseline, not global font metrics.

## Signature-image trust boundary

The overlay adapter deliberately does not parse the case asset's JPEG/PNG bytes. PWS-0305 owns the
future bridge that must:

1. authenticate the immutable encoded case asset against its manifest `AssetRecord` identity,
   media type, byte size, digest, and protected case location;
2. decode it through the bounded worker image boundary and remove filename, EXIF, ICC, comment,
   and other source metadata;
3. produce top-down tightly packed 8-bit red/green/blue/alpha rows with straight (unassociated)
   alpha and RGB samples normalized for the renderer's unprofiled DeviceRGB interpretation; and
4. bind those pixels to an `OverlayRasterAsset` with the same case asset UUID, exact dimensions,
   row stride, byte count, and pixel SHA-256.

The stored encoded `AssetRecord` remains authority for the immutable case file. The decoded
`OverlayRasterAsset` becomes render authority for the exact pixel representation. A compliant
bridge proves the ID relationship and both identities; neither record substitutes for the other.

PWS-0303 takes one bounded snapshot of exactly the required sources and immutable raster metadata.
Using only that snapshot, it authenticates pixel length and SHA-256 while reading in bounded chunks,
rejects a source that returns more bytes than requested before extending or hashing a buffer,
restores each stream position, then encodes a minimal PNG itself with fixed RGBA8 parameters,
filter-zero rows, and deterministic compression through the exact gated CPython zlib-ng closure.
The only PNG chunks are `IHDR`, `IDAT`, and `IEND`. Consequently fpdf2 and its exact Pillow 12.3.0 parser receive renderer-created,
metadata-free PNG bytes rather than the hostile original image encoding.

## Coordinate and crop seam

The PDF adapter first converts `PdfResolvedPageGeometry` into the pure
`PdfPageCoordinateGeometry`. `OverlayPageGeometry` then creates a canvas in physical typographic
points for the complete selected page box after intrinsic `/Rotate` and before manifest crop or
accumulated rotation. Saved normalized boxes and canvas authoring boxes use a top-left origin with
positive y downward.

The emitted PDF page uses ordinary bottom-left PDF coordinates. Its `pdf_placement` matrix maps an
emitted PDF/Form point `(x,y)` to source raw user space as
`(a*x + c*y + e, b*x + d*y + f)`. For raw effective box `[L,R] × [B,T]`, raw spans `W,H`, and
logical canvas size `Cw,Ch` in physical points, the matrix is:

| Intrinsic rotation | `[a b c d e f]` |
|---|---|
| 0° | `[W/Cw, 0, 0, H/Ch, L, B]` |
| 90° | `[0, H/Cw, -W/Ch, 0, R, B]` |
| 180° | `[-W/Cw, 0, 0, -H/Ch, R, T]` |
| 270° | `[0, -H/Cw, W/Ch, 0, L, T]` |

Because `Cw` and `Ch` include `/UserUnit`, this maps physical overlay points back to raw source
units without treating `/UserUnit 2` as ordinary one-point units. Tests bind all corners against
the PWS-0301 transform for non-zero origins, MediaBox/CropBox selection, every intrinsic turn, and
`/UserUnit 2`.

PWS-0307 must use the verified overlay page's pikepdf
`as_form_xobject(handle_transformations=False)` representation and invoke
that Form through the recorded matrix in the destination document. It must not call `add_overlay()`
and accept automatic placement, because that would bypass the explicit coordinate contract. The
source page and placed Form then undergo the same manifest crop and accumulated quarter-turn before
flattened export verification. This task does not alter the current PWS-0112 rule that rejects
active overlays from an export path that has not implemented PWS-0307.

## Resource and lifecycle controls

Limits derive from the central versioned `ResourcePolicy`; the adapter does not invent a profile
label or unbounded queue. The derivation caps the model at 4,096 overlays, one page per operation,
32,768 pixels per raster dimension, 268,435,456 pixels, 100,000 PDF objects, and the policy's CPU
deadline and I/O chunk size. More restrictive memory-derived limits apply:

- each decoded raster is bounded by the pixel demand and one thirty-second of worker memory;
- all decoded rasters together use at most one thirty-second of worker memory and the source-byte
  budget;
- the generated/sanitized PDF uses at most one sixteenth of worker memory and the temporary-byte
  budget;
- total text receives at most one code point per KiB of worker memory, within the overlay model's
  own ceiling; and
- the validated peak equation reserves ten times the total-raster allowance plus six times the
  output allowance while leaving at least one quarter of worker memory for engines, fonts, and
  other operation state.

The adapter reads/writes in bounded chunks, checks cancellation/deadline probes before expensive
stages and during font/raster/output loops, uses a cancel-aware serialized rendering lock, bounds
in-memory output, and creates no renderer-owned temporary path. Cancellation, deadline, output
limit, malformed destination, changed source, and cleanup failures remain distinct sanitized
outcomes.

## PDF sanitation and verification

Generation uses fixed document metadata and exact dependency versions, including qpdf 12.3.2
behind pikepdf. Before any byte reaches the destination, pikepdf reopens without recovery, removes
generation-only catalogue navigation and metadata plus the trailer information dictionary, saves
with deterministic IDs, and strictly reopens the result. Verification requires:

- exactly one unencrypted page with an exact full-canvas MediaBox and either no CropBox or an exact
  full-canvas CropBox;
- absent/default-zero intrinsic rotation, absent/default-one UserUnit, and no BleedBox, TrimBox, or
  ArtBox anywhere in its inherited page geometry;
- an object count within policy;
- the exact expected embedded-font and deduplicated image-resource structure;
- no actions, annotations, AcroForm/XFA, signatures, attachments, embedded files, JavaScript,
  external URI/launch actions, submit actions, rich media, or redaction annotations; and
- no unexpected catalogue open action, names tree, metadata, or interactive field state.

The prohibited-object pass walks the complete reachable direct and indirect dictionary/array/stream
graph plus every indirect object, terminates indirect cycles by object-generation identity, charges
every visited value to the PDF-object budget, and probes cancellation throughout. Active content
cannot evade the check by nesting inside a direct resource, outline item, or other inline value.

The pinned fpdf2 output and fontTools subsetting logger namespaces are disabled only inside the
serialized final output call, and every prior state is restored immediately afterward. This prevents
missing-glyph warnings, user characters, and content-derived glyph names from entering routine logs.
Cmap preflight and a post-output missing-glyph check are still mandatory; logger suppression is
defense in depth, not a fallback policy.

With the same validated intent, authenticated pixels, page geometry, resource policy, and exact
engine/font closure, ordering, placement, text decisions, PNG bytes, and sanitized PDF bytes are
deterministic. A dependency or font identity mismatch fails rather than silently changing output.

## Golden approval policy

`tests/golden/overlay-renderer-v1` is a closed public-synthetic set covering primitives,
representative Unicode shaping, and a metadata-free signature-image placement. The production
`FpdfOverlayRenderer` produces each PDF; the admitted pypdfium2 5.12.1/PDFium 152.0.7947.0 adapter
captures its exact fixed-size pixels. The index binds the raw RGBA digest, stored RGB PNG digest,
PDF digest, font identity, and engine profile. The profile independently records fpdf2, fontTools,
uharfbuzz, Pillow, pikepdf, qpdf, OCRmyPDF, the CPython zlib/zlib-ng identity, Noto, pypdfium2, and
PDFium. The same candidate PDFs were
independently rendered with Poppler and visually inspected before approval.

`automatic_refresh` is false. Validation rebuilds every reference through production code and
compares exact bytes. A staged index is explicitly `candidate-requires-human-review`; only the
separate promotion action accepts the closed date/role/rationale approval record and writes an
approved index. Candidate entries must be single-link regular non-reparse files with the exact
regenerated size and bytes, compared in bounded chunks. A justified visual change requires candidate
output outside the repository, an explanation, repeated PDFium/Poppler inspection, and explicit
approval; a failing assertion is not permission to overwrite a reference.

The explicit lifecycle is:

```text
python tools/overlay_render_goldens.py --candidate <new-outside-repository-directory>
python tools/overlay_render_goldens.py --approve-candidate <reviewed-directory> --reviewed-at 2026-08-22 --reviewer-role implementation-agent --rationale corrected-pws-0303-rendering-baseline-after-audit
python tools/overlay_render_goldens.py --validate
```

## Security, privacy, accessibility, and portfolio effects

All PDF/font work remains inside the network-denied document worker. The adapter receives no
external path, invokes no shell or executable, performs no network operation, and never modifies an
original. Routine errors/results are content-free; fixtures and goldens are synthetic.

Vector text and marks remain sharp under scaling. `/ActualText` and `/ToUnicode` preserve an
extraction path for supported text, while explicit unsupported/does-not-fit outcomes allow the
later accessible editor to focus a recoverable field instead of silently changing it. PWS-0306
still owns keyboard/editor announcements, and PWS-0307 still owns reader and flattening evidence.

PaperWorkShell remains a complete standalone product. Rendering is local and uses only
PaperWorkShell-owned intent and case-scoped assets. It adds no sibling repository, shared runtime,
schema, database, service, adapter, release cadence, or ThirdLife data access. The human-readable
release interface is unchanged, so no portfolio future note is required.
