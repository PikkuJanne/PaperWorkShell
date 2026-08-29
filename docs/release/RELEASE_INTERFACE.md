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

## 1. Product identity

| Item | Release value |
|---|---|
| Product name | PaperWorkShell |
| Reverse-domain application ID | **TBD — local frozen decision required** |
| Product version | **TBD for exact release** |
| Release channel | **TBD — Development, Preview, or Stable** |
| Publisher | **TBD** |
| Product licence | **TBD after final distribution review** |
| Supported operating systems | Windows-first; exact editions/builds **TBD** |
| Supported architectures | **TBD** |
| Portfolio dependency | None; PaperWorkShell must run without ThirdLife or sibling applications |

The development dependency inventory targets Windows x86-64 artifacts where currently required,
but that does not by itself freeze the supported release architecture or operating-system matrix.

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

The checked-in dependency inventory is a policy baseline, not the final release SBOM. The release
artifact does not exist yet.

## 3. Install, update, repair, and remove

| Item | Release value |
|---|---|
| Normal installation path or UI | **TBD** |
| Privilege/elevation requirement | **TBD; elevation must be limited to the selected operation** |
| Supported silent installation options | **TBD or not supported with reason** |
| Default install location | **TBD** |
| Restart requirement | **TBD** |
| Update mechanism | **TBD; core document work remains usable offline** |
| Repair behavior | **TBD or not supported with reason** |
| Rollback behavior and limits | **TBD** |
| Normal uninstall path or UI | **TBD** |
| User choice about cases during uninstall | **TBD; separate explicit choice required** |
| Data remaining after uninstall | **TBD by category** |
| Machine-wide services or scheduled tasks | None unless independently necessary and separately approved; **verify candidate** |

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
| Independently useful CLI options | **Not currently supported beyond development launch entry points** |
| ThirdLife-specific CLI/URI/RPC | **Not supported** |

The source tree has a development console entry point used to start the interactive application.
That is not a packaged CLI contract. Opening a file or workspace must never bypass visible consent,
silently upload content, start hidden destructive processing, or transfer data ownership.

## 5. Data locations and retention

Exact release paths and every retention/deletion rule must be verified on the packaged candidate.
Public documentation uses known-folder notation and never contains a real username or private
machine path.

| Data category | Current contract / release value | Retention and deletion behavior |
|---|---|---|
| Application configuration | **TBD** | **TBD** |
| Case/workspace roots | Implemented convention: `<current-user-profile>\AppData\Local\PaperWorkShell\Cases\<case-uuid>\`; **verify candidate** | Explicit case deletion exists; retention and uninstall policy **TBD** |
| Immutable imported originals | Random `.bin` objects under a case's `originals\` directory | Preserved unless the user explicitly deletes the case under product policy |
| Authoritative case records | `manifest.json`, `journal\`, and `export-history.json` inside the authenticated case | Atomic/recoverable behavior is implemented; release migration/retention **TBD** |
| Derived final outputs | User-selected external destination for supported export | Product never overwrites an imported original; exact release behavior **TBD** |
| Thumbnail/preview caches | Case-scoped `cache\thumbnails\` and `cache\previews\` | Disposable and bounded; packaged cleanup behavior **TBD** |
| Temporary processing data | Case-scoped `temp\` plus restricted worker-private temporary storage | Authenticated cleanup/recovery is implemented; packaged residue tests **TBD** |
| OCR language data / optional engines | Source contract: built-in English plus exact optional German `tessdata_fast` 4.1.0 data; exact packaged path/inclusion **TBD for PWS-0801/PWS-0802** | Close/restart after a local pack change; exact installer/elevation/removal/notices **TBD for PWS-0802** |
| Persistent logs | **TBD; current diagnostic logger is bounded and structured rather than a release log store** | Sanitized; retention **TBD** |
| Support bundles | User-selected local destination; feature **not yet implemented** | Created only after preview and confirmation; user-controlled deletion |
| Migration/rollback backups | **TBD** | **TBD** |

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
| Human-readable package summary | Planned M4 accessible output | **TBD** |
| Separate printable checklist artifact | No independently frozen output contract | **TBD or not supported with reason** |
| Optional technical manifest | Planned only when documented and safe | **TBD** |
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
| Optional downloads | OCR language packs are never downloaded by the application or worker; update/other component behavior **TBD** |
| Data transmitted by optional features | **TBD; disclose before transmission** |
| Offline installation path | PWS-0205 supports verified local prestaging only; exact release path, package UI, and elevation **TBD for PWS-0802** |
| Interrupted download behavior | Not applicable to OCR language discovery or processing because those perform no download; **TBD where later update downloads exist** |

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
| Update provenance | **TBD** |
| Secrets handling | No secrets or sensitive document content in arguments, routine logs, crash reports, or support bundles |
| Telemetry | Off by default |
| Original preservation | Immutable imported copy and hash; candidate verification **TBD** |
| Known security limitations | **TBD** |
| Security reporting route | **TBD** |

## 11. Support bundle

The current repository implements a structured sanitized diagnostic baseline, not a support bundle.
PWS-0805 owns the feature below.

| Item | Release value |
|---|---|
| Creation path | **TBD** |
| User preview before creation | Required |
| Default destination | **TBD; user-selected local path preferred** |
| Included categories | Approved versions, operation IDs/codes, numeric counts, resource data, and sanitized diagnostics; exact allow-list **TBD** |
| Excluded | Document bytes/text, OCR text, form values, names, addresses, filenames, full paths, metadata values, thumbnails, signatures, and credentials; any future content-sample workflow requires a separate decision and approval |
| Cancellation/failure behavior | **TBD and tested** |
| Sample sanitized bundle | **TBD; publish hash in release evidence** |

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

## 14. Release contact and maintenance

| Item | Release value |
|---|---|
| Issue/reporting route | **TBD** |
| Security reporting route | **TBD** |
| Maintained release versions | **TBD** |
| Maintenance status | **TBD — Development, Preview, Stable, or bounded maintenance** |
| End-of-support policy | **TBD** |
| Mandatory user account for support | None implied or required by this sheet |

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
