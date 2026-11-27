# PaperWorkShell release interface sheet

| Field | Current value |
|---|---|
| Document status | **FROZEN — exact packaged M8 candidate. This sheet is not an application API.** |
| Interface revision | `pws0808-r3-interface-1` |
| Source engineering sheet | `pws0807-r3-interface-1`, SHA-256 `db9ad671e63c93bd51daa3324f7b302ca3a80da51a761e5861a897e82dc8a6e8` |
| Product | PaperWorkShell |
| Portfolio role | ThirdLife Team A / A1 standalone product |
| Intended completion | Packaged M8 preview and stable candidate documentation under PWS-D044 |
| Version relationship | This sheet identifies the exact R3 artifact. Product-completion disposition is recorded in the M8 gate report. |

## How to use this sheet

This is the minimal human-readable black-box interface for deployers, support workers, and a
possible future ThirdLife Deployment and Suite Assembly compatibility cut. It documents behavior a
standalone desktop product exposes. It is not a shared runtime schema, a custom adapter contract,
or permission to inspect PaperWorkShell private data.

Use this sheet with the matching evidence index and release notes. The scope is the exact packaged
candidate and the configurations described in each observation. Publication requires separate
authorization. [Portfolio boundary](../../THIRDLIFE_ALIGNMENT.md) · [Evidence index](RELEASE_EVIDENCE.md) · [Release notes](RELEASE_NOTES.md) · [M8 gate report](../gates/m8-product-completion-gate.md)

This sheet describes `pws0807-tab-focus-engineering-r3`. The installed marker reports version `0.0.0` and channel `engineering`. The standard-user clean installation and the R1-to-R3 case-preserving replacement ran on disconnected Windows 11 Pro x64 build 26300.9711. Host component checks ran on build 26300.9974. The payload comparison with R2 found two changed application files, both in the desktop tab presentation and its use by the main window. All 175 third-party acquisition records matched. Public designation and distribution require an owner decision. [R3 qualification](../development/pws0807-r3-qualification.md) · [Candidate matrix](../development/pws0807-rc-matrix.md)

For the current workflow and its limits, see the [user guide](../user/USER_GUIDE.md),
[care and recovery guide](../user/CARE_AND_RECOVERY.md), and [security policy](../../SECURITY.md).

## 1. Product identity

| Item | Release value |
|---|---|
| Product name | PaperWorkShell |
| Reverse-domain application ID | `io.github.pikkujanne.paperworkshell`; selected in ADR 0018 |
| Product version | `0.0.0` in the R3 runtime marker. |
| Release channel | Internal `engineering`. |
| Publisher | Janne Vuorela |
| Product licence | MIT for [authorized project-owned material](../../LICENSING.md); third-party terms preserved; official distribution free including commercial use |
| Supported operating systems | The standard user installed the candidate offline on Windows 11 Pro x64 26300.9711 with Smart App Control Off. [Windows support matrix](WINDOWS11_X64_SUPPORT.md) |
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
| Installer/package filename | `PaperWorkShell-pws0807-tab-focus-engineering-r3-win-x64-UNSIGNED.exe` |
| Package format | Native x64 Inno Setup executable, per-user installation |
| Package size | 513,023,918 bytes |
| SHA-256 | `7791cef6be0062319a9c9f39a4bf130ded8f3914485eb50ab4e07d182c992889` |
| Observed PaperWorkShell publisher signing state | Windows Authenticode reported `NotSigned` for the exact R3 installer. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Artifact verification method | Match the installer receipt and resolved inventory, then verify every installed payload file against that inventory. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Source revision | `54e0b916ac069ce00a0ce200c43e07f3a76d1784` |
| Dependency lock/SBOM reference | The committed `uv.lock`, resolved inventory, CycloneDX and SPDX SBOMs, and component notices accompany this build. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Download source | No public download authorized. If publication is approved, record the actual delivery source. |
| Offline-media source | Use the verified internal prepared bundle supplied by the maintainer. A public media source requires a separate distribution decision. |
| Required external runtime | The prepared bundle includes the application, PDF and OCR runtimes and the reviewed Microsoft runtime terms. Standard-user setup succeeded offline. |

