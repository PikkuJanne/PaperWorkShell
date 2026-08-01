# Dependency, license, and redistribution policy

`PWS-0005` defines how PaperWorkShell admits, updates, packages, and reports third-party
components. It implements the dependency controls required by `PWS-D014`, `PWS-D015`,
`PWS-D016`, `PWS-D017`, and `PWS-D034`.

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
- A Windows packaged-file inventory is required for a release. It is not yet produced by
  `PWS-0005`; final reconciliation and SBOM generation belong to `PWS-0801`.

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
duplicates the source-distribution digest and every universal or compatible Windows x64 wheel
digest from `uv.lock`; validation requires those reviewed records to agree exactly. `uv.lock`
remains the complete cross-platform resolution authority. These development records are not proof
of the files eventually staged in a Windows release.

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
`policy-baseline`; it is not silently repurposed as a release schema. `PWS-0801` must define the
versioned resolved-inventory or generated-evidence contract it needs. Before a release candidate is
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

PWS-0005 establishes the baseline format and rules. `PWS-0801` must generate the resolved release
inventory, compare it with the package contents, and produce the final license and SBOM outputs.

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
in the selecting task's evidence or ADR. Approval is a later explicit transition.

## Adding a dependency

The dependency owner performs these steps before changing an import, lock, installer, workflow,
or runtime executable list.

1. **State the capability.** Explain why the capability is required by a dependency-ready task.
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
distribution, worker-boundary, or regression review. The formal emergency release process is a
`PWS-0801` deliverable.

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
family. The first UI dependency change must prefer the smallest supported wheel set needed for the
UI, expected to be PySide6 Essentials plus its matching Shiboken package, and must inspect the
exact wheel dependency and file closure.

The convenience PySide6 meta-package and PySide6 Addons must not be admitted blindly. Addons can
bring QtPdf/QtPdfWidgets and a PDFium payload into the installation. Qt PDF is an unselected
renderer candidate under `PWS-D017`; it cannot enter the GUI or release package as an incidental
UI dependency. Addons, Qt PDF, or their PDFium closure require an explicit capability need,
`PWS-0106` renderer evidence where applicable, worker-only document parsing, exact binary
inventory, and license review.

The selected Qt license route must be recorded. A community LGPL route requires the applicable
notices, corresponding library source, and a packaging design that preserves the required
replacement or relinking ability. Static or opaque one-file packaging is not presumed compliant.
A commercial route requires separately approved maintainer-controlled terms.

### pikepdf and qpdf

`PWS-D015` fixes the structure layer as pikepdf/qpdf behind the internal PDF adapter. Concrete
versions remain unselected until the adapter task. The chosen pikepdf Windows artifact must be
inspected to determine whether it embeds qpdf and runtime libraries. Inventory embedded qpdf
exactly once, with the version, license route, notices, hashes, and DLL or executable provenance
that match the chosen wheel or separate distribution.

pikepdf/qpdf load and process untrusted PDFs only in the worker. Application and UI modules never
receive third-party objects or raw exceptions. Structural checking through an executable uses a
fixed reviewed path and argument array, never ambient discovery or a shell string.

### OCRmyPDF, Tesseract, and Ghostscript

`PWS-D016` fixes OCR orchestration as OCRmyPDF with Tesseract behind the OCR adapter. `PWS-0204`
must select compatible exact Python and native artifacts, enumerate every executable and library,
and prove local worker-only operation with network denied, fixed paths, bounded resources,
progress, cancellation, timeout, cleanup, and sanitized errors.

Ghostscript is not admitted as an implicit fallback, ambient executable, optional optimizer, or
PDF/A provider. Its current route is distribution-changing or requires unreviewed commercial
terms, so it remains prohibited and its relationship disabled. The OCR adapter and packaged
environment must prevent accidental PATH discovery and include a test that the supported OCR path
does not invoke Ghostscript. Enabling it requires the decision-change or approved commercial-route
process above.

Tesseract upstream does not currently supply the modern official Windows installer needed as
release provenance. Before admission, `PWS-0204` must select a trusted signed distribution or a
reproducible project-controlled build, pin the exact revision, verify every artifact, and inventory
Leptonica, image/compression libraries, compiler runtimes, and other native closure. A locally
installed `tesseract.exe` discovered on PATH is capability evidence only, not an approved release
input.

### OCR language data

Trained data is executable-adjacent input and receives component-level provenance. Every shipped
`.traineddata` file records its repository and model family, language, exact revision, filename,
byte size, SHA-256, and license evidence. Fast, best, and legacy-capable model families are not
interchangeable versions of one opaque language pack.

Language packs remain modular under `PWS-0205`. They may be bundled or installed from a separately
authenticated offline package after review, but the document worker never downloads them and
never receives network access. Missing or mismatched data produces a stable remediation error; it
does not trigger a silent download or fallback.

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
  storage on the reference profile; and
- **Windows packaging:** supported architectures, artifact provenance/signatures, native closure,
  installer size, update path, and notice delivery.

An approved renderer has evidence for all five criteria; `blocked`, `concern`, or `not-measured`
values are not selection evidence. The selecting ADR records exact benchmark artifacts and results.
After selection, update the inventory, lock data, relationships, packaged-file expectations, and
SBOM inputs together. Renderer-specific types remain inside the worker adapter.

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

## Verification

Run the repository-defined commands rather than one-off validators:

```powershell
py -3.14 tools/dev.py validate-dependencies
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
