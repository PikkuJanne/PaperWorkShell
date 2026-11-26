# PaperWorkShell release interface sheet

| Field | Current value |
|---|---|
| Document status | **DRAFT TEMPLATE — not a release guarantee and not an application API** |
| Interface revision | `0-draft` |
| Product | PaperWorkShell |
| Portfolio role | ThirdLife Team A / A1 standalone product |
| Intended completion | Packaged M8 preview and stable candidates |
| Version relationship | A completed sheet is tied to exactly one PaperWorkShell candidate or release |

## How to use this sheet

This is the minimal human-readable black-box interface for deployers, support workers, and a
possible future ThirdLife Deployment and Suite Assembly compatibility cut. It documents behavior a
standalone desktop product exposes. It is not a shared runtime schema, a custom adapter contract,
or permission to inspect PaperWorkShell private data.

Every release-specific `TBD` must be completed from the exact packaged candidate. Use “not
supported” with a reason when an interface does not exist. Do not add a file-opening command,
workspace transfer, association, CLI, URI, or RPC only to fill this sheet. See the
[portfolio boundary](../../THIRDLIFE_ALIGNMENT.md) and [evidence index](RELEASE_EVIDENCE.md).

Current source-tree facts appear only as clearly labeled context. Project metadata `0.0.0` is a
pre-alpha development value, not the product version for this sheet.

For the current workflow and its limits, see the [user guide](../user/USER_GUIDE.md),
[care and recovery guide](../user/CARE_AND_RECOVERY.md), and [security policy](../../SECURITY.md).

## 1. Product identity

| Item | Release value |
|---|---|
| Product name | PaperWorkShell |
| Reverse-domain application ID | `io.github.pikkujanne.paperworkshell`; selected in ADR 0018 |
| Product version | **TBD for exact release** |
| Release channel | **TBD — Development, Preview, or Stable** |
| Publisher | Janne Vuorela |
| Product licence | MIT for [authorized project-owned material](../../LICENSING.md); third-party terms preserved; official distribution free including commercial use |
| Supported operating systems | Windows 11 only; supported release editions/builds require the [x64 support evidence](WINDOWS11_X64_SUPPORT.md) |
| Supported architectures | x86-64 (x64) only under PWS-D043; ARM64 native and emulated application routes are outside project scope |
| Portfolio dependency | None; PaperWorkShell must run without ThirdLife or sibling applications |

The architecture is selected by [PWS-D043 / ADR 0019](../adr/0019-windows-11-x64-only-scope.md),
not inferred from dependency availability. No ARM environment, native/emulated result or
future ARM task is a prerequisite for PWS-0802, PWS-0803 or public release. The
[PWS-0802 qualification record](../development/pws0802-x64-continuation-20261104.md) and
[finite x64 matrix](../development/pws0802-x64-qualification-20261104.md) distinguish
the settled target from still-unproved edition/build and scenario coverage.

## 2. Release artifact

| Item | Release value |
|---|---|
| Installer/package filename | **TBD** |
| Package format | **TBD** |
| Package size | **TBD** |
| SHA-256 | **TBD** |
| Signature or verification method | **TBD** |
| Source revision/tag | **TBD** |
| Dependency lock/SBOM reference | **TBD** |
| Download source | **TBD** |
| Offline-media source | **TBD or not supported with reason** |
| Required external runtime | **TBD; release must not require development tools** |

The checked-in dependency inventory is a policy baseline, not the final release SBOM. An
[unsigned internal engineering installer](../development/pws0802-community-runtime-20261103.md)
now exists with exact inventory and SBOMs. It is not the qualified signed release represented
by the still-unproved release fields in this sheet. Its stable Inno upgrade identity is
`{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}`. No account, registration, email, activation,
subscription or licence key is required; application document uploads, telemetry and automatic
crash-report submission are prohibited.

The preserved engineering R7 build includes the acquired Community 2022 app-local Microsoft
runtime and offline component-only recipient agreement; it does not require a
separately installed VC runtime. The current report separates actual native origin
and workflow observations from the current Sandbox standard-account
checks and the remaining Windows 11 x64 matrix and trusted-signing requirements. These engineering facts do not fill the public
release fields above or create a supported-build claim.

Its exact unsigned installer is 512,713,704 bytes, SHA-256
`a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a`,
with actual signature status **NotSigned**. The newer x64-only source metadata does
not replace its authenticated embedded build metadata, inventory or audit; the
installer is unchanged. Actual engineering target evidence names Windows 11
Enterprise 26100.9711 x64 and a genuine standard account. Pro 26300.9711 is a host
observation, not another qualified guest. R7 used the existing instrumented guest;
R6 clean offline document evidence is reused only within the audited unchanged
runtime/worker/export boundary. Public signed artifact/version/hash/signer fields
remain unproved.

