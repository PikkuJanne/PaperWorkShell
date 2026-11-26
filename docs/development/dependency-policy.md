# Dependency, license, and redistribution policy

`PWS-0005` defines how PaperWorkShell admits, updates, packages, and reports third-party
components. It implements the dependency controls required by `PWS-D014`, `PWS-D015`,
`PWS-D016`, `PWS-D017`, `PWS-D018`, and `PWS-D034`.

This is an engineering admission policy, not legal advice. Unclear license or redistribution
rights are a release blocker and require maintainer review; uncertainty is never resolved by
silently omitting a component from the inventory.

## Authoritative records

- The versioned contract is
  [`schemas/dependency-inventory-v1.schema.json`](../../schemas/dependency-inventory-v1.schema.json).
- The complete checked-in policy-baseline example is
  [`packaging/dependency-inventory-v1.json`](../../packaging/dependency-inventory-v1.json).
- The canonical policy-rejected example is
  [`distribution-decision-bypass.json`](../../tests/fixtures/dependencies/v1/invalid/distribution-decision-bypass.json).
- Direct Python requirements and their resolution are recorded in `pyproject.toml` and
  `uv.lock`. The inventory does not replace either file.
- The separate
  [`resolved-dependency-inventory-v1` contract](../../schemas/resolved-dependency-inventory-v1.schema.json)
  records the exact Windows stage, consumed inputs, component/file owners and obligations.
  [ADR 0017](../adr/0017-release-dependency-staging.md) defines its PWS-0801 scope;
  [the task result](pws0801-result.md) records current verification and candidate provenance.

The JSON Schema proves the bounded shape of an inventory. Repository validation additionally
checks policy that JSON Schema cannot express safely, including unique component IDs, valid task
and decision references, relationship endpoints, admission-state consistency, renderer evidence,
and the distribution-changing decision gate.

## Two inventory profiles

The same component family appears at different levels of resolution during development. These
levels must not be confused.

### Policy baseline

`packaging/dependency-inventory-v1.json` is the reviewed repository baseline. It records:

- dependencies currently admitted for their listed development, build, test, validation, or CI
  scopes;
- frozen application-stack families whose concrete artifacts are intentionally deferred to their
  dependency-ready implementation tasks;
- renderer and tool candidates that have not been admitted; and
- components prohibited without a frozen-decision change.

In this profile, a supported minor line, a minimum external-tool version, or an environment
version check can describe contributor prerequisites. For each locked Python release, the baseline
duplicates the source-distribution digest when one is published and every universal or compatible
Windows x64 wheel digest from `uv.lock`; validation requires those reviewed records to agree
exactly. Every resolved Python dependency edge is also recorded. An edge between `evaluated` or
otherwise non-admitted components remains disabled even though its lock provenance is complete;
lock completeness does not activate product use. `uv.lock` remains the complete cross-platform
resolution authority. These development records are not proof of the files eventually staged in a
Windows release.

`approved` means only "admitted for every listed active scope." It does not approve another
scope, select an unmeasured renderer, or prove that a component is shipped. `candidate` names an
unselected alternative. `evaluated` identifies one concrete, pinned, hash-verified artifact under
measurement, with no active scope or admission. Its evidence names the exact artifact URL,
filename, Windows/platform-independent target, byte size, and SHA-256; Python evidence must also
match one exact `uv.lock` record. `planned` reserves a frozen or required family for
its named dependency-ready task without selecting an artifact. `deferred` records a reviewed family
that is intentionally outside the current implementation path. `requires-decision-change` means
the component must remain inactive and prohibited under current authority. A disabled relationship
documents a possible or deferred edge; it is never permission to install or invoke that edge.

### Resolved build or release inventory

A resolved inventory describes one actual target build. The v1 contract identifies itself as a
`policy-baseline`; it is not silently repurposed as a release schema. PWS-0801 uses the separate
resolved-inventory v1 contract. Before a release candidate is
accepted, that evidence resolves every baseline range, minor-line selection, minimum version,
environment probe, null package URL, and representative hash that applies to the build to the exact
consumed artifact. It records:

- exact interpreter, package, executable, native-library, and data versions;
- the precise vendor or distribution revision, not only an upstream family version;
- artifact filename, reviewed source, target platform, byte size, and verified SHA-256;
- the selected license route and its exact notice, source, and relinking obligations;
- direct, transitive, embedded, and optional-but-enabled relationships;
- the final packaging disposition and all redistributed files; and
- package URLs and license identifiers suitable for the generated SBOM.

