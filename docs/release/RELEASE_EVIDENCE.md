# PaperWorkShell release evidence index

| Field | Value |
|---|---|
| Document status | **DRAFT TEMPLATE â€” complete only for an exact packaged M8 candidate** |
| Product | PaperWorkShell |
| Release version | **TBD** |
| Release channel | **TBD** |
| Evidence revision | `0-draft` |
| Source revision/tag | **TBD** |
| Release interface revision | **TBD** |

## Current Windows 11 x64 scope

[PWS-D043 / ADR 0019](../adr/0019-windows-11-x64-only-scope.md) selects
**Windows 11 x64 only**. Native ARM64 and x64 emulation on ARM64 are outside
project scope and are not prerequisites for installation, updates or public
release. No future mandatory ARM task is created. See the
[support boundary](WINDOWS11_X64_SUPPORT.md),
[current PWS-0802 continuation](../development/pws0802-x64-continuation-20261104.md)
and [finite x64 qualification matrix](../development/pws0802-x64-qualification-20261104.md).
Exact editions/builds and remaining scenarios still require evidence.

These 2026-11-04 policy documents are newer than the immutable R7 build epoch.
Its outer, embedded metadata, inventory, source archives, audits and original
R6/R7 observations remain unchanged; historical ARM plans are not current support
or pending qualifications. The actual engineering guest was Windows 11 Enterprise
26100.9711 x64 with a genuine standard account. Windows 11 Pro 26300.9711 is
host-only evidence. R7's existing instrumented guest is not a new clean-R7 result.

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
pending rename or global reboot indicator. No actual reboot or universal residue
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
acceptance, enrollment or signed artifact is inferred. The public release fields
and final-byte evidence template below remain **TBD**.

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

## Current development evidence available for later reconciliation

The following evidence is real but is not a completed M8 release package:

| Area | Current development evidence | Release interpretation |
|---|---|---|
| Milestone gates | [M0 foundation gate](../gates/m0-foundation-gate.md) and [M1 vertical-slice gate](../gates/m1-vertical-slice-gate.md) passed | Earlier-gate evidence must be rerun or confirmed against the packaged candidate |
| Security | [Threat model](../security/threat-model.md), [data flows](../security/data-flows.md), and [test plan](../security/security-test-plan.md) | Packaged controls and the public reporting process remain M8 work |
| Worker isolation | PWS-0107 and the M1 gate record AppContainer, Job, egress-denial, cancellation, and recovery evidence | Package composition must preserve and retest the boundary |
| Original preservation and recovery | M1 gate plus implemented immutable ingest, atomic manifest persistence, and startup recovery | Migration/uninstall/preserved-case evidence remains M8 work |
| Resources | [Policy version 1 / constrained-v1](../performance/LOW_RESOURCE_POLICY.md) and completed M1/PWS-0208 constrained evidence | These are bounded software facts, not packaged or physical-hardware certification |
| Accessibility | [PWS-0111 human keyboard/NVDA record](../accessibility/pws-0111-nvda-smoke.md) and [PWS-0203 agent scan-review record](../accessibility/pws-0203-scan-review-smoke.md) | The complete packaged M7/M8 keyboard/NVDA matrix is still required |
| Synthetic corpus | [Public synthetic fixture index](../../tests/fixtures/synthetic/v1/index.json) and [fixture policy](../development/synthetic-fixture-policy.md) | M8 must freeze a representative release subset, expected outputs, behavior notes, and hashes |
| Dependencies | [Reviewed policy baseline](../../packaging/README.md), [dependency inventory](../../packaging/dependency-inventory-v1.json), [OCR language-pack catalog](../../packaging/tesseract-language-pack-catalog-v1.json), and [PWS-0801 redistribution audit](../development/pws0801-result.md) | PWS-0801 replaces the JBIG-dependent TIFF library and applies the exact approved lxml omission; its internal dependency stage has separate resolved evidence. Final public-package approval remains pending |
| Diagnostics | [Structured diagnostic baseline](../architecture/startup-recovery-and-diagnostics.md) | No user-authorized support bundle exists yet; PWS-0805 owns it |

