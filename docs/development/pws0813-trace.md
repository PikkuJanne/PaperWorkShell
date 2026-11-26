# PWS-0813 — PaperWorkShell TRACE

## Scope and current baseline

On 25 November 2026 the maintainer approved TRACE as PaperWorkShell's default native design and
authorized implementation through private build, installed verification and delivery. The approval
supersedes the proposal-only sequencing, a permanent optional shell and the old-interface human
prerequisite. The PWS-0811 intake finding and T05/HT-06 timing results retain their recorded status.
This work retains the existing application, worker, case and data protection boundaries and leaves
release signature and public release decisions with their existing owners.

The primary checkout was `codex/pws-0811-internal-repairs` at
`2e70ec0e7174dab342f6df43d7b4d685f84a3259` with substantial local work. It remains intact. The
dedicated checkout is `output/pws0813/trace-worktree` on `codex/pws-0813-trace`, based on the
last exact working product commit `28c254b8a472184ae0817c28a223c7f9a1aa91e8`. The TRACE worktree
can carry explicitly reviewed current local changes. Record the exact source commit used for the
installer when it is built.

The authorized Hyper-V guest `PWS-Test` is running Windows Professional x64 build 26300.9711,
with Ethernet disconnected and Explorer in session 5. Read-only PowerShell Direct checks worked
under the existing local administrator and standard-user credential files. The standard-user
check had no Administrator role. It found 67 cases under the existing LocalAppData root and
`pws0811-engineering-image-preview-v12` under `Programs/PaperWorkShell` with marker SHA-256
`0fb1e920ffa89ae1140fea4eb9e90a02dc5865351b9819411d68a76625dbfa84`. The installed process
was in session 5. The regular and notices Start shortcuts were both present. R16 and R18
checkpoints were observed. The outside-guest backup and a fresh inventory matched all 840 case,
sample and export entries. The baseline inventory and backup changed no guest state. TRACE is
pending build and installation at this checkpoint. The guest only exposed Windows PowerShell
5.1 and its effective policy was Restricted, which blocks generated `.ps1` script-file execution.
Keep that policy unchanged. Exercise the packaged console runtime in the guest and the generated
PowerShell script in a compatible already available host environment.

## Feature parity map

The existing entry points and owners below were identified in the working product. TRACE locations
are the migration targets. The checks column names the focused evidence to collect. Previous
tests and installed observations keep their original source and artifact scope.

