# PaperWorkShell release interface sheet

| Field | Current value |
|---|---|
| Document status | **DRAFT TEMPLATE — not a release guarantee and not an application API** |
| Interface revision | `0-draft` |
| Engineering sheet revision | `pws0807-r1-interface-1`, for the identified unsigned internal candidate |
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

This sheet describes `pws0807-diagnostics-engineering-r1`. Its installed marker reports version `0.0.0` and channel `engineering`. The offline Windows 11 Pro x64 guest on build 26300.9711 supplied standard-user desktop evidence. Component and reader observations on Pro x64 26300.9974 have their own stated scope. The owner decides public designation and distribution separately. [R1 qualification](../development/pws0807-r1-qualification.md) · [Candidate matrix](../development/pws0807-rc-matrix.md)

For the current workflow and its limits, see the [user guide](../user/USER_GUIDE.md),
[care and recovery guide](../user/CARE_AND_RECOVERY.md), and [security policy](../../SECURITY.md).

## 1. Product identity

| Item | Release value |
|---|---|
| Product name | PaperWorkShell |
| Reverse-domain application ID | `io.github.pikkujanne.paperworkshell`; selected in ADR 0018 |
| Product version | `0.0.0` in the installed R1 marker. |
| Release channel | Internal `engineering`. |
| Publisher | Janne Vuorela |
| Product licence | MIT for [authorized project-owned material](../../LICENSING.md); third-party terms preserved; official distribution free including commercial use |
| Supported operating systems | Windows 11. Diagnostics R1 installer observations cover Pro x64 build 26300.9711, offline, with Smart App Control Off. [Windows support matrix](WINDOWS11_X64_SUPPORT.md) |
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
| Installer/package filename | `PaperWorkShell-pws0807-diagnostics-engineering-r1-win-x64-UNSIGNED.exe` |
| Package format | Native x64 Inno Setup executable, per-user installation |
| Package size | 513,022,419 bytes |
| SHA-256 | `a0d809322ea2c29066d26134c649f9ee1a883f4268403ea9c4e2d9e92bf8aaf8` |
| Observed PaperWorkShell publisher signing state | Authenticode reported `NotSigned` on the build host and in the clean guest. |
| Artifact verification method | Match the retained installer receipt and resolved inventory, then verify all 3,219 payload files against that inventory. [R1 artifact evidence](RELEASE_EVIDENCE.md#diagnostics-r1-installer) |
| Source revision | `e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec` |
| Dependency lock/SBOM reference | Source revision's `uv.lock`, resolved package inventory, and accompanying CycloneDX and SPDX SBOMs. [R1 artifact evidence](RELEASE_EVIDENCE.md#diagnostics-r1-installer) |
| Download source | No public download authorized. If publication is approved, record the actual delivery source. |
| Offline-media source | Use the verified internal prepared bundle supplied by the maintainer. A public media source requires a separate distribution decision. |
| Required external runtime | The standalone app and OCR runtimes ship with R1. The clean offline guest installed and launched R1 without a development environment. |

The resolved inventory has SHA-256 `8a9e3202d479c11e4e678adb0c25e31eab1cd1718f036187e500a7dc44566420`,
the installer receipt `875894759adf79cd580c69a81018ca06c8a734e52f0fbd1754d2ec51bbd3220f`,
and the installed runtime marker `c01268636ea37745e065516c82f892fc40fae418fdafaeae22cf814599bf6736`.
R1 includes an app-local Microsoft runtime and its reviewed recipient terms.
The installer audit reproduced both SBOMs and component notices. Earlier R6/R7/R9 results remain
with their original builds and environments. [Installer lineage](RELEASE_EVIDENCE.md#diagnostics-r1-installer)

## 3. Install, update, repair, and remove

| Item | Release value |
|---|---|
| Normal installation path or UI | Run `Setup-PaperWorkShell.exe` with the prepared R1 bundle and the receipt digest from the trusted build reference. [Per-user setup](../../packaging/README.md#automated-per-user-setup) |
| Privilege/elevation requirement | Clean install and launch ran in the signed-in standard user's desktop session. |
| Supported silent installation options | After reviewing the bundled Microsoft terms, the prepared launcher accepts `--accept-microsoft-terms <terms-sha256>` for unattended installation. [Setup options](../../packaging/README.md#automated-per-user-setup) |
| Default install location | `%LOCALAPPDATA%\Programs\PaperWorkShell`. The installer registers removal for the current user. |
| Restart requirement | The observed R1 install, repair and removal operations requested no restart. |
| Update mechanism | For local replacement, choose **Keep cases** during removal and install the prepared successor. B8-to-R1 replacement preserved all 353 baseline case files. R1 uses the local installer with the live update channel disabled. |
| Repair behavior | Close the app and run the matching prepared bundle with `--repair`. On R1 this restored a missing bundled recipient-terms file, verified all 3,219 files and reopened the app. [Repair evidence](RELEASE_EVIDENCE.md#3-install-launch-update-repair-and-uninstall-evidence) |
| Rollback behavior and limits | Same-build repair restored a missing payload file. During installer-target disk exhaustion the user canceled the partial installation, the installer rolled back, and the retained case and external files matched. Local replacement uses **Keep cases**. Previous-runtime restoration has separate isolated update-rehearsal evidence. [R1 installer recovery](../development/pws0807-unsigned-completion.md#pws-0802-installer-target-disk-exhaustion) |
| Normal uninstall path or UI | Open **Settings → Apps → Installed apps**, select PaperWorkShell and choose **Uninstall**. |
| User choice about cases during uninstall | Keeping cases is the default. To delete managed cases, choose review, inspect the inventory, give separate confirmation and acknowledge the reported result before continuing application removal. |
| Data remaining after uninstall | Preserve removal retained the case and its cached thumbnails, journal and immutable original. The zero-byte per-user lifecycle lock remained. The app root, per-user registration and three shortcuts were removed. |
| Machine-wide services or scheduled tasks | The desktop runs in the signed-in user session. R1 per-user installation requires no machine service or scheduled task for the documented workflows. |

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
| Executable or normal launch path | `%LOCALAPPDATA%\Programs\PaperWorkShell\runtime\PaperWorkShell.exe` |
| Start-menu/application-menu identity | **PaperWorkShell** in the current user's Start menu |
| Normal interactive launch | The installed shortcut opened a fresh responsive R1 desktop under the standard-user token. [Clean install and launch](../development/pws0807-unsigned-completion.md#pws-0802-clean-per-user-installation-and-launch) |
| Supported file associations | **Not currently supported; any future policy requires independent product value, user control, and uninstall tests** |
| Open user-selected PDF through OS launch | **Not currently supported; import through the visible PaperWorkShell UI is the present path** |
| Open user-selected JPEG/PNG through OS launch | **Not currently supported; import through the visible PaperWorkShell UI is the present path** |
| Open portable PaperWorkShell workspace/project | **Not currently supported; no portable project-transfer format is frozen** |
| Multiple-file operating-system opening | **Not currently supported** |
| Local PowerShell recipe | In **Recipe**, choose **Export PowerShell script** for ordinary PDF selection, ordering and rotation. The script uses the installed offline runtime and requires a fresh output path. [User guide](../user/USER_GUIDE.md) |
| ThirdLife-specific CLI/URI/RPC | **Not supported** |

Passing PDF, JPEG, PNG or a workspace path to the exact native launcher displayed `LAUNCHER_ARGUMENT_INVALID` and directed the operator to the visible import controls. The four selected Windows file defaults survived removal. [R1 qualification](../development/pws0807-r1-qualification.md) · [TRACE recipe evidence](../development/pws0813-trace.md)

On the host, a script generated by the staged R1 runtime replayed the three synthetic PDF, JPEG and PNG inputs with the saved order and rotation. The resulting 22,413 bytes matched the three-page installed desktop export. Repeating the script with the same destination returned `PDF_EXPORT_DESTINATION_EXISTS` and preserved the first export. [R1 qualification](../development/pws0807-r1-qualification.md)

## 5. Data locations and retention

Cases hold immutable imported copies, a versioned manifest and journal, and rebuildable cache/temporary data. The user selects separate locations for exports, saved summaries and support archives. The retention table identifies configuration, optional engines, case history and recovery files by category. [Data locations and retention](../user/CARE_AND_RECOVERY.md)

| Data | Location and disposition |
|---|---|
| Original files and public outputs | Import reads the selected file at its existing path. The user chooses export and support destinations. These external files survive uninstall and case deletion. |
| Managed case and backups | Under `%LOCALAPPDATA%\PaperWorkShell\Cases`, each case holds `originals`, `manifest.json` and its `journal`. Migration source backups in the journal stay with the case. |
| Cached pages and temporary work | Case preview and thumbnail caches are rebuildable. The worker uses bounded temporary storage. The measured mixed workflow finished with zero case temporary bytes. |
| Engines, preferences and diagnostics | Bundled PDF/OCR engines and English OCR data live under the installed runtime. Case settings remain in managed case state. The support summary uses bounded in-session records until the user chooses a local save. |
| Local update state | Installed activation state and runtime slots belong under the product installation. The per-user lifecycle lock can remain after uninstall. R1 has no configured production trust anchor. |

On diagnostics R1, preserve removal kept the product-created synthetic case
under `%LOCALAPPDATA%\PaperWorkShell\Cases` and the zero-byte
`%LOCALAPPDATA%\PaperWorkShell.lifecycle-v1.lock`. The three external source,
export, and support-bundle files kept their hashes. The four checked PDF and
image extension defaults also matched before and after removal. In the explicit
deletion run the user confirmed one reviewed case. All its files were removed,
and the three external files kept their hashes.
[Care and recovery guide](../user/CARE_AND_RECOVERY.md) ·
[Installer lifecycle evidence](RELEASE_EVIDENCE.md#3-install-launch-update-repair-and-uninstall-evidence).

PaperWorkShell owns responsibility for its configuration and workspace formats, lifecycle, and
migrations; users retain control of their local configuration and workspace content. ThirdLife may
hold only its own catalogue, installation, compatibility, and support metadata. It must not read or
index PaperWorkShell documents, OCR text, overlays, signatures, thumbnails, redaction state, or
private case content.

## 6. Inputs

The product boundary currently admits PDF, JPEG, and PNG by content rather than filename extension.
**Add file** on the offline R1 standard-user desktop imported the three documented synthetic formats.

| Input | Product boundary | Exact release status |
|---|---|---|
| PDF | Copied into the case and hashed | One born-digital input produced one page. External, managed and manifest SHA-256 values matched through export. |
| JPEG | Copied, hashed and decoded for export | The EXIF-tagged JPEG produced one upright page. The independent verifier matched its RGB samples after orientation, and its original and managed hashes matched. |
| PNG | Copied, hashed and decoded for export | The scan produced one page. Its exported RGB samples and original and managed hashes matched the expected input. |
| Portable PaperWorkShell project/workspace import | No frozen portable format | **Not currently supported** |
| Password-protected PDF | Protection restricts processing | Outside this candidate’s qualified input scenarios. Permanent removal requires an unencrypted source. |
| HEIC, TIFF, DOCX, ODT, email import, scanner protocol | Outside the initial frozen boundary | **Unsupported unless separately approved** |

Import never opens the external source for writing. PaperWorkShell creates a random case copy,
computes SHA-256, and records non-destructive intent against that copy.

## 7. Outputs and export

| Output | Product boundary | Exact release status |
|---|---|---|
| One combined PDF | Assembles PDF pages, converts raster inputs, applies supported saved page operations and writes a new PDF | R1 exported the three-page mixed PDF and a separately reordered, rotated derivative after reopening the case. Independent inspection confirmed page order and content. |
| Several named PDFs | One verified derivative for each planned group | The installed four-file checklist export timed out and its destination was empty after cleanup. The staged R1 components separately produced three single-page reference PDFs. |
| ZIP package | Packages ordered PDF derivatives in a new archive | The installed four-member checklist ZIP attempt timed out and its destination was empty after cleanup. For the separate three-page component fixture, every ZIP member matched the corresponding standalone PDF. |
| Human-readable package summary | Shows the planned and verified counts, user reviews and findings. The user may save a text copy | The installed four-category combined export produced a 2,020-byte PDF. The 6,069-byte saved summary matched its displayed text. The sample contains both files. |
| Separate printable checklist artifact | Checklist review uses the case and its exported summary | Unsupported. Review the checklist in the app and use the package summary. |
| Optional technical manifest | User-facing exports are PDF, ZIP and text | Unsupported in R1. The synthetic sample inventory describes only its own example files. |
| Portable project/migration export | No independently frozen format | **Not currently supported** |
| Direct portal submission | Outside product boundary | **Not supported** |

Ordinary overlay export flattens supported visible marks. The qualified removal route reconstructs one affected active PDF page as a fresh image-only unsigned derivative and requires six runtime checks, including independent English OCR. On R1, export cancellation produced no file, the original and its managed copy were preserved and a retry to an occupied path kept the verified derivative. Reader and compression evidence has separate exact-file scope. [R1 qualification](../development/pws0807-r1-qualification.md)

A checklist **Combined PDF** that includes an active image source and a saved visual overlay can fail before creating a file. The staged R1 producer refused the four-page mixed composition. A revised case kept the overlay, rotation and originals, replaced the active PNG reference with a reviewed PDF page and used four active PDF pages. The staged export matched the earlier successful desktop result in extracted text and rendered pixels on every page. [Historical workflow](../development/pws0701-result.md) · [R1 qualification](../development/pws0807-r1-qualification.md)

## 8. Offline and network behavior

| Item | Release value |
|---|---|
| Core case creation, import, organize, check, and export | The disconnected standard user created a case, imported PDF/JPEG/PNG, saved a reordered and rotated plan, restarted the app and exported a verified derivative. |
| Mandatory account | None |
| Project-controlled server required for core workflow | None |
| Worker network access during document processing | The R1 staged launcher denied a zero-capability worker and its descendant access to local IPv4/IPv6 TCP and UDP listeners. Permitted control connections reached the same listeners. |
| Optional downloads | The app discovers local OCR language packs. The offline updater reads a local folder and has no automatic check. The owner has authorized no public acquisition route. |
| Data transmitted by optional features | The disabled live update channel has no production anchor. The installed diagnostics save used only the user-selected local destination. |
| Offline installation path | Prepared per-user setup on the disconnected clean guest verified all 3,219 R1 payload files and launched the desktop under the standard user. |
| Interrupted download behavior | In-app download is unsupported because the OCR pack and update routes use local files. A future public delivery route requires owner approval. |

## 9. Resource behavior

| Item | Release value |
|---|---|
| GPU requirement | The supported processing path uses CPU document engines. No discrete GPU minimum is specified. |
| Minimum practical CPU/RAM/storage claim | No physical hardware configuration is certified for R1. |
| Low-resource/constrained profile | Version 1. The exact packaged runtime and Windows jobs processed three previews and one combined PDF under both normal and constrained profiles on the x64 host. |
| Expected temporary space | The constrained three-input workload sampled 35,109 bytes of case temporary files at peak and zero at completion. Input, profile and operation determine actual space needs. |
| Large-input warnings | Budget limits apply to bytes, pages, raster dimensions and working space. Follow the reported refusal or reduce the workload. |
| Concurrency behavior | The v1 policy bounds heavy operations and worker resources. The constrained workload recorded a peak worker-job commit of 42,938,368 bytes. The qualification record distinguishes Windows job measurements from sampled process-tree usage. |
| Benchmark evidence | [R1 workload and responsiveness evidence](../development/pws0807-r1-qualification.md) · [Candidate matrix](../development/pws0807-rc-matrix.md). The running matrix retains any unqualified timing or accessibility findings. |

Constrained-profile evidence proves bounded software behavior for the named revision and workload.
It does not certify a named physical computer, CPU count, RAM amount, GPU, SSD, HDD, or VM.

## 10. Privilege, security, and privacy

| Item | Release value |
|---|---|
| Normal application privilege | R1 ran under the genuine standard user on the offline Pro x64 guest. |
| Operations requiring elevation | The documented per-user setup, launch, repair, preserve removal and confirmed user-case removal were exercised in the standard account. |
| Untrusted inputs | PDF, JPEG, PNG, templates, and any future project/engine package as applicable |
| Document processing isolation | Document parsing and export use restricted Windows workers. The R1 workload recorded native job limits and resource measurements. The qualification record gives the exact installed or staged scope. |
| Network during processing | No worker traffic reached the four tested loopback listener types. The standard-user document workflow also ran with the guest adapter disconnected. |
| Update provenance | Check the local installer against the trusted build reference before replacement. The disabled live channel has no production trust root. The separately exercised verifier enforces cryptographic channel authority, freshness and exact package identity. |
| Secrets handling | No secrets or sensitive document content in arguments, routine logs, crash reports, or support bundles |
| Telemetry | Off by default |
| Original preservation | The three external mixed inputs and their managed copies matched SHA-256 before and after export. Keep-cases removal preserved managed data and separate external originals and outputs. |
| Known security limitations | One affected active page from a supported unencrypted PDF is qualified for permanent removal. Ordinary visual marks remain overlays. Recheck reader-specific and scenario-specific limits before relying on an exported file. [Known limitations](../../KNOWN_LIMITATIONS.md) |
| Security reporting route | For this internal build use the private maintainer channel that supplied it. A public reporting address remains an owner decision. [Security reporting](../../SECURITY.md) |

## 11. Support bundle

PWS-0805 implements a local, previewable support bundle with a closed allowlist. The
[source contract](../architecture/support-bundle.md) and
[synthetic sample](../../tests/fixtures/diagnostics/v1/README.md) specify exact bytes;
R1 also passed local preview, consent, save, refusal and exact-member verification.

| Item | Release value |
|---|---|
| Creation path | TRACE offers **Create support bundle…** in **Settings** and **Help**. Exact unsigned diagnostics R1 passed filename selection, archive verification, privacy and overwrite refusal. Completion-candidate verification remains scoped to applicable evidence. |
| User preview before creation | Required |
| Default destination | Explicit user-selected local file; no automatic submission |
| Included categories | Exact preview of `summary.txt` and `allowlist.txt`; numeric versions by default, other closed typed diagnostic fields only when actually recorded; no file collector |
| Excluded | Document bytes/text, OCR text, form values, names, addresses, filenames, full paths, metadata values, thumbnails, signatures, and credentials; any future content-sample workflow requires a separate decision and approval |
| Cancellation/failure behavior | Installed R1 native Cancel, picker permission denial and application destination refusal left no new archive. The same preview saved to a fresh supported name. An occupied destination retained its previous bytes. |
| Sample sanitized bundle | Exact installed R1 two-member ZIP with SHA-256 `f8599db42729a3541464f41496960c2b4af95fc96007f0f3d7f47fe07fa63e8e`. [Diagnostics verification](../development/pws0807-unsigned-completion.md#observed-diagnostics-outcome) |

PaperWorkShell and ThirdLife support artifacts remain separate.

## 12. Sample artifacts

The internal R1 handover includes `PaperWorkShell-R1-synthetic-sample.zip`, 4,141,874 bytes with 23 members. The SHA-256 is `995d3d852d1cf68e2911d685d542eb80a4b1afafd6a83a9fe4fd890aa6776416`. A fresh process reopened the archive, checked every member's size and digest, and opened its PDF and ZIP references. [R1 qualification](../development/pws0807-r1-qualification.md)

| Item | Release value |
|---|---|
| Synthetic sample input set | The archive contains a born-digital PDF, an EXIF-oriented JPEG, a scan PNG, a four-category PDF, an intentionally truncated refusal example, a three-page compression reference and a one-page permanent-removal exercise. |
| Sample portable workspace/project | **Not currently supported** |
| Expected PDF/ZIP/summary outputs | Installed mixed and reordered PDFs, four-category combined PDF and saved summary, two-member diagnostics ZIP, color/grayscale and three-part PDF/ZIP component references, and the verified one-page removal derivative. |
| Expected behavior notes | Read `READ-ME.md` inside the archive for the local steps, visible results and the two observed four-part package timeouts. |
| SHA-256 manifest | `manifest.json`, SHA-256 `95b06fffe394ca1f48840493b5e5017514e1bc351c9fd6447b5b00c8d7b0f676`, identifies the exact engineering build, scope, files, lengths, hashes and expected behavior. |
| Licence/provenance of samples | The manifest records each generated fixture's origin. The archive includes the project MIT licence and the SIL Open Font License notice for embedded Noto Sans subsets. |

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

Check the R1 matrix for migration, rollback, accessibility, resource and timing results together with their exact configurations. Unobserved configurations carry no support claim. [R1 qualification](../development/pws0807-r1-qualification.md) · [Current known limitations](../../KNOWN_LIMITATIONS.md)

## 14. Release contact and maintenance

| Item | Release value |
|---|---|
| Issue/reporting route | Report to the maintainer through the existing private R1 handover channel. Include the candidate identity, observed behavior and sanitized error code. |
| Security reporting route | For this internal build use the private maintainer channel that supplied it. A public reporting address remains an owner decision. [Security reporting](../../SECURITY.md) |
| Maintained release versions | The internal engineering handover covers only the identified R1 artifact and documented scenarios. Public supported versions require owner designation. |
| Maintenance status | Internal engineering qualification. Public preview and stable designation require separate owner decisions. |
| End-of-support policy | No public support period has been announced for this engineering build. |
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

- [x] Exact product version, source revision, channel, and engineering sheet revision are recorded.
- [x] The engineering values, unsupported interfaces and public decisions reserved for the release owner are identified.
- [x] Package identity, size, hash, observed signing state, SBOM, notices, and source revision are recorded.
- [x] Install/update/repair/removal, privilege, restart, rollback, and residual-data behavior are documented.
- [x] Launch, supported opening, associations, and the normal launcher’s argument refusal are documented.
- [x] Data locations and retention/deletion behaviors are documented by category.
- [x] Input, output, copy, conversion, overlay flattening and permanent-removal semantics are documented.
- [x] Offline operation, optional network behavior and the measured workload are documented.
- [x] The engineering security scope, support bundle, tested limitations, synthetic sample and internal reporting route are documented.
- [x] [Release evidence](RELEASE_EVIDENCE.md) identifies the exact packaged candidate.
- [x] The engineering sheet uses known-folder notation, synthetic examples and repository-relative references.
- [ ] The completed sheet is frozen with the release artifacts.
