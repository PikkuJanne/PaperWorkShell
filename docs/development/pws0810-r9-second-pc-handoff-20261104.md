# PWS-0810 — R9 second-PC installation and test handoff

This is a handoff-only follow-up to completed PWS-0810, requested on 4 November
2026. The transfer folder is `output/PaperWorkShell-R9-Second-PC`; its
`START-HERE.txt` is the receiving-PC entry point. R9 is preserved. No application,
installer, dependency, schema, security policy or accepted test result is changed.
The extensive pre-existing uncommitted checkout remains intact.

## Transfer contents and identity

- Original filename:
  `PaperWorkShell-pws0802-internal-20261104-r9-win-x64-UNSIGNED.exe`.
- Candidate: `pws0802-internal-20261104-r9`; 512,732,779 bytes;
  SHA-256 `d29f7f6d081c0c43295ba8a3d5bb215344cefa6253a401be34ef0f74941bd9d4`.
- Both original and copied installer were checked; exact size/hash agree and the
  copied artifact reports `NotSigned`. No manual hashing is assigned to the user.
- Seven unchanged synthetic inputs are copied from the accepted internal-use
  packet, with short names: `01-page.pdf`, `02-photo.jpg`, `03-scan.png`,
  `ocr-scan.pdf`, `form.pdf`, `address.pdf`, `support.pdf`.
- Empty `Exports`, `Exports/OCR` and `Exports/Overlay` folders are prepared.
  No development case, diagnostic file, personal document or local test output
  is included in the transfer.

The local receipt is
`output/pws0810/r9-second-pc-preparation-20261104/transfer-verification.json`.
It binds source/copy hashes, sample provenance, strict PDF checks, image-only OCR
proof and seven installed-interface files to the frozen R9 payload. All five
single-page PDFs reopened without syntax findings and were rendered with Poppler
at a bounded 900 pixels; every rendering and both image samples were inspected.
Poppler emitted local display-font configuration warnings, but produced all five
complete, legible images. The OCR source uses only `q`, `cm`, `Do`, `Q`, image
XObjects and no font/text content. The query is **PAGE 206**; the earlier R9 OCR
misreading of SCAN as SCAM is explicitly retained as a review limitation.

## Interface checks and reused evidence

The installed native launcher was opened normally. Current GUI inspection covers
the case chooser, creating a disposable case, complete shutdown/relaunch and
reopening that case, import dialog, importing the supplied form, preview readiness,
checklist category/Add/Confirm/Apply labels, text-overlay creation/property labels
and OCR-language dialog. Unsaved checklist/overlay inspection was cancelled.
English was checked and both installed language statuses were verified locally;
the guide explicitly selects English only. Opening package review without the
four assignments correctly produced the actionable checklist/constraints refusal,
reinforcing why the additional tests include their full assignment setup.

The exact original R9 installer was inspected interactively through **License
Agreement**, **Information** and **Ready to Install**, then cancelled without
reinstalling. The actual visible navigation button is **Next**, followed by
**Install**. There is no destination/components/tasks/launch-checkbox choice.
The completion **Finish** label and fixed per-user path are also bound to the
frozen installer source and accepted installation evidence. A concrete failure
path was observed: setup while the app is running reports `PWS_INSTALL_BUSY`;
after normal complete app shutdown, retry reaches the wizard. The guide includes
this recovery. No application defect or successor build was indicated.

The exact R9 installer-source archive, frozen UI/package code and
[accepted installed workflow report](pws0810-internal-delivery-20261104.md)
provide the remaining ordinary export, reorder/rotate/undo/redo, saved-case,
OCR/overlay package and output checks. These are reused observations with their
original host/account/artifact scope, not newly repeated results. The guide uses
the previously verified mixed PNG plus image-only PDF searchable package route.
The overlay case contains only clean PDFs. Both specialist tests include the
four required category assignments and the separate package review acknowledgement.
Ordinary **Export pages as PDF** is used only for the basic organization test.

R9 implements no persistent startup-error log. The guide names the actual
`%LOCALAPPDATA%\Programs\PaperWorkShell\runtime\paperworkshell-installed-v1.json`
as installation identity only and asks for launcher error text/screenshot. It
does not invent a log directory or require routine diagnostic bundles.

## Human journey and burden

The guide begins with transfer, normal-user installation, Start-menu launch and
the first successful screen. Receiving paths are ordinary local Downloads,
Samples and Exports folders; the receiving user needs no checkout, Python or
development tools. Fixed paths and the OCR query can be selected/copied from the
text; ordinary File Explorer and native dialogs provide folder opening. No script,
clipboard reader, evidence recorder, numeric environment prompt, hash calculation,
private upload or log submission is required.

The basic trial has eight numbered steps and one ordinary PDF viewer. Estimated
burden after installation is 45–65 purposeful selections/entries, 5–7 minutes of
active work and approximately ten minutes including processing; slower processing
may extend this with no timeout. It includes all three imports, one move, one
rotation, undo/redo, automatic saving, complete close/relaunch/reopen, a new export
and one review of each of three pages. Expected final order is blue-panel PDF,
clockwise-sideways SCAN PAGE 206 PNG, then unchanged red-rectangle/blue-circle JPEG.

Transfer/install/launch add roughly 15–25 selections and 3–5 active minutes plus
copy/extraction time. OCR and overlay are independent optional follow-ons, each
with a separate case, four imports and four explicit category confirmations.
Allow about 5–8 active minutes each plus processing, roughly 55–75 actions for OCR
and 65–85 for overlay including property entry, scrolling and viewer review.
Repeating the trials is unnecessary for a first report. Existing cases/exports
retain progress; reports need only the failed step, observed difference and exact
message/screenshot. Existing-output refusal recovers with a new name/folder.
One failed attempt can add about 4–8 actions and 1–2 active minutes; continuing is
optional and no earlier valid observation needs repeating. These estimates are
burden estimates, not speed requirements or second-PC outcomes.

## Verification and boundaries

The source/copy SHA-256, sample identity and image-only checks passed. The following
repository commands passed after the guide and task evidence were prepared:

- `.venv\Scripts\python.exe tools/dev.py validate-task-graph`: 83 tasks,
  248 dependencies, nine milestones and 43 decisions.
- `.venv\Scripts\python.exe tools/dev.py validate-schemas`: all registered contracts.
- `.venv\Scripts\python.exe tools/dev.py validate-docs`: local links, references
  and security traceability.
- `.venv\Scripts\python.exe tools/dev.py validate-portfolio-boundary`: 117 bundled
  canonical files and the unchanged standalone boundary.

The final guide digest and copy inventory are stored with the local preparation
receipt. There is no product-code change requiring a repeated
full processing/security/accessibility suite; accepted PWS-0810 evidence is
preserved. This work creates no platform qualification, public release,
ThirdLife compatibility claim, sibling dependency or content-ownership change.
Release-interface facts and status are unchanged.

The app and setup preview both closed completely. The sole newly created local
synthetic case `564f0ecd-b884-4df1-9bc1-442b05a0a19c` remains at revision 1 with one
unchanged `form.pdf` source and no saved overlays. Automatic approval review
rejected the attempted exact-case cleanup with `blocked by policy`, before the
command ran. No alternative deletion was attempted. This retained case is outside
the transfer folder; no prior case or global recovery state was changed manually.

The second Windows 11 x64 PC has **not been tested** by the preparer. Its installer,
launch and workflow results await the owner's trial. Signing, public publication,
provider contact and unavailable-VM/system-volume disk-full qualification remain
deferred. PWS-0810 stays done; no dependency-ready unfinished task is opened by
this handoff.
