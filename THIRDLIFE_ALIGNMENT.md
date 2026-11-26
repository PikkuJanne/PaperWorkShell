# PaperWorkShell ThirdLife portfolio alignment

| Field | Value |
|---|---|
| Status | Inherited portfolio constraint; PaperWorkShell's local authority order remains controlling |
| Portfolio | ThirdLife Software Portfolio |
| Portfolio source | *ThirdLife Software Portfolio — Two-Team Development Roadmap and Boundary Architecture*, version 2.0, dated 2026-08-14 |
| Product / queue | PaperWorkShell / Team A, A1 |
| Integration model | Standalone first; late-binding black-box assembly |
| Future assembly owner | ThirdLife Deployment and Suite Assembly, Team B, B4 |

## Authority and provenance

This document records maintainer-supplied portfolio steering. Current owner direction,
the approved requirements in [DECISIONS.md](DECISIONS.md) and [ROADMAP.md](ROADMAP.md),
and the operating policy in [AGENTS.md](AGENTS.md) govern local work. Use the product
decision change procedure for changes to PaperWorkShell's standalone boundary.


The binding local mirror is `PWS-D042`, adopted through
[`docs/adr/0008-thirdlife-late-binding-portfolio.md`](docs/adr/0008-thirdlife-late-binding-portfolio.md).
This document supplies context and traceability; it does not create a second authority lane.

The supplied steering bundle was version 1.0.0. Its manifest listed eight source files; all listed
byte sizes and SHA-256 values were verified against the files supplied on 2026-08-14. The bundle
manifest itself had SHA-256
`d6bf62f3a382166546b446d1aa9a8d4ab81bfc24f1c4f42fd170d3fbe65f563a`.
The portfolio roadmap named above is not stored in this repository, so its cited page and section
references are supplier-provided provenance, not an independently reproduced repository check.
See [source traceability](docs/portfolio/SOURCE_TRACEABILITY.md).

## Purpose

PaperWorkShell must be ready for possible later inclusion in ThirdLife without acquiring a live
ThirdLife dependency. Integration readiness means ordinary standalone product quality: a stable
identity, predictable installation and removal, documented local data ownership, ordinary inputs
and portable outputs, normal launch behavior, sanitized support evidence, synthetic release
samples, and release documentation tied to exact frozen artifacts.

This is not an app-to-app API specification. It does not claim that a ThirdLife compatibility cut
exists, and it does not authorize PaperWorkShell-specific work in another repository.

The current PaperWorkShell application target is **Windows 11 x64 only**, selected
locally by [PWS-D043 / ADR 0019](docs/adr/0019-windows-11-x64-only-scope.md).
Native ARM64 and x64 emulation on ARM64 are outside project scope and are not
PWS-0802, PWS-0803 or public-release prerequisites. No future mandatory ARM task
or sibling-platform dependency follows from portfolio assembly. The
[support boundary](docs/release/WINDOWS11_X64_SUPPORT.md),
[PWS-0802 qualification record](docs/development/pws0802-x64-continuation-20261104.md) and
[finite x64 matrix](docs/development/pws0802-x64-qualification-20261104.md) keep
edition/build and scenario evidence separate from this settled scope choice.

## Boundary charter

### Product goal

PaperWorkShell turns supported PDFs, JPEGs, PNGs, scans, and photographs into an understandable,
privacy-conscious application package. The supported-format boundary remains the one frozen in
`DECISIONS.md`; “supporting documents” does not imply arbitrary file-format support.

### PaperWorkShell owns

- Supported document intake and immutable-source preservation.
- Cleanup and local OCR orchestration.
- Page and document ordering and non-destructive operations.
- Visual marks and text overlays.
- Permanent redaction only after its separate security gate.
- Compression and submission-constraint checking.
- Naming, packaging, checklists, and package summaries within locally approved scope.
- Case/workspace recovery, configuration, caches, temporary processing data, sanitized logs, and
  PaperWorkShell support evidence.
