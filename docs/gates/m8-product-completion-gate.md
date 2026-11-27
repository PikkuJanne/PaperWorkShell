# Milestone 8 product-completion gate — PWS-0808

- Review date: 2026-11-27
- Artifact admission: **PASS** for unsigned engineering candidate `pws0807-tab-focus-engineering-r3`
- Product-completion gate: **PASS** for the exact unsigned R3 candidate and recorded support scope.
- Candidate: PaperWorkShell `0.0.0`, channel `engineering`, Windows 11 x64
- Build source: `54e0b916ac069ce00a0ce200c43e07f3a76d1784`
- PWS-0807 qualification and PWS-0808 starting revision: `20c62ed104e8a230b56854e9a0d5238bce1fde10`

## Artifact admission

The candidate is admitted to the M8 review on its exact byte identity and observed
unsigned state under PWS-D044. The host review used the retained files under
`output/pws0807/` and the frozen Git revision. All 13 direct prerequisites and
83 transitive prerequisites are `done` in the task graph at the starting revision.

| Artifact | Bytes | Independently verified SHA-256 |
| --- | ---: | --- |
| Qualification index, `r3-completion-evidence/qualification-index.json` | 49,527 | `4cfe72f050a2abd654a2b35efa843e0125c29c8b314c3713cee89d0e8a3794bc` |
| Qualified build handoff | 2,904 | `71819c9b00b33be65ecdde772020850cde32517a6fb08084c244feab82640755` |
| `PaperWorkShell-pws0807-tab-focus-engineering-r3-win-x64-UNSIGNED.exe` | 513,023,918 | `7791cef6be0062319a9c9f39a4bf130ded8f3914485eb50ab4e07d182c992889` |
| Installer receipt | 1,679 | `057407a387e55bacff420deec7e1cc0689c3dffa09317b280f2b5f8f391b9b74` |
| Resolved inventory | 9,638,270 | `a436ca25db576c7cc60936428381ee4e747030b869a77d6c5342c7b15df202a4` |
| CycloneDX 1.6 SBOM, `bom.cdx.json` | 12,704,822 | `5f925fd49afc7c1e5cbc5b95868c6b8f24503d150f1d916af4420e1435371a42` |
| SPDX 2.3 SBOM, `bom.spdx.json` | 5,366,726 | `f73be051f5ffb59bfdaa0445c0b7cc8f3f736b0276c8bb8736bad8f21f506379` |
| Frozen engineering interface | 36,332 | `db9ad671e63c93bd51daa3324f7b302ca3a80da51a761e5861a897e82dc8a6e8` |
| `PaperWorkShell-R3-synthetic-sample.zip` | 4,173,788 | `027410d15b8a62874d535fca5fdc625e46e272667a07b3c8096920f217b4f468` |
| Sample manifest | 13,446 | `a51a4ccf34b47793e4495b00e9300479264ad0f2f33780caf4722aea887eee90` |

### Provenance, signing, and packaged inventory

- Recomputed size and SHA-256 for all 246 qualification-index references, including
  12 tracked documents verified against their exact Git blobs. The qualified handoff
  at `r3-build-preparation/pws0807-tab-focus-engineering-r3/qualified-build-handoff.json`
  and the index agree on the source, installer, receipt, inventory, and installed marker.
  The source is an ancestor of the qualification commit. The canonical build receipt
  (`build-result.json`, SHA-256 `041b3fc3b9f808c941425ce17a9ef54a136f2f3095dd0422c2d43d68d26ede42`),
  three build logs, and all five prepared setup bundle members matched their recorded identities.
- Windows `Get-AuthenticodeSignature` returned `NotSigned` with no signer or
  timestamper certificate. The x64 PE header had an empty certificate table.
  The installer receipt agrees with the observed SHA-256, size, inventory and internal
  distribution scope. Its `qualification: not-run` records the build-time state.
  Installed engineering observations reside in the later PWS-0807 qualification index.
  An in-memory comparison with a changed expected installer hash was rejected.
