# Normalized page coordinate model

- Status: Implemented PWS-0301 contract; PWS-0303 overlay-placement extension
- Task: PWS-0301
- Extension: PWS-0303 deterministic overlay-page placement
- Decisions: PWS-D011, PWS-D018
- Depends on: PWS-0006 manifest contracts; PWS-0209 Milestone 2 gate

## Scope and ownership

`domain.page_coordinates` is the renderer- and toolkit-independent authority for translating among
saved page intent, raw PDF user space, continuous raster edges, and logical screen coordinates. It
uses only the Python standard library and pure domain support. It does not parse a document, open a
path, allocate a bitmap, inspect UI state, or import Qt, Pillow, pikepdf, PDFium, worker, storage, or
renderer implementations.

The model preserves the released `normalized_page_millionths_v1` meaning. PWS-0301 does not alter
the case-manifest v1 or v2 schema, migrate stored data, add overlay payload fields, or implement
vector rendering. PWS-0302 owns overlay records and validation; PWS-0303 owns concrete vector
placement and font strategy.

## Named coordinate spaces

| Space | Origin and axes | Units and bounds | Stored? |
|---|---|---|---|
| Normalized full page | top-left; +x right, +y down; after intrinsic source orientation and before manifest crop/rotation | integer millionths; points use closed edges `0..1_000_000`; positive boxes are half-open | boxes use the released manifest codec |
| Raw PDF default user space | the PDF page's unrotated coordinate system; conventionally bottom-left and +y up | finite raw PDF user units inside the selected effective box | no |
| PDF typographic space | same source coordinate axes | raw user coordinate multiplied by `/UserUnit`, in physical 1/72-inch points | no |
| Rendered image | top-left; +x right, +y down | continuous outer-edge coordinates `[0,width] × [0,height]`; these are not pixel indices | no |
| Logical screen | toolkit/widget logical coordinate system | continuous coordinates within the actual displayed page-content viewport | no |
| Device pixel | display-scaled screen coordinates | logical screen coordinate multiplied once by the explicit device-pixel ratio | no |

Actual pixel indices remain half-open (`0 <= index < dimension`) and pixel centres are at
`index + 0.5`. A renderer's integer allocation is authoritative for raster mapping. The model does
not reuse a continuous PDF fit scale after a renderer has rounded an output dimension.

## Released normalized box codec

`NormalizedPageBox` owns the one exact shared crop/overlay shape:

```json
{
  "coordinate_space": "normalized_page_millionths_v1",
  "x": 100000,
  "y": 200000,
  "width": 600000,
  "height": 500000
}
```

The codec rejects missing or extra members, another coordinate token, booleans, floating-point
members, zero spans, inversion, and any edge outside the page. `page_operations.NormalizedPageBox`
is an explicit compatibility re-export of this canonical type, so existing crop operations retain
their public import and exact serialized shape. The same codec can be consumed by overlay records
without changing either released manifest schema.

Box construction is strict by default. `NormalizedBoxClipPolicy.CLIP_TO_PAGE` is the only generic
page-bound clipping policy and must be named at the call site. `visible_part()` separately
intersects an overlay/crop box with the active manifest crop for rendering; it returns no box when
the regions are disjoint and never mutates the saved intent. A later crop therefore cannot move or
rewrite a saved overlay.

## PDF box and intrinsic-orientation rules

The PDF adapter resolves and validates MediaBox, CropBox, `/Rotate`, and `/UserUnit` before creating
the pure coordinate geometry. Media mode uses MediaBox. Crop mode uses the non-empty intersection
of CropBox and MediaBox, with an absent CropBox equal to MediaBox. Source PDF rectangles may be
normalized by the PDF parser boundary; user-created normalized boxes are never endpoint-sorted.

Let the selected raw effective box be `E = [L,R] × [B,T]`, with `W = R-L`, `H = T-B`, and let
`(u,v)` be full-page normalized fractions after intrinsic orientation. The inverse map from saved
logical position to raw PDF user space is:

| Intrinsic clockwise rotation | raw `X` | raw `Y` |
|---|---:|---:|
| 0° | `L + uW` | `T - vH` |
| 90° | `L + vW` | `B + uH` |
| 180° | `R - uW` | `B + vH` |
| 270° | `R - vW` | `T - uH` |

The forward raw-PDF map is the exact inverse. This makes intrinsic `/Rotate` part of source
interpretation rather than a manifest edit. It also keeps the full-page normalized basis aligned
with what a user sees before applying their own crop or rotation.

Raw PDF user units are deliberately not called physical points. `/UserUnit` has no effect on
normalized placement or preview fit; the explicit raw-to-typographic conversion multiplies both
coordinates by `/UserUnit`. This preserves the established renderer behavior while preventing a
later overlay/export adapter from silently treating a `/UserUnit 2` page as if one raw unit were
one physical point. Geometry construction also proves that the selected box remains finite and
millionth-resolvable after this physical scaling; raw/typographic point conversion is limited to
that selected box and fails closed outside it.

## Manifest crop and clockwise rotation

For manifest crop edges `(cl,ct,cr,cb)` expressed as normalized fractions, a visible full-page
point becomes crop-local `(a,b)`:

```text
a = (u - cl) / (cr - cl)
b = (v - ct) / (cb - ct)
```

The accumulated manifest quarter-turn is then applied clockwise:

