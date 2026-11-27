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

## Earlier milestone gates on R3

| Gate | How its evidence applies to the engineering candidate |
| --- | --- |
| M0 — foundation | The build receipt binds exact source, runtime inventory, dependency lock, SBOMs and notices. The source checks cover layering, schemas, repository history and synthetic fixtures. |
| M1 — offline vertical slice | Installed mixed import, case restart and independently reopened export establish the end-to-end path. The native resource report measures the restricted worker, heartbeat, cancellation, retry, process tree and cleanup. |
| M2 — imaging and OCR | The installed mixed case preserved input bytes, order and edited rotation. The measured 12-page constrained workload produced a verified searchable PDF after cancellation and fresh retry. |
| M3 — filling and overlays | R3 reproduced the approved five-page PDF covering eight overlay kinds. Byte identity plus independent text and pixel comparison binds earlier human reader observations to this file. |
| M4 — templates and preflight | An incomplete checklist blocked publication. All four confirmations enabled combined, separate and ZIP export, with each saved summary matching the displayed text. Two archived cases separately exercised edited and multi-source paths. |
| M5 — compression and compatibility | The six accepted PDFs and archive reproduced. Three Windows viewers displayed the selected page. A four-stage real compression run refused an impossible target at the unchanged quality floor. |
| M6 — permanent removal | The installed desktop produced the approved one-affected-page derivative. An independent verifier checked the saved file and the ten retained viewer saves and resaves. Fourteen failed or unknown method cases blocked publication. |
| M7 — accessibility and assisted use | The packaged Qt checks cover input, localization-related controls, privacy, consent and retention. The installed keyboard journey and the three Windows display configurations supply independent evidence of actual navigation and focus. |

The ledger attributes existing human observations to their original reader
versions, dates and exact files. New desktop interactions and screen
captures in this run are agent-operated.

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

## Archived package shapes and target refusal

The R3 desktop backend reloaded the two earlier synthetic case shapes and
authenticated the real restricted workers. Main12's mixed, marked four-page
case completed separate PDFs in 129.92 seconds at the unchanged 300-second
deadline. Main16's multi-source case completed combined PDF in 6.613 seconds
and separate PDFs in 19.839 seconds. Independent readers made 12 page
comparisons across the three operations. The Main16 combined file matched
the historical output byte-for-byte. The input and manifest comparisons and
independent reopen are joined in
`r3-completion-compat/archived-native/independent-reopen.json`, SHA-256
`d30e48daf550d9cfc031b9c2ed55a8592172872e818a76c32d9b94317261b892`.

On an edited all-PDF case with a visible overlay, a 100,000-byte target
produced the independently verified 26,223-byte derivative in about nine seconds.
A one-byte target returned `unsupported_policy` and
`PACKAGE_WORKFLOW_BLOCKED` in about ten seconds and left the destination empty.
The earlier successful file and all sources kept their hashes. The
corresponding receipt in
`r3-completion-compat/native-fallback-r3-archive/receipt.json` has SHA-256
`fc02d1f57b2093993265383307f5489cdf6e5858ec4c830c403e8b597e9c36e7`.

In separate fresh Windows sessions, Edge 158.0.4370.45, Chrome 158.0.8127.38
and Firefox 161.0 each displayed page three of three of the R3 color PDF.
Document pixels matched the reviewed R2 display. Firefox's ten-pixel
scrollbar strip was excluded. The fresh viewer receipt is
`r3-completion-compat/reader-smoke/three-current-windows-viewers.json`,
SHA-256 `6c86e234221fae3068ca85795348ba4de3b3f46415bee87d193597a109cf5d40`.
Eight of the nine archived-scenario PDFs also matched their corresponding
R2 bytes. The Main12 one-page PNG/OCR export differed in size by one byte.
Rendered pixels and extracted text matched. Normalizing the generated OCR
form resource identifier and PDF IDs produced identical rewritten PDFs.
The comparison is recorded in
`r3-completion-compat/archived-native-and-fallback.json`, SHA-256
`7072e1f679f3b63fa1aeac1ae79d9f7d8ce12f7a70e38206f613f5358418c2ca`.

On the exact R3 runtime, the three-page eligible raster input exercised the
four-step compression ladder at a one-byte target. The successive verified
outputs measured 1,100,841, 767,251, 363,799 and 212,711 bytes. The last
attempt reached the 200 DPI / quality 80 preservation floor. The application
reported the target unattainable, kept the occupied destination intact and
left no staging files. A further 122 component cases passed, including 14
failed or unknown removal-method decisions that blocked publication. The
joined receipt is `r3-completion-compat/raster-and-controlled-verification.json`,
SHA-256 `70664562e6b21441872e4cf684cfbb924461566f4f11eb485dbefcd34116b837`.

