# Bounded JPEG/PNG intake and image-page normalization

- Status: PWS-0201 implementation contract
- Task: PWS-0201
- Decisions: PWS-D007, PWS-D009, PWS-D011, PWS-D012, PWS-D013, PWS-D034
- Import boundary: [Immutable source-ingest contract](immutable-ingest.md)
- Worker: [Restricted worker lifecycle contract](worker-lifecycle.md)
- Security flow: [DF-IMPORT and DF-PREVIEW](../security/data-flows.md)
- Downstream transforms: [Non-destructive scan-transform pipeline](image-transform-pipeline.md)

## Scope and ownership

PWS-0201 makes content-detected JPEG and PNG sources usable as non-destructive one-page documents.
The trusted import path first copies and independently verifies the selected bytes under a random
case-owned name. Only then may a fresh restricted worker receive an exact reduced-right read handle
to that immutable case copy. Pillow 12.3.0 remains behind `paperworkshell.imaging`; no Pillow object,
encoded image, metadata value, path, or raw exception crosses into application or UI code.

The feature owns complete JPEG/PNG decode, bounded structural metadata inspection, EXIF orientation
intent, color/alpha normalization, a fixed correlated RGBA8 artifact, mixed PDF/image preview
orchestration, and organizer presentation. It does not rewrite an original, persist a re-encoded
image as authoritative state, alter manifest-v1 meaning, provide arbitrary animation/TIFF/HEIC
support, or claim calibrated color fidelity.

PWS-0202 consumes this normalized page without changing PWS-0201's source meaning. Its exact order
is intrinsic EXIF orientation, crop in full logical pre-manifest-transform coordinates, accumulated
quarter-turn, fine deskew, contrast, background cleanup, and bounded fit/serialization. A new
transform capability and artifact bind that complete intent; `image.normalize` version 1 remains
the closed intake operation and is not silently widened.

The package direction is:

```text
UI -> app.page_previews -> domain.image_pages
                       -> structural image-preview generator port

trusted composition -> storage prepared/committed-source ports
                    -> worker image broker

worker host -> imaging.pillow_decoder
```

## Import and immutable-source binding

The lightweight precheck continues to identify JPEG/PNG by bytes and reject obvious framing,
dimension, pixel, expansion, or source-size violations without loading pixels. A successful
precheck is not a successful decode. The import transaction then:

1. streams the same authenticated external handle to a random pending case file while hashing it;
2. flushes, reopens, independently hashes, and retains the exact pending object;
3. lends only an independently positioned read-only handle for worker inspection;
4. runs a complete one-frame decode and one-pixel normalization under the image operation limits;
5. confirms whole-tree worker termination and validates the correlated result; and
6. only then publishes the unchanged case copy and one manifest page.

Any format mismatch, truncated stream, malformed structure, unsupported frame/color form, resource
failure, cancellation, timeout, worker failure, or invalid result leaves the previous manifest
authoritative and the external original untouched. The worker has no manifest, journal, original
publication, case-directory, or external-source write authority.

Committed previews resolve the current page through the validated manifest and authenticate the
random committed source again: case/source/page binding, media type, source page count/index, byte
size, SHA-256, file identity, containment, link/reparse/sparse state, and alternate streams. The
same retained read handle remains authoritative until the one-shot worker tree has ended.

## EXIF orientation and non-destructive rotation

All eight TIFF/EXIF orientation values are represented by the closed `ImageExifOrientation` model,
including the four mirrored forms. Orientation is intrinsic interpretation of immutable source
bytes. It is derived and validated on every worker decode and is not converted into a manifest
rotation command or written back to the source.

Manifest-v1 user rotation remains a separate clockwise quarter-turn intent. A preview first applies
the intrinsic EXIF transform, then the current user quarter-turn, then fits the oriented raster into
the requested bounds without upscaling. This preserves all mirror semantics that manifest-v1's
quarter-turn operation cannot express while keeping undo/redo behavior unchanged. Persisting
intrinsic image metadata would require a separately versioned manifest migration; PWS-0201 does not
silently add fields to the released schema.

## Decode and normalization profile

The admitted source formats and outputs are closed:

- baseline/progressive JPEG and single-frame PNG are accepted when complete and within limits;
- APNG and every multi-frame result are rejected rather than selecting a frame silently;
- bilevel, grayscale, grayscale-plus-transparency, palette, palette-plus-alpha, RGB,
  RGB-plus-transparency, RGBA, and JPEG CMYK are classified explicitly;
- alpha and PNG `tRNS` are composited onto opaque white;
- output is packed top-down RGBA8 with `alpha == 255` for every pixel; and
- embedded profile and EXIF presence are reported only as booleans/counts, not copied metadata
  values or a colorimetric-conformance claim.

The Pillow version is checked against the admitted exact release before source access. The decoder
disables truncated-image loading, rejects decompression warnings/errors, bounds PNG text memory,
performs its own bounded PNG/JPEG record walk, parses only the bounded TIFF IFD0 orientation field,
and restores the caller's stream position. The PNG walk enforces letter-only chunk types, the
reserved-bit rule, one `IHDR`/`IEND`, contiguous `IDAT`, and rejection of unknown critical chunks.
It also bounds and accounts for `PLTE`, applies bit-depth/color-type palette rules, requires indexed
palettes before image data, and validates the exact color-type-specific `tRNS` size and ordering.
JPEG APP1/APP2 segments count as EXIF/ICC only when their exact signatures match. Unrelated
application segments remain opaque bounded metadata.

