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
- [ThirdLife alignment](../../THIRDLIFE_ALIGNMENT.md) — portfolio boundary and late-binding
  interpretation.

Both the interface and evidence files are draft templates. Their release-specific `TBD` fields
must remain unresolved until a real packaged candidate supplies the value. Product-contract and
development-stage facts may be linked as context, but they do not turn a source-tree test or policy
baseline into packaged-release evidence.

Current repository facts that are safe to use as development context include:

- Product name **PaperWorkShell** and Windows-first, offline-first scope.
- Current project metadata version `0.0.0` with a pre-alpha classifier; this is not a release
  version or stable application identity.
- Implemented content-sniffed PDF/JPEG/PNG import, immutable case copies, SHA-256 source records,
  local restricted-worker processing, and single-PDF verified export.
- The implemented case convention
  `<current-user-profile>\AppData\Local\PaperWorkShell\Cases\<case-uuid>\`; exact packaged-release
  location and uninstall behavior still require M8 verification.
- Resource policy version 1 / `constrained-v1`, which is bounded software evidence rather than
  physical-hardware certification.
- A public synthetic fixture corpus and passed M0/M1 gates. These are candidate release-evidence
  inputs, not the frozen M8 sample/evidence package.
- A structured diagnostic baseline; the previewable support bundle remains owned by PWS-0805.

There is currently no installer, signed release package, stable application identifier, final
publisher/licence decision, packaged file-opening contract, project-transfer format, support
bundle, or completed M8 evidence package. Unsupported surfaces must be recorded honestly rather
than added solely for future portfolio convenience.