## Packaged UI, retention and privacy suites

On the Windows host, staged R3 application modules and the packaged Qt
Core, Gui and Widgets binaries passed 645 accessibility and input tests
plus 217 subtests. Matching QtTest, pytest and the offscreen platform
provided the harness. A further 143 assisted-use, retention, explicit
removal and review tests passed on disposable cases. The 107 support
privacy, occupied-destination, signed-source and isolated update checks also
passed. Each group independently bound its imported product files to the
R3 inventory. The joined receipt is
`r3-completion-evidence/packaged-qt-retention-and-privacy.json`, SHA-256
`67654c8be76d351d1439d252ce5f3fa520293584811c2317b0a25f3f1526eaa9`.
The installed keyboard, real screen reader and native contrast observations
use their own methods and receipts.

## Clean keyboard-only checklist and package outputs

From the ordinary case chooser the standard user created a new case,
imported the four-category PDF, reviewed constraints, restored the generic
defaults and applied them by keyboard. In the native dialog button group,
Tab reached Cancel and Left Arrow moved focus to Apply. An initial Tab-only
traversal and a later observation of a selected category row with focus still
on the documents list are retained. The operator verified actual list focus
and continued the same draft, preserving the first confirmed assignment.
The successful route used physical keyboard commands throughout the combined
journey. UI Automation read the focused controls and the summary.

With three confirmations, Supporting evidence was missing, one page remained
unassigned and export was unavailable. After the fourth confirmation,
combined mode produced a four-page, 2,020-byte PDF and a 6,069-byte saved
summary. Separate mode produced four single-page PDFs, 947, 950, 948 and
950 bytes, with an 8,926-byte summary. ZIP mode produced the 4,289-byte
archive with the same four PDFs and a 9,468-byte summary. The six external
files and three summaries matched the frozen sample byte-for-byte. All three
saved summaries matched their displayed text. Independent checks made twelve
page comparisons across the modes for source order, text and pixels.
The clean package receipt is `r3-completion-sample/package-bindings-clean.json`,
SHA-256 `6281ce9da218d6b74ed09551d6d2e9d06f592063e2ce83f28e0887799060efe8`.

## Native resource limits and offline worker

On the Windows host, the exact R3 installed-layout runtime completed the
normal and constrained mixed workloads. Observed process-tree working sets
were 196,845,568 bytes under the normal profile and 197,033,984 under the
constrained profile. Maximum Job commit was 43,216,896 and 43,257,856 bytes
respectively. Both runs recorded their enforced limits, worker count, CPU,
temporary storage and output digests. The software-rendered Qt workload
recorded 2,036 main-thread heartbeat samples, with a maximum interval of
138.78 ms against 250 ms. The initial test-harness invocation stopped before
collection because its collector directory was missing. The measured run
used the same independently rehashed collector and source workload as R2.

A live restricted thumbnail worker acknowledged cancellation in 5 ms against
2,000 ms and a fresh retry succeeded. Local controls were reachable outside
the worker. The worker and its descendant could not reach the local listeners.
Thirty-one storage and fault cases and four delayed-I/O GUI controls passed.
The final sweep rehashed 3,219 files and all 650,884,602 expanded bytes and
found zero native recovery-journal files. Results are in
`r3-native-runtime/runtime-qualification.json`, SHA-256
`32ac283b2cef41d0e7596d93291cc6d5fe846cbbc172cf70743126245160b5ee`.
These are software workload and configuration measurements. Physical-device
observations are optional under PWS-D041.

## Clean mixed files and diagnostics

The standard user imported the PDF, JPEG and PNG through the desktop.
For each input, the managed copy, external file and manifest digest matched.
The ordinary 22,408-byte PDF reopened with its three pages in import
order. The saved edited case reopened at revision 7. Its 22,413-byte export
contained the PDF, then the PNG, then the JPEG rotated clockwise by 90°.
A deliberately truncated PDF produced `SOURCE_UNAVAILABLE` and preserved
the case. The independent checks covered extracted text, source image
pixels, orientation and retained originals. See
`r3-completion-sample/checks/clean/originals-managed-and-exports.json`,
SHA-256 `53cd90776c85965e6e43c72af853ef5799caa0a3ce912a05cbf217a37826b44c`.

With a synthetic case open, the support workflow displayed the two-member
inventory before explicit consent. The reviewed ZIP saved locally. It
contained only `summary.txt` (283 bytes) and `allowlist.txt` (1,062 bytes).
Both member digests and archive CRCs verified, as did the preview's expected
archive, the allowed vocabulary and absence of prohibited fixture tokens.
The clean ZIP was 1,567 bytes, SHA-256
`3f2defae8fd552b75986004b7c097db008b30f5e642743b9ba9e1605bff95b47`.
Each diagnostic ZIP is checked against its own timestamped preview.
The seven clean output comparisons are joined in
`r3-completion-sample/clean-vs-frozen.json`, SHA-256
`64684c952b2fb096a911bebf2aac9f0b634d2432f88fef6f8ef2af0272079b32`.


