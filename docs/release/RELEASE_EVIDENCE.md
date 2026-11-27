# PaperWorkShell release evidence index

| Field | Value |
|---|---|
| Document status | **FROZEN — exact packaged M8 candidate** |
| Product | PaperWorkShell |
| Release version | `0.0.0` in the embedded marker |
| Release channel | `engineering` |
| Evidence revision | `pws0808-r3-evidence-1` |
| Source revision/tag | `54e0b916ac069ce00a0ce200c43e07f3a76d1784` |
| Release interface revision | `pws0808-r3-interface-1` |

The current tables bind `pws0807-tab-focus-engineering-r3` to its exact package, evidence and
standalone envelope. The M8 gate report records product-completion disposition, including the
system-volume exhaustion check. External publication needs separate authorization. Conclusions
apply to their recorded artifact and environment. [M8 gate report](../gates/m8-product-completion-gate.md)

Installed scenarios ran on disconnected Windows 11 Pro x64 build 26300.9711 under a genuine
standard user, with Smart App Control Off. Staged component and resource checks used the exact
Windows host runtime on Pro x64 build 26300.9974. The Windows matrix preserves earlier candidate
and host observations. [Windows scope](WINDOWS11_X64_SUPPORT.md) · [R3 matrix](../development/pws0807-rc-matrix.md)

## 1. Frozen artifact inventory

| Artifact | Bytes | SHA-256 or verified result |
|---|---:|---|
| `PaperWorkShell-pws0807-tab-focus-engineering-r3-win-x64-UNSIGNED.exe` | 513,023,918 | `7791cef6be0062319a9c9f39a4bf130ded8f3914485eb50ab4e07d182c992889` |
| Windows Authenticode | — | `NotSigned`, no signer or timestamper certificate. The x64 PE certificate table is empty. |
| Qualified build handoff | 2,904 | `71819c9b00b33be65ecdde772020850cde32517a6fb08084c244feab82640755` |
| Installer receipt | 1,679 | `057407a387e55bacff420deec7e1cc0689c3dffa09317b280f2b5f8f391b9b74` |
| Resolved payload inventory | 9,638,270 | `a436ca25db576c7cc60936428381ee4e747030b869a77d6c5342c7b15df202a4` |
| Installed runtime marker | — | `5e564b0fef1b5e0ebe30ab19bdfe1d8c65872420889ac35c82d7237c73ab5f1e` |
| CycloneDX 1.6 SBOM | 12,704,822 | `5f925fd49afc7c1e5cbc5b95868c6b8f24503d150f1d916af4420e1435371a42` |
| SPDX 2.3 SBOM | 5,366,726 | `f73be051f5ffb59bfdaa0445c0b7cc8f3f736b0276c8bb8736bad8f21f506379` |
| Dependency lock `uv.lock` at exact source | 171,088 | `6b378cfc5ecf4d71d0191fc8e23d3ce01652ad5c06195d23bc86a196aafa7a3a` |
| External component notices, `THIRD_PARTY_NOTICES.txt` | — | `b09a78d7889d96bb12aaa977cee5ce004360c1e39a765e5539c4d816fc31c566` |
| Installed `notices/COMPONENTS.txt` | — | `51a5727a149a3f990519b8b41e80e0610d54967c75f24c496e078b8265f334d1` |
| Qualification index at `20c62ed104e8a230b56854e9a0d5238bce1fde10` | 49,527 | `4cfe72f050a2abd654a2b35efa843e0125c29c8b314c3713cee89d0e8a3794bc` |
| Original engineering interface `pws0807-r3-interface-1` | 36,332 | `db9ad671e63c93bd51daa3324f7b302ca3a80da51a761e5861a897e82dc8a6e8` |
| [Candidate release interface](RELEASE_INTERFACE.md) | 36,903 | `37e1ff17fe7d4bf9bae1e1c06095e78f5138318ccc21cc2842b58e2c6c01d69b` |
| [Candidate release notes and limitations](RELEASE_NOTES.md) | 4,550 | `cbc9851dd10761b528c7f419d47d7588d6cae67f43a6f1ddd6835a0e3fe1d7e9` |