| Workflow | Existing entry and operation | TRACE location and supported transition | Focused check |
| --- | --- | --- | --- |
| Standard launch and case chooser | Start shortcut → `launcher.c` → `__main__.main` → `ui.application.run_desktop_application`; `PaperWorkShellWindow._build_welcome_page`; `OrganizerBackend.list_cases` | Normal launch opens the TRACE chooser and its saved-case list. Open, refresh, empty and failed-load states remain actionable. | Open through standard Start shortcut, inspect chooser, select and open existing case. |
| Named case creation and rename | `_request_create_case`, `_ask_case_name`, `_request_rename_case`; `OrganizerBackend.create_case/rename_case`, `CaseLifecycleService` | Chooser New case and workbench case identity commands. Confirm and cancel preserve the case's intended identity. | Create, cancel rename, rename, close and reopen with prior edits. |
| Assisted cases and privacy | Chooser Start assisted case, `AssistedSessionController`, consent, lock, unlock, expiry and handover dialogs; `AssistedUseService` | Chooser and case commands retain consent, expiry, explicit lock and unlock. Cover hides case content and child dialogs. | `test_assisted_use_dialogs.py`, `test_assisted_reopen_feedback.py`, `test_assisted_file_dialogs.py`; inspect chooser and lock surface. |
| Close, autosave, retry and recovery | Close case, Retry save, Retry close, shutdown refusal, startup recovery; `OrganizerBackend.close_case/flush_autosave/retry_autosave` and `StartupRecoveryService` | Case save status and inline progress/recovery. Saving, saved, dirty and save failure reflect the state returned by the backend. | `test_organizer_ui_lifecycle.py`, `test_desktop_startup_recovery_windows.py`; inspect retry and focus after failure. |
| File intake and errors | `_choose_import`/Ctrl+I, `_queue_import`, `OrganizerBackend.import_source` and `SourceIngestService` | Add file through the existing picker, real importing and durable result. Unsupported, missing, changed and encrypted sources retain actionable failure. Password-encrypted PDFs use the existing authorized unencrypted-copy recovery. | Import synthetic PDF, JPEG and PNG, reject an unsupported or encrypted sample, verify source hashes and retry. |
| Page list, source grouping and selection | Organizer page model, `_selected_page_id`, `_restore_page_selection`, `OrganizerViewState` | Source and assembled page navigation preserve stable page IDs and show original and assembled position. | Reorder distinct synthetic pages, inspect both mappings and selection, close/reopen. |
| Page preview and preview retry | `_request_previews`/`_thumbnails_ready`, `_retry_failed_preview`, `OrganizerBackend.get_thumbnails`; `PagePreviewService` | Real document canvas and navigation with bounded worker-backed previews. Planned and Source distinguish transformations and identity. Preview failure offers retry. | `test_mixed_preview_window.py`, preview-cache and obsolete-result tests; inspect source orientation after rotation. |
| Page changes, arrange, undo and redo | Earlier, Later, Rotate, Remove, `_change_full_order`/`PageOrderDialog`; `OrganizerBackend.move_page/reorder_pages/rotate_clockwise/remove_page/undo/redo` | Organize and arrangement view keep keyboard moves and visibility into active targets. Undo/redo act on existing operation history. | Move and rotate a selected page, remove and undo, redo and reopen. Confirm assembly via independent PDF inspection. |
| Scan improvement | `_request_scan_review`, `ScanReviewDialog`, `OrganizerBackend.begin_scan_review/get_scan_review_preview/apply_scan_review/cancel_scan_review` | Improve opens a scoped before/after adjustment view with crop, deskew and supported tonal/cleanup intent. Apply commits, cancel preserves original plan. | `test_scan_review_dialog.py`, `test_scan_review_window.py`; inspect one representative edited scan and untouched source. |
| OCR, OCR language settings and quality review | Searchable PDF choice, OCR-language dialog, `_request_quality_review`, findings/open selected page; `OrganizerBackend.export_pages_pdf`, OCR manager and `QualityAnalysisService` | Improve, Check and Settings expose installed languages, applicable quality controls and scoped review. OCR text remains reviewable and output requires supported engines. | `test_ocr_language_window.py`, `test_quality_review_window.py`; independently extract text and retain uncertain recognition. |
| Core marks | `_request_core_overlay_tools`, overlay dialog/canvas; `OrganizerBackend.begin_core_overlay_tools/apply_core_overlay_tools/cancel_core_overlay_tools` | Fill opens text, date, initials, checkmark, cross and supported visual marks, with keyboard placement, movement and resize. Confirm, remove and cancel retain correct page scope. | `test_overlay_tools_dialog.py`, `test_overlay_tools_window.py`; place text/checkmark and independently inspect the exported rendered page. |
| Signature image | `_choose_signature_image`, placement/removal editors; `OrganizerBackend.begin_signature_image/commit_signature_image/signature_image_placements/delete_signature_image` | Fill supports local signature-image review, placement and removal with the existing retention choice. Use the explicit image wording. | `test_signature_image_dialogs.py` and persistence tests; inspect the dialog without treating placement as digital signing. |
| Signed/unsupported documents | PDF and package review, `signed_pdf_confirmation`, source PDF preflight | Protect and Export preserve signed-source acknowledgement, unsigned derivative wording and warnings for unsupported interactive content. | Existing signed-export and preflight tests, plus a representative confirmation inspection. |
| Gated permanent removal | `_request_redaction_tools`, `_request_removal_export`, verification/recovery report dialogs; `OrganizerBackend` removal tools and worker export service | Protect keeps existing admissions and fail-closed verified removal, distinct from Fill. Commented or blocked routes remain gated. | `test_redaction_tools_window.py`, `test_redaction_export_window.py`, `test_removal_export_desktop.py`; no widening of gate or verification claims. |
| Templates, guided checklist and assignment | Built-in template adoption at create, `_request_checklist` and `ChecklistWorkflowService` | Add/Check guided next steps and Checklist editor. Assigning and confirming are separate actions, with incomplete/ready status preserved. | Checklist UI/persistence checks and one installed template/package journey. |
| Portal constraints and preflight | `_request_portal_constraints`, `PortalConstraintWorkflowService`, package review and typed findings | Check retains settings for applicable counts, size, naming and security constraints plus concrete error/review states. | `test_portal_constraints_window.py` and one incomplete then resolved package review. |
| Ordinary and searchable PDFs | Export PDF action/button, `_ask_pdf_export_options`/`_choose_pages_pdf_export`; ordinary, flattened and searchable services | Export states the effective mode, destination and known page count. Use real worker progress, cancellation, recovery and verified file actions. | `test_pages_pdf_export_desktop.py`; actual output reopened/rendered, page count/order and text inspected. Check collision retains the first file. |
| Checklist package and summary | `_request_package_export`, package review, ZIP/separate/combined mode, `_choose_package_summary_destination`; `PackageWorkflowService` | Export package keeps modes, readiness decisions, proposed paths and summary/review. | `test_package_export_dialog.py`, `test_package_export_window.py`; verify an installed package and its summary. |
| Target-size and comparison | `_choose_compression_destination`, `_prepare_compression`, `_preview_compression_page`, commit/discard; compression workflow | Export preserves prepared size, scope, before/after and legibility review. Unattainable targets stay explicit. | `test_compression_review_dialog.py`, `test_compression_review_window.py`; inspect Cancel/Discard and bounded preview. |
| After export | PDF footer Open PDF/Open folder; package result, text companion, explicit case deletion; `save_package_text_companion`, `PostExportDeletionService` | Output state retains actions for the verified artifact and scoped post-export utilities. Deletion still requires explicit review and intact external output. | `test_post_export_window.py`, `test_post_export_deletion_windows.py`; use only a synthetic case if checking deletion. |
| Progress, cancel, errors and focus | `_progress_changed`, `_cancel_active_operation`, `_failed`, `_restore_failure_recovery_focus`, announcements | Shared progress and error surfaces identify the active operation and recovery. Stale case/request results are ignored. | Cancel a relevant synthetic operation and switch cases. Check focus and absence of stale render/result. |
| Settings and help | OCR language chooser, Help/F1, support-bundle preview/consent, separate installed notices | Settings and Help expose existing offline tools and TRACE display preferences, with one styling policy for application-owned dialogs. | Settings/Help, `test_diagnostic_bundle_dialog.py`, `test_notice*`, keyboard/scaling inspection and notices Start shortcut. |
| Installed lifecycle | Per-user Inno Setup, `--review-uninstall` and `--notices`; authorized Windows Settings removal with `Keep cases` | The product launcher opens TRACE. Notices and review have application-owned native surfaces. Installer owns setup chrome. File associations remain disabled. | Inspect installed shortcuts/arguments; back up cases and verify Keep cases before and after installing the new candidate. |
| Recipe, comparisons and portable automation | New TRACE features use existing organizer and worker services | Recipe selection reveals affected stable page instances. The three comparison entry points preserve actual identity. Save a bounded recipe with declared scope and provide a PowerShell route to the installed local runtime. | Check status across page vs plan changes, restart and cancellation. Independently compare a GUI/headless PDF and test Unicode, collisions and unsupported steps. |

## Private build and installed verification

The supported route is an offline `uv build --offline --wheel --sdist`, then
`tools.build_windows_installer stage` and `compile` through the trusted locked environment. The
assembler uses its exact reviewed dependency inputs, launcher/receipt, compiler and Microsoft
runtime overlay. All 8,241 reviewed inputs (3,712,039,365 bytes) were copied and hash-verified
against the frozen build-input closure into the TRACE worktree. The preparation receipt is
`output/pws0813/build-inputs.json` in the primary checkout. Stage in the TRACE worktree
`output/pws0802` with a fresh candidate identity.

