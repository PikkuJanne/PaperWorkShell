# Known limitations

Development worker startup inventories and grants access to the selected Python
runtime before the process starts. A large development environment can delay
startup and cancellation during this setup. The synthetic smoke helper offers
explicit minimal runtime staging; it does not fix that development-path delay.

This describes the current development checkout, not a packaged release.

## Image intake and ordinary PDF export

- JPEG and PNG are the supported image inputs. APNG, MPO/multi-frame JPEG, TIFF,
  HEIC, and 16-bit PNG are unsupported. Unsupported inputs produce a plain-language
  failure and do not replace the case manifest or an original.
- EXIF orientations 1–8, including mirrored forms, are interpreted from the immutable
  image on each decode. Original bytes are never rewritten.
- PNG transparency is composited onto white. Unprofiled RGB/grayscale/indexed images
  and PNG sRGB declarations are supported. Embedded ICC profiles and unsupported
  gamma/chromaticity and PNG 3 color/HDR declarations (`cICP`, `mDCV`, `cLLI`) are
  rejected. CMYK JPEG uses the pinned decoder's
  uncalibrated conversion; accurate print-color matching is not claimed.
- **Export pages as PDF** produces a verified derivative of the ordered pages without
  OCR or a template checklist. It supports intrinsic image orientation and quarter-turn
  rotation. Crop, deskew, cleanup, and active overlays need their existing specialized
  export workflow; this action rejects them rather than discarding their intent.
- Image PDF pages use an explicit page-size policy; source DPI metadata does not
  establish a verified physical document size. Preview pixels are for review, not a
  guarantee of print color or portal acceptance.
- Input, decoded memory, output, and temporary-space ceilings may reject large files.
  See [LOW_SPEC.md](LOW_SPEC.md).

## Release and acceptance status

The repository has no completed installer or packaged release claim. Native binary
redistribution, notices, corresponding source, final SBOM, and final product licence
remain subject to PWS-0801 and the release gates. The
[release interface](docs/release/RELEASE_INTERFACE.md) retains its existing `TBD` facts.
PWS-0408 still requires its separate human first-time-user acceptance; an automated
image smoke does not satisfy that requirement. ThirdLife and sibling applications
are not required and no compatibility cut is claimed.
