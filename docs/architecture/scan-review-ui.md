# Before-and-after scan review UI

- Status: PWS-0203 implementation and evidence contract
- Task: PWS-0203
- Decisions: PWS-D026, PWS-D030, PWS-D037
- Transform boundary: [Non-destructive scan-transform pipeline](image-transform-pipeline.md)
- Organizer boundary: [Page organizer UI](page-organizer-ui.md)
- Trust flow: [DF-PREVIEW](../security/data-flows.md#df-preview--render-bounded-thumbnailspreviews)

## Scope and claims

PWS-0203 lets a person compare an immutable-source preview with one tentative crop, clockwise
quarter-turn, fine deskew, contrast, and background-cleanup preview before any of those tentative
values become manifest intent. It is a review and confirmation surface, not an automatic quality
decision. The UI says that a suggestion **Needs review**, that the adjusted image is a preview, and
that nothing has been applied until the user chooses **Apply adjustments**.

The feature does not infer legibility, OCR accuracy, portal acceptance, document validity, or that
one preview is objectively better. Background cleanup is tonal processing; it does not remove
private information. The feature accepts only current JPEG and PNG image pages. A PDF page is not
shown with unsupported raster-only intent as though the operation had succeeded.

## Dependency and trust boundaries

`paperworkshell.app.scan_review.ScanReviewDraft` is the toolkit-free tentative-state contract. It
binds one internal case ID, page ID, current manifest revision, authoritative base intent,
candidate intent, suggestion-presence flag, and review version. It is frozen, returns a new draft
from `with_candidate`, and deliberately hides IDs, intent values, and digests from its
representation. `reset_to_original` selects the neutral source-relative intent and clears the
suggestion flag; it does not write a command.

The two preview variants are closed:

- `ORIGINAL` is an `ImagePagePreviewResult` with no user quarter-turn. It applies only the source's
  intrinsic EXIF interpretation and bounded normalization.
- `ADJUSTED` is an `ImagePageTransformResult` whose complete canonical intent exactly equals the
  draft candidate.

`ScanReviewPreviewResult` validates that distinction before pixels reach Qt and exposes only the
variant and contract version in its representation. Neither result carries a source path or
display name. The GUI converts only the already validated fixed-layout opaque RGBA8 payload to a
detached Qt image/pixmap; it does not open an encoded source or import an imaging adapter.

The organizer owns exactly one active review. It binds the draft to the exact originating
`PageCommandHistory` identity and remembers only a successful adjusted-preview intent as the
current Apply eligibility token. Beginning a review fails closed for a non-image page, a pending
autosave, an inactive page, or inconsistent manifest state.

## Draft, preview, and Apply lifecycle

The authoritative state and the tentative state stay distinct:

1. Begin review from the current validated image-page projection and manifest revision.
2. Request the neutral original preview and the current candidate preview through the existing
   serial page-preview service. Source authentication still occurs before every worker launch.
3. Changing a control creates a new immutable draft. The displayed adjusted result becomes out of
   date immediately, and Apply is unavailable until the exact new candidate has completed.
4. Reset selects the neutral source-relative candidate and clears a suggestion. Cancel discards the
   active draft. Neither operation queues autosave or changes manifest/history state.
5. Apply revalidates the case, page, revision, base intent, originating history, and successful
   adjusted-preview token. For changed intent, it constructs all changed page commands in local
   immutable history and queues one final autosave candidate only after the complete construction
   succeeds. If candidate and base intent are equal, Apply completes the review with unchanged
   history and no autosave candidate.
6. A validation error, stale state, cancellation, preview failure, or apply failure leaves the last
   authoritative history and manifest unchanged. A later success from an older operation or
   generation cannot enable Apply or replace current pixels.

Apply records ordinary PWS-0202 manifest operations; it does not add a schema field or modify an
existing schema meaning. Autosave, undo, redo, restart reconstruction, and immutable-source checks
remain the existing organizer contracts.

## Presentation states and interactions

`ScanReviewDialog` is resizable and contains a scrollable review body so enlarged text does not
hide critical controls. The original and adjusted surfaces have visible labels plus textual
loading, ready, out-of-date, cancelled, failed, or unavailable state. No meaning depends on color,
hover, animation, or the preview pixels themselves.

The controls expose the transform contract's exact supported ranges through keyboard-native
widgets:

| Control | Domain range | Neutral value |
|---|---:|---:|
| Crop origin and size | normalized integer millionths, positive area within the page | full page |
| Clockwise rotation | 0, 90, 180, or 270 degrees | 0 degrees |
| Fine deskew | -15,000 through +15,000 millidegrees | 0 |
| Contrast | 0 through 4,000,000 millionths | 1,000,000 |
| Background cleanup | 0 through 1,000,000 millionths | 0 |

Every pointer-adjustable control accepts keyboard focus and keys. Visible actions provide keyboard
paths for updating or comparing previews, resetting, cancelling active preview work, rejecting the
dialog, and applying the reviewed candidate. Escape requests cancellation while preview work is
active and otherwise rejects the review. Apply is never enabled merely because controls contain
valid numbers; both the original and exact current adjusted result must be ready.

Progress contains one operation label, a closed display phase, numeric completed/total counts, and
whether cancellation remains available; an unknown phase is rendered only as `working`.
Announcements cover start, bounded progress checkpoints, cancellation, failure, retry readiness,
and completion without duplicating document content. Cancellation returns focus to Update preview;
failure focuses the safe error message so its recovery text is read. Hiding the dynamic Cancel
preview control first moves focus to a visible safe target.

## Bounded resources and scheduling

Scan review reuses the selected version-1 `ResourcePolicy`; it does not add a replacement cache,
executor, subprocess, or numeric resource profile. The controller supplies bounded preview
dimensions, document work remains serialized by the existing preview path, and the worker
continues to materialize one page at a time.

The window controller owns at most one active review-preview operation and one latest pending
draft/variant. A new request replaces that pending value and cancels superseded active work rather
than extending a queue. The dialog also disables adjustment and Update controls while work is
active. The first cycle requests Original and then Adjusted; later idle Update requests reuse the
retained Original and request only the current Adjusted candidate.

The dialog retains at most one detached Original and one detached Adjusted `QPixmap`. The window
charges both decoded byte counts against the same `thumbnail_preview_cache_bytes` ceiling as the
organizer pixmap LRU and evicts organizer previews down to the remaining allowance before accepting
review pixels. Once Original is ready, later Adjusted loading, editing, reset, failure, or
cancellation clears only the disposable Adjusted pixmap and retains Original for comparison or
retry. An Original failure or cancellation instead marks Original unavailable and Adjusted not
produced. Closing the dialog immediately clears both pixmap references, resets both review-byte
charges, and rebalances the organizer LRU. Older operation generations remain ineligible to update
the current dialog or enable Apply.

## Privacy and concrete wording

Routine models, representations, progress, announcements, errors, and logs exclude source paths,
original filenames, metadata values, source/pixel/cache/intent digests, pixels, OCR/document text,
and native exception values. Preview widgets receive no content-derived accessible description;
their metadata describes only the role and current presentation state.

Examples of truthful messages are:

- “Suggested adjustments — needs review. Nothing has been applied.”
- “Adjusted preview out of date; choose Update preview.”
- “Preview cancelled. Adjustments were not applied.”
- “Scan adjustments applied as case intent.”

Messages do not say “Check complete,” “document improved,” “legible,” or “portal ready.”

## Verification and unchanged golden policy

Unit and application tests prove defensive draft construction, neutral reset, suggestion
confirmation, variant/result binding, exact revision/history checks, atomic changed-intent Apply,
truthful no-change completion, and manifest non-mutation on preview/reset/cancel/failure. UI and
accessibility tests cover every keyboard adjustment, comparison mode, direct focus recovery,
progress, cancellation, retry, stale-result suppression, enlarged-text reachability,
one-active/one-latest-pending scheduling, shared byte-budget accounting, and two-pixmap retention.
Complete forward and reverse keyboard traversal remains required in the recorded Windows smoke.
Privacy tests scan
typed models, dialog text/accessibility metadata, errors, tracebacks, and routine logs with distinct
canaries.

PWS-0203 changes presentation and confirmation, not the PWS-0202 pixel engine, pipeline order,
Pillow version, output layout, or golden approval. The approved `image-transforms-v1` index remains
byte-for-byte unchanged with SHA-256
`c24e01bed73f2d770299c5103d14cd3027aaab7d9b1dfc739caeba8d18970d05`.
The UI review uses only repository-synthetic images, including
`tests/fixtures/synthetic/v1/image/render-calibration-crop-rotate.png`; it does not regenerate or
promote a golden.

The executable keyboard and effective-200-percent real-Windows scaling procedure, with unfilled result
fields until the run occurs, is recorded in
[the PWS-0203 scan-review smoke record](../accessibility/pws-0203-scan-review-smoke.md).
