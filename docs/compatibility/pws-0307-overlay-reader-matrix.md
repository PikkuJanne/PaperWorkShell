# PWS-0307 flattened-overlay reader matrix

## Purpose and evidence boundary

This procedure records manual reader evidence for the deterministic PWS-0307 synthetic flattened
PDF. It covers Adobe Acrobat Reader and the built-in Microsoft Edge, Google Chrome,
and Mozilla Firefox PDF viewers on Windows. The automated generator uses the production
`FlattenedPdfExportAdapter`; a successful run means the output strictly reopened, passed the pinned
qpdf 12.3.2 check without warnings, retained the expected noninteractive overlay Form structure,
and rendered on every page through the pinned PDFium profile.

Those automated checks are prerequisites, not substitutes for the four manual viewer runs below.
This matrix makes no claim of universal reader compatibility, portal acceptance, legal validity,
PDF/A conformance, PDF/UA conformance, tagged-PDF reading order, or creation or validation of a
digital signature. The signature-image scenario is a placed visual mark only.

## Quick start - remaining human checks

W2 Edge already has a qualifying result. Complete only W1 Adobe, W3 Chrome, and W4 Firefox. The
guided tool owns folder creation, corpus generation, exact hash verification, common metadata, and
content-free result files. The human does not type an output path, create a folder, calculate a
hash, edit JSON, or transcribe the long evidence table.

Open PowerShell in any directory - including `C:\Windows\System32` - and paste this one complete
line. Do not add continuation backticks or backslashes:

```powershell
uv --directory 'C:\projects\PaperWorkShell' run --locked python tools/dev.py pws0307-reader-test
```

The developer-command banner may appear first. Immediately after that banner, look for this exact
status line:

```text
READY [PWS0307_READER_TEST_READY]
```

If `READY` appears, use the one PDF path printed directly below it; do not type a path from this
document. At the keyboard menu, choose `1` for Adobe Reader/free/reduced mode, `3` for Chrome, or
`4` for Firefox. Choose `Q` to stop. It is safe to stop and rerun the same command later: valid
artifacts and attempts are reverified and preserved. After the three qualifying viewer runs, choose
`S`; the desired terminal result is `MATRIX READY FOR CODEX REVIEW`.

When asked for a viewer version, copy only the numeric dotted version/build from that viewer's
About screen, for example `26.1.21789.0`. Do not include product words, `Official build`,
architecture, parentheses, or installed-file metadata. W1 offers only the qualifying Reader/free
or reduced selection. If the Adobe UI instead shows Standard, Pro, or does not identify the mode,
enter `cancel`; no W1 result will be recorded, and that non-gating state should be reported to Codex.

The workspace is under the repository's ignored `output/` tree. The helper will reuse a pair only
when it exactly matches the PDF already accepted by W2: 9,485 bytes, PDF SHA-256
`05faf2055ccb07d85eca7afdb5f446c2b3aeba895d07c2f5fab4d769059fda3e`, and evidence JSON SHA-256
`23c3ea41b4f58739c43a2ef9b805fc6b957a713e10749989b51bad5b1c6c3c21`. A merely
self-consistent but different pair is rejected because it would invalidate the W2 result.

### If the command does not reach READY

Do not improvise a second path or edit the corpus command. Use the recovery for the exact symptom:

| Symptom | Recovery |
|---|---|
| PowerShell shows `>>` or `>` and waits | Press `Ctrl+C` once. Paste the single-line command above again without the `PS>` prompt, line breaks, backticks, or `\_`/`\--` escapes. |
| `--locked has no effect outside of a project` or a path under `System32\tools` | The old relative command was used. Paste the exact command above; `--directory` selects the project, so no `cd` is needed. |
| `PWS0307_CORPUS_DESTINATION_INVALID` | The old low-level generator was used with a missing parent. Stop it and use the guided command; it creates its own ignored workspace. |
| `PWS0307_READER_PAIR_INCOMPLETE`, `PAIR_MISMATCH`, or `WORKSPACE_CONFLICT` | Stop before opening any viewer. Preserve the files and give Codex only the bracketed code, such as `[PWS0307_READER_PAIR_MISMATCH]`. Do not paste the complete output or an absolute path. The helper will never overwrite or silently accept the files. |
| The printed PDF path is not found | Stop and give Codex only the bracketed `ERROR` or `READY` status code. Do not paste the complete `READY` block, disclose the absolute path, or substitute a path from this document or an earlier run. |
| A browser downloads the PDF or opens an Adobe extension/external app | Configure that browser's ordinary built-in PDF viewer and restart only that slot. A delegated/download-only run is not qualifying. |
| `uv` is not recognized | Run `Get-Command uv`. If it is absent, follow the pinned uv setup in the repository README, then rerun the same guided command. |

