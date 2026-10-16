# Visual overlay domain model

- Status: Implemented PWS-0302 contract; PWS-0303 rendering; PWS-0304 editing extension
- Task: PWS-0302
- Extension: PWS-0303 deterministic overlay-page rendering
- Editing extension: PWS-0304 core visual form-mark commands and keyboard dialog
- Decisions: PWS-D010, PWS-D018, PWS-D020, PWS-D036
- Depends on: PWS-0301 normalized page coordinates

## Scope and ownership

`paperworkshell.overlays.models` is the pure, standard-library representation of visual-overlay
intent. It consumes the overlay-v1 wire already present in both released case-manifest schemas. It
does not open files, decode image bytes, render a page, choose a font implementation, mutate case
storage, or import UI, worker, PDF, imaging, or OCR code.

PWS-0302 binds that released wire to typed records and semantic validation. It does not introduce
case-manifest v3, overlay-v2, a schema default, or a new persistence field. The canonical v1 and v2
schema bytes, their packaged copies, and their reviewed fingerprints remain unchanged:

| Manifest | Reviewed SHA-256 fingerprint |
|---|---|
| v1 | `a4dfbc925b601ce3dca08085173a742642cab6b962b4358f91d921bd9fdbd695` |
| v2 | `af27d3d3569d660638371d840a35b754335b074021c39d20cc4f282df4c7e599` |

## Released records

Every overlay record has one UUID, `overlay_version: 1`, a known case `page_id`, a strict
[`normalized_page_millionths_v1`](page-coordinate-model.md) box, a non-negative `z_index`, and one
closed payload. The saved box is full-page intent after intrinsic source orientation and before
manifest crop or rotation. A crop may hide or clip its visible projection, but never rewrites the
record.

The payload union is unchanged from the released schemas:

| Intent | Required payload state |
|---|---|
| Text, date, initials | Inert `value`, syntactic `font_token`, integer size from 1 through 288 points, and eight-digit RGBA color |
| Checkmark, cross | Eight-digit RGBA color |
| Signature image | Case-internal `asset_id` and exact `visual_mark_not_digital_signature` terminology |
| Highlight, ordinary shape | Rectangle, ellipse, or line; fill and stroke RGBA colors; exact `visual_overlay_only_not_permanent_removal` intent |

Empty textual values, mixed-case hexadecimal RGBA digits, and every syntactically valid released
font token remain wire-compatible. The model does not normalize those values or infer a display
value. Font availability, deterministic fallback, and concrete vector placement belong to
[PWS-0303](overlay-rendering.md). The initial concrete renderer recognizes only
`paperworkshell-default`; another syntactically valid token remains valid saved intent but fails
closed at rendering until a later reviewed font profile admits it.

The only released asset record is a case-internal `signature_image`. It has an internal UUID-based
storage name, JPEG or PNG media type, positive byte count, and SHA-256 digest. A signature-image
overlay must resolve to such an asset. The record carries no external path, original filename, or
cryptographic-signature state.

## Identity, ordering, and visibility

Overlay, asset, page, and the other manifest UUIDs share the case namespace. Duplicate identities,
unknown page references, unknown signature-image assets, and incompatible asset kinds fail closed.

Draw order is determined only by `(page_id, z_index)`:

- lower `z_index` values paint first and higher values paint on top;
- indices may be sparse;
- two overlays on one page cannot share an index; and
- persisted array order is retained for round trips but has no draw-order meaning.

Visibility is a derived projection and is never saved. An overlay is `page_inactive` when its page
is absent from `page_sequence`. On an active page it is `outside_active_crop` when its full-page box
does not intersect the current crop, `visible` when the complete record box is inside the crop, and
`partially_visible` when the crop produces a strict intersection. A visible projection retains the
complete record box; a partially visible projection exposes only the intersection as its visible
box. The original record remains unchanged in all four cases. An overlay attached to a removed
page therefore survives and becomes eligible for display again if the page is restored.

## Validation and safe failures

The complete manifest remains subject to the fingerprint-bound runtime validator before use. The
overlay layer then accepts only the exact released record members and payload union. It rejects
unsupported overlay versions and kinds, malformed IDs, boxes, colors, font tokens, sizes, asset
metadata and references, identifier collisions, and per-page z-order collisions.

Failures use closed `OVERLAY_*` codes and bounded structural contexts. Error text and object
representations do not include text/date/initial values, asset digests, storage names, or image
data. Those values are user content and must not enter routine logs or diagnostics.

The terminology constants describe state; they do not filter user-authored text. A text overlay may
contain words such as “signature” or “redaction” as inert content. Conversely:

- a placed signature image never claims a digital or cryptographic signature; and
- a highlight or ordinary shape never claims redaction or permanent removal.

Permanent removal remains gated to the later redaction milestone and requires separate extraction
and revision verification.

## Manifest migration

PWS-0602 extends the case record to [manifest v3](../schemas/case-manifest-v3.md), preserving this
overlay wire and keeping new unapplied removal marks in a separate collection. Current repository
loads upgrade v1/v2 to v3 in one atomic transaction. The historical overlay-preservation behavior
of the v1-to-v2 step remains as follows.

The v1-to-v2 migration validates and settles v1,
creates a defensive deep copy, changes `schema_version` to `2`, increments `revision`, validates the
complete v2 candidate, and publishes through the transactional repository. Assets, overlays,
nested boxes and payloads, collection order, and every other member are copied without
canonicalization or inferred defaults.

A valid no-overlay v1 manifest contains the required `assets: []` and `overlays: []` members. They
remain empty, fresh collections after migration. Omitting either required member is invalid; load
does not silently add it. Populated overlay-v1 records likewise survive value-for-value unchanged,
including records for inactive pages.

Unknown future manifest or overlay versions fail before mutation. Migration never changes
`overlay_version`, terminology metadata, z-order, visibility, coordinates, or asset references.

## Deferred work

The pure model still stops at intent and derived visibility. PWS-0303 adds a separate pure render
request/result contract and a worker-only PDF adapter; it does not add editing commands, decode a
stored signature image, mutate a case, or flatten an export. The [overlay rendering
contract](overlay-rendering.md) specifies that boundary and the selected font strategy.

PWS-0304 established bounded core create/update/delete commands, ephemeral undo/redo history, and
the exact keyboard form-mark path retained inside [Core visual form-mark
tools](core-overlay-tools.md).
PWS-0305 now provides [authenticated, bounded signature-image intake](signature-image-assets.md) and
the bridge from an immutable encoded case asset to metadata-free RGBA8 pixels. PWS-0306 now provides
the [full-page accessible editor](accessible-overlay-editor.md), bounded page projection, selection
geometry, zoom, guides, snapping, and pointer interaction without changing the released model.
PWS-0307 owns
Form-XObject flattening plus reopen/render and reader verification. None of those deferrals permits
an external service or sibling product to own PaperWorkShell case data.