`uv.lock` supplies the Python resolution and expected hashes. It does not enumerate every DLL,
plugin, executable, model file, notice, or library embedded inside a wheel. The packaged-file
inventory supplies the files actually staged for Windows. Upstream release manifests can explain
embedded components, but cannot substitute for inspection of the chosen artifacts. A release
fails if these sources disagree or if a packaged file has no reviewed owner.

PWS-0005 establishes the baseline format and rules. The PWS-0801 assembly and freeze tools generate
the resolved inventory, compare it with the actual stage and reopened package, and produce
CycloneDX 1.6, SPDX 2.3 and notice sidecars. They fail closed on changed inputs or contents. The
internal `0.0.0` dependency candidate is not a supported portable installation or public release.
Installer identity, signing, final product terms and clean prerequisite installation remain
PWS-0802 work; later candidates must regenerate evidence for their actual files.

## Admission states and packaging state

Admission, activation, and shipment answer different questions:

- `admission_status` says whether current project authority permits the component.
- `active_scopes` says where an approved component is currently used.
- `packaging.ship_state` records the present or proposed disposition, including excluded, planned,
  candidate, bundled, external-runtime, or prohibited use.
- `relationships[].enabled` says whether a dependency edge belongs to the active baseline closure.

Changing any one of these can require a fresh review of the others. In particular, moving an
external developer tool into the application, enabling a previously optional executable, or
shipping a dependency in a new process is a new admission decision. A component must not be
marked approved merely so a concrete candidate can be benchmarked. Once an exact candidate
artifact has been selected for measurement, record it as `evaluated` with its version and integrity
evidence, keep its packaging state `candidate`, leave active scopes empty, and put the measurements
in the selecting task's evidence or ADR. Admission follows review of the component and each intended scope. Codex owns
ordinary component selections within the approved requirements under [AGENTS.md](../../AGENTS.md).

## Adding a dependency

The dependency owner performs these steps before changing an import, lock, installer, workflow,
or runtime executable list.

1. **State the capability.** Explain the product or engineering need for the component.
   Record why the standard library, existing dependencies, or a smaller component cannot provide
   it. Convenience alone is insufficient.
2. **Define the boundary.** Classify the component as direct, transitive, external, candidate, or
   embedded. Identify every active scope, process, executable, native library, data file, network
   need, and whether it processes untrusted documents.
3. **Select evidence, not a floating name.** Review the upstream project and maintenance signal,
   then identify the exact release, commit, archive, wheel, installer, or data revision under
   evaluation. Never trust a filename or package display name as provenance.
4. **Review security and operations.** Document parser and native-code exposure, worker versus GUI
   placement, resource limits, cancellation, cleanup, executable discovery, update behavior, and
   the adapter and contract tests required. Anything that parses or transforms user documents
   runs only in the network-denied worker.
5. **Choose the license route.** Record an SPDX expression or a top-level defined `LicenseRef`, an
   immutable license source where available, the route actually relied upon in
   `license.selected_route`, and the resulting notice, corresponding-source, relinking, or other
   distribution work. Keep `selected_route` null while no route is selected. An unresolved list of
   possible commercial, permissive, weak-copyleft, and strong-copyleft routes is not release
   approval.
6. **Review Windows packaging.** Inspect wheel, archive, DLL, plugin, executable, model, and runtime
   closure. Record architecture support, binary provenance, signing evidence when available,
   expected installer size, and every required redistribution asset.
7. **Pin and verify.** Add the exact declaration and lock entry. Capture the expected SHA-256 from
   a reviewed channel and verify it before extraction or execution. Record the artifact actually
   consumed on the Windows target, not only a source archive with the same version.
8. **Test the boundary.** Add adapter and contract tests for untrusted-content processors. Exercise
   success and at least one failure path, plus malformed input, limits, cancellation, timeout,
   cleanup, command construction, egress, and sanitized logging where applicable.
9. **Update the complete record.** Increment the inventory revision and review date; update the
   component, relationships, lockfile, license/notice inputs, packaging notes, and SBOM fields in
   the same change. Run dependency, schema, documentation, and relevant quality checks.

No dependency is admitted solely because it is already installed on a contributor machine or is
transitively available through another package.

## Updating or removing a dependency

An upgrade repeats the admission review; a version bump is not a clerical lockfile refresh.

- Review the direct and transitive diff, release notes, maintenance/security signal, license
  changes, binary layout, new executables, and new optional features.
- Regenerate `uv.lock` from declared constraints and reject unexplained additions, removals,
  source changes, or platform-marker changes.
- Recheck the Windows artifact, embedded component, and packaged-file closure. A familiar package
  name does not make a replacement wheel or native archive equivalent.
- Update version, source, hash, package URL, maintenance evidence, relationships, obligations,
  and inventory revision together.
