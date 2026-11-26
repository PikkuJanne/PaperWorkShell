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
- Target-size compression supports the ordinary PDF pipeline. Groups requiring image
  intake, crop, overlays, scan cleanup or OCR retain their existing verified output
  and may remain too large. Broader compatibility/sanitization work remains in
  Milestone 5. A size limit does not guarantee a smaller output.
- Safe object/stream optimization may keep the same size when the verified baseline is
  already compact or the temporary-storage budget cannot hold another candidate. It does
  not change image resolution/quality or remove reachable metadata, annotations, or
  attachments. An invalid candidate fails export. Current result/summary size comparisons
  are transient; history recovery retains final sizes and checks without recreating them.
  See the [optimization contract](docs/architecture/pdf-safe-optimization.md).
- The conservative optimization profile requires opaque stream encodings to remain
  exact. The pinned writer rewrites ASCIIHex streams as Flate streams; this case fails
  export during comparison and preserves the originals and existing exports.
- Image re-encoding supports opaque
  eight-bit RGB/gray images with known direct-page placement and page-local resources. Shared
  images, inherited resource aliases, masks, forms, unsupported color/filter semantics, and
  uncertain placement remain unchanged.
  The size-reduction review shows measured candidate sizes and actual page renders.
  Its fixed 200 dpi/JPEG80 floor does not prove legibility, and there is no override
  below the floor. Grayscale requires an explicit choice and affects only eligible
  images. Bounded previews may hide details visible at full resolution; the review
  acknowledgement does not claim every affected page was inspected.
  See the [review workflow](docs/architecture/compression-review.md).
  See the [image stage contract](docs/architecture/pdf-image-stages.md).

## Guided package review and export

- The [conservative portal profile](docs/architecture/portal-export-profile.md) blocks
  prohibited features retained in final package PDFs. It does not convert arbitrary
  source forms or annotations, sanitize page content, or guarantee portal acceptance.
  Source fonts remain preserved and may be substituted by a reader. PDF 2.0 and
  extension-dependent sources are outside this profile. New reader evidence, including
  Android, is tracked in the [PWS-0506 matrix](docs/compatibility/pws-0506-portal-matrix.md).
- Opening or refreshing package review inspects saved PDFs in the restricted worker.
  The bounded marker inventory distinguishes detected, not-detected, and unknown states;
  it does not prove the absence of all hidden data or remove metadata, comments, form
  values, attachments, scripts, or actions. Unavailable or unknown checks remain
  unverified. Page geometry and advisory quality analysis are not rerun by this review.
- PDF inspection shows progress and supports **Cancel**. After initial cancellation,
  choose **Export package** to retry. After a cancelled or timed-out refresh, choose
  **Refresh review**; old results cannot authorize export. The development startup
  delay described above can still delay cancellation before the worker starts.
- Fresh reports can block packages previously reviewed with unavailable inspection.
  Annotations and form fields, including signature fields, retain the unsupported
  interactive-content blocker. Acknowledgement does not remove these blockers.
- If fresh inspection detects signature markers or cannot establish their state while
  the saved source says none were detected, package export is blocked. The application
  does not silently change saved labels or bypass unsigned-derivative consent. Use the
  unchanged original externally or import it into a new case for current inspection
  and review. Saved marker/unknown states keep their existing consent requirement even
  if fresh inspection finds no markers.
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

PaperWorkShell targets Windows 11 x64. Available engineering installers and prepared setup
bundles carry their own build-specific evidence. TRACE B8 has standard-user same-build repair,
reuse, and offline ordinary PDF observations. A separate engineering bootstrap and runtime ZIP
exercise authenticated offline update, recovery, and replay refusal using isolated test signing.
Current builds are unsigned and have no publicly trusted publisher signature. Use the identified
local installer for repair or preserve-cases replacement. The live update channel is disabled.
Supported Windows editions/builds and remaining packaged matrix results still require
exact-candidate evidence. An unsigned candidate is eligible for product completion. [Setup evidence](docs/development/pws0814-automated-setup.md),
[update evidence](docs/development/pws0803-authenticated-updates.md), and [release interface](docs/release/RELEASE_INTERFACE.md).

PaperWorkShell is in engineering development. Public supported versions, a maintenance
commitment, and a private vulnerability-reporting route are pending. Internal recipients can ask
the maintainer who supplied their build through their existing private channel. [Security and reporting](SECURITY.md).

On the standard-user TRACE B8 engineering build, the support preview opened and required consent.
Two saves failed in the walkthrough. The investigation reproduced the native picker retaining its
suggested filename in the installed runtime folder after an accessibility edit. The source fix
starts the picker in the user's home folder with a blank filename. Exact unsigned PWS-0807 R1
saved the selected ZIP and refused an occupied name without changing either file. An independent
reopen matched the saved archive to the preview.
If your build reports a save failure, use description-only reporting. [Care and recovery](docs/user/CARE_AND_RECOVERY.md#create-a-support-bundle)
and [TRACE B8 investigation](docs/development/pws0807-diagnostics-investigation.md).

The Milestone 6 source gate covers a narrow permanent-removal workflow for one affected active
PDF page. Availability and claims for installed builds depend on the exact build and its release
evidence. [User guide](docs/user/USER_GUIDE.md) and [Milestone 6 source gate](docs/gates/m6-permanent-redaction-gate.md).

The maintainer accepted the existing PWS-0408 walkthrough on 2026-09-30; see the
[acceptance record](docs/development/pws0408-result.md#maintainer-acceptance).
The [PWS-0409 source-tree v0.1 pilot gate](docs/gates/m4-pilot-gate.md) passed on
2026-09-30 within its recorded scope.
PaperWorkShell runs and versions independently. The current release work includes no app-to-app
ThirdLife integration or compatibility claim.
