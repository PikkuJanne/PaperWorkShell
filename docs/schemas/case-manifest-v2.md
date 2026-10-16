# Case manifest JSON Schema v2

Historical contract: PWS-0602 introduces [current v3](case-manifest-v3.md). Frozen v2 remains
a validated migration input; its bytes and semantics are unchanged. The v1-to-v2 behavior below
records its original transition. Current repository loads upgrade v1/v2 to v3 in one transaction.

`PWS-0202` introduced the frozen v2 case-state contract at
[`schemas/case-manifest-v2.schema.json`](../../schemas/case-manifest-v2.schema.json). The schema is
a closed, self-contained JSON Schema Draft 2020-12 document and is fingerprint-bound independently
from the immutable [v1 contract](case-manifest-v1.md). Validation never retrieves a network
resource and never applies schema defaults.

## Version transition

Version 2 retains every v1 top-level member and all existing meanings. Its only structural
extension is three typed, non-destructive scan-transform operation payloads. Existing `rotate`,
`crop`, `change_sequence`, and `set_group` records retain `operation_version: 1`; their released
meaning was not edited in place. The new payloads require `operation_version: 2`:

| `kind` | Exact parameter | Inclusive range | Replay rule |
|---|---|---:|---|
| `deskew` | `angle_millidegrees_clockwise` | `-15000` to `15000` | latest value wins |
| `contrast` | `factor_millionths` | `0` to `4000000` | latest value wins; `1000000` is neutral |
| `background_cleanup` | `strength_millionths` | `0` to `1000000` | latest value wins; `0` is neutral |

Every payload also contains `kind` and a canonical `page_id`. Parameters are integers: booleans,
floats, non-finite values, unknown members, mismatched operation versions, and values beyond a
bound fail validation. New operations can address only a page already introduced by the replay
baseline or an earlier import transition. They may remain attached to an inactive page so removal
does not destroy reversible intent.

Absence of a scan-transform operation projects to the neutral state. This is a replay property,
not a schema default: validation and migration never insert an operation or infer user intent.
Clockwise quarter-turns continue to accumulate modulo four and crop continues to use the latest
box.

## Coordinate and transform semantics

`normalized_page_millionths_v1` remains the full logical page coordinate space established by v1.
Crop is evaluated there before any manifest transform. Every renderer, preview, cache, worker, and
downstream consumer must use this exact order:

1. intrinsic EXIF orientation;
2. crop in full logical pre-manifest-transform coordinates;
3. accumulated clockwise quarter-turn;
4. deskew;
5. contrast;
6. background cleanup; and
7. bounded fit and serialization.

The order is semantic and cannot be changed by the chronological order in which a user appended
operations. Full runtime and cache-key requirements are recorded in the
[non-destructive scan-transform pipeline](../architecture/image-transform-pipeline.md).
Background cleanup is a tonal operation, not permanent removal or a redaction claim.

The shared PDF/raster/screen mapping, strict normalized-box codec, and quantization tolerances are
specified in the [normalized page coordinate model](../architecture/page-coordinate-model.md).

## Overlay-v1 runtime binding

PWS-0302 binds the overlay-v1 records already present, with identical meaning, in manifest v1 and
v2. It does not add a manifest or overlay version and does not change either schema. The pure
[visual overlay domain model](../architecture/overlay-domain-model.md) validates the released
text, date, checkmark, cross, initials, signature-image, highlight, and simple-shape payload union.

Saved array order is round-trip state, not draw order. Lower per-page `z_index` values paint first,
and duplicate `(page_id, z_index)` pairs are invalid. Visibility is derived from `page_sequence`
and the active crop: `visible` retains the complete record box, `partially_visible` carries a
strict crop intersection, and `outside_active_crop` and `page_inactive` carry no visible box. These
states are never persisted, and deriving them never moves, clips, or deletes the full-page
normalized overlay record. Signature images retain the exact
`visual_mark_not_digital_signature` metadata, while highlights and shapes retain
`visual_overlay_only_not_permanent_removal`. User-authored textual values remain inert content and
are not interpreted as terminology metadata.

## Automatic v1-to-v2 migration

A repository load first validates and settles the current manifest and its one recovery checkpoint.
Only after that recovery decision is final does it migrate a settled v1 snapshot. The unique
migration candidate is a defensive copy with exactly two changes:

- `schema_version` changes from `1` to `2`; and
- `revision` increments by one.

All other members, values, collection order, source hashes and references, page identities and
sequence, operation identities/order/intent, assets, overlays, assignments, constraints,
confirmations, timestamps, and export-history references remain equal. Assets, overlay records,
boxes, and payloads are fresh nested copies with equal values. In particular, the migration does
not add neutral cleanup operations, infer overlays, or rewrite legacy operation versions.

The complete source is validated with the exact v1 schema and the complete candidate with the
exact v2 schema. Publication then uses the existing two-stage journal transaction and retained-
handle compare-and-swap boundary. An interruption after checkpoint publication is recovered to
that exact candidate on the next load. Successful loads expose only v2 snapshots and report
`migrated` or `recovered_migrated` when the transition occurred; a later load is idempotently
`current`. Unknown future versions fail before recovery or migration mutation.

The migration retains the original `updated_at` value so the transform is deterministic and does
not invent user activity. Revision exhaustion fails before mutation.

## Fixtures and validation

Canonical examples are
[`case-manifest-v2-minimal.json`](../../schemas/examples/case-manifest-v2-minimal.json) and
[`case-manifest-v2-representative.json`](../../schemas/examples/case-manifest-v2-representative.json).
The representative example carries legacy organizer operations and all three v2 scan transforms.
Negative fixtures cover future and legacy dispatch, an out-of-range deskew angle, and an operation-
version/payload mismatch. Migration fixtures preserve a complete representative graph and prove
copy-only, exact, idempotent transactional behavior. Focused migration evidence also proves that
populated asset/overlay graphs are fresh-copied without value loss and that the required empty
`assets` and `overlays` collections in the v1 minimal example remain empty after migration.

The v2 parsing/resource envelope remains the bounded v1 envelope: 8 MiB, 64 nesting levels,
100,000 JSON nodes, and 4,096 items per manifest collection. The runtime validator binds v1 and v2
to separate reviewed schema fingerprints so supplying one version's schema for the other fails
closed. PWS-0302 leaves those fingerprints unchanged: v1 is
`a4dfbc925b601ce3dca08085173a742642cab6b962b4358f91d921bd9fdbd695` and v2 is
`af27d3d3569d660638371d840a35b754335b074021c39d20cc4f282df4c7e599`.
