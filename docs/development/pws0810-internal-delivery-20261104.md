# PWS-0810 — internal application delivery, 4 November 2026

**Status: done — Internal build ready for use.** The finite installed core
workflows and final preservation/metadata checks passed using R9. R9 was
sufficient: no application repair or R10 build was needed. This is not a
qualified public release.

## Authority and scope

The local branch is `codex/pws-0607-redaction-export` at HEAD `2e70ec0`;
extensive pre-existing uncommitted work was preserved, with no reset, staging,
commit or sibling-repository access. This checkout provenance is separate from
R9's frozen candidate source identity.

The explicit 2026-11-04 reset authorizes the narrow task split: PWS-0810 owns a
usable unsigned installer, normal application use, concrete repairs and a short
handoff. It depends only on completed PWS-0707, PWS-0801, PWS-0804 and PWS-0805.
PWS-0802 retains its original signed public requirements; its public phase is
deferred and does not prevent internal delivery. PWS-0803 is not current work.
The M8 public gate still covers its original prerequisites and now PWS-0810.

Security, privacy, consent, private case storage, worker isolation, immutable
originals and verified derivative export remain required. No schema, dependency,
licensing, case-ownership or standalone-boundary change is intended. ThirdLife
and sibling applications remain unnecessary; there is no compatibility claim.

## App identity and launch

| Item | Current internal candidate |
|---|---|
| Revision | `pws0802-internal-20261104-r9` |
| Installer size | 512,732,779 bytes |
| SHA-256 | `d29f7f6d081c0c43295ba8a3d5bb215344cefa6253a401be34ef0f74941bd9d4` |
| Signature | `NotSigned`; internal use only |
| Installer | `output/pws0802/community-20261103/candidate-r9/installer/PaperWorkShell-pws0802-internal-20261104-r9-win-x64-UNSIGNED.exe` |
| Normal launch | Start menu **PaperWorkShell**, or `%LOCALAPPDATA%\Programs\PaperWorkShell\runtime\PaperWorkShell.exe` |

Root independently verified the size, SHA-256 and unsigned identity before use.
R9 remains preserved; no successor or application change was needed. Existing
Microsoft runtime acceptance and recipient terms remain complete for the recorded
components. Publisher Janne Vuorela, MIT licensing for project-owned code and free
commercial use remain established.

Current host installation returned exit 0 through the quiet installer path after
checking the exact already-accepted Microsoft recipient-terms identity. The
normal installed launcher opened the application with its bundled runtime.
A computer-use launch helper timed out while looking for a window owned by the
native launcher; the visible application window belongs to its desktop descendant.
That helper result is not an application-launch failure. This host observation
does not replace the accepted genuine-standard-account guest evidence.

The read-only installed-inventory receipt matches all **3,195 expected payload
files / 650,130,815 bytes**, with zero missing files, size/hash mismatches or unsafe
paths. It does not enumerate unrelated case data or claim an exhaustive inventory
of installer-generated/uninstaller/runtime-generated extras. The current host is
Windows 11 Pro **26300.9711 AMD64**, running at medium integrity with a filtered
administrator token; it is not a new genuine-standard-account or clean-machine
qualification. Local receipt:
`output/pws0810/internal-use-20261104/evidence/installed-inventory.json`.

## Finite functional check

Use separate synthetic cases where an existing processing path requires them;
preserve edits and report exposed limitations. Accepted evidence is reused only
within the exact artifact, implementation and environment boundary established
by the [R9 inventory](pws0802-x64-qualification-20261104.md) and
[retained runtime/workflow report](pws0802-community-runtime-20261103.md).
Windows 11 Enterprise 26100.9711 x64/genuine standard-account observations remain
scoped observations, not a broader supported-build or universal reader claim.