## 3. Install, update, repair, and remove

| Item | Release value |
|---|---|
| Normal installation path or UI | **TBD** |
| Privilege/elevation requirement | **TBD; elevation must be limited to the selected operation** |
| Supported silent installation options | **TBD or not supported with reason** |
| Default install location | **TBD** |
| Restart requirement | **TBD** |
| Update mechanism | Development implementation: the **PaperWorkShell Updates** Start menu shortcut verifies a local signed update folder and waits for explicit confirmation. Public bootstrap, trust and distribution **TBD**. |
| Repair behavior | **TBD or not supported with reason** |
| Rollback behavior and limits | Development implementation: **Restore previous version** selects a verified retained runtime or the original installation. The accepted update sequence stays in force. Public candidate result **TBD**. |
| Normal uninstall path or UI | **TBD** |
| User choice about cases during uninstall | **TBD; separate explicit choice required** |
| Data remaining after uninstall | **TBD by category** |
| Machine-wide services or scheduled tasks | None unless independently necessary and separately approved; **verify candidate** |

The exact TRACE B8 engineering package supplies standard-user same-build repair and reuse
evidence. PWS-0803 tested offline activation, an ordinary PDF export, previous-runtime restoration
and replay refusal using an unsigned engineering bootstrap and an isolated test-signed successor.
See the separate [TRACE B8 setup record](../development/pws0814-automated-setup.md) and
[offline update record](../development/pws0803-authenticated-updates.md).

The update-capable installer and runtime share a per-user lifecycle lock. Close PaperWorkShell
before opening Updates. Local update packages must use the installed case schema and Microsoft
runtime terms. A legacy installation needs the case-preserving replacement route to acquire the
update-capable launcher. Before uninstalling an updated installation, use **Remove installed
updates**, then make the separate case-data choice during removal. See the
[care and recovery guide](../user/CARE_AND_RECOVERY.md) and
[update design](../adr/0022-authenticated-application-updates.md).

ThirdLife may later invoke documented product mechanisms. It may not replace PaperWorkShell's
installer, updater, migration, or uninstall logic.

## 4. Interactive launch and opening behavior

| Item | Release value |
|---|---|
| Executable or normal launch path | **TBD** |
| Start-menu/application-menu identity | **TBD** |
| Normal interactive launch | Intended; exact packaged behavior **TBD** |
| Supported file associations | **Not currently supported; any future policy requires independent product value, user control, and uninstall tests** |
| Open user-selected PDF through OS launch | **Not currently supported; import through the visible PaperWorkShell UI is the present path** |
| Open user-selected JPEG/PNG through OS launch | **Not currently supported; import through the visible PaperWorkShell UI is the present path** |
| Open portable PaperWorkShell workspace/project | **Not currently supported; no portable project-transfer format is frozen** |
| Multiple-file operating-system opening | **Not currently supported** |
| Independently useful CLI options | TRACE supports bounded local recipe replay for ordinary PDF export. Exact public candidate scope and evidence **TBD**. |
| ThirdLife-specific CLI/URI/RPC | **Not supported** |

The local recipe route takes an explicitly selected source and replays supported page selection,
order and quarter-turn rotations. See the [TRACE implementation and scoped installed evidence](../development/pws0813-trace.md).

## 5. Data locations and retention

Exact release paths and every retention/deletion rule must be verified on the packaged candidate.
Public documentation uses known-folder notation and never contains a real username or private
machine path.