- Re-run adapter, hostile-input, limit, and packaging tests affected by the change.
- Remove a component record from the active closure only after the lock and packaged-file
  inventories prove that no copy remains. Historical decisions and released SBOMs remain
  immutable release evidence.

Emergency security work may shorten scheduling but may not bypass source, hash, license,
distribution, worker-boundary, or regression review. Follow the
[emergency dependency security-update policy](emergency-security-updates.md), established by
PWS-0801. The policy does not claim that signing or authenticated updates are implemented.

## Pinning, sources, and hashes

The required evidence depends on how the component enters the build:

| Input | Development baseline | Release requirement |
|---|---|---|
| Python package | Exact lock resolution with lock hashes | Exact consumed wheel or source build, its hash, and installed/package closure |
| GitHub Action | Full 40-character commit | Same immutable commit plus reviewed upstream/repository identity |
| Downloaded native archive or installer | Exact version and expected SHA-256 | Exact vendor revision, artifact URL/name/size/hash, signature evidence when available, and extracted closure |
| Source build | Exact tag or commit and source hash | Source revision/hash, patch set, build recipe/toolchain, output hashes, and embedded dependency closure |
| Interpreter | Supported exact minor may be a contributor baseline | Exact patch/distribution artifact and hash for the release |
| OCR or other data | Unselected family/repository may be documented | One record per shipped file with family, language, filename, revision, size, hash, and license |
| External prerequisite | Capability/minimum version may be documented | Must remain absent from the product package, or be re-admitted as a shipped component |

A release must not use `minimum`, `minor-line`, or `environment-version` as proof of its packaged
artifact. Computing a hash after an unreviewed download proves only consistency with that same
download. The expected digest or signed source metadata must be reviewed independently and
committed before the artifact is trusted.

Schema v1 deliberately treats component IDs, upstream project/license identity, admission scope,
ship state, binary capability, security boundary, and SBOM name/type/supplier/closure source as a
closed reviewed set. Adding a component, moving one into a new scope, or changing one of those
identities requires updating the inventory and validator policy together. This prevents a record
from self-relabeling its source, license, runtime risk, or generated SBOM identity while retaining
an already trusted component ID.

License links for release evidence should resolve to a tag, commit, release artifact, or locally
hashed license text. Floating `main` or `master` pages are background references, not immutable
proof of the terms attached to an artifact.

## Distribution-changing dependencies

AGPL and any other component whose selected use would change PaperWorkShell's distribution terms
is prohibited unless the maintainer completes the frozen-decision change procedure in
`DECISIONS.md`. An ordinary ADR, dependency pull request, transitive edge, optional fallback, or
runtime discovery does not grant that approval.

Until approval exists, the inventory must use `requires-decision-change`, leave active scopes
empty, mark shipment `prohibited-without-decision-change`, and keep all relationships disabled.
The semantic validator rejects a distribution-changing component that is approved, active,
shippable, or reachable through an enabled relationship. Setting
`license.distribution_altering` to `false` is not a waiver; reviewers must evaluate the selected
license route together with linking, bundling, invocation, and shipment.

If a commercial license is proposed as a non-distribution-changing route, the maintainer must
confirm that its terms, scope, provenance, and redistribution rights cover the exact artifacts.
Credentials, contracts, license keys, and purchase records stay outside the repository.

A copyleft developer tool can remain an approved external prerequisite only when it is not linked,
bundled, or invoked by the product and its distribution note says so. Moving it into another scope
starts a new review.

## Frozen and evaluated component families

### PySide6 and Qt

`PWS-D014` selects PySide6 for the desktop UI; it does not approve every wheel in the Qt for Python
family. PWS-0111 admits the smallest supported wheel set needed by the initial Widgets UI:
PySide6-Essentials 6.11.1 as an exact direct runtime dependency and its exact transitive shiboken6
6.11.1 dependency. Product code may import only `PySide6.QtCore`, `PySide6.QtGui`, and
`PySide6.QtWidgets`, and only from the `ui` layer. Importing the `PySide6` root or any other Qt for
Python module is denied by the executable boundary policy.

The convenience PySide6 meta-package and PySide6 Addons must not be admitted blindly. Addons can
bring QtPdf/QtPdfWidgets and a PDFium payload into the installation. Qt PDF is an unselected
renderer candidate under `PWS-D017`; it cannot enter the GUI or release package as an incidental
UI dependency. Addons, Qt PDF, or their PDFium closure require an explicit capability need,
`PWS-0106` renderer evidence where applicable, worker-only document parsing, exact binary
inventory, and license review.