- PaperWorkShell migrations and the exact outputs or project-transfer formats that the product
  independently chooses to support.

### PaperWorkShell does not own

- Resume or cover-letter authoring.
- Job-application records and deadline tracking.
- Audio/video transcription or scam-legitimacy decisions.
- Device assessment, software management, or organizational cybersecurity assessment.
- Backup repositories or recovery-key custody.
- Official portal submission.
- Legal advice, eligibility decisions, identity services, or acceptance guarantees.
- ThirdLife profiles, catalogue metadata, compatibility matrices, or adapter maintenance.

A change to the **Owns** or **Does not own** boundary is a portfolio decision and a local
decision-change matter, not an incidental implementation-task edit.

### Primary PaperWorkShell data

PaperWorkShell data includes user-selected sources, immutable imported copies and hashes, derived
pages and previews, temporary processing artifacts, case metadata and operations, overlays,
checklist assignments, constraints and confirmations, final supported exports, configuration,
sanitized diagnostics, and user-authorized support bundles. Optional project exports are included
only if PaperWorkShell later approves and implements them for independent product value.

### Standalone condition

PaperWorkShell's complete core workflow must work locally without ThirdLife, a sibling application,
a mandatory account, or a project-controlled server. It must remain useful when ThirdLife is
absent, disabled, incompatible, or removed. PaperWorkShell's own release gates continue to govern
hostile input, privacy, accessibility, recovery, licensing, resource budgets, packaging, and
uninstall behavior.

The v0.1 pilot remains the Milestone 4 release defined by `ROADMAP.md`. The complete ThirdLife
black-box release envelope is an M8 packaged-candidate concern and does not retroactively add
installer/update requirements to the v0.1 pilot gate.

## Lifecycle contact model

| Lifecycle point | PaperWorkShell obligation | Permitted ThirdLife posture |
|---|---|---|
| Active development | Build and test the standalone product | No product-specific integration work or blocking dependency |
| Preview artifact | Publish only real draft interface/evidence and synthetic samples | Review public artifacts or run an isolated experiment; no suite-support claim |
| Stable release | Freeze version, artifacts, hashes, interface, evidence, and limitations | Consider scheduling a future B4 compatibility cut |
| Future B4 assembly | Support defects through normal project channels | Test documented black-box actions against exact frozen artifacts and own any adapter |
| Maintenance | Fix security, privacy, corruption, or data-loss defects under product policy | Narrow or disable an adapter before demanding product redesign |

PaperWorkShell versions and releases independently. A stable PaperWorkShell release is not delayed
because ThirdLife has not adopted it, and a newer PaperWorkShell release is unverified for a future
suite until B4 tests that exact version or range.

## Minimal black-box release envelope

An M8 packaged preview or stable candidate carries the following evidence. Publication
is separately authorized. PWS-D044 allows PaperWorkShell to complete on an exact unsigned
candidate and leaves any future B4 compatibility claim to ThirdLife.

- Stable product identity, application identifier, version, channel, publisher, licence, Windows 11
  x64-only scope, and evidenced editions/builds.
- Installer/package identity, size, hash, observed signing state, verification method, installation,
  replacement, repair and removal behavior, privilege and restart behavior, and exact uninstall residue.
- Documented locations, retention, permissions, migration, and deletion behavior for configuration,
  cases, originals, derivatives, caches, temporary files, optional engines/data, logs, support
  bundles, and migration backups.
- Supported inputs and outputs plus copy/reference/convert/flatten/reconstruct semantics.
- Normal interactive launch and independently justified file/workspace opening, associations, or
  command-line behavior. Unsupported surfaces are stated as unsupported rather than invented.
- A local, opt-in, previewable, sanitized support bundle and known limitations.
- Public synthetic sample inputs and, where independently supported, workspaces, expected outputs,
  summaries, behavior notes, and hashes.