| Workflow | Current pass and applicable reused evidence |
|---|---|
| Normal launch | Current host install 0 and ordinary launcher/window observed. Reuse R9 native standard-account install/launch and audited complete app-local runtime delivery. |
| Document organization | Current GUI imported all three PDF/JPEG/PNG samples and displayed previews. **Move earlier** changed order to PDF, PNG, JPEG. Rotate 90°, Undo to 0° and Redo to 90° succeeded. The app reported **All organizer changes saved**, revision 7; **Close case** reported **All queued changes saved**. GUI reopen restored revision 7, the page order and rotations; previews subsequently became ready. The exact synthetic case/source receipt verifies three immutable imported copies against the unchanged sample hashes. Retained R9 organization/R6 reopen observations keep their original scope. |
| Ordinary export | Current GUI **Export pages as PDF** created `arranged.pdf` and recorded case export history. Independent strict reopen/render found three pages, no syntax findings and 0/90/0 rotations. Root inspected all three rendered pages: blue PDF, rotated SCAN 206 PNG, then red/blue JPEG, in the requested order. Retained earlier export observations keep their original scope. |
| Scan adjustment and OCR | Current GUI reviewed the PNG's original/adjusted SCAN PAGE 206 preview at 90°, changed contrast 100→110, used **Update preview**, reviewed and **Apply adjustments**, then saved revision 11. English was **Installed and verified** and selected. The actual package export completed its four-page create/verify path; the scan pages render readably and the raster PDF page has searchable extracted text `SCAM PAGE 206`. The N→M recognition error remains a clear OCR review limitation. Retained R9/R6 OCR observations keep their original scope. |
| Visual form filling | Current correct-PDF separate exports passed. Final **Internal trial** text is extractable and clearly placed at left/top 15/20%, 24 pt, with a 40/8% box; the checkmark is visible at 75/75%, 6/6% box. Root visually inspected the clean output. Four named one-page PDFs reopened/rendered without syntax findings; the saved summary reports four files/four pages/50,039 bytes. Both initial and clean outputs remain preserved. |
| Guided packaging | Current GUI assigned PDF/application, JPEG/identity, PNG/address and raster OCR PDF/supporting evidence, using separate **Add** and **Confirm** for each. All four rows became **Ready** and saved. Combined review showed four pages/one `generic-application-packet.pdf`; warnings/human checks were reviewed and acknowledged. Export completed to `Exports/scan-package`; strict reopen/syntax/all-page rendering passed. **Save summary** created `package-summary.txt`, which truthfully reports one saved/reopened PDF, four pages, 48,369 bytes and the actual remaining review items. |
| Recoverable failure | Current GUI refused the existing `arranged.pdf` destination with **Action failed. A file already exists at the selected destination. Choose another name.** (`PDF_EXPORT_DESTINATION_EXISTS`). The prior output hash stayed unchanged. Retrying as `arranged-retry.pdf` succeeded; its bytes/hash match `arranged.pdf`, and independent strict reopen confirms three pages. Prior installation/removal/recovery failures remain evidence only for those operations. |

Imports, previews and worker preparation involved noticeable waits of tens of
seconds. This is a qualitative observation, not an exact benchmark or an exclusive
cause attribution. The initial **Import inspecting: 0 of 1** status preceded a
later worker appearance and successful import. No parser failure was established
and no production repair was justified by that wait alone.

The organization observation is bound to the exact synthetic case by
`output/pws0810/internal-use-20261104/evidence/organization-before-reopen.json`:
manifest revision 7, three sources/pages, saved PDF/PNG/JPEG sequence and 0/90/0
clockwise rotations. Every imported copy matches its recorded and external sample
hash. The receipt reads only the newly created synthetic case; the GUI reopen is
a separate current direct observation.

The current ordinary export is **23,446 bytes**, SHA-256
`7aeefa6dcd1f63b7ae82d06503edf4370612c63c19664ea408dc6f629d6b952f`.
The separate verifier's strict reopen/page-count/rotation/syntax results and
all-page render paths are in
`output/pws0810/internal-use-20261104/Verification/output-inspection.json`.
This is arranged ordinary PDF output, not a searchable-OCR claim.

The current combined scan package is **48,369 bytes**, SHA-256
`2728b93b60f5b172855be29f4cbe1b664551f9a1cd19ad0d441ef77cc94063c0`.
Independent inspection reopens four pages without syntax findings and renders
every page. Root visually inspected the rotated PNG and raster-text PDF output
pages; the latter's extracted `SCAM PAGE 206` demonstrates a text layer while also
showing why OCR must be reviewed. The source reads `SCAN PAGE 206`. The GUI-saved
summary retains geometry/quality/template/privacy review items and human
confirmation wording; it does not claim OCR correctness or portal acceptance.