PWS-0106 locks PySide6-Addons 6.11.1 in the non-default `renderer-evaluation` group solely to
measure that fallback. The exact reviewed Windows x64 artifacts are PySide6-Addons 6.11.1
(168,816,308 bytes, SHA-256
`0d13c4dfd671b050a48e4f8d8ddc724b7248f9c0437e7fc47fdf316278572923`),
PySide6-Essentials 6.11.1 (77,549,267 bytes, SHA-256
`63311bd48e32c584599ab04b9ef7c324082374cd2c9fa533f978fb893bb47e40`), and shiboken6 6.11.1
(1,223,647 bytes, SHA-256
`c2c6863aa80ec18c0f82cea3417837b279cdc60024ac17123461dc9042577df7`). PyPI publishes wheels,
not source distributions, for these three releases. PWS-0111 reclassifies Essentials and shiboken6
as approved runtime inputs with `planned` shipment state and enables their exact dependency edge.
The generic PySide6 convenience meta-package is `deferred` and not shipped. Addons and Qt PDF stay
`evaluated`, scope-free, candidate-only, and disabled; neither is a selected product dependency.

The published Addons and Essentials `RECORD` files overlap on 64 identical PySide namespace and
type-support paths, including `PySide6/__init__.py`. Consequently, removing the evaluation group
from an in-place environment can delete the shared bootstrap even while Essentials remains
installed, which prevents Qt DLL-path initialization. Qt renderer evaluation should use a
disposable environment. Returning an existing environment to the ordinary dependency closure
requires recreating it or explicitly reinstalling both selected packages with
`uv sync --locked --reinstall-package pyside6-essentials --reinstall-package shiboken6`. This
upstream split-wheel ownership behavior is not permission to retain Addons in the product runtime.

Qt PDF 6.11.1 is bound to the exact Addons wheel because Qt does not publish a QtPdf-only Python
artifact. Its embedded PDFium snapshot is not the same inventory identity as the PDFium build
bundled by pypdfium2 5.12.1; the inventory therefore does not connect Qt PDF to that exact PDFium
component. The wheel hash binds evaluation bytes, while exact Qt PDF DLL, embedded-source, SBOM,
license, and third-party-notice reconciliation remains required before any admission or shipment.

PWS-0111 selects the LGPL-3.0-only community route for the used Essentials/shiboken QtCore,
QtGui, and QtWidgets surface. The upstream wheel remains much broader: its installed record has
2,492 paths, 198 DLLs, 22 binding modules, plugins, QML content, and development tools. Admission
therefore does not approve redistributing the complete wheel. PWS-0801 must build and reconcile a
file-by-file dynamic release subset, including the Windows `qwindows` plugin and only required
plugins, exact corresponding PySide/Qt source, notices, third-party licenses, hashes, SBOM entries,
and replacement or relinking information. Static or opaque one-file packaging is not presumed
compliant. A commercial route would require separately approved maintainer-controlled terms. See
[ADR 0006](../adr/0006-pyside6-ui-runtime.md) for the artifact and source evidence.

### Pillow image decoder

[ADR 0010](../adr/0010-image-decoder-selection.md) records the retrospective Pillow/Qt comparison
and the supported color/frame profile. The exact native members and embedded-component evidence
are recorded in [the Pillow runtime companion](../../packaging/pillow-runtime-closure-v1.json).
`tools/dev.py validate-pillow-runtime` checks that reviewed record, the lock and main inventory,
and the installed Windows wheel's RECORD and actual native/licence bytes without loading optional
codec extensions. Changes to that record require explicit evidence review and its validator pin.

PWS-0201 promotes the already locked Pillow 12.3.0 Windows x64 wheel from a pikepdf transitive
dependency to the direct worker-only JPEG/PNG decoder. The standard library is sufficient for the
bounded preliminary framing checks but not for complete entropy/deflate decode, EXIF orientation,
CMYK conversion, palette/alpha handling, or deterministic resampling. The exact version is checked
before source access and remains behind `paperworkshell.imaging.pillow_decoder`; UI and application
modules receive only closed domain models and validated opaque RGBA8 bytes.

The inventory retains the exact wheel/source hashes, MIT-CMU route, native-code and untrusted-input
classification, disabled optional libimagequant proof, and PWS-0801 codec/DLL/notice/SBOM work.
The companion binds all eight native extensions and the wheel licence file. It distinguishes
wheel-notice provenance from upstream recipe-only optional AVIF subdependency versions, and keeps
external CPython/Windows/CRT and optional font-runtime obligations explicit. It is development
admission evidence; complete shipped-file/source/notice reconciliation is still required. It
adds no runtime network need. Moving Pillow into another process, enabling another codec component,
or changing the selected artifact requires a fresh dependency and parser-boundary review.

### pikepdf and qpdf

