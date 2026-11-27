# Release documentation

This directory holds the human-readable PaperWorkShell release envelope for packaged M8 preview
and stable candidates. Under PWS-D044, the identified unsigned candidate can satisfy the v1.0
product-completion gate. Record product acceptance, observed artifact signing status and publication
approval independently. These documents support standalone release engineering and a possible later
ThirdLife B4 black-box compatibility cut using frozen public artifacts.

## Documents

- [Release interface sheet](RELEASE_INTERFACE.md) — exact identity, artifact, installation,
  launch, data, input/output, security, and support behavior for one packaged release.
- [Release evidence index](RELEASE_EVIDENCE.md) — hashes and links to evidence for that exact
  packaged release and environment.
- [R3 release notes](RELEASE_NOTES.md) — verified changes, scope, maintenance, and limitations.
- [Standalone release checklist](STANDALONE_RELEASE_CHECKLIST.md) — M8 candidate acceptance
  checklist; a checked item requires current evidence.
- [M8 gate report](../gates/m8-product-completion-gate.md) — formal review of the exact R3 candidate.
- [Windows 11 x64 support boundary](WINDOWS11_X64_SUPPORT.md) — selected application
  architecture and the exact edition/build evidence boundary under PWS-D043.
- [PWS-0802 x64 continuation](../development/pws0802-x64-continuation-20261104.md)
  — current scope, preserved R7 build epoch and remaining release work.
- [Finite x64 qualification matrix](../development/pws0802-x64-qualification-20261104.md)
  — accepted packaged evidence and the remaining in-scope checks.
- [TRACE B8 setup evidence](../development/pws0814-automated-setup.md) — exact engineering
  package, standard-user same-build repair and reuse, and one offline ordinary PDF workflow.
- [Engineering update evidence](../development/pws0803-authenticated-updates.md) — authenticated
  local activation, case preservation, offline export, recovery and replay refusal for exact R1/R2.
- [User guide](../user/USER_GUIDE.md), [care and recovery](../user/CARE_AND_RECOVERY.md),
  [known limitations](../../KNOWN_LIMITATIONS.md), and [security policy](../../SECURITY.md) —
  current workflow, privacy, maintenance status and reporting guidance.
- [ThirdLife alignment](../../THIRDLIFE_ALIGNMENT.md) — portfolio boundary and late-binding
  interpretation. See [source traceability](../portfolio/SOURCE_TRACEABILITY.md) and
  [non-gating future notes](../portfolio/THIRDLIFE_FUTURE_NOTES.md).
- [PWS-0802 complete-runtime implementation and qualification](../development/pws0802-community-runtime-20261103.md)
  — preserved unsigned internal R7 and separately corrected R9, exact identity,
  settled Microsoft route and actual scoped qualification, separately from the
  remaining actual low-disk and exact-candidate qualification. The
  [earlier preflight](../development/pws0802-result.md) remains historical.

The interface and evidence index identify the exact unsigned R3 engineering package,
its verified installer and sample, and the Windows configuration used for installed testing.
The candidate retains its embedded `0.0.0` version and `engineering` channel. The
[M8 gate report](../gates/m8-product-completion-gate.md) records the acceptance outcome.
Public distribution requires separate authorization.

The application target is settled: **Windows 11 x64 only**, under
[PWS-D043 / ADR 0019](../adr/0019-windows-11-x64-only-scope.md). Native ARM64 and
x64 emulation on ARM64 are outside project scope, with no release/update prerequisite
or mandatory future task. The Windows support matrix records the exact edition, build,
and conditions of the installed and component runs.

Current repository facts that are safe to use as development context include:

- Product name **PaperWorkShell** and Windows 11 x64-only, offline-first scope.
- Current project metadata version `0.0.0` with a pre-alpha classifier; this is not a release
  version; the stable application/upgrade identities are selected in ADR 0018.
- Implemented content-sniffed PDF/JPEG/PNG import, immutable case copies, SHA-256 source records,
  local restricted-worker processing, and single-PDF verified export.
- The implemented case convention
  `%LOCALAPPDATA%\PaperWorkShell\Cases\<case-uuid>\`. R3 preserve removal, reinstall,
  and explicit case removal have separate receipts in the evidence index.
- Resource policy version 1 / `constrained-v1`, which is bounded software evidence rather than
  physical-hardware certification.
- A public synthetic fixture corpus and passed M0/M1 gates. These are candidate release-evidence
  inputs, not the frozen M8 sample/evidence package.
- A previewable opt-in local support bundle and
  [cleared synthetic sample](../../tests/fixtures/diagnostics/v1/README.md), owned by PWS-0805.

The R3 engineering installer is unsigned and internal. Its publisher is Janne Vuorela.
Project-owned material follows the scoped MIT/free distribution terms. Portable project
transfer is unsupported.

The retained R7 installer is 512,713,704 bytes, SHA-256
`a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a`,
with actual signature status **NotSigned**. The 2026-11-04 source-policy documents
are newer than its embedded build metadata; no installer rebuild or evidence rewrite
is implied. The R7 engineering guest was Windows 11 Enterprise 26100.9711 x64
with a genuine standard account. Windows 11 Pro 26300.9711 was the host for that
rehearsal. R7's instrumented-guest observations and R6's clean document evidence
retain their separate scopes and audited reuse boundary.

The later TRACE B8 setup and PWS-0803 offline update rehearsals used a Windows 11
Pro x64 26300.9711 guest with a standard-user desktop. Both retain their exact
engineering artifact scope. The [Windows support matrix](WINDOWS11_X64_SUPPORT.md)
records the environments and evidence separately.

