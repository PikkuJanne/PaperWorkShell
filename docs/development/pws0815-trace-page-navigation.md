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

## Next development step

PWS-0816 covers a separate roadmap requirement: splitting a page range into a
verified derivative. The pure split/group operation in PWS-0110 preserves
page membership, and R3's separate-PDF evidence covers checklist categories.
Begin by checking the existing selected-page PDF plan against the ordinary
Export PDF entry point and its fail-closed handling of unsupported modes.