`PWS-D015` fixes the structure layer as pikepdf/qpdf behind the internal PDF adapter. `PWS-0105`
admits pikepdf 10.10.0, lxml 6.1.1, packaging 26.2, and the then-transitive Pillow 12.3.0 from the exact `uv.lock`
resolution. The selected `pikepdf-10.10.0-cp314-abi3-win_amd64.whl` is 3,414,578 bytes with
SHA-256 `66567891906906e8733f1ef19cd133f46f34349cb415c858ee64db807d82413c`.
It embeds qpdf 12.3.2 and the Microsoft C++ runtime; security tests bind the inspected `_core.pyd`,
qpdf DLL, and runtime DLL hashes so a wheel replacement cannot silently change the native closure.

Standalone structural checking uses qpdf 12.3.2 from the official Windows x64 ZIP. CI downloads
`qpdf-12.3.2-msvc64.zip` to runner temporary storage, verifies SHA-256
`8941870a604e7c87ed24566b038d46c24ce76616254d2383c578f60c0677f202` before extraction, checks
the executable's exact version, and exports only the absolute `PWS_QPDF_EXECUTABLE` path. The
verified test installation is extracted under `Program Files` so its inherited policy exercises
the installed-resource AppContainer path; a separate live test copies that closure to temporary
storage and proves the launcher's bounded access grant is removed afterward. The ZIP route avoids
executing the unsigned NSIS installer. qpdf uses the selected Apache-2.0 license route; its official
MSVC closure reports libjpeg 9f, OpenSSL 3.6.0, zlib 1.3.1, and Microsoft CRT files as
release-inventory inputs.

pikepdf/qpdf load and process untrusted PDFs only in the worker. Application and UI modules never
receive third-party objects or raw exceptions. Structural checking through an executable uses a
fixed reviewed path and argument array, never ambient discovery or a shell string.

The policy baseline retains its historical `planned` shipment facts. The current resolved stage
uses an explicitly reviewed subset. On 2026-11-03 the maintainer approved omission of exactly
`RNG2Schtrn.xsl` and `XSD2Schtrn.xsl`, which the selected lxml wheel labels unlicensed; the
[approval record](../../packaging/release-reviews/lxml-exclusions-v1.json) binds their exact paths
and hashes. This grants no rights to those resources. The
[corresponding-source review](../../packaging/release-reviews/lxml-source-subset-v1.json) and
`tools/stage_lxml_source.py` omit the same two contents from the redistributed source artifact,
preserve all remaining source-file bytes, and distinguish source LF from wheel CRLF hashes.
The original sdist remains an audited input outside the distributed source subset. A future wheel,
broader omission or change of use requires a fresh review.

Canonical Microsoft VC runtime DLLs are omitted from the stage and supplied by a separately
Microsoft-installed external runtime. Its end-user agreement is not a redistribution grant.
The exact hash-renamed C++ runtime DLL in pikepdf is retained under pikepdf's explicit downstream
distribution route with the full applicable Microsoft terms; that route does not authorize copying
canonical Qt/CPython runtime files. The
[native review](../../packaging/release-reviews/native-components-v1.json) records both routes.
Protective distributor/end-user terms and clean prerequisite installation remain PWS-0802
qualifications. Every qpdf/Pillow codec, runtime object, notice and source obligation still belongs
in the resolved inventory, independently of a top-level wheel licence label.

### Overlay PDF and font stack

PWS-0303 promotes the already resolved fpdf2 2.8.8, fonttools 4.63.0, and uharfbuzz 0.56.0
packages to exact direct worker dependencies. fpdf2 owns the overlay-page PDF boundary, fonttools
provides deterministic metrics and subsetting for the one reviewed font, and uharfbuzz provides
deterministic Unicode shaping. Their versions and Windows-compatible artifact hashes are unchanged
from the OCRmyPDF 17.10.0 lock closure; the direct declarations make independent overlay use and
its redistribution obligations explicit.

The only admitted overlay font is `NotoSans-Regular.ttf` version 2.007, consumed from the exact
OCRmyPDF wheel rather than discovered from Windows. It is 556,216 bytes with SHA-256
`2ec33f84606cbaa0a1a944488e14f97faf2f6a25ecdd8354f5358f06da13c7d9`. The reviewed file has
3,053 Unicode cmap entries and covers the representative Latin, Greek, Cyrillic, and Devanagari
strings exercised by PWS-0303. It does not cover every Unicode script or emoji; unsupported glyphs must fail explicitly,
never substitute an ambient font or silently render a missing-glyph box. Checkmarks, crosses, and
other overlay marks use vector primitives rather than depending on absent symbol glyphs.

