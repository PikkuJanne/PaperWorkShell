# PaperWorkShell user guide

**Draft for the TRACE workbench.** PaperWorkShell is a standalone Windows 11 x64
application. It works offline without an account, versions independently, and
keeps your cases and document processing on your computer. It runs independently
of ThirdLife and its other applications. There is no current ThirdLife
compatibility claim. Use the setup instructions for the build you received.
[Setup instructions](../../packaging/README.md) · [Windows support evidence](../release/WINDOWS11_X64_SUPPORT.md)

## Make your first PDF

1. Open **PaperWorkShell** from Windows Start. At **Choose a case**, select
   **New case** (Ctrl+N), enter a **Case name**, and choose **Create case**. A
   separate case keeps each packet and its saved edits together.
2. Choose **Add file** (Ctrl+I), select one PDF, JPEG, or PNG, and wait for its
   pages to appear. Repeat for each file. PaperWorkShell copies each supported
   input into the case and keeps your source files unchanged.
3. In **Pages**, select a page and review **Planned**. Use **Move up** or
   **Move down** (Alt+Up or Alt+Down) to change its output position. **Rotate
   selected page 90°** (Ctrl+R) changes its orientation. **Remove page** (Delete)
   takes it out of the packet while preserving the imported copy.
4. Choose **Export PDF** (Ctrl+Shift+E). Read the page order, select **Choose PDF
   file**, and give the output a new filename. Wait for the checked result.
5. Choose **Open PDF** to look through the pages in your PDF reader or **Open
   folder** to find the saved file. Check page order, orientation, completeness,
   legibility, and any text or marks you added. Use **Close case** to return to
   the chooser. To resume later, select the named case and choose **Open case**
   (Ctrl+O).

For a recipient with specific requirements, compare its current instructions
with your planned files before you start. Use the checklist workflow below when
you need to review packaging rules or produce several files. Send the finished
files yourself after reviewing them.

## Organize and preview pages

- **Pages** shows the order you are assembling. **Sources** groups pages by
  imported document. **Arrange** exposes the page movement controls.
- **Source** shows the original page. **Planned** shows saved changes and
  supported visual marks. Open the mark editor when a placement needs closer
  inspection. **Exported** shows the checks for the verified PDF. After you
  reopen a case, use **Select exported PDF** to match the file to the saved
  result by size and SHA-256. A missing or changed file requires a fresh export
  if you need another verified copy.
- **Reorder pages** (Ctrl+Shift+O) opens a proposed order with visible move
  buttons. Confirm only after the order looks right. **Undo** (Ctrl+Z) and
  **Redo** (Ctrl+Y) apply to organizer actions in the current session.
- **Recipe** shows the saved steps and their settings. **Save recipe** records
  supported source slots, page selection, order, and rotation. To **Apply
  recipe**, first add the matching files in slot order. **Export PowerShell
  script** is an optional advanced workflow for a new ordinary PDF using the
  installed offline runtime. OCR, scan cleanup, and overlays require the
  application workflow instead.
  [TRACE recipe details](../development/pws0813-trace.md)

## Improve scans or add searchable text

1. Select a supported picture page. Choose **Improve selected scan** (Ctrl+Shift+A).
   Adjust the crop, deskew, or other available settings. Choose **Update
   preview** and compare the original with the adjusted page. Choose **Apply
   adjustments** to save the reviewed settings or **Cancel** to leave them out.
2. For searchable output, open **OCR languages** (Ctrl+Shift+L). Choose and
   **Save selection** for the installed languages you need. The export dialog
   lists the recognition language. English is the initial default. If a
   required language is missing, follow the offline instructions in the
   language dialog and choose **Refresh** after the approved files are in place.
3. In **Export PDF**, turn on **Make scanned text searchable** and choose a
   new PDF file. Local optical character recognition (OCR) adds a text layer to
   eligible scanned pages and keeps existing digital text. Review the result
   against the visible pages, especially names, dates, and reference numbers.

Ordinary PDF export requires searchable export for saved scan improvements. It
refuses combinations of OCR with marks, or marks on pictures, before writing a
file. In checklist exports, scan processing and active visual marks in the
same PDF group are also unsupported. Review the grouping and use separate PDFs
if the recipient allows them. [Known limitations](../../KNOWN_LIMITATIONS.md)

Use **Check page quality** (Ctrl+Shift+Q) to find items marked **Needs review**.
Select a finding and use **Go to selected page** to inspect it. Treat suggestions
about blur, clipping, blank or duplicate pages, and recognition as prompts to
look at the page yourself.

## Fill a form or place a signature image

Select a supported page and choose **Add text or marks** (Ctrl+Shift+F). Choose
**Add text** or **Add checkmark**, then edit the selected item. Position and
dimensions use percentages. Text size uses points. Use the fields, movement
buttons, or Alt+Arrow to move a mark. Use Alt+Shift+Arrow to resize it. Add
Ctrl to either shortcut for smaller steps. Choose **Save changes** to save
the draft. **Cancel** discards a draft before saving begins. Review the
exported PDF for placement and readability.

**Place signature image** (Ctrl+Shift+S) adds a visual mark from a local JPEG or
PNG. Review the placement and confirmations and choose **Place visual image**.
To take a placement out of the case, use **Remove signature image**
(Ctrl+Shift+D), select the placement, and review whether to keep its case copy.

