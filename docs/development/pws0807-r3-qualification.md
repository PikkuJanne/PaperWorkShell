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

## Exact unsigned artifact

| Field | R3 engineering value |
| --- | --- |
| Candidate and source | `pws0807-tab-focus-engineering-r3` 0.0.0, commit `54e0b916ac069ce00a0ce200c43e07f3a76d1784` |
| x64 installer | 513,023,918 bytes, SHA-256 `7791cef6be0062319a9c9f39a4bf130ded8f3914485eb50ab4e07d182c992889`, Windows Authenticode `NotSigned` |
| Qualified build handoff | `71819c9b00b33be65ecdde772020850cde32517a6fb08084c244feab82640755` |
| Installer receipt | `057407a387e55bacff420deec7e1cc0689c3dffa09317b280f2b5f8f391b9b74` |
| Resolved inventory | 3,219 files and 650,884,602 expanded bytes, SHA-256 `a436ca25db576c7cc60936428381ee4e747030b869a77d6c5342c7b15df202a4` |
| Installed marker | `5e564b0fef1b5e0ebe30ab19bdfe1d8c65872420889ac35c82d7237c73ab5f1e` |

All 345 packaged application files matched Git, and all 175 external build
inputs matched the preceding candidate. Comparing both complete inventories
found six changed files. The application changes are confined to
`ui/presentation.py` and `ui/window.py`. The workbench tab bar paints a
two-color focus outline using the active Windows selection palette. The
underlying document engines, native launchers and worker binaries retained
their previous hashes. Two new rendering assertions failed on R2. All 370
accessibility tests passed on the final R3 source after the regression test
was updated to settle the application's palette refresh before measuring.

The release-materials audit matched 957 component names and versions in both
SPDX 2.3 and CycloneDX 1.6 and verified 729 notice files. It also matched
every SPDX file digest to the candidate inventory. The audit is
`r3-completion-compat/verified-release-materials.json`, SHA-256
`b8b4875fe982baaa559dae42da2e72c423a211cab2290a2e007f26428e160530`.
The full R2/R3 payload comparison is
`r3-completion-evidence/r2-to-r3-comparison.json`, SHA-256
`63eae37b9464f048891333ff0d399636df96497d972d187757e378e8012cf0d4`.

## Frozen sample

The 30-member synthetic archive is 4,173,788 bytes, SHA-256
`027410d15b8a62874d535fca5fdc625e46e272667a07b3c8096920f217b4f468`.
Its manifest is
`a51a4ccf34b47793e4495b00e9300479264ad0f2f33780caf4722aea887eee90`.
The README and manifest identify the candidate, procedure and source of each
expectation. All 28 other archive members matched the immutable R2 oracles.
An independent new extraction checked every member, reopened the PDFs and
nested ZIPs, and confirmed rejection of the deliberately truncated PDF
before clean-guest intake. The fresh-reopen receipt is
`b7bab31c50693a6e287029148faceac27cedc2ab7ba2a19a85c1e1e562dba637`.
The local package contains project-authored synthetic inputs and expected
outputs. The release interface documents the product's ordinary in-app import
and external PDF, package and support outputs.

## Setup refusal and offline public interfaces

Before the clean installation, the Windows 11 Pro x64 guest had no
PaperWorkShell installation, cases, registrations or development commands.
The standard user's setup refused each deliberately invalid input before
creating the ordinary target. A modified installer and mismatched inventory
returned `PWS_SETUP_ARTIFACT`. An alternate-stream path returned
`PWS_SETUP_ENVIRONMENT`. In all three cases the process exited 1 and the
original bundle retained its expected hashes. The receipt is
`r3-completion-evidence/clean-admission.json`, SHA-256
`4ca527ada981dfa4cf1f7bd9ddf2c488988eae1487b2ebd0d1c2e9fbb25e1b37`.

The staged Windows R3 launcher displayed `LAUNCHER_ARGUMENT_INVALID` for
PDF, JPEG, PNG and portable workspace arguments and directed the user to
visible in-app import. Each launcher ran on a private host desktop. The
normal public PowerShell recipe generated by R3 exported the edited mixed
case. A second call hit the occupied-name guard and preserved the existing
output. The installed specimen and recipe have separate reopen receipts.

## Active worker interruption

After the installed-layout R3 backend accepted a thumbnail operation, the
test terminated that worker. The application returned
`PDF_THUMBNAIL_WORKER_FAILED`. A fresh backend reopened the same two-page
case and produced a 171 × 256 retry preview. External and managed hashes
and the manifest matched, with zero temporary files remaining. The
constrained-runtime receipt is `r3-native-runtime/interrupt1/receipt.json`,
SHA-256 `c39303b403560cc6348d15d9579bbe8a2bbfe341f4acc1c029110d5473f9f907`.

## Reader files, compression and update trust

