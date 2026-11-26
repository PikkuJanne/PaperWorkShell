# PaperWorkShell Windows 11 x64 scope and evidence matrix

The maintainer's 2026-11-04 decision, frozen as **PWS-D043** in
[ADR 0019](../adr/0019-windows-11-x64-only-scope.md), selects **Windows 11 x64
only**. ARM64 support, native ARM64 builds and x64 execution on ARM64 are outside
project scope. They are not prerequisites for PWS-0802, PWS-0803 or public release,
and are not mandatory future work. A separate maintainer request would be needed
to change that boundary.

## Current engineering qualification matrix

| Environment | Architecture | Evidence and applicable limits |
|---|---|---|
| Windows 11 Enterprise 26100.9711, Windows Sandbox, actual standard account | Native x64 / AMD64 | Accepted R6 clean workflow/interruption and R7 scoped baseline remain preserved. Fresh R9 actual standard guest has install/full 3,195, native GUI input, notices and interactive-repair prefixes; confirmed partial deletion mechanics also pass. Default-preserve removal and bounded removed-state comparisons pass. Finite A9 refusal/A11 review-result reading and independent case/work archives are complete with pointer activation disclosed; actual low-disk remains open; no complete product-candidate pass. |
| Windows 11 Pro 26300.9711 development host, filtered administrator process | Native x64 | Earlier host-only evidence. It does not establish a clean environment or an actual standard account. |
| Windows 11 Pro 26300.9711, disconnected Hyper-V guest, standard-user desktop | Native x64 | Exact unsigned TRACE B8 passed same-build repair, reuse, launch and one offline ordinary PDF workflow. In a separate rehearsal, engineering R1 and the isolated test-signed R2 runtime passed authenticated local activation, an offline ordinary PDF export, restoration of R1 and replay refusal. Case files matched their baselines in both rehearsals. [TRACE B8 setup](../development/pws0814-automated-setup.md) and [engineering update proof](../development/pws0803-authenticated-updates.md). |
| Windows 11 Pro 26300.9711, disconnected Hyper-V guest, standard-user desktop, diagnostics R1 | Native x64 | Exact unsigned PWS-0807 R1 passed B8-to-R1 preserve-cases replacement, normal launch, ZIP save and overwrite refusal. The PWS-0814 entry subsequently completed the same replacement and verified the installed inventory. [Packaged R1 evidence](../development/pws0807-diagnostics-investigation.md#packaged-verification-on-unsigned-r1) · [R1 setup continuation](../development/pws0807-unsigned-completion.md) |
| Unsigned Windows 11 x64 product-completion candidate | Native x64 | Exact identity and remaining packaged checks are tracked in the [PWS-0807 matrix](../development/pws0807-rc-matrix.md). |
| Earlier proposed execution on ARM64 | Outside scope | Earlier unrun scenario superseded by PWS-D043. No pass or future prerequisite is inferred. |

The preserved accepted unsigned baseline is `pws0802-internal-20261103-r7`,
512,713,704 bytes, SHA-256
`a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a`.
Its original artifact audit, registered checks and installation receipts remain
bound to those exact bytes. The source-policy correction does not rebuild it.
Its archived metadata records the earlier inactive ARM64 proposal; that historical
statement is superseded, while its native-x64 installer behavior is unchanged.
The existing installer uses `ArchitecturesAllowed=x64os` and
`ArchitecturesInstallIn64BitMode=x64os`. Inno Setup's
[architecture reference](https://jrsoftware.org/ishelp/topic_archidentifiers.htm)
defines `x64os` as native x64 Windows, excluding execution on ARM64 through
emulation. The scope correction therefore requires no installer-byte change.

## Historical PWS-0802 experimental unsigned R9

`pws0802-internal-20261104-r9` is built, **512,732,779 bytes**, SHA-256
`d29f7f6d081c0c43295ba8a3d5bb215344cefa6253a401be34ef0f74941bd9d4`,
with actual signature status **NotSigned**. Independent audit
`8e113985f492c35260f6f9f9320636b8571c58f1fea2cc296b04510a49409873`
passes for all 3,195 payload files, 650,130,815 expanded bytes and 957 components.
Five Microsoft objects/nine paths at 14.44.35211.0 and their settled terms remain
exact. R9 does not promote, overwrite or relabel accepted R7 or experimental R8.

The fresh R9 guest is `445da5ba-31dd-4438-8b9d-96178904d1dd`, the same Windows 11
Enterprise 26100.9711 x64 edition/build with a genuine standard account. Native GUI
input/quality-OCR/edit/application-verified ordinary PDF export (`85f3fea709eb0bff92c59ed937c8cb614872d588f8b3388675802bfce99e3e0c`),
notices/shortcut and local terms (`8aff2298deedbcd351220df7785ab79dacb8cf76ce6d96f9a4c1b277140075fd`),
and keyboard interactive same-candidate repair
(`3b6c10c2d9867e28bf5f43f3ea829a4c78f3b5596dfed8b221263aefa05795f8`)
have scoped actual prefixes, with Root observations separate from mechanics.
R9 includes the native notices guard missing from R8. The original R7 PDF failure
remains unexplained; no COM or resource-budget fix is inferred.

Confirmed synthetic deletion mechanics pass
`ae1040c87c90441b22068689f8b26bcb965aa68ce9051d53ace9208f3e5d415d`:
one fully deleted, two recognized remaining, one failed/partial after the exact
lock and separate confirmation; retained imported/future cases and six externals
are exact. Default-preserve removal passes75188e18 and the corrected removed-state
category/default/external comparison passes40868e40.

Finite **A9 full refusal reading and A11 review/result reading now pass at their
recorded scope**. A9 raw `d8fc47f0...09222` and
Root join8aaa at `output/pws0802/x64-scope-20261104/r9-error-reader-focus-v1/actual-host-join-v1.json`
(`8aaa2f06ffd865701512b5e2d021382b6f489faf7ecfa5a054ac46409414b697`)
record full error text/OK and Enter after taskbar/native-menu activation.
A11 strict V3 mechanics at `output/pws0802/x64-scope-20261104/r9-review-reader-focus-v3/actual-collected-v1.json`
(`62ef00e5bca5633c7c7a73414b8f7d9d65dec879bb338e9791cf05b1bbb2fb5c`)
and Root join55868 at `output/pws0802/x64-scope-20261104/r9-review-reader-focus-v3/actual-host-join-v1.json`
(`55868afb07f0cd67864e782ac0628f025b648fce27e710109bc45ae7786aab8c`)
bind spoken one available/one unavailable, read-only inventory, separate unchecked
confirmation, checked Delete and zero deleted/two remaining/one failed-partial,
then named Cancel/Space/native1. Reader0/preserve0/all closures/no fallback,
product/registration/shortcuts absence, journal0 and exact cases/work/six externals
pass. Initial stale context and disclosed caption/taskbar/review-icon pointer
activation remain recorded; this is not keyboard-only launch, universal reader
reliability or a proved cause. Failed V1/V2 retries remain failed; earlier gaps
and all original evidence stay preserved.

Independent case review6a94 at `output/pws0802/x64-scope-20261104/r9-retirement-v2/root-case-review-v1.json`
(`6a94ac1d264b59a0e6956af642b98ba2ed60ad0168a5f6fd17a6d5124603138e`)
and work reviewddc1 at `output/pws0802/x64-scope-20261104/r9-work-archive-v3/root-work-review-v1.json`
(`ddc1cd36c7d235b208b903f9983ad5d4da0d42be2a244e68a4db04b7d54c1179`)
complete byte preservation/member hashes for the two retained cases and owned
work/six external files. Failed backup attempts remain preserved. Strict
pikepdf10.10.0 reopened the ordinary verified export as three pages; no searchable
OCR or visual verification claim follows.
Actual packaged low-disk remains UNRUN. Trusted signing remains absent; one
Foundation inquiry was submitted, with no provider acceptance or enrollment.


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

## Support statements and release boundary

Windows 11 x64 is the selected product target. Exact edition/build support claims
must identify the tested packaged candidate and evidence; the rows above do not
certify every Windows edition, cumulative update or physical device. The current
technical installer floor of build 22000 is an engineering admission check, not
evidence that build 22000 was qualified. Additional x64 edition/build claims need
their own applicable evidence when proposed.

Under PWS-D044 the exact unsigned candidate can complete the required x64
qualification. Existing engineering candidates retain their internal status. A
future publication decision records the actual compatibility and signing state.

See the [PWS-0802 qualification record](../development/pws0802-x64-continuation-20261104.md),
[finite x64 qualification record](../development/pws0802-x64-qualification-20261104.md)
and [release interface](RELEASE_INTERFACE.md).
