# PWS-0807 release-candidate matrix

PWS-0807 qualifies the exact unsigned Windows 11 x64 product candidate and its
standalone release evidence under PWS-D044. This plan records candidate runs,
scoped engineering evidence, and prerequisites. The selected candidate is unsigned
`pws0807-tab-focus-engineering-r3`, version 0.0.0. R2's selected-tab focus
failure at 200% in Windows Aquatic remains in its original ledger. [Task graph](../../TASKS.yaml) ·
[Release evidence](../release/RELEASE_EVIDENCE.md) ·
[Standalone checklist](../release/STANDALONE_RELEASE_CHECKLIST.md)

The [R3 qualification ledger](pws0807-r3-qualification.md) records the current
artifact, synthetic sample, actual component and installed results. The
[R2 qualification ledger](pws0807-r2-qualification.md) records the predecessor's
replacement, workflow and resource evidence. The [R1 ledger](pws0807-r1-qualification.md) preserves the two
package timeouts that prompted the repair. The tables below retain each
starting environment and the required outcome for each area.

## Observed R3 scope

The [R3 qualification ledger](pws0807-r3-qualification.md) distinguishes
the installed standard-user desktop from the packaged runtime on the Windows
host. The guest was Windows 11 Pro x64 26300.9711, offline, with Smart App
Control Off. The expected results were frozen before guest testing in
`paperworkshell-pws0807-r3-synthetic-v1` under interface
`pws0807-r3-interface-1`. These are the current R3 observations.

