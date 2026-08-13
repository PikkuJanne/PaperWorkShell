# Non-destructive scan-transform pipeline

- Status: PWS-0202 implementation and evidence contract
- Task: PWS-0202
- Decisions: PWS-D009, PWS-D011, PWS-D012, PWS-D013, PWS-D016, PWS-D035, PWS-D041
- Intake boundary: [Bounded JPEG/PNG intake](image-intake.md)
- Worker boundary: [Restricted worker lifecycle](worker-lifecycle.md)
- Resource policy: [Low-resource engineering policy](../performance/LOW_RESOURCE_POLICY.md)
- Fixture policy: [Synthetic fixture policy](../development/synthetic-fixture-policy.md)

## Scope

PWS-0202 adds reviewable crop, clockwise quarter-turn, fine deskew, contrast, and background-cleanup
intent. The authoritative case records normalized operation data; it does not replace or rewrite an
imported source. A restricted worker reauthenticates the immutable source and materializes one
bounded derivative preview or downstream page at a time. The result remains disposable and
reproducible from the same source identity, ordered intent, transform profile, and requested output
bounds.

The profile-v1 raster worker consumes only imported JPEG and PNG pages. Pure manifest replay keeps
operation intent independent of media type, but current PDF preview and export consumers fail closed
when crop, deskew, non-neutral contrast, or background cleanup targets a PDF page; they never show or
publish an untransformed page as though that intent had been applied. Clockwise quarter-turn remains
supported by the PDF page-copy path. A later versioned PDF-raster consumer is required before the
other scan transforms can apply to PDF sources.

This task does not add OCR, infer that a page is clean or legible, silently accept an automatic
suggestion, or publish an export. PWS-0203 owns before/after confirmation controls. PWS-0204 consumes
the transform contract for local OCR, PWS-0207 owns review findings, and PWS-0503 may later reuse the
bounded raster seam for explicitly selected image re-encoding.

## Canonical order

Every implementation, cache key, worker request, golden record, and downstream consumer uses this
exact order:

1. Apply intrinsic EXIF orientation.
2. Apply crop in the full logical page's normalized, pre-manifest-transform coordinate space.
3. Apply the accumulated clockwise quarter-turn.
4. Apply fine deskew.
5. Apply contrast.
6. Apply background cleanup.
7. Fit to the requested bounds and serialize the fixed output layout.

The machine contract preserves fit and serialization as distinct checkpoints even though the prose
groups them into one terminal operation. Its exact `IMAGE_TRANSFORM_ORDER` tuple is therefore
`("exif", "crop", "quarter_turn", "deskew", "contrast", "background_cleanup", "fit",
"serialize")`. Golden indexes and pipeline bindings must contain those eight tokens exactly and in
that order.

The order is semantic, not an optimization hint. Implementations may omit a no-op allocation, but
they may not commute stages merely because two particular inputs happen to look equivalent. Crop
coordinates remain independent of preview size and integer pixel rounding. A crop selected before
a later rotate or deskew therefore retains the same logical region when the operation history is
replayed.

Intrinsic EXIF orientation is derived from immutable source bytes on each decode and is not stored
as user rotation. Quarter-turn intent is discrete and exact. Fine deskew is distinct from both and
must remain within the approved narrow angle range. Crop boxes must have positive area and remain
inside the normalized page. Non-finite, noncanonical, extreme, or type-confused values fail before
worker dispatch.

Contrast and background cleanup are deterministic profile operations, not quality claims.
Background cleanup may change only the documented tonal range; it is never described as removing
private information. The fixed terminal layout remains top-down, opaque-white RGBA8 so untrusted
encoded image data never returns to the GUI process.

## Deterministic intent and cache identity

Transform intent has a closed version and canonical serialization. Caller-owned mappings are
copied before validation, booleans are not accepted as numbers, identifiers are canonical UUIDv4,
and replay is bounded by the case manifest's operation limit. The current page projection is
derived from the complete contiguous operation sequence; undo and redo restore exact operation
identity and parameters.

