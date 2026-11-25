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