Pillow conversion and resampling are deterministic within the pinned decoder profile. The decoder
does not copy no-op orientation/rotation stages and closes every superseded Pillow core before the
next stage. Planning charges the caller-owned destination plus the largest permitted decode,
three-source-raster alpha-composite/resize, source-plus-resize-output, or
output-plus-serialization stage; it also charges bounded scan/metadata state and 25 percent
allocator/native headroom. Native
decode/transpose/convert/resize calls are not cooperatively interruptible from inside one call, so
the retained Windows Job memory/CPU limits and whole-tree timeout/termination remain authoritative;
cooperative probes still run before, between, and during bounded Python-level copy phases.

## Resource and temporary-storage limits

The selected central `ResourcePolicy` supplies the production worker/process budget. Each image
operation additionally validates a closed `ResourceLimits` record before dispatch:

- one source and one output object only;
- source byte identity from the immutable import record;
- encoded width, height, source pixels, and decompressed-byte estimate;
- metadata byte and record counts;
- requested output width, height, pixels, exact bytes, and fixed artifact header;
- worker wall/CPU time and process/Job memory; and
- worker-private temporary raster capacity plus the fixed free-space reserve.

Normal and constrained profiles currently cap requested previews at 4,000,000 pixels per page.
The worker operation is stricter than the domain's defensive absolute ceilings. Inclusive boundary
tests prove exact-limit acceptance and maximum-plus-one rejection. An allocation or native parser
failure maps to a stable limit/dependency error; it never exposes native diagnostics.
Image-specific failure tests also prove that a capacity refusal occurs before decode, a partial
anonymous-raster write is closed and removed without touching the mapped result, and a fresh retry
can produce a validated artifact.

## RPC, artifact, and trust handoff

The additive protocol-v1 capability is `image.normalize` version 1. One request contains exactly one
JPEG/PNG source reference plus distinct worker-local source/output handles, one random output UUID,
bounded preview dimensions, a quarter-turn, and explicit resource limits. The broker duplicates
read-only source authority and map-write authority for one fixed-size anonymous output section. It
does not transfer a path or persistent cache writer.

The worker writes a fixed version-1 binary artifact: correlated operation/output UUIDs, closed
metadata and layout codes, dimensions/stride/counts, exact source/output facts and digests, followed
by the exact opaque RGBA8 payload. RPC success refers to its content kind, byte count, and whole
artifact SHA-256; raster bytes never enter RPC. The broker confirms one-shot Job-tree termination,
reads only the claimed bounded bytes, and revalidates every version, ID, length, digest, geometry,
orientation, color, and alpha invariant before constructing `ImagePagePreviewResult`.

Unlike PDF thumbnails, PWS-0201 does not publish a persistent image-raster cache file. Validated
image pixels enter the organizer's existing byte-budgeted QPixmap LRU, keyed by internal page
identity and current request generation. Eviction is disposable and regeneration reauthenticates
and decodes the immutable source. This is the task's image-to-page-cache handoff without granting a
worker persistent storage authority.

## Mixed preview orchestration and accessibility

`PagePreviewService` accepts one ordered tuple of typed PDF/image requests for a single case and
dispatches each page serially under one monotonic deadline and cancellation probe. Aggregate
progress is content-free: lookup, generating, validating, complete, completed-page count, and total
page count. A mismatched type, page identity, result, or progress phase fails the batch.

The organizer uses the same keyboard, focus, progress announcement, cancel, retry, and byte-LRU
behavior for PDF and image pages. Image rows no longer say “Preview unavailable.” User rotation is
included in the next bounded request and never changes the original. Errors use plain-language
recovery text and contain no filename, path, metadata value, raster, or decoder message.

## Dependency and release boundary

Pillow 12.3.0 was already locked as part of the pikepdf closure; PWS-0201 promotes that exact
reviewed wheel to a direct worker-only dependency. The inventory records the MIT-CMU route, native
code/parser boundary, disabled optional libimagequant component, hashes, and PWS-0801 release
closure work. Live-worker resources stage and authenticate that package before the shared fixture
is published; tests never trust or mutate a partial Pillow directory afterward. This change adds no
network need and does not approve Pillow for the GUI process.

## Verification evidence

Focused domain/adapter/protocol tests cover all eight EXIF values, rotation composition, JPEG/PNG
framing and identity, strict chunk/marker grammar, CMYK, palette/alpha/`tRNS`, malformed/truncated
input, multi-frame rejection, conservative peak-memory planning, metadata/decompression/temp/output
limits, dependency mismatch before source access, cancellation, timeout, clean retry, exact artifact
correlation, opaque alpha, and sanitized exceptions. A direct AppContainer probe proves the real
Pillow adapter succeeds with reduced source/output authority inside the configured Job. The broker
integration exercises the authenticated committed-image lease and preserves exact external and
case-copy hashes through real import and preview generations. Mixed PDF/image ordering, organizer
handoff, accessible progress/cancellation, and byte-LRU behavior are proved at the application and
widget boundaries. Security tests scan representations, tracebacks, progress, and routine logs for
synthetic path, name, metadata, digest, and pixel canaries.
