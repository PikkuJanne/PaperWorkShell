# Windows packaging and dependency evidence

Under PWS-D044, build and qualify the unsigned Windows 11 x64 candidate with the
existing installer and receipt. Record the observed signing state with the artifact
identity, inventory, notices and SBOM. Validate third-party provenance as before.
External distribution is a separate decision. Existing engineering receipts retain
their internal distribution designation.

## Automated per-user setup

Use `Setup-PaperWorkShell.exe` from a prepared PaperWorkShell setup bundle on native
Windows 11 x64. Run it in the intended user's interactive desktop without elevation.
The bundle contains the existing Inno Setup installer and the exact build receipt and
resolved inventory. The separately supplied receipt SHA-256 binds the installer and the
runtime that setup verifies. Obtain the bundle and receipt SHA-256 from the trusted
build or delivery source. The receipt and installer hashes identify the selected
bytes against that reference. The installed engineering build has no trusted
PaperWorkShell publisher signature.

```powershell
.\Setup-PaperWorkShell.exe --installer .\PaperWorkShell-pws0813-trace-engineering-b8-win-x64-UNSIGNED.exe --receipt .\installer-receipt.json --receipt-sha256 145d9efeb0ace1e24e43dd1543a48c0433dac2aad2f764fb4139238c4b4461bc
```

This example installs the internal TRACE B8 build. Windows must allow the two supplied
executables. The bundle includes the application runtime, local OCR components and
recipient terms. Running setup requires no checkout, SDK, Python installation, PATH
changes or network connection. Review and accept the Microsoft runtime terms in the
native installer when it prompts. For unattended installation, review the exact included
terms first and add `--accept-microsoft-terms <published-terms-sha256>` to record that
acceptance explicitly.

Run the same command again to recheck the actual installed payload and launch the
verified application. To request same-build repair, close PaperWorkShell normally and
append `--repair`. For a different fixed-layout build, close the app, remove any update
slots if present, then uninstall through Windows **Installed apps** and choose **Keep
cases and continue application removal**. Install the identified replacement under the
same Windows account and reopen the preserved cases using **Open case**. Follow the
[recovery guide](../docs/user/CARE_AND_RECOVERY.md) for supported migrations and backups. If the result asks you to close an application, finish installer consent or restart,
complete that action and rerun the same command. Setup reports an exit code and a concise
result. Use `--result <new-json-path>` to retain that result for automation.

To prepare the launcher on a Windows build machine, compile the
[setup source](windows/Setup-PaperWorkShell.cs) with the Windows .NET Framework x64
compiler:

```powershell
$framework = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319"
& "$framework\csc.exe" /nologo /target:exe /platform:x64 /r:System.Web.Extensions.dll /r:System.Management.dll "/r:$framework\WPF\UIAutomationClient.dll" "/r:$framework\WPF\UIAutomationTypes.dll" /out:Setup-PaperWorkShell.exe packaging\windows\Setup-PaperWorkShell.cs
```

Place the launcher, exact installer, `installer-receipt.json`,
`resolved-inventory.json` and `Microsoft-runtime-recipient-terms.txt` together
in the prepared bundle. Record the launcher and receipt SHA-256 with the build identity.
See the [PWS-0814 setup record](../docs/development/pws0814-automated-setup.md) for
the tested build and observed scope.

## Authenticated offline updates

The unsigned product path uses the local installer for explicit replacement and repair.
It has no live network update service or configured production update trust. The route
below is exercised on an isolated engineering installation with test trust. If an
authenticated channel is configured in future, the same verification and failure rules apply.

An update-capable installation with a configured channel root offers **PaperWorkShell
Updates** in Start. Close the desktop app, open the updates window, then choose a folder on
a fixed local drive containing `trust.json`, `trust.sig`, `release.json`, `release.sig`,
and `runtime.zip`. Review the displayed version and candidate before selecting
**Install update**. The normal PaperWorkShell shortcut opens the selected version.
The updates window remains available from the original installation for
**Restore previous version** and **Remove installed updates**. Remove update slots before
using the ordinary uninstaller and its separate case-data choice.

