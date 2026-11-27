# PWS-0802 Windows 11 x64 qualification inventory — 4 November 2026

> **Superseding internal-delivery reset, 2026-11-04:**
> [PWS-0810](pws0810-internal-delivery-20261104.md) owns the current usable
> unsigned application handoff. Retain this inventory's accepted observations
> within their recorded scope. Public signing, SignPath/onboarding/publication
> and remaining release-only qualification are deferred; the earlier active
> procedures below create no current work or owner resource request. Earlier
> contact permission is exhausted; no new external contact or upload is
> authorized. The real system-volume disk-full test is not performed and
> deferred to later release qualification; do not request its VM again.

> **2026-11-26 supersession.** PWS-D044 permits unsigned product completion. The
> earlier A2 signing requirement and signing-only procedures below are historical.
> Exact-candidate work and actual low-disk follow-up are in the [PWS-0807 matrix](pws0807-rc-matrix.md).

This inventory preserves earlier acceptance criteria, actual observations and the
finite procedure. The project target remains native Windows 11 x64. PWS-0804 and
PWS-0805 retain their accepted evidence.

The preservation baseline is
`output/pws0802/x64-scope-20261104/baseline-v1.json`, SHA-256
`9687373e51b82102a7fa7c0b4e1b1cd10d79930a4335dc46083dbd9d523f8ae9`.
It binds the pre-edit 2,126-file map, accepted R7 receipts and task blocks.
Historical output, failures, candidate bytes and source snapshots remain intact.

## Actual R9 qualification — current disposition

This section records the R9 outcome after the earlier execution plans. Those
plans, failures, build epochs and accepted observations retain their original
scopes. PWS-0802 was **blocked** under the policy at that date. R7 remains the
preserved baseline. R9 is a separate unsigned internal engineering candidate.

| R9 artifact | Actual value |
|---|---|
| Revision | `pws0802-internal-20261104-r9` |
| Installer | 512,732,779 bytes; SHA-256 `d29f7f6d081c0c43295ba8a3d5bb215344cefa6253a401be34ef0f74941bd9d4` |
| Receipt | `da8f1b8e69ccd5403758e1a83931121be485ff9fa6fe9cb17bd15f5958c7f426` |
| Inventory | `fa337baa3ea03d251706a74ca20e61c1594d67822af4de41041026e03e6a0121`; 3,195 files; 650,130,815 expanded bytes |
| Independent five-phase data audit | PASS `8e113985f492c35260f6f9f9320636b8571c58f1fea2cc296b04510a49409873` |
| Audit source closure | `80c90d1a249d6d5103585fdfb36ce7b5c6765fb1aa44034747678089473d3486` |
| Signature / distribution | Actual `NotSigned`, no signer; internal only |

The audit verifies 957 components, 956 unchanged third-party components, all
obligations and replacement rights, five unchanged Microsoft objects at nine
paths, and frozen application/wheel/sdist/installer-source provenance. Complete
application-local runtime delivery is verified at this inventory and the accepted
offline workflow boundary. It requires no user Python, uv, Visual Studio, PATH
repair, system OCR engine or separately installed runtime DLL. It is not a claim
that every workflow or Windows build has passed packaged qualification.

R9 contains the confirmed bundled Notices viewer, native-derived `SystemDrive`
and guarded native `--notices` entry. R8's direct Notices-entry limitation is
corrected in R9. There is no PDF/COM initialization or resource-policy change.
The historical R7 GUI inspection failure remains unexplained; subsequent controls
and the actual R9 first GUI import succeeded, without erasing that failed result.

The new offline Windows 11 Enterprise 26100.9711 AMD64 guest has a newly bound
genuine standard account: no administrator SID including deny-only, no linked
token and default elevation. Pilot `e49f28e1d9bfe4e7a41f8ef54758bda9c652feafe67c479a79521c5e6d54d17c`
binds VM `445da5ba-31dd-4438-8b9d-96178904d1dd`. Qualification instruments are
separate from the product; accepted clean R6 dependency/workflow evidence is
reused only within the audited unchanged boundary. No extra edition/build or
physical-device certification is inferred.

The reports below are under
`output/pws0802/x64-scope-20261104/remaining-episodes-r9-host-correction-v1/`.
Root's separate `root-cua-r9-v1/` screenshots and observations record actual
interaction; mechanical reports do not infer reader observations.

| Scenario | Actual result and scope | Collected report SHA-256 |
|---|---|---|
| A1/A8 install and public workflow | Install 0, full inventory/registration/shortcuts; first native GUI PDF, JPEG and PNG import, rotation, reorder, local language selection, quality OCR review and verified ordinary PDF export; natural close. | Input `85f3fea709eb0bff92c59ed937c8cb614872d588f8b3388675802bfce99e3e0c` |
| A4 local Notices | Native entry and actual installed shortcut open the bundled viewer offline; Microsoft/MIT terms, selection, find and paging observed with keyboard/NVDA. | `8aff2298deedbcd351220df7785ab79dacb8cf76ce6d96f9a4c1b277140075fd` |
| A9 interactive repair | Local terms readable; Next disabled before acceptance; Accept/Next/Install/Finish keyboard controls and progress observed after one taskbar activation. Repair 0/full inventory; cases and external files exact. This is not keyboard-only launch. | `3b6c10c2d9867e28bf5f43f3ea829a4c78f3b5596dfed8b221263aefa05795f8` |
| A9 refused-option message | Finite full NVDA message reading, keyboard dismissal and natural refusal 1 pass after disclosed taskbar activation and native system-menu/Escape/Tab interaction. Initial stale context and earlier failures remain retained. | Raw `d8fc47f0e45287367138b247d1ad3612c46cc4e34ac21d6897ec6a98e2309222`; join `8aaa2f06ffd865701512b5e2d021382b6f489faf7ecfa5a054ac46409414b697` |
| A11 review and Cancel | 1 available/0 unavailable; unchecked separate confirmation and disabled Delete; natural Cancel 1; installation, case and six external files exact. | `e8b55bc42efab32b6f873471b195dbf4abbc9d915987470c7dd78cf9d4028197` |
| A11/A12 explicit deletion and real failure | Inspect: 2 available/1 unavailable. After distinct confirmation and the reproducible held-manifest lock: 1 fully deleted, 2 recognized cases with data remaining, 1 failed/partial attempt. Locked imported and unsupported-schema cases stayed exact; six external files exact; natural Cancel 1. | `ae1040c87c90441b22068689f8b26bcb965aa68ce9051d53ace9208f3e5d415d` |
| A11/A12 default-preserve removal | Quiet removal 0; payload, registration and shortcuts absent. Two retained cases exact: 10 files/15 directories/744,147 bytes, snapshot `1e8f0543f0091caf819195c3f7aa6ca711498cec5f2ab7f1baf45f03e300385f`; all six external files exact; journal zero. | `75188e18537c4b8ae326fc15526eafcc7e8b3c10444e9c4140ec698e090e9065` |

