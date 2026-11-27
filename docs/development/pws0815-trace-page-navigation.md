# PWS-0815 — discoverable TRACE page navigation

## Scope

[R3 qualification](../gates/m8-product-completion-gate.md) applies to the frozen
unsigned R3 engineering package, its recorded workflows and its tested Windows
configuration. The original gate report and its receipt stay with that
candidate. The accepted signing policy in [ADR 0023](../adr/0023-unsigned-product-completion.md)
and the proven [automated setup](pws0814-automated-setup.md) apply to ongoing
development.

The [second-PC feedback](pws0811-repairs-20261106.md#second-pc-human-result-and-feedback--2026-11-25)
records difficulty discovering additional pages in a one-page preview. The
[approved TRACE parity map](pws0813-trace.md#feature-parity-map) calls for
document canvas navigation and stable source and assembled page identity.
TRACE already provides an assembled page list, Previous and Next controls, and
an Arrange view. In R3 the canvas controls follow the preview, and the
document preview shows an individual output position without the packet total.
The compact view can also hide the page rail.

## Installed comparison plan

Open a synthetic multipage case through the ordinary Start and case chooser
workflow in the current offline standard-user installation. Ask whether the
document panel makes the presence of further pages apparent before the user
scrolls at a compact size. Check the initial position text, existing
navigation location, and Source and Planned selection. The predecessor and
R3 source comparison will bind the observations to their exact scope.

Move the existing actions ahead of the preview and give them the current
assembled position and total. An initial visible total and working forward
and backward traversal will establish the affected behavior. A wrong page,
stale total, inaccessible controls, invisible focus, or a changed case
revision will keep the corresponding check open. The canvas continues to use
the existing bounded preview service and selected-page identity.

## Baseline and correction

The active offline lab installation was R1, `pws0807-diagnostics-engineering-r1`
from `e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec`. A standard-user Start launch
reopened the retained two-page synthetic case in a 770 × 590 window on the
1024 × 768 console at 96 DPI. The workflow viewport ended at y=505 and Next
page occupied y=647–685. The thumbnail rows and export command conveyed page
information, but the preview and its nearby controls gave no visible way to
advance in the initial viewport. The saved Exported view also exposed an active
Next action with the whole-file verification controls. The case manifest and
four managed original hashes matched after the observation. The native trees,
captures and receipts are under `output/pws0815/r1-navigation-native`.

The R1 and exact R3 navigator code differs only by the R3 focus-visible workbench
tab installations. A separate compact source regression on the R3 descendant
failed against the original layout before the change. The correction places
the existing buttons and the assembled page position ahead of the canvas, retains
the selection UUID when switching views, and announces the new position on
navigation. At either end, keyboard focus stays on an available paging action.
The saved whole-file Exported view hides draft paging. In-session Exported
continues to support navigation of its existing verified page mapping.

## First engineering build

`pws0815-trace-page-navigation-engineering` was built from
`0350fbdb63534e012f693dacf847e47e60695e18`. The installer is 513,024,443 bytes,
SHA-256 `2a32be661db4ec66339e12f350643a06fbb471a4eab8677d0e0224d7ee68b758`,
with observed Authenticode status `NotSigned`. The digest-bound prepared Setup
bundle is under
`output/pws0815/build-preparation/pws0815-trace-page-navigation-engineering/setup-bundle`.
Its handoff SHA-256 is `da09daf9847f9b09976abecd810d8ce8e202b36b07271e1005a02a82118eed3b`.

Offline wheel, source archive, stage and native compile passed. All 345 packaged
application files matched Git, all 3,219 staged payload members verified, and the
175 reviewed third-party inputs matched R3. SBOMs and notices reproduced. Only
`ui/window.py` changed in the application payload. The separate installed marker,
component list, and source archives reflect the new build. The verified Setup
launcher is the PWS-0814 launcher.

The affected workflow, setup and repository validators passed 219 tests and 149
subtests. The registered native accessibility lane passed 370 tests. Format,
lint, strict typing, localization, schemas, task graph, documentation, release
inventory, and import and portfolio boundary checks passed. The accessibility
and localization reruns explicitly loaded the selected worktree source after a
shared editable development environment initially resolved the old checkout.

## First installed comparison

The offline standard-user PWS-Test guest retained the active R1 checkpoint
`999a5747-66e2-4565-baee-5e619dce8656` and all 23 checkpoint identities. The
registered Keep cases removal exited 0. Prepared Setup ran the exact successor's
per-user installer, verified all 3,219 installed files and launched the verified
desktop in the existing limited session. All 358 case files and 143 files in the
retained synthetic rehearsal folder matched across removal and replacement.
See `output/pws0815/installed-successor-r1/receipt.json` and the actual Setup
receipt alongside it.

Two initial lab-helper attempts stopped before guest mutation. The first used
syntax rejected by the guest's Windows PowerShell, and the second referenced a
historical export scratch folder that was absent from the restored R1 baseline.
The corrected helper used the live synthetic rehearsal inventory. Its install
and retention receipts completed before the final cleanup encountered the
read-only attribute copied from the frozen bundle.

The exact first successor moved both paging buttons above the canvas in the
same compact native window. Next was enabled at y=300–338, within the initial
viewport. On reopening the case from the chooser, the count was exposed as
`Page 1 of 2` in UI Automation with empty bounds and was absent in the capture.
The earlier test built the case state before showing the window. Exercising the
chooser-to-workbench transition reproduced the zero-width label at both
620 × 560 and 770 × 590. The second build places the count in the normal vertical
layout directly above the paging actions so Qt sizes it on show. Both test
variants then passed. The failed installed observation remains under
`output/pws0815/successor-navigation-native`.

The first installed build separately verified keyboard traversal from page 1 to
2 and back, reverse-button focus at each end, selection of the matching source,
and saved Exported controls. The ordinary Export PDF dialog offered whole-packet
output, with both pages listed. It had no selected range control. The dialog
was cancelled. The case manifest and its four managed originals matched.
See `output/pws0815/successor-navigation-keys`.

The second build corrected label sizing. The expanded source run also revealed
that, with the page rail hidden in the compact layout, the initial focus on the
canvas could scroll the new header past the top of the viewport. The third build
prefers an enabled paging action when the page rail is unavailable and when the
user switches back to Document. The geometry and focus assertions cover both
transitions.

## Next development step

PWS-0816 covers a separate roadmap requirement: splitting a page range into a
verified derivative. The pure split/group operation in PWS-0110 preserves
page membership, and R3's separate-PDF evidence covers checklist categories.
Begin by checking the existing selected-page PDF plan against the ordinary
Export PDF entry point and its fail-closed handling of unsupported modes.