| Rotation | oriented x | oriented y |
|---|---:|---:|
| 0° | `a` | `b` |
| 90° | `1-b` | `a` |
| 180° | `1-a` | `1-b` |
| 270° | `b` | `1-a` |

The inverse table is respectively `(x,y)`, `(y,1-x)`, `(1-x,1-y)`, and `(1-y,x)`. Crop is always
evaluated before the manifest turn regardless of operation chronology. PWS-0301 does not apply or
claim alignment through the later deskew stage.

## Raster, viewport, and display scaling

For actual rendered dimensions `(Iw,Ih)`, oriented fractions map to continuous image edges as
`px = x*Iw`, `py = y*Ih`. A logical page-content viewport `(sx,sy,Sw,Sh)` then maps them as:

```text
screen_x = sx + px * Sw / Iw
screen_y = sy + py * Sh / Ih
```

The viewport is the displayed page content, not the outer widget; callers resolve centring,
letterboxing, zoom, and pan before constructing it. Device-pixel ratio is a separate reversible
logical/device conversion. It does not change PDF coordinates, normalized intent, saved boxes, or
the logical viewport and must not be applied twice by Qt callers.

## Quantization and round-trip bounds

Saved points and boxes remain exact integers. Intermediate crop and quarter-turn math uses rational
arithmetic; floats enter only at PDF, raster, and screen boundaries. Point conversion uses
deterministic nearest, half-up quantization.

- Exact manifest decode/encode has zero tolerance.
- A representable normalized point converted to another space and back recovers each normalized
  axis within one integer millionth; exact rational cases recover exactly.
- For PDF → normalized → PDF, `pdf_round_trip_tolerance()` reports the per-axis bound implemented
  by the model: half a raw box span per million normalized units plus 64 ULPs of the relevant box
  coordinates/span. Rotation changes which normalized axis controls a raw axis, not this raw-axis
  bound.
- `BoxQuantization.NEAREST` rounds every continuous edge half-up. The default explicit
  `OUTWARD` policy floors left/top and ceils right/bottom so a selected area is not lost; conversion
  through floats may expand a previously integral edge by at most one millionth.
- Raster and screen error after normalized quantization scales with the actual raster dimension or
  viewport span divided by the active crop's integer width/height. Tests compare recovered saved
  intent in millionths instead of claiming a universal pixel epsilon.

Tolerance never admits invalid input. PDF boxes and screen viewports are rejected when their span
is too small relative to the floating-point ULP at their origin to preserve nearest-millionth
placement. Viewport logical and device-scaled edges must also remain finite, resolvable to that
precision, and inside the model's bounded continuous coordinate range. Non-finite values, invalid
scales, points outside the active crop/viewport, disjoint PDF boxes, and unsupported rotations
likewise fail before conversion with a stable sanitized `PAGE_COORDINATE_*` error. Once a screen
point has passed the closed viewport check, exact outer edges are normalized to exact raster edges
so harmless floating-point residue cannot turn the model's own output into an out-of-bounds
inverse input.

## Overlay-page placement seam

PWS-0303 consumes the pure `PdfPageCoordinateGeometry` only after the PDF adapter has converted its
validated `PdfResolvedPageGeometry` through `pdf.page_geometry.to_page_coordinate_geometry`. It
creates a full logical overlay canvas in physical typographic points after intrinsic source
orientation and before manifest crop or rotation. Authoring boxes remain top-left-origin on that
canvas. The generated one-page PDF, like every PDF page/Form, exposes bottom-left-origin PDF
coordinates.

`OverlayPdfPlacementMatrix` maps those **generated PDF/Form coordinates**, not top-left authoring
coordinates, into the selected source page's raw PDF user space. Its scale therefore includes the
inverse of `/UserUnit`, and its quarter-turn reflects intrinsic `/Rotate`. The matrix maps every
full-canvas corner to the same raw edge produced by the normalized-coordinate model, including
non-zero page-box origins, CropBox selection, all four intrinsic rotations, and `/UserUnit 2`.

PWS-0307 must convert the verified overlay page with pikepdf
`as_form_xobject(handle_transformations=False)` and invoke the
result through the recorded matrix while assembling the derivative. It must not use pikepdf
`add_overlay()`: automatic page placement would bypass the explicit raw-user-space contract. The
source and placed Form then receive the same manifest crop and accumulated rotation. PWS-0303 does
not itself mutate a source page or publish an export. See the [deterministic overlay rendering
contract](overlay-rendering.md).

## Verification boundary

Fixed-seed generated tests cover non-zero origins, effective MediaBox/CropBox selection, all
intrinsic and manifest quarter-turns, random crops, mixed page sizes, raster dimensions, viewport
offsets, zoom, and device scale. Known PDF quadrant centres are also mapped into the existing
approved `render-geometry-matrix` PNG references for every intrinsic and additional quarter-turn.
Those references were reused without refresh. `/UserUnit 2` has a separate raw-versus-physical
assertion.

PWS-0301 proves deterministic coordinate decisions and placement against synthetic renderer
evidence. PWS-0303 additionally proves the overlay-canvas matrix against those same pure transforms
and renders separate synthetic overlay goldens. Neither task claims PDF/UA conformance,
physical-device display calibration, arbitrary deskew alignment, portal acceptance, flattened
export compatibility, or completion of the PWS-0307 reader matrix.
