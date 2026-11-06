# PWS-0702 screen-reader audit and NVDA scenario

Task: **PWS-0702 — Complete screen-reader names, state, and announcements**.
The binding requirements are [PWS-D030](../../DECISIONS.md#pws-d030--accessibility-is-designed-from-the-first-ui-task)
and [PWS-D037](../../DECISIONS.md#pws-d037--user-facing-validation-claims-remain-concrete-and-limited).

## Evidence method

Qt interface inspection and announcement-event tests establish exposed names,
roles, states and application event policy. They cannot prove what NVDA actually
speaks. The desktop scenario below requires an actual NVDA Speech Viewer
observation, using production widgets and normal controls. Simulated replies must
be identified separately and do not establish import, persistence, cancellation
cleanup, export publication or verification success.

The six-phase developer scenario has **cumulative recorded NVDA coverage**,
as detailed below. Each original attempt retains its own status and source
binding; no old observation was copied into a new attempt. Installing or
launching NVDA, preparing an attempt, or running widget tests alone records no
spoken observation.
An agent-operated NVDA run is developer evidence; it is not a human assessment,
PDF/UA conformance, or compatibility with every screen reader. Accepted earlier
keyboard, reader and removal-security results retain their original scope and
must not be repeated just to improve this test's recorder.

Use a disposable NVDA configuration with Speech Viewer enabled, welcome prompts
disabled, add-ons disabled and automatic update/usage-statistics collection off.
Keep the user's NVDA configuration unchanged. Inspect the live Speech Viewer
through the Windows accessibility surface, leaving focus in PaperWorkShell while
waiting for speech: focusing Speech Viewer pauses its updates. Record closed
observations rather than speech transcripts, clipboard content or screenshots
from other applications. No raw speech or document text belongs in routine logs
or committed evidence.

## Preparation and checkpointing

The developer recorder is [tools/pws0702_screen_reader.py](../../tools/pws0702_screen_reader.py).
It does not launch the desktop, open source documents, change cases, weaken worker
containment or create an export. Its only writes are new observation receipts in
ignored `output/pws0702-screen-reader/<attempt>/`. Existing PWS-0701 synthetic
cases and artifacts may be inspected through the normal application after their
source and fixture provenance is checked. Use a fresh derivative destination.

Freeze the implementation and helper before preparing the actual observation
attempt. From the repository root:

```powershell
.venv\Scripts\python.exe -m tools.pws0702_screen_reader prepare --attempt native-v1
.venv\Scripts\python.exe -m tools.pws0702_screen_reader status --attempt native-v1
```

Preparation automatically binds every production Python source file, the locked
dependency definitions, this helper and its fixed phase plan, exact synthetic
fixtures, current Windows/Python/Qt versions, and the installed NVDA executable's
file version and hash. Installed-binary evidence is explicitly separate from
actual spoken observations. No username, private absolute path, source payload,
speech transcript or old pass is copied into this metadata. A source, fixture,
helper or environment change refuses the old attempt; preserve it and create a
new attempt for affected observations.

After observing each phase, immediately append its outcome. For example:

```powershell
.venv\Scripts\python.exe -m tools.pws0702_screen_reader record --attempt native-v1 --phase case-pages --outcome observed --input-actions 5
```

The number is the operator's measured input-call count for that phase; the
example is not a prefilled result or burden estimate. Use `problem` for a defect
and `later` for missing coverage. A later retry appends another record and keeps
the original problem. Do not mark a phase observed until every listed semantic
check has actually been heard. Optional repeated `--artifact` arguments bind
specific synthetic files under the existing PWS-0701/PWS-0702 output directories
by relative path, byte count and hash; no document contents are recorded.
This is useful for the exact exported PDF, saved summary, or an independent
verification receipt. Changed or missing bound artifacts refuse current status.

Each receipt is flushed before exclusive atomic publication, with a hash link to
the preceding receipt. Interrupted unpublished stages are retained and ignored;
the previous valid observation remains usable and the next action can resume.
Invalid input never overwrites a completed step. Fast status hashes only bounded
source/metadata/artifacts and never invokes parsing, rendering, OCR or the test
suite. Even all six observed phases produce only `observations_recorded`;
current automated verification and the task's acceptance review remain separate.

## Full developer scenario

The agent prepares synthetic cases and native chooser destinations. The operator
then follows one ordinary action at a time, checking the current focused control
and live Speech Viewer after each meaningful transition. No external PDF reader
or repeated M3/M5/M6 compatibility session is required for this UI task.

| Recorder phase | Production activity and required spoken meaning |
|---|---|
| `case-pages` | Open the prepared case through the case chooser. Hear its identity/revision. Select two page rows; hear document/page position, selected state, rotation and truthful preview availability. Rotate or reorder once and hear the updated state. |
| `improve-cancel` | Open scan adjustment review and OCR settings; hear native control roles/values and preview limitations. Start a long operation and request cancellation. Hear useful phase/count progress without repeated identical updates, one cancellation request and its actual terminal outcome. Cancellation must never imply completion. |
| `fill-protect` | Open visual overlays; arrow between different mark kinds. Hear type, ordinal, geometry, crop visibility and applicable editing state without payload text. Open saved removal marks and hear geometry plus the distinction between saved intent and applied removal. Leave through the ordinary return path. |
| `check-findings` | Open checklist review. Hear category, assigned-document, page-count and explicit-confirmation state; adding or moving alone must not imply satisfaction. Review quality/preflight findings, hear severity and safe guidance, and follow a finding to its page or correction action. Review items remain review items. |
| `error-recovery` | Import the prepared invalid synthetic PDF. Hear its stable plain-language failure and reach the Import recovery action. A failed action must not announce success, expose raw exception data or leave focus on an unavailable control. |
| `export-result` | Open package review, read the selectable summary and confirmation states. Export a supported synthetic packet to a new destination. Hear the actual terminal result, performed checks and remaining human review. Traverse Save summary, save/read its result and close back to a meaningful organizer action. Independently bind the new output/summary and verification receipt. |

If a substep fails, record `problem` immediately, retain its safe finding in the
findings log, and continue only where later coverage is independent. If an
operation times out or no result is published, its export phase is incomplete;
progress reaching the last page is insufficient. Existing accepted exports may
support provenance checks but cannot stand in for a new affected announcement.

## Human-session boundary and burden

This is an **agent-operated developer scenario**, not a finished human handoff.
It avoids repeating the prior 439 measured keyboard input calls, reader/resave
sessions and data preparation. The current CLI intentionally has no human
one-action guide, Copy/Open-folder controls, or automatic desktop resume.
Do not give it to a person as a completed human-testing helper.

Before any human handoff, follow [the human-testing policy](../development/human-testing.md):
finish those controls, rehearse the entire journey, freeze it, and record total
human actions and active time including setup, save/reopen, recording and
recovery. No human burden estimate or human result is asserted here. Actual
agent input counts belong to the new phase receipts; worker elapsed time is not
human active time. A later human assessment needs fresh observations, while
reusing valid environment facts with their provenance.

## Findings and observation log

Actual agent-operated observations were made on 2 November 2026 using NVDA
2026.1.1.55980, Qt/PySide6 6.11.1, Python 3.14.6 and Windows build 26300.
The disposable NVDA configuration was closed normally after observation; the
user's configuration was untouched. Speech Viewer was inspected live, with no
raw transcript persisted. This is developer evidence, not a human assessment.

The cumulative index is
`output/pws0702-screen-reader/native-cumulative-coverage-index-v2.json`, SHA-256
`a6badd6cb59240973e28c45f3792b9fc0a654e9a79379ad288675877d7bf1fc1`.
It authenticates original append-only observation chains, output hashes,
preservation audits and the limited source-invariance proof. It records no new
native action and infers no task completion.

| Phase | Actual observation and scope |
|---|---|
| Case/pages | Original `native-v2/observation-0001.json`: case identity/revision, document/page ordinals, selection, rotation and ready preview were spoken. |
| Fill/Protect | Original v2 observation 2: distinct Text/Checkmark type and editing/geometry/crop meaning, plus saved removal intent with information still present. Both editors returned normally without saving provisional changes. |
| Improve/cancel | Final source in the fresh v5 runtime: actual import progress, one cancellation request, terminal “Import cancelled. No source was committed.” and recovery focus; later successful image import, both scan previews ready and local OCR availability/selection were heard. The initial `later` record remains retained. |
| Check/findings | Cumulative original v2 category/document/count/confirmation review and later final-source preflight severity, review guidance and acknowledgement. The original partial `later` entry remains unchanged; indexing these observations created no new interaction. |
| Error/recovery | Final source: actual `SOURCE_UNAVAILABLE` failure and Import recovery focus. This proves failed-source-selection recovery, not parser rejection. Five editor-launch recovery paths and stale error names are separately covered by fail-before Qt regressions. |
| Export/result | Final source in the fresh v5 runtime: real normal worker inspection, a newly saved/reopened four-page PDF, the actual terminal count/check summary, successful Save summary, and return to Review and export package. The supported fixture has one source and no overlays. |

The earlier successful case/page/Fill/Protect observations retain their original
v2 binding `095f2f3139179c5b99a3ba208cbf1f40aafb061f0dafa2bbf0fc7f5ee5486aaf`.
Their full old binding is now stale and the official recorder refuses it.
`output/pws0702-qa/v4-production-ast-scope-proof.json` (SHA-256
`303c683ab8ed71ee7881fc6d5fef7fad97d5126c91c37d40c359771bd08ef506`)
establishes that only six window failure handlers changed, with two narrow new
helpers; all remaining window AST and all other production module bytes are
exact. This supports retaining the successful unaffected observations. It does
not establish executed paths or speech, nor native execution of all changed
failure methods.

The final v4 recorder binding is
`99ef550f89a95a752c4c48de52c33296033f6a62b827bb07cd0ad352734674bf`.
Its own status remains **incomplete**, with case/pages and Fill/Protect pending,
because old passes were deliberately not copied into new observation records.
Coverage is cumulative across preserved epochs, not one uninterrupted final
source run. The current implementation's full automated checks and applicability
review are separate from recorder status.

The first final-source multi-source attempt produced no export and remains a
`problem`. Its cause was not established; it is not relabelled as cancellation or
success. A fresh runtime was built with the normal locked offline production
dependencies and unchanged containment/resources. A prospective deterministic
four-page single-source fixture was then prepared through public case/manifest
services with explicit agent-test category confirmations. That trusted setup is
not an untrusted import or a human checklist observation. Its actual subsequent
inspection/export ran through the normal production worker and UI. No old case
or original was replaced and no security, timeout or budget was relaxed.

The new PDF is 2,020 bytes, SHA-256
`9719fb9c9369b3cc318677302cd74d5d1ecb8a35b1a9076a5a10a62d96350eca`.
Independent pikepdf/qpdf checks, extraction against the fixed synthetic markers,
all four PDFium renders and agent inspection of the four page images are bound
by `native-v5-output-verification-v2/receipt.json` (SHA-256
`26afc3c6385ac114a0c0ebd36d64ba1b8bbbbe008c80727b811a7cb9745c3a7b`)
and its separate `visual-review.json`. The saved summary is 6,069 bytes, SHA-256
`8c08d9a76a35a18e1c5bf365ce6aa4d025a38ef38a23ab6f5243f7657c8febb8`;
the actual selected UIA text plus one final LF matches every saved byte. Return
from a button can select the dialog's safe Close default; Space activated the
focused action. Save summary was reached through Tab, then Space.

The original phase receipts retain the operator's successful key/type/click
API-call counts for their recorded scopes. No cumulative action total or human
action/time estimate is inferred from those differently scoped records.
`post-native-v5-audit-v2.json` (SHA-256
`06694c363e4c9685e09197590e93c7c72a9b2efe7c4ebbd0504966d2469e746e`)
confirms old protected files and all 286 production files unchanged, new source
hashes valid, all five case temp directories empty, no process matching its
recorded ownership filter, and no resource journal remaining. A separate later
snapshot found no qpdf/Tesseract process; that is a later boundary, not proof
of every possible earlier child process. The first audit's missing-input-allowlist failure
is retained; the second explicitly binds that public low-resolution fixture.

No claim is made for the failed multi-source composition, mixed image/overlay
export, physical-device performance, portal acceptance, PDF/UA, other screen
readers or a new permanent-removal security gate. Slow contained-runtime setup
and the earlier multi-source failure remain export/performance qualification
observations for PWS-0807, rather than an inferred cause or a raised budget.
See [the completion report](../development/pws0702-result.md) for exact-source
automated verification and the preserved initial failures.

## Security, privacy and product boundary

The scenario uses synthetic local files and the existing standalone application.
No new dependency, network path, sibling integration, private-content interface,
case migration or release qualification is introduced. No release-interface TBD
fact is resolved by these developer observations. Worker resource/security and
immutable-original guarantees remain in force during ordinary test actions.
