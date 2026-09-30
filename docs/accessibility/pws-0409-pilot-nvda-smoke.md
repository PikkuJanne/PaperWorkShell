# PWS-0409 desktop, NVDA, and failure-path observations

- Date: 2026-09-30
- Operator: coding agent using Windows computer-use controls and NVDA Speech Viewer
- Product revision: `248c26ed56679b5bd4ba3faadc13c2410347960f`; product source unchanged during this gate
- Host: Windows 11 Pro, 10.0.26200, 64-bit
- Runtime: CPython 3.14.6; PySide6/Qt 6.11.1; NVDA 2026.1.1
- Resource profile: `CONSTRAINED_RESOURCE_POLICY_V1`

## Scope and setup

This is an agent-operated desktop observation, separate from the maintainer's
accepted PWS-0408 first-time-user/keyboard walkthrough. It is not human NVDA
certification, a PDF/UA claim, or a repeat of that accepted walkthrough.

The existing PWS-0408 launcher prepared four large synthetic pages and an isolated
case profile under ignored `output/`. A copy included a synthetic `/FT /Sig`
field. This exercises conservative signature-marker detection, not cryptographic
signature validity. Two separate invalid PDFs exercised preliminary framing
rejection and restricted-worker parsing. No applicant documents were used.

The ordinary production desktop, backend, storage, dialogs, and worker composition
were used. The test supplied copy-owned CPython/code/dependency resources through
the existing Windows integration staging helpers. The final session used
`tests.integration.test_flattened_pdf_export_worker_windows._stage_python_closure`,
the same exact locked dependency staging used by the real package pilot. It
retained production AppContainer, ACL, Job, RPC, verification, and cleanup controls.
Only trusted executable/resource locations were substituted.

After the initial UI import and clean close, normal backend methods restored generic
constraints and persisted four confirmed category assignments. This was test-data
setup; category assignment is not claimed as a new manual observation. The reopened
production summary displayed all four persisted assignments. Their human workflow
is covered by the separately accepted PWS-0408 evidence.

NVDA used a separate attempt-local configuration with add-ons disabled and Speech
Viewer enabled. The user's NVDA profile and Windows display settings were unchanged.
The Qt process used `QT_SCALE_FACTOR=1.6`; this record claims enlarged Qt text,
without inferring a measured device-pixel ratio or a Windows scaling setting.
Keyboard actions included Ctrl+O, Ctrl+I, Ctrl+E, Alt+S, Tab, Shift+Tab, Space,
Enter, Escape, and Alt+F4. Pointer actions activated/repositioned the test window
and one case-opening control. This supplemental session is not described as an
entirely pointer-free first-time run.

## Observed outcomes

| Scenario | Observation |
|---|---|
| Preliminary malformed framing | A PDF without trailing EOF framing was rejected before worker inspection. NVDA announced the existing generic “The selected source file is not available” message and the Import action. An empty case stayed empty; a later valid import succeeded. |
| Valid signed-marker import | The restricted worker imported the synthetic four-page packet. NVDA announced copying, inspection, commit, completion, and four active pages. Persisted source state was `markers_detected`. |
| Restart and persistence | Clean close and fresh desktop launch reopened the same case. Four pages, confirmed assignments, and later explicit unsigned-derivative consent survived. No final package result was incorrectly reconstructed as a durable history entry. |
| Package review | Ctrl+E opened the production dialog with Close as the safe default. NVDA read the dialog's purpose, selectable read-only summary, control instructions, and package status. The summary distinguished planned counts from unverified size/check facts. |
| Consent refusal | Export remained disabled after acknowledging human review while unsigned-derivative consent was still unchecked. No destination was selected or file published in that state. |
| Signed-source consent | Tab reached the unsigned-derivative checkbox. NVDA read its unchecked/checked states and the consequence: modified output will be unsigned; original signatures do not remain valid; the action does not validate or create a digital/qualified signature. Space enabled export only after both acknowledgements. |
| Restricted-parser failure | A separate 59-byte input with valid header/EOF framing reached the worker and failed inspection. NVDA announced “Document inspection did not provide complete supported source metadata,” then the Import action. The saved case remained at one source, four pages, revision 4. |
| Verified export after failure | The valid packet exported through the production package path. The final summary reported one saved/reopened four-page PDF, 3,545 bytes, no encryption, clean qpdf structure, four-page render/order/rotation checks, destination byte verification, and unsigned-derivative status. Unavailable compatibility, geometry, and quality facts remained review items. |
| Completion and text companion | NVDA read “Package export complete. Review the saved files and remaining human checks in the summary.” Tab reached Save summary and its sharing guidance. Saving a new UTF-8 text companion announced “The displayed summary was saved. Review it before sharing.” The upload inventory remained one PDF. |
| Active cancellation | In a separate export using the complete staged dependency closure, Escape was pressed after creation started and progress displayed 0 of 4. NVDA announced the cancellation request and that it would wait for the final result. The dialog remained open while stopping. The final message conservatively said export did not finish safely and the destination state was not established by that message; it instructed inspection and a fresh review. The destination was independently confirmed empty. |
| Recovery after cancellation | Escape closed the terminal review; Ctrl+E opened a fresh review with four pages, zero files saved by the new attempt, all four confirmed categories, and a newly required human acknowledgement. Both test applications then closed normally. |

The speech observations came from NVDA Speech Viewer, not merely Qt accessible-name
inspection. The summary/control values were also inspected through Windows
accessibility output. No raw desktop screenshot or speech trace containing local
paths or other application content is committed.

## Output and source checks

The generated PDF was independently reopened with pikepdf: four pages, no
encryption, no AcroForm, and no syntax warnings. PDFium rendered every page; visual
inspection confirmed the four large headings/numbers in their intended order and
the unchanged practice boxes. The saved text included the concrete byte count and
unsigned-derivative statement, without the source display name or attempt path.

The external signed-marker input and immutable case copy both retained the import
manifest's SHA-256. Failed export destinations were empty when inspected. These
checks use only synthetic artifacts retained in ignored local output; they do not
create new golden files or alter approved references.

## Test-setup correction and limitations

The first session mistakenly reused the narrower image/PDF smoke staging helper.
Its package retry failed with `FLATTENED_PDF_EXPORT_ASSEMBLY_FAILED` while waiting
for the worker to become ready; package propagation was `PACKAGE_EXPORT_PDF_FAILED`.
The missing overlay dependency closure was corrected in the ignored test launcher.
The subsequent complete-runtime export passed. The initial failure is not counted
as successful export or final cancellation evidence. No production fallback,
resource limit, error expectation, or security control was weakened.

Malformed preliminary framing currently maps to a generic unavailable-source
message. It still rejects before mutation and returns focus to a retry action;
the separate parser-invalid scenario verifies the worker failure boundary.

Cancellation can occur during worker startup, before the first output page; this
manual run does not claim a cancel after publication. Automated package staging,
ZIP writing, deadline, partial-publication, disk-full, and cleanup tests complement
it. Forced process-exit/autosave recovery is also automated, and is not described
as a manual crash in this session. The full-suite and resource evidence, deferred
features, and final gate conclusion are in [the M4 gate report](../gates/m4-pilot-gate.md).
