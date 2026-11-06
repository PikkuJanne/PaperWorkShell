# ADR 0018: Windows installer, identity and signing boundary

- Status: Accepted implementation choices after explicit maintainer direction; packaged/public release qualification remains pending
- Date: 2026-11-03
- Platform-scope amendment: 2026-11-04, superseded by [ADR 0019](0019-windows-11-x64-only-scope.md) / PWS-D043
- Task: PWS-0802
- Decisions preserved: PWS-D004 (historical platform scope, replaced by PWS-D043), PWS-D005, PWS-D009, PWS-D012, PWS-D013, PWS-D028,
  PWS-D029, PWS-D030, PWS-D034, PWS-D038, PWS-D041, PWS-D042

## Context and evidence

The maintainer's resumption instruction selects Janne Vuorela as publisher/maintainer, MIT for
authorized project-owned material and free official distribution, including commercial use.
The target is Windows 11 with no product account, registration, activation, subscription or
licence key, and no document uploads, telemetry or automatic crash-report submission. It
authorizes unsigned internal engineering candidates and production signing integration, but
supplies no provider enrollment, production signing credential or Microsoft agreement acceptance.
The earlier preflight is preserved in [its historical report](../development/pws0802-result.md).
The subsequent maintainer decision explicitly accepts Community 2022 as an individual
and authorizes official acquisition and scoped recipient corrections. The
[current route result](../development/pws0802-community-runtime-result.md) records the
actual authenticated product copy; the earlier proposal was not approved verbatim.