- The source-to-payload comparison verified all 345 bound application files across
  the exact Git revision, wheel, source distribution and staged inventory. The installed marker SHA-256 is
  `5e564b0fef1b5e0ebe30ab19bdfe1d8c65872420889ac35c82d7237c73ab5f1e`.
  The exact-source release tooling rehashed 3,219 staged files totaling
  650,884,602 bytes, including 729 notice files, plus 178 build inputs and 47 review
  inputs. It reproduced the stage audit and matched all 3,219 entries in the
  receipt-bound installer file and target-check lists. Retained clean, repaired
  and replacement inventory receipts report the same resolved inventory SHA-256,
  file count and byte count. An in-memory change to one staged-file digest was
  rejected with `RELEASE_STAGED_HASH`.
- The release tooling reproduced both SBOMs and `THIRD_PARTY_NOTICES.txt` byte for
  byte. Both SBOMs passed the vendored official schema validators, describe 957
  components and agree on the SHA-256 and SHA-1 of all 3,219 payload files. The SBOMs
  record SHA-256 `bb52f260856bfb9f34f1af6e80a9e8fa61a71da127089aaa7d61d26ffbfbf4d3`
  for their normalized inventory input. The installer receipt hashes the raw resolved
  inventory shown in the table. The retained release-materials audit is bound at
  `r3-completion-compat/verified-release-materials.json`, SHA-256
  `b8b4875fe982baaa559dae42da2e72c423a211cab2290a2e007f26428e160530`.
- The external `THIRD_PARTY_NOTICES.txt` is generated from the full inventory and has
  SHA-256 `b09a78d7889d96bb12aaa977cee5ce004360c1e39a765e5539c4d816fc31c566`.
  The installed `notices/COMPONENTS.txt` is generated before adding its own entry and
  has SHA-256 `51a5727a149a3f990519b8b41e80e0610d54967c75f24c496e078b8265f334d1`.

### Engineering interface and synthetic sample

The frozen copy in `r3-completion-sample/` matches the [release interface](../release/RELEASE_INTERFACE.md)
at the qualification commit. Its engineering revision is `pws0807-r3-interface-1`.
All 74 relative file links resolved in the repository. The linked
[release evidence](../release/RELEASE_EVIDENCE.md) and sample proof identify the same
candidate, source, installer, and release materials.

The synthetic ZIP has 30 unique safe members. CRC checks passed, and all 29 manifest
rows matched the member sizes and SHA-256 values. The embedded manifest matched the
external copy byte for byte. Independent reopening covered 24 valid PDFs and three
nested ZIPs and refused the deliberately truncated PDF and ZIP. Twenty-eight members
match the R2 sample exactly. The README and manifest carry the R3 bindings.
The retained clean and replacement runs each bind seven input files to this ZIP and
match 11 workflow outputs to frozen references. Each separately generated support ZIP
matches its own receipt and the frozen allowlist.

## Milestone, compatibility, accessibility and resource evidence

All M0–M7 gates and the required M8 prerequisites are recorded `done`. The
earlier gate decisions apply at their recorded scope. The exact R3 evidence adds
the installed and packaged observations summarized below. The [R3 qualification ledger](../development/pws0807-r3-qualification.md)
maps each earlier milestone to its candidate evidence, and the
[Windows support matrix](../release/WINDOWS11_X64_SUPPORT.md) identifies the
recorded configurations. The checks below independently matched 30 receipt and
artifact digests across the R3 matrix and the linked M6 approval evidence.

### Windows 11 x64 and reader compatibility

**Pass within the tested scope.** The installed matrix covers Windows 11 Pro
x64 build `26300.9711` in an offline Hyper-V guest, with a genuine standard user
and Smart App Control Off. Exact-payload component runs used Windows 11 Pro x64
build `26300.9974`. Support claims for other editions, builds or execution
policies require their own evidence. The historical Enterprise runs and the
installer's technical build floor retain their original scope under PWS-D043.

