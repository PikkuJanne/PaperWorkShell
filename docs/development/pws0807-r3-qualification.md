# PWS-0807 packaged R3 qualification

## Installed defect and verification scope

At 200% display scaling with Windows Aquatic contrast, R2 moved keyboard focus
from **Close case** to **Pages** without drawing an indicator at the selected
tab. The full-window comparison showed that the outgoing button lost its
indicator. The rest of the application image remained identical. Both normal
DPI settings had shown a visible change. The [R2 qualification ledger](pws0807-r2-qualification.md)
records the exact failed candidate and its independently verified package
exports.

The successor has candidate ID `pws0807-tab-focus-engineering-r3`. The
repair targets selected tabs on both workbench tab bars. Verification compares
real installed keyboard navigation and screenshots before and after focus at
normal 96 DPI, normal 192 DPI and Aquatic 192 DPI. The selected tab must
remain identifiable and show visible focus on entry, including after reverse
and arrow-key navigation. The previous candidate, build receipts, synthetic
oracles and checkpoints remain available for comparison.

The clean branch begins with a disconnected Windows 11 Pro x64 standard-user
desktop with no application, cases or development tools installed. A separate
replacement branch begins with the preserved R1 installation and synthetic
cases. Each measurement records which starting state, inputs, executable and
observers supplied it. PWS-0808 owns the product-completion decision.