The low-level no-replace generator remains available to maintainers for independent deterministic
corpus evidence. Human operators should use only the guided command above.

## Fixed five-page scenarios

| Page | Synthetic coverage | Required visible result |
|---:|---|---|
| 1 | Letter-size born-digital page; no added rotation; embedded subset font | The source heading remains visible. Separate text, date, and initials overlays are crisp, correctly ordered, and not clipped. |
| 2 | Cropped small geometry page; 90-degree added rotation | One blue checkmark and one red cross remain inside the visible crop and do not rotate or shift away from their colored reference quadrants. |
| 3 | Cropped geometry page with intrinsic 90-degree rotation | The translucent, stroked ellipse remains centered over the reference geometry without clipping or gross displacement. |
| 4 | Differently sized cropped page; 270-degree added rotation | The synthetic transparent signature-image visual mark remains visible and aspect-preserved over the reference geometry. No viewer may describe it as a digital signature created by PaperWorkShell. |
| 5 | Cropped page with `/UserUnit 2`; 180-degree added rotation | The translucent highlight rectangle remains visible, correctly rotated, and inside the cropped reference geometry. |

The small geometry pages are intentionally physically small. Their different on-screen sizes are
expected and are not a failure by themselves.

## Required Windows viewers

Run the complete procedure independently in each exact viewer. A browser run counts only when its
built-in PDF viewer displays the local file; an Adobe browser extension, an external viewer, or a
download-only configuration does not count as that browser's result.

| Matrix slot | Required viewer | How to capture the viewer version |
|---|---|---|
| W1 | Adobe Acrobat Reader desktop PDF viewer, or unified Acrobat in confirmed Reader/free/reduced mode | Open the app's About screen and record only its numeric dotted version/build. Confirm the effective Reader/free/reduced mode in the Adobe UI. The classic Reader app and Adobe's unified `Acrobat.exe` qualify only in that effective mode; Standard, Pro, mode not shown, and browser-extension runs are non-gating. |
| W2 | Microsoft Edge built-in PDF viewer | Open `edge://settings/help`; record only the numeric dotted Edge version/build and confirm the built-in PDF viewer configuration. |
| W3 | Google Chrome built-in PDF viewer | Open `chrome://settings/help`; record only the numeric dotted Chrome version/build and confirm PDFs are opened in Chrome, not downloaded or delegated. |
| W4 | Mozilla Firefox built-in PDF viewer | Open `Menu > Help > About Firefox`; record only the numeric dotted Firefox version/build and confirm the built-in viewer is selected. |

## Guided protocol 2 - one viewer

The terminal displays the same test card before each result. Keep it open while using the viewer:

1. Open the viewer's About screen. Enter only its numeric dotted version/build and select the mode
   actually shown. Do not enter product words or architecture. For W1, proceed only when the Adobe
   UI confirms Reader/free/reduced mode; otherwise enter `cancel`, which records no result.
   Windows/build/architecture, the immutable artifact binding, UTC time, slot, and operator role
   are captured automatically.
2. Keep the file local. Use `Ctrl+O` or the viewer's ordinary local-file command and select only the
   PDF path printed after `READY`. Do not upload, download, edit, resave, or replace it.
3. Confirm five pages and navigate `1 > 2 > 3 > 4 > 5 > 4 > 3 > 2 > 1`.
4. Inspect every page at Fit Page, 100%, 200%, and 400% against the five-page table above. For each
   overlay, check presence, kind, approximate planned region, crop containment, orientation,
   opacity/aspect where applicable, and absence of gross displacement or clipping. The small page
   sizes are expected; 400% is still required.
5. On page 1, search for the three visible overlay values. Enter only the resulting count from zero
   through three. This remains a limited embedded-font and `/ToUnicode` observation, not a PDF/UA or
   reading-order claim.
6. Click once in all eight overlay regions and press `Tab` through the document surface. No text
   editor, checkbox, signature field, annotation editor, widget, form prompt, script, attachment, or
   external action may activate. Viewer chrome focus is allowed.
7. Close the viewer or tab completely, reopen the same printed path in the same viewer, and revisit
   all five pages at Fit Page and 400%.