Noto Sans is a separately inventoried direct data component and remains contained by OCRmyPDF for
artifact ownership. It is OFL-1.1 and may be embedded or subset, but the redistributed source font
must retain its copyright and complete license notice. The checked-in notice is
[`NotoSans-Regular-2.007-OFL-1.1.txt`](../../packaging/licenses/NotoSans-Regular-2.007-OFL-1.1.txt).
fonttools uses MIT terms and uharfbuzz uses Apache-2.0. fpdf2 uses LGPL-3.0-only and includes the
separately inventoried ICC profile terms, so PWS-0801 must provide notices, corresponding source,
and a replaceable/relinkable library layout in addition to verifying every packaged hash and SBOM
edge. None of these components needs runtime network access, and all remain worker-only or
data-only.

PWS-0605 reuses the existing pinned fonttools 4.63.0 dependency specifically through
`fontTools.ttLib` inside `pdf.redaction_sanitization`. The hardened derivative profile must parse
and rebuild supported embedded font tables so document-derived font names and metadata cannot
survive as opaque bytes while approved glyphs and searchable OCR text remain usable. The
standard library and pikepdf do not implement that font-table capability. This adds no package,
version change, native binary, executable, or network requirement; the existing MIT license,
locked artifacts, inventory and SBOM inputs continue to apply. Untrusted font parsing stays in
the isolated bounded document worker and unsupported fonts fail closed. The static architecture
checker allows only this exact adapter and `fontTools.ttLib` prefix; fontTools sibling packages
and every other adapter remain subject to their existing rules.

### OCRmyPDF, Tesseract, and Ghostscript

`PWS-D016` fixes OCR orchestration as OCRmyPDF with Tesseract behind the OCR adapter. `PWS-0204`
selects OCRmyPDF 17.10.0 and its exact `uv.lock` closure for worker-only use. The supported route
forces ordinary PDF output, the pypdfium rasterizer, the fpdf2 text renderer, optimization level
zero, and one job. The adapter supplies a fixed Tesseract path through a project-owned plugin;
neither OCRmyPDF nor the worker may discover executables from ambient `PATH`.

Ghostscript is not admitted as an implicit fallback, ambient executable, optional optimizer, or
PDF/A provider. Its current route is distribution-changing or requires unreviewed commercial
terms, so it remains prohibited and its relationship disabled. The OCR adapter and packaged
environment must prevent accidental PATH discovery and include a test that the supported OCR path
does not invoke Ghostscript. Enabling it requires the decision-change or approved commercial-route
process above.

The admitted engine is Tesseract 5.5.3 from the signed MSYS2 UCRT64 repository package
`mingw-w64-ucrt-x86_64-tesseract-ocr 5.5.3-1`. Its 6,509,584-byte package SHA-256 is
`7e797396b34e2f0c78c9b9ff4b90d66c068c053d60bded81fc5232892b2b1caf`; its detached signature
SHA-256 is `039fb4d142caa2471f74ed4205b5b7925f4d80f7e05806b558a32ee2c62557bf`.
Acquisition must validate the signature with the reviewed MSYS2 keyring and expected signer
fingerprint `5F944B027F7FE2091985AA2EFA11531AA0AA7F57` before extraction. A digest alone does not
substitute for that signature verification.

PaperWorkShell does not install or stage the complete repository packages. The historical
[`packaging/tesseract-runtime-closure-v1.json`](../../packaging/tesseract-runtime-closure-v1.json)
records the recursive PE import closure selected from 30 exact signed packages: one executable,
34 DLLs, three output configs, and the English model, 39 files and 27,588,734 bytes in total. Each
entry binds its safe relative path, byte size, SHA-256, and owning package. It remains unchanged
for accepted PWS-0204 evidence and the historical redistribution audit; production no longer admits
that JBIG-dependent profile.

Production uses the separately fingerprinted
[`tesseract-runtime-closure-v2.json`](../../packaging/tesseract-runtime-closure-v2.json): 38 files
from 28 signed vendor packages and one source-built provider. PWS-0801 rebuilds libtiff 4.7.2 with
JBIG disabled, preserving the other selected codec imports and Windows I/O. The
[frozen recipe](../../packaging/libtiff-no-jbig-build-v1.json) binds the exact source and 19 build
inputs; the new DLL is not MSYS2-signed. The
[selected static-support review](../../packaging/release-reviews/libtiff-build-support-v1.json)
records the actual GCC exception and MinGW startup objects, full terms and corresponding source.
The [external build-tool review](../../packaging/release-reviews/native-build-tool-components-v1.json)
records the unshipped compiler/CMake/Ninja inputs separately. Private use of those build tools
does not transfer their aggregate archive licence labels to the application.