The R3 M3 overlay generator reproduced the accepted 9,485-byte, five-page PDF
with all eight overlay kinds, SHA-256
`05faf2055ccb07d85eca7afdb5f446c2b3aeba895d07c2f5fab4d769059fda3e`.
Poppler independently matched the text and rendered pixels of every page.
This binds the earlier reader observations to the identical artifact. The
first host run, concurrent with corpus generation, source testing and history
audits, reached its 120-second limit and published no file. In a quiet run,
R3 completed the same corpus in about 65 seconds at the original limit.
Both receipts are retained. The successful run is
`r3-completion-compat/m3-canonical-overlay-serial/receipt.json`, SHA-256
`a181cf439159997781270e7bb3427b6f687809d02b43b396a60cff8804bc30ad`.

The six M5 reference PDFs and their ZIP matched their earlier accepted bytes.
R3 reverified the ten actual M6 reader saves and resaves and the negative
control. Earlier reader observations retain their original viewer versions and
dates. The independent permanent-removal release profile and the runtime
verification report remain separate. The [R2 ledger](pws0807-r2-qualification.md)
records the threshold and quality-floor controls on the unchanged document
engines, including four bounded attempts at an impossible target. It also
records the supported native all-PDF fallback for an edited checklist and the
explicit refusal for a mixed PNG with a visual overlay. The R3 joined
compatibility receipt is `r3-completion-compat/qualified-compatibility.json`,
SHA-256 `2972394350773f764ea3ded33f3bfbc04ade55b3508af0941197157dd2d4c587`.

The actual unprovisioned R3 update controller refused the previously signed
offer with `UPDATE_TRUST_UNAVAILABLE` and preserved installation status.
With the original isolated public test root, R3's Windows verifier
authenticated the previous signatures and streamed the actual archive.
It refused replay, the wrong channel, expiry and a changed signature.
The originally observed slot activation and restoration remain attributed to
their recorded builds. The R3 check is
`r3-completion-compat/update-rehearsal-crosscheck.json`, SHA-256
`dc22003bfb02850a6822400ecf93e858d39f6d320e4be55476cfb952bc0166a1`.

## Constrained searchable export

R3 ran the full 12-page signed-source M2 workload using the packaged Python,
qpdf, OCR engine, English language data, Windows workers and constrained
ResourcePolicy. Its fresh retry produced the independently verified
128,180-byte searchable derivative in 19,056 ms. Cancellation was acknowledged
in 12 ms. The complete workload took 81,031 ms. An external observer
recorded a peak process-tree working set of 341,299,200 bytes against
1,342,177,280 bytes. There were 1,365 complete RSS samples and four
incomplete samples, with at most 140.22 ms between valid samples. The private
temporary-storage sampler completed 1,116 observations and reported a
740,444-byte sampled peak.

The first run published a retry PDF but failed its mandatory private-temp
measurement assertion. That run supplies no complete resource result.
The second run used a quieter host. An observation wrapper counted calls to
the original reporter without changing return values, deadlines or exception
handling. Four scans saw a concurrent private-temp tree change and retried
inside the production reporter. The final required measurement was complete.
The evidence does not identify the cause of the first run's incomplete
sample. Both attempts are retained. The successful canonical report is
`r3-completion-runtime/m2-quiet/finalized-resource-evidence.json`, SHA-256
`6e6508962c2c1eed2648eda81c447830dbc751ee49823666d9abea67f19697cf`.

## Clean installation and visible keyboard focus

On disconnected Windows 11 Pro x64 26300.9711, a fourth negative control
supplied the wrong receipt digest. Setup refused it before installation.
The authentic per-user installer returned exit 0. Independent reading of
the ordinary install directory matched all 3,219 files and 650,884,602 bytes.
The expected registration and three shortcuts appeared. The standard user
opened the empty chooser, imported the first PDF, exported it, encountered
the occupied-name refusal, saved a new copy and reopened the case. The
original and managed 802-byte source retained SHA-256
`3f62fe484b8b389ec18afe115939d491f795e517195a7acf7fe5989075839c47`.

With the actual one-page case open, the standard user tabbed from **Close
case** to the selected **Pages** tab. At 192 DPI with Windows Aquatic, the
newly focused tab showed a dashed outline in the actual system selection
colors. This same transition produced no incoming indicator anywhere in the
R2 window. The five paired R3 captures, including the selected Pages,
Sources, Recipe and Checks tabs, changed 102, 102, 128, 112 and 118
pixels respectively on the measured outline row. The alternating-color
transitions and the native-pixel contact sheet located the indicator on the
selected tab. The 24 forward/reverse focus observations over 96 DPI,
192 DPI and 192 DPI with Aquatic identified the intended controls inside the
window. The operator then restored 1,024 × 768 at 96 DPI, contrast off,
and all four original system colors. The app returned to the ordinary chooser.

The host receipt in `r3-native-display/r3clean1/host.json` has SHA-256
`1b255d54535f51e4ac858aa58fe7a4320e85f21729ce0b7adb3d43f9caa93574`.
The visual comparison in `tab-visual-review.json` has SHA-256
`2e83500e0fbfdc1226c45a8a82f5786fa0603ef9f108ce116ed04e4833e372a2`.
