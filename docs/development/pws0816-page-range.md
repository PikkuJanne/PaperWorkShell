# PWS-0816 — verified page-range PDF export

The roadmap includes splitting assembled pages. The installed PWS-0815
rehearsal showed that ordinary Export PDF offered whole-packet output.
The new option uses first and last positions in the saved assembled order.
The workbench binds the range to a case, revision, and ordered page identities
before asking for a destination. The export service validates that selection
again against the saved packet. [PWS-0815 evidence](pws0815-trace-page-navigation.md)

## Verification plan

Use a synthetic reordered case and select an interior range with a saved
rotation. Verify actual page content, order, orientation, and count in a
fresh derivative. Compare source and managed-original digests, case order,
and the prior destination before and after. Exercise invalid and stale
selection, cancellation, unsupported intent, and destination collision.
Use the normal offline installed application, prepared Setup and a new
candidate. Preserve the prior PWS-0815 artifact and guest checkpoints.

## Source verification

Ordinary, flattened, and searchable export use their existing bounded
selected-page planners. The range continues to enforce case-wide removal
admission, signed-source acknowledgement, no-replace publication, and
verification before success. The saved export history records the actual
derivative. In the current session TRACE identifies the range and maps its
page IDs to their positions in the exported file.

The Windows constrained backend integration passed. An independent PDF
reopen and render checked only the two selected pages of a reordered
packet, including the saved quarter-turn. The test also compared external
and managed originals and exercised invalid ranges, a stale range, early
cancellation, and an occupied destination. The ordinary, flattened, and
searchable service tests passed, including applicable unsupported processing
combinations. The native accessibility suite passed 372 tests. It includes
keyboard range review, refusal of invalid positions before the destination
picker, and cancellation of the range dialog.

The preceding hosted core run failed when the checked-in scan-quality fixture
was compared against a newer implementation index. The integration lane passed.
The existing evaluator recorded and validated a separate receipt on the frozen
PWS-0816 source. The corpus, profile, and metrics match the retained TRACE
receipt. The unit suite selects the new receipt and continues to check the
exact implementation index and working bytes.
[Scan-quality evidence procedure](scan-quality-evaluation.md#candidate-receipts-without-replacing-accepted-evidence)

## Installed range and label observation

`pws0816-page-range-engineering-r2` from
`1ccc80bd8f9788da592fcb02c8aeb8463a74aeda` installed via prepared Setup in
the offline standard-user guest. Setup verified 3,219 payload files and
preserved 359 existing case files and 143 external rehearsal files. The
guest stayed on the same active checkpoint with all 23 checkpoints.
The installer SHA-256 is
`99b5bc4869eb55b0772de8466f4ef6193354ff5aabaaf9c9d679d363a4683cb9`.

The installed app reopened one four-page synthetic case after a saved reorder
and rotation. Pages 2–3 produced a two-page PDF with rotations 0 and 90 degrees.
Independent pikepdf reopening and PDFium render comparison matched the two
selected originals in assembled order. The manifest changed only in export
history, revision and update time. All three source and managed-original
hashes matched. Invalid positions, Cancel, the occupied filename, and the
unsupported searchable export with a visible mark preserved existing data.
The unsupported message described recovery using compatible ranges. See
`output/pws0816/installed-range-proof/b1c6af4f839f`.

The application and UI Automation exposed both input names. The screenshot
showed that the paired visual labels had empty bounds. The focused regression
reproduced this after turning range mode on. Explicit labels in the vertical
dialog layout restored their visible size and preserved keyboard focus and
label relationships.

## Final engineering candidate

`pws0816-page-range-engineering-r3` was built from
`1e21c225c71a8776f458aba634bd85f7bec154b0`. The private unsigned installer
is 513,030,240 bytes, SHA-256
`ee240e88d473c6a438a33fe84e63684b2af4ff1855762f5fa39cbf60c0b485fb`.
The prepared Setup handoff SHA-256 is
`5ccfdb740940c0c416ec3f9a1add4b8c585c5474ad89c8549bd95833ea5b6d1e`.
Its bundle is under
`output/pws0816/build-preparation/pws0816-page-range-engineering-r3`.
The build bound 345 application files to Git and verified 3,219 payload
members. The R2-to-R3 application diff is limited to `ui/window.py`, so the
record keeps the earlier export and refusal observations with R2.

The final source passed 372 accessibility tests, 21 focused dialog tests,
and the registered formatting, lint, typing, and repository validators.
The exact R3 source also supplied a separate passing scan-quality receipt.
Its strict implementation binding supports the current unit-suite default.

Prepared Setup replaced exact R2 under the standard desktop account and
verified 3,219 installed files. All 372 existing case files and 143 external
rehearsal files matched across replacement. All 23 checkpoints were retained.
The receipt is `output/pws0816/installed-successor-r3/receipt.json`.

The final installed dialog showed First page and Last page above their fields.
Native bounds placed both labels and fields inside the viewport, and Tab
reached Last page. The same retained case exported only assembled page 3.
Independent reopening found one page at 90 degrees. Its rendered pixels
matched both the rotated original and page 2 of the earlier verified range.
The manifest changed only in export history, revision, and update time.
The case closed normally. Evidence and the independent PDF check are under
`output/pws0816/installed-range-proof/r3-5195d2e8f4c9`.

The first observer stopped on a transient accessibility query after creating
the rehearsal case. A subsequent observation saved the reorder and rotation,
then encountered an exclusive manifest read. The completed R2 run resumed
from that saved case and used those actual edits. Tool policy blocked a
cleanup command for the first observer's synthetic scratch folder. The folder
remains in the test guest and the transferred evidence is retained.

## Next development step

PWS-0817 covers the one-file packet with a marked digital form and separate
searchable scans. The installed PWS-0816 check confirmed that an OCR request
with a visible mark in scope is refused. Begin with a synthetic marked
born-digital form and a distinct scan. Trace the existing flattened form
and searchable scan plans into one verified ordered derivative.