Before granting access or launching Tesseract, packaging/runtime validation rejects missing files, extra files,
reparse points, case-colliding names, and any size or digest mismatch. The executable is exactly
`bin/tesseract.exe`; `TESSDATA_PREFIX` is exactly `share/tessdata`. Training tools, headers,
libraries not reached by the selected executable, and all other package files are excluded.

The upstream `tesseract-ocr-w64-setup-5.5.3.20260724.exe` installer is explicitly rejected even
though its SHA-256 (`bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4`)
matches the GitHub asset digest. Its Universität Mannheim Authenticode signer certificate expired
in 2023; a 2026 timestamp made with that expired signer does not establish a valid trusted
signature. A locally installed executable or any alternative distribution remains only capability
evidence, not an approved runtime input.

The historical baseline shipment state remains `planned`; the resolved stage supplies its own
current file, licence, source/replacement and SBOM evidence. OCRmyPDF's fonts and ICC profile are
separate components. The complete unused `pi-heif` package and its native HEIF closure are excluded
under the [runtime-omissions review](../../packaging/release-reviews/runtime-omissions-v1.json).
The lock retains the upstream resolution; OCRmyPDF's existing optional-import fallback supports
the selected PDF/JPEG/PNG path without modifying its source. A partial HEIF installation is not
allowed. None of these staging choices weakens the closed worker runtime or grants runtime
network access.

### OCR language data

Trained data is executable-adjacent input and receives component-level provenance. Every shipped
`.traineddata` file records its repository and model family, language, exact revision, filename,
byte size, SHA-256, and license evidence. Fast, best, and legacy-capable model families are not
interchangeable versions of one opaque language pack.

The initial English data is admitted from the signed MSYS2 UCRT64 package
`mingw-w64-ucrt-x86_64-tesseract-data-eng 4.1.0-3`. The package SHA-256 is
`543111e842aeebe248839902945b3883f9bf79b58787a1696d6ac211c13f7029`, its signature SHA-256 is
`380afbdc4fccc03ddac5944d63d8b8a86b1ad87623b39818207bfed6ab7d9d87`, and the staged
`eng.traineddata` SHA-256 is
`7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`.
It is the Apache-2.0 `tessdata_fast` 4.1.0 model family, not an interchangeable `best` or legacy
model.

PWS-0205 admits one modular German component from the signed MSYS2 UCRT64 distribution package
`mingw-w64-ucrt-x86_64-tesseract-data-deu 4.1.0-3`. It is the Apache-2.0 `tessdata_fast` 4.1.0
model family. The package archive is 816,507 bytes with SHA-256
`0d1bfa3838617fa0a6c439da638926e50f1186b87a247b3dd5ab3eac25887e41`; its 566-byte detached
signature has SHA-256 `448ee3de17ec1b9592455d85a678ff97e73db603b1b3bf1786af5d8a0c311542`;
and the extracted 1,525,436-byte `deu.traineddata` has SHA-256
`19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d`.

The canonical
[`packaging/tesseract-language-pack-catalog-v1.json`](../../packaging/tesseract-language-pack-catalog-v1.json)
and its byte-identical packaged copy bind the optional model and its source evidence. Runtime
admission authenticates the catalog bytes, then checks the locally staged model's exact relative
path, size, and SHA-256. Runtime checks do not authenticate the upstream distribution package: release
packaging must still validate its detached signature against reviewed MSYS2 signer fingerprint
`5F944B027F7FE2091985AA2EFA11531AA0AA7F57` before extraction. The committed metadata records the
required upstream evidence; it does not claim that the detached signature was cryptographically
reverified during PWS-0205.

Installation is verified offline prestaging only. The application and worker do not download
language data, do not use ambient Tesseract discovery, and receive no network authority for pack
discovery or OCR. Close and restart PaperWorkShell after adding, replacing, or removing a pack so
the next discovery reads one complete stable resource set. A missing pack is reported as not
installed; a size/hash mismatch makes the runtime invalid. Both cases provide local remediation and
leave case data unchanged, with no silent download or language fallback. PWS-0801 and PWS-0802
retain the exact package inclusion, staging path, elevation, repair/removal, and user-visible
license/notice behavior for the release candidate.

### PDF renderer candidates

`PWS-D017` and `PWS-0106` require selection of an end-to-end renderer option, not approval of a
brand name in isolation. Each concrete integration option is pinned for evaluation and assessed on
the same synthetic corpus and bounded Windows profile for:

- **security:** native parser surface, scripting/features, update history, worker isolation,
  malformed-input behavior, limits, and contract-test coverage;
- **license:** selected license route, embedded components, notices, source/relinking obligations,
  and any decision gate;