A reusable result key must bind at least:

- internal case, source, and page identities;
- immutable source byte count and SHA-256;
- source page index and the admitted decoder profile;
- the transform contract and pipeline versions;
- the fixed-width canonical bytes (and full SHA-256) of the replayed final crop, accumulated
  quarter-turn, latest deskew, latest contrast, and latest cleanup state;
- requested maximum dimensions, output pixel layout, opaque-white policy, and resampling profile;
  and
- every dependency version that can change pixels.

Golden provenance additionally records the exact transform intent, cache-key, and artifact
versions; the transform engine-profile SHA-256; the upstream strict-normalization-profile SHA-256;
and the admitted Pillow version. Neither a friendly profile label nor a dependency name alone is a
sufficient pixel-policy binding.

The key is not source authority. Storage reauthenticates the current committed source before lookup
or worker launch. A stale generation, mismatched operation digest, wrong dimensions, unexpected
alpha, or wrong result correlation is rejected. Disposable result invalidation never changes the
manifest or an original.

Operation IDs, overwritten operation values, equivalent history shapes, case revision, and undo/
redo provenance are deliberately not cache identity: they cannot change pixels after replay. The
immutable source identity/digest, complete final semantic intent, requested bounds, and versioned
engine/profile still separate every pixel-affecting input. This permits two histories that project
to the same page transform to reuse only the same disposable result while any latest-wins value,
crop, accumulated turn, source, bound, or profile change selects a different key.

## Worker execution and trust handoff

The application sends only typed internal identities, canonical transform intent, bounds, and
resource limits. The trusted broker resolves and authenticates one exact committed source, creates
one bounded volatile output authority, and transfers reduced source-read/output-write handles only
after a fresh one-shot worker has negotiated the transform capability. No source path, display
name, case directory, manifest writer, journal writer, or persistent cache writer crosses the
boundary.

The worker verifies the complete source identity again before decode. It applies the seven
conceptual operations through all eight machine checkpoints
under the pinned image adapter and writes a fixed, versioned artifact that binds the operation,
output object, transform/pipeline profile, canonical operation digest, geometry, stride, byte count,
pixel digest, and opaque-alpha invariant. RPC success is not trusted. The broker first confirms
whole Job-tree termination, then validates the complete artifact and request correlation before a
typed result may reach application code.

Cancellation is checked before source work, between every bounded Python stage, during row-copy or
serialization loops, and before result publication. A native decoder/resampler call that cannot be
interrupted cooperatively remains bounded by the operation deadline, Windows Job limits, and
whole-tree termination. A late success cannot win after the trusted client has settled cancellation
or timeout.

## Resource and failure contract

Processing is incremental by page. The planner charges the destination plus every simultaneously
live source, crop, rotation, deskew, contrast, cleanup, and serialization raster needed by the
selected implementation, including bounded metadata/scratch state and native-allocation headroom.
No packet-wide full-resolution materialization, unbounded queue, extra worker, or new cache is
permitted. Production composition uses the central versioned `ResourcePolicy`; a profile name alone
does not alter a limit.

Before private temporary output is created, the capacity seam reserves its complete worst-case
bytes plus the approved volume floor. Exact-boundary and one-byte-short tests are required. A
capacity refusal occurs before decode. Disk full or cancellation during a write closes and removes
only the identity-matched partial, releases reservations and handles, leaves the last manifest
authoritative, and permits a fresh retry. Cleanup uncertainty never authorizes reuse or deletion of
an unproved object.

Dimension, pixel, decompression, metadata, memory, CPU, wall-time, temporary-storage, output, and
result limits all fail through stable sanitized codes. Error text, progress, routine logs, and
diagnostics contain no path, filename, source or pixel digest, metadata value, image bytes, OCR or
document text, native exception, or transform preview.

## Golden-output evidence

The closed `image-transforms-v1` set contains exactly six small public-synthetic outputs:

