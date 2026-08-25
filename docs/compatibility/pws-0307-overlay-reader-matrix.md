# PWS-0307 flattened-overlay reader matrix

## Purpose and evidence boundary

This procedure records manual reader evidence for the deterministic PWS-0307 synthetic flattened
PDF. It covers Adobe Acrobat Reader and the built-in Microsoft Edge, Google Chrome, and Mozilla
Firefox PDF viewers on Windows. The automated generator uses the production
`FlattenedPdfExportAdapter`; a successful run means the output strictly reopened, passed the pinned
qpdf 12.3.2 check without warnings, retained the expected noninteractive overlay Form structure,
and rendered on every page through the pinned PDFium profile.

Those automated checks are prerequisites, not substitutes for the four manual viewer runs below.
This matrix makes no claim of universal reader compatibility, portal acceptance, legal validity,
PDF/A conformance, PDF/UA conformance, tagged-PDF reading order, or creation or validation of a
digital signature. The signature-image scenario is a placed visual mark only.

## Generate one immutable test pair

Run from the repository root and choose two new, explicit destinations whose parent directories
already exist:

```powershell
uv run --locked --no-sync python tools/pws0307_overlay_reader_corpus.py `
  --output-pdf C:\pws-evidence\pws-0307-overlay-reader-corpus.pdf `
  --evidence-json C:\pws-evidence\pws-0307-overlay-reader-corpus.json
```

The generator never replaces either destination. The JSON is content-free: it records hashes,
sizes, versions, closed check codes, and synthetic coverage facts, but no output path, source path,
overlay text value, image pixels, or UUID. It initially records the manual matrix as `not_run`.
Do not edit or re-export the PDF between viewer runs.

Before the first viewer run, independently calculate both artifact hashes and compare the PDF hash
and byte size with the JSON:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath C:\pws-evidence\pws-0307-overlay-reader-corpus.pdf
Get-FileHash -Algorithm SHA256 -LiteralPath C:\pws-evidence\pws-0307-overlay-reader-corpus.json
Get-Item -LiteralPath C:\pws-evidence\pws-0307-overlay-reader-corpus.pdf |
  Select-Object Length
```

If the PDF hash or size does not match the JSON, stop. Regenerate into two new destinations and
restart every viewer run against that one new pair.

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
| W1 | Adobe Acrobat Reader desktop | `Menu > Help > About Adobe Acrobat Reader`; record the complete displayed version/build. |
| W2 | Microsoft Edge built-in PDF viewer | Open `edge://settings/help`; record the complete Edge version/build and whether the new or legacy built-in PDF viewer is configured if the UI identifies it. |
| W3 | Google Chrome built-in PDF viewer | Open `chrome://settings/help`; record the complete Chrome version/build and confirm PDFs are opened in Chrome, not downloaded or delegated. |
| W4 | Mozilla Firefox built-in PDF viewer | Open `Menu > Help > About Firefox`; record the complete Firefox version/build and confirm the built-in viewer is selected. |

## Exact per-viewer procedure

1. Record the Windows edition, version, OS build, architecture, viewer name, full viewer
   version/build, and relevant built-in-viewer or extension configuration. Record the repository
   revision, generator version, PDF SHA-256, evidence JSON SHA-256, PDF byte size, operator role,
   and test date in UTC.
2. Keep the file local. Do not upload it, open it through a portal, or replace it with a browser
   download. Use `Ctrl+O` or the viewer's ordinary local-file command and select the recorded PDF.
3. Confirm the viewer reports five pages. Navigate sequentially from page 1 through page 5 and then
   back from page 5 to page 1. Record the pages actually checked.
4. At Fit Page, 100%, 200%, and 400% zoom, inspect the page-specific expected result in the table
   above. For every overlay, check presence, kind, approximate planned region, crop containment,
   orientation, opacity where applicable, and absence of gross displacement or clipping. At least
   400% is required for pages 2 through 5 because those pages are intentionally small.
5. On page 1, use the viewer's ordinary text search for each of the three synthetic overlay values
   shown on the page. Record whether each visible text/date/initials value is found. Searchability is
   a limited embedded-font and `/ToUnicode` observation; it is not a PDF/UA or reading-order claim.
6. Click once in each overlay region and press `Tab` through the document surface. Confirm no text
   edit control, checkbox, signature field, annotation editor, widget focus, form prompt, script,
   attachment, or external action is activated. Viewer chrome controls may receive focus and are not
   document interactivity.
7. Close the viewer or browser tab completely. Reopen the same local PDF through the same viewer,
   verify the recorded SHA-256 still applies, revisit all five pages at Fit Page and 400%, and record
   the reopen result.
8. If any overlay disappears, changes kind, is clipped, is grossly displaced, loses expected
   transparency/aspect, activates interactive behavior, or the file/page sequence cannot be
   reopened, record `FAIL` with a content-free defect description and the affected page numbers.
   Do not regenerate or edit the artifact to turn that run into a pass.

## Result vocabulary and completion rule

- `PASS`: the exact recorded viewer/version completed every step for all five pages, including
  close/reopen, and no required defect was observed.
- `FAIL`: the viewer opened the artifact but any required step or page result failed.
- `UNAVAILABLE`: the required viewer could not be installed, launched, versioned, or configured to
  use its own viewer. This is evidence of absence, not a pass.
- `NOT RUN`: no qualifying attempt was made.

The PWS-0307 Windows reader matrix passes only when W1 through W4 are all `PASS` against the same PDF
SHA-256. In particular, Adobe Acrobat Reader being unavailable must be recorded as `UNAVAILABLE`;
it is a non-pass and leaves the matrix incomplete. A result from Adobe Acrobat Pro does not silently
substitute for the named Reader slot; record it as a non-gating additional observation if useful.

## Evidence record

Copy one row per qualifying run. Use exact values, not `latest`, `current`, or a product family name.
Keep observations synthetic and content-free: no user document names, local absolute paths,
usernames, accounts, browser profiles, or unrelated installed software.

| Slot | Viewer name | Viewer version/build | Built-in viewer/configuration | Windows edition/version/build/architecture | Repository revision | Generator version | PDF SHA-256 | PDF bytes | Evidence JSON SHA-256 | UTC date | Operator role | Pages checked | Zooms checked | Text/date/initials search | Noninteractive observation | Close/reopen | Result | Content-free observations |
|---|---|---|---|---|---|---:|---|---:|---|---|---|---|---|---|---|---|---|---|
| W1 | Adobe Acrobat Reader | TBD | Desktop Reader | TBD | TBD | 1 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | NOT RUN | None |
| W2 | Microsoft Edge PDF viewer | TBD | Built-in viewer | TBD | TBD | 1 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | NOT RUN | None |
| W3 | Google Chrome PDF viewer | TBD | Built-in viewer | TBD | TBD | 1 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | NOT RUN | None |
| W4 | Mozilla Firefox PDF viewer | TBD | Built-in viewer | TBD | TBD | 1 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | NOT RUN | None |

Overall PWS-0307 manual reader result: **NOT RUN**.

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
