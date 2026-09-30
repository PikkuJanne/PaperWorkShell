# PWS-0408 human pilot

Status: **accepted by the maintainer on 2026-09-30**. The existing walkthrough was
accepted with “PWS-0408 reviewed, and accepted. Please continue.” See the
[acceptance record](pws0408-result.md#maintainer-acceptance). This protocol remains
available for later focused retests; the accepted walkthrough need not be repeated.
The separate [PWS-0409 source-tree v0.1 pilot gate](../gates/m4-pilot-gate.md)
passed on 2026-09-30 within its recorded scope.

## Stable entry point

From the repository root:

```powershell
uv run python -m tools.dev pws0408-pilot
```

This developer-only launcher prepares a fresh, isolated synthetic case profile and
large four-page input under ignored `output/pws0408-pilot/`, prints a test card, and
opens the ordinary desktop UI with the constrained software resource policy. It never
opens or changes the normal user case profile. Follow the printed source/export paths
and recovery command; keep the card so an interrupted attempt can be resumed.
No existing attempt or exported file is overwritten or automatically deleted.

## Walkthrough

Use a first-time operator. For keyboard evidence, do not use a mouse, drag-and-drop,
or another PDF application. Use Tab/Shift+Tab, arrows, Space, and Enter; no timed review.
All four pages are synthetic and clearly labelled with their intended categories.

1. Create a case and import the prepared PDF using the ordinary UI. Confirm that four
   pages are present. Review their large headings in PaperWorkShell.
2. Open **Review package constraints…** with Tab/Space. Activate **Restore generic template defaults**,
   inspect the example limits and filename, and apply them. These are example test
   limits, not real portal rules.
3. Press Ctrl+Shift+C for **Review checklist assignments**. For each of the first three
   categories, select the corresponding page in **Pages without a confirmed assignment**,
   activate **Add selected pages**, then **Confirm added document**. Apply the changes.
4. Press Ctrl+E. Confirm that supporting evidence is missing, the unassigned page is
   visible in the summary, and export cannot proceed. Close the review; assign and
   confirm the fourth page, then apply the checklist changes.
5. Press Ctrl+E again. Read the proposed combined filename, page/category counts,
   unknown sizes, and all warnings and required statements. Explain which checks were
   not performed. Acknowledging review must not turn unverified facts into passes.
6. Focus **I reviewed the listed warnings and human checks** and press Space to select
   the checkbox only after completing the synthetic review.
   Activate **Export package**, choose the prepared destination, and wait for the final
   result. Check the actual file count, exact name, byte size, four pages, category
   inventory, performed checks, and remaining human review.
7. Use **Save summary** to save a `.txt` companion. Check that this action does not add a
   PDF or ZIP member or alter the displayed upload count. Wait for **The displayed summary
   was saved. Review it before sharing.** before closing review and reopening it,
   then retry exporting to the same destination: the existing file must not be replaced.
   The final result must not report that retry as a successful new export.
8. Close and reopen the case. Confirm that the four source pages and assignments remain.
   The unsaved final package result is intentionally not restart-persistent.

Separate-PDF and ZIP modes are additionally covered by automated real-worker pilot tests.
For an optional human mode change, review the mode-specific requested filenames in
Constraints before exporting. Do not silently clear or replace intentional saved names.

## Record only the evidence needed

Report the tested Git revision, Windows build, whether the operator was new to the
workflow, and whether the attempt was keyboard-only. For steps 1–8 report pass, fail,
or not run, plus the step number and a short content-free defect description if needed.
State whether the operator could explain planned versus saved files, unknown checks,
remaining review, and the lack of portal/signature/redaction guarantees.

No applicant data, personal names, paths, screenshots of other apps, or actual identity
documents are needed. Do not infer a pass from lack of an error report. NVDA and the
broader manual signed-input, malformed-input, cancellation, and recovery matrix remain
part of PWS-0409's gate; earlier accepted evidence is not repeated just for ergonomics.

## Acceptance consequence

The recorded maintainer acceptance closes PWS-0408's remaining walkthrough acceptance
and permits work on PWS-0409. A later usability defect needs correction and a focused
retest of the affected step. PWS-0409 remains the separate integrated v0.1 pilot gate.