At adoption, the repository is a pre-alpha source tree with project metadata `0.0.0`; no installer,
signed package, stable application ID, frozen release sample package, or M8 release candidate
existed. The internal PWS-0801 dependency stage is recorded separately below; public-release artifact and gate rows remain pending.

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
not evidence of an implemented updater. The final artifact inventory below remains pending.

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
scope. PWS-0802 owns final identity, product terms, installer, signing and clean-machine
prerequisite installation; subsequent packages must regenerate their own evidence. This is not
a supported portable installation, public-release approval, or a ThirdLife compatibility cut.
The later public-release rows below still require their own exact-candidate evidence.

## 1. Frozen artifact inventory

| Evidence | Location/reference | SHA-256 or verification | Status |
|---|---|---|---|
| Installer/package | **TBD** | **TBD** | Pending |
| Portable/offline package, if supported | **TBD** | **TBD** | Pending/not supported |
| Release notes | **TBD** | **TBD** | Pending |
| Known limitations | **TBD** | **TBD** | Pending |
| `RELEASE_INTERFACE.md` | [Draft template](RELEASE_INTERFACE.md) | **TBD for frozen revision** | Pending |
| Source revision/tag | **TBD** | Signed tag/hash **TBD** | Pending |
| Dependency lock | **TBD** | **TBD** | Pending |
| SBOM | **TBD** | **TBD** | Pending |
| Licence/notice bundle | **TBD** | **TBD** | Pending |

## 2. Application identity and versioning evidence

- Reverse-domain application ID: `io.github.pikkujanne.paperworkshell`, selected in ADR 0018
- Product version/channel: **TBD**
- Publisher and product licence: Janne Vuorela; scoped project-owned MIT, third-party terms preserved
- Application target: Windows 11 x64 only; supported release editions/builds follow the [support boundary](WINDOWS11_X64_SUPPORT.md)
- Installer identity and upgrade/package code: `{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}`, selected in ADR 0018
- Evidence that PaperWorkShell versions independently from ThirdLife: **TBD for exact release**
- Future B4 evidence that a newer unverified version is not automatically compatible: **TBD in
  ThirdLife, not a PaperWorkShell gate**

The selected identities and target do not fill the still-unproved public release
version, signed artifact, signer or final release-approval fields. The
[authorized eligibility inquiry](../development/pws0802-foundation-inquiry-20261104.md)
was submitted once through the official General message contact form; its
submission acknowledgement was observed. No provider enrollment, agreement,
Foundation acceptance or production signing has occurred in this continuation.

## 3. Install, launch, update, repair, and uninstall evidence

| Scenario | Exact environment | Public command/UI path | Result | Evidence reference |
|---|---|---|---|---|
| Clean install | **TBD** | **TBD** | Pending | **TBD** |
| Standard-user launch | **TBD** | **TBD** | Pending | **TBD** |
| Visible import of supported synthetic file | **TBD** | **TBD** | Pending | **TBD** |
| OS-level open of supported sample, if supported | **TBD** | Currently unsupported | Pending/not supported | **TBD** |
| Open portable workspace/project, if supported | **TBD** | Currently unsupported | Pending/not supported | **TBD** |
| Repair, if supported | **TBD** | **TBD** | Pending/not supported | **TBD** |
| Authenticated update | **TBD** | **TBD** | Pending | **TBD** |
| Interrupted/failed update recovery | **TBD** | **TBD** | Pending | **TBD** |
| Rollback, if supported | **TBD** | **TBD** | Pending/not supported | **TBD** |
| Uninstall while preserving cases | **TBD** | **TBD** | Pending | **TBD** |
| Explicit app-data removal | **TBD** | **TBD** | Pending | **TBD** |
| Reinstall and reopen preserved case | **TBD** | **TBD** | Pending | **TBD** |
| Remove optional associations, if supported | **TBD** | Currently unsupported | Pending/not supported | **TBD** |

Record prompts, privileges, restarts, and residue by category. Public evidence must use known-folder
notation rather than real usernames or private absolute paths.

## 4. Data-location and preservation evidence