The receipt distinguishes build-time qualification status from later installed observations.
M8 admission matched all 3,219 staged payload files, totaling 650,884,602 bytes, and all 345
application files to the exact Git source and build artifacts. The stage audit reproduced both
SBOMs and notices. The two SBOMs describe 957 components and all 3,219 staged files. The
M8 gate report records exact input, inventory and notice details. The verified prepared setup bundle
supports offline installation. A portable application package is unsupported. [Artifact admission](../gates/m8-product-completion-gate.md#artifact-admission)

## 2. Application identity and versioning evidence

The application ID is `io.github.pikkujanne.paperworkshell` and the installer upgrade identity
is `{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}`. Publisher Janne Vuorela, project-owned MIT material
and retained third-party terms are recorded with the package. PaperWorkShell versions independently.
R3 retains its embedded engineering identity. [Installer identity](../adr/0018-windows-installer-and-release-identity.md) · [Licensing](../../LICENSING.md)

## 3. Install, launch, update, repair, and uninstall evidence

| Observed scenario | R3 result | Retained receipt SHA-256 |
|---|---|---|
| Clean installation and launch | Prepared setup verified all 3,219 payload files and launched the standard-user desktop offline. | `a65c6d6c9aef37aa14b347e7e2f80c0bcd5b90e2340b9d7997f50c45073a511b` |
| Same-build repair | Restored a missing Microsoft recipient-terms file, verified the payload and reopened the preserved case. | `84933e63026890f88aa85e71b019d76900a847a1c1baf91b87968504fd027144` |
| R1-to-R3 replacement | **Keep cases** preserved 367 existing case files. R3 verified the payload and reopened the earlier case. | `ec1419bef9ef2ba46480333443a727bde55b9fdcd0d81f7ae2c6d00195834b64` |
| User quota failure and recovery | Real NTFS per-user exhaustion caused pre-copy refusal and mid-copy error in separate runs. Cancellation rolled back the partial installation and the retained data matched. | `be9a01df4ab08bd471dec7ccbe011f8e067c6d640c90ae84fe806182c3d7c88a` |
| Preserve, reinstall, reopen and reviewed removal | Four synthetic cases and 31 managed files survived preserve removal. Reinstall reopened a preserved case. Explicit reviewed removal left no cases and kept 23 external files and all four checked shell defaults. | `74508fa7694869ea32a16ebb64b8b0833990720c7c94ba0b25223b904f92695a` |
| Offline updater | Unprovisioned R3 refused the archived offer. An isolated test anchor supported the archived authentic offer and refused replay, expiry, wrong channel and a changed signature. | `dc22003bfb02850a6822400ecf93e858d39f6d320e4be55476cfb952bc0166a1` |

The installer evidence above records per-user NTFS quota behavior. See the gate report for the
separate whole system-volume exhaustion result. Per-user install, repair and removal require the
standard account. Direct operating-system file opening, associations and portable workspaces are
unsupported. The staged launcher rejected file and workspace arguments with visible guidance to use
in-app import. Automatic rollback across local installer builds is unsupported. The earlier isolated
updater rehearsal exercised previous-runtime restoration on its recorded builds. [R3 lifecycle and setup](../development/pws0807-r3-qualification.md) · [Isolated update verification](../development/pws0803-authenticated-updates.md) · [M8 gate report](../gates/m8-product-completion-gate.md)

## 4. Data-location and preservation evidence

The installed program lives under `%LOCALAPPDATA%\Programs\PaperWorkShell`. Cases live under
`%LOCALAPPDATA%\PaperWorkShell\Cases` in the current user's profile. The case holds its originals,
manifest, journal, migration backups and rebuildable caches. User-selected export and support files
stay at their external destinations. Update slots and bundled engines live with the installation.
The recovery guide records retention, permissions, cache and temporary behavior by category.
The R3 observed preserve, migration and deletion results are in the lifecycle receipts above.
[Data locations and retention](../user/CARE_AND_RECOVERY.md#where-data-stays) · [R3 replacement and migration](../development/pws0807-r3-qualification.md#r1-replacement-and-case-migration)

## 5. Public input/output and black-box workflow evidence

On both R3 configurations, PDF, JPEG and PNG originals and managed copies matched the recorded
digests. Ordinary and edited three-page exports matched expected ordering, rotation and pixels.
The four-category checklist blocked export when incomplete and enabled all three output modes
after confirmation. Each run produced matching combined, separate and ZIP exports and saved
summaries. The clean and replacement comparison receipts are identified by
`64684c952b2fb096a911bebf2aac9f0b634d2432f88fef6f8ef2af0272079b32` and
`f7c69c376c7d14a43bbc539793db31461197a47ada26dba21a5c1d43f4470722`.
A truncated synthetic PDF was refused and preserved the case. [R3 workflows](../development/pws0807-r3-qualification.md)

## 6. Synthetic release sample package

| Evidence | Verified identity |
|---|---|
| `PaperWorkShell-R3-synthetic-sample.zip` | 4,173,788 bytes, 30 members, SHA-256 `027410d15b8a62874d535fca5fdc625e46e272667a07b3c8096920f217b4f468` |
| Embedded and standalone manifest | 13,446 bytes, SHA-256 `a51a4ccf34b47793e4495b00e9300479264ad0f2f33780caf4722aea887eee90` |
| Fresh reopen receipt | `b7bab31c50693a6e287029148faceac27cedc2ab7ba2a19a85c1e1e562dba637` |
| Engineering interface and sample proof | `37570a29527423cfb4e1db49bcf04f36801120cd3c5ee030405d1ebedefbf940` |

The manifest records inputs, expected PDF/ZIP outputs and summaries, diagnostics, behavior notes,
licence notices, and their digests. Portable workspace examples are inapplicable because the product
has no portable case format. The immutable sample names `pws0807-r3-interface-1` as its producing
interface reference and retains R2 output provenance. M8 admission checked all member hashes,
reopened 24 valid PDFs and three nested ZIPs, and refused the truncated PDF and ZIP controls. Eleven
retained workflow outputs per installed run matched the frozen reference bytes. Each R3 diagnostics
ZIP matched its own preview and the frozen allowlist. [Companion compression note](RELEASE_NOTES.md#synthetic-sample) · [Sample admission](../gates/m8-product-completion-gate.md#engineering-interface-and-synthetic-sample)

## 7. Support-bundle privacy evidence

On both installed R3 configurations, the user previewed `summary.txt` and `allowlist.txt` and
consented before saving locally. The reopened archives matched each preview and the closed allowlist.
On replacement, cancel, occupied destination and over-limit leaf refusal preserved data. A fresh
destination succeeded with the same reviewed bytes. The clean and replacement archive SHA-256 values
are `3f2defae8fd552b75986004b7c097db008b30f5e642743b9ba9e1605bff95b47` and
`9b8597cbac384e08eb3ff8b02847054dae19c9b67850d5ee7079d72e490df7db`.
Packaged privacy checks exercised prohibited synthetic tokens and the documented failure contract.
The two-member synthetic example and exclusion proof are retained locally. ThirdLife support
artifacts remain separate. [Support implementation and allowlist](../architecture/support-bundle.md) · [R3 diagnostics](../development/pws0807-r3-qualification.md#clean-mixed-files-and-diagnostics)

## 8. Offline and network evidence

Offline setup, launch, case work and exports passed on the disconnected R3 guest. The exact staged
Windows worker and its descendant were denied access to tested IPv4 and IPv6 TCP and UDP loopback
listeners, which were reachable to controls. The clean guest had zero named ThirdLife products,
services or processes. There is no product account or mandatory server. The live update channel is
disabled and no in-app acquisition is offered for local OCR language packs. [R3 worker](../development/pws0807-r3-qualification.md#native-resource-limits-and-offline-worker) · [Standalone observation](../development/pws0807-r3-qualification.md#permanent-removal-on-the-clean-installation)

## 9. Low-resource evidence

The exact R3 installed-layout runtime exercised ResourcePolicy version 1 in normal and constrained
profiles. Peak process-tree working sets were 196,845,568 and 197,033,984 bytes. Maximum worker Job
commit was 43,216,896 and 43,257,856 bytes. The runtime receipt records capacity, CPU, cache,
temporary storage and output digests. Thirty-one storage and fault cases, four delayed-I/O
GUI controls, cancellation and fresh retry passed. Under the constrained profile, the 12-page
searchable retry produced 128,180 bytes with a 341,299,200-byte peak working set against the
1,342,177,280-byte limit. An earlier incomplete storage sample retains that result. There is no
physical-device certification. [Native resource evidence](../development/pws0807-r3-qualification.md#native-resource-limits-and-offline-worker) · [Constrained searchable export](../development/pws0807-r3-qualification.md#constrained-searchable-export)

## 10. Accessibility and compatibility evidence

Installed keyboard traversal produced the four-category combined package, including actionable
findings and summary. Normal 96 and 192 DPI and Aquatic 192 DPI showed selected-tab focus.
NVDA 2026.1.1.55980 generated 477 action-correlated utterance entries across ten workflow groups
in an agent-operated speech and native focus review. Packaged Qt checks covered the separate input,
assisted-use, consent, retention and privacy cases. [Keyboard and focus](../development/pws0807-r3-qualification.md#clean-keyboard-only-checklist-and-package-outputs) · [NVDA and timing](../development/pws0807-r3-qualification.md#installed-nvda-and-first-preview)

R3 reproduced the accepted overlay PDF, six M5 PDFs and their ZIP. Reader observations for Adobe
and the earlier named viewers retain their original file, version and observation dates. Fresh
headless checks displayed page 3 of the R3 color-compressed file in Edge 158.0.4370.45, Chrome
158.0.8127.38 and Firefox 161.0. R3 also reverified ten earlier actual permanent-removal saves and
resaves and checked the failure controls. These checks support the documented artifacts and scenarios.
[Reader compatibility](../development/pws0807-r3-qualification.md#reader-files-compression-and-update-trust) · [Original overlay observations](../compatibility/pws-0307-overlay-reader-matrix.md)

## 11. Security and privacy evidence

The source threat model at R3 has SHA-256
`03b6f7b121ad66560bc3568b48715d6983b467915f298c36a1b7efb56540fc98`. Exact source and
packaged-runtime receipts identify their security, schema, privacy, path and input refusal checks,
as well as separately scoped skips. The clean setup refused changed installer bytes, mismatched
inventory and an alternate-stream path before creating the installation target. Installed
diagnostics remained local and network was disconnected. Telemetry is off by default.
[Threat model](../security/threat-model.md) · [R3 security and component qualification](../development/pws0807-r3-qualification.md#source-checks-and-candidate-coverage)

The independent M6 gate approved the single-active-affected-page PDF profile. Exact R3 produced
the approved 34,084-byte unsigned derivative without changing the original. Independent reopening,
rendering and OCR verified the visible outside control and absence of the removed target.
Fourteen failed or unknown method cases blocked export. The retained independent R3 result has
SHA-256 `359f801211006ab1387a55761b6113846178cd3420ff781b064141210fdddb71`.
[M6 gate](../gates/m6-permanent-redaction-gate.md) · [Installed permanent removal](../development/pws0807-r3-qualification.md#permanent-removal-on-the-clean-installation)

## 12. Migration and recovery evidence

R3 opened disposable v1, v2 and v3 cases through the chooser and migrated each to v4 at exactly
one higher revision. Each prior manifest matched its backup. Original digests, order and rotation
were preserved. Source migration and storage failure cases retain their separate scope. A native
R3 worker termination during thumbnail generation returned a stable failure. Retry reopened the
same case, preserved digests and left no temporary files. The portable workspace format is
unsupported. [R3 migration](../development/pws0807-r3-qualification.md#r1-replacement-and-case-migration) · [Native worker interruption](../development/pws0807-r3-qualification.md#active-worker-interruption) · [Case migration contract](../architecture/case-migration.md)

## 13. Licence, provenance, and maintenance evidence

The resolved inventory, SBOMs and notices cover the verified staged bytes. R3 uses documented
build inputs and a candidate-bound receipt. The independently reproduced inventory-derived metadata
does not establish byte-for-byte repeatability of the outer installer. Keep the Microsoft recipient
terms and applicable third-party notices with the package. Use the private supplying-maintainer
channel for vulnerability reports and the documented emergency process for dependency incidents.
The exact local engineering handover and supported repair and replacement paths establish current
maintenance scope. There is no designated public support period or public channel.
[Licensing](../../LICENSING.md) · [Dependency policy](../development/dependency-policy.md) · [Security reporting](../../SECURITY.md) · [Emergency process](../development/emergency-security-updates.md)

## 14. Known limitations

The candidate notes and interface describe unsupported formats, forms and file opening,
permanent removal limits, case transfer, unsigned publisher status, rollback and accessibility
scope. Earlier outcomes, untested environments and incomplete observations keep their original
attribution. [Candidate release notes](RELEASE_NOTES.md#known-limitations) · [Release interface](RELEASE_INTERFACE.md)

## 15. Portfolio handoff status

The [standalone checklist](STANDALONE_RELEASE_CHECKLIST.md) reconciles the interface, observed
standalone journeys, data ownership and candidate artifacts. Future B4 compatibility review may
use only an exact frozen artifact through its documented public behavior after any necessary
authorization. No compatibility finding is recorded here. [Portfolio boundary](../../THIRDLIFE_ALIGNMENT.md)

## 16. Approval

| Review | Record |
|---|---|
| Artifact admission | Verified exact R3 identity and Authenticode `NotSigned`. |
| Installed workflow, security, accessibility and compatibility | Scoped R3 observations and independent reconciliations as indexed above. |
| PWS-0808 product-completion disposition | [M8 gate report](../gates/m8-product-completion-gate.md) |
| External publication and signed variants | Separate owner decisions. No public delivery authorized. |

## Historical engineering records

The records below retain their original artifact and evidence scope.

## Earlier R2 engineering evidence

The [R2 qualification ledger](../development/pws0807-r2-qualification.md)
records its installed Windows results, including the 200% Aquatic keyboard-focus failure. The
[successor ledger](../development/pws0807-r3-qualification.md) and [matrix](../development/pws0807-rc-matrix.md) track the affected check and the exact candidate environments.
The [R1 ledger](../development/pws0807-r1-qualification.md) preserves the
failed split and ZIP exports and the comparison leading to the R2 repair. The tested R2 build reports version 0.0.0 on the engineering channel.

| Engineering artifact | Exact identity |
| --- | --- |
| Windows 11 x64 installer | 513,023,220 bytes, SHA-256 `a73864739b739ba38a906898da281f41702ae3b160e39f1ad2c7eda9f6b9ba0e`, Authenticode `NotSigned` |
| Qualified build handoff | `0a0f2536d1516227b0bac6c10e572450890070fb7533f04297ac3fe55620cc0a`, source `8769ad85788edca9c8ee3c039b92cdaccd2ed084` |
| Frozen synthetic sample | `paperworkshell-pws0807-r2-synthetic-v1`, 30 members and 4,173,176 bytes, SHA-256 `441f9896015c26a2afb93f9930197c7e21222523e79cf2f1b0f312f292278e7d` |
| Sample manifest and engineering interface | Manifest SHA-256 `d5ee78ef5dcd51226d5070b8c8072aaae7071e49b4dca0840e7490cfa68f31cf`, interface `pws0807-r2-interface-1` |
| Actual NVDA, guest timing and resource evidence | NVDA `a8e3d1a2e89aba4c77877e5bfc2a5ce95b15b3289962967bc080413e7b662649`, timing `7e88ee6f968c515ace8a65655da421e49eef86c795c27b04061e092ad38565ae`, bounded runtime `44042f621b9c8f1fe6d8065225dad9a5eb7402d1fc91e0305f9de40c8758ca69` |

## Unsigned product completion

PWS-D044 makes the identified unsigned product candidate eligible for PWS-0808.
The current engineering artifacts retain their original internal distribution status.
Record the exact candidate's observed signing state and verify its size and SHA-256
against the retained build reference. The checksums bind selected bytes. The current
PaperWorkShell artifacts have no trusted publisher signature. External publication
requires its own authorization.

## Current Windows 11 x64 scope

[PWS-D043 / ADR 0019](../adr/0019-windows-11-x64-only-scope.md) selects
**Windows 11 x64 only**. Native ARM64 and x64 emulation on ARM64 are outside
project scope and are not prerequisites for installation, updates or public
release. No future mandatory ARM task is created. See the
[support boundary](WINDOWS11_X64_SUPPORT.md),
[PWS-0802 qualification record](../development/pws0802-x64-continuation-20261104.md)
and [finite x64 qualification matrix](../development/pws0802-x64-qualification-20261104.md).
The R3 installation evidence covers Windows 11 Pro x64 26300.9711. Any additional edition or build requires its own evidence.

These 2026-11-04 policy documents are newer than the immutable R7 build epoch.
Its outer, embedded metadata, inventory, source archives, audits and original
R6/R7 observations remain unchanged; historical ARM plans are not current support
or pending qualifications. The actual engineering guest was Windows 11 Enterprise
26100.9711 x64 with a genuine standard account. The original R7 records used
Windows 11 Pro 26300.9711 as a development host. The diagnostics R1 records
below cover a separate offline Pro guest.

### Diagnostics R1 installer

PWS-0802 exercised the engineering build `pws0807-diagnostics-engineering-r1`
from `e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec`. The candidate identifies
PaperWorkShell version `0.0.0`, channel `engineering`, application ID
`io.github.pikkujanne.paperworkshell`, and stable Inno upgrade identity
`{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}`. The project-owned software is
MIT licensed. Third-party components carry their own terms.

| Exact R1 item | SHA-256 |
|---|---|
| Unsigned x64 installer, 513,022,419 bytes | `a0d809322ea2c29066d26134c649f9ee1a883f4268403ea9c4e2d9e92bf8aaf8` |
| Installer receipt | `875894759adf79cd580c69a81018ca06c8a734e52f0fbd1754d2ec51bbd3220f` |
| Resolved inventory, 3,219 payload files | `8a9e3202d479c11e4e678adb0c25e31eab1cd1718f036187e500a7dc44566420` |
| Installed version marker | `c01268636ea37745e065516c82f892fc40fae418fdafaeae22cf814599bf6736` |
| Source revision's `uv.lock` | `6b378cfc5ecf4d71d0191fc8e23d3ce01652ad5c06195d23bc86a196aafa7a3a` |
| CycloneDX SBOM, `bom.cdx.json` | `226c9447c968b017ad9149eaa777ac91311c9bab136451348d1c302ebee3afdd` |
| SPDX SBOM, `bom.spdx.json` | `93465465a52eacb2d61646719ebb96e981ab5d21900a9d73aad064f9d2591fc0` |
| Build notice bundle, `THIRD_PARTY_NOTICES.txt` | `b7608b7de1c5bb4c503a70da6958419d3baef3991c1ef58d81c9aab1fbbe2475` |
| Installed `notices/COMPONENTS.txt` | `c1467be762fe1cf73bb78b153b7912c79f0c1421db8167c19162bcfd8dcf34c9` |

The artifact audit verified 650,881,153 expanded bytes, 178 build inputs, 345
application files against the source revision, 957 components and their
redistribution obligations, and 24 licence references. The verified SBOMs
match the CycloneDX 1.6 and SPDX 2.3 schemas. All 956 third-party component
records and 2,857 third-party payload files matched the audited R9 inputs.
The Microsoft runtime consists of five reviewed objects at nine destinations.
The receipt `output/pws0807/r1-installer-artifact-audit/result.json` has SHA-256
`17e31531b63d33ef0801e489d0babbe0e384b0be8c9f8af486e05728e2a87e6c`.

On the offline Windows 11 Pro x64 build 26300.9711 guest with Smart App Control
Off, clean setup verified each installed payload file and the application opened
from the user's Start menu. Authenticode reported `NotSigned` for the installer.
The install, registration, shortcut and both ordinary launch observations are
bound by `clean-installed-final.json`, SHA-256
`def5577c19b8c88339c982ccfa2e22161edee639ebb4e42362bb44eb69d54784`.
[PWS-0802 clean test record](../development/pws0807-unsigned-completion.md#pws-0802-clean-per-user-installation-and-launch).

## PWS-0802 unsigned internal installer

The [retained continuation](../development/pws0802-community-runtime-20261103.md)
records the accepted baseline `pws0802-internal-20261103-r7`, exact installer/inventory hashes,
the authenticated Community app-local runtime route and actual qualification.
Its unsigned installer is 512,713,704 bytes, SHA-256
`a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a`;
actual Authenticode status is **NotSigned**. Its installer receipt is
`0add5a47b863111cc8c0187aa17d6b84e006fcf01ddfd8840d76bebb4ff0afc6`;
the inventory is `60caaf221b9c55f1ebc5a0d5c285579bd332972e973ef6cc0187effe098204b8`.
The independent artifact audit and frozen-source privacy/secret scans pass. Its
standard-account install, repair, launch, cancellation and preserve-uninstall
mechanics pass in the existing instrumented guest. R6's original clean offline
document evidence is reused only within the audited unchanged boundary. A fresh
first-review observation announced the removal controls before diagnostic UIA
querying, followed by natural keyboard Cancel. Earlier intermittent reader
context gaps and their unproved cause remain recorded with their exact scope.
The [earlier report](../development/pws0802-implementation-20261103.md) preserves R1/R2
source epochs and all accepted and failed observations without changing their scope.
It is internal and unsigned, not a public release or completion of PWS-0802.
The [historical R7 continuation results](../development/pws0802-x64-qualification-20261104.md#historical-r7r8-continuation-observations)
add a new standard-account install, bounded installed-permission/default
comparison and intended SYSTEM refusal. They also retain two actual R7 failures:
the Notepad-dependent Notices shortcut and native-launch PDF inspection. Later R9
corrections and their scoped actual prefixes are recorded below; these
R7 failures are retained. Historical direct composition smoke does not by itself
cover the newly exposed native-environment seam.
The real low-disk scenario is unrun because this Sandbox volume lacks quota
support. None of these facts changes the settled Microsoft recipient agreement.

The separate experimental `pws0802-internal-20261104-r8` installer is now built:
512,732,582 bytes, SHA-256
`11a5fdd25beb6072c87ffb3fdf1e410f7b40a36af2fca601b33babed89743829`,
**NotSigned**. Its independent data audit passes
(`27f3a27dbc45170b9b2bada1b8aebf374d17f21cc36ac524c5bcfbfadba3e895`)
for all 3,195 payload files, rights and frozen source provenance. This does not
qualify runtime behavior. A source review identified the new Notices shortcut's
bypass of native crash-report suppression. R9 now includes that source correction;
R8 remains unchanged. No actual crash/upload result is inferred. The original R7
GUI import failure remains unexplained; successful direct-service and QThread
controls, including the default normal-policy control, do not establish its cause.
No COM or resource-budget fix is inferred. R7 remains the preserved accepted baseline.

### Newest experimental R9: built and partially qualified

`pws0802-internal-20261104-r9` is **built**, 512,732,779 bytes, SHA-256
`d29f7f6d081c0c43295ba8a3d5bb215344cefa6253a401be34ef0f74941bd9d4`.
Actual signature status is **NotSigned**. Its installer receipt is
`da8f1b8e69ccd5403758e1a83931121be485ff9fa6fe9cb17bd15f5958c7f426`;
inventory `fa337baa3ea03d251706a74ca20e61c1594d67822af4de41041026e03e6a0121`
binds all 3,195 payload files and 650,130,815 expanded bytes. Independent audit
`8e113985f492c35260f6f9f9320636b8571c58f1fea2cc296b04510a49409873`
passes all five data/rights/source phases for 957 components. Its source proof is
`80c90d1a249d6d5103585fdfb36ce7b5c6765fb1aa44034747678089473d3486`.
R7 and R8 artifacts remain exact; R9 is not promoted to the accepted baseline or
public release by its build or audit. These post-build summaries are newer than
the frozen R9 build source map; they do not change its archived README, wheel,
source archives or installer bytes. The audit remains bound to that build epoch.
Five original Microsoft DLL objects at nine
destinations, version 14.44.35211.0, and the settled scoped terms remain unchanged.

The new owned guest `445da5ba-31dd-4438-8b9d-96178904d1dd` is Windows 11
Enterprise 26100.9711 x64 with a genuine standard account. R9 installs all 3,195
files without a development environment. The following corrected host reports
under `output/pws0802/x64-scope-20261104/remaining-episodes-r9-host-correction-v1/`
bind the frozen guest source and actual completed prefixes:

| Scoped R9 result | Receipt and SHA-256 |
|---|---|
| Offline notices viewer and separate actual shortcut supplement; versions, MIT/terms, search and paging observed | `notices-collected-v1.json`, `8aff2298deedbcd351220df7785ab79dacb8cf76ce6d96f9a4c1b277140075fd` |
| Normal native public GUI PDF/JPEG/PNG input, quality OCR/edit and application-verified ordinary PDF export | `input-collected-v1.json`, `85f3fea709eb0bff92c59ed937c8cb614872d588f8b3388675802bfce99e3e0c` |
| Keyboard-operated interactive same-candidate repair after disclosed taskbar activation: local Microsoft terms, enabled/disabled navigation, progress and Finish; natural0/full inventory/preservation | `repair-collected-v1.json`, `3b6c10c2d9867e28bf5f43f3ea829a4c78f3b5596dfed8b221263aefa05795f8` |
| Exact lock-held synthetic deletion and natural Cancel1, all jobs closed/journal0 | `review-delete-ed6d-collected-v1.json`, `ae1040c87c90441b22068689f8b26bcb965aa68ce9051d53ace9208f3e5d415d` |

Root's CUA observations remain separate from these mechanical reports. Initial
notices/title-only and stale Explorer/Viewer context gaps, failed error timeouts
and the guest OSK observation remain preserved with unproved cause. They are not
retroactively made passes by successful cleanup.

**Finite A9 error reading now passes:** raw
`d8fc47f0e45287367138b247d1ad3612c46cc4e34ac21d6897ec6a98e2309222`
and Root factual join at `output/pws0802/x64-scope-20261104/r9-error-reader-focus-v1/actual-host-join-v1.json`
`8aaa2f06ffd865701512b5e2d021382b6f489faf7ecfa5a054ac46409414b697`
bind the full refusal message/OK reading and ordinary Enter/native1 after disclosed
Setup taskbar activation and its own native menu/Escape/Tab. Speech Viewer remained
untouched; the initial stale Explorer context is retained.

**Finite A11 review/result reading now passes:** V3 strict collection at `output/pws0802/x64-scope-20261104/r9-review-reader-focus-v3/actual-collected-v1.json`
`62ef00e5bca5633c7c7a73414b8f7d9d65dec879bb338e9791cf05b1bbb2fb5c`,
raw `73d0fb64ee2ac477164093886a81b33fa901835d3f2d3afd1df982fbe6f73ec9`
and Root joined observations at `output/pws0802/x64-scope-20261104/r9-review-reader-focus-v3/actual-host-join-v1.json`
`55868afb07f0cd67864e782ac0628f025b648fce27e710109bc45ae7786aab8c`
bind CUA013/014/015/016/019/020: one available/one unavailable spoken inspection,
read-only inventory, separate unchecked confirmation, explicitly checked Delete,
spoken zero deleted/two remaining/one failed-partial under the canonical manifest
lock, then actually focused named Cancel/Space and natural1. Reader0/preserve0,
all checked closures/no fallback, absent product/registration/shortcuts, journal0
and exact cases/work/six-externals equality pass. Initial caption/taskbar context
and disclosed review-icon pointer activation/Escape/Tab remain explicit: this is
not keyboard-only launch, an exclusive-cause finding or universal AT reliability.
Review V1 raw `268cb751...208dd` and V2 `0035a4f0...8b3f6d` remain failed with their
successful cleanup distinguished; V2 did not reach Inspect/delete. No product or
resource-budget change is inferred.

The deletion observation had initially unchecked separate confirmation, then an
explicit check after inspection and the exact lock-held barrier. The UI reported
one fully deleted, two recognized remaining and one failed/partial attempt from
two available/one unavailable inventory. Strict mechanics verify the imported and
future-schema cases and all six external files remained exact; raw result
`b75bbd4fc7210ee1dc67a54a36f2a3f1744677588c6cf7414282920681dffed9`
is retained. This proves the scoped partial-result/preservation path, not complete
data deletion. The later V3 finite reader observation above used the two retained
cases, so its separately observed result was zero deleted/two remaining/one failed.

**R9 final preserve removal: PASS**
`75188e18537c4b8ae326fc15526eafcc7e8b3c10444e9c4140ec698e090e9065`.
Actual corrected read-only removed record
`40868e40202cb432850f7577d8afe0ea2e58598d6b3ff43d00b444e5ac036e15`
and transport `bed1c2864950c58f1f74d86bbd286b70995ea7c291a463eb4a3969d189dea8b6`
pass: product, registration, both shortcuts and selected class keys absent;
four selected extension defaults and all six external files unchanged since
installed; both fixed service/task namespaces queried and absent; no observed
rename queue or global reboot indicator. No actual reboot or universal residue
claim is made. Managed cases/cache/records remain, case temp and worker recovery
are empty. No separate global settings/log/language/support-store claim is made.
The first PS5 helper failure remains preserved: its reader rejected the legal
empty registry-default key. The correction only parses the exact pinned installed
record using a bounded inbox .NET dictionary reader; five managed models and the
actual corrected probe pass without product or registry/ACL/policy mutation.
Independent final case/work byte preservation and member review are **COMPLETE**.
Root case review at `output/pws0802/x64-scope-20261104/r9-retirement-v2/root-case-review-v1.json`
`6a94ac1d264b59a0e6956af642b98ba2ed60ad0168a5f6fd17a6d5124603138e`
verifies case ZIP `4d0067d9f2ad5dbd8dda29da47c2492908bec36b07bdb6a3f4430e49c3e67beb`
(749,203 bytes, 24 members, ten files/744,147 bytes).
Root work review at `output/pws0802/x64-scope-20261104/r9-work-archive-v3/root-work-review-v1.json`
`ddc1cd36c7d235b208b903f9983ad5d4da0d42be2a244e68a4db04b7d54c1179`
verifies work ZIP `13736b5f43deebadaeac9bb5b37b72d0c662754ea6e6693f1bfa5c5a9d48f653`
(87,641 bytes, 22 members, eight files/83,095 bytes), every member and all six
external hashes. The failed composite backup remains failed; the valid case ZIP
was reused unchanged and the work-only correction preserved actual filename
VERIFIED-EXPORT.PDF. Strict pikepdf10.10.0 reopened that ordinary export as three
pages, without searchable-OCR or visual claims. Subsequent V3 snapshots remain
exact, so no backup rerun is inferred or required solely for the reader retry.
Actual packaged low-disk is
**UNRUN**; the Sandbox quota/partition capabilities were unavailable. Trusted
signing remains absent. The Foundation inquiry was submitted once; no provider
acceptance, enrollment or signed artifact is inferred. This R9-era observation applies to its archived package and environment.

The [PWS-0804 migration contract](../architecture/case-migration.md) and
[PWS-0805 diagnostic sample](../../tests/fixtures/diagnostics/v1/README.md) are
independent local product evidence; they create no ThirdLife interface or dependency.


A fresh read-only probe of that same R9 guest is now recorded separately:
`7339733454b669dc6b048aa510a6cbadafbd0fc4940c2a335584cdea18ae8a2b`,
joined by `f56db5bd66e85372ce5c36f5c522c6881ffaed29485dadf6e3ece478b5d30934`.
NTFS flags29,830,879 lack FILE_VOLUME_QUOTAS; COM was not attempted. The C:
partition query reports CimJobException-2146233087, with no usable partition or
supported-size query and all owned query jobs closed. No quota/partition/profile/
account/policy mutation or actual low-disk test occurred. This is current-guest
capability evidence, separate from the older retired-guest records. After verified
case/work backups and final natural preserve cleanup, the official CLI stopped
only the owned guest; stop0/empty-list receipt is
`9e4d3fafd62b86c20fdd30988046e50edeebd2aebd521cddd080af90c9bdf12d`.
No product-uninstall result is inferred from stopping the VM.

## Purpose

This document indexes frozen black-box release evidence for maintainers, deployers, support
workers, and a possible future ThirdLife B4 compatibility cut. It does not expose user data,
require ThirdLife source access, or create a shared runtime schema.

An entry is release evidence only for the exact product revision, packaged artifacts, dependency
set, and environment named in it. Source-tree tests and policy documents may be listed as
development context, but they do not make a packaged-candidate row pass. See the
[release interface](RELEASE_INTERFACE.md), [standalone checklist](STANDALONE_RELEASE_CHECKLIST.md),
and [portfolio boundary](../../THIRDLIFE_ALIGNMENT.md).

### Current setup and update engineering evidence

Both rehearsals used the disconnected Windows 11 Pro x64 26300.9711 guest with a standard-user
desktop. The following results are scoped to their specific packages and trust configuration.

| Candidate or pair | Installed result | Evidence |
|---|---|---|
| Unsigned `pws0813-trace-engineering-b8` | Same-build repair and reuse, standard-user launch, and one offline ordinary PDF export. Existing case files matched their prior hashes. | [PWS-0814 setup record and artifact identities](../development/pws0814-automated-setup.md) |
| Unsigned `pws0803-updates-engineering-r1` bootstrap and isolated test-signed `pws0803-updates-engineering-r2` runtime ZIP | Local authenticated offer and explicit consent, activation, offline ordinary PDF export, restoration of R1 and refusal to replay the accepted R2 offer. All 353 pre-existing synthetic case files matched their prior hashes. | [PWS-0803 update record and artifact identities](../development/pws0803-authenticated-updates.md) |

The diagnostics R1 row below records the local installer replacement and the clean
installation separately. The isolated update exercise used its own trust configuration.
The live production channel is disabled.

### TRACE B8 documentation walkthroughs

PWS-0806 exercised the installed TRACE B8 novice and support routes on the disconnected
standard-user Windows 11 Pro x64 26300.9711 guest. Ordinary PDF export refused an existing
destination, then saved a verified PDF with a new name. The support preview and consent worked.
Saving the support ZIP failed at two fresh fixed-local destinations, and neither attempt left a
ZIP. The closure record preserves both failures and the description-only reporting fallback.
[Documentation walkthrough evidence](../development/pws0806-documentation.md#installed-documentation-walkthroughs).

PWS-0807 isolated the native picker retaining its suggested ZIP filename in the installed working
directory. With a temporary in-memory change to start at the user's home folder with an empty
filename, the same accessibility edit selected the requested path. The installed B8 writer
committed the exact reviewed two-file ZIP. The same change is in source and passed the focused
regression.
[TRACE B8 diagnostics investigation](../development/pws0807-diagnostics-investigation.md).

The exact unsigned PWS-0807 R1 installer was built from
`e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec` and has SHA-256
`a0d809322ea2c29066d26134c649f9ee1a883f4268403ea9c4e2d9e92bf8aaf8`.
The offline standard-user desktop saved the exact preview to the selected
filename, refused an occupied ZIP, and left zero staging files. Verified
replacement preserved all 353 baseline case files. These assertions pass for
exact unsigned R1. The original run left separate installed cancellation and
storage-failure rows open. [Unsigned continuation](../development/pws0807-unsigned-completion.md)
records subsequent work. In that continuation, the PWS-0814 setup entry completed
R1 replacement after **Keep cases**. It verified all 3,219 installed payload files,
launched the ordinary application, and retained all 353 baseline case files.
The subsequent diagnostics run returned to review without a file on picker Cancel.
The native picker denied a restricted folder. The application independently refused
a Windows-valid filename above its 180 UTF-16 code unit limit. A retry saved
an exact copy of the reviewed 1,567-byte archive at a supported name. Independent
inspection verified the permitted members, CRCs and privacy canaries. The source
and occupied ZIP hashes matched the baseline, and the canceled and rejected
destinations and staging location stayed empty.
[Packaged R1 verification](../development/pws0807-diagnostics-investigation.md#packaged-verification-on-unsigned-r1).

The [PWS-0807 matrix](../development/pws0807-rc-matrix.md) records the environment inventory,
existing engineering results, synthetic rehearsal sample, and remaining exact-candidate
checks for unsigned product completion.

## Historical development evidence before packaged qualification

The table records the development evidence and open work at the earlier planning checkpoint.
The exact R3 results are indexed above.

| Area | Current development evidence | Release interpretation |
|---|---|---|
| Milestone gates | [M0 foundation gate](../gates/m0-foundation-gate.md) and [M1 vertical-slice gate](../gates/m1-vertical-slice-gate.md) passed | Earlier-gate evidence must be rerun or confirmed against the packaged candidate |
| Security | [Threat model](../security/threat-model.md), [data flows](../security/data-flows.md), and [test plan](../security/security-test-plan.md) | Packaged controls and the public reporting process remain M8 work |
| Worker isolation | PWS-0107 and the M1 gate record AppContainer, Job, egress-denial, cancellation, and recovery evidence | Package composition must preserve and retest the boundary |
| Original preservation and recovery | M1 gate plus implemented immutable ingest, atomic manifest persistence, and startup recovery | Migration/uninstall/preserved-case evidence remains M8 work |
| Resources | [Policy version 1 / constrained-v1](../performance/LOW_RESOURCE_POLICY.md) and completed M1/PWS-0208 constrained evidence | These are bounded software facts, not packaged or physical-hardware certification |
| Accessibility | [PWS-0111 human keyboard/NVDA record](../accessibility/pws-0111-nvda-smoke.md) and [PWS-0203 agent scan-review record](../accessibility/pws-0203-scan-review-smoke.md) | The complete packaged M7/M8 keyboard/NVDA matrix is still required |
| Synthetic corpus | [Public synthetic fixture index](../../tests/fixtures/synthetic/v1/index.json) and [fixture policy](../development/synthetic-fixture-policy.md) | M8 must freeze a representative release subset, expected outputs, behavior notes, and hashes |
| Dependencies | [Reviewed policy baseline](../../packaging/README.md), [dependency inventory](../../packaging/dependency-inventory-v1.json), [OCR language-pack catalog](../../packaging/tesseract-language-pack-catalog-v1.json), and [PWS-0801 redistribution audit](../development/pws0801-result.md) | PWS-0801 replaces the JBIG-dependent TIFF library and applies the exact approved lxml omission; its internal dependency stage has separate resolved evidence. Match the exact candidate inventory, notices and SBOM |
| Diagnostics | [PWS-0805 previewable support bundle](../architecture/support-bundle.md), [synthetic sample](../../tests/fixtures/diagnostics/v1/README.md), and [unsigned R1 installed evidence](../development/pws0807-diagnostics-investigation.md#packaged-verification-on-unsigned-r1) | Reuse matching exact-candidate evidence and verify remaining failure paths |

At adoption, the repository is a pre-alpha source tree with project metadata `0.0.0`; no installer,
signed package, stable application ID, frozen release sample package, or M8 release candidate
existed. The internal PWS-0801 dependency stage is recorded separately below.

### PWS-0801 development-input provenance (not a candidate freeze)

The 2026-11-02 audit binds the following repository inputs. The working tree contains accepted
development work beyond HEAD; HEAD alone is not its source identity. These hashes identify the
audited dependency inputs only, not a built installer, a clean release environment, or a complete
source snapshot. No lock upgrade or native artifact replacement occurred in this audit.

| Input | SHA-256 | Interpretation |
|---|---|---|
| `pyproject.toml` | `69f72c844ca5921f29578f256195ea967e8eb7dc0bc2766b9a65805ac8eaa366` | Current declarations; release version/channel remain unresolved |
| `uv.lock` | `6b378cfc5ecf4d71d0191fc8e23d3ce01652ad5c06195d23bc86a196aafa7a3a` | Existing exact Python resolution, unchanged; not a complete native inventory |
| `packaging/dependency-inventory-v1.json` | `712e2fcc33a55742196e6274ca543162ac4125c16f0cf59e9172adb3bea86698` | Reviewed policy baseline, not a resolved release SBOM |
| `packaging/tesseract-runtime-closure-v1.json` | `1ffb85724770b4c30aa4d982564d548dadf466ebd5861cd827d2836198119c0a` | Exact selected 30-package/39-file closure; redistribution blocked |

The [historical audit](../development/pws0801-audit-20261102.md) records verified archive/member/license
identities, alternatives, and required review. The
[emergency dependency policy](../development/emergency-security-updates.md) is a process document,
not evidence of an implemented updater. The R3 artifact inventory above records the separately qualified exact candidate.

### PWS-0801 resolved internal dependency candidate

Candidate **`pws0801-dependency-stage-20261103-r3`**, internal version `0.0.0`, targets
Windows x86-64 / CPython 3.14.6. The clean constructor consumed 175 authenticated inputs
and 32 recipe/review identities. The package has **957 components, 3,153 files and
622,288,120 expanded bytes**. Source, notices and build-only attribution are included;
external build executables are not application runtime files.

All outputs below are under `output/pws0801/resume-20261103/frozen-candidate-r3/`, except
the wheel and application/tooling source archives under sibling `app-artifacts/`.
These local generated artifacts remain ignored. The consumed input store is sibling
`resolved-inputs-r3/`; the complete file/component/source/obligation mapping is in the inventory.

| Exact artifact | Bytes | SHA-256 |
|---|---:|---|
| `bom.cdx.json` | 12,648,990 | `7235beac39e653dbfd4f68686dbe68b50ed368bd0253c8f5ad76748168e81330` |
| `bom.spdx.json` | 5,271,873 | `ab4ca99ce167af9a9c29e447d9113838de768c6ce1cdad8cfd9c373e38822be8` |
| `dependency-stage.zip` | 502,569,903 | `e14bd67b4b9e4f5bfb6f21eabbe3dcc7d2a03fe721fde33bbb11a4fb97e1e15d` |
| `input-verification.json` | 57,555 | `93d8dee09cf013a6a15423a81cd8b49b30787e203cf4c1375ab9aa5426063037` |
| `resolved-inventory.json` | 9,590,361 | `8fb9366e08dee502a69858d8070b97a98ba9d55af232bd87fcf2a50ccad5eb2b` |
| `SHA256SUMS` | 516 | `e116433f0b10c6298e2f0317846bfb353d0fd0e77052bc7d21e943ff41a401ae` |
| `THIRD_PARTY_NOTICES.txt` | 7,948,844 | `95ede0d5b7c9bb5776711029892917adf6eea8febd51246c4db2909dfb5d43fe` |
| `paperworkshell-0.0.0-py3-none-any.whl` | 1,764,902 | `3c4790053a6a9bf37435cd15ee9efd3ff3f278b8532080afa0487fc6357bcca8` |
| `paperworkshell-0.0.0.tar.gz` | 1,558,611 | `2d2f8c4144d4b6c4e33898ec99da91d3f9bd1706232c7ae5c9fd3dbeaca6ed54` |
| `paperworkshell-release-tooling-20261103-r4.tar.xz` | 827,124 | `fa4268eb08417ed43021cf7d170e748713439f6df944f44e2fd84146afb0b0c2` |

Application source identity is
`source-snapshot:2d2f8c4144d4b6c4e33898ec99da91d3f9bd1706232c7ae5c9fd3dbeaca6ed54`;
the separate tooling archive binds the exact constructor, bootstrap recipe, reviewed inputs
and raw notice assets. The Git base `2e70ec0e7174dab342f6df43d7b4d685f84a3259` alone is
not the complete dirty-source identity. The unchanged `uv.lock` SHA-256 is
`6b378cfc5ecf4d71d0191fc8e23d3ce01652ad5c06195d23bc86a196aafa7a3a`.

The isolated builder has 180 files from eight authenticated archives; its external anchor is
preparation SHA-256 `f9d7ceb3da8ab8035fcd662e5515f2abb8383aabd7b3ee0ff17af5228a43cc43`,
with manifest SHA-256 `28c8a13f0e1f7590bd713db235de2f3fb4aa624fbedb05c74ee56e5b690da628`.
Both identities are consumed review inputs and were verified before/after construction. The
constructor asserts that validator imports resolve from the isolated builder and developer
modules are unavailable. All 29 retained MSYS signatures and actual GPG/DLL identities passed.
Both generated SBOMs pass the vendored official schemas offline. The final R3 tooling snapshot
includes the reviewed exact-byte public-attribution policy; two independent snapshots are equal. All 13 exact-runtime native
adapter smoke steps pass; 2,414 runtime files are byte-identical to the earlier native-engine
integration stage. Broad QA, the final tooling delta and source-preservation closure are recorded separately in the
[current task result](../development/pws0801-result.md).

The [staging ADR](../adr/0017-release-dependency-staging.md) explains this internal candidate's
scope. PWS-0802 owns identity, product terms, installer and clean-machine prerequisite
installation. Subsequent packages require their own exact-candidate evidence. The
staged dependency files confer no installation or ThirdLife compatibility claim.

### Diagnostics R1 lifecycle evidence

| Scenario | Exact environment | Public command/UI path | Result | Evidence reference |
|---|---|---|---|---|
| Clean install | Diagnostics R1 on offline Windows 11 Pro x64 26300.9711, Smart App Control Off | Prepared per-user setup bundle | Native installer exit 0. All 3,219 payload files verified. | [R1 setup evidence](#diagnostics-r1-installer) |
| Standard-user launch | Same R1 clean installation | Prepared setup launcher and installed Start menu shortcut | Each opened a responsive desktop with the actual standard-user token and exited normally. | [R1 launch evidence](#diagnostics-r1-installer) |
| Component information | Diagnostics R1 on the offline Pro guest | Installed **PaperWorkShell notices** shortcut | Opened the local viewer showing the product version, candidate, component and licence selector, and read-only notice control. | `r1-installer-completion/notices-content.txt` |
| Visible import of supported synthetic files | Diagnostics R1 on offline Windows 11 Pro x64 26300.9711 | Standard user, **New case**, then **Add file** for PDF, JPEG and PNG | Each selection added one saved page. The three external and three managed files matched their manifest digests after export. | [R1 mixed-input workflow](../development/pws0807-unsigned-completion.md#installed-mixed-input-pdf-export) |
| Same-build repair | Diagnostics R1, Pro x64 26300.9711, a bundled recipient-terms file missing | Matching prepared bundle with `--repair` | Restored the missing file, exited 0, verified all 3,219 payload files and opened the installed desktop. | `r1-installer-completion/Repair.json`, SHA-256 `a7fa782531a30a46a6a8ed19ecedd8f7b2ec72c3929afccafd68284217d8c97c` |
| Local preserve-cases replacement | TRACE B8 to diagnostics R1, Pro x64 26300.9711 | Per-user removal with **Keep cases**, then prepared R1 installer | Verified 353 preserved case files after removal and installed R1. | [PWS-0807 R1](../development/pws0807-unsigned-completion.md#prepared-r1-replacement) |
| Uninstall while preserving cases | Diagnostics R1 on Pro x64 26300.9711, with a case created by the installed app | Per-user uninstaller, preserve cases | Installation root, registration and all three shortcuts removed. Five synthetic case files and three external files matched their original hashes. | `r1-installer-completion/state-native-removed.json` |
| Explicit app-data removal | Diagnostics R1, Pro x64 26300.9711, one available synthetic case | Inspect cases, separately confirm deletion, acknowledge the result, then confirm native removal | One case fully deleted, zero remaining, zero failed or partial. Native removal exited 0. No case files, app root, registration or shortcuts remained. All three external file hashes and four file defaults matched. | `r1-installer-completion/deletion-text.txt` and `state-after-explicit-removal.json` |
| Reinstall and reopen preserved case | Same guest and product-created case | Prepared setup, then **Open case** | Restored registration and shortcuts. The installed desktop reopened the saved one-page case. All five managed files and the external source, export and support ZIP matched. | `r1-installer-completion/state-native-reopened.json` |

A separate run preserved the bytes of a manually copied case. Its reopen
reported `RECOVERY_SCAN_FAILED`. The successful reopen above used a case
created by the installed R1 desktop.

### R1 installer disk exhaustion

An enforced NTFS quota limited the standard user's writes on the system
volume of a disposable offline Windows 11 Pro x64 build 26300.9711 guest.
With 65,536 bytes available to that user, native Setup reported Windows
error 112 during temporary-file creation and exited 1 before creating the
application directory. With 64 MiB available, installation reached the ordinary
per-user target and failed copying `Qt6Gui.dll`. Cancel rolled back the partial
installation. Five managed case files and three external files retained their
pretest hashes. The first refusal measures Setup initialization. The installer
wizard's total-payload-size check was outside that observation.

The interactive observer lost its UI Automation element in the copy-error dialog
and recorded an incomplete result. The captured error dialog, retained native log and
post-failure inventory establish the copy failure, rollback and absent
installation. The native exit code for this mid-copy attempt was unobserved.
Restoring the quota configuration on the same guest branch allowed the exact
prepared setup to install with native exit 0, verify all 3,219 files and open
the normal desktop. The desktop exited 0 and the five managed and three
external file hashes still matched.

The joined host receipt
`output/pws0807/r1-installer-exhaustion/qualified-r1-disk-exhaustion.json`
has SHA-256 `338652ea4b494c89a9ca599b6e5cda7ee5a7983425ba2b417efa64f69e2baa7e`.
It binds the following observations in `actual02` and the subsequent return to the
offline active R1 checkpoint, where all 358 case files matched the original inventory.

| Observation | Receipt SHA-256 |
|---|---|
| Initial temporary-file refusal, `pre01-result.json` | `bbd9c0414073d58965329fbc248b26d9970574b6af13db946c2137d6f0a9fed2` |
| Copy-error window, `exhaustion-window.txt` | `f6cebf20f6574e490bb36bae170c06a44db0fdb2d5b954836174212f98891ed6` |
| Retained native log identity, `retained-native-log-identity.json` | `ea3e94da8934714c9032b3041ba09911c3387b50642ed9381fe787f6f6d13972` |
| Restored installation, `recovery.json` | `d7358c0df1e7f8c0aa0f3196dbe811e79fccf7c0283eeab9cb83629d2de2b96d` |
| Data after recovery, `preservation-after-recovery.json` | `88afb6f58221c0245c54cde77132233aac169adfbb4c0bf641f7c1930af81a4d` |