- **fidelity:** crop/media boxes, rotation, transparency, CMYK, fonts, malformed files, and golden
  rendering results;
- **performance:** bounded startup, per-page latency, peak memory, cancellation, and temporary
  storage under the versioned constrained resource policy; and
- **Windows packaging:** supported architectures, artifact provenance/signatures, native closure,
  installer size, update path, and notice delivery.

An approved renderer has evidence for all five criteria; `blocked`, `concern`, or `not-measured`
values are not selection evidence. The selecting ADR records exact benchmark artifacts and results.
After selection, update the inventory, lock data, relationships, packaged-file expectations, and
SBOM inputs together. Renderer-specific types remain inside the worker adapter.

PWS-0106 selects pypdfium2 5.12.1 with default PDFium 152.0.7947.0 for the worker renderer. The
accepted [ADR 0005](../adr/0005-pdf-renderer-selection.md) binds the exact clean revision, installed
`RECORD` checks, 1,015-file staged-closure digest, analytical goldens, stable hostile outcomes, and
same-corpus pypdfium2/Qt measurements. pypdfium2 passed both required semantic modes; Qt PDF's
public API could not satisfy the distinct MediaBox mode. The choice also avoids making the broad
PySide6-Addons/Essentials closure and unresolved Qt redistribution route part of the product
renderer. Qt PDF remains evaluated, non-default, scope-free, and disabled. Selection admits the
exact pypdfium2/PDFium worker dependency but does not complete the release-package notice, file,
redistribution, or SBOM reconciliation owned by PWS-0801.

## SBOM and notice generation

The inventory is designed to feed CycloneDX JSON and SPDX JSON, but the baseline JSON is not itself
an SBOM. `PWS-0801` combines:

1. the reviewed component and relationship records;
2. exact Python resolution and hashes from `uv.lock`;
3. exact upstream inventories for embedded native distributions;
4. the hashed files in the final Windows staging directory; and
5. the selected license texts, notice files, corresponding-source locations, and relinking
   materials.

The result includes the PaperWorkShell root component, exact package URLs where defined, direct and
transitive relationships, artifact hashes, suppliers, selected SPDX license expressions, and
definitions for every `LicenseRef`. Build/CI-only and external prerequisites remain distinguishable
from shipped runtime components. The human-readable third-party notice bundle and both SBOM formats
must describe the same resolved build.

Release reconciliation fails on an unowned packaged file, an enabled dependency missing from the
SBOM, a declared shipped component absent from the package without explanation, a hash mismatch,
an unresolved license route, or an unapproved distribution-changing component. SBOM generation
does not authorize a dependency; it reports components already admitted through this policy.

The [PWS-0801 result](pws0801-result.md) retains the original JBIG/lxml audit and records the
replacement closure and approved omission. The old audit still correctly rejects runtime v1;
it is not a validator for the new stage. `tools.build_dependency_stage` assembles authenticated
explicit inputs, and `tools.release_evidence validate` reconciles current review fingerprints,
input identities, obligations and actual files before `freeze` writes matching sidecars and a
deterministic reopened ZIP. The [packaging guide](../../packaging/README.md#reproduce-the-internal-dependency-stage)
gives the exact commands. A dirty worktree is identified by its exact application source snapshot
and reviewed tooling/input hashes; HEAD alone is not complete provenance. Final candidate hashes
belong in the release evidence index only after this verification passes.

The original upstream notice bytes include public author and licence contacts. Their exact
reviewed hashes and provenance are retained in
`packaging/release-reviews/upstream-attribution-privacy-v1.json`. Repository privacy validation
recognizes only those immutable blobs for email attribution; document fixtures, altered notices,
other personal-data patterns and unapproved document identities retain their strict checks.
See the [quality boundary](quality-gates.md#synthetic-fixture-validation).

## Verification

Run the repository-defined commands rather than one-off validators:

```powershell
py -3.14 tools/dev.py validate-dependencies
py -3.14 tools/dev.py validate-release-inventory
py -3.14 tools/dev.py validate-schemas
py -3.14 tools/dev.py validate-docs
py -3.14 tools/dev.py check
```

The canonical complete example must pass. The canonical rejected example deliberately attempts to
bypass the distribution-changing decision gate and must fail with the documented stable policy
code. Other negative fixtures cover malformed structure, duplicate keys, unsupported versions,
invalid references, inconsistent state, relationships, and incomplete renderer evidence.

Validation is offline. It must not fetch license pages, package indexes, binaries, or vulnerability
data during the ordinary quality suite. Network-backed research and artifact retrieval are explicit
review activities; their accepted identities and hashes are committed as evidence before release.
