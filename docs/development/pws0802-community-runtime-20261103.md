# PWS-0802 — accepted Community route and complete-runtime continuation

Current 4 November engineering outcome: R9 is built and separately qualified at
the recorded scope; R7 remains the preserved accepted baseline. The
[current x64 ledger](pws0802-x64-qualification-20261104.md#actual-r9-qualification--current-disposition)
supersedes current-status wording in the historical epochs below. It records
R9 `d29f7f6d081c0c43295ba8a3d5bb215344cefa6253a401be34ef0f74941bd9d4`,
512,732,779 bytes, actual NotSigned/internal, complete app-local inventory,
fresh standard-user workflow/repair/removal, completed finite reader and archive
results, and precise remaining real low-disk and signing gaps. Earlier failures and accepted
results below keep their original scope; neither R7 nor any prior receipt is
rebuilt or relabeled.

> **2026-11-26 supersession.** The signing-dependent status and next actions below
> describe the historical R7/R9 plan. PWS-D044 permits unsigned product completion.
> See the [current PWS-0802 continuation](pws0802-x64-continuation-20261104.md)
> and [candidate matrix](pws0807-rc-matrix.md) for remaining observations.

**Historical release status: BLOCKED; PWS-0802 was not done.** The current unsigned candidate
is R7, SHA-256 `a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a`.
Its independent artifact audit, focused source checks and canonical privacy/secret
scans pass. Actual standard-user installation, same-candidate repair, normal public
launch and preserve-default removal pass with unchanged bounded synthetic data.
Shipping removal controls were announced in a later first review after fresh
reader startup, before any UIA query. A separate fresh-reader run reproduced
title-only/stale context; intermittent initial reader-context reliability and its
cause remain unproved in those observations. The maintainer's 2026-11-04
PWS-D043 decision fixes Windows 11 x64 as the only target and supersedes every
earlier ARM64 prerequisite and environment request below; an unrun scenario has
not passed. Public trusted signing remains required. One nonbinding Foundation
inquiry is now authorized. Current finite requirements and inquiry status are in
the [x64 continuation](pws0802-x64-continuation-20261104.md), which supersedes
the earlier outstanding-action lists in this retained R6/R7 history.

R7 was exercised in the existing instrumented standard-user guest. The earlier
R6 clean Windows offline document smoke and interruption/recovery observations
retain their exact R6 identity and support only the independently verified
unchanged runtime, worker and document-engine boundary; they are not new pristine
R7 observations. The sections below retain their historical candidate scope;
the appended R7 records state the current build, observed results and limits.
PWS-0804 and PWS-0805 remain done. Earlier R1/R2 evidence and failures remain in
the [earlier report](pws0802-implementation-20261103.md).

## Maintainer authority and scope

On 2026-11-03 the maintainer explicitly accepted Visual Studio Community 2022
EULAID `VS_Comm_2022_ENU.1033` as an individual developer and authorized agent
acquisition and installation of required release components through official
Microsoft channels, without purchases. The maintainer authorized scoped
correction and implementation of Microsoft recipient terms, not verbatim approval
of the earlier proposal. Acceptance will not be requested again.

The exact agreement, component hashes, legitimate acquired product copy,
unmodified Microsoft files and final component-only terms must be bound before
the new app-local runtime enters the installer. MIT and other open-source rights,
including modification, replacement and redistribution, remain separate.
Installation and normal document use remain local, free and accountless.

## Implementation and proof plan

- Preserve the current 2,112-file source state, original Git HEAD/index and all
  accepted milestones before acquisition or edits. The local baseline is
  `output/pws0802/community-20261103/preservation-baseline.json`, SHA-256
  `1cb632a705eba04910c87f9e19f41c48fb0184dd079af48b8b8e923bee15a759`.
- Acquire and authenticate the minimal Community product copy and exact release
  redistributables; reconcile the inherited pikepdf runtime alias; record final
  terms, official sources, versions, licences and hashes.
- Integrate the approved app-local overlay through a fail-closed builder, present
  required recipient agreement offline, regenerate inventory/notices/SBOM and
  build a clearly unsigned internal candidate with unchanged product/upgrade IDs.
- Exercise exact payload/module origins, restricted-worker PDF/image/OCR/edit/
  verified-export workflows, repair/reinstall and preserve-default removal.
- Make the actual interruption cutpoint observable and reproducible, retain its
  state and verify recovery and case/external-file preservation.
- Diagnose computer-use binding independently, then perform real keyboard,
  focus, agreement, error and uninstall-choice observations on the new candidate.
- Prepare a lawful clean Windows environment and actual standard-account and
  architecture checks where available. Unavailable capabilities remain explicit;
  host-only results never become clean-machine or ARM64 evidence.

Relevant canonical format, lint, typing, schema, task, documentation, boundary,
unit, integration, security, accessibility and privacy/secret checks will be run
for changed code and candidate bytes. Failure, cancellation, low-disk and cleanup
paths are part of recovery qualification. Existing evidence is reused only when
its exact sources and bytes remain applicable.

## Product and release boundary

No sibling repository, data, service or compatibility dependency is introduced.
Cases and migrations remain PaperWorkShell-owned. Public trusted signing remains
required and unavailable until an approved provider/process can sign the exact
final bytes; this continuation authorizes no enrollment, purchase or publication.
PWS-0802 is not done and PWS-0803 remains dependent on its actual completion.

## Historical R3 build and independent artifact audit

| Artifact | Exact value |
|---|---|
| Candidate revision | `pws0802-internal-20261103-r3` |
| Installer | `output/pws0802/community-20261103/candidate-r3/installer/PaperWorkShell-pws0802-internal-20261103-r3-win-x64-UNSIGNED.exe` |
| Installer bytes | 512,656,714 |
| Installer SHA-256 | `b09774a442cdced3671062bd4a28318205112a893009ba3593232ed89827bb35` |
| Installer receipt SHA-256 | `f06780cb8a25861a1b992a8585af1321ad9eefa55c52fca40151b3a247842e90` |
| Inventory SHA-256 | `c94c2b5b8dc65da5f742644d2db4efab61040a5dc887fb11f9ad099ef3d23ec9` |
| Payload | 3,191 files; 649,978,313 expanded bytes; 957 components |
| Signature/distribution | Unsigned internal engineering candidate; public distribution disallowed |
| Product/application ID | PaperWorkShell / `io.github.pikkujanne.paperworkshell` |
| Stable installer upgrade ID | `{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}` |

`tools.build_windows_installer stage` and `compile` completed against the current
local source, accepted PWS-0801 R3 baseline, authenticated compiler and the new
approved overlay. The unchanged 319-file application wheel/sdist and native
launcher were reused after exact-source verification. There is no separately
installed Python, Visual Studio or repaired PATH lookup in the installed launch.

The independent actual artifact audit passes every payload identity, authentic
offline CycloneDX 1.6/SPDX 2.3 schemas, exact SBOM and notice regeneration, all
319 application sources and 896 current installer-source members. Only the two
legacy Microsoft routes are replaced, with five necessary dependency references
rewired. All other inherited component rights, obligations, 23 LicenseRefs and
seven replacement notices remain intact. Every old R2 runtime byte is unchanged
except the candidate marker; eight new runtime paths supply the added copies.
The pikepdf alias retains the same bytes, now independently resourced.

Audit receipt: `output/pws0802/community-20261103/artifact-audit-r3/audit-result.json`,
SHA-256 `2d07c1c391dc0053d582fd22823ad36ad98be833910d6e4ff9611661d0ce40b8`.
Its independently collected 1,216-source hash receipt has SHA-256
`05cc8d1c3103666f46d61a2066042a3a2455a91cc1ae8fdbfb9c8804d103f126`.
The outer installer is independently hash-bound; this data-only audit does not
claim independent unpacking, target execution or clean-machine operation.

## Final Microsoft route and local recipient agreement

The [acquisition result](pws0802-community-runtime-result.md) records the
authentic Community 2022 `17.14.44+37827.9` selected product copy: 155 files,
1,390,324,709 bytes, native official layout verification exit zero. No installed
IDE, sign-in or product activation was used. The exact accepted agreement and
official REDIST provisions are bound separately from the runtime's end-user
agreement; the earlier proposal was not approved verbatim.

The [final Microsoft-only recipient text](../../packaging/licenses/release-native/microsoft-msvc-runtime/COMMUNITY-2022-RECIPIENT-TERMS.txt)
is 5,466 bytes, SHA-256
`a5cdf1136b18611fc5235c657b7ad113a9ac338abe6c1af3c299205d9492c718`.
It lists the five unchanged AMD64 objects, version **14.44.35211.0**:
`msvcp140.dll`, `msvcp140_1.dll`, `msvcp140_2.dll`, `vcruntime140.dll` and
`vcruntime140_1.dll`. Nine explicit paths cover the Python root, the byte-identical
pikepdf loader alias and three qpdf-adjacent copies. The asset contains every
path/hash; the [source review](../../packaging/release-reviews/microsoft-community2022-v1.json)
binds official URLs, versions, signatures, hashes and original notices.

The local built-in installer agreement is these component-only terms. It starts
unaccepted and requires explicit assent before installing. A quiet install must
provide exactly one `/ACCEPTMICROSOFTRUNTIME=<published-terms-SHA256>` argument;
wrong, duplicate or missing acceptance is refused before application writes.
MIT and every other open-source grant remain separate, including Qt/LGPL rights
and applicable-law exceptions. The full Community agreement is supplied for
provenance; end users need no development-product licence or account.

## Current qualification observations

The source host remains **HOST_NOT_CLEAN**. Its token is an unelevated member of
Administrators with a linked token, so it is explicitly **not** an actual standard
account. A successful host observation cannot satisfy clean Windows or ARM64.

The fresh synthetic Qt control and actual R3 Inno wizard now accept returned
window bindings and real keyboard input. The historical identical-owner helper
failure did not reproduce; its underlying cause is not established or attributed
to the product. The current method is fresh `get_window` binding, state-derived
input and a fresh observation after each action.

Actual R3 preinstallation keyboard observations passed: default rejection and
disabled Next, keyboard selection enabling Next, local unsigned notice, final
review, visible focus, and cancellation through a safe-default confirmation.
Native cancellation exit was 2; setup windows, product directory and registration
were absent afterward. The existing production case root stayed ten entries,
count-only. No private case was opened. UIA document/focus fields sometimes
lagged immediate screenshot state; stable fresh observations were used. This is
not a completed NVDA or full packaged UI/accessibility claim.

The source-host Windows Pro licence includes Windows Sandbox entitlement under
[Microsoft's current documentation](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/).
The earlier feature-enablement attempt received elevation-required HRESULT
`0x800702e4`. The maintainer subsequently installed Sandbox; the feature is now
enabled and Codex has operated an actual Windows 11 Enterprise 26100.9711 AMD64
guest. The earlier enablement request is satisfied. Guest qualification results
are recorded separately below; the earlier unrun ARM64 investigation is now
superseded/outside scope under PWS-D043. No repeat Microsoft
agreement acceptance, environment construction or technical transcription is
requested.

## R3 actual host workflow, runtime origins and recovery

R3's installed synthetic PDF/image import, rotation/history, two previews, OCR,
verified export and reopen completed successfully through the packaged interpreter
and existing restricted-worker composition. The native observer recorded 16 product
process instances and separately identified 15 owned Windows console-support instances.
Independent reconciliation verifies all observed product images against the sealed
inventory and installed bytes. Their union includes all nine Microsoft module paths,
with the five authenticated hashes; no System32 VC runtime load was accepted.
A separate held qpdf process proves its own three adjacent CRT origins and native
synthetic PDF check, bound to that process's actual creation identity.

Preliminary observer attempts exposed qualification-helper Windows path casing,
relative-child-path and OS console-host classification errors. Those source versions,
failures and process cleanup remain retained. Independent review then caught the need
for executable-inventory binding and a qpdf-current-process gate. The corrected observer
makes job membership mandatory, accepts only inventoried executable identities, and
binds qpdf's exact three module paths to its held creation identity. Valid completed
workflow observations were retained; the dedicated qpdf check was executed separately,
without relabelling old observations as a new full workflow or token-security test.

The actual R3 repair interruption passed in one observed attempt: Windows reported
an acknowledgement-required blocking oplock break while the exact held installer
was live, an earlier deliberately missing notice had been restored, and the barrier
file's identity/hash remained unchanged. The controlled job was terminated once and
reached zero active processes before releasing the barrier. Recovery retained and
authenticated two known complete stages, removed those exact files, then passed
ordinary repair, all 3,193 installed-file identities, registration and uninstaller
binding. This proves controlled interruption and explicit repair recovery, not
automatic rollback or power-loss recovery. Fresh reopening of the prior synthetic
case preserved two pages/order, 90/0 rotations, source hashes and operation/export history.

Final R3 preserve-uninstall passed: application directory, registration and both
shortcuts are absent; four synthetic profiles and nine external originals/exports
remain unchanged. The real case root stayed ten entries, count-only.
The final controlled session has SHA-256
`949da2d6950816bb028b73262917be0fcd92ab3be120eeeba18a12a22d2e7e26`;
the final removal receipt has SHA-256
`e11aa7fa4208d03d50a03bbf2e8eeb3744163bba4cc006030e42a66ff9258e74`.

## R4 process identity correction

Actual R3 GUI interaction still failed before input: both the original Sky and
alternative CUA surface listed the embedded process under the unrelated IDLE
registration and rejected its window despite identical printed owner IDs. Its
normal process AUMID was independently correct before/after Qt. This is retained
as a helper failure, not an application accessibility failure or completed test.

A bounded diagnostic copied the authenticated 104,672-byte CPython GUI interpreter
without changing its bytes, signature or metadata, using the temporary filename
`PaperWorkShellDesktop.exe`. It resolved as the exact process path and accepted
actual keyboard activation. The controlled diagnostic ended normally, its job was
empty, and only the authenticated temporary file was removed. The original
interpreter and every sealed candidate remained unchanged.

R4 retains all original Python files and adds this byte-identical CPython-owned
GUI filename, with a PSF change-summary notice and preserved obligation text.
The native launcher changes exactly one fixed child-name literal. Installed
discovery accepts/requires the fixed GUI resource and exposes a read-only path;
its marker schema, dataclass fields and worker `python.exe` behavior stay unchanged.
Product/upgrade IDs, user-facing launch path, Microsoft component bytes/terms,
accountless free use and local processing remain unchanged. This identifies the
product process in Windows/support/accessibility tools without weakening their
ownership checks. R4 requires fresh affected packaged observations.

The new discovery regressions failed before correction and pass afterward:
71 focused unit passes with one existing synthetic-symlink capability skip.
The launcher fallback regression also failed before correction; all seven native
launcher tests now pass, with 15 unit and ten security passes. Its final receipt
is `output/pws0802/launcher-r7/launcher-receipt.json`, SHA-256
`87ce26214e9362eee7e713281d4f50a635067176a618cb4104d97b850ab27c0a`.
The exact 17-archive/4,575-file toolchain is reverified. The launcher still has no
CRT or delay imports. The new application wheel/sdist is built offline from the
current 319-file source; only installed discovery changed among those files.

## Preserved sealed R4 candidate and source bindings

| Artifact | Exact value |
|---|---|
| Candidate | `pws0802-internal-20261103-r4` |
| Installer | `output/pws0802/community-20261103/candidate-r4/installer/PaperWorkShell-pws0802-internal-20261103-r4-win-x64-UNSIGNED.exe` |
| Installer bytes / SHA-256 | 512,711,316 / `3df3dc557f27646fecf68cc7f4b789f1c521110873ac173ca3ce6a01570c3155` |
| Installer receipt SHA-256 | `2e8f06d562aec549e107c95227d6b7944bb3704546056494d8f6a34bafb0702c` |
| Inventory SHA-256 | `f695e632934f603d09b753fde8b5788fc0035385db7e2c0e0d2b8756fbcede25` |
| Payload | 3,193 files / 650,084,591 expanded bytes / 957 components |
| Application wheel | 1,797,463 bytes / `76913d441a60efbd74efb316ea1f152a3bfa87b297827485efba757b837ecc76` |
| Application sdist | 1,585,297 bytes / `e7d121e3c6b04b1b02c97d6665658cb42e0ccada9e8d061c5af0009832186536` |
| Installer-source archive | 896 members / 886,408 bytes / `f7f59ecc8125055fa47de0bc51f0fcd9f32a5e17995bfe40bdf7259cb7133b18` |
| Native R7 launcher | 23,720 bytes / `03682bc38dbe73bdb6a8349199089cc07f96e53acbf2264c11ef41f998d0d37c` |
| CPython-owned GUI copy | 104,672 bytes / `e3889c5020644b9ecfd9e678f95372b1e127e29e6c02b1129ed88c38381d0972` |
| Signature / publication | Unsigned internal candidate; public distribution disallowed |

The current source has these independently collected SHA-256 bindings:

| Source or proof | SHA-256 |
|---|---|
| `src/paperworkshell/app/installed_runtime.py` | `2f1852f5a6c603f070dd88bbd5e991420a6032a7c34156af9a2f886087389bfa` |
| `packaging/windows/launcher.c` | `598d18b385f235101e5551ddbf11f55cc9916df4269797cd5fffcb5db1140673` |
| `tools/build_windows_installer.py` | `d0c82d86d32f188d317a23e9b03b9b5921430f5e48af3ed5e6e5d526d92f110d` |
| `tools/qualify_windows_installer.py` | `40ac2f9e1977bee779b03b9f8a4d953e112e876c546c7512829055dac9d3589e` |
| R4 independent artifact audit | `0e08f4b55ce33d54f109b17d43b757b2f63c86c76492921798f4c92aefb98627` |
| R4 independent source-hash receipt | `abd1c4c958051199cf4101e637514459de4e74d3c967566526f6cf8c39815efa` |
| R4 independent workflow/origin review | `9090a859fce1421f88557b00f86ed141568be456b91d5ff880b0c39cd3e55e1a` |

The artifact audit and source hashes are under
`output/pws0802/community-20261103/artifact-audit-r4/`. It verifies every payload
file, authentic offline CycloneDX 1.6/SPDX 2.3 schemas, exact SBOM/full-notice and
installed-notice regeneration, all 319 current application sources and all 896
installer-source members. All third-party component records and all **24** current
R3 LicenseRefs remain exact, including the 23 previously inherited refs and new
Microsoft ref. The first-party component's wheel hash is refreshed; its MIT
licence and other fields remain unchanged.
The CPython obligation preserves its original text with only the exact filename
change summary appended. All seven replacement/relinking notices remain exact.

Compared with R3, the only changed existing runtime files are the launcher,
candidate marker and `app/installed_runtime.py`. The only new runtime path is
the CPython GUI copy; original `python.exe`, `pythonw.exe`, `python314._pth`,
all other runtime bytes and the nine Microsoft copies remain unchanged. The
first-party `notices/PaperWorkShell-CPython-GUI-ALIAS.txt` supplies the PSF change
summary without assigning MIT to the interpreter. Product/application/upgrade
IDs and manifest meanings are unchanged. The outer installer is hash-bound to
the frozen compile inputs; this data audit performed no independent unpacking
or application/installer execution.

## R4 qualification: actual results and remaining observations

| Check | Current evidence and scope |
|---|---|
| Quiet missing-terms refusal | Pass: native exit 1, no product/registration, real case count ten before/after. The quiet message itself was not captured. |
| Per-user install and exact installed files | Pass under the current host token; full payload/registration/uninstaller binding checked. |
| Observed repair interruption and recovery | Pass: exact live installer, acknowledged blocking oplock cutpoint, earlier-copy proof, controlled job termination/empty job, exact-stage cleanup and ordinary repair. |
| Prior synthetic case reopen | Pass: exact pages/order/rotations, sources, manifest/timeline and authenticated export history. |
| R3-created case under R4 | Pass: two complete open/verify/close cycles, preserved hashes and empty temporary directory. |
| Installed synthetic workflow | Pass: PDF/image import, rotation/history, two previews, one OCR page, verified PDF export and reopen, failure/no-overwrite paths, external originals unchanged and zero remaining temp files. |
| Runtime module origins | Pass at host scope: 15 inventoried product-process records, eight runtime-Python instances, ten separate OS console-support records; all nine Microsoft paths/five objects match the sealed inventory and authenticated source. |
| Held qpdf origins | Pass: its own creation-bound record contains exactly the three qpdf-adjacent CRT files; native synthetic PDF check exits zero. |
| Isolated GUI keyboard and focus | Pass at the recorded scope: fresh direct CUA binding; Ctrl+O opened a synthetic two-page case at revision 4; keyboard Rotate saved 90→180 at revision 5; keyboard Undo saved 90 at revision 6; preview completion and visible focus observed. |
| Current packaged screen-reader output | **NOT OBSERVED**; no speech-viewer or reader-output pass. |
| GUI busy error/recovery observation | Pass: actual installer busy dialog exposed actionable text, dialog role, accessible OK and visible focus; Return dismissed it. Native exit 1, empty owned job and full installed inventory/registration unchanged. |
| Normal GUI close / owned-process cleanup | Pass: revision 6 reopened with two active pages and 90/0 rotations; previews were loading at normal Alt+F4. Safe-close status was observed, native exit 0 and empty owned job followed without timeout/explicit stop. |
| Final R4 preserve-uninstall and residue/preservation check | Pass: native success; application directory, product registration and both Programs shortcuts absent; all five synthetic profiles and twelve external originals/exports unchanged. GUI case revision 6 retained. Ten production case entries counted only; contents never inspected. |
| Normal public first launch/default-profile workflow | Not established by the isolated internal-composition harness. |
| Clean Windows / actual standard account / earlier ARM64 proposal | **Historical NOT RUN / NOT PROVED; ARM64 superseded/outside scope under PWS-D043, not passed.** |
| Fresh network-egress, restricted-token or peak-resource measurements | Not performed by the module-origin receipt; no new measurement claim. |
| Trusted public product signature | Absent; required for public release. |

The R4 install/interruption/recovery and prior-case receipt is
`output/pws0802/community-20261103/r4-interruption-prepared/result.json`, SHA-256
`135d4a7832d4b4ccb9c14235626e4ef81c71d1f7182d77c3da6fdf542b0a32c9`.
The controlled session is
`8562a964a6b9997c7f3d03f0772aaad8e316f1c38b9e5f8ecddae5c728988f54`.
At that receipt's installed-ready checkpoint, four prior synthetic profiles and
nine external sentinels were unchanged; real cases remained count-only at ten.
It holds installation for the coordinator and makes no final-uninstall claim.
Controlled interruption and explicit repair recovery do not establish automatic
rollback or power-loss recovery.

The separate R3-created-case compatibility receipt has SHA-256
`2ceebf1123363bb4cea1d29ecf2451f3c07dc8852610a91108c995b3df81fc1b`;
its two-cycle reopen result is
`d83ea426def114c44d4ae794d5be6e4919cfbbd2c2e1fcb7a4219745af28e4e1`.
Both remain under the original R3 synthetic qualification directory in a new
`r4-case-compatibility` child. No accepted older receipt was rewritten.

The actual R4 module-origin receipt is
`output/pws0802/qualification/host-r4-runtime-origins-20261103/module-origin-result.json`,
SHA-256 `d9f78043ebbbf4e163d344c8e86afdf419d1a3caa14e5a10b31847ecd1e15c80`;
its smoke receipt is
`07c41f75dfac8693bad9646b4f162244287f3b0d95aba039c403a96b371161e4`.
Strict observer SHA-256
`37f732b1312b7a896a0c29b296d9aad53de5b8ade1160bf10790decd40e057a8`
requires owned-job membership and exact inventoried image size/hash. Microsoft
module paths/hashes must be app-local and inventoried, and qpdf's own held
creation identity must contain its exact three adjacent files. The independent
review above reconciled all recorded images/modules with installed and source
bytes. Only the observed workload is covered; GUI origins and token/network
properties require their own evidence.

The direct computer-use GUI observations are sealed in
`output/pws0802/qualification/host-r4-gui-20261103/interactive-observations.json`,
SHA-256 `bdcdf0d847aadab20f9e77f173132bbf2f4ecbc98231ab51ef249347cc0c9395`.
They bind the exact installed GUI process, candidate and inventory. UI text,
focus and screenshots were inspected in live tool responses; raw screenshot
files were not independently saved. The controller's normal-end receipt is
`da43e4c1d889ea6de094b5219d402a2d8e6cf884b4b56c7175cb7202bbe589df`;
it records the named GUI image and its five canonical app-local CRT origins.
The separate busy-refusal receipt is
`output/pws0802/community-20261103/r4-busy-refusal/ended.json`, SHA-256
`e099af95f70fff3d320ce251b6a587bce4a87738a6e046a6742d3b9cc65c0f8c`.
Its full 3,195 installed-file/registration check includes the two generated
uninstaller files. It records the real case root as ten entries, count-only.

NVDA **2026.1.1.55980** received three controlled launch attempts with an isolated
configuration. The original UIAccess variant prevented subsequent target
activation. The vendor-signed no-UIAccess variant allowed ordinary target
keyboard input, but injected Insert+N and keypad-zero+N did not open its menu;
`Insert_L` and `KP_Insert` were unsupported computer-use keys. All reader
instances quit after their attempts. No speech-viewer capture or spoken-output
observation was obtained. This is a qualification-tool limitation, not a screen-
reader pass or a demonstrated application reader failure. Accepted M7 reader
evidence remains unchanged and may support unaffected semantics; it does not
replace a current installer/packaged-reader observation.

## Current verification and preserved failures

- Canonical affected `tools/dev.py test-unit`: **179 passed, one existing host
  synthetic-symlink skip**, 16.67 seconds. This is the selected installer/launcher/
  discovery/preparer/qualifier/signing lane, not a new full-suite result.
- Canonical `format-check`, `lint` and `typecheck`: pass across 922 Python files
  at the frozen source epoch. The separate native launcher lane passed all seven
  tests; its supporting unit/security selections passed 15/10.
- Current canonical affected `test-security`: **26 passed**, 1,253 deselected,
  5.90 seconds. Current selected `test-integration`: **14 passed**, 720 deselected,
  43.11 seconds: one signing CLI, six interruption-barrier and seven native
  launcher tests.
- Canonical `validate-fixtures`: pass, 412.02 seconds; canonical `scan-secrets`:
  pass, 391.82 seconds. Both used the unchanged **2,122-file** snapshot with map
  SHA-256 `ef36531ae6505c916dda30268d6d825e0c319acc3195661ce0d76ae418313673`.
  `closure-scans-result.json` has SHA-256
  `df3abd4c2836223b461805608393f1e6976301e8c893c841fefa71fb643c8faf`.
  These cover that frozen source/privacy and all-ref/index/working secret epoch.
  This report and later coordinator metadata changes are subsequent deltas and
  require their own final documentation/metadata closure.
- The actual R4 data audit and independent workflow/module-origin review passed
  at the exact hashes above. Independent legal/terms, preparer, launcher,
  discovery and builder reviews found no concrete remaining defect in their
  reviewed surfaces.

The initial layout-wrapper cleanup-name error, incorrect review-hash refusal,
static-tool/source-resolution attempts, before-fix discovery/launcher regressions,
observer casing/path/console-host and scope corrections, original GUI binding
failures and NVDA control failures remain retained. A final metadata-only YAML note punctuation error was corrected; its failed validator logs remain retained, and candidate/source bytes are unchanged. A later pass does not erase
their source epochs or turn an unobserved check into a pass. No fixture/golden
refresh, runtime-rights waiver, shared repository dependency, host-security
disablement or public unsigned publication was used.

## Task result — PWS-0802: Windows installer and signed release artifacts

**Status:** blocked. The preserved R4 handover below remains valid at its stated
scope; the newest candidate is unsigned R5, documented in the continuation below.
Microsoft entitlement, acquired-copy provenance, nine-path app-local delivery
and scoped recipient terms are resolved for this candidate. Public signing and
the required clean/standard-account/architecture evidence remain unresolved;
the current packaged reader-output observation remains incomplete.

### Implemented

- Authenticated Community acquisition, exact unmodified Microsoft overlay,
  component-only local agreement and independent artifact/runtime review.
- Source-pinned per-user installer, controlled interruption/repair and preserved
  synthetic-case compatibility using the packaged runtime.
- A byte-identical CPython GUI filename with preserved upstream rights, a fixed
  native launch target and fail-closed installed discovery.

### Changed files

The continuation's substantive surfaces are listed here; earlier PWS-0802
implementation and unrelated accepted work remain recorded in their own reports.

| Files | Reason |
|---|---|
| `tools/prepare_windows_microsoft_runtime.py`; dedicated helper/unit/security tests | Explicit rights/acquisition/review gates, pinned source members, exact overlay and read-only builder verification. |
| `tools/build_windows_installer.py`; `packaging/windows/paperworkshell.iss`; `tests/unit/test_build_windows_installer.py` | New-candidate inventory/notice integration, offline scoped agreement, exact-hash quiet consent and authenticated GUI copy. |
| `tools/qualify_windows_installer.py`; dedicated qualifier unit/security/integration tests | Scoped acceptance, observed interruption, bounded owned-process cleanup and exact recovery/preservation. |
| `packaging/windows/launcher.c`; `tests/integration/test_windows_launcher_native.py` | Fixed product GUI filename and missing-alias/no-fallback regression. |
| `src/paperworkshell/app/installed_runtime.py`; `tests/unit/test_installed_runtime.py` | Fixed GUI property/resource admission with unchanged marker/dataclass/worker interfaces. |
| Three `COMMUNITY-2022-*.txt` assets; `packaging/release-reviews/microsoft-community2022-v1.json` | Exact component-only terms, full agreement/provenance and source/destination review; historical licence files remain unchanged. |
| Community/runtime review/result documents and this report | Authority, exact current/historical artifacts, retained failures, evidence scope and release blockers. |

Ignored acquisition, audit and qualification receipts are evidence outputs, not
new application dependencies. Task/BUNDLE/release-document metadata is owned by
the coordinator and is not changed by this report update.

### Verification

The canonical and exact-candidate results are listed above. Required remaining
observations are explicitly pending; no full task/gate pass is inferred.

### Security/privacy/accessibility notes

Originals, external exports and prior synthetic workspaces retain their scoped
preservation checks. Production case contents were not inspected. No account,
activation, telemetry, upload or development-tool dependency enters normal use.
Keyboard/focus evidence is scoped to actual observations; reader, clean-platform,
network and resource-measurement claims remain limited as stated above.

### Portfolio boundary and release-interface notes

PaperWorkShell remains independently versioned and complete with every sibling
absent. No sibling source, service, private data or adapter is introduced. Cases
and migrations remain locally owned. The release interface must retain explicit
unknowns until exact-candidate observations and public signing close their gates.

### Residual risks or deferrals

- Approved trusted public signing is absent; no provider enrollment, purchase,
  credential access or public publication was performed.
- At this R4 host epoch, a clean Windows environment and actual standard account
  were unproved; later R6 results retain their separate bindings. The earlier ARM64
  proposal is now superseded/outside scope and creates no prerequisite.
- Final R4 preserve-uninstall passed. Current packaged screen-reader output
  remains unobserved; unsuccessful tool interaction is not an application failure.

### Task graph

- The coordinator records PWS-0802 as blocked. Final BUNDLE/metadata closure
  remains coordinator-owned; this task must not be marked done with open gates.
- PWS-0804/PWS-0805 remain done with their accepted evidence intact.
- Next dependency-ready implementation task: none while PWS-0802 is blocked;
  PWS-0803 remains dependent on actual PWS-0802 completion.

## Final R4 preservation and remaining inputs

The final preserve-uninstall receipt is
`output/pws0802/qualification/host-r4-interruption-20261103/final-preserve-uninstall.json`,
SHA-256 `8cc9e659d7d8a1073758879ae35caf578a7dd2967e7dd8690285a33c1419de50`.
The qualification session is uninstalled with no pending action, SHA-256
`8a3202a5570282570b16f2d18bfb9a9986804bb2bb0597e58b18a6b739520fcf`.
It preserves all five synthetic profiles and twelve external original/export
files, including the new revision-6 GUI case, while removing only the installed
product, its registration and its two Programs shortcuts. All owned GUI,
installer and reader processes are closed. Actual filtered-admin host evidence
does not establish clean Windows or real standard-account operation.

The exact R4 installer, build receipt, inventory and R7 launcher/receipt hashes
were reverified after removal. No signing or repackaging changed these bytes.
The full source privacy and all-ref/index/working secret scans passed on the
frozen R4 source; final metadata-only closure and canonical validation receipts
are recorded separately under `output/pws0802/community-20261103/`.

Current continuation and remaining release inputs:

- **Clean Windows capability is available:** Sandbox is enabled. Codex created
  fresh offline guests and proved genuine interactive standard-account tokens
  after guest-only group removal and normal new logons. R4's complete offline
  smoke and preserve removal pass; its earlier timeout remains separate. The
  audited R6 candidate has passed first-launch layout, keyboard focus, plain
  recovery-message and natural-shutdown observations in a new clean guest.
  Its full document smoke, repair, preserve removal and reinstall/reopen pass;
  a separately bound completion retains the original helper failure. Agent-operated
  interruption and reader observations follow; no Sandbox owner action is pending.
- **Earlier ARM64 scenario — superseded/outside scope:** the earlier unrun
  x64-emulation proposal is superseded by PWS-D043. It has not passed and creates
  no release prerequisite, mandatory future task or owner environment request.
- **Trusted signing input:** no approved provider or credential route is
  connected. The maintainer has authorized one factual, nonbinding Foundation
  inquiry; [its current record](pws0802-foundation-inquiry-20261104.md) replaces
  the earlier authorization question. The [reviewed conditions](pws0802-signing-and-runtime-review.md)
  remain subject to provider assessment. Its
  whose certificate publisher would be SignPath Foundation while the product
  publisher stays Janne Vuorela, or select another approved trusted route.
  Foundation admission and approval of the bundled Microsoft/third-party files
  remain conditional. No enrollment, agreement acceptance, account creation,
  identity-document submission, purchase or publication has been performed.
- **Earlier reader interaction gap:** the failed menu/input attempts remain
  historical. Later actual keyboard/NVDA observations below establish the scoped
  workflows. The current [finite x64 scenarios](pws0802-x64-qualification-20261104.md)
  govern remaining work; universal reliability across all contexts is not a gate.

No further Microsoft licence acceptance or acquisition authorization is needed.
After public signing, verify the exact resulting artifacts and rerun all checks
affected by signed/repackaged bytes before public distribution.

## Sandbox continuation and newest R5 candidate

| Artifact | Exact value |
|---|---|
| Candidate | `pws0802-internal-20261103-r5` |
| Installer | `output/pws0802/community-20261103/candidate-r5/installer/PaperWorkShell-pws0802-internal-20261103-r5-win-x64-UNSIGNED.exe` |
| Installer bytes / SHA-256 | 512,711,831 / `e149ad8b2ae863d7f565c02e992d84b8533d98d35ffe723b4385b1e5e7d498da` |
| Installer receipt SHA-256 | `61e1a4055ed2a0b68e27eab9ff28530fa6f514367a157a1be3bffad484cb1192` |
| Inventory SHA-256 | `5b45f7351575a0954094363f6a35d4a0c475e71b2f06ed54016954369f566907` |
| Payload | 3,193 files / 650,085,841 expanded bytes / 957 components |
| Independent audit SHA-256 | `97a4101ba976edf2f331935f36d280d0a8f35cce30aa0e43919f6c441c55618e` |
| Signature / distribution | Unsigned internal engineering candidate; no public distribution |

Sandbox exposed a first-launch layout defect: the inactive organizer inflated
the chooser's minimum height and moved its heading and Create action outside
the initial viewport. R5 includes only the active workflow page in the stacked
layout while retaining widget ownership; its initial window fits the available
screen, and the empty case list directs focus to Create without stealing focus
on later refresh. Application, upgrade and worker identities remain unchanged.
The nine unmodified Microsoft runtime destinations, all native engines and all
third-party terms/replacement rights remain byte-identical to R4.

The regression failed before the fix; nine new regressions, all 355 canonical
accessibility tests, 228 affected unit tests and format/lint/strict typing across
923 Python surfaces pass. An initial signature-focus failure is retained; the
unchanged isolated test, module, repeated affected lane and preserved R4 control
pass. No signature code or assertion was weakened. Offline wheel/sdist, staging
and installer compilation completed successfully. The independent data audit
checks all payload bytes, 319 application sources, the 896-member current
installer-source archive, 957 components and 24 licence references. This audit
does not constitute target execution.

The guest runs Windows 11 Enterprise 26100.9711 AMD64 with networking and device
redirections disabled. Before installation, six development/runtime entry points
were absent from PATH and five relevant System32 VC runtime DLLs were absent.
No host profile, real documents, private cases or project developer environment
is mapped. Normal Sandbox initially supplies an administrator account; Codex
removed that disposable guest account from Administrators and obtained a fresh
normal interactive logon. The new token has no Administrators SID, including
deny-only membership, no linked token, and elevation type 1. This is an actual
standard-account observation, separate from the host's filtered-admin evidence.

R4's exact installed inventory and registration pass under that token, as does
its normal public launcher and responsive first GUI. The subsequent installed
document smoke hit the existing 360-second process ceiling. Its owned job was
confirmed empty, its environment destroyed and all native handles closed. No
document/OCR/export, uninstall or complete clean-runtime pass is inferred from
that attempt. Earlier valid host workflow and interruption evidence is retained
at its original scope.

The first staged diagnostic then failed before product imports: native token
helper initialization was out of order, followed by a computer-name comparison
between different APIs. These are diagnostic failures, not product failures.
Both receipts and the original sealed inputs are preserved. New diagnostic
epochs use the same [Windows NetBIOS identity API](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getcomputernamew)
and retain the VM, nonce, exact-source and genuine-logon bindings. Smoke and OCR
time limits and product isolation are unchanged. Complete R5 runtime and reader
qualification remain pending until actual successful observations are recorded.

The corrected V2 diagnostic subsequently reached product code under the proven
standard token. It verified all 3,193 installed payload files and recorded PDF
import failure `SOURCE_INSPECT_FAILED` after 3,037 ms, with normal backend cleanup
by 3,045 ms. This differs from the earlier 360-second timeout; it does not establish
that timeout's cause. The strict collector validates all 13 operation markers and
cleanup receipts: no owned processes or handles remain, and evidence transport
reports zero read/write errors. Its supervisor receipt is SHA-256
`24465a5efb301d0a048b39d055241a6f03ae9a5586661a71a71beb00f1d3f903`.
Read-only preparation for the next upgrade also found one worker recovery record.
It is retained; R4 will not be overwritten while its recovery state is unresolved.
The next bounded replay observes existing worker error codes and contexts before
they are sanitized by the public import boundary. No product fault or complete
workflow pass is inferred prematurely.

The first-PDF replay in the actual standard user's LocalAppData then confirmed
`WORKER_WINDOWS_ACCESS_GRANT_FAILED` at `access.recovery_allowlist`, before native
worker creation. The validated diagnostic has SHA-256
`51d0e52c9213443d1970f7f28118ff86bc2d38d047375d88ad6cbaab743c7556`.
This is a product recovery-authority mismatch. Its correction will keep each
worker's granted resources unchanged and validate recovery against an independently
derived, bounded set of installed runtime/engine resources. Exact journal-root,
SID, owner and object-identity checks remain mandatory; journal paths are never
adopted as authority. A separate trusted R4 recovery procedure is being prepared
against the unchanged installed recovery API. Source correction and any resulting
candidate require fresh verification; neither is a completed qualification claim.

The unchanged installed R4 recovery API then repaired the orphaned record under
the genuine standard token using four independently derived, validated runtime
roots. One journal record became zero; one lease was repaired and none were
active. Eight source modules, six qpdf resources and 44 Tesseract resources were
verified. No cases were opened, no journal paths were adopted as authority and
no journal was edited manually. The result is SHA-256
`48f167bf6ec5562271f4daa368be6cbe19313dc72ccc695e972e44f475c4dee2`;
the supervisor receipt is SHA-256
`5e14ad55c93eb1c4230080f9940e8ab1f0767dbddd54d94f068b3595d28d625e`.
The owned job was empty, its environment destroyed and all handles closed.
This validates that recovery operation; it does not qualify the pending source
fix or retroactively pass the original timed-out document workflow.

The subsequent unchanged R4 full workflow passed in the offline genuine
standard-account guest: PDF/image import, rotation, two previews, one OCR page,
verified export, reopen, invalid-source rejection, existing-output refusal,
unchanged external originals and zero case-temp residue. The full diagnostic
completed in 351.775 seconds within its unchanged 360-second ceiling. OCR reports
OCRmyPDF 17.10.0 and Tesseract 5.5.3. All seven PDF verification checks passed,
including rendering, page order/rotation and reopening the committed destination.
The exact result is SHA-256
`915bd79ccb7260d1fbebc1ffe84ebe5c8455c390efb1409d6626854176084315`;
standard-session receipt
`2fdeee881827ae68ee9c67f20123f2b7c51241b05aa7381f983c1960e8089c3a`;
supervisor receipt
`5db533550c62caf5251fd8717385e73dad327a729a5c1edc9f088aa75a67573b`.
Its controlled job, environment and handles were cleaned up successfully.

The 120-second stack capture showed pre-child runtime ACL verification during
thumbnail startup. This run observed no OCR error or shutdown hang; its final
shutdown took milliseconds. The original V6 timeout remains a valid failed
observation with unproved cause. The successful run was near the overall ceiling;
no deadline or resource limit was increased. The recovery-authority defect was
independently reproduced and remains necessary to fix before release. R6 source
implements that correction, with 186 canonical affected unit passes, one disclosed
symlink capability skip, three new native passes and 926-surface static checks.
R6 packaging and target lifecycle/reader qualification remain outstanding.

## Newest R6 build and affected source verification

| Artifact | Exact value |
|---|---|
| Candidate | `pws0802-internal-20261103-r6` |
| Installer | `output/pws0802/community-20261103/candidate-r6/installer/PaperWorkShell-pws0802-internal-20261103-r6-win-x64-UNSIGNED.exe` |
| Installer bytes / SHA-256 | 512,713,347 / `db51945dbe6c92175ea3de4a30e8a8e7b0836c648a10452745f8b66b7eae6774` |
| Receipt SHA-256 | `8d3272c523c6064badadbd9d07db4bebc751d47a37c35118538b6620ca1367bd` |
| Inventory SHA-256 | `e17c3337beaeea1be9a9d5a0cfae5e099d3fee29c15fb7cf50217979282fd82a` |
| Payload | 3,193 files / 650,091,333 expanded bytes |
| Signature / distribution | Root independently verified `NotSigned`; internal only, public distribution disallowed |

Offline wheel/sdist creation, staging and compilation pass. The initial staging
invocation supplied the compiler executable where the builder expects a
directory; it failed before output creation. The corrected directory invocation
passes without a source change, in 181.931 seconds for staging and 118.2 seconds
for compilation. The failed attempt and bounded input diagnosis remain retained.

The recovery fix changes seven application modules. An independently validated
installed-resource tuple supplies recovery authority separately from each worker's
actual access roots. Existing explicit trusted developer configurations retain
their own validated roots. The lease's granted paths, exact object inventory,
serialized journal roots, root membership, SID, owner and identity checks remain
unchanged. Unknown roots still fail before mutation. The new dataclass field
preserves the existing positional export-version argument; its regression failed
before the placement correction and passes afterward.

Current-source canonical affected unit verification passes 186 tests with one
disclosed synthetic-symlink capability skip. Formatting, lint and strict typing
pass across 926 Python surfaces. The three new native regressions pass. Broader
native verification records 156 passes, one directory-link capability skip and
one working-tree virtual-environment launch failure. That same test previously
failed in the original PWS-0801 working tree and passed in its owned source scope.
Matching current-R6 and preserved-R5 319-file owned-source controls both pass the
unchanged test, with authenticated parent and child source paths. The failed
working-tree command is retained separately; its precise local cause is not
asserted. Canonical affected qpdf security verification passes five tests with one
reparse privilege skip, and all 20 worker isolation/containment security tests pass.
All 355 canonical accessibility tests also pass against the current R6 source.
These automated widget results do not replace the pending packaged reader-output
observations.

R4/R5 installers and accepted evidence remain unchanged. R6's independent artifact
audit, exact installed lifecycle, interactive accessibility and affected automatic
recovery qualification must complete before these build results become target
qualification. Trusted signing remains a separate release input; the earlier ARM64
proposal is superseded/outside scope under PWS-D043.

The independent R6 artifact audit passes, SHA-256
`574c7eecf9253409f8fffcca10ddf80290c584a10007d011ea21df03d6ebc841`.
It verifies all 3,193 files, 957 components, 24 licence references, 178 inputs,
319 application modules, official offline schemas and regenerated SBOM/notices.
The source proof has 1,220 members, SHA-256
`314fc1a6b47c991dcadc361c1e1a8a55dda28635e937f67416aa7ea916788d02`.
Only the seven frozen recovery modules and candidate marker change at runtime;
the installer-source archive changes only its product CID. R5's window fix, the
launcher/GUI alias, all engines, nine Microsoft destinations, licence terms,
notices and replacement rights remain exact.

The first R6 target procedure incorrectly assumed in-place replacement of the
different R4 engineering candidate. The actual installer displayed
`PWS_INSTALL_CANDIDATE` and kept the prior candidate intact. ADR 0018 explicitly
supports same-candidate repair and reserves different-version authenticated
updates for PWS-0803. That guard will remain. The procedure failed at its install
wait limit; no R6 installation or GUI pass is claimed. Its failure/cleanup receipt
is SHA-256 `c356609680cf651fe1ce0db827f71a1b6065d710862086fd44aa4bc0929e6801`.
All owned processes, native handles and environment were cleaned up. The error
window disappeared after a keyboard attempt, but concurrent timeout cleanup
prevents crediting a verified keyboard dismissal.

R4 qualification will close with preserve removal and a bounded local backup of
its exact known synthetic case. R6 then requires a new stock Sandbox first-install
run, with a fresh normal standard-account logon proof. Existing-guest results will
not be relabelled as pristine first-install evidence, and removal of R4 will not
be represented as a supported upgrade path.


## Fresh R6 standard-account continuation

The prior R4 guest completed ordinary preserve removal with all four synthetic
cases and seven known case/external roots unchanged. The exact known case backup
has 13 members, 233,979 bytes and SHA-256
`cb3454203b44a31daba467aa5141817257e1f2ed550a1d890932c9b92076dff7`.
The strict joined result is SHA-256
`01417aad3cb2c67505fb0624f1123633b58ce24106b3fda2f485775e45797fb3`.
Both owned child processes exited zero, the archive process handle closed before
uninstall, and the WTS job, environment and native handles closed. The old VM was
then stopped and observed absent. This is qualification closure, not an upgrade.

The new offline 6 GiB Sandbox runs Windows 11 Enterprise 26100.9711 AMD64. Only
metadata and reviewed OS instrumentation were mapped before qualification. The
fresh logon proof is SHA-256
`96304c01ad1313de54c30df0a120b5d624f1d1728c8aab1bb027c1731e60b447`.
It proves the same stock guest account in a new logon, with no Administrators SID
including deny-only, no linked elevation token, elevation type 1 and a successful
fresh Local mutex create/release/close. Only guest administrator membership was
removed; no password, host account, namespace ACL or security policy changed.
The machine-name hash is corroboration, not a unique VM identity; the actual
new official VM UUID, configuration, nonce and salted logon facts are bound.

An initial headless pilot failed before its snapshot or account change because
Root omitted the initial connection. It was retained and disposed without product
execution. The new guest's initial connection also exposed a host controller pipe
lifetime defect: the GUI descendant retained the captured output pipe. Root
verified and stopped only its own host Python controller, retained the already
connected guest, and completed the fixed machine query separately. Future official
connect calls use no inherited output pipes and record the CLI parent result
separately from actual desktop and logon observations. These are test-control
failures, not product accessibility failures or qualification passes.

Before R6 execution, the SYSTEM baseline found all six development entrypoints
and all five canonical System32 Microsoft runtime destinations absent. A separate
actual standard-account probe found Tesseract, qpdf and OCRmyPDF entrypoints absent
before candidate mapping. No engine or project environment was installed outside
the candidate. The normal R6 public launcher displayed the empty case chooser,
all controls and visible focus. After focusing the guest application, Tab reached
Open selected case, Space produced the plain selection message, and Tab returned
to Create new case. Root closed the product through its normal window control;
the launcher exited zero, its job was empty and all handles closed without force.
The Alt-F4 attempt did not close it and is not credited as keyboard closure.
Reader output has not yet been observed. The complete offline R6 document smoke
passes, including PDF/image import, rotation, two previews, OCRmyPDF 17.10.0 /
Tesseract 5.5.3, all seven PDF export checks and case reopen. Failure checks return
`SOURCE_UNAVAILABLE` and `PDF_EXPORT_DESTINATION_EXISTS`; originals remain unchanged
and temporary files are zero. The inner smoke deliberately does not assert its
environment; the independently bound cold baseline and genuine standard token
supply that scope. Application-local runtime delivery is now verified for the
tested representative workflows without development tools or external engine setup.

Same-candidate repair preserves case/export hashes. Preserve removal leaves the
application and registration absent with seven protected files unchanged.
Reinstallation verifies all 3,193 payload files and preserved data again. The next
reopen helper returns native exit 2. Source review identifies a JSON string case ID
being supplied to a UUID-only backend API; a guarded replay must confirm the
observed failure's cause. The case-layout API accepts strings, which hid that
mismatch in the helper's earlier checks. The candidate remains unchanged while
the helper is corrected. The original overall failure remains SHA-256
`d99f2c5028189cba3dd6b1b6fdb81e60d3efc72556960c54ee77c4eb87a0c23d`;
its first five passing phases are preserved. Job, environment and native-handle
cleanup pass, and control/evidence transport reports zero errors. A separately
bound typed-UUID reopen and final preserve removal must complete and join the
original evidence before claiming the full installer lifecycle.

Canonical `validate-fixtures` and `scan-secrets` pass for all 2,126 frozen working
files, the index and Git refs. Their source and explicit ref/hash state remained
unchanged throughout. Receipt SHA-256
`0928b018eb5b1ef65ff352dec39693d70001dba9573cbd2a85735a01cce83288`.
Final report metadata changes require a separate exact-delta scan.


## R6 normal lifecycle completion

The separately sealed completion passes with the exact R6 installer unchanged.
The corrected opener records all 13 stages, including the typed UUID API call,
two reopened pages, normal close/shutdown, and unchanged manifest/export hashes.
Its Python process exits zero and closes its handle before ordinary preserve
uninstall. The product directory, registration and both shortcuts are absent.
The expanded before/reopen/after snapshots match across 18 case/work files,
8,181,350 bytes and all directories. Recovery entries remain zero. The genuine
standard-user job is empty, its environment is destroyed and all handles close.

Strict joined result SHA-256
`91dcc35ab341a83d04f7a84a75bf514db8a884e591d645215fc0e43db3b7a977`
validates five original and two completion phases. It explicitly preserves the
original failed status/native exit 2 and every original receipt hash; it does not
rewrite them or claim the original seven-phase collector passed. The original
collector's actual refusal is retained too. Normal clean install/launch, complete
offline representative workflows, same-candidate repair, preserve-default removal,
reinstall/reopen and final preserve removal are now qualified on this exact Windows
11 Enterprise 26100.9711 AMD64 guest. This does not qualify a different Windows
build, ARM64, interruption, reader output or public signature. No runtime or
application-source change was needed to correct the test-helper API mismatch.


## R6 live worker interruption and normal automatic recovery

The exact R6 candidate passed the observed worker interruption scenario in the
existing genuine standard-account Windows 11 guest. The strict result is
`output/pws0802/community-20261103/worker-interruption-r6-fresh-v2/actual-result.json`,
SHA-256 `a7d7c80a7c4e25a7d4c57450b37a562e310927136ad96308ad3c48f5a152ca44`.

The observer held and verified the parent, helper and restricted worker process
identities, creation times, native images, direct parent chain and tokens. Both
helper and worker were live at the gate. It terminated only the helper with code
73 and observed the worker exit after the precise termination-request boundary.
Job membership corroborated the sealed inherited containment; it was not
presented as a direct query of an unexposed private job handle.

A subsequent normal installed desktop factory, with unchanged production
recovery arguments and behavior, reported one orphaned lease, zero active leases
and one repaired lease. The worker reached Ready and shut down; the recovery
journal was empty and the synthetic case manifest was unchanged. Both retained
WTS jobs were empty, their environments destroyed and all handles closed. No
manual recovery API, journal-root adoption or ACL relaxation supplied the pass.

The earlier V1 attempt remains failed. Its observer refused helper identity at
stage 7 before any termination: the managed `WindowsIdentity.Groups` projection
omits logon and deny-only groups. Its test double had incorrectly supplied a
logon group. V2 parses bounded live native TOKEN_USER/TOKEN_GROUPS allocations,
checks every Administrators SID entry including disabled and deny-only entries,
and retains the other identity and termination predicates. Before V2, separate
operational cleanup used the normal desktop factory to recover the orphan left
by V1's bounded guardian cleanup; that receipt explicitly does not claim an
intended interruption pass. V1 sources and actual failed receipts are unchanged.

The new observer has 98 passing pure models, including 38 native-token scenarios
that retain the actual parser and fake only declared native APIs, plus source
compilation and independent review. Those models support the helper; the actual
V2 process, recovery and case observations supply the scenario evidence. This
worker scenario does not replace the remaining installer repair interruption,
screen-reader, platform-matrix or final signed-byte checks.


## R6 actual installer repair interruption and recovery

The current R6 candidate also passed the intended installer interruption
scenario in the same Windows 11 genuine standard session. Its strict result is
`output/pws0802/community-20261103/installer-interruption-r6-fresh-v1/actual-result.json`,
SHA-256 `eea85497a1ee2caa7e140b46ca0f08a197ba98db46fad2ea6b630410b506e3ee`.

The unchanged accepted RH barrier observed acknowledgement-required behavior,
verified an earlier file copy and held the exact live original installer image
and creation identity before terminating its job. The actual installer exited
73 and the job was empty. Two complete, known Inno staging files were retained
and authenticated before their handle-bound cleanup. Ordinary same-candidate
repair then exited zero, left an empty job and verified all 3,193 payload files,
registration and the owned uninstaller. Unknown or partial staging files would
refuse this procedure. Automatic rollback and power-loss recovery are not claimed.

All 20 case/work files (8,182,452 bytes), 25 directories and external
original/export hashes were identical before interruption, after interruption
and after repair. The worker recovery journal stayed empty. An independent
43-file CPython observer with 22 unchanged notice files supplied the test
without locking the installation: its 36 loaded native modules included none
from the installed runtime. All WTS jobs, environments and handles closed.
This instrumented phase does not replace the earlier clean-runtime proof.

The final owned uninstaller executable is 7,905,510 bytes, SHA-256
`31afaa17ce9c08e8494aba1f1e4c0d31a0f3786cca2f2472de583f5cc07f2b15`.
Its current data file is 3,343,582 bytes, SHA-256
`4223ae1cc59535581e76b43a32a779494bf84bb7a20df4d80ae5926833ec0159`.
These exact final bytes bind any subsequent removal. The installer candidate
itself, complete runtime, Microsoft DLLs, final terms and product identity have
not changed.

The first instrumented NVDA phase remains failed before interaction: precheck
returned 1, reader startup exited 255, and no ready receipt or observation was
published. The application job was stopped with code 73; all trees and handles
closed and a separate standard-account probe confirmed zero recovery entries.
This is not an application accessibility failure or a completed interaction test.
The helper's missing processor-architecture environment input is being checked
through a separate instrument-only paired startup diagnostic.


## R6 actual application reader observations and retained controller failure

The corrected reader helper reached live NVDA and the normal R6 desktop in the
same genuine standard account. The actual ready receipt is SHA-256
`90d3594c95668b28f440e2ee1cedbf643306dea1382c173249e698c730441caa`.
Root operated the RDP guest directly through computer use and observed NVDA
Speech Viewer output, including names, roles, instructions, shortcuts, progress
and save completion. The retained observations are
`reader-phase-controller-r6-fresh-v2/root-cua-observations-v1.json` under the local
Community evidence root.

Actual observations passed for case-list selection; logical Tab navigation and
visible button focus; opening the known two-page synthetic case with Space;
active-page descriptions; keyboard rotation from 90 to 180 degrees; save at
revision 5; keyboard undo back to 90 degrees and save at revision 6; both previews
ready; and keyboard Close case with confirmation that all queued changes were
saved. The normal window close then exited zero, without app fallback, with
app/job/metadata handles closed before the installer attempt. Window closure used
the visible X; a new keyboard-window-close result is not claimed.

The original helper omitted `PROCESSOR_ARCHITECTURE` from its restricted NVDA
environment. The exact upstream startup source accesses that key before its
protected core startup. A genuine failing regression was retained, and V2 adds
only `AMD64` after native architecture verification. The same authenticated
reader code then reached Ready and delivered the observed output. The original
255 receipt is unchanged; no uncaptured actual KeyError is invented. The optional
separate instrument-only diagnostic remains source-only and unrun.

The combined V2 controller is still **failed**, SHA-256
`be324dc8e19f04cd88c6a2648167905dfd9cc338cc2f904726d87b903c5d7999` for its
supervisor receipt. Its later rejected-option installer launch produced no
visible dialog during the bounded observation, timed out and was stopped. No
agreement wizard was launched. Reader cleanup then used fallback termination;
all jobs, handles and the parent environment closed. Those facts do not establish
an installer accessibility pass or failure. A narrow installer-only supplement
will add the missing observations without repeating the valid application work.

## WFP positive control and exact audit-policy restoration

The corrected WFP capability helper actually passed on the current guest. Its
strict capability-only result is
`wfp-capability-fresh-r6-v2/actual-result.json`, SHA-256
`7537970ad5f515a4a6a146fa4c4359fa4098dd4e96a02f2994ef70eaf2365fb3`.
One held genuine-standard-user control made one loopback connection and accepted
it, producing two matching permitted-connection records. The source used
EventData process/application identity, held native image and creation identity,
and bounded record/time windows. No truncation or logging-health issue occurred.
The original target audit flags were 0, enabled flags were 3 and exact restored
flags were 0. All jobs, environment blocks and handles closed. No raw EVTX or
endpoint addresses left the guest.

V1 had refused the valid query value 0 before any policy change. An independent
stock auditpol query confirmed No Auditing. Microsoft's
[AUDIT_POLICY_INFORMATION documentation](https://learn.microsoft.com/en-us/windows/win32/api/ntsecapi/ns-ntsecapi-audit_policy_information)
permits the zero query value; it was a helper restoration-admission limitation,
not proof of a missing Windows capability. V2 uses the stock command to disable
both target flags on restoration and requires exact native readback 0; it never
uses the unchanged-zero setter as a reset. The actual 0-to-3-to-0 round trip now
proves that route. The V1 refusal and sources remain unchanged.

This positive control supplies no product-attempt counts or universal claim
about uploads, telemetry or every socket API. The bounded product observation is
a separate dependency-ready phase. The earlier clean offline workflow and
worker-network-denial evidence retain their independent scope.


## Retained installer-only reader supplement V1

The separate installer-only supplement reached a real visible rejected-option
error dialog in the genuine standard session. Its retained window samples show
the default desktop and WinSta0, an on-screen Win32 dialog and no reported hung
window. The final sample was taken about 110 seconds into that phase. This adds
visibility evidence that the earlier combined reader controller did not capture;
it does not establish successful keyboard dismissal or screen-reader output for
that dialog.

The bounded operator/UI window expired before a normal installer exit was
observed. The error phase therefore records `native_exit=null`,
`expected_cancel_or_refusal=false` and fallback cleanup. The agreement and
removal phases were not reached. Reader cleanup also used fallback rather than
the ordinary quit path. The actor and wrapper remain failed; the wrapper observed
actor exit 2 after about 214 seconds. Their jobs and handles closed. The final
preservation snapshot is absent, so this failed attempt does not supply a new
before/after data-preservation pass. No application workflow was rerun.

The actor result is
`installer-reader-supplement-r6-v1/packet-v1/evidence/result.json`, SHA-256
`09a137604186ad211cc7a311e330b4acb088e8e1c71a8f293e6482e7855a85a9`.
Its wrapper result is SHA-256
`ce0ac2cb40faa2c44cd3b30454e2b9083f8abd1460703c5fe2c07c5171cae029`.
These receipts and the visible-window samples remain unchanged. A separate
source review also found that the host advance helper expected fields absent
from the actual eight-field phase result. The new supplement epoch corrects that
control contract; it does not retroactively complete V1. The accepted application
NVDA observations, core lifecycle and installer/worker interruption results above
retain their separate scope.

## Retained first product-WFP attempt

The first bounded product observer did not admit or observe a product process.
The host coordinator waited 35 seconds for startup readiness, while Root observed
the ready receipt only about 51 seconds after dispatch. The coordinator stopped
before invoking the standard-user public-launch helper or publishing START. The
observer subsequently exhausted its own bounded wait for that admission.

Its result is `unavailable`, not a product network-observation pass:
`wfp-product-observation-fresh-r6-v1/packet-v1/evidence/product-observation.json`,
SHA-256 `691831fa28467a558416e64c5bdee4ee7aa9ef4a2f33aa8cafa09d9808bb557f`.
The ready receipt is SHA-256
`5210cf02696ca5c00031e7ec955db7fa4a8665802a2e86889f2adf8097e0b614`.
No START file, launcher-ready receipt or launcher result was produced. The
observer reports no admitted root, zero roster processes and `counts=null`.
Those values provide no evidence of zero product network attempts.

The target audit flags were read as 0, enabled as 3 and restored to exactly 0
with successful native readback. Audit-process and roster handles closed. The
receipt records about 90 seconds of observer execution and no exported raw EVTX,
native paths or endpoint addresses. This retained failure concerns the host
startup-coordination window; it does not replace or invalidate the separately
passed WFP positive control, offline runtime workflow or worker-denial evidence.
A later product-window result requires its own bound admission, observation and
cleanup receipts.

## R6 installer-reader supplement V2: completed mechanics and remaining speech gap

The separate V2 supplement completed under the genuine standard account. Error,
licence cancellation and removal cancellation ended naturally with native exits
1, 2 and 1 respectively; none required a fallback stop. Full installed inventory
and the preservation snapshot remained unchanged: 22 files, 28 directories and
8,660,160 bytes, digest
`cc5fa8030dd6a087ebad905f8c8a44d64fed9e7c28e33aaa81271cad44f04437`.
NVDA quit with exit 0; all owned jobs were empty and handles closed. The mechanical
result is `installer-reader-supplement-r6-v2/packet-v1/evidence/result.json`,
SHA-256 `d6c0ce91cd2a98542ab263ce56f9cd59a27cd7061c47900075919d4338792ca7`;
the wrapper is SHA-256
`2a844bd5f3fb63a87d5b0b8f52052a6b6577526b4877857a0255cca1f8184797`.
This supplement does not rerun or replace the accepted application workflows.

Root's direct keyboard and Speech Viewer observations are retained separately in
`installer-reader-supplement-r6-v2/root-cua-observations-v1.json`, SHA-256
`40da24593528933ec244c14f1680232e380ad984ad5fe03e86650c485b01946e`.
The licence terms, read-only text, selected radio state, Cancel and exit-confirmation
controls were announced and operated by keyboard; no agreement was accepted.
Return dismissed the visible unsupported-option error, but its full message was
not observed in NVDA. Removal retained the default preserve choice and cancelled
by keyboard, while NVDA announced only the Qt window title; removal-control speech
remains unresolved. A focused main-event-loop diagnostic is pending. These facts
do not establish a removal-reader pass or a product fix.

## R6 product-WFP V2: validated sampling window, failed composite lifecycle

The host-only window validator produced
`wfp-product-observation-fresh-r6-v2/actual-window-only-validation.json`, SHA-256
`950840666e77a7799cadebe38596e6b6ccfd31a08232c9c1551423a960e343bf`.
It authenticates the actual observation receipt
`809fa184776e60424eb1f39ff466b3797c42a6e2b64a566648a6ff991cecc665`,
its admitted root, START, readiness and creation identities against the frozen
source, packet and positive control. The configured post-admission interval was
60 seconds; the recorded sampling range was 69,375 milliseconds. Three held
roles were admitted: launcher, GUI and worker. The selected event counts were
zero. qpdf and Tesseract were not admitted in this window. Audit flags returned
to exactly 0 with verified restoration and all observer handles closed. These
bounded observations establish no whole-application or universal absence claim.

Root missed the required natural-close interval. The launcher result,
SHA-256 `56413dfc089b7cbda947cae14d8ec91347badd905a855b0abbad63d940979e05`,
therefore remains failed with forced cleanup, an empty job and closed handles.
The strict composite collector still refuses a pass; the separate window report
explicitly records `FAILED_NOT_REWRITTEN`. No repeat or rewritten pass is inferred.
Root's subsequent read-only journal probe reported empty (exit 200). The original
receipts, failed composite and earlier V1 startup failure remain unchanged.

## Current R7 build, source checks and independent artifact audit

R7 changes only the removal review's standalone entry loop and the engineering
candidate ID. The dialog now connects `finished` to `application.quit`, shows the
dialog, enters `QApplication.exec()` and retains the accepted/cancelled exit
mapping 0/1. The existing lifecycle guard, service, deletion choices and storage
code remain unchanged. Qt's [main GUI event loop initializes the accessibility
root](https://github.com/qt/qtbase/blob/6.11/src/gui/kernel/qguiapplication.cpp#L1842).
The new fresh-process regression exercises Accepted and Rejected with real Qt,
substituted store/guard ports and zero storage operations. Its genuine two old-entry
failures remain, and both cases pass after the change.

The earlier process-local diagnostic was initially recorded as causal. That
exclusive interpretation is not established: session and observation order,
`-c` versus `-m` invocation and parent route differed. The functional entry-loop
correction and Qt root initialization do not prove that this alone explains all
reader variability. Actual shipping observations below retain that distinction.

Only three existing shipping/source files changed: the removal entry module,
`tests/unit/test_uninstall_review_dialog.py` and the product candidate ID.
Membership remains 2,126 files. Focused canonical unit, security and accessibility
selections pass 23, 12 and 4 tests respectively; canonical formatting, lint and
type checking pass across 926 source files. Source freeze
`removal-main-regression-r7-v1/shipping-source-freeze-v1.json` is SHA-256
`7e2c2c1e53d38a6d434a1043d0bb228b7f8f8af574054b330446b91a0b6983e8`.
The build-source map is SHA-256
`edc6a9b2adcc4708d3113623ec447d10e76aed843b9064d9d3298196837e94f9`.

| R7 artifact | Exact value |
|---|---|
| Candidate | `pws0802-internal-20261103-r7` |
| Installer bytes | 512,713,704 |
| Installer SHA-256 | `a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a` |
| Installer receipt SHA-256 | `0add5a47b863111cc8c0187aa17d6b84e006fcf01ddfd8840d76bebb4ff0afc6` |
| Inventory SHA-256 | `60caaf221b9c55f1ebc5a0d5c285579bd332972e973ef6cc0187effe098204b8` |
| Payload | 3,193 files; 650,091,464 expanded bytes |
| Signature/distribution | Actual Root inspection: `NotSigned`; public distribution disallowed |

The offline application build, stage and compile completed sequentially with
new R7 outputs and unchanged authenticated base, launcher, compiler and Community
overlay. The independent data-only audit is
`audit-r7/result-v3/audit-result.json`, SHA-256
`ab4e1d33a0fb983380fbd2fd09873db08bb679b92c3057b48861caa14a92cffd`.
Its 1,223-entry source proof is SHA-256
`3aaf3f502f2cd38ca781bb593504e9963ecee95d37ec350db9ba910ca537b805`.
It verifies all payload bytes/owners, official offline SBOM validation and exact
regeneration, 319 application members (307 Python modules and 12 JSON resources),
and 896 installer-source members. Runtime differences from R6 are exactly the
removal entry module and candidate marker; the wheel additionally changes RECORD,
the sdist changes only that module, and the installer-source archive changes only
the product candidate ID. Ordinary test proof is separate from archive membership.

All third-party components, obligations, replacement rights, launcher/GUI alias,
desktop, workers and document engines remain byte-identical to R6. The same
MSVC 14.44.35211 runtime remains five distinct DLL objects at nine paths, under
recipient terms SHA-256
`a5cdf1136b18611fc5235c657b7ad113a9ac338abe6c1af3c299205d9492c718`.
MIT and other open-source rights remain unchanged.

Both earlier audit failures remain. V1 misapplied the archive ASCII-name policy
to the approved Unicode repository fixture; the initial dot-file diagnosis was
withdrawn. V2 completed six artifact phases then failed an unsupported assertion
of 317 Python files. The pinned source and actual archives establish 307 Python
files plus 12 JSON resources. The path-policy and one-count corrections affected
only the ignored audit helper. No candidate failure or relaxed archive allowance
is inferred from these retained harness failures.

Full canonical fixture/privacy and working/index/all-ref secret scans pass:
`r7-canonical-scans-v1/result.json`, SHA-256
`017f524afeb472bfd3f067b21a07b2b2f35e2efc107bd7367585739a84c68a6f`.
They bind all 2,126 frozen source files to the build map above and unchanged
Git state SHA-256
`6c8fc0c1275fa7273ae3677dffb2ac2fb2abac2d4bfb6e978eb717e105226398`.
Final metadata edits require their separate privacy/secret delta and preservation
closure; this source-scan result does not predeclare that later work complete.

## Actual R7 standard-user mechanics and shipping reader observations

The strict mechanical result is
`r7-review-qualification-v1/actual-mechanics-result.json`, SHA-256
`4984fe7c8998df7b06d24b04bbc680805769bc350f3a325e2d6979f581f5c962`.
Raw actor and wrapper results are respectively SHA-256
`ab79bb88bfe61cc1b28fd49e8a34d5669943ee1daea71328734c569e374a02fb` and
`28b96559a4e82641686268aed48dc4fcdf827cf637b82b2d7645d2e010c7b291`.
Actual genuine-standard-session exits were install 0, same-candidate repair 0,
normal public application 0, unsupported-option error 1, review Cancel 1 and
default-preserve removal 0. All ended naturally, with no fallback stop; NVDA
quit/native exit was 0, all jobs/handles closed, and product directory,
registration and shortcuts were absent afterward. Recovery journal entries were 0.

The bounded before/after case/work/external snapshot remained exact: 31 files,
39 directories and 32,384,350 bytes, SHA-256
`ae319ad3cfbb6a08745d06372fc79ad59d05d5d1148b511ef28768378224820a`.
No deletion choice was selected. The public launcher displayed the normal chooser
and closed through its ordinary window control. These tests used the existing
instrumented standard-user guest; the report explicitly claims no pristine R7
guest or repeated R7 document smoke. R6 clean document processing and recovery
proofs remain applicable only within the audited unchanged runtime/worker/export
boundary, with their original R6 receipts retained.

Root's direct observations are separately recorded in
`r7-review-qualification-v1/root-cua-observations-v1.json`, SHA-256
`91a7ead89e669efc0deca6453461a7630902cfa8becaa66da2e05684b76de4bc`:

- In the first shipping Cancel review, controls were visibly focusable and Cancel
  worked by keyboard, but NVDA announced only the title. This gap remains evidence.
- In the second, Keep review, NVDA announced the dialog purpose/default-preserve
  and separate-confirmation description, grouping, unchecked deletion-review
  checkbox, read-only inventory/status, Cancel and Keep controls. Keyboard cycling
  exposed the controls and the Keep button's Enter shortcut. The user-facing
  default preserve path continued without selecting deletion; the Win32 question,
  Yes control and full confirmation were read, followed by successful removal and
  the completion OK button. The focused actions closed the windows naturally.
- The unsupported-option error's full text was actually announced after activating
  its message text, and Return dismissed it. Pure keyboard-only message reading
  was not established and is not claimed.

Mechanical receipts and reader observations remain separate. The subsequent fresh
reader records below retain both an initial-context gap and a later positive
first-review observation. They establish no exclusive event-loop or query-restoration
cause and no universal initial-focus reliability. R7 remains unchanged; no R8
product revision or owner-run accessibility test is inferred. Public signing and
the [finite x64 observations](pws0802-x64-qualification-20261104.md) remain required.
The intermittent reader-context observations are retained for those normal-use
checks; universal reliability is not a separate qualification demand. ARM64 is
superseded/outside scope under PWS-D043.

## Closed fresh-reader baseline and separate focus-query observation

The fresh-reader baseline subsequently completed without a preceding product GUI
or error phase. Its strict mechanical result,
`r7-review-reader-state-v1/actual-mechanics-result.json`, SHA-256
`efcd8bf9e4fc1ce28cee2d6037459a3c63dcbfc2a257e582baee8439868b7d95`,
records ordinary reinstall 0, shipping Cancel 1, final quiet preserve removal 0,
NVDA exit 0, all jobs/handles closed and product/registration/shortcuts absent.
The same 31-file, 39-directory, 32,384,350-byte snapshot remained unchanged.
The separate Root observations, SHA-256
`e26d03de2a20fafed28ebdb62d89a198e14a2a0402cb3575a6c981fe434b7141`,
retain visible checkbox/status/Cancel focus and natural keyboard cancellation,
but NVDA announced only the title and read stale Program Manager/desktop context.
The source-only wording proposal
`final-r7-metadata-v1/fresh-reader-report-proposal.md`, SHA-256
`8a913834c958d889216eb69ef417f31cc00eb7dc0cd57ec90b472dca4871ef8d`,
was prepared for that result; it is not an additional observation or pass.
The earlier second Keep-review announcements remain valid within their own scope
and do not erase this fresh-baseline gap.

A separate same-R7 focus diagnostic then completed. Its strict result is
`r7-review-focus-probe-v1/actual-diagnostic-result.json`, SHA-256
`6ed7abe7a6c838d44f11efb60db5f069c1ceaca402b433789b6df7934755b636`.
Final Root observations are
`r7-review-focus-probe-v1/root-cua-observations-v2.json`, SHA-256
`b7a1c8f100dabb019769e55f31e0ad3818312af72a5b60558fcaa04eaebfd25b`.
With a fresh reader and no preceding product GUI or error, the first ordinary
shipping review produced the following actual speech **before any query**:

- Caption focus and Tab announced the full dialog purpose, default preserve and
  separate-confirmation description, grouping and unchecked deletion-review choice.
- Tab announced the read-only inventory/status name, role and text, then Cancel.
- Further Tab/Shift+Tab navigation announced Keep cases, its button role and Enter
  shortcut. No removal/deletion action was activated at that point.

No Alt+Tab, viewer focus cycle, forced accessibility setter, synthetic input
helper or explicit UIA probe preceded those announcements. This is a positive
first-review observation in this run; the earlier negative baseline remains.

Only afterward did Root authorize the bounded read-only query, whose receipt is
SHA-256 `ae9e9e1d10c8031908aeefcda2fb5637357a203b5d2848011ea05224693a8d6a`.
Native foreground and focus remained on the owned target before and after the
query. UIA exposed the Qt review-dialog root and a target Qt focused group
(control type 50026; known-name category OTHER). Querying may activate a provider,
but speech already observed beforehand cannot be credited to query restoration.
No cause of the earlier stale context is proved by this metadata.

Post-query Tab actually announced the focused Keep button/name/Enter, then the
unchecked choice, status and Cancel. Space cancelled naturally with exit 1.
Official NVDA quit and final quiet preserve removal both returned 0. There was
no fallback stop; all jobs/handles closed, product/registration/shortcuts were
absent, and all 31 files/39 directories/32,384,350 bytes remained unchanged.
No product or forced accessibility-state change occurred. This remains an
existing instrumented guest, not a new clean-OS or document-smoke observation.

The nine-file helper source seal is SHA-256
`0a636d57f4e2995102b34106453245a9b6acd6de996b788c0928fc36c7b57a21`;
the Root host driver is SHA-256
`70737c52769949cd42825ad055c7010d873766d064170a16b4260949b956711b`.
Fourteen helper models and 31 pure driver checks pass. The first Root observation
file, SHA-256 `48eda5045caa144622454b8b3f7057c7647488123744f2e00df7b575e125660d`,
is retained: it mislabeled the raw pre-query READY hash as a control-receipt hash.
V2 distinguishes READY from the actual control receipt; the strict collector had
already joined both correctly. Only that metadata label changed, with no new UI,
mechanical or chronological outcome and no candidate failure.

The observed cancellation and preserve workflows are established within these
recorded runs. Initial reader-context behavior remains intermittent with cause
unproved; neither the event-loop correction nor the query establishes exclusive
causality or blanket assistive-technology qualification. No R8 change is claimed.

## Historical signing-inquiry draft and superseding authorization

The GitHub metadata observation
`final-r7-metadata-v1/signpath-repository-observation-v1.json`, SHA-256
`d5d08617cc6e4e02e3c6cd8691a60eb996c26d4fd31f5583190f94776095dc36`,
records the repository as public, non-archived and defaulting to main. It does not
verify publication of this local working tree or build, public CI signing
eligibility, or provider admission.

The unsent eligibility-inquiry draft, SHA-256
`b6a7d108b1d95230cc03cf477f6af06d5735a3c197201cd983933b8e19091e85`,
asks whether a first signed Windows installer can be considered before a first
public binary release, and whether the five unmodified Microsoft runtime objects
at nine paths can be assessed under the provider's System Libraries exception.
Those are questions, not assumed eligibility or an exemption already granted.
At that epoch, the INQUIRY-ONLY authorization question was unanswered and nothing
had been sent. The maintainer's 2026-11-04 direction now explicitly authorizes one
factual inquiry and its four questions. The [current inquiry record](pws0802-foundation-inquiry-20261104.md)
contains the exact replacement text, official contact route and actual status;
no new enrollment, agreement, identity submission, installer upload, paid service
or public publication is authorized. PWS-0802 still requires trusted signing and
the applicable finite x64 observations. No owner-run technical test or hash
transcription is assigned by this report.

After verified product/reader cleanup and rechecking the accepted synthetic backup
and proof pins, Root stopped the owned disposable Sandbox through the official
CLI (stop 0; subsequent environment list empty). The closure receipt is
`final-sandbox-close-r7-v1/result.json`, SHA-256
`c60de7376b9ef0923fdcf795f31f173012daa41abfa5c84fbf0122c639b3f0b6`.
Host cases and external originals were not touched. This resource closure grants
no additional release or qualification pass.