| Matrix area | Evidence on this candidate |
| --- | --- |
| Provenance and standalone boundary | The build handoff, unsigned installer, 3,219 payload files, two SBOMs and 729 notices matched the independently retained reference. The standalone desktop completed the document workflow with no active network connection and zero named suite products, services or processes. [Artifact](pws0807-r3-qualification.md#exact-unsigned-artifact) · [Installed environment](pws0807-r3-qualification.md#permanent-removal-on-the-clean-installation) |
| Installation and normal launch | The clean standard-user desktop opened the chooser after exact per-user setup. Four invalid setup inputs left the ordinary target absent. The first one-page PDF reopened. [Setup](pws0807-r3-qualification.md#setup-refusal-and-offline-public-interfaces) · [Clean install](pws0807-r3-qualification.md#clean-installation-and-visible-keyboard-focus) |
| Same-build repair, removal and storage failure | The standard user reopened the preserved case after reinstall and after repairing a missing recipient-terms file. Actual NTFS quota exhaustion produced startup refusal and partial-copy rollback. Reviewed removal deleted exactly four synthetic cases, retained all 23 external files and preserved Windows file defaults. [Quota](pws0807-r3-qualification.md#installer-disk-exhaustion-and-rollback) · [Lifecycle](pws0807-r3-qualification.md#same-build-reuse-repair-and-reviewed-uninstall) |
| Sources, edits and primary workflow | Import preserved every external and managed PDF, JPEG and PNG digest. The exported three pages reopened in input order. The saved edit reopened in the order PDF, PNG, rotated JPEG. Three confirmed checklist categories blocked export and four enabled it. The combined, separate and ZIP outputs and three saved summaries matched the prospective sample. [Mixed files](pws0807-r3-qualification.md#clean-mixed-files-and-diagnostics) · [Packages](pws0807-r3-qualification.md#clean-keyboard-only-checklist-and-package-outputs) |
| Resources, offline behavior and recovery | Normal and constrained workloads measured process-tree memory, Job limits, heartbeat, cancellation, retry, cleanup and worker network denial. The constrained 12-page OCR export passed after an earlier incomplete storage measurement. [Native measurements](pws0807-r3-qualification.md#native-resource-limits-and-offline-worker) · [OCR workload](pws0807-r3-qualification.md#constrained-searchable-export) |
| Support and privacy | A reviewed, explicitly authorized support ZIP matched its own preview, exact two-member inventory and privacy checks. Malformed input preserved the case. A locally retained description-only report documents its recovery. [Installed support](pws0807-r3-qualification.md#clean-mixed-files-and-diagnostics) · [Packaged privacy](pws0807-r3-qualification.md#packaged-ui-retention-and-privacy-suites) |
| Accessibility | The four-category combined package was completed by keyboard. Focus stayed visible on both repaired tab bars, including at 200% in Aquatic. [Keyboard](pws0807-r3-qualification.md#clean-keyboard-only-checklist-and-package-outputs) · [Focus](pws0807-r3-qualification.md#clean-installation-and-visible-keyboard-focus) |
| Reader compatibility, compression and permanent removal | Overlay and compression reference files reproduced. Independent readers checked archived packages, fresh Windows renders and existing viewer resaves. Runtime and independent checks verified the installed one-affected-page derivative, including OCR of the outside control. [Readers](pws0807-r3-qualification.md#reader-files-compression-and-update-trust) · [Archived packages](pws0807-r3-qualification.md#archived-package-shapes-and-target-refusal) · [Removal](pws0807-r3-qualification.md#permanent-removal-on-the-clean-installation) |

## Candidate and environment inventory

| Environment or artifact | Available evidence | Use for PWS-0807 |
| --- | --- | --- |
| Disconnected Windows 11 Pro 26300.9711 x64 Hyper-V guest, genuine standard-user desktop, existing installation | Unsigned TRACE B8, source acc0b2621259975c6d418807488ce239b338ac05. Installer SHA-256 6b2dce059dc2ef2f02e8fc5206e5aacf285af472dfd281d7785881c9c809d33e, installed marker c5499f269b524a169848cc27681c1cee019cbfb75f04f6fbe30979a07c1363a3. Same-build repair, reuse, launch and ordinary offline PDF export have scoped receipts. | Engineering workflow rehearsal and defect isolation. Qualify clean installation in a clean environment. [PWS-0814](pws0814-automated-setup.md) |
| Windows 11 Enterprise 26100.9711 x64 Sandbox, genuine standard-account sessions | Historical unsigned R6/R7/R9 results. R9 SHA-256 d29f7f6d081c0c43295ba8a3d5bb215344cefa6253a401be34ef0f74941bd9d4. The owned R9 guest was retired after verified backup and preserve-case removal. Its volume lacked NTFS quota support. | Evidence for the recorded artifacts and operations. Prepare a clean x64 environment for any exact edition/build claim. [x64 qualification ledger](pws0802-x64-qualification-20261104.md) |
| Windows 11 Pro 26300.9711 x64 offline diagnostics rehearsal, standard user | Unsigned PWS-0807 R1, source e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec, installer SHA-256 a0d809322ea2c29066d26134c649f9ee1a883f4268403ea9c4e2d9e92bf8aaf8. | Exact R1 passed installed selected-name ZIP save, preview identity/privacy, and occupied-name refusal. The PWS-0814 entry separately completed B8-to-R1 replacement with all 3,219 payload files verified and all 353 baseline case files preserved. [R1 diagnostics](pws0807-diagnostics-investigation.md#packaged-verification-on-unsigned-r1) · [R1 setup continuation](pws0807-unsigned-completion.md) |
| Separate Windows 11 Pro 26300.9711 x64 update rehearsal, standard user and offline | Unsigned R1 bootstrap SHA-256 d4dc66045e4ee89be881e5c32a93ed8c9ff279b2bd53ce6a03dae3a3db1b018c. Isolated test-signed R2 runtime ZIP SHA-256 33bed87aaa04be6f382305d66420fd087ab14380977b20ac1464a7d038030960. | Authenticated-update mechanism rehearsal within the isolated test trust configuration. [PWS-0803](pws0803-authenticated-updates.md) |
| Clean, contained Windows 11 x64 environments for the proposed support claims | Required exact editions and builds are TBD. Capture actual editions, builds, architecture, target scope, privileges, NVDA and PDF-reader versions, network state, and installed product baseline before each run. | Start from fresh user and machine state for clean installation. Use separately seeded states for update, migration, removal, and recovery. [Windows support boundary](../release/WINDOWS11_X64_SUPPORT.md) |
| Windows 11 Pro x64 26300.9711, offline standard user, disposable Hyper-V checkpoint | An enforced per-user NTFS quota on the guest system volume limited writes at the ordinary R1 installation target. | With 65,536 bytes available, native Setup refused temporary-file creation before creating its target. With 64 MiB available, copying `Qt6Gui.dll` ran out of disk space and Cancel rolled back the installation. Five managed files and three external files matched their pretest hashes. [R1 quota evidence](../release/RELEASE_EVIDENCE.md#r1-installer-disk-exhaustion) |

The 2026-11-27 PWS-0802 run adds clean per-user install, normal shortcut and
Notices launch, same-build repair, preserve removal, reinstall and case reopen,
and separately confirmed case deletion for diagnostics R1 on Pro 26300.9711.
The installer and payload audit reproduced the component notices and both SBOMs.
The preserved removed-state checkpoint `PWS0802-R1-removed-product-case-20261127`
contains the application-created synthetic case and external preservation files
for the bounded installer disk-exhaustion check. On that branch, after restoring
the quota, the prepared installer exited 0 and verified all 3,219 files. The
freshly launched desktop exited 0 and the case and external files kept their
pretest hashes.
[R1 installer qualification](pws0807-unsigned-completion.md#pws-0802-installer-provenance-and-repair)

Before testing, record the exact installer and payload inventory, source revision,
lock, SBOM, component licenses, sizes, SHA-256 values and observed signing state.
Compare the installer and inventory against the independently retained build
receipt. Pin matching release-interface and sample revisions in each receipt.
Any byte-changing rebuild creates a new identity. Reassess the affected cells
against that candidate. [Windows qualification plan](../../packaging/windows/QUALIFICATION_PLAN.md) ·
[Release interface](../release/RELEASE_INTERFACE.md)

## Verification cells

Each cell requires a result for the identified candidate and scenario. Prior observations retain
their original verdicts. Record pass, fail, not run, or unsupported per actual scenario. An
unsupported result requires a product reason, the visible behavior where
applicable, and the documented in-app route. Record failed attempts and
preservation or cleanup results separately.

Bind each receipt to the exact candidate and installed marker, interface and
sample revisions, environment and scenario starting state. Record the entry
point, expected behavior, actual result, output hashes, preservation checks and
receipt digest. Use known-folder notation and synthetic filenames in published
evidence. A not-run cell names the missing prerequisite. A failed cell records
the actual defect or observation and the evidence for any later retest.

| Area and owner | Prior evidence | Required candidate outcome |
| --- | --- | --- |
| **Artifact admission and offline deployment** · packaging operator | Exact R1 clean setup verified the unsigned installer and all 3,219 payload files. Changed installer bytes, a mismatched inventory and an unsafe path were refused. [R1 installer qualification](pws0807-unsigned-completion.md#pws-0802-installer-provenance-and-repair) | Use the exact R1 admission, install and standard-user launch receipts. Recheck affected operations for a different candidate or supported environment. |
| **Primary workflow and standalone operation** · application operator | On offline Pro x64 26300.9711, the R1 standard user added PDF, JPEG and PNG through the app. The three originals, managed copies and manifest digests matched. The ordinary combined PDF reopened on the host and rendered in import order. B8 separately refused an occupied output name. [R1 mixed-input result](pws0807-unsigned-completion.md#installed-mixed-input-pdf-export) · [M4 gate](../gates/m4-pilot-gate.md) · [B8 walkthrough](pws0806-documentation.md#installed-documentation-walkthroughs) | Retain the exact R1 import and ordinary export result. Verify edited order and rotation after reopen, readiness, checklist review, combined/separate/ZIP package outputs, package summary and constraints. Exercise the remaining standalone, restart, association, OS file-opening and portable-project assertions under their documented scope. |
| **Local replacement, repair and recovery** · packaging operator | B8-to-R1 replacement preserved 353 prior case files. Same-build R1 repair restored a missing payload file and reopened a saved one-page case. [Installer lifecycle](../release/RELEASE_EVIDENCE.md#3-install-launch-update-repair-and-uninstall-evidence) · [Isolated update rehearsal](pws0803-authenticated-updates.md) | Retain the exact replacement, repair and quota failure results. Verify applicable case migration on the selected packaged candidate and record its update configuration. |
| **Case migration and application recovery** · storage and application operator | PWS-0804 source/native-storage tests cover supported v1/v2 to v3 paths, exact previous-manifest backups and failure cases. [Migration result](pws0804-result.md) · [Compatibility fixture index](../../tests/fixtures/manifests/release-compatibility-v1.json) | Open disposable supported older cases through the packaged app and confirm migrated intent and immutable source hashes. Check exact backup, restart/reopen, processing interruption, cancellation and recovery. Future-version, invalid, interrupted or storage-limited cases must fail safely or recover under the documented contract. |
| **Uninstall, preservation, reinstall and explicit data removal** · packaging operator | R1 preserve removal, reinstall and reopen, and confirmed deletion of one reviewed case have receipts on Pro x64. The R9 ledger records earlier unavailable-case, partial-result and Cancel behavior. [R1 removal evidence](../release/RELEASE_EVIDENCE.md#3-install-launch-update-repair-and-uninstall-evidence) · [R9 ledger](pws0802-x64-qualification-20261104.md) | Use the measured R1 residue, external file hashes and file defaults. Verify any remaining claimed case categories, Cancel or failure paths against the applicable packaged configuration. |
| **Resources, failure cleanup and offline worker boundary** · resource/security operator | Source budget suites and constrained pilot results establish software behavior for their tested profiles. V12 warm PNG timing exceeded 2,000 ms and its measurement limitations remain. The PWS-0811 preparation completed no timed B8 import. Earlier keyboard and NVDA evidence retains separate composition failures. [PWS-0811](pws0811-repairs-20261106.md) · [Keyboard results](pws0701-result.md) · [NVDA results](pws0702-result.md) | Record central ResourcePolicy version and actual enforced normal/constrained settings, profile, workload hashes, full process-tree memory, worker Job limits, CPU/time, caches/temp, concurrency and output size. Exercise slow I/O, simulated low space, cancellation and cleanup, malformed input, CPU-only operation and packaged egress denial. Keep the actual ordinary-target disk-full installer result distinct from simulations. Recheck reported timing and affected compositions using the exact candidate and a qualified observer. |
| **Diagnostics and privacy** · support operator | PWS-0805 verifies source behavior and the generated sample. PWS-0806 recorded B8 preview/consent, two failed saves and a description-only fallback. Exact unsigned PWS-0807 R1 passed normal ZIP save, preview identity/privacy, occupied-name refusal and preservation. The continuation verified native picker cancellation, picker permission denial, the application's safe-save refusal for a separate over-limit filename, and successful retry with an independently verified ZIP. [Source diagnostics](pws0805-result.md) · [R1 diagnostics](pws0807-diagnostics-investigation.md#packaged-verification-on-unsigned-r1) · [R1 continuation](pws0807-unsigned-completion.md) | On the genuine standard-user desktop, check exact inventory before consent. Save through the public picker with a synthetic case open, then verify the reopened archive's digest and exact members. Scan with synthetic prohibited tokens. Check cancellation, existing-name refusal, rejected destination or storage failure and cleanup, and the offline description-only fallback. |
| **Accessibility and assisted use** · accessibility operator | M7 source gate includes native keyboard, NVDA, scaling, contrast and assisted-use evidence. B8 proved scoped configuration-pane readability and focus at 96/192 DPI. [M7 gate](../gates/m7-accessibility-assisted-gate.md) · [B8 installed accessibility](pws0813-trace.md#installed-b8-verification) | Run the packaged keyboard-only journey and named NVDA scenarios. Capture visible focus, names/states, announcements, recoverable errors, consent/retention, accessible summary, 200% scaling and high contrast. Record agent-observed interaction and human judgments separately. Use one resumable helper for any human observations and estimate the full journey before handoff. [Human-testing policy](human-testing.md) |
| **Reader compatibility, compression and redaction** · compatibility and security operators | M3/M5 reader observations apply to their exact PDFs. PWS-0608 source approval describes the qualified single active affected PDF page and runtime verifier. Earlier viewer-resave observations retain their limits. [Overlay readers](../compatibility/pws-0307-overlay-reader-matrix.md) · [M5 gate](../gates/m5-compression-compatibility-gate.md) · [M6 gate](../gates/m6-permanent-redaction-gate.md) | Verify candidate-generated compression/overlay files in supported Windows Adobe Reader, Edge, Chrome and Firefox configurations and record exact versions. Confirm quality-floor refusal and ordinary export reopening. Exercise the independent redaction release profile, candidate rendering and extraction checks, required reader/resave behavior and export blocking on verification failure. |
| **Evidence freeze and release blockers** · release owner | The interface, index and checklist remain drafts. [Release index](../release/RELEASE_EVIDENCE.md) · [Standalone checklist](../release/STANDALONE_RELEASE_CHECKLIST.md) | Audit every public file for prohibited personal data, private paths, unapproved fixture content and misleading claims. Freeze the tested artifacts, sample package, exact interface/evidence revisions and limitation inventory together. Identify unresolved release-blocking results for sign-off. |

## Engineering rehearsal results

- The standard-user B8 comparison isolated the support ZIP picker handoff.
  Starting with an empty filename selected the intended path and produced a
  reopened ZIP matching the reviewed digest and exact file inventory. The
  source fix and regression passed. [Diagnostics investigation](pws0807-diagnostics-investigation.md)
- Unsigned R1, built from commit e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec,
  passed the installed standard-user support ZIP scenario with a synthetic
  case open. The native picker honored the submitted filename, the reopened
  archive matched the preview and the occupied ZIP stayed byte-for-byte intact.
  Candidate-level diagnostics evidence applies to these observed assertions
  on exact R1. The follow-up established native picker cancellation, permission
  denial at the picker, and publication refusal at the application. The same
  preview saved at a supported name on retry. Independent inspection matched
  the two permitted members, CRCs and privacy checks. No staging file remained,
  and the original input and occupied ZIP stayed unchanged. [Packaged R1 verification](pws0807-diagnostics-investigation.md#packaged-verification-on-unsigned-r1) · [Unsigned continuation](pws0807-unsigned-completion.md)
- The existing canonical installer smoke ran the exact B8 installed runtime on
  the disconnected Pro guest under constrained-v1. The separate launch receipt
  qualifies the standard-user token and session. The component smoke imported
  PDF and PNG, generated two previews, reopened a verified two-page PDF after
  rotation, and processed one English OCR page. It refused unavailable input
  and an occupied export name. The external originals and the existing case
  inventory were unchanged, with zero temporary files remaining. Final case
  and cache sizes were 318,825 and 290,512 bytes. Peak resource measurements,
  slow I/O and disk-full were outside this run.
- The smoke report at
  `output/pws0807/constrained-smoke-prep/receipts-b8v1/smoke-result.json`
  has SHA-256
  `eeb8e1e0978ec938bd71b93583fd531b850ff80557e4d9ca89269d2ebe8bad03`.
  The separately retained synthetic PDF has SHA-256
  `a40627abae5a8725bbcba247b2af0478aadd63925dc097f3ee05e5b434c70af7`.
- The environment probe found Edge 157.0.4341.42 in a conventional installation
  path. Chrome, Firefox, Adobe Reader and NVDA did not resolve at the
  conventional paths checked. Record actual qualified copies and versions in
  the final reader and NVDA scenarios.
- In the recorded B8 run, after the diagnostic and constrained-smoke receipts
  were retained outside the guest, the owned pre-test checkpoint was restored. The active checkpoint is
  `PWS0807-before-original-diagnostics-reproduction-20261126`. The exact B8
  marker and all 353 baseline case files matched the pre-test receipt.

## Earlier rehearsal sample

The local engineering rehearsal archive is
output/pws0807/rc-sample-preparation/paperworkshell-pws0807-rehearsal.zip in the
primary checkout. It contains 12 members, including the manifest, instructions,
mixed PDF/JPEG/PNG and malformed inputs, a four-category packet, and sample
outputs and summary. The archive is 79,466 bytes with SHA-256
3f99132e414566b664dc1ccbaaed926525da549daae7c33db41d01f95bdd77f0. Its separate
verification receipt records repeat generation, archive reopening and hashes,
source/reference PDF text and render matching, and privacy checks. The sample
use instructions identify the earlier outputs as engineering references.

The checked-in [synthetic corpus index](../../tests/fixtures/synthetic/v1/index.json)
records input provenance, and the [M4 pilot walkthrough](pws0408-pilot.md)
defines the four-category package workflow, expected assignment and summary
checks. The one-page born-digital PDF has SHA-256
3f62fe484b8b389ec18afe115939d491f795e517195a7acf7fe5989075839c47. The PNG OCR
fixture has SHA-256 f2976a85d04b992bd690d8a2b70faf8444197d87ce58a7a5687a778c97f3fd82.
The [existing generated support sample](../../tests/fixtures/diagnostics/v1/README.md)
has SHA-256 4f5bb7b36f1b58f36901f31c18b721a6875a44bc731fb8da3779e39b77662304.
Use its fixed two-member inventory as a documentation example. Compare a live
bundle against its own preview digest.

Reopen candidate-generated combined, separate and ZIP results, and save and
compare the accessible summary. Bind each generated file to its workflow, input
digests, candidate, expected behavior and individual SHA-256. Keep the internal
migration fixture distinct from a portable case/project deliverable. Current
public project transfer is unsupported. Publish the mechanical fixture and
expected-output entries through the existing release documents and keep
engineering sample files identified as engineering evidence. [Release sample
inventory](../release/RELEASE_EVIDENCE.md#6-synthetic-release-sample-package)

## Original execution sequence

1. Bind the exact unsigned candidate to its installer, source revision,
   receipt, payload inventory and observed signing state. Carry forward only
   observations within their proved candidate and scenario scope.
2. Use the exact R1 results for the installer rows. Run the remaining
   packaged product workflows against the selected candidate, including case
   migration, verified exports, and the applicable resource and accessibility
   scenarios. Keep the Windows edition, build and starting state with each result.
3. Record all applicable product results and genuine failures against the exact
   candidate. Resolve supported editions/builds, version, reporting and remaining
   defects in the release documents for PWS-0808. External publication has its own
   authorization.

The work uses PaperWorkShell's existing standalone interfaces and synthetic
data. Each release remains independently testable with ThirdLife absent.
