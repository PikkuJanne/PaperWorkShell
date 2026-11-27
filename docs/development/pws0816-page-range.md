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
label relationships. The final installed check will verify this change on
the retained case, then reopen and render a fresh single-page range.