Build input admission checks whether the reviewed dependency closure can be reused for TRACE.
The copied inventory and compiler passed their existing checks. The first launcher verification
failed because the Windows checkout expanded two committed launcher sources from LF to CRLF.
Compare copied review anchors against the committed Git bytes, then restore those exact bytes
in the isolated worktree for files where line-ending conversion is the sole difference. If the
launcher verification passes with those bytes, reuse the launcher. A remaining mismatch requires
identifying the changed reviewed input before compiling. Launcher and Microsoft overlay
verification passed after the two launcher files were restored to their committed LF bytes.
Verify the packed application members against the committed source and record the Setup hash,
payload inventory, marker and signature status. Do not reuse previous candidate identities.

The installer intentionally rejects a different candidate installed at the same location. Use the
supported replacement journey. Retain and compare current synthetic cases, samples and exports
outside the VM, verify actual session, candidate and foreground process, close normally, then use
the current installation's Windows Settings removal with **Keep cases** before running TRACE Setup
as the standard user. Launch the actual installed app from Start and reopen an existing case.
Preserve the R16/R18 checkpoints. The checked-in installer requires lowest privileges and does
not register file associations. Verify those installed properties for the TRACE candidate.

The first installed rehearsal answers whether the new native entry points preserve a standard
user's retained cases, real edits and independently verifiable output in the current offline guest.
Use a new candidate and new synthetic destination. A retention mismatch, absent required
application surface, wrong mapping, invalid output or collision overwrite blocks that path. A
passing rehearsal supplies candidate-specific evidence only for its actual scenarios. Include
Ctrl+K and visible routes, keyboard focus, native accessible names and state, and a 200% display
scaling inspection if available. No subjective preference currently needs a human session.

## First TRACE engineering build

The offline wheel, sdist, stage and Inno Setup compiler succeeded from
`40b6c4558e920a26a6d046bf1c349540b4300cbd`. The resolved inventory matched all 336
packaged application files to that Git revision. The unsigned installer is
`PaperWorkShell-pws0813-trace-engineering-win-x64-UNSIGNED.exe` under the TRACE worktree's
`output/pws0802/pws0813-trace-engineering/installer`. It is 512,949,494 bytes with SHA-256
`f9ad6954ada73266090a45676f858d07943f9aed5cbcc5962a51271be44ad684`. The full receipt is under
`output/pws0813/build-pws0813-trace-engineering` in the primary checkout.

The bundle's six transfer hashes matched. Ruff, strict mypy, localization, schemas, task graph,
docs and import and portfolio boundaries passed. The development headless run used the real
isolated worker, exported the requested two pages in order and rotation, and refused an
occupied destination. Independent PDF inspection and source and destination SHA-256 checks
matched. The automation case was removed. A separate focused privacy, preview and assisted
suite passed all 64 tests.

The first source focus run showed five tests still asserting focus on controls now represented
by TRACE menus. Verified replacement assertions identify the visible Fill and Export menus.
The accessibility run stopped at 12 failures after 289 passes. Source inspection also confirmed
two product defects. Dialog-start errors skipped visible menu proxies, and tab-order setup ran
before some controls were parented. The successor uses native Tab traversal across the workbench and resolves available
hidden editor actions to a visible action group. A passing focused run would justify
that source change for the next private installer. The earlier accessibility results remain scoped
to the first build.

The packaged PowerShell script reached the backend on a separate byte-verified runtime copy
and reported `PDF_EXPORT_DESTINATION_FAILED`. A destination-only publication using the same
storage adapter and same parent will distinguish path admission from the full export pipeline.
An adapter pass would move the investigation to export composition. A failure with the same
sanitized code would identify the destination path or fixture setup as the next narrow check.
The destination adapter committed a bounded synthetic payload in the same directory with
both ASCII and Unicode filenames. That rules out the tested destination path and no-replace
publisher. The next run captures closed adapter contexts through the full packaged export
boundary. That run reached worker verification, then the destination publisher returned an
uncommitted outcome. The original output directory also contained the protected runtime copy.
Running the unchanged script in a sibling output directory tests whether that relationship
caused the failure and determines whether a product repair is needed.
That run passed under host PowerShell 7 with the exact staged runtime copied and verified
against all 2,454 inventory members. The unchanged script created the expected 786-byte PDF.
Independent page order, render dimensions, rotation and source-hash checks passed. The two
failed attempts in the runtime-ancestor directory remain recorded. The ordinary output path
required no automation code change.

The focused successor run passed 157 tests covering page and command identity, visible menu
focus, editor recoverability, RTL, review and export controls, and scaled layouts. The successor
also displays the actual PDF destination during export and distinguishes unsaved changes after
a failed save from a save in progress.

The first installer replaced V12 through the supported interactive Keep cases route in PWS-Test.
All 840 retained entries and 3,210 installed payload members matched. The standard-account Start
shortcut opened TRACE. The native chooser capture showed white text on a lime row despite the
palette's requested dark selection text. The next candidate specifies the approved dark row and
lime edge in the Qt item styling. Its native check will verify selected text legibility and focus.
The source rehearsal also confirmed that two signature cancellation branches skipped focus
restoration because the backing action was hidden. The successor targets the available Fill menu.
The installer-focused verification proceeds with these narrow corrections together.

## Third engineering build

Source `2b56ab3dc05a60989008de0008b7cac704218913` on `codex/pws-0813-trace` built the unsigned
`PaperWorkShell-pws0813-trace-engineering-b3-win-x64-UNSIGNED.exe`. The verified delivery copy is
under `output/pws0813/delivery` in the primary checkout. Size is 512,949,825 bytes and SHA-256 is
`834ed22fb745a8099cb9dfbb9ad8ff2adf5cb75f2f5daca286f995dd8481297b`. The exact build receipt is
under `output/pws0813/build-pws0813-trace-engineering-b3`.

The B3 source passed all 359 accessibility tests and 68 focused assisted-case, signature,
removal, constraints and workbench tests. Format check, lint, strict typing and the portfolio
boundary passed. The broad B2 unit run was stopped after surfacing assertions that still expected
old controls to be visible. The changed routes were checked with native menu locators in the
focused successor suite.

The confirmed B1 installed rehearsal created a synthetic case, imported two PDFs, a JPEG and a
PNG, and persisted all four pages. Keyboard movement and rotation selected the intended
stable source-page instance. Whole-candidate installed verification is still in progress at this
record point.

