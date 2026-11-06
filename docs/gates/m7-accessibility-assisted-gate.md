# Milestone 7 accessibility and assisted-mode gate — PWS-0707

- Review date: 2026-11-02
- Gate conclusion: **PASS — source-tree Milestone 7** (`PASS_SOURCE_TREE_M7`)
- Scope: source-tree Milestone 7; no packaged or public v1.0 qualification
- Repaired source identity: `045a0ed278f234eec0506d967a3bedcce02747edf2cb73df8085286518ce0cc0`;
  1,058 files, genuine local verification checkpoint
  `d1b2797fda7de1ff9df988285ca853da07a569a5`
- Frozen decisions: PWS-D030, PWS-D031 and PWS-D037; continuing privacy,
  resource, original-preservation and standalone obligations remain in force

The reviewed implementation and native evidence pass. Canonical completion
metadata is frozen after this report; its terminal validation/privacy receipts
are recorded separately before publishing the task as done. This report does
not claim outcomes for checks that have not yet run.

## Scope and evidence rule

Authority is [DECISIONS.md](../../DECISIONS.md), the
[M7 roadmap](../../ROADMAP.md#m7--accessibility-localization-and-assisted-mode),
[TASKS.yaml](../../TASKS.yaml) and [AGENTS.md](../../AGENTS.md).
This gate evaluates keyboard operation, NVDA names/state/announcements, scalable
and contrasting presentation, reduced motion, localization readiness and
assisted consent, locking, retention and deletion.

Evidence is cumulative. Accepted task observations retain their original
source, helper, artifact and environment identities; they are not new gate
observations. Fresh gate checks and focused current native observations cover
the later implementation and presentation changes. No single uninterrupted
current-source workflow, independent human assessment or every-screen-reader
compatibility result is inferred from that combination.

The starting working tree contains accepted M6/M7 implementation and evidence.
This gate preserves that work, originals, prior observations and failures. It
does not change document processing, security boundaries, dependencies, schemas,
resource budgets, approved golden artifacts or translation meaning.

The binding verification calls for manual scenarios without specifying an
independent human participant. Native agent interaction is recorded as developer
evidence. No maintainer test session is requested. Any future human handoff must
follow the [human-testing policy](../development/human-testing.md), including a
finished helper, whole-journey rehearsal and immediate resumable recording.

## Prerequisites and acceptance mapping

All six prerequisites are recorded **done** in the current task graph.

| Prerequisite | Accepted evidence |
|---|---|
| PWS-0701 — keyboard and focus | [Result](../development/pws0701-result.md), [action inventory and native audit](../accessibility/pws-0701-keyboard-audit.md) |
| PWS-0702 — screen-reader semantics | [Result](../development/pws0702-result.md), [cumulative NVDA audit](../accessibility/pws-0702-nvda-audit.md) |
| PWS-0703 — display accessibility | [Result](../development/pws0703-result.md), [presentation policy and native audit](../accessibility/pws-0703-display-audit.md) |
| PWS-0704 — localization and RTL | [Result](../development/pws0704-result.md), [catalog and direction contract](../development/localization.md) |
| PWS-0705 — assisted-use policy | [Result](../development/pws0705-result.md), [native/source audit](../accessibility/pws-0705-assisted-use-audit.md) |
| PWS-0706 — post-export deletion and companions | [Result](../development/pws0706-result.md), [verification plan](../development/pws0706-test-plan.md), [retention contract](../architecture/post-export-retention.md) |

| PWS-0707 criterion | Evidence and gate assessment |
|---|---|
| Every M7 prerequisite is done | PASS; PWS-0701 through PWS-0706 are done and their original evidence is preserved. Terminal graph validation is separately recorded with final metadata. |
| Full primary workflow completes without a mouse and is usable with NVDA | PASS from accepted cumulative keyboard primary actions, actual package/summary success, six cumulative NVDA areas and current unrestricted unit/accessibility checks. Current native RTL review adds actual NVDA roles/guidance and focus/case/consent recovery. No uninterrupted new document-workflow or human assessment is claimed. |
| No action depends only on color or drag-and-drop | PASS; list/button/exact-field alternatives, native semantics and textual states remain available. All 340 current accessibility cases pass; the new native review observes visible keyboard focus and explicit disabled/unchecked states. |
| 200% scaling and high contrast preserve critical controls/focus | PASS for the source-tree scope after resolving M7-R1: the exact before-fail regression passes, full current accessibility checks pass and current native RTL200 dark/light observations retain readable selected text, actions, consent and focus recovery. Immediate palette geometry is not misrepresented as full-row containment. |
| Retention is understandable before import and deletion is explicit | PASS; unchanged policy/dialog components retain actual consent/retention and explicit-deletion observations, including retained output hashes. Current native consent again presents complete retention/removal limits before entry, with unchecked consent and Cancel. Current controller/integration/privacy checks pass. |
| Localization updates need no application-code text edits | PASS; data-only catalogs, unchanged loader/layout core, preserved original messages, current 2,761-message validation and unrestricted pseudo/RTL accessibility checks. Native RTL200 review confirms the scoped current presentation. Test locales are not reviewed translations. |

## Retained keyboard and NVDA observations

PWS-0701 covers Add, Organize, Improve, Fill, Protect and Check through its
original keyboard observations. Its separate closure completed real inspection
of three PDF sources and a four-page Combined PDF with saved overlay intent,
then actual Save summary, chooser cancellation/recovery and meaningful focus
return. The closure records 70 input API calls, 15 checkpointed observations and
zero pointer actions. Its older 45-observation/369-input record is unchanged.

The closure record is
`output/pws0701-closure/new-native-observations.json`, SHA-256
`33eba5b570732c7e5ba8b5300aefa7f7d7c10b625f0856637b8b6c7e6770b12b`.
Its recording-time pending-audit label is preserved; the separate post-exit
preservation receipt supplies the later audit outcome. The PDF is 26,223 bytes,
SHA-256 `98ebb6e1ca4e50977a337c6849b8c2e376ca35667b03c99a2b6527d60d447d3f`;
the saved summary is 6,559 bytes, SHA-256
`e0b2bf05dbb8d17c50c2782229bd80f188915de19e859f92a446c7df1b9c4209`.

PWS-0702 records actual Speech Viewer observations for case/pages, Fill/Protect,
Improve/cancellation, checklist/findings, failed-source recovery and terminal
export/summary. Earlier successful case/page and editor observations retain
their original v2 binding and its narrow later invariance proof. The final
recorder remains incomplete for those two phases because historical outcomes
were never copied into new records. The unavailable-source recovery observation
does not prove a native parser-rejection scenario.

Its cumulative index is
`output/pws0702-screen-reader/native-cumulative-coverage-index-v2.json`, SHA-256
`a6badd6cb59240973e28c45f3792b9fc0a654e9a79379ad288675877d7bf1fc1`.
This gate's read-only audit authenticated all 29 unique referenced artifacts
against their recorded size and digest without an identity error. That proves
preservation, rather than new speech or a fresh whole-source pass.

PWS-0702's terminal success used a supported single-source/four-page packet
without overlays, real normal worker inspection/export and an actual saved
summary. Independent reopen, qpdf, every-page PDFium rendering and synthetic
text checks are bound to the accepted output. The earlier multi-source failure
remains a failure with unproved cause; neither this success nor PWS-0701's
separate supported composition certifies that earlier composition.

## Initial source applicability

The independent audit is `output/pws0707/evidence-applicability.json`, SHA-256
`18745c0f20e0c8ad3e3b596f882b625e86f197cda541bdc438d42d701b4bdfd2`.
It records exact initial gate and historical hashes, catalog comparisons, original
artifact identities, method-scope differences and explicit limits. It launches
no native process and grants no gate approval. Its source epoch predates the
M7-R1 repair; it must not be relabelled as authentication of later code.

- The presentation policy, localization loader/catalog implementation,
  localization layout adapter, overlay and removal editors, checklist and
  constraint editor are byte-for-byte identical to the accepted PWS-0704 visual
  manifest. All 2,706 original messages retain exact values in each of `en`,
  `en-XA` and `ar-XB`; each current catalog adds 55 messages, with no removed or
  altered original entry and unchanged non-message metadata.
- The application, domain and storage assisted-use policy modules and assisted
  consent dialog are byte-for-byte identical to the PWS-0705 v6 native binding.
  The announcer and presentation policy also match it. Original consent,
  retention and recovery observations remain tied to those unchanged components.
- PWS-0706 changed the assisted controller's `cover` and `_uncover` methods and
  added `discard_hidden_window` for post-export privacy. Earlier lock observations
  are not represented as execution of these later branches. Current controller,
  lock/close and stale-consent tests cover that boundary in the passing repaired
  unit, accessibility, integration and privacy checks below.
- The PWS-0706 native helper and every direct PaperWorkShell module it imported
  match their original bytes. Its actual summary/companion/inventory/deletion
  observations used production dialogs and secure storage with a synthetic
  coordinator. The helper never instantiated the full desktop or assisted
  controllers, nor the worker capability handshake. Its original one-file
  scope proof is authenticated as historical evidence; the later current
  comparison correctly records five changed modules. No whole-desktop native
  result is inferred from that narrow proof.

PWS-0703's recorded 100/150/200-percent native Qt views, dark/light application
palettes, doubled text, focus and static activity remain source-specific
observations. PWS-0704's 15 glyph-bearing synthetic images and 62 layout cases
remain offscreen Qt evidence. The focused new gate observation covers current
native RTL/scaled presentation without turning either earlier record into
Windows OS-settings or reviewed language-translation evidence.

The separate after-fix supplement is
`output/pws0707/evidence-applicability-after-fix.json`, SHA-256
`d63014ff8281972bb776996b786adbb97c4688c86216b216ce3e3abed228ad7f`.
It authenticates all 1,058 repaired-source entries against both the canonical
root and the new PWS-0707 verification worktree, and explicitly records the
three changed paths. It rechecks that the catalog, presentation and assisted
policy components listed above remain unchanged after the focus repair.

Every direct PaperWorkShell module imported by the PWS-0706 native helper also
still matches its original bytes. This remains a narrow statement: that helper
does not import or instantiate `ui/window.py`, the edited product module. Its
synthetic coordinator does not establish integrated desktop or assisted
controller behavior. The supplement preserves the original applicability
receipt and all observations without rerunning or relabelling them. It supplies
neither a whole-source-unchanged claim nor repaired-source native approval.

## Resolved finding — M7-R1: focused case row outside the viewport

The second current native RTL200 attempt reproduced an accessibility defect
after the test window was positioned entirely within the available screen.
With `caseList` focused, the selected row occupied `[11, -154, 850, 46]` in the
outer application viewport. Its full height remained above the viewport;
pressing Down and changing the application palette did not reveal it. The
operator observed a blank central area. Native names and keyboard focus alone
therefore did not establish visible focus or usable selected content.

The mechanical geometry is
`output/pws0707/native/current-rtl-02/geometry-01.json`, SHA-256
`4cb23405be7ddbb77445b7602990dd9982334fe36bf2cd612f2ceb93f877b927`.
It records DPR 2.0, right-to-left layout, a 900 by 522 window frame within the
960 by 576 available screen, and focus before/after on `caseList`. The separate
operator record is `current-rtl-02/operator-actions.json`, SHA-256
`65c2e4a8b083b918df7212b08b778741ed1156a0ffb12135d4935101347e6e3f`.
The geometry is a mechanical measurement; the operator record supplies the
actual native visibility observation. This is a product focus-reveal finding,
not an inferred display-positioning failure.

The first attempt remains `PROBLEM_PRESERVED_NOT_VISIBILITY_PASS`: initial
placement was ambiguous and partly outside the captured desktop. Its observation
is not discarded or converted to a pass. The second attempt is
`FAIL_SELECTED_ROW_OFFSCREEN`. Both applications returned normally with exit 0;
both owned NVDA instances quit normally, with no force kill or global execution
policy change. Their `ended.json` and `nvda-quit.json` records preserve source and
profile identities and are separate from the rejected visibility observations.

A narrow focus-reveal repair is frozen. It reveals the current item rectangle
inside the outer scrolling surface, after the item view's own scrolling, and
queues a coalesced reveal when a focused view acquires or changes its current
row. Empty or invalid current items retain the prior whole-widget fallback.
Existing ownership, visibility, close and popup guards remain in force; the
repair changes no document, selection, access grant or publication policy.

The fair before-fix regression ran against the exact retained PWS-0706 window:
**8 failed, 4 passed**. The same 12 cases remain byte-for-byte unchanged in the
final test, with four added empty/invalid-current fallback cases: **16 passed**
after repair. All results used the registered `test-accessibility` entry point
with the focused selector. The four original recovery cases already passed
before the repair and are not presented as failing regressions.

The independent read-only review is
`output/pws0707/selected-row-independent-review.json`, SHA-256
`db41c5397cff29ac088bbd55595067516419e420c7d4a5a86c32ca1ed1280e41`.
It authenticates exact source/log hashes and reconstructs the original test
bytes from the final file by removing only the new four-case fallback test and
its import. It found no actionable correctness, privacy or reentrancy defect.
Its focused case-chooser evidence is supplemented by the complete current unit
and accessibility suites and the fresh native review below. The repair and that
combined evidence resolve M7-R1 for this source-tree gate. The original native
failure and pre-fix green tests remain unchanged; none is relabelled as a pass.

## Initial pre-fix gate verification

The first five registered gate lanes and four static checks passed on the
initial 1,057-file implementation identity
`bac718af2981b760e16353ea17f703c24ea3636a060a49a8fe9c4f20911a6365`.
They remain **pre-fix results**, not final-source approval and not evidence
that M7-R1 was absent.

| Registered command and scope | Pre-fix result |
|---|---|
| `tools/dev.py test-accessibility`, complete suite | PASS; 324 passed, no selection; 29.44 seconds |
| `tools/dev.py validate-localization` | PASS; 2,761 messages and three catalogs |
| `tools/dev.py test-unit`, recorded M7 selector | PASS; 529 passed, 6,562 deselected; 57.32 seconds |
| `tools/dev.py test-integration`, recorded M7 selector | PASS; 62 passed, 622 deselected; 71.29 seconds |
| `tools/dev.py test-security`, assisted-use/companion selector | PASS; 10 passed, 1,012 deselected; 4.49 seconds |
| `tools/dev.py format-check`, `lint`, `typecheck`, `check-import-boundaries` | PASS; 837 formatted/typed surfaces; 14 layers / 297 production source files |

The five-lane receipt is `output/pws0707/runs/final-01/results.json`, SHA-256
`1c3cb0d245b4d610543e7237727cee060fbc6b85a8079bc127e1a4e214932f0b`.
Despite its historical folder name, it predates the focus repair. Each command
retains its exact selector, log digest, before/after source identity and Git
preservation guards. The static receipt is
`output/pws0707/static-01/results.json`, SHA-256
`da3637ceaf7c250fe6efc526cde168e5e2b724bf130681020aaec4d0dc69c7f0`.
Selected lanes are not full unit, integration or security suite results.

## Repaired-source verification

The repaired source is frozen in `output/pws0707/final-source.json` and the
new task-owned `output/pws0707/verification-worktree`. The canonical source
differs from the preserved 1,057-file pre-fix epoch in exactly three paths:
`src/paperworkshell/ui/window.py`,
`tests/accessibility/test_selected_row_focus_reveal.py`, and
`benchmarks/pws0207-quality-findings-v1.json`. The baseline change renews only its
genuine implementation revision. An independent comparison confirms that every
other JSON field, corpus metric and resource fact is unchanged. This renewal
is not a new accuracy measurement or a substituted historical test outcome.

Preparation and publication proofs are
`output/pws0707/verification-artifacts/prepared-proof.json` and
`output/pws0707/verification-artifacts/published-proof.json`. They bind the actual
checkpoint, candidate baseline, original/retained source and Git preservation
guards. The independent review also rehashed all 1,058 current source entries
without a mismatch. Preparation itself ran no full suite or native scenario.

All nine fresh final-02 registered commands passed with the exact repaired
source frozen before and after every command. Their receipt is
`output/pws0707/qa-final-v2/final-02/results.json`, SHA-256
`66ffef572fd02ea8dbc0c51a20b7b20cf6d28f4300f9b67aadae1855031f8a97`.
Independent authentication of all nine receipts, logs, selectors and guards is
`output/pws0707/final-qa-peer.json`, SHA-256
`cb650fddf7910e6cb67c8a3a506e8150f88ebf9576bdb68d9487b6586b5fa75e`.
The original root and retained PWS-0706 Git/source state remain preserved; the
new worktree provides a real separate index for the implementation-bound unit
checks. The initial five-lane/static results above remain pre-fix evidence.

| Registered command | Exact repaired-source result | Command duration |
|---|---|---|
| `tools/dev.py format-check` | PASS; 838 Python files formatted | 2.422 s |
| `tools/dev.py lint` | PASS | 3.070 s |
| `tools/dev.py typecheck` | PASS; 838 source files | 38.232 s |
| `tools/dev.py check-import-boundaries` | PASS; 14 layers / 297 production files | 4.610 s |
| `tools/dev.py test-unit` | PASS; 7,091 tests / 1,239 subtests, no selection or skips; pytest 937.29 s | 942.355 s |
| `tools/dev.py test-accessibility` | PASS; all 340 tests, no selection or skips; pytest 35.73 s | 38.863 s |
| `tools/dev.py validate-localization` | PASS; 2,761 messages / three catalogs | 4.592 s |
| `tools/dev.py test-integration`, selected M7 lane | PASS; 62 passed / 622 deselected, no skips; pytest 78.26 s | 81.980 s |
| `tools/dev.py test-security`, selected M7 privacy lane | PASS; 10 passed / 1,012 deselected, no skips; pytest 6.83 s | 10.161 s |

The exact integration selector is
`assisted_use or post_export or text_companion or organizer_ui_lifecycle`.
The exact security selector is `assisted_use or text_companion`. Both are
supplied through `PYTEST_ADDOPTS` to the unchanged registered commands. They
are not full integration/security reruns. Complete PWS-0706 security and relevant
processing evidence retain their original source-specific scope, supported by
the explicit unchanged-component/delta audit rather than invented new results.

The complete accessibility/localization/assisted-use suites must cover keyboard
dispatch, logical focus, native names/state/announcements, scaling, contrasting
text/focus, static progress, pseudo/RTL layout, consent and lock recovery,
retention, explicit post-export removal and companion privacy. Failure coverage
includes cancelled dialogs, failed/stale grants, expiry, changed export bytes,
partial deletion and worker errors where applicable. Native-capable Windows
lanes run serially because they share the per-user access journal.

Reduced motion remains unconditional: the unchanged presentation policy
disables decorative effects and uses static unknown-length activity with
truthful status. Current accessibility tests cover the progress policy; the
new empty-case native scenario does not claim a new long-operation run.

## Current native RTL200, contrast and consent observations

The fresh `current-rtl-03` scenario used the real production desktop/backend
with one prepared empty synthetic case, the RTL test locale and DPR 2.0. Direct
environment collection records Windows 11 build 26300, CPython 3.14.6,
Qt/PySide6 6.11.1 and NVDA executable version 2026.1.1.55980. These are observed
runtime facts, not reused human outcomes or hardware certification.

The agent recorded **21 keyboard input API calls, zero pointer actions and ten
immediate observations over 292.302 seconds**, including recording and tool
interaction. This is neither per-character input counting nor human active
time or a validated human burden estimate. The append-only operator record is
`output/pws0707/native/current-rtl-03/operator-actions.json`, SHA-256
`d27baf653d4493b6ff087a1de0a7e97b06c6e44cdadc6aca7ba5c1a6a0cd1f7d`.

Actual observations include readable selected case/revision text and dashed
focus; Tab/Shift+Tab return to the list; live NVDA list/button roles and shortcut
guidance; complete assisted local-storage/retention/expiry/removal/locking
warnings at RTL200; default seven days, empty protected fields, unchecked
consent, logical Tab order and safe Cancel; cancellation focus returned to
Start assisted case. Dark/light application palettes retained chooser and
Import focus. Ctrl+O opened the prepared empty case through the real backend;
Close case returned to the visible selected case/list focus without creating a
new assisted case or processing a document.

The immediate F12 palette snapshot `geometry-01.json` records the row rectangle
`[11, -11, 850, 46]`: that rectangle is **not fully contained**. It is not used
as a geometric containment pass. The operator separately saw readable complete
case/revision text and later explicit reverse-focus return and normal case
closure. Complete settled-row containment is proved by the deterministic
regression, rather than inferred from that immediate snapshot. Some SDK
accessibility snapshots lagged actual visual focus; the operator used actual
screenshots and live Speech Viewer. No raw speech transcript was persisted.

The observation record's `OBSERVED_NORMAL_EXIT_PENDING_PRESERVATION` status
remains its original recording boundary. Separate post-exit preservation passed
in `output/pws0707/post-native-preservation-post-native-01.json`, SHA-256
`e9beb72425ff2c1183ff42214d65b0e8126d2669fa070d6b625ee75705751f69`.
The application returned 0, the owned reader quit normally without force,
source and empty-case profile hashes matched, prior attempt profiles remained
preserved, known owned app/reader/direct-loader PIDs and exact helper matches
were absent, and the access journal was empty under read-only inspection.
This is a bounded process snapshot, not proof about every historical descendant.

Independent record/source/log/lifecycle review is
`output/pws0707/final-native-evidence-review.json`, SHA-256
`79e26cfdb3594a1946d7080ebb044fa2fd777330f2773afcd926aa678d44f2e4`.
That reviewer authenticated the evidence without performing a new native
observation. The scenario establishes current scoped presentation/consent and
empty-case backend behavior. It does not rerun the full document pipeline,
retention expiry, case removal or prior human reader matrix.

## Implemented and task-owned files

- `src/paperworkshell/ui/window.py`: reveal the selected item inside oversized
  views and follow current-row changes without broadening processing/access.
- `tests/accessibility/test_selected_row_focus_reveal.py`: fair failing-before
  reproduction, keyboard/navigation/recovery coverage and invalid/empty fallback.
- `benchmarks/pws0207-quality-findings-v1.json`: genuine revision-only provenance
  renewal; accepted metrics, corpus and limits remain unchanged.
- `docs/gates/m7-accessibility-assisted-gate.md`: this acceptance, evidence,
  failure/repair and source-tree gate report.
- `docs/gates/README.md`: gate-index entry.
- `README.md`: current source-tree gate status and documented limits.
- `TASKS.yaml`: PWS-0707 evidence/state and PWS-0801 readiness after successful
  terminal closure; other task evidence remains preserved.
- `BUNDLE_MANIFEST.json`: matching canonical metadata inventory.

## Frozen report and terminal canonical closure

This report freezes the implementation/review conclusion before final canonical
metadata validation. The unchanged `output/pws0707/closure_qa_v2.py` owns the
terminal result index at `output/pws0707/closure-v2/<run-id>/results.json`; the
coordinator records its exact run ID and outcome in task evidence. There is no
prospective PASS claim or circular rewrite of this report with its downstream
receipt digest.

Publication of the done state requires the terminal schemas, task graph,
documentation, portfolio boundary, dependency/Pillow/resource/renderer profiles,
quality negative self-tests, fixture validation, full canonical secret scan and
Git whitespace checks to pass with implementation, metadata inventory and Git
identities frozen. The terminal receipts are authoritative for those later
outcomes. Prior failed attempts and the pre-fix native rejection stay preserved.

## Security, privacy and standalone boundary

Tests and visual targets are synthetic. Original files, worker network denial,
structured RPC/arguments, publication authority, source immutability and central
resource limits remain unchanged. Speech, OCR/document content, user filenames
and private paths are not routine diagnostics. A signature image remains a
visual mark; modified signed sources produce unsigned derivatives. Plain text
can omit visual marks or have reading-order/OCR errors and needs review.

Assisted lock does not encrypt files or defeat control of the same Windows
account. Expiry locks access without silently deleting stored files. Case
removal is explicit and does not promise forensic erasure. After partial
manifest-first removal, surviving files are unavailable for normal reopening;
there is no automatic destructive retry. Separate verified exports remain the
usable retained results.

PaperWorkShell remains standalone and owns its private data and migrations.
There is no sibling repository, adapter, shared service/schema or release
dependency. This source-tree gate proves no packaged data location, installation,
uninstall residue or ThirdLife compatibility fact. Release-interface documents
remain draft/TBD until their M8 owners supply packaged evidence.

## Limits and task graph

- Prior multi-source/mixed-image/overlay export failures and slow normal runtime
  setup remain recorded qualification observations for PWS-0807. No security or
  resource budget is raised and no unproved common cause is asserted.
- Application contrast palettes and Qt device scaling do not certify every
  Windows contrast theme, OS-setting transition, mixed-monitor configuration or
  platform file picker. Packaged native-dialog qualification belongs to M8.
- `en-XA` and `ar-XB` are synthetic test locales, not reviewed English/Arabic
  language translations. Agent observations establish neither independent human
  usability nor every screen reader, PDF/UA or hardware certification.
- Exact OS-clock adjustment and prolonged-close native Lock activation were not
  performed in the accepted assisted session; direct deterministic tests cover
  the recovery branches. Earlier failures and narrow observation limits remain
  visible in their original records.

M7-R1 is resolved and the **source-tree M7 gate passes**. PWS-0707 completion
metadata and newly ready status are published only after the required terminal
closure succeeds. The next dependency-ready task is **PWS-0801 — Finalize
dependency lock, license inventory, hashes, and SBOM**. No M8 implementation is
started by this report, and packaged/public v1.0 remains gated by M8.