All admitted episodes require natural expected exits, empty owned jobs, closed
handles and no fallback. The original failed error attempt timed out before
Root completed interaction; it remains failed and is not adopted. Subsequent
mechanical passes do not turn its missing speech into a pass. Source seal
`1b7c1c72fa9d742d64fdf3dc5b7d69a74785d403d20133f12f82471f60fad7c7`
and executed actor/wrapper bytes remain unchanged. Additive host correction
`82d2f5db75b2589a0ecff63195395798599784d1032bea4eed22fb74f3ddb16a`
admits a finite timing float only in the authenticated source manifest; mechanical
receipts stay integer-only. Its genuine before-failure and 18 passing models are
preserved. No accepted UI episode was replayed to correct that host parser.

The 23,444-byte export is
`0fca8b80a936ba121763eece709db2ccc0632d2e86147f44e976e82747b2fa8a`.
The application announced its verified export and case-history entry. This was
ordinary PDF export, not a new searchable-OCR export claim. Accepted R6 full OCR,
reopened verified outputs, reinstall/UUID reopen and exact interruption/recovery
remain valid at the unchanged worker/document/runtime boundary. Installer
interruption occurred at the observable ACK_REQUIRED gate and recovery used
authenticated stages plus repair; it does not prove automatic rollback or power
loss. Unsupported update, associations and optional architecture paths gain no
invented execution result.

Installed mechanics `e2f7e4e39b85065712c9ce94b1928a21292ce75abc9ce77e1117a0abb0283ee0`
and additive peer `c1b5282d8817b74c72ded5786c27c3632f7ed068ea2c84524f83d464487b197b`
verify all nine target ACL shapes. Two exact salted SID hashes identify Owner
Rights, correcting the raw helper's earlier `other` classification without changing
that record. This is not a current-logon ACE or all-profile permission claim.
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

## Completed finite reader and preservation observations

A11's fresh-reader supplement now passes at its stated scope. Source
`6c3dc0b969db84aba355c41329a6e4d5945c8341d6490e83ec214691698239e1`,
raw result `73d0fb64ee2ac477164093886a81b33fa901835d3f2d3afd1df982fbe6f73ec9`
and strict collected mechanics
`62ef00e5bca5633c7c7a73414b8f7d9d65dec879bb338e9791cf05b1bbb2fb5c`
and factual join `55868afb07f0cd67864e782ac0628f025b648fce27e710109bc45ae7786aab8c`
bind the same R9 candidate, genuine standard session and unchanged data. Root's
`root-cua-r9-client264778-v1/009..020` records the actual finite interaction:
initial stale Explorer context, taskbar activation, moving the review's own caption
clear of the untouched Speech Viewer, and opening the review's native system menu
by its icon. Escape/Tab then produced actual control speech. This includes the
unchecked review choice, Inspect, 1 available/1 unavailable inventory, read-only
status, separate initially unchecked confirmation and disabled Delete, explicit
confirmation, and the real held-manifest result: **0 fully deleted, 2 remaining,
1 failed/partial**. NVDA read the full result. Root verified the named Cancel
control before Space; natural review exit1 followed. These disclosed pointer
activation/positioning actions are not keyboard-only launch or a universal
assistive-technology reliability claim.

Reinstall0 verifies all 3,195 payload files; review1, reader0 and default-preserve
removal0 close naturally with empty owned jobs, closed handles and no fallback.
The canonical manifest lock is held during deletion and Cancel, then released.
Product/registration/shortcuts are absent, recovery journal zero, and both retained
cases, the input work tree and all six external files equal the initial state.
The V1 Alt+Space attempt and V2 300-second timeout remain failed, not relabeled;
V2's spoken unchecked choice alone was not an Inspect or deletion pass. No product
fix or exclusive cause for the original context gap is inferred.

A10/A11's bounded removed-state comparison and retained-artifact preservation are
complete. Independent case-byte review
`6a94ac1d264b59a0e6956af642b98ba2ed60ad0168a5f6fd17a6d5124603138e`
verifies all 24 archive members/10 files/744,147 uncompressed bytes. It adopts only
the case bytes from the failed first composite, not that composite's cleanup.
Independent work review
`ddc1cd36c7d235b208b903f9983ad5d4da0d42be2a244e68a4db04b7d54c1179`
verifies all 22 members/8 files/83,095 uncompressed bytes, all six external hashes
and the actual uppercase export filename. The ordinary 23,444-byte synthetic
export opens strictly without recovery as three pages. This is not searchable-OCR
or visual certification. All later reader supplements preserve these source
snapshots exactly; no backup rerun is required.

After these verified backups and final natural cleanup, Root stopped only the
owned guest 445da5ba through the official CLI. Stop0 and the empty environment list
are recorded in `root-r9-guest-retired-v1.json`, SHA-256
`9e4d3fafd62b86c20fdd30988046e50edeebd2aebd521cddd080af90c9bdf12d`.
VM retirement is separate from the actual product-uninstall proof.

## Exact remaining x64 observations and external gates

