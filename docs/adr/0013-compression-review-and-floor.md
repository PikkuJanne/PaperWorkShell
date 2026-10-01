# ADR 0013: Review exact compression candidates before publication

- Status: Implemented; verification in progress for PWS-0505
- Date: 2026-10-01
- Decisions preserved: PWS-D024, PWS-D026, PWS-D030, PWS-D037, PWS-D041, PWS-D042
- Builds on: [ADR 0011](0011-staged-pdf-image-reencoding.md) and
  [ADR 0012](0012-target-size-search.md)

## Decision

Expose target-size preparation through the existing package review. Keep one verified
candidate in a private destination transaction while the user inspects actual sizes,
affected pages, and before/proposed page renders. Export publishes those exact bytes;
it does not repeat compression after approval. Closing or replacing the review removes
the private candidate. Destination collisions retain the existing no-replace behavior.

The default policy preserves color. An explicit **Allow grayscale** choice selects a
version 2 schedule: safe optimization, then grayscale 300 dpi/quality 90, 240 dpi/quality
85, and 200 dpi/quality 80. It still has at most four attempts. Version 1 retains its
original color-preserving meaning. Color changes affect only eligible, selected image
assets; retained images, PDF vector graphics and text keep their original color.
Additive named image-stage profiles carry the choice through the existing validated
worker request. No released field changes meaning.

## Floor and approval boundaries

The fixed automatic floor is 200 effective dpi on both image axes and JPEG quality 80.
These are the existing supported engineering bounds, not a measured human-readability
threshold. Images already below the resolution floor stay unchanged; they are not
upscaled to manufacture compliance. There is no lower-quality override in this task.
Changing that boundary requires a separate policy decision and evidence.

The user must review the listed changes and explicitly acknowledge legibility risks.
At least one exact before/proposed pair must load successfully when images change.
Every affected page can be selected, but the software does not claim that every page
was inspected. Small type, stamps, handwriting, and color-dependent meaning still need
human judgment. An unsafe target offers recipient-permitted splitting, a larger limit,
or better source material; it never silently weakens the floor or saved constraints.

Signed-source consent is saved before preparation. The candidate binds the resulting
manifest revision, digest and CAS token, plan, policy, destination and output hashes.
A changed case, mode, destination or color policy invalidates approval. Review itself
has no timeout. Preparation, each preview request and publication have separate bounded
processing deadlines and cancellation. Workers terminate before control returns for
human review.

## Preview and resource boundary

Render the authenticated original page and the retained proposed PDF using the existing
restricted thumbnail worker. For supported ordinary PDFs, the original page plus saved
rotation represents the uncompressed appearance. Proposed pages come from the retained
PDF, including ZIP members. Parent code grants reduced read handles and validates
identity, dimensions, rotations and hashes before pixels reach widgets.

Only one pair is retained, with no persistent candidate thumbnail cache. The central
ResourcePolicy caps pixels, image copies and staging; retained package bytes reduce
the next worker's temporary allowance. Organizer previews are released while the
comparison is open. Rendering and preparation share the existing single heavy-operation
gate. No unbounded queue, whole-document rasterization or native GUI parser is added.

Compression support remains the ordinary PDF subset in ADR 0012. Cropped, overlaid,
photo/scan and OCR groups keep their established verified output path. The review
reports unsupported reduction honestly rather than discarding document intent.

## Effects

This adds no dependency, license change, case migration, shared service or sibling
interface. It is useful in standalone PaperWorkShell. Packaged-release facts and
ThirdLife compatibility claims are unchanged. See the
[workflow guide](../architecture/compression-review.md) and
[task evidence](../development/pws0505-result.md).