| Case | Source geometry | Output geometry | Primary oracle |
|---|---:|---:|---|
| `rotate-90` | 13×9 | 9×13 | asymmetric corners and exact clockwise turn |
| `crop` | 16×12 | 8×6 | pixel-aligned normalized middle crop |
| `deskew` | 48×32 | 48×32 | fine angle, preserved canvas, white fill, line continuity |
| `contrast` | 32×8 | 32×8 | deterministic grayscale-ramp response |
| `background-cleanup` | 32×16 | 32×16 | dark-stroke retention and paper-field cleanup |
| `transparency` | 12×10 | 12×10 | opaque-white alpha normalization through the full no-op chain |

The different dimensions also prove mixed page-size handling. Composition/order tests remain
separate from these one-primary-effect images so a failure identifies the stage responsible.
Production output is compared as exact RGBA8 pixels; PNG encoding is only the deterministic review
container.

[`tools/image_transform_goldens.py`](../../tools/image_transform_goldens.py) has no production
transform import. It creates deterministic source rasters and can stage supplied production results
only outside the repository. The review set contains a labeled before/after HTML contact sheet and
a candidate index whose status is `candidate-requires-human-review`. It is deliberately not
promotable as approved evidence.

The approval workflow is:

1. Run focused unit, property, worker, cancellation, resource, source-hash, and privacy tests.
2. Produce all six candidate results from the exact pinned production profile into a temporary
   directory outside the repository.
3. Inspect every before/after pair at native scale and enlarged nearest-neighbour scale. Confirm
   crop borders, direction, fill, line continuity, tonal behavior, stroke retention, alpha, and
   geometry rather than checking only that an image exists.
4. Record the profile/operation bindings and review date, role, rationale, golden-index SHA-256,
   and visual conclusion in PWS-0202 evidence.
5. Invoke the separate explicit approval step to create a promotion inventory outside the
   repository, then copy only its closed index and six PNGs into the reviewed golden directory.
6. Run the non-mutating golden/fixture validator and inspect the Git diff. Never regenerate or
   replace approved output merely because a test failed.

The index binds each source recipe and pixel/PNG digest, exact eight-token parameter list and
pipeline digest, intent/cache/artifact versions, transform and normalization profile digests,
Pillow version, output geometry/layout and pixel/PNG digest, comparison rule, approval record,
PWS-D035, and PWS-0202. Any added, missing, linked, noncanonical, mutated, nonopaque, or unapproved
entry fails closed. An intentional pixel-policy change requires a reviewed `image-transforms-v2`
set; it does not rewrite version-1 evidence in place.

## Required verification seams

- Pure unit and fixed-seed generated-property tests cover value boundaries, operation replay,
  canonical serialization, deterministic composition, undo/redo, and cache-key separation.
- Adapter tests cover every stage and no-op, extreme crop/deskew rejection, transparency,
  malformed input, exact memory/output boundaries, cancellation checkpoints, native failure, and
  repeat equality.
- Worker/protocol/broker tests cover negotiation, malformed parameters, identifier aliasing,
  digest/profile/result mismatches, cancellation/timeout races, one-shot cleanup, disk full,
  low-space preflight, fresh retry, and late-output suppression.
- Windows integration compares external and committed source SHA-256 before and after a successful
  constrained-profile transform. Focused broker, adapter, cancellation, and resource-failure tests
  prove that rejected work never receives source-write authority, private intermediates are removed,
  and a fresh retry succeeds. This is bounded software evidence, not a physical-hardware
  certification claim.
- Privacy tests use distinct canaries in source paths, names, metadata, operation payloads, pixel
  buffers, native exceptions, progress, representations, tracebacks, and routine logs.
- PWS-0203 retains accessibility ownership for apply/reset/cancel/compare controls, keyboard
  adjustment, focus recovery, announcements, and 200% scaling. PWS-0202 keeps progress and errors
  content-free and cancelable so that UI can consume them safely.
