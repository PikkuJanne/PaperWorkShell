# ADR 0005: Selection of pypdfium2 and PDFium for PDF rendering

- Status: Accepted
- Date: 2026-08-05
- Task: PWS-0106
- Decisions: PWS-D012, PWS-D013, PWS-D017, PWS-D019, PWS-D034
- Architecture: [PDF page-rendering contract](../architecture/pdf-page-rendering.md)
- Dependency policy: [PDF renderer candidates](../development/dependency-policy.md#pdf-renderer-candidates)

## Context

PaperWorkShell needs bounded raster page rendering for thumbnails, review, and later export
verification. Rendering hostile PDFs is a document-processing operation, so it runs only in the
restricted worker. PWS-D017 additionally requires a replaceable internal boundary and a selection
based on security, licensing, fidelity, performance, and Windows packaging evidence.

No renderer is approved merely because its API works. The dependency policy requires acceptable
evidence on all five axes, including exact benchmark artifacts and results. The CPython 3.14
capability probe, installed-artifact inspection, same-corpus Windows comparison, complete
golden/hostile suite, and bounded worker adapter are now recorded. This ADR accepts the selection
without making a release-package, universal-fidelity, or product-latency claim.

## Decision

Use **pypdfium2 5.12.1 with its default bundled PDFium 152.0.7947.0 (build 7947)** as the
PWS-0106 renderer. The selected artifact for supported Windows x86-64 is
`pypdfium2-5.12.1-py3-none-win_amd64.whl`. The default, non-V8/non-XFA build is required; a build
selected with `PDFIUM_PLATFORM=auto-v8`, or any build reporting a non-empty PDFium feature-flag
set, is outside this decision.

The renderer remains behind the renderer-neutral contract. No `pypdfium2`, `pdfium`, bitmap,
page, document, exception, or color object may leave the PDF adapter. The worker writes a bounded,
tightly packed, opaque-white RGBA8 raster to a caller-owned sink and returns only typed,
content-free metadata after the exact output is flushed. The detailed geometry, color,
cancellation, and failure behavior is defined in the
[PDF page-rendering contract](../architecture/pdf-page-rendering.md).

The exact selected Python and native closure is admitted for the worker-side integration and
remains subject to the release inventory, notice, packaged-file, and SBOM reconciliation owned by
PWS-0801. Qt PDF stays locked only as a non-default, evaluation-only comparison candidate with no
product scope or enabled shipment relationship. PWS-0111 later admits the already reviewed
Essentials/shiboken portion as the desktop UI runtime without admitting Addons or Qt PDF. A later
renderer fallback selection still requires an amended ADR and the same worker, corpus, limits, and
output contract rather than an adapter-specific leak.

## Published-artifact and implementation evidence

The following documentary and published-artifact evidence complements the live measurements and
tests recorded below. A declared PyPI wheel hash is kept distinct from installed-file and staged
closure evidence.

### Artifact identity and provenance

- PyPI publishes pypdfium2 5.12.1 and the Windows x86-64 `py3-none` wheel. The published wheel is
  approximately 3.9 MB and has SHA-256
  `9609be73a6701a68f29dffe0335f7a2e4b3ba581542ed65d35d49f761a4600ca`.
- PyPI records Trusted Publishing plus Sigstore/in-toto attestation for the artifact, associated
  with source repository commit `4a2636051ff4a841966be01484f4a70b4309b55c`.
- The pypdfium2 version metadata identifies the bundled engine as PDFium 152.0.7947.0. The wheel is
  platform-specific but does not bind to one CPython extension ABI. The exact locked installation
  imports and renders successfully under the repository's supported CPython 3.14 runtime.
- Upstream lists Windows x86-64 as a tier-1, host-tested platform. This remains upstream support
  evidence rather than proof of a future PaperWorkShell release package.
- The non-default `renderer-evaluation` group resolves Qt PDF through
  `pyside6_addons-6.11.1-cp310-abi3-win_amd64.whl` (168,816,308 bytes; SHA-256
  `0d13c4dfd671b050a48e4f8d8ddc724b7248f9c0437e7fc47fdf316278572923`),
  `pyside6_essentials-6.11.1-cp310-abi3-win_amd64.whl` (77,549,267 bytes; SHA-256
  `63311bd48e32c584599ab04b9ef7c324082374cd2c9fa533f978fb893bb47e40`), and
  `shiboken6-6.11.1-cp310-abi3-win_amd64.whl` (1,223,647 bytes; SHA-256
  `c2c6863aa80ec18c0f82cea3417837b279cdc60024ac17123461dc9042577df7`). These
  releases publish no PyPI source distributions. At PWS-0106 selection time, all three artifacts
  were evaluation evidence only. PWS-0111 later admits Essentials and shiboken6 for the distinct
  desktop UI purpose while leaving Addons and Qt PDF evaluated, scope-free, candidate-only, and
  disabled. That later admission does not change this renderer decision.
- Qt PDF 6.11.1 has no standalone Python wheel. Its evaluated artifact is therefore the exact
  Addons wheel. The Qt PDF PDFium snapshot is not equated with pypdfium2's independently pinned
  PDFium 152.0.7947.0 build; exact embedded revision, DLL, SBOM, and notice reconciliation remains
  pending.

### Security and feature surface

- pypdfium2 is a narrow Python binding over PDFium rather than a complete GUI or browser stack.
  The default wheel does not enable V8 or XFA. PaperWorkShell renders stored annotation appearance
  streams under one fixed profile, but does not initialize form filling, enable XFA, or execute
  scripts, actions, calculations, or dynamic form behavior.
- PDFium is not thread-safe. The adapter contract serializes all PDFium calls and confines each
  document, page, bitmap, and render lifecycle to one worker execution context.
- PDFium exposes a progressive raw API with pause callbacks, but the selected high-level binding's
  page-render call is synchronous. The adapter's cooperative probes do not claim to interrupt every
  native instruction; the retained PWS-0107 Job Object remains the hard timeout and native-fault
  boundary.
- Native parser risk remains material. The choice depends on the zero-capability AppContainer,
  exact handle brokerage, Job Object limits, output validation, one-shot generation cleanup, and
  stable error mapping; library selection does not replace those controls.

### License route

- pypdfium2 offers Apache-2.0 OR BSD-3-Clause. PDFium itself uses a BSD-style license and includes
  third-party components under additional licenses.
- The exact wheel's `BUILD_LICENSES` material and complete native dependency closure must be
  preserved and reconciled with the dependency inventory, notice bundle, packaged-file inventory,
  and later SBOM. Upstream notes that some build variants may have additional compiler-runtime
  linkage, so the exact Windows wheel must be inspected rather than inferred from the project
  license alone.
- This route is not known to require a distribution-changing decision, subject to completing that
  exact closure and notice review. That is materially simpler than the currently blocked AGPL/GPL
  candidates, but it is not final redistribution approval.

## Candidate comparison

All size and artifact statements below concern the named published candidate. Measured rows use
the accepted same-corpus Windows run; unmeasured alternatives remain documentary comparisons only.

| Candidate | Security and feature surface | License route | Fidelity/API evidence | Performance evidence | Windows packaging evidence | Outcome |
|---|---|---|---|---|---|---|
| **pypdfium2 5.12.1 / PDFium 152.0.7947.0** | Direct PDFium binding; default build has no V8/XFA; native parser remains worker-only; progressive API and closed adapter are available | pypdfium2 Apache-2.0 OR BSD-3-Clause; PDFium BSD-style plus exact third-party notices to reconcile for release | Passed all 15 analytical goldens, both semantic checks, and all three malformed/high-dimension outcomes; explicit inherited MediaBox/CropBox behavior is implemented behind the adapter | Warm p50/p95/max: 518,800/4,152,500/6,255,300 ns; cold import/open/first page: 375,281,700/1,873,900/5,025,600 ns; exact resource/lifecycle evidence below | Exact Windows wheel, installed `RECORD`, CPython 3.14 import, engine identity, and 1,015-file staged closure verified | **Selected** |
| **Qt PDF through PySide6-Addons 6.11.1** | Qt PDF wraps its own PDFium snapshot, but the Python wheel also exposes WebEngine, multimedia, 3D, and other unrelated Addons surfaces; evaluation remains worker-only and inactive | No route selected; commercial OR LGPL-3.0 OR GPL-2.0 at the Qt PDF module, plus Qt/PDFium notices and LGPL source/replacement/relinking obligations for a community route | Passed all 15 analytical goldens, the missing-font semantic check, and all three stable outcomes; the required distinct MediaBox semantic mode is unsupported by the public API | Warm p50/p95/max: 683,300/6,484,400/9,441,700 ns; cold import/open/first page: 283,393,700/9,078,200/7,794,900 ns; exact resource/lifecycle evidence below | Exact 168,816,308-byte Addons wheel plus 77,549,267-byte Essentials and 1,223,647-byte shiboken6 wheels are locked and hash-recorded; no QtPdf-only Python wheel | Not selected; evaluation-only fallback |
| **PyMuPDF 1.28.0 / MuPDF** | Mature direct native API and strong page/pixmap controls; still a hostile native parser requiring the worker boundary | AGPL-3.0 or Artifex commercial terms; current route is distribution-altering or unreviewed | Explicit rotation, MediaBox/CropBox, clipping, alpha, and Gray/RGB/CMYK pixmaps | Vendor benchmark exists, but its corpus and methodology are not comparable to the PWS reference workload | Published 19.8 MB Windows x64 CPython `abi3` wheel | Blocked without a decision change or approved commercial terms |
| **Poppler 26.08.0** | Mature native renderer; a Python integration would add a custom bridge or executable boundary and its own update surface | GPL route is distribution-altering under current policy | Mature page-box/rotation behavior and transparent-paper support; exact CMYK and output behavior still need a chosen backend and goldens | No comparable PWS measurement | Upstream publishes source archives, not an official Python 3.14 Windows wheel; project-controlled build and closure would be required | Blocked by license and packaging |

Ghostscript is excluded. The dependency policy already prohibits it as an implicit renderer,
fallback, optimizer, or PDF/A provider without a decision change or approved commercial route.

The measured timings are mixed: Qt PDF imported faster, while pypdfium2 opened the document and
rendered the first and warm pages faster in this record-only local smoke. Selection therefore does
not rest on a single latency number. pypdfium2 satisfies both required semantic modes, including
the distinct MediaBox request, and provides the narrower direct worker dependency with the simpler
license and packaging route. Qt PDF lacks the required explicit MediaBox mode and would add the
much broader Addons/Essentials closure plus unresolved Qt redistribution work.

## Admission and acceptance evidence

### Installed capability and closure

The locked CPython 3.14 environment imports pypdfium2 5.12.1, reports PDFium
`152.0.7947.0`/build 7947 with an empty feature-flag set, and renders the synthetic probe. Every
selected installed renderer file is checked for size and SHA-256 against its distribution
`RECORD`. The benchmark keeps the declared published-wheel identity separate from those installed
records and from the complete staged closure.

The accepted comparison ran from clean repository revision
`74c1c7aae799195a87608a797d7136b88e80cc87`. Its authenticated stage contained 1,015 files and had
closure-manifest SHA-256
`f78dffe61e4b86e6596f1080097a7a23aed90cc0ca6eca9023d67fb2521e3da8`. Tracked project/data bytes
came from the exact Git revision, and the harness required the same clean `HEAD` before staging,
after staging, and immediately before completing the report. The fixed child bootstrap and minimum
isolation/protocol imports necessarily loaded before the child received the manifest digest; no
benchmark adapter or native renderer imported until the child verified the complete manifest.

The dependency inventory and lock bind the selected pypdfium2/PDFium artifacts and retain the
native/license/notice closure as release inventory inputs. This accepts worker-side development and
runtime use; final package-file, notice, redistribution, and SBOM reconciliation remains PWS-0801
work and is not implied by this ADR.

### Same-corpus Windows comparison

Both candidates ran through fresh zero-capability AppContainer workers under the production Job
Object, the same two-logical-processor affinity, 4,096 MiB attestation, integrated-graphics
attestation, synthetic corpus, and canonical RGBA8-white output contract. The measurements are
nanoseconds:

| Candidate | Fidelity and stable outcomes | Warm p50 / p95 / max | Cold import / open / first page | Peak process / Job bytes | Cooperative cancel / hard Job termination |
|---|---|---:|---:|---:|---:|
| pypdfium2/PDFium | 15/15 analytical goldens; 2/2 semantic cases; malformed, malformed-page-tree, and high-dimension outcomes matched | 518,800 / 4,152,500 / 6,255,300 | 375,281,700 / 1,873,900 / 5,025,600 | 50,356,224 / 51,597,312 | 1,230,400 / 188,100 |
| Qt PDF | 15/15 analytical goldens; missing-font semantic case passed; distinct MediaBox mode unsupported; all three stable outcomes matched | 683,300 / 6,484,400 / 9,441,700 | 283,393,700 / 9,078,200 / 7,794,900 | 49,643,520 / 50,884,608 | 1,315,600 / 191,000 |

The parent separately bounded every strictly positive child timing inside a launch-through-verified-
exit monotonic envelope. The report also recorded output and private temporary-storage counters,
exact corpus/profile/renderer identities, and stable synthetic case IDs without any page pixels,
document content, source path, filename, user identity, machine identity, or native exception text.
Cooperative cancellation is measured only at the documented between-render checkpoint; it does not
claim interruption of an in-flight native render.

This was a **record-only local-smoke selection run**, not a product performance target or a prepared
limited-storage/HDD qualification. The complete sanitized JSON result was reviewed locally but is
not committed; the repository commits the versioned workload, exact corpus/golden bindings, this
decision record, and reproducible command instead. Neither ordinary CI nor this ADR treats mutable
host timing as a release threshold.

The comparison does not establish one universal performance winner: Qt imported faster and used
slightly less peak memory, while pypdfium2 opened and rendered faster. pypdfium2 is selected because
it also passes both required semantic modes—including distinct MediaBox rendering—and has the
narrower direct artifact and simpler license/packaging route. Performance corroborates that the
selected route is bounded; it is not the sole selection rationale.

Normal cleanup uses retained Windows identities and fails closed on an unknown or replaced object.
An authority-setup failure deletes only an authenticated empty root by its exact identity and
preserves any root for which authority was never established. A sandbox launch, uncertain process
close, or post-revocation verification failure irreversibly withdraws cleanup capability; no later
path restores the namespace DACLs or attempts exact-tree deletion. An abrupt parent-process or
machine termination can therefore leave a sealed, public-only stage of at most 768 MiB containing
only copied runtime/project code and synthetic fixtures. A later process has no trustworthy
inherited handle authority, so it preserves that residue for explicit maintenance rather than
trusting a self-asserted marker or token as permission to delete it.

### Fidelity and hostile-input suite

The selected build passed inspected synthetic evidence for:

- intrinsic rotations and every additional quarter-turn;
- inherited and direct MediaBox/CropBox values, including asymmetric boxes and CropBox different
  from MediaBox;
- a non-default PDF 1.6 `/UserUnit`, proving bounded fit-to-rectangle behavior without a false
  physical-DPI claim;
- transparency, soft masks, blend modes, and the required white-background composite;
- DeviceCMYK and ICCBased source colors converted to the RGBA8 preview contract without claiming
  sRGB or colorimetric equivalence;
- stored annotation appearances under the fixed profile, without form initialization, XFA, or
  script execution;
- embedded-font and documented missing-font/fallback behavior;
- malformed, truncated, password-required, script/XFA/interactive, and unsupported inputs; and
- extreme declared geometry, output-size multiplication, timeout, cancellation, memory, and
  destination-limit failures without a partial trusted result.

Golden refreshes still require visual inspection and recorded approval. Exact byte comparison is
used only where the engine and environment make it stable; the reviewed one-channel tolerance for
alpha/blend references is explicit in the golden index.

## Reconsideration triggers

Amend this ADR before changing the renderer when any of these occurs:

- the exact pypdfium2/PDFium build or its feature flags change;
- a dependency advisory, unsupported Python/Windows target, packaging failure, or license closure
  makes the selected route unacceptable;
- the renderer fails required malformed, cancellation, limit, or fidelity evidence;
- Qt PDF produces materially better measured fidelity or bounded-resource behavior on the same
  corpus and its broader package/license obligations are acceptable; or
- a future render requirement cannot be expressed by the renderer-neutral request/result
  contract.

An engine change may alter adapter implementation and goldens, but it must not alter domain or
manifest formats, expose third-party types, weaken worker isolation, or reinterpret cached output
without a versioned PWS-0108 migration/invalidation rule.

## Primary sources

- [pypdfium2 5.12.1 project files, hashes, and attestations](https://pypi.org/project/pypdfium2/)
- [pypdfium2 5.12.1 PyPI JSON](https://pypi.org/pypi/pypdfium2/5.12.1/json)
- [pypdfium2 platforms](https://pypdfium2.readthedocs.io/en/stable/platforms.html)
- [pypdfium2 Python API](https://pypdfium2.readthedocs.io/en/stable/python_api.html)
- [pypdfium2 version and build metadata](https://pypdfium2.readthedocs.io/en/stable/shell_api.html)
- [pypdfium2 installation and licensing](https://pypdfium2.readthedocs.io/en/stable/readme.html)
- [PDFium license](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/LICENSE)
- [PDFium progressive-render API](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/public/fpdf_progressive.h)
- [PDFium bitmap and page-render API](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/public/fpdfview.h)
- [PySide6-Addons 6.11.1 PyPI JSON](https://pypi.org/pypi/PySide6-Addons/6.11.1/json)
- [PySide6-Essentials 6.11.1 PyPI JSON](https://pypi.org/pypi/PySide6-Essentials/6.11.1/json)
- [shiboken6 6.11.1 PyPI JSON](https://pypi.org/pypi/shiboken6/6.11.1/json)
- [Qt for Python package and dependency layout](https://doc.qt.io/qtforpython-6/package_details.html)
- [Qt PDF licensing](https://doc.qt.io/qt-6/qtpdf-licensing.html)
- [Qt 6.11.1 third-party component inventory](https://doc.qt.io/qt-6/licenses-used-in-qt.html)
- [Qt for Python PDF API](https://doc.qt.io/qtforpython-6/PySide6/QtPdf/index.html)
- [PyMuPDF 1.28.0 PyPI JSON](https://pypi.org/pypi/PyMuPDF/1.28.0/json)
- [MuPDF releases and licensing](https://mupdf.com/releases/)
- [PyMuPDF performance methodology](https://pymupdf.readthedocs.io/en/latest/app4.html)
- [Poppler releases](https://poppler.freedesktop.org/releases.html)
