# Private full-page raster reconstruction

- Task: PWS-0603
- Decisions: PWS-D009, PWS-D012, PWS-D013, PWS-D022, PWS-D023, PWS-D029
- Status: Internal reconstruction primitive; permanent removal remains disabled
- Security contract: [redaction threat model](../security/redaction-threat-model.md)
- Intent contract: [removal-mark review](redaction-intent-review.md)

## Supported boundary

The restricted worker reconstructs one selected PDF page into a fresh one-page PDF.
This is an intermediate for the later derivative pipeline. It accepts separately
authored removal marks, never ordinary shapes, and binds the exact immutable source
digest and length, source page count/index, case, revision, page and complete mark
records. Crop and quarter-turn are part of the request fingerprint. This binds the
supplied intent; this private intermediate has no current-manifest compare-and-swap
authority. PWS-0606/PWS-0607 must revalidate current intent before final publication. Unsupported
or ambiguous input fails before a candidate can be returned.

The operation has a separate internal launch scope. Normal application workers do
not advertise it. It grants no export destination, original-write access, case
manifest mutation or export-history authority. It never marks intent as applied or
verified. The only candidate status is `awaiting_verification`; an operation that
succeeds has completed reconstruction checks, not the permanent-removal gate.
The ordinary and alternate export blockers from PWS-0602 remain in force.

This seam consumes a PDF page. Packet orchestration, image-input normalization,
visual-overlay composition and selection of unaffected pages belong to the later
derivative/export integration. It does not silently ignore any unsupported edit.
Deskew and arbitrary resampling are outside this request version. No public UI,
CLI, file association or interoperable application API is introduced.

## Pixels and geometry

The fixed reconstruction profile renders at 300 dpi on an opaque white background
using the admitted PDFium adapter and stored annotation appearances. A page that
cannot fit this profile within the actual operation budgets fails; the worker never
silently lowers resolution to make it fit. Source CropBox/MediaBox inheritance,
nonzero origins, UserUnit and intrinsic rotation resolve through the existing
geometry adapter.

Saved marks use full visible CropBox millionths after intrinsic rotation and before
manifest crop or quarter-turn. The worker renders this full page, burns the union
of mark rectangles into opaque black pixels, then crops and rotates sanitized
pixels. Edge quantization includes every touched pixel plus a one-pixel safety
margin on each side, clipped to the full page. No unsanitized image is
encoded into a PDF. Raw intermediate pixels remain worker-private and are deleted
before the operation returns, including on failure.

Output physical size comes from the effective PDF box and UserUnit, intrinsic axis
orientation, manifest crop fractions and final quarter-turn. The new page uses a
zero-origin MediaBox/CropBox and no Rotate entry; its physical appearance incorporates
the rotations. Original PDF box coordinates and object numbers are not preserved.
Crop quantization rounds outward; it can include less than one additional source
pixel per crop edge. Fitting that raster to the exact physical crop size can move
visible details by up to two reconstruction pixels at a crop edge. The retained
pixels themselves remain lossless; no fidelity claim is made below that boundary.
Source vector/text/form/accessibility structures are intentionally lost on the
reconstructed page. Lossless RGB Flate encoding preserves the sanitized samples.
The page builder reopens and verifies its decoded image digest. Rendering the new
page additionally checks that the candidate is renderable.

Object-number differences alone are never proof of removal. The builder starts a
new PDF containing only new image/content/page resources; source PDF objects, hidden
OCR, fonts, optional-content layers, attachments and earlier revisions are not copied
into this intermediate. The whole derivative still requires global sanitization,
OCR exclusion, independent multi-method verification and resave/viewer checks.

## Resources and failure

Limits derive from the central versioned ResourcePolicy and the admitted operation
limits. Processing stays page-at-a-time with one active full-resolution pixel plane;
row-wise masking and disk-backed transforms avoid a packet-sized raster allocation.
Raster dimensions/pixels/bytes, source size/page count, memory, temporary storage,
output size, object count and deadline are enforced. Parent-owned plan/output sections
and private staging count toward temporary storage. One reconstructor permits at most
one outstanding private candidate; it must close before the next reconstruction, and
composition shutdown also closes it. Existing worker cancellation,
termination, AppContainer network denial, minimal environment and runtime admission
remain required.

Source identity is checked before and after processing. New output creation is
exclusive; source paths and previous good candidates cannot be overwritten.
Cancellation, deadline, source changes, malformed geometry, budget exhaustion or
write/verification failure produce stable sanitized errors and discard the candidate.
Reports and representations contain only internal identities, digests, versions,
counts and closed status. Routine progress carries numeric counts; pixels, paths,
filenames, removed text and native exceptions are excluded.

Every reconstructed page is an **unsigned derivative**, including a page whose
source contains digital-signature markers. Marker detection is not cryptographic
signature validation. Originals remain unchanged. Reconstruction does not make a
secure-erasure, OCR-accuracy, PDF/UA, portal-acceptance or release-redaction claim.

## Remaining gate and product boundary

PWS-0604 owns OCR regeneration/exclusion, PWS-0605 the complete derivative sanitizer,
PWS-0606 multi-method verification, PWS-0607 final export authority and reports, and
PWS-0608 independent release approval. This intermediate cannot substitute for them.

The capability serves PaperWorkShell's own document workflow. Private case data,
migrations and final export authority stay PaperWorkShell-owned. It requires no
ThirdLife or sibling repository, service, adapter, account or shared schema.
Draft release-interface facts remain unchanged and unproved.