The limited PowerShell Direct batch invocation on installed B1 rejected the unsupported OCR
recipe and failed supported input inspection with `SOURCE_INSPECT_FAILED`. A local limited
noninteractive task will test whether that failure is specific to the remoting token. This uses
the same fixture, installation and parser boundary. A pass establishes local background batch
processing in this lab. A failure leaves installed headless execution unresolved.
Task Scheduler rejected registration of the limited S4U task with Access is denied. No process
or document worker started. A local limited Interactive task tests whether the installed
batch entry works outside PowerShell Direct with the routine user token. It can establish
the background console behavior in an existing user session. A separate noninteractive logon
remains unverified in this environment.
The limited local task ran installed B1 `--trace-batch` without launching a GUI, producing the
expected 786-byte PDF and seven actual checks. Its SHA-256 matches the independently inspected
PowerShell output. The source remained unchanged and the temporary automation case was removed.
This result is scoped to a console process in the existing limited desktop session. The S4U
registration denial, PowerShell Direct intake failure and guest `.ps1` restriction retain their
separate results.

The first installed PDF export wrote `trace-installed-packet.pdf`, 23,664 bytes, with SHA-256
`481babcba02526217d4933925fb12f62d7aa38046172c2f2cf9bcb393b67e452`. Bounded Poppler reopening,
four independent renders and text extraction confirmed the planned JPEG, rotated PDF, digital form,
and PNG order. Strict pikepdf reopening found no syntax findings or encryption. Page rotations were
0°, 90°, 0°, and 0°. The original fixture hashes matched, and the saved export history identified
the same bytes.

The final source audit found that a checklist package could clear the shared footer path while
the Exported view still had verified evidence for a PDF. Authentication could succeed, then the
viewer could try to use the different footer path. The correction passes the verified result's
own path to the viewer and ignores completion after case closure starts. A regression test uses
distinct report and footer destinations. A pass establishes that the view opens the artifact it
actually checked even after a separate export changes the footer.

## B4 delivery

The unsigned Windows x64 installer was built offline from
`cba562af164305ca1de7384727ad781d81a7138e`. Use `output/pws0813/delivery/PaperWorkShell-pws0813-trace-engineering-b4-win-x64-UNSIGNED.exe`
in the primary checkout. It is 512,950,039 bytes, with SHA-256
`dba22c0c902b148744bcd99ba35e84a0471058fff16ca5f19beaf8b0d72cfc7d`. The delivery copy matched
the compiler receipt, and Authenticode reported NotSigned. The build bound all 336 application
members to the exact source. The private build receipt is in
`output/pws0813/build-pws0813-trace-engineering-b4`.

B4 passed all 359 accessibility tests and 180 focused unit, worker-boundary, privacy and lifecycle
tests. The exact preview and export regression checks were part of these runs. Format check, Ruff,
strict typing, localization, documentation, schemas, task graph and both import and portfolio
boundaries passed. Of 2,454 B1 and B4 runtime members, only the window, presentation, three locale
catalogs and installed marker differ. This preserves the exact batch, recipe, worker, importer,
exporter and engine files exercised earlier.

B1's installed F1 guide, Generic application packet checklist, and verified offline English
settings were exercised. The standard-user editor saved a text mark and a checkmark on the chosen
PDF page. The four-page searchable derivative was 26,461 bytes with SHA-256
`d5df18fce4db1f85bf67ae7562ad6d21094e88b1efe9b2a640f022d36f463db0`. Independent rendering and
extraction verified retention of born-digital text and a searchable scan. The scan still reads
`SCAM PAGE 206` in extraction. The source files remained byte-identical.

B4 Setup completed as the limited `PWSTest` account in the existing offline guest. The B1 removal
review used Keep cases, and all 860 inventoried pre-install entries matched after B4 installation.
All 3,210 installed payload members matched. Both Start shortcuts point to the installed launcher
under the standard user's local `Programs/PaperWorkShell/runtime` directory. The regular shortcut
has no arguments. The notices shortcut has `--notices`. The initial B4 marker SHA-256 was
`9e854c18232c362ccc135511fd03f49a6fee28831ba6c39bfaf0189a162fe0c4`. The owned Setup task exited
and was removed. Evidence is in `output/pws0813/installed-trace-b4` in the primary checkout.

The standard-user Start launch reopened an older saved case and the retained four-page rehearsal
case. The latter opened at revision 11 with existing page identity, rotations, marks, real canvas
and linked recipe. The native chooser and selected page row measured 12.28:1 selection contrast
at 96 DPI, correcting the first build's observed 1.11:1. Ctrl+K opened the named search edit with
focus. The combined image and PDF case with saved marks produced the existing
`ORDINARY_PDF_COMBINATION_UNSUPPORTED` error and kept the edits. The independent PDF-only marks
and stale-result check uses a reversible selection change on this synthetic case.

B4 wrote the supported two-page marked PDF. The 4,315-byte artifact has SHA-256
`b4d73aabb483b8d1ea11ee14f2b6d758130a55aa8a4d1e9ba3a5e64bd067f008`. The saved history and original
fixture hashes matched. Independent Poppler rendering and extraction confirmed `TRACE tested mark`
and the checkmark on the rotated first page and the unmarked original form on page two. The
Exported view checked the actual bytes and identified the selected page as output page 1 of 2.
The same-name retry reported `PDF_EXPORT_DESTINATION_EXISTS` and the file hash stayed identical.
A 90° to 180° semantic rotation changed the result to Out of date while retaining the exact
artifact identity. Undo did not restore removals made before the successful export. The existing
ordinary export service reopens the saved case into a new history baseline after publication.

At native 96 DPI a compact page row can visually join its filename and packet position. The
accessible row still exposes distinct source and assembled positions. Keep that row wrapping
visible as a presentation refinement.

The native Display settings at 1024×768 exposed 100% and 125%. A larger resolution offered by
Windows could expose a regular 200% mode. Such an observation would establish native 192-DPI
coverage. If the regular route cannot provide that mode, an installed application-scale reflow
check can support layout evidence within its own scope. The original guest display settings are
the state to restore after any temporary inspection.