| Term | Meaning in PaperWorkShell |
| --- | --- |
| Signature image | A visible picture on a page. Use **Place signature image** for this action. |
| Digital signature | A cryptographic signature embedded in a PDF. PaperWorkShell can report detected signature markers. Creation and validation belong to a dedicated signing tool. |
| Qualified electronic signature | A regulated kind of electronic signature provided through a qualified signing service. Use the recipient's required signing process. |

If you modify a digitally signed source, keep the original and review the
**unsigned derivative** warning. Proceed only if a new unsigned copy meets
your needs. Visual overlays also leave underlying information in place. Use
the dedicated removal workflow when content must be removed from a new PDF.

## Permanently remove information

When your build offers verified removal export, use a case with one active PDF
page containing the information to remove. The source must be unencrypted.
Quarter-turn rotation and supported crop settings are allowed. Additional
active pages, visual overlays, scan cleanup, and other unsupported content
block this export.

1. Select the page and choose **Permanently remove information** (Ctrl+Shift+P).
   Choose **Add region** (Alt+A), then use **Left**, **Top**, **Width**, and
   **Height** to mark the area. The values describe the full page before crop
   or rotation, with 1,000,000 equal to 100%. Choose **Update region** and
   repeat for other areas. Choose **Save unapplied marks**. Information is
   still present at this stage. Ordinary PDF and checklist export are blocked
   while active removal marks are saved.
2. Choose **Export removal derivative** (Ctrl+Alt+E). Review the saved regions
   and warnings, then select **Marks reviewed**. Select **Unsigned derivative**
   if prompted. Choose **Choose new file** and give the output a new filename.
3. Read **Removal export result**. Use the derivative only after the required
   checks pass. If they fail or remain unknown, inspect the result and follow
   its recovery action. A failed required check blocks export.

PaperWorkShell reconstructs the affected page as a new image with the marked
regions removed and discards original page objects from the derivative. The
supported profile checks PDF structures and revisions, text extraction, image
extraction, rendering, a full resave, and private English OCR. The output is
an unsigned, image-only PDF. You may lose interactive forms and accessibility
structure in the new file. Your original and any earlier copies still contain
their original information.
[Removal qualification and limits](../architecture/redaction-export-gate.md)

## Review and export a checklist package

1. Open **Document checklist** (Ctrl+Shift+C). The built-in generic template
   lists **Application form**, **Identity evidence**, **Proof of address**, and
   **Supporting evidence**. Select a category and its pages, choose **Add
   selected pages**, then **Confirm added document** after checking it. Check
   assignments and suggestions yourself and choose **Save checklist**.
2. Open **Review package constraints**. Compare allowed output formats,
   file and page limits, and requested names with the recipient's current
   instructions. Choose **Apply constraint changes** to save your changes.
3. Choose **Export checklist package** (Ctrl+E). Under **Output format**,
   choose **Combined PDF**, **Separate PDFs**, or **ZIP package** when
   available. Read the **Package summary**. If you need to fix the case or
   its constraints, close the review, make the changes, and open it again.
   Use **Refresh review** to rerun checks in the open dialog. Once you've read
   the warnings and completed the human checks, select **I reviewed the listed
   warnings and human checks**. Confirm creation of unsigned derivatives
   separately when prompted.
4. Choose **Export package** and select a destination. If the packet is too
   large, use **Preview size reduction** when offered. Inspect measured sizes
   and original/proposed pages before accepting the changes. If the target
   cannot be reached without going below the image-quality floor, review the
   recipient's permitted options instead of expecting a smaller file.
5. After a successful export, open the files from the destination and read
   the result. **Save summary** keeps a copy of the displayed checks and
   review notes. **Save text companion**, when offered, saves text available
   in the exact exported PDF or package after a privacy acknowledgement.
   Review both before sharing them. [Storage, privacy, and deletion](CARE_AND_RECOVERY.md)

The summary records the checks actually performed. File counts, size limits,
and other packaging checks help you compare the output with the recipient's
instructions. The recipient decides acceptance. Use an appropriate reader to
review the files. Searchable or selectable text alone does not establish
PDF/UA conformance.

## Work with the keyboard or a screen reader

- Press F1 or choose **Settings → Keyboard guide** to open the in-app reference.
  Ctrl+K searches for commands available for your case or selected page.
  Escape closes the search and returns to your previous control.
- Use Tab and Shift+Tab to move between controls, arrows to select pages and
  options, and Space on a focused button or checkbox. Enter can activate a
  dialog's default action, so read its label before confirming. In mark
  fields, Enter updates the draft. Use **Save changes** to keep it.
- Lists, buttons, and exact numeric fields provide a keyboard path for
  reordering, assignment, and mark placement. In a read-only guide or
  summary, Ctrl+Home and Ctrl+End move to the start and end. Tab leaves
  the text view.
- Follow the progress and result messages and wait for the saved or checked
  result. Use **Cancel current operation** or Escape when cancellation is
  available. After a failed preview, use **Retry selected preview**
  (Ctrl+Shift+R). For save failures, read the recovery message and use the
  retry action before closing the case.

For help with a shared computer, a failed save, local updates, a support bundle,
or removing PaperWorkShell, see [Care and recovery](CARE_AND_RECOVERY.md).