An initial automation navigation mistake placed the two synthetic marks on the
PNG. They were removed explicitly in that page's visual editor before export;
organizer Undo/Redo separately restored the scan contrast to 110. The intended
PDF was then explicitly selected and marked as described above. This recovery
required no product repair and leaves the earlier scan/package evidence intact.
The exact four-source, scan and overlay intents are retained in
`output/pws0810/internal-use-20261104/evidence/pre-final-intents.json`.

The first successful separate-PDF package is preserved in `Exports/form-package`:
`application-form.pdf` (3,552 bytes), `identity-evidence.pdf` (21,590 bytes),
`address-evidence.pdf` (2,437 bytes) and `supporting-evidence.pdf` (22,460 bytes),
each one page, totaling **50,039 bytes**. Root inspected the correct PDF's text
and mark. Its initial text coordinates crossed the synthetic preprinted border;
this was a sample-placement choice, not an application defect. `Exports/form-clean`
contains the reviewed cleaner text position and its GUI-saved package summary.
The clean `application-form.pdf` is **3,552 bytes**, SHA-256
`6b11aa4cb02d60a3e04e00df8f9573e3388433bb6d1e682d2f58fee679db5695`.
The bound `evidence/clean-export-inspection.json` receipt is SHA-256
`bb5c363719aac6c81abdcd4a6acc0b810781631d239111e2942c1a322502e8ec`.
Across all **11 exported PDFs**, independent strict reopen/syntax checks pass,
18 pages render to unique paths, and the seven prior output hashes remain exact.

Final GUI close reported **Case closed. All queued changes were saved.** The
application is left open at the ordinary case chooser, with the synthetic case
at revision 19. The independent final preservation receipt verifies manifest
SHA-256 `50bef36814e985bcf34497d393a5d4e8f74d8a61662f399746b3f73a5c55eada`;
correct 24 pt/15/20/40/8 text and 75/75/6/6 checkmark; PNG with zero overlays,
contrast 110 and 90° rotation; all four confirmed assignments; and all four
immutable source copies matching the manifest and prepared samples. That exact
case's temp directory is empty: zero files/directories/bytes. The installed-runtime
process filter finds the two ordinary app processes and zero `host_bootstrap`
workers. No other cases or global recovery state were inspected.
Receipt `evidence/final-case-preservation.json` is SHA-256
`2acc1f3e70cf566cbc63203bd510af89e726f8306cbfc1ca83545a8570468c47`.

## First use and local sample packet

The prepared local packet and concise instructions are in
`output/pws0810/internal-use-20261104/START-HERE.txt`, alongside
`Samples/Organization`, `Samples/OCR`, `Samples/Form` and `Samples/Package` under
`output/pws0810/internal-use-20261104`. These are local ignored handoff artifacts,
not publicly published installer or source packages.

Open PaperWorkShell normally, create a case and use **Import** to add supported
PDF, JPEG or PNG files. Review the previews, make the needed page changes and
export to a new destination. Review the resulting PDF and any package summary
before using the files. The prepared optional owner trial is ordinary use,
estimated at **20–30 purposeful actions and 5–8 active minutes**, with variable
processing waits and no time limit. That estimate includes selecting the three
files, reopening the case and opening the exported PDF. No result form, repeated
qualification, development environment, hash transcription, VM or private-document
upload is required; feedback does not gate the internal handoff.

Practical workflow details and observed limitations:

- **Export pages as PDF** is the checked ordinary organization/rotation path.
  Use **Review and export package** for the checked scan/OCR and visual-overlay
  paths. In the demonstrated packet mixing PDF overlays and image/scan
  adjustments, choose **Several separate PDFs**; enable that choice in package
  constraints if needed. The case retains the original edits and combined-output
  choice rather than discarding either kind of work.
- The generic checklist needs all four categories separately assigned and
  confirmed. **Save summary** is an explicit action after package export.
- Searchable OCR was verified in the combined scan package, where the actual
  extracted phrase is `SCAM PAGE 206`. The observed separate form package's raster
  supporting-evidence PDF remained image-only. Only the form's added text was
  extractable from that four-file package; do not assume every group acquired OCR.
  Review recognition and the chosen output.
- Processing and previews can involve waits of tens of seconds. These are
  qualitative current observations, not a fixed performance promise.

## Deferred public phase and correspondence

Trusted public signing, SignPath eligibility/application/onboarding, public
installer publication, remaining release-only environmental qualification and
final checks for the eventual signed release are pending for later maintainer
resumption. PWS-0802 and the public M8 gate are not marked complete.

