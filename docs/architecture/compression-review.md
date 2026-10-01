# Review size reduction

Task: PWS-0505. Policy: [ADR 0013](../adr/0013-compression-review-and-floor.md).

## Desktop workflow

1. Open **Export package**, choose the output organization, and review its findings.
2. Confirm the required warnings and any **Unsigned derivative** consent.
3. Choose **Preview size reduction**, then choose the destination folder.
4. Review the measured current/proposed sizes and configured limits. “Current” means
   the verified safe-optimization baseline, not the sum of imported source sizes.
5. Select an affected page and choose **Show page**. Inspect the original and proposed
   renders, especially small text, stamps, handwriting and colored information.
6. To permit color removal, select **Allow grayscale** and choose **Prepare** again.
   This discards the previous candidate and review acknowledgement.
7. Confirm the displayed changes and legibility risks, then choose **Export**. Review
   the final package summary for actual publication status.

The summary lists every changed output/page and the actual resolution, pixel dimensions,
color mode and JPEG quality. ZIP summaries distinguish PDF member limits from the
archive and total-package limits. A first attempt that already fits needs no image
changes. A target that cannot safely fit disables Export and shows alternatives.

The floor is 200 effective dpi and JPEG quality 80, with no override below it.
These settings do not guarantee readability or portal acceptance. A page preview is
bounded and scaled to fit; inspect the final output with an appropriate reader too.
Color in vector graphics or retained images is not removed by the grayscale choice.

**Cancel** stops processing and waits for cleanup; **Close** leaves review without
publishing. Review has no time limit. All controls support keyboard focus, and the
content scrolls at enlarged text and display scales while Close/Cancel remain available.

## Implementation

- `app.package_workflow` owns exclusive saved-case review, signed-source confirmation,
  invalidation and explicit publication authority.
- `export.compression_review` prepares a private transaction, reports validated facts,
  authenticates preview bindings, and commits or discards the exact retained output.
- `export.target_size_export` applies the bounded search. `package_export` exposes a
  prepared lifetime with authenticated PDF readers, including internal ZIP members.
- `worker.compression_preview` renders one original/proposed pair through the existing
  thumbnail broker. Every operation has a fresh restricted worker lifetime and central
  resource limits; there is no native parser or persistent private-image cache in UI.
- `ui.compression_review` handles accessible presentation and transient acknowledgement.
  The backend independently enforces current evidence and at least one successful pair
  for changed output. Neither layer labels the result as proven legible.

See [target-size search](target-size-export.md) for supported pipelines, constraints
and resource accounting, and [verification](../development/pws0505-result.md).