For a reviewed baseline build, supply `--update-anchor <path>` and
`--update-anchor-sha256 <sha256>` together to the installer stage command. The public-only
JSON file must be inside the build checkout's `output` tree. Its fields are
`schema_version` (1), `channel` (the baseline marker's channel), `root_public_key`
(lowercase hexadecimal P-256 x and y coordinates, 32 bytes each), `minimum_epoch`
(positive 63-bit counter), and `release_sequence_floor` (nonnegative 63-bit counter).
The builder records the original input and exact installed bytes. A build without those
arguments has no authority to verify update offers.

Build the update ZIP from the exact `payload` directory and `resolved-inventory.json`
of the candidate to be offered. `tools.build_update_payload` checks the full inventory,
including component ownership, notices and source obligations. The declared case schema
must match the case schema supported by the target candidate.

```powershell
.venv\Scripts\python.exe -m tools.build_update_payload --inventory .\resolved-inventory.json --stage .\payload --output .\runtime.zip --channel engineering --case-schema-version 4
```

The builder reports the ZIP size and SHA-256 for the release record. The offline root
signs the trust record and the authorized channel key signs the release record. Both bind
their exact record bytes through distinct domain separators. Keep private keys outside
the repository, installer, ZIP and ordinary tool output. The update ZIP must carry the
same case-manifest schema and exact Microsoft recipient terms as the baseline. Use the
regular installer for a trust-root change or different terms and qualify any schema
transition separately. The ZIP leaves the fixed launcher and registration in place.
Production channel activation is optional future work. An engineering trust root must
stay confined to its test installation. [Trust, activation and recovery design](../docs/adr/0022-authenticated-application-updates.md).

## Packaging inputs

Windows packaging configuration and reviewed release inputs belong in this directory. PWS-0801
implements an internal dependency-stage package and evidence pipeline. The selected installer,
product terms, installation qualification and product-completion evidence belong to their
Milestone 8 tasks.

The current application/package/update target is **Windows 11 x64 only**, under
[PWS-D043 / ADR 0019](../docs/adr/0019-windows-11-x64-only-scope.md). Native ARM64
and x64 emulation on ARM64 are outside project scope, with no release prerequisite
or mandatory future task. The
[support boundary](../docs/release/WINDOWS11_X64_SUPPORT.md),
[current x64 continuation](../docs/development/pws0802-x64-continuation-20261104.md)
and [finite qualification matrix](../docs/development/pws0802-x64-qualification-20261104.md)
record the exact scope and remaining edition/build and scenario evidence.
Upstream architecture facts, source examples and licence metadata are retained;
they do not declare PaperWorkShell support.

PWS-0802's [historical installer preflight](../docs/development/pws0802-result.md) preserves its earlier
unresolved inputs. [Accepted ADR 0018](../docs/adr/0018-windows-installer-and-release-identity.md)
and the [qualification plan](windows/QUALIFICATION_PLAN.md) define the implemented per-user
installer. The [current complete-runtime continuation](../docs/development/pws0802-community-runtime-20261103.md)
binds unsigned internal R7, the acquired Community route and actual qualification separately
from pending product-matrix coverage. The
[earlier R2 report](../docs/development/pws0802-implementation-20261103.md) remains historical.
Neither candidate is a public release.

R7 remains the exact 512,713,704-byte installer SHA-256
`a348150bd322c2c170dd015dc9a164023c645dcd8dea9ae209af265886de653a`,
with actual signature status **NotSigned**. Its original build metadata, source
archive, inventory and audit remain authenticated historical inputs. The newer
x64-only source-policy metadata does not restage or rebuild that artifact. The
engineering guest evidence is Windows 11 Enterprise 26100.9711 x64/genuine
standard user; Pro 26300.9711 describes the host only. R6 clean runtime evidence
and R7's existing instrumented-guest checks keep their separate scopes. The exact
product-completion candidate and supported Windows editions/builds still need evidence.

The R7 build consumed `--microsoft-overlay` and the independently retained
`--microsoft-overlay-receipt-sha256`; there is no unchecked complete-runtime CLI switch.
The builder reauthenticates the full acquisition/recipient receipts and all nine copies,
preserves historical notices, regenerates both SBOMs and presents the Microsoft-only
agreement locally. Quiet installation requires exactly one
`/ACCEPTMICROSOFTRUNTIME=<published-terms-SHA256>` after review of the included terms.
The argument is scoped to those Microsoft files and does not alter MIT or OSS grants.

## Dependency inventory

- [`dependency-inventory-v1.json`](dependency-inventory-v1.json) is the complete checked-in
  PWS-0005 policy baseline.
- [`dependency-inventory-v1.schema.json`](../schemas/dependency-inventory-v1.schema.json) is its
  versioned Draft 2020-12 contract.
- [`dependency-policy.md`](../docs/development/dependency-policy.md) defines admission, update,
  license, integrity, redistribution, renderer, OCR, and SBOM rules.
- [`distribution-decision-bypass.json`](../tests/fixtures/dependencies/v1/invalid/distribution-decision-bypass.json)
  is the canonical policy-rejected example.

The baseline records currently admitted development/build/CI dependencies as well as planned,
deferred, candidate, evaluated, and decision-gated application families. `evaluated` is reserved
for a concrete pinned artifact under measurement, with exact URL, filename, target, size, and
SHA-256 evidence; it does not activate or approve that artifact. Locked Python records duplicate
the reviewed source and compatible Windows x64/universal wheel hashes from `uv.lock` so source,
artifact, and root-project metadata drift fails closed.
The baseline is not a snapshot of an installed machine and is not a release SBOM. In particular, a
minor-line interpreter, a minimum external-tool version, an environment probe, or a representative
source hash must not be presented as the exact identity of a packaged Windows artifact.

### PWS-0801 redistribution audit

The [task result](../docs/development/pws0801-result.md) records the original redistribution audit
and current verification. [ADR 0017](../docs/adr/0017-release-dependency-staging.md) defines the
implemented internal stage. Its separate
[`resolved-dependency-inventory-v1` contract](../schemas/resolved-dependency-inventory-v1.schema.json)
binds exact inputs, review fingerprints, component owners, staged files, selected licence routes
and source/replacement obligations. The historical policy baseline remains unchanged.

The old runtime v1 contains a verified libtiff-to-JBIG loader dependency. Removing only the JBIG
DLL breaks loading, and excluding TIFF user inputs does not remove the dependency. The current
runtime v2 replaces libtiff with an independently built no-JBIG DLL; it does not claim a JBIG
waiver. The exact lxml two-resource omission is separately maintainer-approved, as described below.

The historical archive audit remains reproducible against its 30 original packages:

```powershell
.venv\Scripts\python.exe -m tools.audit_tesseract_redistribution --artifacts <artifact-directory>
```

The audit reads archive members without extracting or executing them. Its expected exit 1 with
`RELEASE_DISTRIBUTION_DECISION_REQUIRED` describes the historical v1 selection; it is not the new
stage's acceptance check. Missing or changed historical inputs fail separately. This preserves
accepted evidence rather than relabelling it as approval of a replacement. Current validation and
freeze commands are below. The
[emergency update policy](../docs/development/emergency-security-updates.md) applies to replacements.

## Maintaining the baseline

For every admitted addition or upgrade:

1. Follow the complete review in the dependency policy.
2. Update the direct declaration and `uv.lock` where applicable.
3. Review every transitive, platform-marker, source, license, executable, and native-binary change.
4. Update components and relationships, then increment `inventory_revision` and `reviewed_on`.
   New component IDs or upstream/license identity changes also require an explicit update to the
   validator's closed reviewed identity policy.
5. Keep non-approved components inactive. A disabled candidate edge documents risk; it does not
   permit production installation, bundling, discovery, or invocation. A concrete benchmark
   artifact remains `evaluated` with candidate packaging state until explicitly approved.
6. Run the registered dependency, schema, documentation, and full quality checks.

Do not commit downloaded installers, native archives, OCR language data, build directories,
generated SBOMs, license keys, certificates, signing material, or local machine inventories as
ordinary baseline files. PWS-0801 generated stages, source archives, receipts and SBOMs remain
under ignored `output/`; reviewed input identities and original licence assets are checked in.

## Release reconciliation

The PWS-0801 pipeline creates a resolved inventory for one exact Windows dependency candidate.
It reconciles three independent views:

```text
reviewed baseline + uv.lock + upstream embedded-component records
                              |
                              v
                 final packaged-file inventory
                              |
                              v
           CycloneDX JSON + SPDX JSON + notice bundle
```

The resolved evidence uses exact interpreter/package/tool/data revisions and hashes the artifacts
actually consumed or shipped. It maps every staged DLL, executable, plugin, model, license, and
notice to a reviewed component. It also records the selected license route and defines every SPDX
`LicenseRef`.

Release validation fails when an enabled dependency is absent from the SBOM, a packaged file has
no owner, a source or hash does not match, a selected license route is unresolved, required
notices/source/relinking material is missing, or a distribution-changing component lacks an
explicit frozen-decision change.

The historical PWS-0801 dependency-stage candidate uses project version `0.0.0`
and first-party licence `NOASSERTION`; the later installer uses scoped MIT.
This limited first-party deferral does not waive any third-party obligation. It is not a supported
portable installation, final product licence or public release. PWS-0802 owns the
installer and clean-machine runtime delivery. Future installer contents must be
reconciled again; this stage does not pre-approve unknown installer bytes.

### Reproduce the internal dependency stage

Use the reviewed application wheel/source and derived source/build outputs named by
[`app-inputs-v1.json`](release-reviews/app-inputs-v1.json) and the other review records. Original
archives must match their committed identities. A source-built input must already exist with its
reviewed output hash; the assembler does not run downloaded code or invent replacement outputs.
`tools/build_libtiff_no_jbig.py` and `tools/stage_lxml_source.py` are the bounded offline recipes for
the native DLL and approved source subset. A fresh application build uses the locked uv/uv-build
toolchain and requires a new application-input review if its bytes change.

The first-party application artifacts are produced with
`uv build --wheel --sdist --out-dir output/pws0801/reproduce/app-artifacts`.
`python -m tools.snapshot_release_tooling --output output/pws0801/reproduce/release-tooling.tar.xz`
creates the separate bounded, deterministic recipe/review source snapshot and reopens every
member. Its internal source manifest records exact bytes. The application-input review, final
reports, task state and bundle manifest are excluded from that snapshot to avoid circular hashes.
Record its actual name/hash and recipe in the application review before assembly. Two different
output filenames with identical source inputs must produce the same archive digest.

Prepare a clean constructor from the reviewed eight inputs: CPython 3.14.6 embeddable x64,
the five validator distributions (`attrs`, `jsonschema`, `jsonschema-specifications`,
`referencing`, `rpds-py`), `typing-extensions`, and the exact Git for Windows archive supplying
GPG and its 13 local DLLs. Their versions, sizes and hashes come from the CPython, validation,
Python-input and [GPG build reviews](release-reviews/gpg-build-components-v1.json).
The builder contains no application document libraries or development environment. It still
uses the reviewed external Microsoft runtime prerequisite; preparation neither installs nor
qualifies that prerequisite.

Preparation uses only standard-library code and reads the authenticated archives as data;
it executes none of their binaries. It publishes `builder/`, `builder-inventory.json` and
`preparation.json` together. The returned `preparation_sha256` is the caller's anchor: retain it
before using the builder and supply the same value to both verifications. Recomputing an
expected value from a changed receipt would not prove preservation.

Run from the trusted repository root with fresh output paths and an available bootstrap
CPython 3.14. The constant runner below supplies the reviewed repository explicitly on
`sys.path`: isolated `-I` ignores ambient Python paths, while `-B` prevents bytecode writes.
Only the module name and its ordinary CLI arguments come from `sys.argv`; no command is
constructed from document data. The example reuses candidate identity/time from the application
review and the final `resolved-inputs-r3` cache. Repeated `--artifacts`/`--cache` preparation
arguments and assembly `--cache-directory` arguments can name other reviewed repository
`output/` caches when reproducing elsewhere.

```powershell
$repositoryRoot = (Get-Location).Path
$moduleRunner = 'import runpy,sys; root=sys.argv.pop(1); module=sys.argv.pop(1); sys.path.insert(0,root); sys.argv[0]=module; runpy.run_module(module,run_name="__main__",alter_sys=True)'
$builderOutput = 'output/pws0801/reproduce/clean-builder'
$preparedJson = py -3.14 -I -B -c $moduleRunner $repositoryRoot tools.prepare_dependency_builder prepare `
  --output $builderOutput `
  --artifacts output/pws0801/resume-20261103/resolved-inputs-r3
if ($LASTEXITCODE -ne 0) { throw 'Builder preparation failed; preserve earlier evidence.' }
$prepared = $preparedJson | ConvertFrom-Json
$preparationSha256 = $prepared.preparation_sha256
if ($preparationSha256 -notmatch '^[0-9a-f]{64}$') { throw 'Missing preparation anchor.' }

py -3.14 -I -B -c $moduleRunner $repositoryRoot tools.prepare_dependency_builder verify `
  --output $builderOutput --expected-preparation-sha256 $preparationSha256
if ($LASTEXITCODE -ne 0) { throw 'Builder verification failed.' }

$builderPython = Join-Path $repositoryRoot "$builderOutput/builder/python/python.exe"
$gpgExecutable = Join-Path $repositoryRoot "$builderOutput/builder/gpg/gpg.exe"
$candidateReview = Get-Content packaging/release-reviews/app-inputs-v1.json -Raw | ConvertFrom-Json
& $builderPython -I -B -c $moduleRunner $repositoryRoot tools.build_dependency_stage `
  --artifacts output/pws0801/reproduce/inputs `
  --destination output/pws0801/reproduce/payload `
  --inventory-output output/pws0801/reproduce/inventory.json `
  --app-review packaging/release-reviews/app-inputs-v1.json `
  --qpdf-review packaging/release-reviews/qpdf-cli-v1.json `
  --gpg-executable $gpgExecutable `
  --candidate-id $candidateReview.candidate.id `
  --created-utc $candidateReview.candidate.created_utc `
  --cache-directory output/pws0801/resume-20261103/resolved-inputs-r3
if ($LASTEXITCODE -ne 0) { throw 'Dependency assembly failed.' }
& $builderPython -I -B -c $moduleRunner $repositoryRoot tools.release_evidence validate `
  --inventory output/pws0801/reproduce/inventory.json `
  --stage output/pws0801/reproduce/payload `
  --artifacts output/pws0801/reproduce/inputs
if ($LASTEXITCODE -ne 0) { throw 'Stage validation failed.' }
& $builderPython -I -B -c $moduleRunner $repositoryRoot tools.release_evidence freeze `
  --inventory output/pws0801/reproduce/inventory.json `
  --stage output/pws0801/reproduce/payload `
  --artifacts output/pws0801/reproduce/inputs `
  --output output/pws0801/reproduce/evidence
if ($LASTEXITCODE -ne 0) { throw 'Evidence freeze failed.' }

py -3.14 -I -B -c $moduleRunner $repositoryRoot tools.prepare_dependency_builder verify `
  --output $builderOutput --expected-preparation-sha256 $preparationSha256
if ($LASTEXITCODE -ne 0) { throw 'Builder changed during use.' }
```

The constructor actually executes the verified GPG executable from this owned 14-file closure
to check signed inputs. Its interpreter imports only the prepared validation stack and trusted
repository tooling, rather than `.venv` application packages. Verify before and after use with
the retained caller anchor. Preparation/verification are offline and do not execute the prepared
Python, GPG or downloaded application binaries; execution starts with the explicit clean-Python
constructor commands. Assembly is offline by default. Its `--fetch` option explicitly permits
acquisition of missing URL-backed inputs with reviewed identities; it cannot recreate missing
local source-build outputs. Signature verification remains required for signed inputs.

Serialize builder publication, assembly and native runtime probes with native-capable test
lanes in the same Windows user session. If publication reports `RELEASE_BUILDER_OUTPUT_BUSY`,
preserve prior evidence, let ordinary native lease cleanup finish, then retry with a fresh output
path. Do not change ACLs or weaken the native lease guard to make publication pass.

Freeze writes `resolved-inventory.json`, `bom.cdx.json`, `bom.spdx.json`,
`THIRD_PARTY_NOTICES.txt`, `input-verification.json`, the reopened `dependency-stage.zip` and
`SHA256SUMS`. Sidecars are outside the ZIP's exhaustive file inventory to avoid circular hashes.
Existing stages and freeze destinations are not replaced. Record the resulting exact hashes in
the [release evidence index](../docs/release/RELEASE_EVIDENCE.md) after verification; this guide
does not supply unobserved candidate results or installer facts.

## Family-specific packaging gates

- PWS-0111 admits exact PySide6-Essentials 6.11.1 plus transitive shiboken6 6.11.1 for the initial
  QtCore/QtGui/QtWidgets UI. The convenience meta-package is deferred, and the non-default Addons/
  Qt PDF renderer candidate remains scope-free and disabled. PWS-0801 must not copy the complete
  Essentials wheel blindly: construct a reviewed dynamic file subset with `qwindows`, only needed
  plugins and native dependencies, exact corresponding source, notices, hashes, SBOM entries, and
  preserved replacement or relinking ability. Addons and Essentials declare 64 identical shared
  namespace/type-support paths, so uninstalling Addons is not an acceptable packaging-pruning
  method; the release inventory must assign and stage every selected file explicitly.
- PWS-0105 selects pikepdf 10.10.0 with Pillow 12.3.0, lxml 6.1.1, packaging 26.2, and qpdf
  12.3.2; PWS-0201 additionally admits that exact Pillow build as the direct worker-only JPEG/PNG
  decoder. The historical baseline retains `planned` shipment state; the resolved stage supplies
  current native-file, licence, source and SBOM evidence. The maintainer approved omission of
  exactly the two unlicensed lxml Schematron resources on 2026-11-03 in
  [`lxml-exclusions-v1.json`](release-reviews/lxml-exclusions-v1.json). The corresponding
  [`source-subset review`](release-reviews/lxml-source-subset-v1.json) binds their source forms,
  including LF/CRLF equivalence. Both runtime and redistributed source omit those same contents;
  all remaining source-file bytes are preserved. No rights to omitted resources are inferred.
- Historical PWS-0801/R1/R2 canonical Microsoft VC runtime DLLs are excluded and supplied by an independently
  Microsoft-installed external runtime; its end-user terms do not grant redistribution. Only
  pikepdf's exact hash-renamed C++ runtime object is retained under its explicit upstream
  downstream-distribution notice and full Microsoft terms. The
  [native review](release-reviews/native-components-v1.json) records the two routes and their
  limits. Current PWS-0802 R3 instead uses the
  [acquired Community route](release-reviews/microsoft-community2022-v1.json) and
  scoped local recipient agreement for nine app-local destinations. The original
  baseline/reviews/notices are not rewritten. Qt/CPython wheel contents alone are
  not a Microsoft redistribution grant; clean packaged qualification remains separate.
- CI acquires qpdf only from the reviewed official Windows x64 ZIP, verifies the recorded SHA-256
  before extraction, checks version 12.3.2, and exposes its absolute path through
  `PWS_QPDF_EXECUTABLE`; it does not add qpdf to `PATH` or execute the unsigned installer.
- Keep OCRmyPDF, Tesseract, and all document-processing helpers in the network-denied worker with
  fixed executable paths. Ghostscript remains prohibited unless the required decision or approved
  commercial route exists. PWS-0204 admits OCRmyPDF 17.10.0 and Tesseract 5.5.3 from the signed
  MSYS2 UCRT64 package revision `5.5.3-1`. The historical 30-package, 39-file selection remains in
  [`tesseract-runtime-closure-v1.json`](tesseract-runtime-closure-v1.json). Production now uses
  [`tesseract-runtime-closure-v2.json`](tesseract-runtime-closure-v2.json): 38 files from 28 signed
  vendor packages and one independently source-built no-JBIG libtiff 4.7.2 provider. The
  [frozen native recipe](libtiff-no-jbig-build-v1.json) preserves the other codec imports and
  Windows I/O. The replacement DLL is not vendor-signed. A runtime root is acceptable only when
  it has exactly the v2 relative files, byte sizes and SHA-256 values,
  contains no reparse points, and has no unlisted files. The upstream Windows installer is rejected
  because its signer certificate expired before it was signed.
- Treat every Tesseract `.traineddata` file as a separate, versioned, hashed, licensed data
  component. The current 38-file base closure contains the pinned `tessdata_fast` English model from the
  signed MSYS2 `tesseract-data-eng 4.1.0-3` package. PWS-0205 adds German as a separate planned
  component from `tesseract-data-deu 4.1.0-3`; its archive, detached signature, and extracted model
  identities are frozen in both the dependency inventory and
  [`tesseract-language-pack-catalog-v1.json`](tesseract-language-pack-catalog-v1.json).
- Keep the canonical language-pack catalog byte-identical to the packaged copy under
  `src/paperworkshell/worker/client/`, and require both the built wheel and source distribution to
  contain that copy. Runtime
  admission checks the catalog fingerprint plus exact model size and SHA-256; it does not replace
  upstream detached-signature validation. Packaging must validate the signature against the
  reviewed MSYS2 key before extracting and staging a pack. This PWS-0205 update records the
  signature evidence but does not claim that cryptographic verification was re-performed here.
- Language packs are local, offline-prestaged resources. Neither the application nor worker may
  download them or gain network access for discovery. Restart after adding, replacing, or removing
  one. Missing or changed data stays unavailable until a verified copy is restored. PWS-0801 and
  PWS-0802 retain the exact release-package inclusion, install path, elevation, removal, and
  user-visible notice evidence.
- Preserve the historical baseline shipment state. The current resolved stage records selected
  native licence routes, original notices, corresponding source/replacement material and actual
  files. The [static-support review](release-reviews/libtiff-build-support-v1.json) identifies
  GCC exception and MinGW startup/support members in the rebuilt DLL; the
  [build-tool review](release-reviews/native-build-tool-components-v1.json) separately records
  its 19 unshipped build inputs. The uv/uv-build
  [review](release-reviews/build-tool-components-v1.json) labels vendor-declared all-target Rust
  inputs accurately rather than claiming every declared crate was compiled into Windows binaries.
- Exclude the complete unused `pi-heif` package and its HEIF native closure under
  [`runtime-omissions-v1.json`](release-reviews/runtime-omissions-v1.json), while retaining the lock
  resolution. The existing OCRmyPDF optional-import fallback supports PDF/JPEG/PNG without a source
  patch. Do not leave a partial HEIF decoder or enable an unsupported input format.
- Admit a renderer only after concrete security, license, fidelity, performance, and Windows
  packaging evidence is accepted and its exact artifacts are added to the inventory.

## Validation

```powershell
py -3.14 tools/dev.py validate-dependencies
py -3.14 tools/dev.py validate-release-inventory
py -3.14 tools/dev.py validate-schemas
py -3.14 tools/dev.py validate-docs
```

All validation is offline. It checks the committed evidence and references without downloading
packages, binaries, notices, or vulnerability data.
