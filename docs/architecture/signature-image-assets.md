# Signature-image assets and placement

Status: PWS-0305 implementation contract
Decisions: PWS-D009, PWS-D020, PWS-D021, PWS-D027, PWS-D028
Depends on: immutable case intake from PWS-0104, normalized page coordinates from PWS-0301,
overlay intent from PWS-0302, and the worker renderer boundary from PWS-0303

## Product meaning

The user action is **Place signature image**. It places a visual image only. It does not create or
validate a digital signature or a qualified electronic signature. The desktop workflow requires the
user to acknowledge that distinction before it can save a placement, and routine completion text
describes an image as placed rather than a document as signed.

The encoded image is an immutable case asset. The placement is an ordinary `signature_image`
overlay whose `asset_id` refers to that asset and whose box uses the same normalized full-page
coordinate system as every other overlay. Page crop, accumulated quarter-turn rotation, intrinsic
PDF rotation, and non-zero page-box origins are therefore handled by the PWS-0301 projection seam;
they do not alter the stored normalized intent.

## Intake and storage boundary

Only local JPEG and PNG inputs are admitted. The trusted GUI/application process never decodes the
selected image. The case-storage adapter performs the same Windows path, reparse-point, device-path,
hard-link, alternate-data-stream, access-control, media-signature, byte-limit, and change-during-read
checks used for immutable document intake. It copies the selected bytes through an authenticated
handle into a random pending file, hashes and reads back the complete copy, then publishes it as
`originals/<asset UUID>.bin`. The external source is never renamed, overwritten, or modified.

The asset becomes authoritative only through one journaled manifest transaction that records its
media type, byte count, SHA-256, and random storage name together with the first overlay reference.
The copy is published before the transaction checkpoint so crash recovery can either complete the
manifest change or remove an unreferenced orphan. Startup recovery treats the union of manifest
source and asset storage names as authoritative; it must not mistake a valid signature asset for an
orphan.

The selected path, original filename, encoded bytes, digest, decoded pixels, preview pixels, and
asset identifiers are absent from routine logs, announcements, exceptions, representations, and
diagnostic summaries. Failures cross layers only as closed signature-image codes with bounded,
content-free contexts and approved plain-language recovery messages.

## Worker-only raster preparation

The encoded case copy remains hostile. A committed read lease re-authenticates the exact case file,
ACL, path containment, media type, byte size, and digest before duplicating a read-only handle into a
fresh network-denied document worker. The `image.signature_raster` capability accepts exactly one
`ASSET_READ` handle and no path. It has a separate versioned request/result and artifact profile; it
does not change the established opaque-white `image.normalize` artifact contract.

The worker applies EXIF orientation, enforces configured encoded-byte, dimension, pixel,
decompression, memory, CPU, temporary-storage, and output limits, and resizes only when required to
fit the requested bound. It never enlarges a source. Embedded ICC and distinguishable non-sRGB color
profiles fail closed in this profile instead of invoking an additional color-management parser;
unprofiled/sRGB inputs use the profile's fixed sRGB/DeviceRGB interpretation. The result is
metadata-free, top-down, tightly packed RGBA8 with straight alpha. RGB samples of fully transparent
pixels are canonical zero so invisible source content cannot survive as a covert pixel channel. The
broker authenticates the artifact header, correlation identifiers, exact extent, and pixel SHA-256
before exposing an in-memory preview or an `OverlayRasterAsset` source to the renderer.

One worker process handles one raster operation. Cancellation, deadline expiry, protocol failure,
invalid worker output, or cleanup failure terminates the operation and prevents manifest mutation.
The asset lease and every temporary/output handle are deterministically closed.

## Preview, placement, and persistence

The preview may show preserved transparency over a neutral/checkerboard or white display
background. That choice affects preview display only. Manifest schema versions 1 and 2 have no
durable crop rectangle or background-removal recipe for an asset, and PWS-0305 does not reinterpret
or extend a released schema in place. The saved intent therefore consists of the complete immutable
encoded asset plus one aspect-fitted normalized placement box. A future durable crop or cleanup
recipe requires an explicit versioned schema decision and migration.

The placement dialog provides labelled numeric left, top, width, and height controls in normalized
page units, keyboard access, visible focus, a non-color status announcement, a preview alternative,
and the confirmation **I understand that this image does not digitally sign the PDF.** Saving uses a
compare-and-swap manifest revision so a concurrent case edit fails for review instead of silently
overwriting newer work.

## Deletion policy

Removing a placement first commits a manifest candidate without that overlay. The user can retain
the case asset, or explicitly select **Remove unused signature image from this case**. In the latter
mode the asset record is removed from the same candidate only when no remaining overlay references
it. The exact case file is eligible for physical deletion only after the committed manifest proves
the asset absent, under the case transaction guard and after a fresh authenticated path/identity
check. A shared asset is always retained.

An uncertain manifest commit is never followed by speculative deletion. If post-commit file cleanup
fails, the placement remains removed and the result truthfully reports that the unused case copy
could not be removed; recovery can later remove the manifest-unreferenced file. This ordering favors
recoverable residue over a dangling manifest reference.

## Security, accessibility, and portfolio effects

No untrusted full image parsing occurs in the GUI process, no worker receives a filesystem path, no
shell command or network request is used, and no external original is changed. Synthetic tests place
distinct canaries in source paths, filenames, encoded metadata, pixels, logs, errors, UI state, and
announcements to prove the prohibited values do not cross those seams.

The workflow is usable without a pointer and never relies on color or a bitmap preview alone.
Progress, cancellation, errors, confirmation state, placement coordinates, completion, and cleanup
outcomes have accessible text. The PWS-0306 [accessible overlay
editor](accessible-overlay-editor.md) now provides richer on-page selection, guides, zoom/pan, and
keyboard geometry manipulation across all overlay types while preserving this workflow's explicit
signature asset cleanup boundary.

PaperWorkShell remains standalone and owns its case assets, decoded transient rasters, overlays, and
manifest transactions. This work adds no sibling repository, shared service, database, schema,
adapter, release cadence, or ThirdLife data access. The human-readable release interface already
distinguishes placed images from cryptographic signatures, so no release-interface change or
portfolio future note is required.
