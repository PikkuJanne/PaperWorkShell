# Known limitations

Development worker startup inventories and grants access to the selected Python
runtime before the process starts. A large development environment can delay
startup and cancellation during this setup. The synthetic smoke helper offers
explicit minimal runtime staging; it does not fix that development-path delay.

This describes the current development checkout, not a packaged release.

## v0.1 scope and deferred features

- Permanent redaction is absent. Shapes, highlights, and flattened overlays are visual
  marks and do not prove removal of underlying information. Permanent removal remains
  gated by Milestone 6.
- **Place signature image** places a visual mark. PaperWorkShell does not create or
  validate cryptographic or qualified electronic signatures. Modified signed inputs
  produce unsigned derivatives after explicit consent; imported originals stay unchanged.
- The workflow is local and requires no product account or cloud service. There is no
  direct portal submission, eligibility decision, legal advice, identity verification,
  or guarantee of acceptance. Users compare the package with the recipient's current
  instructions and submit it themselves.
- Form entry uses visual overlays. Arbitrary AcroForm editing, dynamic XFA, JavaScript,
  calculation-heavy forms, and cryptographic signature fields are outside v0.1 editing.
- Target-size compression and broader compatibility/sanitization work remain in
  Milestone 5. A size limit can block export; it does not guarantee a smaller output.

## Guided package review and export

- The current package review does not rerun full source inspection, page geometry
  analysis, or advisory quality analysis. Unavailable checks remain visible as unverified
  and require human review; acknowledging them does not make them passes.
- Combining scan/image processing and active visual overlays in the same PDF group is
  unsupported. The workflow rejects the combination rather than discarding intent.
- Package results and unsaved summaries are transient. Save the optional plain-text
  summary before closing when it is needed. The summary may include intended output
  filenames and category labels; review it before sharing.
- No optional technical manifest or portable case-transfer format is implemented.
  See the [package review contract](docs/architecture/package-summary.md) for exact
  verification, publication, and recovery behavior.

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
The maintainer accepted the existing PWS-0408 walkthrough on 2026-09-30; see the
[acceptance record](docs/development/pws0408-result.md#maintainer-acceptance).
The separate PWS-0409 v0.1 gate is in progress and has not passed. ThirdLife and sibling
applications are not required and no compatibility cut is claimed.