At 1920×1080 the regular picker stopped at 175%. At confirmed 1600×1200 it offered 200%.
The exact installed B4 window reported native 192 DPI as the standard user. The workbench
wrapped its action bar and exposed the labelled Export, Pages, Document and Inspection
controls. At that width the three panes remain visible and some tab labels are elided. Ctrl+K
opened a readable native palette with the named search field and visible keyboard focus.
Inspection collapsed and the document canvas expanded at native 200%. The 200% evidence covers
this screen, controls and tested width. Windows was restored to 1024×768 at 100%, with the B4
window reporting 96 DPI. Undo from Planned restored the post-export action from 180° to 90°.

Automated palette-transition tests verified the contrast fallback. One temporary native Windows
contrast-theme selection through the ordinary Accessibility settings tests whether that fallback
also responds in the installed process. A readable result with visible focus would supply the
native observation. The original contrast setting is the state to restore afterward.

Windows' actual Aquatic contrast theme changed the installed controls to native styling, including
the cyan selected document view. The workbench and command palette remained legible with visible
focus. Some list and focus highlights retained TRACE colors, so full native color delegation
remains open. The original contrast setting, None, was restored. The first Start-menu notices
launch while the main app was active showed `NOTICES_APPLICATION_BUSY`. The wrapper then showed
its generic failed-exit warning. The existing application guard requires the main app to be
closed. A single retry under that condition tests the actual catalog and exit path.
After normal main-app shutdown, the actual notices shortcut opened the readable installed
catalog. Its normal close returned to Windows without the wrapper warning. The normal Start
shortcut then launched B4 again in the same standard user session.
The fresh process reopened `TRACE Installer Rehearsal` at revision 16 with saved marks, four
source pages and two assembled PDF pages. All three prior export digests matched. The offline
guest finished at the B4 workbench, 1024×768 at 100% scaling, with contrast theme None. The final
state receipt confirms cleanup of the owned scheduled tasks and helpers.

The normal launcher resolves to the single native TRACE window. The shipped file-picker and
desktop viewer integrations retain their Windows ownership. The case data model and existing
worker, parser, export and review authority remain the same. The private runtime comparison
and installed workflow evidence above are scoped to the named candidates. PaperWorkShell
continues to operate offline and independently of the ThirdLife portfolio.

### Contract scope at B4

TRACE's Exported view and comparison checks cover the latest ordinary or searchable PDF in the
active case session. Package and removal reports are available in their own workflows. Restoring
receipt context across application restarts remains open. The central Planned preview identifies
marks that need the existing editor and does not composite every saved PDF crop or mark. The
recipe describes active execution order, resolved page transforms, stable targets and supported
PDF replacement actions. Full engine-version and API-configuration inspection, and detailed
overlay, removal and package configuration in the recipe, remain open.

Portable recipes and PowerShell cover page selection, order, quarter-turn rotation and ordinary
PDF export. Supported processing passed in the installed guest under the standard user with an
existing sign-in. The guest script policy and S4U registration denial leave direct `.ps1`
execution and a logged-out batch run unverified there. The staged PowerShell 7 run preserved its
scope and evidence. The primary checkout and prior protected snapshots remain in place.

### Changed files and task state

- `src/paperworkshell/ui/window.py`, `ui/backend.py`, `ui/trace_*.py`, `ui/keyboard_help.py`,
  `ui/notices.py` and `ui/uninstall_review.py` implement the native presentation and action routes.
- `src/paperworkshell/app/organizer.py`, `app/trace_*.py`, `domain/trace_recipe.py`,
  `storage/trace_files.py` and `__main__.py` provide derived recipe intent, verified result
  binding, local recipe storage and bounded console execution.
- The three localization catalogs, `packaging/windows/product-v1.json`, `README.md`,
  `AGENTS.md`, `ROADMAP.md`, `TASKS.yaml` and `BUNDLE_MANIFEST.json` record presentation,
  engineering build identity and scoped task evidence. The PWS-0811 evidence remains separate.
- The focused TRACE unit/integration tests and affected keyboard, dialog and recovery tests
  verify current native routes and exact artifact binding.

PWS-0813 stays in progress for the remaining contract scope. PWS-0808 remains dependency-gated.

The authorized origin is public. This local branch includes private candidate ancestry,
so the private engineering work stays in the dedicated checkout.

## B5 native contrast follow-up

B4 kept TRACE colors on some selected rows and keyboard focus borders in Windows Aquatic.
TRACE compared the current palette with a newly derived palette after the theme change. A
change to an inherited color could prevent that comparison from clearing the TRACE overrides.
The new regression reproduces that mixed palette and verifies that an open workbench retains
selection and focus while clearing the overrides. TRACE now gives Qt only the colors it owns and
releases that palette when Windows contrast becomes active. Native focus styling still uses the
current platform palette.

The regression failed against B4 because its application highlight remained set, then passed
with the fix. The registered accessibility lane passed all 360 tests. The focused TRACE
workbench, package-window and installer unit suite passed 76 tests. Ruff formatting and lint,
strict typing, localization, schema, task graph, documentation and both boundary validators
passed. The accessibility lane ran with the dedicated checkout's `src` on `PYTHONPATH` so the
reused locked environment resolved this checkout's code.

The offline B5 build used source `445d948bb5eadc713752957988e5fd29c76f9db4`. The unsigned
512,950,123-byte Setup file has SHA-256
`eb7df78fdb99dcaec5d6723d1a4cfe3ed0146d4d338988462a808502eaa88b21`. Its read-only delivery
copy is `output/pws0813/delivery/PaperWorkShell-pws0813-trace-engineering-b5-win-x64-UNSIGNED.exe`
in the primary checkout. The offline build bound all 336 application files. Of 3,210 payload
members, the presentation module was the only changed runtime code. The installed marker,
component list and two source archives also changed.