- A completed [release interface](docs/release/RELEASE_INTERFACE.md) and current
  [release evidence index](docs/release/RELEASE_EVIDENCE.md) frozen with the artifacts.

The release checklist is [kept separately](docs/release/STANDALONE_RELEASE_CHECKLIST.md). Adding
draft templates does not complete an M8 task or release gate.

## Permitted future integration

Future integration methods are considered in this order:

1. Install the application through its documented product mechanism.
2. Launch it normally.
3. Open an explicit user-selected supported file through documented visible behavior, if the
   product independently supports that behavior.
4. Open a documented workspace or project export without ThirdLife parsing private contents, if
   PaperWorkShell independently supports such a format.
5. Show human guidance and links.
6. Add a narrow custom adapter only when evidence proves that the earlier methods leave a frequent,
   high-value journey materially difficult.

Any future adapter must be ThirdLife-owned, optional, removable, version-bounded, tested against
exact frozen artifacts, explicit about files/settings/privileges, and backed by a complete manual
fallback.

PaperWorkShell currently exposes no ThirdLife-specific CLI, URI scheme, RPC command, project
handoff schema, file association, or operating-system open contract. Those surfaces remain
unsupported until a PaperWorkShell-local use, decision, implementation, and test evidence justify
them. Future observations belong in
[the non-gating notes](docs/portfolio/THIRDLIFE_FUTURE_NOTES.md), not in active task dependencies.

## Prohibited coupling

Standalone PaperWorkShell development must not create:

- A shared user-content database, workspace, runtime schema, SDK, plugin framework, universal job
  engine, handoff package, or portfolio background service.
- A cross-repository source dependency, copied sibling package, synchronized branch plan, shared
  sprint, or release gate that requires a sibling build or private fixture.
- Direct calls to an unfinished ThirdLife or sibling application.
- Live integration tests against active ThirdLife branches.
- A ThirdLife-specific CLI, URI, RPC command, manifest field, file association, or export whose only
  justification is hypothetical suite use.
- Hidden automation of import, OCR, compression, export, redaction, migration, or deletion.
- ThirdLife inspection of PaperWorkShell cases, manifests for content discovery, documents, OCR
  text, overlays, signatures, thumbnails, redaction state, or routine logs.
- A requirement to adopt or share ThirdLife's programming language, UI toolkit, data model, or
  technology stack.
- Synchronized PaperWorkShell and ThirdLife version numbers or release dates.

ThirdLife may later store its own catalogue, profile, installation, compatibility, and support
metadata. It does not thereby own PaperWorkShell documents or domain records. Uninstalling
ThirdLife must not remove PaperWorkShell cases; PaperWorkShell's own uninstall workflow must make
separate user-data choices explicit.

## Unresolved PaperWorkShell choices

Portfolio steering does not choose PaperWorkShell's reverse-domain application ID, publisher,
final product licence, supported Windows editions/builds, installer technology,
optional update channel, exact packaged data paths and retention defaults, file-association policy,
independently useful CLI options, project export format, hardware claims, release contact, or
supported-version policy. The maintainer has now selected Janne Vuorela, scoped MIT/free
distribution and the stable application/upgrade IDs in
[ADR 0018](docs/adr/0018-windows-installer-and-release-identity.md), and Windows 11
x64-only scope in ADR 0019. PWS-D044 determines eligibility for product completion.
Other release facts remain `TBD` until their owning local tasks produce evidence.
Existing engineering installers retain their actual scope and internal status.

The immutable R7 engineering installer is 512,713,704 bytes, SHA-256
`a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a`,
actual signature status **NotSigned**. The newer scope documents do not replace
its embedded build metadata or original R6/R7 evidence. Actual engineering guest
evidence names Enterprise 26100.9711 x64/genuine standard user; Pro 26300.9711 is
host-only. Neither a broader supported-build matrix nor public signed release
approval is inferred, and no ThirdLife compatibility claim is created.
