# Release documentation

This directory holds the human-readable PaperWorkShell release envelope for packaged M8 preview
and stable candidates. These documents support ordinary standalone release engineering and a
possible later ThirdLife B4 black-box compatibility cut; they are not an application API or a live
cross-project dependency.

## Documents

- [Release interface sheet](RELEASE_INTERFACE.md) — exact identity, artifact, installation,
  launch, data, input/output, security, and support behavior for one packaged release.
- [Release evidence index](RELEASE_EVIDENCE.md) — hashes and links to evidence for that exact
  packaged release and environment.
- [Standalone release checklist](STANDALONE_RELEASE_CHECKLIST.md) — M8 candidate acceptance
  checklist; a checked item requires current evidence.
- [Windows 11 x64 support boundary](WINDOWS11_X64_SUPPORT.md) — selected application
  architecture and the exact edition/build evidence boundary under PWS-D043.
- [PWS-0802 x64 continuation](../development/pws0802-x64-continuation-20261104.md)
  — current scope, preserved R7 build epoch and remaining release work.
- [Finite x64 qualification matrix](../development/pws0802-x64-qualification-20261104.md)
  — accepted packaged evidence and the remaining in-scope checks.
- [ThirdLife alignment](../../THIRDLIFE_ALIGNMENT.md) — portfolio boundary and late-binding
  interpretation.
- [PWS-0802 complete-runtime implementation and qualification](../development/pws0802-community-runtime-20261103.md)
  — preserved unsigned internal R7 and separately corrected R9, exact identity,
  settled Microsoft route and actual scoped qualification, separately from the
  remaining real low-disk and trusted-signing requirements. The
  [earlier preflight](../development/pws0802-result.md) remains historical.

Both the interface and evidence files are draft templates. Their release-specific `TBD` fields
must remain unresolved until a real packaged candidate supplies the value. Product-contract and
development-stage facts may be linked as context, but they do not turn a source-tree test or policy
baseline into packaged-release evidence.

The application target is settled: **Windows 11 x64 only**, under
[PWS-D043 / ADR 0019](../adr/0019-windows-11-x64-only-scope.md). Native ARM64 and
x64 emulation on ARM64 are outside project scope, with no release/update prerequisite
or mandatory future task. Supported editions/builds and packaged scenarios still need
their exact evidence; this scope choice grants no test or public-release pass.

Current repository facts that are safe to use as development context include:

- Product name **PaperWorkShell** and Windows 11 x64-only, offline-first scope.
- Current project metadata version `0.0.0` with a pre-alpha classifier; this is not a release
  version; the stable application/upgrade identities are selected in ADR 0018.
- Implemented content-sniffed PDF/JPEG/PNG import, immutable case copies, SHA-256 source records,
  local restricted-worker processing, and single-PDF verified export.
- The implemented case convention
  `<current-user-profile>\AppData\Local\PaperWorkShell\Cases\<case-uuid>\`; exact packaged-release
  location and uninstall behavior still require M8 verification.
- Resource policy version 1 / `constrained-v1`, which is bounded software evidence rather than
  physical-hardware certification.
- A public synthetic fixture corpus and passed M0/M1 gates. These are candidate release-evidence
  inputs, not the frozen M8 sample/evidence package.
- A previewable opt-in local support bundle and
  [cleared synthetic sample](../../tests/fixtures/diagnostics/v1/README.md), owned by PWS-0805.

An unsigned engineering installer now exists, with publisher Janne Vuorela, scoped project-owned
MIT/free distribution and stable application/upgrade identity. There is no publicly trusted
signed release, completed Windows 11 x64 release matrix, project-transfer format or completed
M8 evidence package. Unsupported surfaces remain explicit.

The retained R7 installer is 512,713,704 bytes, SHA-256
`a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a`,
with actual signature status **NotSigned**. The 2026-11-04 source-policy documents
are newer than its embedded build metadata; no installer rebuild or evidence rewrite
is implied. The actual engineering guest was Windows 11 Enterprise 26100.9711 x64
with a genuine standard account. Windows 11 Pro 26300.9711 describes the host only.
R7's instrumented-guest observations and R6's clean document evidence retain their
separate scopes and audited reuse boundary.