The limited account replaced B4 through Windows Settings with cases preserved. All 868 entries
in the pre-install backup matched after B5 installation, including the earlier three TRACE PDFs.
All 3,210 installed payload members matched the build inventory. The normal Start shortcut
opened B5 and reopened the two-page retained rehearsal case. At native 96 DPI, Windows Aquatic
changed the selected page, its visible focus border, the selected recipe row, and the command
palette highlights to cyan `#8EE3F0`. The palette opened with Ctrl+K and focused its named search
edit. The workbench and command-palette captures each contained zero TRACE lime pixels in
Aquatic. Restoring Windows contrast to None returned both surfaces to their original TRACE lime
pixel counts in the same process. A third inventory matched all 868 entries after rehearsal.
The offline guest finished at the reopened B5 workbench at 1024 × 768 and 100% scaling, with
the R16 and R18 checkpoints saved. The setup and desktop helper tasks are gone. See
`output/pws0813/installed-trace-b5/contrast-pixel-receipt.json`, `final-state.json` in the same
directory, and `output/pws0813/desktop-evidence-b5` in the primary checkout. This check covered
Aquatic and None at 96 DPI. B4's separate native 192 DPI result retains its original scope.

## B6 saved Exported view

The exact B5 staged application showed the session-only empty Exported view on case open. B6
uses the last reference in the active manifest's export history and requires an exact version and
record digest match. The saved page count and structural checks come from history v1. Missing,
changed and unlinked records cannot supply restored context. A file chosen in Exported is checked
on the backend for exact byte size and SHA-256. Open exported PDF repeats that check against the
same selected file before the viewer handoff. A case close clears the selection. The reopened view
omits page mapping, current-recipe status and OCR findings because history v1 has no durable
evidence for them. Reports from an export completed in the current session still supply those details.

The focused TRACE, export desktop, history v1, Windows storage and security suite passed 103
tests with one Windows symlink privilege skip (1314). That suite covered missing and changed
records, unlinked records, same-size changed PDFs, missing selected files, cancellation, stale
completion after closing the case, and byte re-verification before viewer handoff. An existing
PDF export keyboard test now expects the page-count label used since B5. The registered Windows
accessibility lane passed all 360 tests. A separate offscreen run clipped the focused Next page
control in one enlarged RTL case. That case passed in isolation offscreen and in the full native
Windows lane. Ruff format check and lint, strict typing, localization, schemas, task graph,
documentation and both boundary checks passed. The offline build introduces no new dependency,
file association, case migration or public release interface.

The private `pws0813-trace-engineering-b6` Setup was built offline from
`a3246304da09dd98c095e53a2a8f408d4f997385`. The read-only unsigned installer is
`output/pws0813/delivery/PaperWorkShell-pws0813-trace-engineering-b6-win-x64-UNSIGNED.exe`
in the primary checkout. It is 512,956,400 bytes. SHA-256 is
`df972ef8877af617eea1c9930eebe2f04b0edb35d01cdf325634f12fb805bce1`. All 336 application
files matched the exact Git source and all 3,210 staged payload members passed verification.
The build receipts are under `output/pws0813/build-pws0813-trace-engineering-b6`.

### Installed verification

The product question is whether the exact successor restores saved v1 checks on the retained
rehearsal case and enables Open exported PDF only after explicit identity verification. B6
replaces the active B5 installation through Windows Settings with Keep cases. The retained exact
two-page PDF and a second valid 4,315-byte PDF with a different digest exercise the comparison.
An exact match must enable Open exported PDF. A digest mismatch must leave it unavailable. A
second process must require a fresh selection. Any receipt, identity or retention discrepancy
leaves the installed criterion failing.

B6 Setup ran in the offline guest's standard desktop session at elevation 0. All 3,210 installed
payload files matched the build. The actual Start shortcut launched the app and the retained
rehearsal case showed its two-page v1 receipt with seven structural checks. The named findings
list took keyboard focus. The native chooser cancelled back to the selection request. The
4,315-byte PDF with a different digest was rejected and the Open action stayed hidden. The
exact retained 4,315-byte PDF matched and enabled Open. The checks stopped at the enabled
control. The focused source test covers re-verification and viewer handoff. After normal close
and a second Start launch, Exported required a fresh file selection in the reopened case. At
1024 × 768 the chooser control scrolled into view on focus.

All 868 retained case, sample and export entries matched the pre-install backup after the
rehearsal. The B5 delivery hash still matches. R16 and R18 remain saved, and their active parent
was preserved. The offline guest finished at the reopened B6 Exported view under the standard
account in session 5, at 96 DPI with the original contrast settings and per-user file associations.
The owned helper tasks were removed. Final receipts are in `output/pws0813/installed-trace-b6`,
and screenshots and native accessibility trees are in `output/pws0813/desktop-evidence-b6`.

The B6 screenshot `output/pws0813/desktop-evidence-b6/capture-initial-picker-cancelled.png`
showed the current plan's shared `output page` summary below the historical Exported canvas.
That wording made a current-plan position look like saved mapping. The new visibility assertion
failed against B6 source. Commit `dead56ed4e695f7b83b5af3e8c01e7f382cb9b31` hides that summary in
Exported and restores it when switching back to Planned. The focused suite passed again with
the same Windows symlink skip, and all 360 native accessibility checks passed again. B6 remains
the one private successor built in this bounded step.
Installed verification of the label correction remains open.

## B7 completion verification

B7 contains the Exported label correction and the remaining comparison and inspection changes.
Planned and the organizer thumbnails apply a saved PDF crop to the bounded worker raster using
the saved quarter-turn coordinate transform. Source shows the unedited raster. The overlay and
removal editors use the same crop for preview geometry. Planned paints saved core visual marks
using the existing editor painter. The paint step bounds text and the aggregate clipped area
before drawing. Text sizing may be approximate. The page editors remain the review surface for
other marks and for cases where the preview cannot safely paint the saved marks.

The recipe inspector shows the selected operation's saved configuration. This includes overlay
placement and layer, unapplied removal intent, package limits and allowed output modes, and
configured PDF engine and profile requirements. Optional OCR requirements describe the processing
profile. Historical export checks continue to use history v1, and portable automation retains its
page selection, order, rotation and ordinary PDF scope.

The exact B7 source passed 242 focused unit, integration and security tests with one Windows
symlink capability skip. The registered native accessibility lane passed 360 tests. The
constrained thumbnail checks passed with the real Windows worker for rotated pixels, cache
behavior and dimension limits. Ruff, strict typing, localization, schemas, documentation,
task graph and both boundary checks passed.