The table retains the 4 November gap inventory. PWS-D044 superseded the A2
publisher-signing requirement on 2026-11-26. The later R1 test used an enforced
standard-user NTFS quota on the offline Windows 11 Pro guest. Its results are in
the [R1 installer disk-exhaustion record](../release/RELEASE_EVIDENCE.md#r1-installer-disk-exhaustion).

| Historical item or criterion | Observation or input missing at that checkpoint |
|---|---|
| Qualification-plan real low disk | Actual bounded disk-full at the ordinary fixed installation target, recovery and case/external preservation. A fresh current-guest read-only probe `7339733454b669dc6b048aa510a6cbadafbd0fc4940c2a335584cdea18ae8a2b`, joined by `f56db5bd66e85372ce5c36f5c522c6881ffaed29485dadf6e3ece478b5d30934`, confirms NTFS flags 29,830,879 without FILE_VOLUME_QUOTAS. COM was not attempted. C: partition query fails with CimJobException; supported size is unqueried, owned jobs closed. No quota/partition/profile/account/policy mutation occurred. A contained licensed Windows 11 x64 target with real quota enforcement or a bounded ordinary OS volume is unavailable; simulations do not pass this scenario. |
| A2 production signing and final bytes | Qualified provider/approved route, exact final Authenticode signer/trust/timestamp/inventory, then checks affected by changed signed/repackaged bytes. Foundation inquiry submitted once; no response, admission or coverage is established. No signing credential is supplied. |

The exact observed platform is Windows 11 Enterprise 26100.9711 AMD64. Pro 26300.9711
is development-host evidence only. The engineering installer floor 22000 is not
qualification of that build. No additional edition, universal Windows-build or
ARM qualification requirement follows. Candidate claims require the applicable
checks on the exact artifact.

Codex owns technical preparation, operation and collection. The prior quota
mutation helper is explicitly a method/model preparation, not an operational R9
disk-full test. Any necessary owner involvement is limited to the specific
unavailable licensed target or access. No general manual matrix,
ARM environment or repeat Microsoft acceptance is requested. Microsoft evaluation
registration/account/agreement is separate from the settled MSVC route and has
not been submitted or accepted.

The reader diagnosis is finite and complete at the stated observation scope.
NVDA's Speech Viewer can pause while hovered/focused and affects observer context;
this and the remote input behavior are possible confounds, not proved exclusive
causes. No demand for universal reliability or speculative application fix is added.

## Preserved R7/R8 candidate epochs and evidence boundary

| Object | Accepted value |
|---|---|
| Candidate | `pws0802-internal-20261103-r7`, unsigned/internal |
| Outer installer | 512,713,704 bytes; `a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a` |
| Installer receipt | `0add5a47b863111cc8c0187aa17d6b84e006fcf01ddfd8840d76bebb4ff0afc6` |
| Resolved inventory | `60caaf221b9c55f1ebc5a0d5c285579bd332972e973ef6cc0187effe098204b8` |
| Payload | 3,193 files, 650,091,464 bytes |
| Independent R7 audit | `ab4e1d33a0fb983380fbd2fd09873db08bb679b92c3057b48861caa14a92cffd` |
| Audit source closure | `3aaf3f502f2cd38ca781bb593504e9963ecee95d37ec350db9ba910ca537b805` |

A separate experimental build now exists. It does not overwrite, relabel or
supersede the accepted R7 baseline automatically:

| Experimental object | Actual value |
|---|---|
| Candidate | `pws0802-internal-20261104-r8`, unsigned/internal, runtime qualification incomplete |
| Outer installer | 512,732,582 bytes; `11a5fdd25beb6072c87ffb3fdf1e410f7b40a36af2fca601b33babed89743829` |
| Installer receipt | `c38f03943128c6a087c5f4d043f1454b893297afa543d301bacd5163db4d5057` |
| Resolved inventory | `1e2831f7bb5187439da2febb8c32fd313396c3b7ddc7bea9dd38dfd277aff486` |
| Payload | 3,195 files; 650,130,191 bytes |
| Independent data audit | `27f3a27dbc45170b9b2bada1b8aebf374d17f21cc36ac524c5bcfbfadba3e895` |
| Frozen build source map | `463a5657e8ba580af19d19e4383f6a243eeeebb6dbfd80f98e245ec0bc788fb2` |

R8's five data-audit phases pass: exact files, official SBOM/notice schemas,
ownership/obligations/replacement rights, unchanged five Microsoft objects at
nine paths, and frozen application/build source provenance. All 956 third-party
components remain unchanged. Its 321 application sources comprise 309 Python
modules and 12 JSON resources; the installer source archive has 896 members.
The first audit attempt failed because its allowlist omitted README-derived
first-party runtime METADATA. The narrowly corrected auditor verifies that file
against the checked wheel metadata and frozen README; the original failure is
retained. This is not a product defect or a broader byte-change allowance.

R8 has a known source-policy limitation: its new Notices shortcut directly
invokes the GUI interpreter and bypasses the native launcher's process-specific
Windows Error Reporting suppression. No crash or upload was observed or tested.
A source correction routes Notices through the same native guard; it requires
another candidate and affected qualification. R8 is therefore experimental,
not a qualified replacement or public release.

The audit's exact R6-to-R7 runtime delta is the removal review module plus the
installed candidate marker. Other runtime, worker, native launcher, engine and
Microsoft runtime bytes retain their R6 identity. Third-party notices and
replacement rights are unchanged; `notices/COMPONENTS.txt`, the two application
source archives and derived first-party provenance/SBOM artifacts may refresh
only to reproduce that authorized source delta.
It proves the boundary for reusing accepted R6 checks; it does not turn an R6
execution into an R7 execution. R7 remains unchanged. New defects found below
require separate source corrections and affected packaged qualification.

The actual clean R6 guest was Windows 11 Enterprise 26100.9711 AMD64, offline,
with a genuine standard session, no administrator SID including deny-only groups,
no linked token and default elevation type. Development prerequisites and the
tested system CRT/ambient OCR entry points were absent. This supports that exact
observed environment, not every Windows 11 edition/build or named hardware class.
The installer's engineering minimum is a compatibility filter, not proof of the
whole range. Public release documentation must state its supported range honestly.

The historical guest was stopped and the environment list was empty. Its closure
receipt is `final-sandbox-close-r7-v1/result.json`, SHA-256
`c60de7376b9ef0923fdcf795f31f173012daa41abfa5c84fbf0122c639b3f0b6`.
No old VM UUID, machine-name hash, session or logon snapshot authorizes new work.

## Accepted evidence ledger

Paths below are under `output/pws0802/community-20261103/`. A hash is an evidence
anchor, not permission to rerun the corresponding historical controller.

| ID | Evidence and exact scope | SHA-256 |
|---|---|---|
| E1 | `fresh-r6-clean-v1/completion-v1/actual-joined-result.json`: clean standard-user/offline R6 lifecycle; first five original phases plus separate UUID-reopen/final-preserve completion. The original helper failure/native 2 remains failed. | `91dcc35ab341a83d04f7a84a75bf514db8a884e591d645215fc0e43db3b7a977` |
| E2 | `r7-review-qualification-v1/actual-mechanics-result.json`: R7 install 0, same-candidate repair 0, public GUI 0, option refusal 1, review Cancel 1 and Keep/removal 0; NVDA 0; all owned jobs/handles closed; preserved snapshot exact. | `4984fe7c8998df7b06d24b04bbc680805769bc350f3a325e2d6979f581f5c962` |
| E3 | `r7-review-qualification-v1/root-cua-observations-v1.json`: actual shipping Keep review, unchecked choice, roles/status, confirmation and completion announced; first Cancel title-only gap retained. Full option error was read after clicking its text. | `91a7ead89e669efc0deca6453461a7630902cfa8becaa66da2e05684b76de4bc` |
| E4 | `r7-review-reader-state-v1/actual-mechanics-result.json`: fresh reader, ordinary shipping Cancel, NVDA quit and quiet preservation all natural; earlier stale-context observation is retained separately. | `efcd8bf9e4fc1ce28cee2d6037459a3c63dcbfc2a257e582baee8439868b7d95` |
| E5 | `r7-review-focus-probe-v1/actual-diagnostic-result.json` and `r7-review-focus-probe-v1/root-cua-observations-v2.json`: first ordinary shipping review announced all relevant controls **before any UIA query**, then natural Cancel, NVDA quit and preservation. | Result `6ed7abe7a6c838d44f11efb60db5f069c1ceaca402b433789b6df7934755b636`; observations `b7a1c8f100dabb019769e55f31e0ad3818312af72a5b60558fcaa04eaebfd25b` |
| E6 | `installer-interruption-r6-fresh-v1/actual-result.json`: exact live installer reached RH ACK_REQUIRED; owned interruption 73/job empty; two known complete stages retained and authenticated recovery followed by ordinary repair 0/full inventory. No automatic rollback or power-loss claim. | `eea85497a1ee2caa7e140b46ca0f08a197ba98db46fad2ea6b630410b506e3ee` |
| E7 | `worker-interruption-r6-fresh-v2/actual-result.json`: controlled orphan, then automatic recovery through normal desktop composition, 1 to 0 journal entries; no manual journal deletion. | `a7d7c80a7c4e25a7d4c57450b37a562e310927136ad96308ad3c48f5a152ca44` |
| E8 | `r6-preserve-uninstall-archive-v1/actual-result.json`: authenticated synthetic backup plus preservation uninstall before R7 installation. | `d746d0a9078314239aea0bed827eb063ce0038c0b8f8722a0764fa1b4514ace8` |

The [runtime report](pws0802-community-runtime-20261103.md) supplies the detailed
receipt chain. Earlier host/source UI observations and tests remain supporting
evidence at their recorded scope. They are not relabeled clean/package executions.

## Historical acceptance inventory before R9 execution

The numbering follows the current PWS-0802 task order. “Remaining” names the
specific absent observation; it does not invalidate a separately accepted result.

| Item | Accepted support | Exact remaining observation or decision |
|---|---|---|
| A1 Clean Windows, no development environment | E1 actual clean R6 runtime/document workflow; independent R7 unchanged-dependency audit; E2 actual R7 packaging. New actual standard-account install verifies all 3,193 files. | **An R7 normal native-launch PDF import failed.** Subsequent direct-service, Qt-thread and default-policy controls succeed but do not explain that observation. Qualify the corrected candidate's first public GUI launch and PDF/JPEG/PNG selection, OCR/edit/export and verified reopen in a fresh standard-user guest. If the GUI failure recurs, capture its actual boundary and correct it before a pass. E1 does not establish the public-launch seam. |
| A2 Approved signing; private material protected | Approved signing procedure and failure controls exist; current artifact is explicitly unsigned. | Provider/approved process and actual final signature, signer, trust/timestamp and final signed inventory. Root owns this external gate. No keys, credentials or private material are requested in this document. |
| A3 Least-privilege paths/permissions | Fixed per-user destination; genuine standard-token install/repair/run/remove. New nine-target before/installed ACL comparison and independent peer review pass; no enumerated broad-group write. | Rerun affected inventory/permission checks after a corrected package. This is a bounded target comparison, not an all-profile ACL claim. |
| A4 Notices and versions available | Exact component versions, terms, notices and corresponding sources audited; experimental R8 includes the bounded viewer. | **R7 ordinary Notices shortcut failed:** Notepad is absent. R8's shortcut bypasses the native process crash-report suppression, so the source routes it through that guard for the next candidate. Qualify the final offline shortcut, component/version/terms navigation and keyboard/NVDA behavior. |
| A5 Independent stable identity | ADR 0018, exact product/upgrade/application IDs and audited package metadata; no sibling dependency. | No new execution required. Root reconciles current release documentation; product/version independence remains unchanged. |
| A6 Licence and supported Windows matrix consistent | Scoped MIT and accepted Microsoft-only terms; audited exact dependency rights; observed x64 OS facts above. | Root/ADR 0019 records the x64-only selection and honest edition/build scope consistently. ARM is out of scope. Signing does not expand tested OS coverage. |
| A7 ARM exclusion | Current PWS-D043 scope explicitly excludes ARM64 builds, support and x64 execution on ARM64. | No ARM execution, environment request or pass is required. This is a scope exclusion, not a capability test. |
| A8 Standard-user launch; limited elevation | E1 genuine standard account; E2 native public launch. New actual SYSTEM/session-0 control passes: installer exit 1, intended `PWS_INSTALL_PRIVILEGE`, targeted state unchanged and all owned resources closed. | Rerun affected normal launch/operation checks after the public-launch correction. The negative control proves the intended refusal, not ordinary document processing. |
| A9 Safe documented silent options | Exact `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-` install/repair and preserve removal observed; bound Microsoft acceptance once for install. | Preserve that narrow option contract. Keyboard-only full reading of one rejected option is a remaining accessibility observation, not a new silent combination. |
| A10 Optional safe associations | OS file/URI association is explicitly unsupported. New actual before/installed comparison passes: four selected extension defaults unchanged and application class keys absent. | After-removal residue comparison remains; repeat installation deltas only when affected by a corrected package. No association feature or file-opening API is added. |
| A11 Separate removal/data choice and residue | E2/E3 actual default-preserve and cancellation; E5 roles/defaults before query. Source/native tests cover exact authenticated deletion and unsafe retention. | Packaged review inspection, separate confirmation, actual synthetic deletion, truthful unavailable/denied result, and category-specific residue below. Existing preserve/NVDA roles need no blanket replay. |
| A12 External originals/exports never deleted | E1/E2/E8 snapshots and source/native deletion tests preserve external sentinels. | Check the same independent synthetic sentinel hashes through the new confirmed-deletion scenario. This is the newly affected path; no private/host source is read. |

## Historical verification inventory before R9 execution

The task has three verification bullets. Each named operation is separated here
so a passing neighbor cannot hide a missing observation.

| Operation | Disposition |
|---|---|
| Install on clean Windows | E1 accepted R6 clean install; R7 dependency/runtime equivalence and E2 actual install. |
| Standard-user launch | E1 genuine token and E2 public R7 launcher observed. |
| Repair as supported | E2 exact same-candidate quiet repair 0 with full inventory between and after both executions. |
| Update as supported | Different-candidate update is intentionally refused and belongs to PWS-0803. Existing candidate is preserved; do not invent an update pass or remove it as an automatic replacement workaround. |
| Uninstall | E2/E3 ordinary preservation and quiet preservation observed; explicit case deletion remains below. |
| Preserve cases | E1/E2/E8 exact preservation accepted. |
| Explicit data removal | Packaged observation missing; source/native tests are support, not a substitute for the shipping choice/confirmation path. |
| Reinstall and reopen | E1 joined actual proof, including unchanged original failure and separate completion; unchanged R7 storage/worker bytes audited. |
| Privilege | Actual standard-token install and SYSTEM intended refusal pass separately; ordinary native-launch import failed and needs correction. |
| Restart | Accepted operations use no-restart and exit naturally; source disables forced restart and rejects overrides. No reboot/pending-file-replacement result is claimed. Inspect pending-state deltas for the finite ordinary sequence; any untested restart path stays unsupported/unproved. |
| Rollback | E6 controlled repair interruption/recovery accepted. Automatic rollback, arbitrary-stage cleanup and power-loss recovery are not supported claims. No general rollback rerun is demanded. |
| Optional association | Unsupported contract; actual targeted before/installed no-registration/default-preservation comparison passes. After-removal comparison remains. |
| Signatures | Outstanding A2 gate. An unsigned inventory pass cannot become a signature pass. |
| Packaged file inventory | Exact R7 all 3,193 files and notice/source/SBOM closure audited and observed after install/repair; repeat only after new affected operations or signed bytes. |

## Historical R7/R8 continuation observations

These records are under `output/pws0802/x64-scope-20261104/`. They belong to the
new owned offline Sandbox and genuine standard session, not the stopped R6/R7
guest. The current journal has 13 admitted maps; earlier journals retain their
historical counts. Observer files and NVDA are separate qualification instruments,
not product prerequisites.

| Observation | Actual result and exact receipt |
|---|---|
| Ordinary per-user R7 installation | **Pass:** native exit 0; all 3,193 payload files, registration and shortcuts verified; standard child and owned containment closed. `remaining-v1/packet-v1/evidence/install-session.json`, `8a9881853b96ce8536365450ef1911852c1610ef24fc9b5814a2ce8a3b94375e`. Root dispatch: `fc3ddb0d29223e8d4b5a82eb23327e89fb5f8dd2ff8d697717c8f8dc9842d00b`. |
| A3 installed paths/permissions and A10 selected file defaults | **Scoped pass:** nine owned targets; seven inherit standard user/System/Administrators FullControl; managed root and Cases have protected Owner Rights/System/Administrators FullControl. No enumerated broad write grant. Four selected extension defaults unchanged; product class keys absent. Installed receipt `48b179c81dcec5ee972a50fc041464b6bbf6d91ce712fe7a85dbf88ddc1859e2`; independent peer `mechanics-peer-review-v2/receipt.json`, `753fe7d368e751fb9752bc65ae2c042209ad0fc0507c92ce2e26f44bd0778a94`. This does not prove all ACLs, cleanup or reboot. |
| A8 privileged installer refusal | **Pass:** actual SYSTEM/session 0; installer exit 1 and bounded private log contains `PWS_INSTALL_PRIVILEGE`; targeted state unchanged, job empty, no fallback, all handles closed. `mechanics-v1/packet-v1/evidence/negative-result.json`, `36ab6c32dc293dbc09690b7562f53710336e959c329ed57f162306c9c1979b4a`; wrapper `9616e8667510c93baa6f0cd335f006bfd59e5111054fcb365abb0ac8b2b04d82`. |
| A4 ordinary Notices shortcut | **Fail:** the installed shortcut requires `notepad.exe`, absent in this clean Windows 11 guest. Broken-shortcut message was visible and read by NVDA; the shortcut was retained. No notice content opened. `root-cua-v1/notices.json` and CUA transcript preserve the observation. The original monolithic actor subsequently timed out at its notice gate; it is not a completed qualification pass. |
| A1 ordinary native-launch PDF import | **Fail:** ordinary keyboard case creation and Ctrl+I copied the 596-byte synthetic PDF, then reported that the prepared source could not be inspected safely; zero active pages. `remaining-episodes-v1/evidence/d08490e8e215436094b453b6d04bd4f4/result.json`, `0e7c94d5c20181eead0088d5bbefb62731e3c293adeb8b6d3c8528cc722c33ad`; Root observation `root-cua-v1/input-d084.json`. The later JPEG capture is after cleanup and does **not** show the error. NVDA quit naturally with exit 0. The product was stopped by the failed input barrier timeout; no natural-close or completed-input pass is claimed. The created empty case, failed prefix and all work remain preserved. |
| Launcher environment diagnosis | **Reproduced defect, not yet the complete import cause:** three harmless standard-session children compare normal, exact seven-entry launcher environment and that environment plus only native-derived `SystemDrive`. Without it, ProgramData resolves successfully to a nonabsolute path containing `%SystemDrive%`; adding it restores all six known-folder hashes to normal. `runtime-env-diagnosis-v1/knownfolder-result.json`, `f4987e62204dd0b6b7eb9747bf0bb7c78b162810fcd019ae8568496276a3d20f`. No worker/profile creation or policy change occurs in this probe. |
| Actual worker-start comparison | **Observed, not an import pass:** both exact seven-entry and plus-only-SystemDrive environments start the normal installed worker to READY and shut it down normally. No typed launch error occurs; both AppContainer folders are absolute. Both owned jobs are empty, no fallback is used, all handles close, all six quarantine counts are zero and recovery stays zero. The original failed case, all 3,193 payload files/uninstaller and the pre-existing literal-path residue remain unchanged. `runtime-worker-cause-v1/result.json`, `3ecb1360d996feb4551e96af143b34f8140b4b2e56b060a0bae9e71869fb0033`. Thus the controlled startup comparison does **not** establish SystemDrive omission as the PDF error's cause. Source-ingest/GUI operation diagnosis remains open. |
| Synthetic PDF validity control | **Host fixture check passes:** the exact 596-byte PDF opens as one PDF 1.3 page with zero pikepdf syntax findings. `runtime-worker-cause-v1/fixture-validation.json`, `c43e07d82178d237336ebdeec8f693b9dfb43820973a49c5311f3a4ceaad3bc1`. This is not a packaged import, renderer or export result. |
| Packaged low-disk capability | **Unavailable in this guest; scenario unrun:** NTFS volume flags are 29,830,879 (`0x1c72edf`), without `FILE_VOLUME_QUOTAS` (`0x20`). COM was not attempted. No quota, account, ACL, profile or product mutation occurred. `quota-v1/packet-v2/evidence/quota-capability.json`; independent peer `a4427929a3198b08ebf8f19054ac1c3ca8b7d256a0bf32a100a6c9b10902a848`. A quota enforcement probe on this unsupported volume is not authorized by this result. |

The original Notices correction has 11 files sealed by
`notices-viewer-fix-v1/source-freeze-v1.json`,
`cec8de6579048027703965ef50bd3ae8a49cfab842b6d540dab0a70de0f8b514`.
It uses the existing application runtime, fixed installed notice resources and a
bounded read-only Qt viewer. It adds no dependency and changes no Microsoft DLL
or accepted recipient term. The affected 140 tests pass (one existing host
symlink-capability skip); format/lint/typecheck cover 932 source surfaces, and
localization validates 2,831 messages in three catalogs. Those bytes are now
included in experimental R8; this is not a guest/NVDA pass. R7 remains
byte-for-byte preserved.

The native launcher correction included in R8 derives
`SystemDrive` from `GetSystemWindowsDirectoryW`, validates the native drive prefix
and inserts that one value into the sorted minimal environment. It never copies
the ambient value. A real native child queries ProgramData and checks absolute
resolution and the independently derived drive with the ambient key absent and
with two poisoned values. All three regressions fail against the original
launcher. The corrected source passes 10 native, 15 builder-unit and 10 security
tests; canonical format/lint/typecheck pass. Its two-file seal is
`launcher-systemdrive-fix-v1/source-freeze-v1.json`,
`acac2ecb0ad6441d888f51b7defb6c55a97bcde0fc149f542ad07811f817a010`.
The initial CRT-free build failure from a large automatic buffer is retained;
using the existing static-buffer pattern fixes it without adding a runtime
library or changing limits. Host-native tests are not packaged guest results.

The subsequent Notices native-entry correction is sealed separately by
`notices-native-entry-fix-v1/source-freeze-v1.json`,
`58df9ad1105d72d41469c0dc7d1b2be564574b1acda6ce00c7328628b652cd00`.
Only the native launcher, its fixed shortcut and two existing test files change.
The fixed `--notices` mode selects the one bundled module, retains the early
process error mode, safe DLL initialization, minimal environment and Job, and
delegates the existing application mutex to the viewer. Extra operands are
refused. Two genuine before failures are retained; all 86 relevant tests and
canonical static checks pass. The separately built native launcher is 24,497
bytes, `1dd8fe16cf892ca34fd6739c7fed649452697916d1fdb45dcc35fa6ee9479c00`,
with receipt `71c8d2b080761946a9716afd640f57c3ba07dc29ccf7111e8f6f9554c5bc391b`.
It is not yet a new installer or a packaged qualification result. Windows
settings and third-party runtime bytes are not changed.

Further R7 controls retain the original failed GUI observation:

- The same 596-byte PDF imported successfully through normal stores and
  SourceIngestService in both exact-seven-entry and plus-only-SystemDrive
  environments. Two new synthetic cases each committed one source and one page;
  the original failed case stayed unchanged. Receipt
  `runtime-pdf-cause-v1/result.json`,
  `4889240e279a73e75a0f5dfc69ba74c36d997ff274afa4dc90ef28340ee5d8ce`.
- A plain QObject/QThread control with a normal main-created resource context
  also imported that PDF successfully without forced COM initialization.
  `CoGetApartmentType` reported `0x800401F0` on main and worker threads throughout;
  this does not support a COM or general QThread fix. Receipt
  `runtime-qthread-cause-v1/result.json`,
  `352dbe973cca1a6370e1b927f67cffc1b4443b04384cee303b4739aae60ffbb4`.
  The thread stopped naturally, all owned processes/handles closed, recovery
  stayed zero and all three prior cases remained unchanged.
- A further QThread control uses the actual default normal policy, with its
  complete values recorded and no environment override. It also imports one
  source/page successfully and closes naturally; prior four cases, payload,
  uninstaller and residue remain unchanged and recovery stays zero. Receipt
  `runtime-normal-cause-v1/result.json`,
  `c8715f3863406b7bd1fbb71b09cedfc6bad20655f04a1ee327a3bde451abd340`.
  Neither profile's limits were changed. These post-failure controls do not
  explain or universally exclude the original cold/GUI orchestration failure.
  No PDF, COM or resource-policy code change is supported by these successes.

The next candidate is selected as `pws0802-internal-20261104-r9`, retaining the
confirmed viewer/environment/native-entry corrections only. Its installer is
not yet built. The next execution must be a fresh guest's actual first public
GUI import with NVDA, followed by the finite remaining workflows. No further
isolated success is substituted for that observation.

The accepted R6 smoke uses installed Python and direct desktop composition while
retaining the WTS environment. It still proves its recorded offline document,
storage and recovery behavior. It did not exercise the native launcher's reduced
environment through a real document worker, so it does not contradict or close
the newly observed public-launch failure.

The 21-row [qualification matrix](../../packaging/windows/QUALIFICATION_PLAN.md)
also makes low-disk and removal-failure coverage explicit. Actual packaged
low-disk evidence is absent; a mocked free-space value or early path refusal is
not disk-full behavior. Use a bounded OS quota/volume capability only after its
containment and restoration are reviewed. If no such capability is available,
record that specific unavailable check instead of allocating unbounded filler or
claiming success. Do not weaken installation path/alias checks to mount a test disk.

## Historical remaining list before R9 execution

| Requirement | Precise missing observation |
|---|---|
| A1 public launch and packaged offline workflows | Corrected candidate in a fresh standard-user guest: actual first public GUI PDF/JPEG/PNG import, relevant OCR/edit/export and reopened verified output. Preserve the unexplained R7 failure and all scoped controls; investigate an actual new failure if reproduced. |
| A4 notices | Corrected package's ordinary offline shortcut opens its bundled viewer; component versions, MIT and the scoped Microsoft/other component terms are reachable with keyboard and NVDA. |
| A9 accessible rejected-option error | Fresh reader and finite normal keyboard method read the complete active refusal, then ordinary dismissal/native refusal exit. Pointer-only reading and pre-interaction automation failures do not count. |
| A11 review inspection and Cancel | Packaged inspection, separate unchecked confirmation and Cancel, with exact data/install/sentinel preservation. Existing default-preserve/roles observations remain accepted. |
| A11 explicit deletion and truthful partial result | Known supported, unsupported and held-denial synthetic cases; actual confirmation/deletion; accurate retained/failed counts; controlled release of the owned handle. |
| A12 external protection | Exact external original/export/support sentinels unchanged through the newly observed confirmed-deletion path. |
| A11 removal residue and restart facts | Ordinary final preserve removal plus bounded after-removal inventory, registration, shortcut, selected default and pending-state comparison; no general profile sweep. |
| Qualification matrix low disk | Actual bounded disk-full at the ordinary fixed installation target, recovery and case/sentinel preservation. This Sandbox volume lacks quota support; source plans and free-space simulations are not passes. A usable contained environment remains to be established. |
| Corrected/repackaged bytes | New candidate hash/inventory/source closure and reruns affected by its actual shipping delta. Old R7 results retain their original scope. |
| A2 trusted public signing | Qualified approved signing route, final artifact signature/trust/timestamp checks and all reruns affected by signed/repackaged bytes. Foundation inquiry receipt is not acceptance. |

ARM64 and Microsoft agreement acceptance are not outstanding observations or
owner actions. The genuine standard-account and observable interruption/recovery
requirements already have the scoped accepted evidence above.

## Supporting tests and what they do not prove

Accepted canonical checks include the following source/native boundaries. This
inventory requires no repeat merely to change documentation or helper ergonomics.

- `tests/accessibility/test_uninstall_review_accessibility.py`: default Enter
  preserves without storage access; keyboard inspection, separate unchecked
  confirmation and final action; names and enlarged reachability.
- `tests/unit/test_uninstall_review.py` and
  `tests/unit/test_uninstall_review_dialog.py`: one-shot consent, cancellation
  before/between cases, progress, partial results, result acknowledgement and
  actual main event-loop/exit mapping.
- `tests/security/test_uninstall_review_security.py`: corrupt/future data,
  changed manifest/identity, active cases, hardlinks, mutex exclusion and truthful
  partial outcomes; external original/export preservation.
- `tests/integration/test_case_lifecycle_windows.py`,
  `tests/integration/test_post_export_deletion_windows.py` and
  `tests/security/test_case_lifecycle_security.py`: real Windows authenticated
  deletion, leases, aliases, revalidation, partial restart and outside sentinels.
- Installer builder/qualifier/signing tests: exact identity, source/inventory
  closure, caller/target guards and signing failure controls. They do not turn
  a simulated installer error into an actual packaged low-disk or elevated run.

Historical accepted keyboard/NVDA JPEG/PNG/PDF import and failure observations
remain in the M1/M2/M4 records, including
[PWS-0209](../accessibility/pws-0209-m2-nvda-smoke.md) and
[PWS-0409](../accessibility/pws-0409-pilot-nvda-smoke.md). New work checks the
packaged selection boundary; it does not repeat all their review/export variants.

## Historical Root-operated procedure for the preceding guest

This is a new synthetic-only guest procedure. It is not an instruction to test on
the host or reuse the stopped historical guest. Root provisions and operates the
guest/CUA. Agent work stops at reviewed source, immutable input and strict
collection. No owner must install tools, type paths, calculate hashes or repeat
already accepted cases.

### Admission and automatic preparation

Before any product action, bind the new official VM UUID and original offline
configuration hash, actual fresh pilot prepared/binding/post-login/report hashes,
current map journal, native AMD64 and the genuine standard session's salted
user/logon facts. A computer-name hash is only non-host corroboration, not unique
VM ownership. Refuse the stopped historical VM, missing/zero placeholders, stale
logon facts, unexpected maps, developer dependencies or a nonempty recovery journal.

Pin the R7 installer/receipt/inventory above and all executed helper dependencies.
Use the existing admitted standard-session/WTS containment implementation with
only reviewed fixed namespace changes. Copy the independent observer runtime,
its original PSF/Microsoft notices and four admitted pure storage files from the
exact inventory; do not run the observing actor in installed Python while testing
installer/removal behavior. No developer environment or extra package is added.

Use a new empty default managed case root belonging only to this disposable
standard user. Refuse any unexpected entry before creating fixtures. Product
controls inspect/delete **all** supported cases in that root; an internal custom
root is not a substitute for testing the shipping reviewer. Never target host,
historical or private data. Generate three small clearly labelled inputs, one each
PDF/JPEG/PNG, external preservation sentinels and only the synthetic cases required
below. Automatically record original hashes and exact expected root identities.

Quiet install verifies all 3,193 files, actual registration, shortcuts and new
owned uninstaller hashes. Existing accepted repair and full OCR smoke need no
repeat. Snapshot ACL categories, product registry/association presence, services,
tasks and restart indicators without exporting unrelated names/values. An elevated
negative control must fail before mutation, under separate Root-owned containment.

### Session A: ordinary packaged use and one refusal

1. Root opens the installed **PaperWorkShell notices** shortcut offline. Observe
   that component versions and included terms can be found, then close it.
2. Root launches PaperWorkShell normally, creates one synthetic case and chooses
   the prepared PDF, JPEG and PNG in three ordinary single-file chooser imports.
   Confirm the copied
   source/page counts and completion; automation verifies original hashes and
   installed module/resource origins. Close naturally. Use already accepted R6
   verified export evidence for unchanged export code; a new export is necessary
   only if this selection path exposes a distinct failure.
3. With fresh admitted NVDA, Root uses the fixed wrong-option refusal, reads the
   active error by normal keyboard navigation/reader command and dismisses it.
   One normal guest application cycle with Alt+Page Up or Alt+Page Down may bring
   it forward before Insert+B; these are Microsoft's documented
   [Remote Desktop application-switching shortcuts](https://learn.microsoft.com/en-us/windows/win32/termserv/terminal-services-shortcut-keys).
   Record the actual message accessibility and nonzero exit separately. Pointer
   activation of message text does not satisfy keyboard-only reading. If the
   normal method reproduces a gap, record exact steps and visible result; stop
   that item rather than retrying until an arbitrary successful run appears.

The existing roles/state observations before UIA querying remain valid. No UIA
query, forced accessibility activation, speech hook or programmatic input is used
to manufacture this new observation. Keep the pointer outside Speech Viewer's
text area when using its transcript, because that viewer can suppress updates on
hover. Do not attribute a gap or recovery to that condition without evidence.

### Session B: review, cancellation and confirmed synthetic deletion

1. After ordinary app/worker closure and journal zero, Root opens ordinary
   uninstallation. Verify the review choice starts unchecked and its inspection
   action is disabled; select that choice, activate **Review case deletion**, read
   the inventory, leave the
   separate confirmation unchecked and Cancel. Automation proves no case,
   installation, uninstaller or external sentinel changed.
2. In the same synthetic-only profile, preparation adds one bounded deliberately
   unsupported synthetic manifest and one owned deletion-denial fixture. Unknown
   fixture identities or any unanticipated case abort preparation. The denial is
   a held no-delete-sharing handle on a known synthetic file, admitted only after
   the displayed inventory and released by a finite owned guard; it is not an ACL
   change or journal manipulation. Native partial-removal tests already support
   this exact safety boundary.
3. Root opens ordinary uninstallation again, requests inspection, observes the
   unavailable count and separate unchecked confirmation, then confirms and
   activates **Delete reviewed cases**. Observe progress/final counts and the
   truthful failed/retained guidance; record the result and use Cancel to leave
   the application installed. Release only the helper's owned lock, then perform
   ordinary quiet preserve removal.
   Automation checks exactly which authenticated cases were deleted, denied or
   retained, every outside sentinel, no automatic retry, and final app/registration/
   shortcut disposition. A partial case must never be described as fully deleted.

Cancel before confirmation has no destructive timing race. Between-case active
cancellation remains supported by accepted tests; no new human race against fast
deletion is imposed. If a packaged active-cancellation observation is later needed,
first provide a deterministic bounded fixture without changing production timing.

The two short sessions are estimated at 25–35 purposeful selections/activations
and 7–10 minutes of active Root interaction, excluding installation, hashing and
automatic cleanup. Tab navigation adds keystrokes but no transcription. The
controller checkpoints each completed step immediately and resumes the first
unfinished one; a failed step does not repeat a completed notice/import/review.
The estimate must be checked against the frozen helper before dispatch and reduced
if the actual journey becomes arduous. Qualification waiting ceilings protect
owned processes; they do not add product review timeouts.

The main helper collects only ordinary installation/full inventory, the finite
notice/import/error/deletion observations, external preservation and owned cleanup.
Separate `mechanics-v1` preparation collects the before-install,
installed-before-removal and after-removal ACL/association/restart/residue facts
and the actual SYSTEM privilege refusal. It is agent-prepared and Root-operated,
not assigned to the owner. The separate `quota-v1` lane first checks whether the
new guest actually supports a bounded real low-disk scenario. Source preparation
or capability availability is not that scenario's pass. These lanes use truthful
cumulative map journals; the main binder accepts the independently authenticated
current count rather than assuming the pilot's initial two maps are still current.

### Collection and residue

Mechanical records and Root's human observations are separate. Record actual
created/exit-observed/native exit, fallback use, job empty, environment destruction
and every held-handle close. Success cannot be inferred from a missing window,
process timeout or generic wrapper exit. Each immutable control binds the nonce,
phase and raw READY hash; terminal controls use CreateNew, never Replace/WAIT loops.

Classify residue after removal, without arbitrary profile scans:

| Category | Required disposition/evidence |
|---|---|
| Exact binaries, engines, English data, notices, shortcuts and registration | All inventory-owned files and registrations absent after accepted app removal. |
| Reviewed supported synthetic cases | Exact authenticated deletion result and final file/directory facts. |
| Unsupported, unknown or denied synthetic entries | Retained with truthful unavailable/failed counts; no broad recursive fallback. |
| Case caches/temp/records/assets/derived files | Follow the case's actual authenticated outcome; no detached whole-profile wipe. |
| Worker profile/private temp/recovery journal | Ordinary cleanup or normal authorized recovery; journal must be zero before disposal. Never discard the journal to pass. |
| Optional user-supplied language data | Distinguish it from bundled inventoried English/engines; record actual path category and preserve/delete policy without inventing a selector. |
| Settings/logs/migration backups | State actual observed stores or absence; case migration backups follow case policy. No invented global store or cleanup path. |
| External sources, prior exports and support bundle sentinel | Hashes unchanged through explicit deletion and removal. User-selected destinations remain user owned. |

Use only fixed owned synthetic paths internally. Export counts, enum outcomes,
hashes, relative owned inventory IDs and sanitized numeric errors; do not export
raw document text, private paths, exception messages, SIDs or unrelated registry
values. Networking remains disabled. The previous bounded WFP window and worker
network-denial/source checks keep their scope; no new connection-attempt capture
or universal no-API-attempt result is inferred from offline operation.

## Completion rule and release boundary

This document adds no task pass. Root may close each remaining row only after the
exact required observation or an explicit supported/unsupported decision is bound.
Signing remains independent. Accepted R6/R7 evidence is reused only where its
unchanged-byte and behavioral boundary applies. Earlier failures and intermittent
reader-context observations remain available; finite normal-use observations do
not promise universal assistive-technology reliability.

PaperWorkShell remains standalone. No sibling service, shared runtime, database,
adapter, catalogue or synchronized release is introduced. Cases and external
documents stay under the user's ownership. The release interface is human-readable
documentation of proved behavior, not a new API. The next task is determined by
the validated task graph and the [current matrix](pws0807-rc-matrix.md). Earlier
PWS-0802 evidence keeps its original scope.