- Configuration location and permissions: **TBD**
- Case/workspace location and permissions: implemented architectural convention; packaged result
  **TBD**
- Immutable-original location and hash verification: implemented case convention; candidate result
  **TBD**
- Cache/temp/log/support-bundle locations: case cache/temp known; remaining locations **TBD**
- Retention and cleanup policy: **TBD**
- Migration/rollback backup location and cleanup: **TBD**
- Original byte-for-byte preservation result: **TBD for candidate**
- Atomic save and crash-recovery result: **TBD for candidate**
- Uninstall data-preservation result: **TBD**
- Evidence that ThirdLife is absent and neither owns nor migrates PaperWorkShell data: **TBD for
  packaged black-box matrix**

## 5. Public input/output and black-box workflow evidence

| Workload | Input hashes | Expected output | Output hashes | Verification | Evidence |
|---|---|---|---|---|---|
| Canonical document-package workflow | **TBD** | PDF/ZIP/summary **TBD** | **TBD** | Pending | **TBD** |
| Mixed PDF/JPEG/PNG workflow | **TBD** | **TBD** | **TBD** | Pending | **TBD** |
| Interruption and recovery | **TBD** | Recovered case and verified export | **TBD** | Pending | **TBD** |
| Malformed/hostile input | **TBD** | Safe failure; no corrupt case | N/A | Pending | **TBD** |
| Low-space/slow-I/O workflow | **TBD** | Safe failure or verified completion | **TBD** | Pending | **TBD** |

Verification must include expected page count/order, parse/reopen, structural checks, rendering,
constraints, and unchanged source hashes.

## 6. Synthetic release sample package

| Artifact | Purpose | SHA-256 | Provenance | Status |
|---|---|---|---|---|
| Sample PDF input | Basic born-digital/scanned input | **TBD** | Synthetic/cleared **TBD** | Pending |
| Sample JPEG/PNG input | Image intake | **TBD** | Synthetic/cleared **TBD** | Pending |
| Sample portable workspace/project | Transfer fixture, only if product supports it | N/A | N/A | Not currently supported |
| Expected combined PDF | Output verification | **TBD** | Generated | Pending |
| Expected multi-file/ZIP output | Packaging verification | **TBD** | Generated | Pending |
| Expected package summary | Human-readable output | **TBD** | Generated | Pending |
| Sample sanitized support bundle | Support-boundary verification | **TBD** | Generated | Pending |
| Hash/expected-behavior manifest | Black-box fixture index | **TBD** | Project documentation | Pending |

No release sample may contain real identity, financial, medical, employment, signature,
beneficiary, or applicant data.

## 7. Support-bundle privacy evidence

- Support-bundle creation path: **TBD**
- User preview screenshot or accessible transcript: **TBD**
- Exact approved allow-list: **TBD**
- Prohibited-token test corpus: **TBD**
- Automated absence of document/OCR text, values, names, addresses, filenames, paths, metadata,
  thumbnails, signatures, credentials, and document bytes: **TBD**
- Cancellation/permission/disk-full behavior: **TBD**
- Sample sanitized bundle hash: **TBD**
- Evidence that PaperWorkShell and ThirdLife bundles remain separate: **TBD**

## 8. Offline and network evidence

- Core workflow with network unavailable: **TBD for candidate**
- Worker egress-denial result: **TBD for candidate**
- Optional update/language-pack disclosure: **TBD**
- Interrupted optional-download recovery: **TBD**
- Offline installation or verified media/cache procedure: **TBD**
- Packet-capture/privacy review: **TBD**
- No mandatory ThirdLife process, sibling product, account, or project server: **TBD for black-box
  candidate**

## 9. Low-resource evidence

- Resource policy/profile version: **TBD for candidate**
- Workload IDs and input hashes: **TBD**
- Enforced concurrency/cache/temp limits: **TBD**
- Peak complete process-tree memory and worker Job facts: **TBD**
- CPU and elapsed time: **TBD**
- Peak cache and temporary storage: **TBD**
- Output size: **TBD**
- Slow-I/O and low-space/disk-full behavior: **TBD**
- Cancellation acknowledgement and cleanup: **TBD**
- No-discrete-GPU/CPU-path evidence: **TBD**
- Hardware wording and limitations: **TBD**