The broader host sweeps started from the B6 baseline before B7 source changes. Inherited Git
configuration in the test process was corrupted when a Windows test restored an empty environment
value. The unmodified secret-scanning and synthetic-fixture modules passed with those inherited
overrides cleared for the test process. They supplied 55 and 20 passes, with two directory-link
capability skips. The renderer benchmark shared setup initially rejected a host CPython file
during guarded staging. The staging check passed on retry, along with all 58 nonlive cases in a
separate run. PWS-0207's frozen implementation-index comparison continued to fail on descendant
source; the divergence predates TRACE. The existing PWS-0207 evidence retains its exact scope.

The full B6-started unit run reported 8,055 passes, two skips, seven failures and eight setup
errors. In addition to the renderer and historical baseline findings, it exposed the three
obsolete-preview test assumptions repaired below and two preexisting preview-storage-capacity
contract failures in the worker image-normalize and searchable-export tests. The full integration
sweep was stopped at 37 percent after the affected live checks were identified. The affected
flattened export failure path passed in isolation on exact B8 source.

The initial broad security run included host worker recovery allowlist failures. Stopping the
integration sweep left one lease from its authenticated owner process. The same allowlist
rejection remained on a serial rerun. The production recovery API repaired the stopped test
lease using only its two independently authenticated disposable fixture roots. The exact B8
serial rerun passed all three live containment and isolation tests. The flattened-output bound
integration check passed separately, and the host recovery journal remained empty. See
`output/pws0813/host-worker-test-lease-recovery.json`. These results cover the affected worker
checks. The broad preliminary runs retain their original results.

The offline B7 installer uses commit `d8c4b8e261984e776e4bb7328f01aedc13b30908`. The read-only
unsigned delivery file is
`output/pws0813/delivery/PaperWorkShell-pws0813-trace-engineering-b7-win-x64-UNSIGNED.exe` in
the primary checkout. It is 512,967,046 bytes, with SHA-256
`ec3eab1ca59dcd11603222f46dc9544846293d490c44a3c441f38218fb106bbd`. All 336 application
files bound to the Git source and all 3,210 payload members verified. The receipts are in
`output/pws0813/build-pws0813-trace-engineering-b7`.

### Installed acceptance plan

Replace B6 through Windows Settings with Keep cases. Verify the new payload and normal Start
shortcut in the offline standard desktop. Reopen the retained mark and export rehearsal. Compare
Source and Planned, inspect mark and engine configuration, and verify the saved Exported checks,
explicit file selection and label correction. Run a fresh synthetic template and combined package
journey through incomplete and ready review, then inspect the saved output and its summary. Check
the changed inspector at native 200 percent if the approved route is available. Restore display
settings, inventory the retained B6 data and preserve the older candidates and checkpoints.

The installed B7 payload and both Start shortcuts verified. The standard-user app reopened the
retained two-page case and displayed the history v1 checks with explicit PDF selection. The
current-plan output-page label was absent in Exported. The exact retained PDF enabled Open
exported PDF and its verification stayed visible after switching views. Planned retained the
current-plan page summary and painted the saved text and checkmark on the rotated page. Source
showed the unmarked original. The recipe inspector took keyboard focus and displayed the saved
mark geometry and configured export requirements. Long engine and profile rows clipped at the
right edge at 96 DPI. The capture `output/pws0813/desktop-evidence-b7/capture-pdf-export-configuration.png`
records that failing layout. The successor's inspection check requires the complete long identifiers
to wrap at the narrow width, including the native 192-DPI layout.

The fresh synthetic package case imported four PDFs. The unassigned case received checklist
guidance and no package was created. After separate assignment and confirmation of all four
requirements, the native review showed Ready and the four-page combined plan. The worker
verified all four pages before publication. The native summary dialog saved its displayed
text after the filename was entered and the Save button invoked. An earlier automation
submission had failed to confirm the save and produced no text file.

Independent PDF reopening found no syntax findings or encryption. All page rasters, text,
dimensions and rotations matched the saved assignments in template order. The actual keyboard
selection assigned the fixtures in form, address, support and identity order. Those saved
assignments supplied the comparison expectation. The 1,274-byte combined PDF has SHA-256
`7b1d261c664e497cdb58f0d16bc5882367106abd7d1ffdcb20c77af9190dc4fc`. The 6,393-byte summary has
SHA-256 `7e185da40d0c3f9d1168978f4f7381232b84d15302a03acab925c10673616f41`. Both artifacts and
their independent receipt are under `output/pws0813/installed-trace-b7/package-output`.

The exact B7 console runtime processed the retained bounded recipe under a limited Interactive
task in the offline standard-user desktop session, without a TRACE GUI for the batch command.
It exited successfully, performed seven checks on two pages and preserved the input. The
786-byte PDF matched the earlier PowerShell and B1 output, with SHA-256
`364a77bc7603d6956d64d18f0280c89ab27170ac06aa2a6bcf815ed30a5dca95`. See
`output/pws0813/installed-trace-b7/interactive-batch`. A read-only batch-logon admission probe
for the existing account returned Windows status `0xc000015b` for logon type 4. The guest's
script policy and account rights stayed in place. A logged-out run remains unverified in this
VM. The supported bounded console path and the PowerShell transport have separate evidence.

## B8 configuration readability

The B7 configuration text clipped inside a narrow list item. B8 puts the same text in a named
read-only pane with vertical scrolling. Long profile identifiers wrap within the visible width,
and Tab leaves the pane. The narrow 260-pixel fixture with 20-point text reproduced the B7
problem and passed after the repair. It checks an actual profile token across multiple text
layout lines with zero horizontal scroll range.

Exact B8 source passed 243 focused tests with one Windows symlink capability skip and 360
native accessibility tests. The 33-module desktop route suite passed 425 tests on B7. Three
older thumbnail-race tests initially used the retired page delegate and assumed a fixed Qt
timer delivery sequence. The repaired fixtures drive the actual TRACE delegate and accept one
current request regardless of whether the event loop has already delivered it. All three pass
while still checking that obsolete results leave the current pixels intact. This test-only repair
followed B8 packaging. Ruff, strict typing, localization, schemas, documentation, task graph and
both boundary validators passed.