The [care and recovery guide](../user/CARE_AND_RECOVERY.md) records current location classes,
retention, deletion and uninstall behavior by category. Exact public candidate paths, permissions,
cleanup and uninstall residue remain **TBD**. Record verified values for each category and link the
matching [data-location evidence](RELEASE_EVIDENCE.md#4-data-location-and-preservation-evidence).

PaperWorkShell owns responsibility for its configuration and workspace formats, lifecycle, and
migrations; users retain control of their local configuration and workspace content. ThirdLife may
hold only its own catalogue, installation, compatibility, and support metadata. It must not read or
index PaperWorkShell documents, OCR text, overlays, signatures, thumbnails, redaction state, or
private case content.

## 6. Inputs

The product boundary currently admits PDF, JPEG, and PNG by content rather than filename extension.
The exact packaged release status still requires candidate testing.

| Input | Product boundary | Exact release status |
|---|---|---|
| PDF | Core supported input; selected source is copied, hashed, and preserved | **TBD after packaged testing** |
| JPEG | Core supported input; selected source is copied, hashed, and preserved | **TBD after packaged testing** |
| PNG | Core supported input; selected source is copied, hashed, and preserved | **TBD after packaged testing** |
| Portable PaperWorkShell project/workspace import | No frozen portable format | **Not currently supported** |
| Password-protected PDF | Exact policy depends on supported inspection/workflow | **TBD or unsupported with reason** |
| HEIC, TIFF, DOCX, ODT, email import, scanner protocol | Outside the initial frozen boundary | **Unsupported unless separately approved** |

Import never opens the external source for writing. PaperWorkShell creates a random case copy,
computes SHA-256, and records non-destructive intent against that copy.

## 7. Outputs and export

| Output | Product boundary | Exact release status |
|---|---|---|
| One combined PDF | Supported product output; a verified single-PDF path is implemented | **TBD after packaged testing** |
| Several named PDFs | Supported product output; source-level deterministic planning, verification, and per-artifact no-replace publication are implemented | **TBD after packaged testing** |
| ZIP package | Supported product output; source-level safe ordered ZIP creation, reopen verification, and single-artifact no-replace publication are implemented | **TBD after packaged testing** |
| Human-readable package summary | Source-tree plain-text review/result and explicit `.txt` companion are implemented; PWS-0408 was accepted and the [PWS-0409 source-tree pilot gate](../gates/m4-pilot-gate.md) passed on 2026-09-30 | **TBD after packaged testing** |
| Separate printable checklist artifact | No independently frozen output contract | **TBD or not supported with reason** |
| Optional technical manifest | Not implemented in PWS-0408; no machine-readable output contract is frozen | **TBD or not supported with reason** |
| Portable project/migration export | No independently frozen format | **Not currently supported** |
| Direct portal submission | Outside product boundary | **Not supported** |

The completed release sheet must state whether each output copies, converts, flattens,
reconstructs, or references material. External originals and prior verified outputs are never
silently replaced.

## 8. Offline and network behavior

| Item | Release value |
|---|---|
| Core case creation, import, organize, check, and export | Must work offline; **verify exact release** |
| Mandatory account | None |
| Project-controlled server required for core workflow | None |
| Worker network access during document processing | Denied by product design and current Windows tests; **verify packaged release** |
| Optional downloads | The app discovers local OCR language packs. The offline updater reads a local folder and has no automatic check. Any public artifact acquisition route is **TBD**. |
| Data transmitted by optional features | The implemented updater and support-bundle creation operate locally. Exact public candidate verification **TBD**. |
| Offline installation path | TRACE B8 exercised the prepared per-user setup route. Exact public release media, package UI and privileges **TBD**. |
| Interrupted download behavior | In-app download is unsupported because the OCR pack and update routes use local files. Public acquisition and recovery instructions **TBD**. |

## 9. Resource behavior

| Item | Release value |
|---|---|
| GPU requirement | No discrete GPU required by policy; final CPU-path evidence **TBD** |
| Minimum practical CPU/RAM/storage claim | **TBD only after measured evidence** |
| Low-resource/constrained profile | Current source policy: version 1 / `constrained-v1`; packaged evidence **TBD** |
| Expected temporary space | **TBD by representative packaged workload** |
| Large-input warnings | **TBD** |
| Concurrency behavior | Conservative and bounded; exact candidate defaults **TBD** |
| Benchmark evidence | See [release evidence](RELEASE_EVIDENCE.md) |

Constrained-profile evidence proves bounded software behavior for the named revision and workload.
It does not certify a named physical computer, CPU count, RAM amount, GPU, SSD, HDD, or VM.

## 10. Privilege, security, and privacy

| Item | Release value |
|---|---|
| Normal application privilege | Designed for the current standard user; **verify candidate** |
| Operations requiring elevation | Installer/update/repair/removal operations only as documented; **TBD** |
| Untrusted inputs | PDF, JPEG, PNG, templates, and any future project/engine package as applicable |
| Document processing isolation | Restricted worker; exact packaged controls **TBD** |
| Network during processing | Denied; **verify candidate** |
| Update provenance | The offline updater verifies channel authority, signed release metadata, freshness and exact package bytes against a provisioned trust root. Production trust and exact release checks **TBD**. |
| Secrets handling | No secrets or sensitive document content in arguments, routine logs, crash reports, or support bundles |
| Telemetry | Off by default |
| Original preservation | Immutable imported copy and hash; candidate verification **TBD** |
| Known security limitations | **TBD** |
| Security reporting route | **TBD** |

## 11. Support bundle

PWS-0805 implements a local, previewable support bundle with a closed allowlist. The
[source contract](../architecture/support-bundle.md) and
[synthetic sample](../../tests/fixtures/diagnostics/v1/README.md) specify exact bytes;
release-grade packaged qualification remains separate.

| Item | Release value |
|---|---|
| Creation path | TRACE offers **Create support bundle…** in **Settings** and **Help**. Exact public release verification pending. |
| User preview before creation | Required |
| Default destination | Explicit user-selected local file; no automatic submission |
| Included categories | Exact preview of `summary.txt` and `allowlist.txt`; numeric versions by default, other closed typed diagnostic fields only when actually recorded; no file collector |
| Excluded | Document bytes/text, OCR text, form values, names, addresses, filenames, full paths, metadata values, thumbnails, signatures, and credentials; any future content-sample workflow requires a separate decision and approval |
| Cancellation/failure behavior | Source-tested cancellation, no-replace publication, low/disk-full and partial-write cleanup; packaged release coverage pending |
| Sample sanitized bundle | [Synthetic archive and exact allowlist](../../tests/fixtures/diagnostics/v1/README.md); SHA-256 `4f5bb7b36f1b58f36901f31c18b721a6875a44bc731fb8da3779e39b77662304` |

PaperWorkShell and ThirdLife support artifacts remain separate.

## 12. Sample artifacts

The repository's public synthetic fixture index is a development candidate source. The release
sample package is not frozen yet.

| Item | Release value |
|---|---|
| Synthetic sample input set | Candidate source: `tests/fixtures/synthetic/v1/index.json`; frozen subset **TBD** |
| Sample portable workspace/project | **Not currently supported** |
| Expected PDF/ZIP/summary outputs | **TBD** |
| Expected behavior notes | **TBD** |
| SHA-256 manifest | **TBD** |
| Licence/provenance of samples | Repository fixtures are algorithmically synthetic; release review **TBD** |

No sample may contain real applicant, identity, financial, medical, employment, signature, or
beneficiary data.

## 13. Known limitations

Product-level limitations already fixed by local authority include:

- PaperWorkShell is a guided document-package workflow, not a general PDF editor.
- It does not author resumes/cover letters, transcribe media, assess scams, configure devices,
  manage software, own backups, submit documents, give legal advice, decide eligibility, or
  guarantee acceptance.
- A placed signature image is not a cryptographic or qualified electronic signature.
- Permanent redaction is available only after its independent security gate; visual covering is
  never represented as permanent removal.
- The initial input boundary excludes HEIC, TIFF, DOCX, ODT, email import, and scanner protocols.

Exact unsupported behavior, migrations, rollback constraints, accessibility limits, resource
limits, and untested environments remain **TBD for each release**.
See the [current known limitations](../../KNOWN_LIMITATIONS.md).

## 14. Release contact and maintenance

| Item | Release value |
|---|---|
| Issue/reporting route | **TBD** |
| Security reporting route | **TBD** |
| Maintained release versions | **TBD** |
| Maintenance status | **TBD — Development, Preview, Stable, or bounded maintenance** |
| End-of-support policy | **TBD** |
| Mandatory user account for support | None implied or required by this sheet |

The [security policy](../../SECURITY.md) describes how to prepare a report that protects case
contents. Publication and exact release support commitments require the applicable release evidence.

## 15. ThirdLife compatibility interpretation

A future ThirdLife release may claim support only for an exact PaperWorkShell version or tested
range in its own compatibility matrix. Permitted shallow actions are documented installation,
update/removal, normal launch, optional user-controlled associations, explicit user-selected file
or supported workspace opening, and guidance. This sheet never authorizes ThirdLife to read cases,
index documents, control hidden processing, change originals, or perform PaperWorkShell migrations.

## 16. Completion checklist

- [ ] Exact product version, source revision, channel, and interface revision are recorded.
- [ ] Every release-specific `TBD` is completed or marked “not supported” with a reason.
- [ ] Package identity, size, hash, signature, SBOM, notices, and source revision are recorded.
- [ ] Install/update/repair/removal, privilege, restart, rollback, and residual-data behavior are documented.
- [ ] Launch, supported opening, associations, and CLI behavior are documented without inventing an interface.
- [ ] Data locations and retention/deletion behaviors are documented by category.
- [ ] Inputs/outputs and copy/reference/convert/flatten/reconstruct semantics are documented.
- [ ] Offline, optional network, and measured resource behavior are documented honestly.
- [ ] Security/privacy limits, support bundle, samples, limitations, and contacts are complete.
- [ ] [Release evidence](RELEASE_EVIDENCE.md) points to the exact packaged candidate.
- [ ] The sheet contains no private usernames, private or real-machine absolute paths, credentials, or user data; known-folder notation and repository-relative or public artifact references remain allowed.
- [ ] The completed sheet is frozen with the release artifacts.