## Permanent removal on the clean installation

The standard user imported the frozen one-page PDF, marked the synthetic
target, saved the case and reviewed the separate permanent-removal action.
The review preceded destination selection. The application reported one
affected page and six passing checks for objects and revisions, text,
images, rendering, save/resave and independent OCR. Its final result was
`REDACTION_EXPORT_COMPLETE`. The external source and managed original still
matched SHA-256
`7858323c5907cad84c96d857cc491b4e070f334c3c355d7bfdf7b84b45858168`.
The case retained its unapplied mark at revision 2.

The 34,084-byte unsigned derivative matched the frozen sample, SHA-256
`33a6cc1dbfee6fca8d7fe940e5f0ced6e22090e9046fbd9a00481ac0355b8e8c`.
An independent reader found one image-only page. A separate 200 DPI render
and English OCR recovered the outside control and found no marked target.
The raw application report is `r3-installed-matrix/clean/qual-removal-report/report.txt`.
The independent result is
`r3-installed-matrix/clean/outputs/permanent-removal/unsigned-derivative-independent-removal.json`,
SHA-256 `359f801211006ab1387a55761b6113846178cd3420ff781b064141210fdddb71`.

The clean guest had a disconnected virtual network adapter and zero registered ThirdLife
products, named services or processes. The R3 inventory had zero named
ThirdLife components or paths. All installed workflows ran on this
disconnected standalone configuration. The observation is
`r3-completion-evidence/standalone-environment.json`.

## Installer disk exhaustion and rollback

After the clean primary workflows, the default Keep removal removed the
application, shortcuts and registration. All 31 managed files in four
synthetic cases and all 23 checked external files retained their hashes.

With a 65,536-byte additional NTFS quota, the standard-user process observed
49,152 bytes available at launch. The native installer displayed Windows
error 112 while creating a temporary file, exited 1 and left the ordinary
install target absent. On the 64 MiB allowance, an initial observer stayed
at the license page with Windows Settings in front. It sent no control input
and restored the prior quota. That attempt supplied no partial-copy result.

On a fresh run at 64 MiB, the foreground-verified installer copied the
first inventoried payload and ran out of disk space writing `Qt6Gui.dll`.
The native log recorded 853 attempted destinations. The observer reached
its deadline at that error dialog, restored the original quota and recorded
the incomplete attempt. The same installer still displayed the error. The
operator verified focus on **Cancel installation** and pressed Enter. Setup
rolled back and exited naturally. The program directory and registration
were absent, all 31 managed files and 23 external files retained their
hashes, and the original user and volume quotas were restored.

The closed 599,917-byte native log has SHA-256
`370cb58d28ffef9443b2fe45495b0f2314ed0c36a4548a32f0d4162030f507fa`.
`r3-installed-matrix/quota/r3-low-space-focused/cancellation-sequence.json`
records the time of the disk-full dialog, quota restoration and cancellation.
The startup observation is `r3-installed-matrix/quota/r3-low-space/r3-preinstall-result.json`.

## Same-build reuse, repair and reviewed uninstall

The matching setup reused the verified R3 installation and started the
standard-user desktop without invoking the installer. The preserved first
PDF case reopened. The Notices shortcut opened the bundled terms. Following
the real quota rollback, a fresh setup installed and verified all 3,219 files
and the same case reopened again. A subsequent repair on this installed R3
restored the missing `notices/Microsoft-runtime-recipient-terms.txt`. The
independent inventory and the reopened case both verified.

On the originally empty test branch, the standard user reviewed the
uninstaller's inventory of four synthetic cases, separately confirmed their
deletion, read the four-deleted result and confirmed application removal.
There were zero partial deletions, managed case files, program registrations
or shortcuts afterward. All 23 external files retained their bytes. The
four Windows shell defaults for PDF, JPEG, JPG and PNG matched across the
baseline, install, preserve removal, reinstall and confirmed removal.
The reviewed profile contained only the four test cases. The lifecycle
receipt is `r3-installed-matrix/clean/lifecycle-result.json`, SHA-256
`0477ee070f583c1c1ae8628fa6edaccbcc78c0ca6591036f02eef4cfd1ade165`.
The independent comparison is
`r3-completion-evidence/clean-lifecycle-independent.json`, SHA-256
`74508fa7694869ea32a16ebb64b8b0833990720c7c94ba0b25223b904f92695a`.