PWS-0801 is done. Its exact internal dependency candidate is
`pws0801-dependency-stage-20261103-r3`, documented in the
[release evidence index](../release/RELEASE_EVIDENCE.md#pws-0801-resolved-internal-dependency-candidate).
[ADR 0017](0017-release-dependency-staging.md) deliberately leaves publisher, first-party
licence, protective distribution terms, installer, signing and clean-machine qualification to
PWS-0802. Its `0.0.0` / `NOASSERTION` values are internal-stage facts.

The reusable payload has embedded CPython under `runtime/`, application modules under
`runtime/Lib/site-packages/`, engines under `runtime/engines/`, and separate `notices/` and
`sources/` trees. The isolated `python314._pth` contains only the standard-library archive,
runtime directory and installed package directory; it does not enable `site` initialization.
Keep this architecture and the separate restricted document worker.

Source inspection found two actual packaging gaps: qpdf's default is a development-machine
installation, and OCR discovery relies on explicitly supplied paths or environment overrides.
Installed launch must use package-relative engine resources, preserve exact OCR admission,
and keep worker network denial and resource limits. A direct adapter smoke run from PWS-0801
does not prove installed desktop/worker launch.

The 2026-11-03 read-only host preflight observed Windows build `26300.9711`, edition
`Professional`, a 64-bit process/OS, and an unelevated process. `makecab.exe` and `msiexec.exe`
were discoverable. Inno's compiler, NSIS, SignTool, Windows Sandbox and Hyper-V's `Get-VM`
were not discoverable through their normal command entry points. This is a source host with
development tools, not clean-machine or supported-edition evidence. It establishes no hardware
certification. No certificate store or private signing material was inspected.

## Selected implementation

Use Inno Setup **7.1.1 x64**, whose official download page identifies the immutable release
and expected publisher, as the installer technology. Before execution, admit its exact binary,
source, licence, embedded components and build dependencies under the dependency policy;
authenticate the download and signer; pin their hashes; and add the resulting compiler and
shipped installer/uninstaller components to the exact resolved inventory and SBOM. The
compiler's 133 files, upstream source and embedded notices are now admitted in
[`inno-setup-7.1.1-review.json`](../../packaging/windows/inno-setup-7.1.1-review.json).
The exact unsigned internal candidate is documented in the
[implementation report](../development/pws0802-implementation-20261103.md).

Use the following independent identity and installation policy:

| Field | Selected value or required evidence |
|---|---|
| Product | PaperWorkShell |
| Application ID | `io.github.pikkujanne.paperworkshell`, based on the public repository owner |
| Installer upgrade identity | `{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}`; UUIDv5 of the public repository URL plus application ID, fixed in the installer |
| Package identity | Product, explicit version, channel and `win-x64`; never a ThirdLife version |
| Publisher/distributor | Janne Vuorela |
| First-party licence | [MIT](../../LICENSE), with [explicit scope](../../LICENSING.md); third-party material retains its terms |
| Product version/channel | `0.0.0` / `engineering` for internal qualification; not a public v1.0 version |
| Architecture | Windows 11 x86-64 (x64) only under PWS-D043; ARM64 native and x64 emulation on ARM64 are outside project scope |
| Windows editions/builds | Windows 11 target; engineering minimum build 22000; exact supported release matrix requires clean packaged evidence |
| Application directory | `<LocalAppData>\Programs\PaperWorkShell`, separate from cases |
| Privilege | Per-user, `PrivilegesRequired=lowest`, no privilege override, reject elevated execution |
| Associations/URI handlers | Unsupported; visible PDF/JPEG/PNG import remains available in the application |
| Services/scheduled tasks | None |

### Superseded architecture plan

The 2026-11-03 architecture row selected: “x86-64 binaries; chosen ARM64 route is Windows 11 x64 emulation, not yet qualified or enabled by the initial installer.” That unqualified plan is retained here as history. The explicit 2026-11-04 maintainer decision in [ADR 0019](0019-windows-11-x64-only-scope.md) replaces it with Windows 11 x64-only scope. It is not an ARM test pass, pending release prerequisite or future mandatory task. Existing R7 artifact bytes, embedded build-epoch metadata, audits and observations remain unchanged.

Set a matching AppUserModelID on the normal shortcut/application. Use a small GUI launcher
and the packaged interpreter; preserve the console interpreter for restricted workers. Do not
introduce a freezer, shared runtime, system Python dependency, hidden processing CLI or
environment-based engine lookup into installed launch. Startup failures must use stable,
plain-language messages without exposing exception values or private paths.

The R4 GUI child has the fixed product process filename
`runtime\PaperWorkShellDesktop.exe`: an unmodified byte-for-byte copy of CPython's
`pythonw.exe`, owned by the CPython component with its original signature, metadata,
licences and sources. Retain the original interpreter and use `runtime\python.exe`
for restricted workers. The public launcher stays `runtime\PaperWorkShell.exe` and
both selected identities remain unchanged. A separate installed change-summary notice
records this filename-only packaging change under the PSF terms.

The 2026-11-04 Notices correction uses one fixed native `--notices` mode to
launch only `paperworkshell.ui.notices` in that packaged GUI interpreter. The
ordinary Notices shortcut must enter through `PaperWorkShell.exe`, preserving
its process-specific Windows Error Reporting suppression, safe DLL search,
minimal environment and child Job. The viewer retains its existing lifecycle
mutex; as with removal review, the native helper delegates that mutex to the
module to avoid acquiring it twice. No filenames, URLs, processing operations
or extra operands are accepted. This serves ordinary offline licence access;
it is not a document-opening or sibling-application interface. Experimental R8's
direct-interpreter shortcut remains historical and unqualified; the corrected
route needs its own candidate and keyboard/NVDA observations.

The controlled R3 diagnostic established independent Windows process/support value:
the generic embedded interpreter was misidentified as the machine's unrelated IDLE
registration by both UI surfaces, whose ownership checks then rejected the actual
window. A byte-identical product-named diagnostic was correctly resolved and accepted
real keyboard input. No ownership guard, authentication or Windows security setting
was disabled. The actual R4 package must repeat affected launch, isolation and UI checks;
the diagnostic alone is not packaged qualification.

`DisableDirPage=yes` alone does not enforce the installation directory: Inno accepts `/DIR=`.
Disable previous-directory reuse, reject directory/INF overrides, and validate the resolved
destination before any writes. Authenticate its ancestors and reject reparse/alias paths and
unsafe pre-existing contents. Never follow an install target into case data or external files.
Reject an already elevated setup/uninstaller rather than accidentally operating as another
user. `ArchitecturesAllowed=x64os` confines the initial candidate to native x64 Windows;
`x64compatible` also admits ARM64 emulation and would exceed the current PWS-D043 scope as well as the historical candidate's evidence.

### Prerequisites, notices and licensing

Historical R1/R2 retained ADR 0017's external Microsoft-installed runtime prerequisite;
they did not satisfy complete clean-machine runtime delivery. Current R3 supplies five
unchanged AMD64 Microsoft runtime objects in nine explicit app-local paths under the
accepted Community 2022 individual REDIST route: five beside Python, the byte-identical
pikepdf loader alias and three beside qpdf. The actual product acquisition, source/member
hashes and signatures are bound by the
[new review](../../packaging/release-reviews/microsoft-community2022-v1.json).
The filename-only alias interpretation is explicit, not claimed Microsoft approval.
Historical wheel/VS2019 notices and PWS-0801 evidence remain unchanged; the new candidate
independently resources these Microsoft copies and does not infer acceptance of an old
VS2019 distributor indemnity. Native app-local origin and clean-platform proof remain
separate qualification obligations.

Present the [final Microsoft-only recipient terms](../../packaging/licenses/release-native/microsoft-msvc-runtime/COMMUNITY-2022-RECIPIENT-TERMS.txt)
through the local installer agreement. It starts unaccepted. Quiet installation must
explicitly supply exactly one `/ACCEPTMICROSOFTRUNTIME=<published-terms-SHA256>`;
missing, wrong or duplicate acceptance fails before application writes. No runtime
download, account, sign-in, payment or development-product installation is needed by
recipients. Normal processing remains offline.

Separate first-party licensing from third-party notices and component-specific terms. Preserve
Qt/LGPL replacement, relinking and reverse-engineering rights. Never apply a blanket restrictive
EULA to those components. Make versions, full notices, corresponding sources and replacement
instructions available through ordinary installed user-visible paths.

### Signing and exact output identity

Keep private material in a maintainer-controlled certificate store, hardware device or signing
service. Obtain only the approved public signing identity and procedure. Do not read/export
keys, request a PFX/password, log credentials, or use an arbitrary available certificate.

Prepare the launcher, sign it through that approved route, then verify expected publisher,
certificate identity, Authenticode trust, digest and timestamp. Generate/sign the Inno
uninstaller before embedding it; Inno supports an external signed-uninstaller handoff. Build
and sign the final installer and independently verify its extracted uninstaller and installed
launcher. Microsoft's documented SHA-256 file/timestamp algorithms are the proposed baseline.
Unsigned or test-certificate artifacts remain internal qualification outputs and cannot satisfy
PWS-0802's signed-artifact criterion.

Regenerate the inventory, both SBOMs, checksums and release sidecars after final signed bytes
exist. Keep source snapshot, toolchain/recipe hashes, dependency inputs, candidate ID and
signature evidence aligned. PWS-0801's existing hashes remain unchanged historical facts;
they are not the new installer's provenance.

### Removal, reinstall and recovery

Remove only installer-owned files and registrations. Preserve cases by default, including in
any eventually supported silent removal. Interactive uninstall must present a separate,
explicit data choice before removing the application. Confirmed deletion delegates to the
existing authenticated per-case lifecycle store; never recursively delete the application-data
root, interpret external manifest paths as deletion targets, or delete external exports/sources.

Retain and report active, unsupported, corrupt, unknown or partially removed cases. Allow
cancellation between cases and state completed/retained counts. Do not erase worker ACL
recovery journals to make the install directory removable. Prove ordinary worker shutdown and
recovery before removing its resources. The [qualification plan](../../packaging/windows/QUALIFICATION_PLAN.md)
lists residue categories, failure paths and the required external-file sentinels.

The first installer implements reinstalling the exact same candidate as a narrow
application-file repair, without changing cases. Different-version updates, downgrade policy
and authenticated update recovery remain PWS-0803 work. Do not uninstall the previous version
as a first step of replacement. Do not claim crash/power-loss atomicity or automatic restoration
of a previous version: Inno's installation-order documentation describes a boundary after which
later failures do not roll back earlier installation work. In particular, a `[Run]` smoke launch
cannot serve as a transactional acceptance gate. Test precommit failures, interruption and
same-candidate recovery before documenting their actual behavior.

The engineering qualifier uses the fixed options documented in its report; other combinations
remain unqualified. Reject destructive silent data removal, forced application
closure, automatic reboot and unsupported destination/privilege overrides. Do not enable
installer path logs by default; they are not PaperWorkShell's sanitized diagnostic stream.

## Alternatives considered

- **NSIS:** permissive core licence and a reusable installer engine, but more custom work for
  repair/recovery and safe data-choice behavior. It still needs exact component/source admission.
- **MSI authored through Windows Installer APIs:** the OS provides database/cabinet facilities,
  but bespoke table/UI/custom-action authoring would add substantially more security-sensitive
  code than the selected mature installer engine. WiX is an alternative authoring layer with
  its own dependency and current maintenance-fee terms to review.
- **PyInstaller/Nuitka:** unnecessary for the accepted embedded-interpreter payload; adds another
  native toolchain, licence and worker-bootstrap qualification surface.
- **Bundle canonical VC runtime files or `vc_redist`:** not authorized by the accepted internal
  stage. A downloaded valid signature establishes authenticity, not redistribution authority.
- **Internal unsigned installer:** useful later for engineering tests, but cannot establish the
  publisher/licence or replace the mandatory approved signatures and clean-machine evidence.

## Remaining release conditions and consequences

Publisher, first-party licensing and the selected Microsoft acceptance/acquisition/terms
route are resolved by explicit maintainer authority and actual engineering evidence;
see the [current continuation](../development/pws0802-community-runtime-20261103.md).
No repeat Community agreement acceptance is needed. Exact supported Windows 11 x64
editions/builds, actual standard-account behavior and remaining packaged observations
still require their real release evidence; ARM64 native/emulation is outside scope.
Public signing also requires an approved provider/identity; the
SignPath Foundation route remains conditional and is not enrolled. No private material is
requested. Engineering and independently ready tasks continue while these actions are pending.

The original implementation selected existing parameters without a released case/RPC schema
transition. Its platform plan is subsequently superseded by PWS-D043 / ADR 0019; that
approved scope amendment changes no case/RPC schema or implementation behavior. There is
no sibling repository, runtime, service, data access, release-cadence dependency or compatibility
claim. Release-specific fields stay draft/TBD until their exact packaged evidence exists.

## Primary sources checked on 2026-11-03

- [Inno release downloads and verification entry point](https://jrsoftware.org/isdl.php)
- [Inno licence](https://github.com/jrsoftware/issrc/blob/is-7_1_1/license.txt)
- [PrivilegesRequired](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm)
- [Actual administrative privilege check](https://jrsoftware.org/ishelp/topic_isxfunc_isadmin.htm)
- [Architecture identifiers](https://jrsoftware.org/ishelp/topic_archidentifiers.htm)
- [Setup command-line overrides and restart behavior](https://jrsoftware.org/ishelp/topic_setupcmdline.htm)
- [External signed-uninstaller workflow](https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm)
- [Installation order and rollback boundary](https://jrsoftware.org/ishelp/topic_installorder.htm)
- [Microsoft SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool)
- [Microsoft VC redistribution rules](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170)
- [Qt 6.11 upstream Windows matrix](https://doc.qt.io/qt-6.11/windows.html)
- [CPython 3.14 upstream Windows requirements](https://docs.python.org/3.14/using/windows.html#supported-windows-versions)
