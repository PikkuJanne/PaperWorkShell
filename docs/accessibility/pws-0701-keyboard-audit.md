# PWS-0701 keyboard navigation and focus audit

Task: **PWS-0701 — Complete keyboard navigation and focus audit**. The binding
requirement is [PWS-D030](../../DECISIONS.md#pws-d030--accessibility-is-designed-from-the-first-ui-task).
This audit covers keyboard operation; NVDA completeness belongs to PWS-0702 and
the scaling/contrast review belongs to PWS-0703.

## Evidence boundaries

The interaction inventory below describes the production Qt widgets and their
keyboard alternatives. Automated widget tests establish action dispatch, saved
intent, focus availability and traversal. They do not establish that a person
can perceive every focus indicator. A desktop walkthrough must record actual
visible focus and keyboard interaction separately. Launching a probe is not a
pass, and a simulated backend response is not a document-processing result.

The accepted earlier M3, M4, M5 and M6 observations remain evidence for their own
revisions and artifacts. This task does not copy those passes onto new behavior
or ask the maintainer to repeat their reader/resave sessions. It does not enable
another redaction profile, change the one-page removal boundary, or establish a
packaged-release, PDF/UA, legal-validity or portal-acceptance claim.

## Interaction inventory

Tab and Shift+Tab move between available controls. Arrow keys select list items
and combo-box choices; Space operates a focused button or checkbox. Enter
operates the default dialog action; read its label before confirming. Escape closes an idle dialog or requests cancellation
where a long operation owns the dialog. Disabled and hidden controls are skipped.

| Workflow | Keyboard path and ordinary alternative | Focus and editing contract |
|---|---|---|
| Discover controls | F1, or Help → Keyboard guide. File menu lists the three export actions. Every shortcut also has a named visible action. | Help closes through its Close button or Escape; focus returns to the invoking workflow. Tab leaves its read-only text. |
| Choose a case | Case list arrows; Create new case, Open selected case and Refresh case list. Ctrl+N creates; Ctrl+O opens the selection. | Opening a case moves to its organizer; closing returns to the chooser. No double-click is required. |
| Add | Import document or Ctrl+I opens the ordinary file chooser. PDF, JPEG and PNG share this path. | Escape cancels selection without importing. Import failure exposes the safe message and a recovery action. During processing the visible Cancel action and Escape request cancellation. |
| Organize | Active pages arrows; Move earlier/later or Alt+Up/Down; Rotate clockwise or Ctrl+R; Remove from packet or Delete; Undo/Redo or Ctrl+Z/Y. | Page identity follows a reorder; no dragging is enabled or required. Removing a page leaves an available selection/action rather than an inaccessible removed item. |
| Change complete order | Change full order or Ctrl+Shift+O. In Proposed page order use arrows and Move earlier/later or Alt+Up/Down; confirm with OK or abandon with Cancel/Escape. | Movement to the first/last position must leave focus on a visible enabled control. The dialog has a finite forward and reverse Tab cycle. |
| Improve scans | Review scan adjustments or Ctrl+Shift+A. Select comparison view; use exact crop, rotation, deskew, brightness/contrast and other offered adjustment controls; Update preview, Reset to original, Apply adjustments and Cancel are buttons. | Preview panels have visible focus. Provisional edits remain local until Apply. Cancellation/error must retain a reachable retry or exit. |
| OCR settings | Manage OCR languages or Ctrl+Shift+L. Arrow/Space in the language list; use the visible selection, verification/import and cancellation actions. | Missing language data remains a remediation state. Numeric field editing and list navigation retain their native keys. No download is implied. |
| Quality review | Check page quality or Ctrl+Shift+Q; findings arrows; Enter or Go to selected page follows a finding. | Results need review; following a finding focuses the referenced active page. Cancellation does not confirm a requirement. |
| Fill | Edit visual overlays or Ctrl+Shift+F. Choose New visual form-mark type, Add selected type; select a mark in the native list; edit its exact value, font, color and geometry fields; Apply property changes, then Apply overlay changes. | Text entry owns Delete, Ctrl+Z/Y and ordinary caret keys. Text editing cannot remove or undo the selected document mark. Tab leaves fields. Return in a property field applies local properties. Alt+Down in a combo opens its choices. |
| Position and view overlays | Visible move/resize, zoom, fit, pan and snap buttons. Alt+Arrow moves; Alt+Shift+Arrow resizes; adding Ctrl uses the fine step. Ctrl+G snaps, Ctrl+Minus, Ctrl+Equal or Ctrl+Plus zoom; Ctrl+0 sets 100%. | Exact fields provide the complete alternative to the pointer canvas, including deskewed previews. View operations do not alter saved geometry. |
| Place/remove a signature image | Place signature image or Ctrl+Shift+S; choose JPEG/PNG; preview and exact placement controls; explicit Place/Cancel. Remove signature image or Ctrl+Shift+D selects a placement and an explicit retention/removal choice. | The disclaimer and relevant choices are keyboard reachable. Placement is a visual mark, with no digital-signature claim. |
| Protect: saved marks | Permanently remove information or Ctrl+Shift+P. Add region (Alt+A), select its list row, edit exact full-page geometry, apply geometry, remove, undo/redo, Save marks or Cancel. | The canvas is not the sole editing surface. Saving intent does not apply removal. Spin-box text editing must not invoke draft-level undo/redo. |
| Protect: derivative | Export removal derivative or Ctrl+Alt+E. Review warnings; Marks reviewed and, when required, Unsigned derivative; Choose new file. The report offers its exact concrete outcomes and Close/Cancel. | The safe initial action is Cancel. The current supported path is one active affected PDF page without overlays/scan cleanup. Required verification and independent release approval remain mandatory. Closing the report returns to Permanently remove information, the accepted Protect-entry action. |
| Checklist | Review packet checklist or Ctrl+Shift+C. Choose category and unassigned pages; Add selected pages; Confirm added document. Select a document to accept suggestions, move categories, remove assignment or reorder its pages with buttons. Apply checklist changes or Cancel. | Adding/moving alone does not confirm satisfaction. Every drag-style assignment/reorder has a list/button path. Undo/Redo are local to the draft. |
| Constraints | Review package constraints. Restore generic template defaults or edit allowed modes, encryption policy, optional limits and requested filename stems; add/update/remove/move names with buttons. Apply constraint changes or Cancel. | Editing filename text owns text-entry shortcuts. Validation keeps the invalid draft and a reachable correction control. No real portal rule is inferred. |
| Check/export package | Review and export package or Ctrl+E. Choose output format; read/select summary; Refresh review; explicit human and unsigned-derivative confirmations; Export package; Cancel current operation; Save summary; Close. | Read-only summary supports ordinary selection/caret/scroll keys, including Ctrl+Home/End, and Tab leaves it. No acknowledgement is inferred from opening the review. Closing returns to the organizer export action. Known package codes show fixed safe causes and relevant format/requested-name/constraint recovery; unknown errors stay generic and no error establishes the destination state. |
| Size reduction | Preview size reduction inside package review. Grayscale choice, Prepare preview, affected-page list, comparison radio buttons/panels, explicit risk acknowledgement and Export reviewed candidate. | Exact before/proposed views remain keyboard reachable; cancellation stays available during work. No target-size success is inferred from preview. |
| Ordinary PDF export | Export pages as PDF or Ctrl+Shift+E; choose a new destination; explicit unsigned-derivative confirmation if required. | Canceling the chooser does not publish. Progress exposes Cancel; failure returns to Export pages as PDF for retry. |
| Status/recovery | Visible status/error text, Cancel current operation, Retry selected preview (Ctrl+Shift+R), Retry saving changes and Retry closing PaperWorkShell. | A focused transient control must not disappear or become disabled without an available focus destination. Recovery does not require a pointer or expose raw document/library values. |

The preview canvases intentionally have no Tab stop where all interactions are
provided by the adjacent list, exact fields and visible buttons. Image-comparison
panels are focusable so a keyboard user can scroll to and inspect both views.
Lists, buttons and intentionally focusable scroll containers show a dashed frame
when focused, including after a programmatic return. Focused controls in owned
scrollable dialogs are brought into view after the layout settles.

## Automated coverage and failure paths

Run the repository entry points from the repository root:

```powershell
.venv\Scripts\python.exe -m tools.dev test-accessibility
.venv\Scripts\python.exe -m tools.dev format-check
.venv\Scripts\python.exe -m tools.dev lint
.venv\Scripts\python.exe -m tools.dev typecheck
.venv\Scripts\python.exe -m tools.dev check-import-boundaries
```

The accessibility suite covers names/roles, explicit Tab orders, keyboard
activation, enlarged text and operation focus. The PWS-0701 regression cases add
actual key events for the defects found by this audit; the task result records
the precise files and results. Relevant desktop integration tests exercise
backend release, autosave/reopen and recovery. Tests with a presentation-only
backend remain labelled as such.

At least one error, one cancellation, one boundary reorder, one text-entry
shortcut and one forward/reverse traversal must be exercised. Passing dispatch
tests alone is insufficient evidence for visible focus or a successful export.

## Full keyboard walkthrough

This is an **agent-operated developer script**, not a finished human handoff or
an instruction to the maintainer to assemble a test workspace. Before any human handoff, apply the
[human-testing policy](../development/human-testing.md): freeze and test the
entry point, prepare synthetic files and output destinations automatically,
provide explicit Copy/Open-folder actions, and checkpoint each observation
immediately. A missing receipt or unrun observation remains unknown. Do not ask
the person to type a path, edit JSON, calculate a hash or repeat known versions.

The stable developer entry point is:

```powershell
.venv\Scripts\python.exe -m tools.pws0701_keyboard
```

Use `--prepare-only` to prepare and validate without opening the desktop. It
copies admitted synthetic fixtures into `output/pws0701-keyboard/inputs`, creates
an isolated `profile` and `exports` directory, and validates the exact inputs
when reused. It never deletes or replaces existing case/export state. The
`inputs-v1.json` receipt records only synthetic fixture sizes/hashes and explicitly
says observations are not recorded. Changed inputs or reparse points fail closed
with a sanitized error. This is an agent-operated developer launcher; it has no
human observation recorder or Copy/Open-folder controls and must not be handed
over as a finished human helper without those policy requirements being met.

| Prepared input | Existing admitted fixture | Use |
|---|---|---|
| `packet.pdf` | `pdf/ocr-readable-mixed.pdf` | Two-page mixed packet |
| `support.pdf` | `pdf/born-digital.pdf` | Additional packet page |
| `scan.png` | `image/ocr-raster-text.png` | Image intake and scan review |
| `signature.png` | `image/transparent.png` | Synthetic signature-image interaction |
| `removal.pdf` | `pdf/redaction-born-digital.pdf` | Separate supported one-page Protect case |
| `invalid.pdf` | `pdf/malformed-truncated.pdf` | Import failure and recovery |

Importing `packet.pdf`, `support.pdf` and `scan.png` creates the four-page mixed
packet. The launcher opens the normal case chooser; it does not create cases,
confirm categories, bypass import or pre-record a result.

Prepare two isolated cases and one empty case with production persistence. The
agent prepares these cases through ordinary production services before the
relevant phase. The main case has synthetic packet targets, an image page for scan review and
three confirmed categories; leave one category unconfirmed so the operator can
exercise the complete assignment/confirmation path once. The separate Protect
case contains one active supported PDF page, no overlays/scan cleanup, and a
large labelled region target. The empty case is for Add. Prepare invalid and
valid synthetic import choices and new, empty export destinations. Preparation
must not pre-record any interaction result.

Use the normal production desktop/worker for document-processing claims. A
presentation probe may exercise dialog keyboard behavior, but record that
boundary and leave production processing unknown. Do not turn a mocked success
into export evidence. The current user profile and real cases remain untouched.

The seven phases below are read one at a time. The operator records **Observed**,
**Problem** or **Leave for later** after each phase; each problem is recorded
immediately before continuing. Record the exact implementation/fixture bindings,
environment provenance, operator kind, focused destination and observed result
automatically where available. Expensive PDF/OCR checks run separately from the
recording interaction.

| Phase | One coherent activity | Expected outcome |
|---|---|---|
| 1. Discover and Add | Open Help with F1, use Tab/Shift+Tab to reach Close, then close with Escape. In the empty case use Ctrl+I; cancel once, import the invalid synthetic choice, then retry with the valid choice. | Help and file selection return focus. The failure is recoverable and the valid import adds pages without touching the source. |
| 2. Organize | In Active pages use Alt+Down, Ctrl+Z, Ctrl+Y and Ctrl+R. Open Change full order with Ctrl+Shift+O; move a page to a boundary with the visible button, then cancel the draft. | Selection follows page identity, visible focus survives the boundary, and cancel preserves the saved order. |
| 3. Improve | In the prepared main case select the image page. Open scan review with Ctrl+Shift+A; Tab through a preview and exact adjustment field, change one value, update the preview, then cancel. Open OCR settings with Ctrl+Shift+L and leave with Escape. | Focus is visible on previews/fields; Tab escapes each field; cancellation returns to a useful organizer action and keeps saved content unchanged. |
| 4. Fill | Open Edit visual overlays with Ctrl+Shift+F. Add Text; enter the synthetic label; edit with Delete and Ctrl+Z, then use Apply property changes. Move once with Alt+Arrow; Apply overlay changes. | Text keys edit the value rather than deleting/undoing the whole mark. Geometry has a complete pointer-free path; saving returns focus to the invoking action. |
| 5. Check | Open the checklist with Ctrl+Shift+C. Assign and confirm the one outstanding category, then apply. Open package constraints with its visible button, inspect a filename field and cancel. Run Check page quality, follow a finding if one exists, then open package review with Ctrl+E. | Assignment needs explicit confirmation. Text entry remains local. Findings are review signals; focus follows the chosen page or remains on the available quality action. |
| 6. Export | For the supported completion composition, remove the scan page reference, import the prepared PDF replacement, replace/explicitly confirm Supporting evidence and allow Combined PDF through ordinary controls. Retain originals and Main12 evidence. Read the package summary with Ctrl+End, Tab out, explicitly acknowledge and export the four PDF pages plus saved overlay to a fresh folder. Require the terminal committed result. Open Save summary, cancel once, reopen and save; Close review. | Actual four-page Combined PDF is saved/reopened/verified. Save summary cancellation returns visible action focus, the actual text is saved, and review Close returns visible focus to Review and export package. Original Main12 Separate timeouts remain failures; progress alone is insufficient. |
| 7. Protect and leave | Open the prepared one-page Protect case. Use Ctrl+Shift+P, Add region, exact geometry and Save marks. Use Ctrl+Alt+E; Tab through warnings and confirmations, choose the prepared new destination and observe the report, then Close. Close the case and reopen it through the case list. | Saved intent remains distinct from applied removal. Required checks cannot be bypassed. Report text can be traversed and left; focus returns to Permanently remove information. Reopening preserves the case. |

An operator must observe focus continuously, including the return from every
dialog; a keyboard action without visible focus is a problem. The script uses no
dragging or pointer actions. If desktop activation itself needs a pointer,
record that exception and do not describe the run as entirely pointer-free.

### Whole-journey burden and recovery

The prepared journey uses one desktop application and no external readers or
devices. The planned coverage includes a main package save, a text-summary save, a
one-page removal save and a case reopen. The initial session completed the removal
save/reopen and a separate ordinary PDF export; main package and summary saving
remained unmet. Reader/resave, OCR extraction,
cryptographic and resource-budget checks are automated or retained in their
separate task evidence; the operator does not repeat them to improve the helper.

The original **130–180 input / 12–18 active-minute estimate is withdrawn**. The initial session recorded **369 measured input events and zero pointer actions**, including fix rechecks, error recovery and the bounded environment attempts. Earlier checkpoints were 262 at v5 close, 332 at v6 close and 352 through v7b; initial original-workspace actions were not fully counted. Literal text sends and chords have different instrumentation granularity. Human active time was not measured and cannot be inferred from worker wall-clock duration. The launcher/agent recorder is not a human-ready helper; redesign, test and freeze a policy-compliant whole journey with measured total actions and active time before any human handoff. No shorter estimate or human outcome is invented.

The launcher and agent recorder remain agent-only. Before any required human/NVDA handoff, redesign, freeze and test the complete journey under the human-testing policy, measure its total actions and active time, provide the required Copy/Open-folder and fast persistent recording/recovery actions, and minimize repeat work. A later human assessment requires new observations and cannot inherit agent results.

On interruption, preserve all completed observations and resume at the first
unfinished phase/action. A missing output must offer the same prepared choice
again without deleting earlier records. A material code/fixture change retains
old evidence and invalidates only affected observations. Do not record a pass
for an unrun or stale phase.

## Result and source-bound evidence

**Status: done.** The prospectively revised supported four-PDF-page composition completed the actual Combined package and Save summary. Primary-action coverage is cumulative across the preserved earlier observations and the new closure epoch. Exact-final scanner tests and the complete canonical-root scan pass with unchanged limits. Main12 processing timeouts remain an export/resource qualification limitation.

The final layout has **187 accessibility tests passing**; one pure alias-contract case was moved unchanged into `tests/unit/test_package_export_composition.py`, whose affected module has **29 passing tests**. The affected window/dialog/helper unit selection has **110 passing tests**, relevant lifecycle integration **16**, and selected privacy checks **20**. Strict typing passes 779 files and import boundaries pass 277 Python source files. Counts refer to their recorded scopes; selected checks are not complete suite passes. The [task result](../development/pws0701-result.md) records commands, historical failures and receipts.

The automated regressions cover visible traversal/focus, shared-window Tab initialization, list/scroll/button focus frames, focus reveal without combo-popup or unrelated-window interference, history/filename boundary recovery, ordinary text/numeric Delete and Undo/Redo, report Ctrl+Home/End and Tab escape, pending save/failure/retry, Fill Add Text scroll reveal, property Return without accidental zoom/save, and standard combo Alt+Down. Known package failures are rebuilt from exact trusted error types and fixed closed-code messages. Unknown types/subclasses stay generic; safe guidance cannot establish destination publication. The pure error contract and all other export implementation AST remain unchanged, with the existing export imports preserved as exact aliases. Independent focus and error/privacy reviews pass; actual native observations remain separate.

### Initial native observations and historical package gaps

The agent-native record is `output/pws0701/native-keyboard-observations.json`. Its preserved initial-session SHA-256 is `8f0b7c754f4944e31ea3f68e05c34afddfdd45d954b3fdcb4094713fbfc0720b`; all 45 historical observations remain preserved with status `complete_with_problems`. The final 369-input / zero-pointer action receipt has SHA-256 `745b2f906ea12f78270eebea18514bccf81bfb6d455808666fb550a407f2d230`. Native Add, Organize, Improve cancellation, Fill, checklist/constraints and quality navigation have recorded observations. Quality produced Needs review findings, including an OCR probe without a verified result; no OCR certainty or requirement satisfaction follows. Accurately labelled unavailable source-preview fallbacks are not rendered-preview passes.

Native v4 found offscreen Add Text focus and property Return activating an unrelated zoom action; a separate widget audit identified the combo Alt+Down shortcut conflict. Native v5 rechecked the corrected value reveal, local Return behavior, ordinary combo popup, Help/report navigation, scroll focus and constraint-status reveal without persisting temporary Fill changes.

Native v5 completed the actual one-page Protect derivative: `REDACTION_EXPORT_COMPLETE`, with RV_CLEAR for all six groups (objects/revisions/metadata/attachments/forms/actions; text selection/search/extraction; image extraction; page rendering; save/resave; independent OCR). Exact output SHA-256 is `33a6cc1dbfee6fca8d7fe940e5f0ced6e22090e9046fbd9a00481ac0355b8e8c`. The report recorded save/reopen; summary/detail/Ctrl+End/Tab exit were keyboard usable. Close returned visible focus to **Permanently remove information**, matching the accepted entry-action contract. Closing/reopening preserved revision 2 and one active page. This is evidence for that exact synthetic artifact and v5 source; the named-viewer matrix, human/NVDA, packaged-release and legal-validity claims are excluded.

The default Combined PDF attempt on the four-page mixed image/PDF packet with a text overlay failed with a generic destination-unknown status. The selected existing empty folder was the intended chooser input; it stayed empty. Original failure remains recorded. A read-only metadata/path diagnostic (`output/pws0701/package-route-diagnostic-v1.json`, SHA-256 `f245c92d441d44dcd87ca6dc57023ba73df5b381f20218e6ad6173b9cabdc360`) found: directory constructor admission passes; Combined flattened planning rejects the image, then searchable planning rejects the overlay. Static translation explains likely generic display but does not capture the native exception. Clearing the single custom filename and choosing Separate PDFs yields supported pure routes while preserving every page/overlay. Native v6 observed the improved safe error guidance, then saved ordinary constraints only and retained Main revision 12's four pages and text mark. Its Separate attempt advanced to 3/4 before the configured timeout; inspected destination stayed empty and no terminal PackageSummary existed. Fresh package inspection cancellation completed and restored visible focus. The v7b normal-runtime retry also reached 3/4 then timed out with an empty destination. The final declared-runtime v8 review completed 2/2 inspections and creation/verification progress reached **4/4**, then ended with **PACKAGE_EXPORT_TIMED_OUT**. Its fresh destination was empty after cleanup, Main remained revision 12 and no terminal PackageSummary or Save summary was available. **At that original-session boundary, Main package completion and native Save summary were unmet. The later revised Main16 completion is recorded separately below; Main12’s timeout remains a failure.** Pure planning and progress are not publication success.

Native v6 separately completed **ordinary one-page PDF export** through Ctrl+Shift+E from the retained Add case, independently of checklist/package state. The UI reported verified save and history recording, Add advanced to revision 2 and focus returned to Export pages as PDF. Exact output is **744 bytes**, SHA-256 `e255e4aa2fe57f955cb9096490e31b1801f3a97ac568870991f7a6ad0fb957cf`. This success does not cover the failed package or Save summary.

The static deadline/summary diagnosis (`output/pws0701/package-timeout-static-diagnosis-v1.json`, SHA-256 `e60d55735c303b044030f3348da3f48389c9f4fd6b0319bbffb37388bb4bf4e9`) shows one cooperative whole-package 300-second expiry and fresh one-shot worker processes per PDF despite retained composition objects. Worker/profile and post-launch handshake budgets are separate. A fixed timeout message does not identify the exact firing phase, and no empirical ACL-causality or performance claim is made. Before publication the candidates are private stages; failure cleans uncommitted stages. Ready/exception-failed review has no terminal summary authority, so the existing UI cannot truthfully save a planned summary. No forged summary or budget change is proposed.

V5 source snapshot is `dd24c66e89939992024beab34c42925eea5b1d0d211ce10017e86d56cea65306`. After clean shutdown, v6 admitted the pure error-contract/UI changes and preserved all 24 existing synthetic input/case/cache/export files; its snapshot is `b329c48643102709417975fe02259e30acf75d09177d4395f7f409f26e788bcc`. Epoch, launch and preservation receipts in `output/pws0701-runtime/` retain exact source bindings. No live stage was edited; prior processing/observations are not relabelled as final-source passes. Final normal native shutdown and the strict preservation check completed before release of the full unit queue; complete unit PASS and scanner FAIL are separately recorded below, with final metadata outcomes in the closure receipt.

The earlier owned v6 whole-app QA environment had **48 distributions / 8,521 authenticated environment resources**, including optional evaluation-only Addons; its Separate attempt timed out at 3/4. V7 used the README ordinary default locked install, **`uv sync --locked --offline`**, with **47 distributions / 7,155 authenticated environment resources**; only optional Addons was absent and all retained versions matched. Base Python remained **4,026 resources**, source **303 resources / 285 frozen production files**. Its byte-copied profile failed normal startup `RECOVERY_SCAN_FAILED`; read-only admission proved `WORKSPACE_PERMISSIONS_INSECURE` at `permissions.inheritance`, while the original secured v6 profile passed. The failure demonstrates the private-directory guard; no ACL repair, data migration or bypass was used. V7b used that same secured application-data workspace through the public helper workspace argument, with the new ordinary install/code kept separate. Its fresh Separate attempt still timed out at 3/4, leaving an empty destination and no terminal summary. Earlier environments, failed copied-profile bytes and all outcomes remain preserved.

V8 used a normal whole-project declared-runtime install, **`uv sync --locked --offline --no-dev`**, with **30 locked distributions**, distinct from the README default developer install. Static source imports map to that closure and ordinary desktop-module import succeeded. Production authentication admitted **5,066 environment / 4,026 base-Python / 303 source resources** and verified the same 285 production files, retained versions, exact qpdf binary/version and all 40 admitted Tesseract files. Receipt `output/pws0701-runtime-v8/authentication-v8.json` has SHA-256 `19e82595f746b9191319cb1e7475a691ba7c23d6219a603e83bcde1a853e0754`; source snapshot is `2ad4f4bb851978fb9720336510a1a0255836680022a22a8a8f3de2fddbce3045`. The normal factory derived and authenticated its entire environment/base roots, and the public workspace argument reused the existing secured profile. There was no curated worker closure, test/private resource substitution, ACL repair, processing or numeric-limit change. The final actual attempt timed out after 4/4 producer progress; its empty destination and unavailable terminal summary leave the required package/summary coverage unmet. That original session ended there; the later closure uses the same environment and revised supported packet, recorded below. A smaller count does not establish speed or empirical timeout causality.

The original workspace and all earlier epochs remain preserved. Claims are limited to **no manual ACL/journal alteration or containment bypass**: earlier production inventory APIs may themselves clean unpublished worker stages. The final exact helper was **`output/pws0701/post_native_audit_v6.py`**, invoked with Python `-B` and `--run-after-native-exit` after normal v8 shutdown and before full unit release. It returned **PRESERVATION_PASS**, exit 0, using existing-path checks and strict read-only `scandir`, with no ensure/inventory/recovery APIs, grants, source/case edits or deletion. At that boundary it found zero owned GUI/worker/OCR processes and zero resource-journal entries, verified all 285 source files in root/v6/v7/v8, six fixtures and original/prior-output hashes, and preserved all 29 failed copied-profile files. Case revisions were Main 12, Protect 2 and Add 2; temporary directories and failed destinations were empty. No package/summary output existed. Receipt `output/pws0701/post-native-preservation-audit-v6.json` has SHA-256 `9b8b055fd4b88e0e265c338103f43bd6cdf50cde1456c5c16dc4529dde26584c`; its exact archived observation binding is `output/pws0701/native-keyboard-observations-bound-to-post-audit-v6.json`, SHA-256 `8339d9209115f4f3166df3947680404324447f5d9aaa189929ef644c36f756fb`. This preservation result does not satisfy the missing package/summary observations, reverify PDF contents, or assert no test workers after the unit queue resumes.

### Initial QA and gate limits

Production source checkpoint `13bea12d8d843155b2b8452a7ef1c4f440e9b6b4` has genuine baseline evidence `f29a8ab4c6c6e891b950c5e149e23029df379448`; the later test-placement-only QA HEAD is `08a578ffdac46a52d65bcca9930e2d0e0dd11e2b`. The actual baseline SHA-256 `115b74f2fd7e9116e76f7cbfc1e97f51822b71a62aa76825bd181a4364c4fcc9` differs from the original baseline only by revision. The unchanged validator accepts it after the test move, without regeneration. Root HEAD/index/refs/original baseline and unrelated excluded files remain untouched. The owned checkout preserves complete own history/refs with exact canonical raw source bytes plus only this genuine generated baseline; original workspace-specific source/index or owner failures are kept separately.

The historical root full-unit run failed its old source/index baseline binding (6,647 passed, 1 failed); it remains historical. The final complete QA unit command passed **6,649 tests in 776.79 seconds**, including all 40 baseline-evaluation cases, without skips or selection. The complete unchanged QA secret scan failed `SECRET_SCAN_INPUT_TOO_LARGE` in 353.860 seconds at its 512 MiB logical-work limit, not a runner timeout. Both commands passed all 10 before / 6 after source/Git preservation checks. Receipts are `output/pws0701-qa/verification-final-source-v5/test-unit-final-v5-1.json` (SHA-256 `908606866934fe447aeeadfdba2fd9f0b3adf48f78b60d0e2275ea0f65a40b95`) and `scan-secrets-final-v5-1.json` (SHA-256 `424dd75a495032a01a303b85abc17d0bb15eaeb05afbbecc1801be85cda678c6`). Production revision `13bea12d8d843155b2b8452a7ef1c4f440e9b6b4` and test-only QA HEAD `08a578ffdac46a52d65bcca9930e2d0e0dd11e2b` remain their authenticated inputs. Original complete-root/all-ref failures and the scoped historical public-history pass remain separate; no source/test/fixture normalization, history narrowing, budget increase or suppression was applied.

Initial metadata validation and fifteen-path outcomes remain in `output/pws0701/closure-validation-v1.json`; that historical scoped check did not replace the failed initial full scan. The later genuine full-root PASS and authenticated four-file completion delta are recorded below. PWS-0701 is now done; PWS-0702/PWS-0703 are ready, with PWS-0702 next.

### Completed representative closure

The export phase above was revised prospectively for the maintainer-requested closure. Main12’s PNG was removed only from the packet, its original retained, a prepared PDF reimported, the stale Supporting assignment replaced and explicitly confirmed, and Combined PDF allowed. Main16 retains four active PDF pages, the saved text overlay, rotation/order intent and four confirmed categories. No runtime, resource-policy, verification, source-authentication or containment change was made.

The actual three-source inspection completed, followed by a terminal four-page Combined package save/reopen/verification. The operator traversed the terminal summary, cancelled its native save chooser, observed visible Save summary recovery focus, reopened it and saved the text. Closing review restored visible package-action focus; Alt+F4 closed normally. This completes the outstanding primary keyboard action coverage using genuine production services. Earlier Add, Organize, Improve, Fill, Protect and Check observations remain bound to their original recorded epochs; there is no claim of a single uninterrupted run, human/NVDA testimony or Main12 Separate success.

The new immutable native record is `output/pws0701-closure/new-native-observations.json` (70 input API calls, 15 observations, zero pointer actions). Its pending-independent-audit status describes the recording boundary; `post-native-preservation-v1.json` supplies the later PASS. The 26,223-byte PDF has SHA-256 `98ebb6e1ca4e50977a337c6849b8c2e376ca35667b03c99a2b6527d60d447d3f`; the 6,559-byte summary has SHA-256 `e0b2bf05dbb8d17c50c2782229bd80f188915de19e859f92a446c7df1b9c4209`, matching the selected terminal body plus the renderer’s final LF. Prior originals/exports and 285 production files were preserved; journals, owned processes and temporary files were zero at the strict post-exit audit boundary.

Final checks pass: 187 accessibility tests, 146 focused quality-tool units with 16 subtests, the complete exact-final scanner security module, quality self-test and static/canonical validators. The existing source-bound 6,649-test full unit pass remains valid for unchanged production bytes and is not represented as a new full-unit run. `output/pws0701-closure/full-root-scan-v1.json` records a genuine complete canonical-root scan PASS, covering every ref/index and tracked/nonignored working source without changing the 512 MiB limit. Raw scanning reuses existing immutable Git bytes while all path/alias/ref coverage and mutation checks remain. Original full-scan failures stay failures. After that pass, only four completion metadata files change and are incrementally authenticated, validated and scanned in `final-metadata-scan-v1.json`.

The initial 45-observation record and 369 measured inputs remain unchanged; the closure adds 70 input calls. Neither per-character keystrokes nor human active time is inferred. The agent launcher remains unsuitable as a finished human handoff until the existing human-testing policy is met. Main12’s supported Separate timeout and the separate mixed-image/overlay Combined planning limitation remain documented export/resource qualification risks for PWS-0807. This does not gate the action-based PWS-0701 completion or waive later release qualification. PWS-0702 and PWS-0703 are ready; PWS-0702 is next.