B8 `pws0813-trace-engineering-b8` was built offline from `acc0b2621259975c6d418807488ce239b338ac05`.
The read-only unsigned installer is
`output/pws0813/delivery/PaperWorkShell-pws0813-trace-engineering-b8-win-x64-UNSIGNED.exe`,
512,966,979 bytes, with SHA-256
`6b2dce059dc2ef2f02e8fc5206e5aacf285af472dfd281d7785881c9c809d33e`. All 336 application files
matched the exact source and all 3,210 payload members verified. The only changed installed
application source file since B7 is `ui/trace_recipe.py`. The installed marker, component list
and two source archives also changed. See `output/pws0813/build-pws0813-trace-engineering-b8`.

### Installed B8 verification

B8 replaced B7 through the standard-user Keep cases route. The verified 889-entry baseline
included the fresh package case, package PDF, saved summary and batch PDF. The installed app
reopened the earlier saved Exported view. At 96 DPI the named read-only configuration control
showed all PDF engine versions and wrapped the long render profile inside the narrow pane.
Native accessibility reported the exact control enabled, on screen and keyboard-focused. Tab
moved focus onward. See `output/pws0813/desktop-evidence-b8/capture-pdf-export-configuration.png`
and the direct focus receipts in that directory.

Windows reported native 192 DPI at 1600 × 1200. Ctrl+K opened the command palette with visible
focus in its named search control. A depth-limited accessibility tree led the first probe to
hide the inspector. The direct lookup then found the named Edit, but its first screenshot
showed only the top edge of the pane in the outer application viewport. Focusing the export
checks button and pressing Shift+Tab brought the configuration into view with keyboard focus.
The capture `output/pws0813/desktop-evidence-b8/capture-b8-focus-native-192-configuration.png`
shows all four engine versions and the full previously clipped render profile across wrapped
lines. The 192-DPI readability observation covers those visible rows. The enlarged-text
regression separately checks the text pane's wrapping and horizontal scroll range.

The guest returned to 1024 × 768 at native 96 DPI with its original contrast settings and tested
per-user file associations. The final offline session remained at the B8 rehearsal workbench and
retained all 889 inventoried entries. R16 and R18 are saved and the active checkpoint parent was
preserved. The owned helper tasks exited. Exact installed and retention results are under
`output/pws0813/installed-trace-b8`, including `final-state-verified.json` and
`post-native-state.json`. The verified final receipts preserve the earlier raw receipts and bind
the retained PDF digest to a fresh hash and the backup inventory.

### Task closure

PWS-0813 is done for the private native replacement. The representative installed evidence covers
the standard launch, saved cases, comparisons, package, bounded console runtime, keyboard and native
scaling. PWS-0811 remains in review. PWS-0808 and public release retain their separate dependencies.
The primary checkout and older immutable candidates stayed in place.

## Development baseline reconciliation

The clean baseline branch `codex/pws-0813-development-baseline` starts at closeout
`0919421eec02011d1483bdbacbd438af45e8a343`. The private B8 installer remains bound
to `acc0b2621259975c6d418807488ce239b338ac05` and the installed verification above.

The previous checkout at `2e70ec0e7174dab342f6df43d7b4d685f84a3259` is an ancestor.
Its source and test changes are present in TRACE history. The reconciliation brings
forward the distinct PWS-0811 evidence, including the November 26 B8 Add rehearsal,
and corrects the starter prompts. See the
[current PWS-0811 continuation](pws0811-repairs-20261106.md#current-continuation--installed-application).

The Windows checkout now follows the existing LF policy for the affected sources.
Explicit checkout attributes preserve the reviewed upstream notice and provenance
bytes. Earlier checkout work and installed evidence are still available at their
original locations.

### Repository checks on Windows 11 Pro x64

The reconciled source checkpoint is `d168db80b93164eb05312bff8adcefce3f3ba0bc`.
The B8 installer rehashed to the recorded SHA-256 and remained read-only. Comparing
the application source with the installer source shows only CRLF-to-LF changes.

- `py -3.14 tools/dev.py bootstrap` and `uv sync --locked --group renderer-evaluation`
  passed with CPython 3.14.6.
- `py -3.14 tools/dev.py check` passed formatting, lint, import boundaries and
  strict typing, then stopped at the unit suite. That suite began before the
  evidence commit and finished with 8,076 passes, two capability skips and three
  failures. The failures were the historical PWS-0207 source binding and two
  `PREVIEW_STORAGE_CAPACITY` contract checks in image normalization and searchable
  export. The three tests were repeated on the frozen source checkpoint. The
  documented PWS-0207 candidate receipt passed its assertion and the two capacity
  failures reproduced. The historical receipt remains available for its revision.
- The registered integration, security and accessibility lanes ran serially on
  the frozen checkpoint. Integration passed 732 tests with 43 skips. Security
  passed 1,329 with six link-capability skips. All 360 accessibility tests passed.
- The registered schemas, localization, dependency and release inventories, task
  graph, Pillow runtime, documentation, portfolio boundary, M1 report, PWS-0208
  workload, renderer benchmark and negative self-test checks passed.
- The shared Git repository failed `validate-fixtures` and `scan-secrets` at the
  existing 512 MiB logical-work limit. In a fresh single-branch clone of the
  reconciled checkpoint, `validate-fixtures` passed and `scan-secrets` reached the
  same limit. An isolated clone of `0919421e` also reached that limit during
  `scan-secrets`, which establishes that the failure predates reconciliation.

The command logs and the PWS-0207 candidate receipt are in
`output/pws0813/development-baseline-checks` in the primary checkout. The baseline
branch is clean. Shared promotion is pending because required checks failed.
Local `main` and `origin/main` remain at `060cc36`. PWS-0813 retains its private
installed closure and PWS-0811 remains in review.

The next bounded baseline step is to diagnose the unchanged scan-work limit
against the retained full repository using ADR 0021. Preserve its coverage,
history and limits. Record the result in this continuation and rerun the affected
checks if a narrow correction is found.