The resolved inventory has SHA-256 `a436ca25db576c7cc60936428381ee4e747030b869a77d6c5342c7b15df202a4`, the installer receipt `057407a387e55bacff420deec7e1cc0689c3dffa09317b280f2b5f8f391b9b74` and the runtime marker `5e564b0fef1b5e0ebe30ab19bdfe1d8c65872420889ac35c82d7237c73ab5f1e`. The build audit matched all 345 installed application files to their Git objects, revalidated 3,219 staged files, and reproduced both SBOMs and the notices. [R3 qualification](../development/pws0807-r3-qualification.md)

## 3. Install, update, repair, and remove

| Item | Release value |
|---|---|
| Normal installation path or UI | Run `Setup-PaperWorkShell.exe` from the prepared R3 bundle with `--installer`, `--receipt` and `--receipt-sha256` from the trusted build reference. [Per-user setup](../../packaging/README.md#automated-per-user-setup) |
| Privilege/elevation requirement | The signed-in standard user installed R3, launched the desktop and exported the sample files on both configurations. |
| Supported silent installation options | After reviewing the bundled Microsoft terms, the prepared launcher accepts `--accept-microsoft-terms <terms-sha256>` for unattended installation. [Setup options](../../packaging/README.md#automated-per-user-setup) |
| Default install location | `%LOCALAPPDATA%\Programs\PaperWorkShell`. The installer registers removal for the current user. |
| Restart requirement | Neither observed R3 setup required a Windows restart. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Update mechanism | For this engineering channel use the prepared local installer. Choose **Keep cases** during removal for a replacement. The R1-to-R3 replacement retained and opened the earlier case. Live update trust is unprovisioned. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Repair behavior | R3 same-build repair with `--repair` restored a deliberately missing Microsoft runtime recipient-terms file on the clean configuration. The installer returned exit 0, verified all 3,219 payload files and launched the desktop. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Rollback behavior and limits | When an enforced per-user NTFS quota exhausted the standard user's writable space during file copy, **Cancel installation** removed the partial target and preserved the managed cases and external files. Automatic cross-build rollback is unsupported. Previous-runtime restoration belongs to the separate isolated update rehearsal. The M8 gate report records the separate system-volume check. [R3 qualification](../development/pws0807-r3-qualification.md) · [M8 gate report](../gates/m8-product-completion-gate.md) |
| Normal uninstall path or UI | Open **Settings → Apps → Installed apps**, select PaperWorkShell and choose **Uninstall**. |
| User choice about cases during uninstall | Keeping cases is the default. To delete managed cases, choose review, inspect the inventory, give separate confirmation and acknowledge the reported result before continuing application removal. |
| Data remaining after uninstall | On the clean R3 configuration, **Keep cases** retained four synthetic cases and all 31 managed files. After reinstall the standard user reopened a retained case. Confirmed removal of the four reviewed cases left no cases, and all 23 external inputs and outputs retained their digests. The checked PDF/JPEG/PNG shell defaults were preserved. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Machine-wide services or scheduled tasks | The installed desktop and native document worker run in the signed-in user session. The documented local workflows require no machine service or scheduled task. |

The exact TRACE B8 engineering package supplies standard-user same-build repair and reuse
evidence. PWS-0803 tested offline activation, an ordinary PDF export, previous-runtime restoration
and replay refusal using an unsigned engineering bootstrap and an isolated test-signed successor.
See the separate [TRACE B8 setup record](../development/pws0814-automated-setup.md) and
[offline update record](../development/pws0803-authenticated-updates.md).

The unprovisioned R3 update controller refused the archived signed offer. Using the original isolated public test root in memory, the R3 verifier authenticated the historic 515,298,153-byte ZIP and refused a replay, expiry, a wrong channel and a changed signature. The installed activation and previous-runtime restoration took place on the earlier PWS-0803 test runtimes. [R3 qualification](../development/pws0807-r3-qualification.md)

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
| Normal interactive launch | The offline standard user opened the R3 desktop after clean setup and after a case-preserving replacement. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Supported file associations | **Not currently supported; any future policy requires independent product value, user control, and uninstall tests** |
| Open user-selected PDF through OS launch | **Not currently supported; import through the visible PaperWorkShell UI is the present path** |
| Open user-selected JPEG/PNG through OS launch | **Not currently supported; import through the visible PaperWorkShell UI is the present path** |
| Open portable PaperWorkShell workspace/project | **Not currently supported; no portable project-transfer format is frozen** |
| Multiple-file operating-system opening | **Not currently supported** |
| Local PowerShell recipe | In **Recipe**, choose **Export PowerShell script** for ordinary PDF selection, ordering and rotation. The script uses the installed offline runtime and requires a fresh output path. [User guide](../user/USER_GUIDE.md) |
| ThirdLife-specific CLI/URI/RPC | **Not supported** |

The R3 staged launcher displayed LAUNCHER_ARGUMENT_INVALID for PDF, JPEG, PNG and workspace arguments and directed the operator to the visible in-app import. [R3 qualification](../development/pws0807-r3-qualification.md) · [R2 qualification](../development/pws0807-r2-qualification.md)

On the host, a script generated by R3 replayed the mixed PDF, JPEG and PNG case after its saved page reorder and rotation. Its 22,413-byte result matched the independent reference. Reusing the occupied output name returned an error and preserved the first file. [R3 qualification](../development/pws0807-r3-qualification.md) · [R2 qualification](../development/pws0807-r2-qualification.md)

## 5. Data locations and retention

Cases hold immutable imported copies, a versioned manifest and journal, and rebuildable cache/temporary data. The user selects separate locations for exports, saved summaries and support archives. The retention table identifies configuration, optional engines, case history and recovery files by category. [Data locations and retention](../user/CARE_AND_RECOVERY.md)

| Data | Location and disposition |
|---|---|
| Original files and public outputs | Import reads the selected file at its existing path. The user chooses export and support destinations. These external files survive uninstall and case deletion. |
| Managed case and backups | Under `%LOCALAPPDATA%\PaperWorkShell\Cases`, each case holds `originals`, `manifest.json` and its `journal`. Migration source backups in the journal stay with the case. |
| Cached pages and temporary work | Case preview and thumbnail caches are rebuildable. The worker uses bounded temporary storage. The measured mixed workflow finished with zero case temporary bytes. |
| Engines, preferences and diagnostics | Bundled PDF/OCR engines and English OCR data live under the installed runtime. Case settings remain in managed case state. The support summary uses bounded in-session records until the user chooses a local save. |
| Local update state | Installed activation state and runtime slots belong under the product installation. The per-user lifecycle lock can remain after uninstall. R3 contains no configured production trust anchor. |

R3 preserve removal kept four synthetic cases and 31 managed files. Reinstall reopened a retained
case. Reviewed deletion removed those four cases, kept all 23 checked external files and preserved
the four PDF and image shell defaults. Removal eliminated the application root, registration and
shortcuts. The per-user `%LOCALAPPDATA%\PaperWorkShell.lifecycle-v1.lock` may remain, as observed
on diagnostics R1. User-selected external exports and diagnostic archives stay at their destinations.
[Care and recovery guide](../user/CARE_AND_RECOVERY.md) ·
[Installer lifecycle evidence](RELEASE_EVIDENCE.md#3-install-launch-update-repair-and-uninstall-evidence).

PaperWorkShell owns responsibility for its configuration and workspace formats, lifecycle, and
migrations; users retain control of their local configuration and workspace content. ThirdLife may
hold only its own catalogue, installation, compatibility, and support metadata. It must not read or
index PaperWorkShell documents, OCR text, overlays, signatures, thumbnails, redaction state, or
private case content.

## 6. Inputs

The product boundary currently admits PDF, JPEG, and PNG by content rather than filename extension.
**Add file** on both offline R3 configurations imported the sample PDF, JPEG and PNG. [R3 qualification](../development/pws0807-r3-qualification.md)

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
| One combined PDF | Assembles PDF pages, converts raster inputs, applies supported saved page operations and writes a new PDF | On each R3 installation the ordinary export reopened in PDF, JPEG and PNG insertion order. Reopening the saved case and exporting again produced PDF, PNG and the clockwise-rotated JPEG. The checklist Combined PDF contained four category pages. |
| Several named PDFs | One verified derivative for each planned group | Each R3 installation produced four named, one-page PDFs. Independent text extraction and rendered pixels matched the expected category on every page. |
| ZIP package | Packages ordered PDF derivatives in a new archive | Each installed R3 ZIP contained four category PDFs under the reviewed names in the reviewed order. Independent checks matched each member to its source. |
| Human-readable package summary | Shows the planned and verified counts, user reviews and findings. The user may save a text copy | For each mode on both installations, the saved text matched the summary displayed by the app and the frozen reference text. |
| Separate printable checklist artifact | Checklist review uses the case and its exported summary | Unsupported. Review the checklist in the app and use the package summary. |
| Optional technical manifest | User-facing exports are PDF, ZIP and text | Unsupported in R3. The synthetic sample manifest describes the example archive. |
| Portable project/migration export | No independently frozen format | **Not currently supported** |
| Direct portal submission | Outside product boundary | **Not supported** |

Ordinary visible marks are flattened overlays. For supported permanent removal, R3 rebuilt the sole active page of the unencrypted synthetic PDF as an image-only unsigned derivative. The clean installed desktop reported all six required checks passed, including independent OCR, and saved and reopened the output. An independent parser, rendered-pixel comparison and OCR matched the 34,084-byte frozen derivative. The retained marker stayed visible and the marked target was absent. The staged verifier separately checked ten actual saved or resaved files from the named-reader matrix. [R3 qualification](../development/pws0807-r3-qualification.md)

The R3 staged exporter regenerated the five-page M3 reference with all eight overlay kinds and its synthetic signature image. The PDF bytes and every page’s extracted text and rendered pixels matched. A concurrent host run timed out before writing a file. The same test completed in about 65 seconds on a quiet host at the original 120-second limit. The four accepted Windows reader observations apply to this file. [Overlay reader matrix](../compatibility/pws-0307-overlay-reader-matrix.md) · [R3 qualification](../development/pws0807-r3-qualification.md)

A mixed checklist **Combined PDF** with an active image and visual overlay can be refused before creating a file. R2 demonstrated this limit in the unchanged planner and producer. For the archived mixed four-page case, **Separate PDFs** retained the overlay and original PNG and completed on R3 in 129.920 seconds. For the revised all-PDF case, R3 produced the historic 26,223-byte combined file in 6.610 seconds and the four separate files in 19.840 seconds. All 12 page comparisons matched the reference text and pixels. The OCR PDF for the archived mixed case had a different generated resource name and PDF identifiers on R3. Normalizing those fields on disposable copies yielded identical PDFs. [R3 qualification](../development/pws0807-r3-qualification.md) · [R2 qualification](../development/pws0807-r2-qualification.md)

The R3 packaged adapters reproduced the six compression PDFs and one ZIP byte for byte. These are the files covered by the accepted Windows and Android reader observations. In fresh R3 checks, Firefox 161.0, Edge 158.0.4370.45 and Chrome 158.0.8127.38 displayed the third page of the color-preserving PDF. The document pixels matched the retained R2 views. The R3 one-byte scan search measured all four stages and stopped at the 200-dpi and JPEG-quality-80 floor. The smallest verified candidate was 212,711 bytes. The app preserved the previous output and published no over-budget file. [R3 qualification](../development/pws0807-r3-qualification.md) · [R2 qualification](../development/pws0807-r2-qualification.md)

The R3 target-size test exercised the ordinary restricted-worker fallback on a four-page PDF package with a visible overlay. A 100,000-byte limit produced the 26,223-byte historic output. A one-byte limit returned an unsupported compression policy for this composition. The blocked commit left its destination empty and preserved the first output and every original. [R3 qualification](../development/pws0807-r3-qualification.md)

## 8. Offline and network behavior

| Item | Release value |
|---|---|
| Core case creation, import, organize, check, and export | The disconnected standard user created a case, imported PDF/JPEG/PNG, saved a reordered and rotated plan, restarted the app and exported a verified derivative. |
| Mandatory account | None |
| Project-controlled server required for core workflow | None |
| Worker network access during document processing | On the exact R3 host runtime, the restricted worker and its child could not reach the tested IPv4 and IPv6 TCP and UDP loopback listeners. Control processes reached those listeners. The installed document workflows ran on a disconnected guest. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Optional downloads | The app discovers local OCR language packs. The offline updater reads a local folder and has no automatic check. The owner has authorized no public acquisition route. |
| Data transmitted by optional features | The disabled live update channel has no production anchor. The installed diagnostics save used only the user-selected local destination. |
| Offline installation path | Prepared setup verified the complete R3 payload and launched the desktop on the disconnected clean and case-preserving replacement configurations. |
| Interrupted download behavior | In-app download is unsupported because the OCR pack and update routes use local files. A future public delivery route requires owner approval. |

## 9. Resource behavior

| Item | Release value |
|---|---|
| GPU requirement | The supported processing path uses CPU document engines. No discrete GPU minimum is specified. |
| Minimum practical CPU/RAM/storage claim | The engineering qualification covers the recorded configurations. The project has issued no physical hardware certification. |
| Low-resource/constrained profile | Version 1. The R3 installed-layout runtime processed three previews and one ordinary mixed PDF in each of the normal and constrained resource profiles. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Expected temporary space | The R3 three-input constrained run sampled 31,457 bytes of temporary case files at peak and zero at completion. Inputs and operations determine actual space needs. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Large-input warnings | Budget limits apply to bytes, pages, raster dimensions and working space. Follow the reported refusal or reduce the workload. |
| Concurrency behavior | Resource policy bounds heavy operations and worker processes. The R3 constrained mixed workload recorded a 43,257,856-byte peak Windows worker Job commit and a 197,033,984-byte process-tree working set. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Benchmark evidence | [R3 qualification](../development/pws0807-r3-qualification.md) · [R2 qualification](../development/pws0807-r2-qualification.md) · [R1 qualification](../development/pws0807-r1-qualification.md). The qualification records identify the environment and scope for each measurement. |

On the offline R3 replacement desktop, PNG import showed action-specific feedback within 167.57 ms cold and 168.15, 172.30 and 157.72 ms warm, within the 250-ms budget. The original upright preview landmark appeared within 1,407.66 ms cold and 1,458.67, 1,463.93 and 1,526.81 ms warm, within the respective 5,000-ms and 2,000-ms budgets. These figures are capture-return upper bounds for four observations on the identified configuration. [R3 qualification](../development/pws0807-r3-qualification.md) · [R2 qualification](../development/pws0807-r2-qualification.md)

The staged R3 constrained searchable-PDF workload produced a verified 128,180-byte, 12-page PDF. The complete measured run took 81.031 seconds with a peak process-tree working set of 341,299,200 bytes against the 1,342,177,280-byte limit. The private temporary-file peak was 740,444 bytes. The first run produced the derivative but its private temporary measurement was incomplete. [R3 qualification](../development/pws0807-r3-qualification.md) · [R2 qualification](../development/pws0807-r2-qualification.md)

Constrained-profile evidence proves bounded software behavior for the named revision and workload.
It does not certify a named physical computer, CPU count, RAM amount, GPU, SSD, HDD, or VM.

## 10. Privilege, security, and privacy

| Item | Release value |
|---|---|
| Normal application privilege | The standard user installed and launched R3 in the offline Pro x64 desktop session. |
| Operations requiring elevation | None for the tested per-user setup, launch, repair, preserve removal and confirmed user-case removal. The installer rejects elevated execution of this route. |
| Untrusted inputs | PDF, JPEG, PNG, templates, and any future project/engine package as applicable |
| Document processing isolation | The R3 desktop paths used restricted Windows workers for permanent removal, ordinary export and the target-size fallback. The measured workloads record worker and child process limits, cancellation and recovery. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Network during processing | No worker traffic reached the four tested loopback listener types. The standard-user document workflow also ran with the guest adapter disconnected. |
| Update provenance | Check the local installer against the trusted build reference before replacement. The disabled live channel has no production trust root. The separately exercised verifier enforces cryptographic channel authority, freshness and exact package identity. |
| Secrets handling | No secrets or sensitive document content in arguments, routine logs, crash reports, or support bundles |
| Telemetry | Off by default |
| Original preservation | The PDF, JPEG and PNG originals, managed copies and stored manifest hashes matched on both R3 configurations. Reopened ordinary and edited exports preserved the expected page order and pixels. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Known security limitations | Permanent removal requires exactly one active page, and that page must be the affected supported PDF page. Ordinary visual marks remain overlays. Recheck reader-specific and scenario-specific limits before relying on an exported file. [Known limitations](../../KNOWN_LIMITATIONS.md) |
| Security reporting route | For this internal build use the private maintainer channel that supplied it. A public reporting address remains an owner decision. [Security reporting](../../SECURITY.md) |

## 11. Support bundle

PWS-0805 implements a local, previewable support bundle with a closed allowlist. The
[source contract](../architecture/support-bundle.md) and
[synthetic sample](../../tests/fixtures/diagnostics/v1/README.md) specify exact bytes;
R1 also passed local preview, consent, save, refusal and exact-member verification.

| Item | Release value |
|---|---|
| Creation path | Open **Create support bundle…** in **Settings** or **Help**. Review the two-member inventory, consent, then choose a local filename. Both R3 installations exercised this workflow. [R3 qualification](../development/pws0807-r3-qualification.md) |
| User preview before creation | Required |
| Default destination | Explicit user-selected local file; no automatic submission |
| Included categories | Exact preview of `summary.txt` and `allowlist.txt`; numeric versions by default, other closed typed diagnostic fields only when actually recorded; no file collector |
| Excluded | Document bytes/text, OCR text, form values, names, addresses, filenames, full paths, metadata values, thumbnails, signatures, and credentials; any future content-sample workflow requires a separate decision and approval |
| Cancellation/failure behavior | Cancel left the local destination empty. On the R3 replacement, an occupied filename and a Windows-valid over-limit name were refused. Choosing a fresh name saved a byte-identical copy of the same reviewed preview. [R3 qualification](../development/pws0807-r3-qualification.md) |
| Sample sanitized bundle | The sample contains the independently observed R2 reference ZIP. The clean and replacement R3 bundles each matched their own reviewed preview, including member hashes, CRCs and the closed allowlist. Their timestamps can differ. [R3 qualification](../development/pws0807-r3-qualification.md) |

PaperWorkShell and ThirdLife support artifacts remain separate.

## 12. Sample artifacts

The internal R3 handover includes `PaperWorkShell-R3-synthetic-sample.zip`, 4,173,788 bytes with 30 members, SHA-256 `027410d15b8a62874d535fca5fdc625e46e272667a07b3c8096920f217b4f468`. Its expected outputs were captured on R2 and frozen before the R3 black-box runs. A fresh process verified every member and independently reopened the valid PDFs and nested ZIPs. The two R3 configurations were compared with the same archive. [R3 qualification](../development/pws0807-r3-qualification.md)

| Item | Release value |
|---|---|
| Synthetic sample input set | The archive contains a born-digital PDF, an EXIF-oriented JPEG, a scan PNG, a four-category PDF, an intentionally truncated refusal example, a three-page compression reference and a one-page permanent-removal exercise. |
| Sample portable workspace/project | **Not currently supported** |
| Expected PDF/ZIP/summary outputs | Two ordinary mixed PDFs, the four-category combined PDF, four named PDFs, their ZIP and all three saved summaries, the two-member diagnostics ZIP, color/grayscale references, three single-page references and their ZIP, and the one-page removal derivative. |
| Expected behavior notes | Open `READ-ME.md` inside the archive for the documented local actions, expected page order, reviewed package names and refusal examples. The companion release notes clarify the three-page compression references. [Release notes](RELEASE_NOTES.md#synthetic-sample) |
| SHA-256 manifest | `manifest.json` has SHA-256 `a51a4ccf34b47793e4495b00e9300479264ad0f2f33780caf4722aea887eee90`. It names the R3 target and documents the R2 reference material, original artifact provenance, member sizes and digests. |
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

Check the candidate matrix for migration, rollback, accessibility, resource and timing results with their tested configurations. [R3 qualification](../development/pws0807-r3-qualification.md) · [Current known limitations](../../KNOWN_LIMITATIONS.md)

At 200% display scaling with the Windows Aquatic high-contrast theme, R3 drew a visible keyboard-focus outline around the selected Pages, Sources, Recipe and Checks tabs on the clean installed desktop. Forward and reverse navigation also passed at normal 100% and 200% settings. The earlier R2 build failed the Aquatic Pages-tab transition. On the replacement configuration, NVDA 2026.1.1.55980 announced the ten tested phase groups, including error focus, cancellation, consent, review and all three saved package summaries. [R3 qualification](../development/pws0807-r3-qualification.md) · [R2 qualification](../development/pws0807-r2-qualification.md)

## 14. Release contact and maintenance

| Item | Release value |
|---|---|
| Issue/reporting route | Report to the maintainer through the existing private engineering handover channel. Include the candidate identity, observed behavior and sanitized error code. |
| Security reporting route | For this internal build use the private maintainer channel that supplied it. A public reporting address remains an owner decision. [Security reporting](../../SECURITY.md) |
| Maintained release versions | The internal handover covers the exact R3 artifact and documented scenarios. Public supported versions have no designation. |
| Maintenance status | Verified local repair and case-preserving replacement cover this build. The live channel is disabled. Vulnerability reports use the existing private route and the maintainer's emergency dependency procedure. [Security policy](../../SECURITY.md) · [Emergency dependency updates](../development/emergency-security-updates.md) |
| End-of-support policy | No announced public support period or response-time commitment. |
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

- [x] Exact product version, source revision, channel, and matching interface and evidence revisions are recorded.
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
- [x] This candidate sheet is frozen with the matching sample and installer references.