R3 produced the same five-page M3 overlay PDF and the six M5 compression PDFs
and ZIP byte for byte. The accepted Windows Adobe Reader/free-mode, Edge, Chrome
and Firefox matrices therefore apply to those exact outputs at the recorded
reader versions and dates. The accepted Android observation covers exported
files. Fresh R3 checks also compared document pixels on page 3 of the color
PDF in Edge `158.0.4370.45`, Chrome `158.0.8127.38` and Firefox `161.0`.
The raster size search made four measured attempts, reached the 200 DPI and
JPEG-quality-80 floor at 212,711 bytes, preserved the pre-existing file and
refused the one-byte target. [Overlay reader matrix](../compatibility/pws-0307-overlay-reader-matrix.md) ·
[M5 gate](m5-compression-compatibility-gate.md) ·
[R3 reader evidence](../development/pws0807-r3-qualification.md#reader-files-compression-and-update-trust).

### Independent permanent-removal approval

**Pass for the one-active-affected-PDF-page profile.** The retained independent
M6 approval at `output/pws0608-repair/independent-final-source-approval-v1.json`
has SHA-256 `7836d583e75706999fbc49bf47cf2486cfe3850be5644b697d735c98dfd6b196`.
All 35 removal-named application, PDF and worker source blobs match between
the independently approved snapshot and the R3 source. The six changed
removal-named files cover manifest-version acceptance, localized report text
and UI presentation. The installed R3 desktop completed the separately reviewed
export. Its report recorded `REDACTION_EXPORT_COMPLETE` with six `RV_CLEAR`
results, including independent OCR. Independent reopening, rendering and
English OCR of the actual image-only derivative found the outside control and
no marked target. The source and managed original kept their hashes.

R3 also reapplied the packaged built-in verifier to ten retained named-viewer
saves and resaves with their exact-file OCR and reader receipts. The unsafe
control was refused. Fourteen cases of failed or unknown verification methods
blocked publication. Separate named-reader evidence qualifies the release
profile. For the newly generated derivative, the runtime report records the
six checks actually run. [Independent M6 gate](m6-permanent-redaction-gate.md) ·
[R3 removal evidence](../development/pws0807-r3-qualification.md#permanent-removal-on-the-clean-installation).

### Keyboard, NVDA and display

**Pass.** The clean installed keyboard journey started at the case chooser,
imported the four-category PDF, applied constraints, confirmed assignments and
completed a combined export and saved summary. An incomplete assignment set
blocked export. All three package modes and their summaries were independently
verified. Actual NVDA `2026.1.1.55980` speech and native focus covered ten
groups with 477 phase-correlated entries, including consent, cancellation, error
recovery and package summaries. Twenty-four forward and reverse focus checks
covered 96 DPI, 192 DPI and 192 DPI with Windows Aquatic. Five paired captures
located the focus outline on both tab bars. Exact packaged Qt checks add 645
accessibility and input tests, 217 subtests and 143 retention and assisted-use
tests. The installed keyboard and NVDA observations were agent-operated.
[M7 gate](m7-accessibility-assisted-gate.md) ·
[R3 keyboard journey](../development/pws0807-r3-qualification.md#clean-keyboard-only-checklist-and-package-outputs) ·
[R3 screen reader and timing](../development/pws0807-r3-qualification.md#installed-nvda-and-first-preview).

### Resource budgets, slow I/O and worker boundaries

**Pass for the measured software workloads.** The exact installed-layout
runtime passed ResourcePolicy v1 normal and constrained mixed cases with
sampled process-tree working sets of 196,845,568 and 197,033,984 bytes. The
software-rendered GUI heartbeat peaked at 138.78 ms against 250 ms. Four
delayed-I/O GUI controls and 31 storage and fault cases passed. The restricted
worker and its child could not connect to tested loopback listeners that the
control processes could reach. After active cancellation or forced worker
termination, a fresh retry succeeded and the checked state was preserved.
The quiet-host constrained OCR run completed a verified 12-page derivative,
with peak sampled tree working set of 341,299,200 bytes against 1,342,177,280,
complete private temporary-storage measurement and cleanup. The earlier
incomplete measurement retains its original result. The four installed PNG
imports met the 250 ms acknowledgement target, and the larger preview landed
within a 1,407.66 ms cold upper bound and 1,526.81 ms maximum warm upper bound.
These are capture-return bounds for the tested scenarios. [R3 resource results](../development/pws0807-r3-qualification.md#native-resource-limits-and-offline-worker) ·
[OCR workload](../development/pws0807-r3-qualification.md#constrained-searchable-export).

The R3 installer exhausted the standard user's actual NTFS quota at the ordinary
target on the guest system volume. Native Setup refused at startup and rolled
back after a separate partial-copy error. Existing managed and external files
kept their hashes. The whole-system-volume exhaustion scenario is a separate
check from that quota evidence. [R3 quota results](../development/pws0807-r3-qualification.md#installer-disk-exhaustion-and-rollback).

## Lifecycle, migration, recovery, support, and offline trust

Evidence paths below are relative to `output/pws0807/`. The read-only review recomputed length and SHA-256 for 132 relevant qualification-index entries and checked the artifact inventory and every evidence reference in the engineering disposition. All 13 direct prerequisites and all eight M0–M7 gates are done.

| Criterion | Reviewed result | Primary evidence and SHA-256 |
| --- | --- | --- |
| Clean install and launch | The disconnected Windows 11 Pro x64 26300.9711 standard-user guest installed R3 without development tools. Exit 0, ordinary desktop launch, per-user registration and three shortcuts were observed. All 3,219 files and 650,884,602 bytes matched the installer inventory. | `r3-installed-matrix/clean/install.json` `a65c6d6c9aef37aa14b347e7e2f80c0bcd5b90e2340b9d7997f50c45073a511b` · `r3-installed-matrix/clean/installed-layout.json` `ba47471a669161731fa7b84ec1c7beff46303a9d3b17876f8f78676459e65efc` |
| Same-build repair | Repair restored the deliberately removed Microsoft runtime terms file, reverified all 3,219 files and launched R3. | `r3-installed-matrix/clean/repair.json` `84933e63026890f88aa85e71b019d76900a847a1c1baf91b87968504fd027144` |
| R1-to-R3 replacement | The separate replacement branch preserved all 367 pre-existing case files and reopened an R1 case after R3 installation. Before and after, all 11 checked external files and four Windows file defaults matched. Subsequent use added one rebuildable thumbnail cache. | `r3-installed-matrix/replace/install.json` `6a1d071cb208d270fc3ae81f4e7a9af8e06b51fbffb1e3d41685de889f75079f` · `r3-installed-matrix/replace/preserved-existing-delta.json` `dbcf59afac8edf78b35e493eb57dd94b061dd7c82301855b3cba6db2530eefdb` |
| Preserve, reinstall, and reviewed removal | Keep cases retained four synthetic cases and all 31 managed files. The standard user reinstalled and reopened a retained case. Explicit review and confirmation removed the four cases. All 23 external files and the four checked file defaults matched. | `r3-installed-matrix/clean/lifecycle-result.json` `0477ee070f583c1c1ae8628fa6edaccbcc78c0ca6591036f02eef4cfd1ade165` · `r3-completion-evidence/clean-lifecycle-independent.json` `74508fa7694869ea32a16ebb64b8b0833990720c7c94ba0b25223b904f92695a` |
| Case migration | The installed chooser migrated schema 1, 2 and 3 cases to 4 with one revision increment and one exact backup each. External and managed original hashes, ordering, rotation and active pages agreed. Thirty-one exact-package native storage cases passed, including future and malformed manifests, interrupted backup/checkpoint recovery, injected disk-full failure and slow atomic rename. | `r3-installed-matrix/replace/migration-verify-0.json` `79a19421769307a500e78c0e298c13e8f84be3a6a77d8dbaeb175e34b42bac3f` · `r3-native-runtime/storage1.json` `8421bb244e6fc0b04511295b9bdc64946444bf971dd2ad598d2ae5e1fa442020` |
| Worker interruption | A terminated worker returned the stable failure code. A fresh worker reopened the same case and produced the retry preview. Manifest and source digests matched and no temporary files remained. Four simulated slow-storage GUI tests passed for flush, import, read and directory scans. | `r3-native-runtime/interrupt1/receipt.json` `c39303b403560cc6348d15d9579bbe8a2bbfe341f4acc1c029110d5473f9f907` · `r3-native-runtime/slow-ui/receipt.json` `c10a63a4905426617051e61dbaf41f04c8c8044497edb6acf9b7fa716f1e3f6a` |
| Installer low-space refusal and recovery | A per-user NTFS quota produced native error 112 before installation with 49,152 bytes available to the caller. A separate quota run logged 853 attempted destinations and stopped copying Qt6Gui.dll. After quota restoration, explicit Cancel rolled the partial target back and the normal reinstall succeeded. About 77.8 GB remained physically free on the guest volume. The next section records the whole-system-volume test. | `r3-installed-matrix/quota/r3-low-space/r3-preinstall-result.json` `746c6c1a7240d3374d0f626e1f134517b74370845ca57c2276bee88d94da8b65` · `r3-installed-matrix/clean/native-quota-rollback.json` `be9a01df4ab08bd471dec7ccbe011f8e067c6d640c90ae84fe806182c3d7c88a` |
| Diagnostics | The standard user previewed and consented to the two-member support archive. Independent reopening verified exact member identities, CRCs, privacy and the closed allowlist. The replacement exercises included cancellation, an occupied name, an over-limit destination and a successful byte-identical retry. The packed privacy suite also passed its injected disk-full cleanup case. | `r3-installed-matrix/clean/support-reopened.json` `dd6b6c99b3bd99b58c32c237ce991dbca40eeebcd7b975e6cd9ee6bf4c53f705` · `r3-installed-matrix/replace/support-reopened.json` `573789b6c227f130eba75bb1b8880a3785b90cca0ba06456d7c6e596d49c2a00` |
| Offline update authentication | Unprovisioned R3 refused the archived offer and left installation status intact. The R3 Windows verifier with the isolated public test anchor authenticated the historical 515,298,153-byte ZIP and refused replay, wrong channel, expiry and a changed signature. The exact-package test suite also refused missing and wrong signatures, malformed versions and changed, truncated or extended payloads. Historical slot activation and restoration remain attributed to the earlier isolated PWS-0803 runtimes. Live update is disabled. | `r3-completion-compat/update-rehearsal-crosscheck.json` `dc22003bfb02850a6822400ecf93e858d39f6d320e4be55476cfb952bc0166a1` · `r3-completion-runtime/support-trust.json` `7bed88268c0a415d0d32f1a0d6d1eab050636076bdbf2c7c2b1ad4f721f9fb57` |
| Offline isolation and standalone operation | The clean installed guest had zero active network adapters and zero ThirdLife-named processes, services or registration entries. The resolved inventory contained zero ThirdLife-named components or paths. The actual document journey ran offline. In the exact staged host runtime, reachable IPv4/IPv6 TCP/UDP controls received no traffic from the zero-capability AppContainer worker or its descendant. The worker could read approved input and was unable to write it or read or write outside scope. | `r3-completion-evidence/standalone-environment.json` `fcbeeb3e905cd49b6edb455ae72130ecfae7502540d37535e60b91808d9e185b` · `r3-native-runtime/loopback1/receipt.json` `3a06eb6d904b1e3a4a5e3c6dd309c013153ce690dd04dbad3b4b96b1a7394303` · `r3-tab-focus-evidence/static-runtime-independent.json` `efef3da6d1cc0313e8f0c8551f55d0163b6ebe834b75a2f5dc37416e29763d25` |

The native Windows host runtime was bound to the same installed-layout inventory in `r3-native-runtime/runtime-qualification.json` (`32ac283b2cef41d0e7596d93291cc6d5fe846cbbc172cf70743126245160b5ee`). Its `Run-Serial.ps1` ran the staged interpreter with `loopback.py loopback1`. The observer SHA-256 is `9cea13ea9dca516a44fa644f79da9d2533d290b8d0f6e5a8c4e02e8fd7df612b` and the receipt names the repository containment probes. The source-stage import and portfolio boundary checks each exited zero.

The native storage, slow controls, and support/trust JUnit files matched their bound receipts. Those suites recorded 31, 4 and 107 cases respectively, with no failures or skips. The clean installer separately refused changed installer bytes, mismatched inventory and an unsafe alternate-stream path before creating the ordinary target.

### Actual system-volume exhaustion

PWS-0808 used a disconnected disposable VM from the frozen R3 clean pre-quota
checkpoint. The independent test bounded its NTFS system partition at
28,821,155,840 bytes. NTFS quotas were disabled, and Windows Reserved Storage
was disabled in the clone after an earlier attempt showed that its reserves
could service writes even with zero caller-available bytes. All earlier
instrumentation attempts are retained separately.

With 32,768 bytes free on the volume, a limited interactive standard-user
control attempted to append 1 MiB and received Windows error 112. The exact
R3 native Setup displayed error 112 while creating a temporary file and
exited naturally with code 1. The existing per-user installation survived,
and the retained case and external file inventory matched its baseline after
the refusal. The observed result covers the setup-initialization failure.
Mid-copy rollback is recorded separately in the R3 per-user quota run.

The native failure receipt is `output/pws0808/system-volume-lab/native-failure.json`,
SHA-256 `6fdec256b3cd11181461da40f73c98f219488f63ed241b86ee5c34947e396948`.

After removing the filler, the standard-user recovery check found 7,377,076,224
bytes available. It independently verified all 3,219 installed files and their
650,884,602 total bytes, then started the exact R3 desktop with its normal
launcher. The same 31 managed and 12 external synthetic files matched before
exhaustion, after refusal, and after launch. The inventory and launch receipts
are `recovered-inventory.json` with SHA-256
`0fbd7c6425723072de163dc97d94155a6e879e4ba14c82e687515f9289172a22`
and `recovered-launch.json` with SHA-256
`08404c72ee5a77ff02b30c10538529b0b77076c36cf8e2690217a63fa740a944`
in `output/pws0808/system-volume-lab/`.
The joined case and capacity result is `system-volume-qualification.json`,
SHA-256 `ea0c5e9072c72aaca6e2dec55f0bd0691494a16245eed1bc3dacae43c74a4f89`.

The forced repair and the prepared-setup reuse observers each elapsed at four
minutes without a completed receipt in the disposable configuration. Those
attempts remain inconclusive. The verified recovery here is a complete payload
recheck and normal desktop relaunch. The earlier standard-user clean matrix
supplies the separate accepted same-build repair and reinstall results.

## Discrepancies and limits

The admission checks found no artifact identity discrepancy. The frozen sample
manifest's expected-behavior note for `expected/compression/grayscale.pdf` describes
pages 1 and 2. The reopened PDF has three pages, and the retained compression receipt
shows re-encoding on page 3. The companion release notes describe the complete
three-page behavior. The archived ZIP and manifest keep their original digests.
[R3 release notes](../release/RELEASE_NOTES.md#synthetic-sample).

The canonical release interface, evidence index, notes and standalone checklist identify
the exact R3 artifact. The original engineering sheet remains part of the PWS-0807 freeze.
Installed R3 observations apply to Windows 11 Pro x64 `26300.9711` with Smart App Control
Off. Earlier R1 and R2 failures retain their original attributions. Public distribution
requires separate owner authorization. [Exact interface](../release/RELEASE_INTERFACE.md) ·
[Evidence index](../release/RELEASE_EVIDENCE.md) ·
[Standalone checklist](../release/STANDALONE_RELEASE_CHECKLIST.md).

This review used local PaperWorkShell artifacts and synthetic files. The engineering
interface documents standalone behavior and PaperWorkShell's ownership of local case
data. Future ThirdLife compatibility remains a separate B4 decision.

## Formal decision

PWS-0808 accepts the identified unsigned R3 package for product completion
under PWS-D044. The independent review reconciled the earlier gates, the exact
installed workflow and maintenance paths, security and permanent-removal
approval, accessibility and reader coverage, resource limits, and the local
standalone envelope. The system-volume refusal and recovery close the remaining
disk-full criterion. The supported installed configuration and each historical
or component observation keep their recorded boundaries. The package retains
its embedded `0.0.0` version and `engineering` channel. Public distribution
requires separate owner authorization.

## Repository verification

- `py -3.14 tools/dev.py validate-docs`, `validate-task-graph`, `validate-portfolio-boundary`,
  `validate-release-inventory`, and `validate-schemas` passed.
- Focused `pytest` runs for `test_validate_docs.py`, `test_validate_tasks.py`,
  `test_validate_portfolio_boundary.py`, and `test_release_sbom.py` passed 209 tests
  and 149 subtests.
- Repository formatting, lint and type checks passed across 995 source files.

## Preserved checkpoint and freeze

The retained final R1 receipt
`r3-completion-evidence/final-active-r1.json` matched SHA-256
`2c322594c5f9138d9bd495e9a945198e94f89a621312fd886b86a8a51182cfef` and
identifies the active R1 checkpoint `999a5747-66e2-4565-baee-5e619dce8656`.
The host saved the active R1 session to free memory for the disposable clone.
After the test, R1 resumed offline with the same active checkpoint and disk
attachment. All 23 checkpoints remained, the source checkpoint disk SHA-256
matched, and the disposable VM and its writable disk were removed. The readback
is `output/pws0808/system-volume-lab/r1-preservation.json`, SHA-256
`4604fb7076c04208228a1ec53bb55c3a78ecea0787bb915d5bf109c8050bcb01`.

The exact candidate, original PWS-0807 index, final release documents and new
system-volume receipts are bound by `output/pws0808/completion-index.json`.
