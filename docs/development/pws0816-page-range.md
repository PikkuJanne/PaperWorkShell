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