No named physical configuration may be described as verified unless it was actually tested.

## 10. Accessibility evidence

- Keyboard-only primary packaged workflow: **TBD**
- No drag-only operation: **TBD**
- Focus order and visible focus: **TBD**
- Names, roles, states, relationships, and progress announcements: **TBD**
- NVDA environment and result: **TBD**
- 200-percent scaling/text and clipping result: **TBD**
- Contrast/high-contrast/reduced-motion result: **TBD**
- Plain-language error review: **TBD**
- Accessible package-summary result: **TBD**
- Known output-accessibility limitations: **TBD**

Earlier task-level accessibility records are linked above and must not be relabeled as the final
packaged matrix.

## 11. Security and privacy evidence

- Threat-model revision: **TBD for release freeze**
- Packaged worker isolation and network denial: **TBD**
- Path traversal, command injection, and unsafe-overwrite tests: **TBD**
- Hostile PDF/image/template tests: **TBD**
- Timeout, memory, page, pixel, and temporary-storage limits: **TBD**
- Original preservation and signed-source handling: **TBD**
- Logging/support prohibited-content audit: **TBD**
- Update provenance/signature tests: **TBD**
- Independent M6 permanent-redaction gate and exact-candidate result: **TBD; required for PWS-0808/v1.0. A preview that omits redaction cannot satisfy that gate**
- No-telemetry default: **TBD for candidate**
- Security reporting process: **TBD**

## 12. Migration and recovery evidence

- Supported prior case versions: **TBD**
- Forward migration and idempotent reopen: **TBD**
- Interrupted/disk-full migration: **TBD**
- Future-version rejection: **TBD**
- Worker crash/restart recovery: **TBD for candidate**
- Update failure and application rollback/recovery: **TBD**
- Migration-backup cleanup: **TBD**
- Recovery documentation walkthrough: **TBD**
- Confirmation that only PaperWorkShell performs case migrations: **TBD**

Portable workspace/project import/export remains unsupported unless later independently approved;
case-schema migration does not imply a portable handoff format.

## 13. Licence, provenance, and maintenance evidence

- Final packaged dependency inventory and SBOM: **TBD**
- Bundled engine/model/template/font licences and notices: **TBD**
- Release input hashes and provenance: **TBD**
- Reproducible or documented-build status: **TBD**
- Vulnerability reporting route: **TBD**
- Supported versions and security-update policy: **TBD**
- Upstream abandonment/replacement strategy: **TBD**
- Maintenance status and capacity limits: **TBD**

## 14. Known limitations

Exact release limitations document: **TBD**.

At minimum, record unsupported formats, forms/signatures, redaction status, OS/file opening and
project-transfer status, rollback constraints, accessibility and resource limitations, and every
untested environment.

## 15. Portfolio handoff status

| Requirement | Status | Evidence |
|---|---|---|
| Frozen installer/package and hashes | Pending | **TBD** |
| Complete release interface | Pending | **TBD** |
| Non-sensitive samples and hashes | Pending | **TBD** |
| Sanitized support-bundle sample | Pending | **TBD** |
| Low-resource candidate profile | Pending | **TBD** |
| Migration/update/uninstall results | Pending | **TBD** |
| Security/privacy acceptance | Pending | **TBD** |
| Accessibility acceptance | Pending | **TBD** |
| Offline acceptance | Pending | **TBD** |
| Standalone integrity without ThirdLife | Pending | **TBD** |
| Ready for future B4 review | **No â€” draft template** | Complete only after the standalone release gate |

## 16. Approval

| Role | Name/identifier | Date | Result |
|---|---|---|---|
| PaperWorkShell release owner | **TBD** | **TBD** | Pending |
| Security review | **TBD** | **TBD** | Pending |
| Accessibility review | **TBD** | **TBD** | Pending |
| Release gate | **TBD** | **TBD** | Pending |

A future ThirdLife B4 review is separate. Completing this index does not approve PaperWorkShell's
inclusion in any ThirdLife release.