8. Return to the terminal and answer the six closed prompts. `PASS` is derived only when the named
   viewer configuration, page sequence, complete zoom sweep, 3/3 searches, non-interactivity, and
   close/reopen all pass. A failure stores only closed defect categories and affected page numbers;
   there is no free-text field in which paths or personal data can leak.

Protocol 2 changes preparation and evidence capture, not the visual coverage. The qualifying W2
protocol-1 run is a strict superset and remains valid against the same immutable PDF hash.

Adobe now documents a unified Windows installer with a common executable for Reader and Acrobat;
available features depend on licensing/sign-in. W1 therefore tests the Adobe desktop rendering
surface only when the Adobe UI confirms its effective Reader/free/reduced mode, rather than relying
on the executable name. A Standard, Pro, or mode-not-shown observation can inform troubleshooting
but does not satisfy W1. This is a matrix-method inference from Adobe's
[unified installer documentation](https://helpx.adobe.com/acrobat/kb/acrobat-unified-installer-windows-overview.html),
not a claim that subscription features were tested.

## Result vocabulary and completion rule

- `PASS`: the recorder derived success because the exact viewer/version completed every required
  group for all five pages, including close/reopen, with no required defect.
- `FAIL`: the viewer opened the artifact but any required step or page result failed.
- `UNAVAILABLE`: the required viewer could not be installed, launched, versioned, or configured to
  use its own viewer. This is evidence of absence, not a pass.
- `NOT RUN`: no qualifying attempt was made.

The PWS-0307 Windows reader matrix passes only when W1 through W4 are all `PASS` against the same PDF
SHA-256. W1 accepts the classic Reader desktop app or Adobe's unified desktop executable only when
the Adobe UI confirms Reader/free/reduced mode. Standard, Pro, and mode-not-shown observations are
non-gating and must not produce W1 `PASS`. An Adobe browser extension or a different desktop viewer
never substitutes for W1.

## Evidence record

Do not copy or edit a row by hand. The guided recorder writes one exclusive JSON attempt with exact
shared metadata and closed observations; Codex validates it and updates this tracked table. Use exact
values, not `latest`, `current`, or a product family name. Keep evidence synthetic and content-free:
no user document names, local absolute paths, usernames, accounts, browser profiles, or unrelated
installed software.

`Artifact implementation revision` is the immutable corpus producer/acceptance revision bound to
the recorded PDF and evidence hashes. It is not the operator's current working-tree revision or the
revision at which a later manual run was performed.

| Slot | Viewer name | Viewer About version/build | Built-in viewer/configuration | Windows edition/version/build/architecture | Artifact implementation revision | Generator version | PDF SHA-256 | PDF bytes | Evidence JSON SHA-256 | UTC date | Operator role | Pages checked | Zooms checked | Text/date/initials search | Noninteractive observation | Close/reopen | Result | Content-free observations |
|---|---|---|---|---|---|---:|---|---:|---|---|---|---|---|---|---|---|---|---|
| W1 | Adobe Acrobat Reader desktop PDF viewer | NOT RUN | Unified Adobe desktop executable detected; Reader/free/reduced mode not yet confirmed | Windows 11 Pro 10.0.26200 / build 26200 / 64-bit | `e088bb7dd10088f3eb454763e3fd4bd203706eb4` | 1 | `05faf2055ccb07d85eca7afdb5f446c2b3aeba895d07c2f5fab4d769059fda3e` | 9485 | `23c3ea41b4f58739c43a2ef9b805fc6b957a713e10749989b51bad5b1c6c3c21` | 2026-08-27 | Codex read-only installation probe | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | Adobe Acrobat is installed. Executable metadata reports version 26.1.21789.0, but installed-file metadata is not About evidence. A qualifying protocol-2 Reader/free/reduced-mode run is still required. |
| W2 | Microsoft Edge PDF viewer | 151.0.4129.101 | Built-in Adobe-powered viewer; no external extension/delegation | Windows 11 Pro 10.0.26200 / build 26200 / 64-bit | `e088bb7dd10088f3eb454763e3fd4bd203706eb4` | 1 | `05faf2055ccb07d85eca7afdb5f446c2b3aeba895d07c2f5fab4d769059fda3e` | 9485 | `23c3ea41b4f58739c43a2ef9b805fc6b957a713e10749989b51bad5b1c6c3c21` | 2026-08-25 | Codex computer-use operator | 1→2→3→4→5→4→3→2→1 | Fit Page, 100%, 200%, 400% | 3/3 found | PASS | PASS | PASS | Five pages reported. Text/date/initials, marks, ellipse, signature-image visual mark, and highlight remained present, contained, oriented, and unclipped. Overlay clicks and 12 Tab presses exposed no document widget, form field, annotation editor, script, attachment, or external action. The same local PDF reopened successfully. The installed Edge executable later updated to 151.0.4129.107; this row records the numeric dotted About version during the qualifying run. |
| W3 | Google Chrome PDF viewer | 151.0.7922.174 | Built-in viewer opened the local PDF; no delegation | Windows 11 Pro 10.0.26200 / build 26200 / 64-bit | `e088bb7dd10088f3eb454763e3fd4bd203706eb4` | 1 | `05faf2055ccb07d85eca7afdb5f446c2b3aeba895d07c2f5fab4d769059fda3e` | 9485 | `23c3ea41b4f58739c43a2ef9b805fc6b957a713e10749989b51bad5b1c6c3c21` | 2026-08-25 | Codex computer-use operator | Pages 1-2 observed only; non-qualifying partial run | 88% and 400% observed only | NOT RUN | NOT RUN | NOT RUN | NOT RUN | Chrome reported five pages and visibly rendered the same final artifact, including page-1 text/date/initials and the page-2 mark thumbnail. The control surface then denied further local `file:` interaction under its browser security policy. Search, the complete page/zoom sequence, non-interactivity, and close/reopen were therefore not completed; no alternate or indirect bypass was attempted. |
| W4 | Mozilla Firefox PDF viewer | NOT RUN | Built-in viewer not exercised | Windows 11 Pro 10.0.26200 / build 26200 / 64-bit | `e088bb7dd10088f3eb454763e3fd4bd203706eb4` | 1 | `05faf2055ccb07d85eca7afdb5f446c2b3aeba895d07c2f5fab4d769059fda3e` | 9485 | `23c3ea41b4f58739c43a2ef9b805fc6b957a713e10749989b51bad5b1c6c3c21` | 2026-08-25 | Codex computer-use operator | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | Firefox is installed; installed-file version 154.0 is not About evidence. A qualifying local-file run was not attempted after the browser security boundary stopped agent-controlled `file:` viewer interaction. A human operator must complete the exact procedure without bypassing that control. |

## Independent automated and rendered-artifact evidence

- The two independently generated no-replace PDF/JSON pairs were byte-identical. The recorded PDF
  is 9,485 bytes with SHA-256
  `05faf2055ccb07d85eca7afdb5f446c2b3aeba895d07c2f5fab4d769059fda3e`; the evidence JSON
  SHA-256 is `23c3ea41b4f58739c43a2ef9b805fc6b957a713e10749989b51bad5b1c6c3c21`.
- qpdf 12.3.2 reported no syntax, recovery, or stream-encoding errors. Poppler 25.07.0 reported five
  unencrypted pages, extracted all three page-1 overlay values, and rendered all five pages at
  144 dpi. The rendered pages were visually inspected: every expected overlay was visible and in
  its planned geometry. Poppler emitted only disclosed source-font substitution warnings for the
  synthetic legacy source resources; the overlay subset font remained embedded.
- The production generator's pinned PDFium path rendered all pages, strict reopen and output-graph
  validation passed, and the semantic fingerprint was
  `162ab8bdbbefd3b91ecea8b35ae9836b63e21548dd56384622a472cebc0c2e07`.

Overall PWS-0307 manual reader result: **INCOMPLETE**. W2 passed. Adobe is now available, but W1,
W3, and W4 do not yet contain qualifying complete runs. The implementation, automated artifact
evidence, and guided protocol-2 recorder are ready; the four-viewer completion rule is not yet
satisfied.

## Deferred platform ownership

- Android is not part of PWS-0307. The representative Android reader corpus and compatibility
  matrix are explicitly owned by `PWS-0506`, together with the conservative portal profile. An
  Android observation made now is optional and cannot substitute for any W1-W4 result.
- macOS Preview is later than the Windows-first platform boundary in `ROADMAP.md`. No current
  PaperWorkShell task authorizes macOS packaging or promotes Preview into the PWS-0307 matrix.
  A future maintainer-selected platform/release task must define its supported macOS versions,
  artifact provenance, ownership, and pass criteria. `PWS-0807` re-runs the packaged release
  candidate matrix but does not by itself expand the supported platform boundary.

These deferrals do not create a sibling-application, cloud, service, database, or shared-runtime
dependency. The corpus and all reader evidence remain standalone PaperWorkShell artifacts.