The real system-volume disk-full test is **not performed and deferred to later
release qualification**. No VM resource request is pending. Do not provision or
purchase one, repartition, or fill a real working volume. Existing low-space and
injected-failure evidence proves only the limited behavior actually observed.

Existing SignPath correspondence and submissions remain historical and preserved.
Earlier contact permission is exhausted. No new inquiry, application, follow-up,
withdrawal, enrollment, upload, purchase or agreement is authorized. Later contact
requires the maintainer's explicit approval of recipient, exact message,
attachments and action.

## Task result — PWS-0810: usable internal Windows application

**Status: done.** The finite core workflows, clean overlay derivative,
separate-output summary, final GUI case close, preservation and canonical
metadata/bundle checks passed. Internal build ready for use.

### Implemented

- Prepared the existing R9 installer and normal launch path for internal use.
- Ran the finite installed GUI checks and kept new observations separate from
  applicable accepted evidence.
- Added the small runnable internal task and local sample/first-use instructions.
- No application code repair, dependency change or R10 build has been needed.

### Changed files

- `TASKS.yaml`: runnable PWS-0810, public-phase deferral/current no-action note and
  retained M8 gate coverage.
- `ROADMAP.md`, `CODEX_START_PROMPT.md`: current internal-delivery sequence.
- `README.md`: prominent current internal-use record link.
- `docs/development/pws0810-internal-delivery-20261104.md`: this scoped result and
  handoff record.
- `docs/development/pws0802-x64-continuation-20261104.md` and
  `docs/development/pws0802-x64-qualification-20261104.md`: short superseding notes;
  historical observations and plans retained.
- `BUNDLE_MANIFEST.json`: current document identities, new result record, task
  counts and a scoped internal-delivery note; historical notes retained.

### Verification

- `.venv\Scripts\python.exe tools/dev.py validate-task-graph` — pass; 83 tasks,
  248 dependencies, nine milestones, 43 decisions.
- `.venv\Scripts\python.exe tools/dev.py validate-schemas` — pass; task, manifest,
  template, fixture, RPC, dependency, resource-report and export-history contracts.
- `.venv\Scripts\python.exe tools/dev.py validate-docs` — pass; local links,
  references and security traceability.
- `.venv\Scripts\python.exe tools/dev.py validate-portfolio-boundary` — pass;
  83 tasks, 248 dependencies and 116 canonical files, including matching bundle
  identities and release-document stage.
- Scoped `git diff --check` — pass; an informational CRLF warning is not a failure.
- Exact R9 installer/installed-payload identity, current GUI workflows and separate
  synthetic PDF reopen/syntax/render/content checks are recorded above.
- Final exact synthetic-case preservation — pass; revision 19, four unchanged
  immutable sources, intended edits/assignments, empty case temp and zero owned
  worker processes after ordinary close.

### Security/privacy/accessibility notes

The pass uses synthetic material only. Four imported source hashes remain exact;
existing output collision handling preserves the prior PDF and permits a new-name
retry. Consent, private case storage, worker isolation, bounded processing and
export verification stay intact. Earlier accepted standard-account and bounded
accessibility observations retain their scope; this host pass adds no universal
accessibility, clean-machine or genuine-standard-account claim.

### Portfolio boundary and release-interface notes

PaperWorkShell remains independently useful, locally installed and usable with
ThirdLife absent. No sibling repository/service/data dependency, adapter, schema,
migration or new integration surface was introduced. Cases and content remain
PaperWorkShell-owned under user control. This internal record grants no signed
public-release or portfolio compatibility claim; the public envelope stays draft.

### Residual risks or deferrals

OCR can misrecognize visible text, processing waits vary, and package summaries
retain the actual review warnings. Signing/publication and remaining release-only
qualification, including the unperformed real system-volume disk-full test, remain
deferred as specified above. No unresolved concrete application defect was found
that required a code change during this finite internal-use pass.

### Task graph

PWS-0810 is `done`, completed 2026-11-04. PWS-0802 retains its blocked original
public-signing outcome; that public phase is deferred. PWS-0804 and PWS-0805
remain done. No updater work was started.
Next dependency-ready unfinished task: **none**. Stop this internal-delivery
phase for optional ordinary-use feedback; no updater or public qualification work
is authorized by this handoff.
