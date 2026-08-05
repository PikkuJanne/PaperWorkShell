# ADR 0005: Conditional selection of pypdfium2 and PDFium for PDF rendering

- Status: Proposed (conditional implementation target; live admission evidence pending)
- Date: 2026-08-03
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
evidence on all five axes, including exact benchmark artifacts and results. The documentary and
supply-chain comparison below narrows the implementation target, but the local CPython 3.14
capability probe, exact installed-artifact inspection, same-corpus Windows benchmark, and complete
golden/hostile suite have not yet been recorded. This ADR therefore cannot yet be `Accepted`.

## Conditional decision

Use **pypdfium2 5.12.1 with its default bundled PDFium 152.0.7947.0 (build 7947)** as the
conditional PWS-0106 implementation target. The selected artifact for supported Windows x86-64 is
`pypdfium2-5.12.1-py3-none-win_amd64.whl`. The default, non-V8/non-XFA build is required; a build
selected with `PDFIUM_PLATFORM=auto-v8`, or any build reporting a non-empty PDFium feature-flag
set, is outside this decision.

The renderer remains behind the renderer-neutral contract. No `pypdfium2`, `pdfium`, bitmap,
page, document, exception, or color object may leave the PDF adapter. The worker writes a bounded,
tightly packed, opaque-white RGBA8 raster to a caller-owned sink and returns only typed,
content-free metadata after the exact output is flushed. The detailed geometry, color,
cancellation, and failure behavior is defined in the
[PDF page-rendering contract](../architecture/pdf-page-rendering.md).

This choice becomes accepted only after all pending evidence in this ADR passes and the task
records the exact artifacts and results. Until then:

- the dependency is an evaluation candidate, not an approved shipped renderer;
- active release scopes and shipment relationships remain disabled;
- a failed or materially worse benchmark, fidelity failure, unsupported CPython 3.14 probe, or
  unacceptable binary/license closure reopens the choice; and
- Qt PDF is the first fallback to measure under the same worker, corpus, limits, and output
  contract. A fallback selection requires an amended ADR rather than an adapter-specific leak.

## Evidence completed before implementation

The following is completed **documentary and published-artifact evidence**. It does not substitute
for a live render, benchmark, hostile-input run, or packaged-file inspection.

### Artifact identity and provenance

- PyPI publishes pypdfium2 5.12.1 and the Windows x86-64 `py3-none` wheel. The published wheel is
  approximately 3.9 MB and has SHA-256
  `9609be73a6701a68f29dffe0335f7a2e4b3ba581542ed65d35d49f761a4600ca`.
- PyPI records Trusted Publishing plus Sigstore/in-toto attestation for the artifact, associated
  with source repository commit `4a2636051ff4a841966be01484f4a70b4309b55c`.
- The pypdfium2 version metadata identifies the bundled engine as PDFium 152.0.7947.0. The wheel is
  platform-specific but does not bind to one CPython extension ABI, which makes CPython 3.14
  plausible. Actual import and render compatibility remains a pending local probe.
- Upstream lists Windows x86-64 as a tier-1, host-tested platform. This is upstream support
  evidence, not proof of PaperWorkShell's packaged runtime.
- The non-default `renderer-evaluation` group resolves Qt PDF through
  `pyside6_addons-6.11.1-cp310-abi3-win_amd64.whl` (168,816,308 bytes; SHA-256
  `0d13c4dfd671b050a48e4f8d8ddc724b7248f9c0437e7fc47fdf316278572923`),
  `pyside6_essentials-6.11.1-cp310-abi3-win_amd64.whl` (77,549,267 bytes; SHA-256
  `63311bd48e32c584599ab04b9ef7c324082374cd2c9fa533f978fb893bb47e40`), and
  `shiboken6-6.11.1-cp310-abi3-win_amd64.whl` (1,223,647 bytes; SHA-256
  `c2c6863aa80ec18c0f82cea3417837b279cdc60024ac17123461dc9042577df7`). These
  releases publish no source distributions. The three resolved edges and artifacts are inventory
  evidence only: all endpoints remain `evaluated`, scope-free, candidate-only, and disabled.
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

All size and artifact statements below concern the named published candidate. Performance rows are
deliberately marked pending where PaperWorkShell has not run a comparable workload.

| Candidate | Security and feature surface | License route | Fidelity/API evidence | Performance evidence | Windows packaging evidence | Outcome |
|---|---|---|---|---|---|---|
| **pypdfium2 5.12.1 / PDFium 152.0.7947.0** | Direct PDFium binding; default build has no V8/XFA; native parser remains worker-only; progressive API and closed adapter are available | pypdfium2 Apache-2.0 OR BSD-3-Clause; PDFium BSD-style plus exact third-party notices still to reconcile | Rotation, MediaBox/CropBox queries, clipping, alpha output, DeviceCMYK/ICCBased input, and deterministic bitmap formats are exposed; inherited-box and UserUnit helper limitations plus CMYK/RGB conversion require contract handling | Same-corpus cold/warm latency, memory, cancellation, and temporary-storage measurements pending | 3.9 MB published `py3-none-win_amd64` wheel with hash and attestation; CPython 3.14 import and exact DLL/license closure pending | **Conditional target** |
| **Qt PDF through PySide6-Addons 6.11.1** | Qt PDF wraps its own PDFium snapshot, but the Python wheel also exposes WebEngine, multimedia, 3D, and other unrelated Addons surfaces; evaluation remains worker-only and inactive | No route selected; commercial OR LGPL-3.0 OR GPL-2.0 at the Qt PDF module, plus Qt/PDFium notices and LGPL source/replacement/relinking obligations for a community route | Page size, rotation, scaled clipping, annotations, grayscale, and transparent images are exposed; public API does not provide the same explicit page-box or CMYK-output controls | Same-corpus measurement pending; similar PDFium core performance is only an inference | Exact 168,816,308-byte Addons wheel plus 77,549,267-byte Essentials and 1,223,647-byte shiboken6 wheels are locked and hash-recorded; no QtPdf-only Python wheel | Measure first fallback; not selected |
| **PyMuPDF 1.28.0 / MuPDF** | Mature direct native API and strong page/pixmap controls; still a hostile native parser requiring the worker boundary | AGPL-3.0 or Artifex commercial terms; current route is distribution-altering or unreviewed | Explicit rotation, MediaBox/CropBox, clipping, alpha, and Gray/RGB/CMYK pixmaps | Vendor benchmark exists, but its corpus and methodology are not comparable to the PWS reference workload | Published 19.8 MB Windows x64 CPython `abi3` wheel | Blocked without a decision change or approved commercial terms |
| **Poppler 26.08.0** | Mature native renderer; a Python integration would add a custom bridge or executable boundary and its own update surface | GPL route is distribution-altering under current policy | Mature page-box/rotation behavior and transparent-paper support; exact CMYK and output behavior still need a chosen backend and goldens | No comparable PWS measurement | Upstream publishes source archives, not an official Python 3.14 Windows wheel; project-controlled build and closure would be required | Blocked by license and packaging |

Ghostscript is excluded. The dependency policy already prohibits it as an implicit renderer,
fallback, optimizer, or PDF/A provider without a decision change or approved commercial route.

The smaller direct pypdfium2 wheel and narrower runtime surface suggest lower installation and
startup overhead than the complete PySide6-Addons wheel. That is an inference from packaging, not
a measured performance result and not selection evidence by itself.

## Pending admission and acceptance evidence

Before this ADR can be accepted and PWS-0106 can be marked done, one reviewed evidence record must
close every item below.

### Installed capability and closure

1. Resolve and lock exactly pypdfium2 5.12.1 for the repository's CPython 3.14 environment.
2. Import the package in that environment, open and render a synthetic page, and record the
   package version, PDFium version `152.0.7947.0`, build number 7947, and empty feature flags.
3. Verify the exact wheel filename, byte size, SHA-256, PyPI attestation, installed files, native
   DLL imports, compiler runtime, `BUILD_LICENSES`, license texts, and notices.
4. Update the dependency inventory, lock data, candidate relationships, packaged-file
   expectations, notice inputs, and SBOM inputs together. Advancing the candidate to an approved
   active/shipped state waits for every admission criterion.

### Same-corpus Windows comparison

Run pypdfium2 and Qt PDF through equivalent worker-only adapters against the same synthetic render
corpus and output contract. The report must bind:

- the repository revision, exact fixture/golden hashes, adapter version, renderer artifact/hash,
  engine feature flags, CPython version, Windows version, and
  [`windows-low-spec-v1`](../../benchmarks/reference-windows-v1.json) profile;
- cold worker launch, import, document-open, and first-page latency;
- warm per-page latency with p50, p95, maximum, and page/fixture attribution using only synthetic
  identifiers;
- peak private bytes, output bytes, and temporary-storage high-water mark;
- cooperative cancellation latency and hard Job-termination latency; and
- malformed/truncated/high-dimension behavior and the stable outcome code.

The reference profile currently defines a record-only generated-I/O workload, not renderer
targets. The renderer benchmark must add its own versioned corpus/workload record while reusing the
profile's bounded Windows envelope; it must not silently reinterpret the existing I/O workload.
No applicant document, rendered page, path, filename, OCR text, or machine identifier enters the
report.

The report must label a PyPI/wheel hash as declared published-artifact evidence unless the run
directly observes that downloaded wheel. Installed evidence is separate: the harness validates the
SHA-256 and size of every selected installed file against that distribution's `RECORD`, records the
observed `RECORD` digest, and binds the complete staged closure with the stage-manifest digest. It
stages tracked project/data bytes directly from the exact reported Git commit and includes that
revision inside the hashed manifest. It also requires the same clean Git `HEAD` before staging,
after staging, and immediately before the complete report. The fixed child bootstrap and minimal
isolation/protocol imports necessarily load
before the child receives the manifest digest; no benchmark adapter or native renderer imports
until the child has verified the full manifest.

All child, cold-segment, and warm-sample timings must be strictly positive. The parent measures a
separate monotonic envelope from before process launch through verified child exit and rejects a
child total outside that envelope. The report retains both sources so a malformed child cannot
manufacture the only timing evidence.

In-process cleanup uses retained Windows identities and fails closed on any unknown or replaced
object. An authority-setup failure deletes an authenticated empty root by its exact retained
identity, but preserves and reports any root for which that authority was never established. A
sandbox launch, process-close, or post-revocation verification failure irreversibly withdraws the
in-process stage cleanup capability. No later path may restore its namespace DACLs, release them
through normal stage cleanup, or invoke exact-tree deletion; the sealed public-only residue is
retained for the process lifetime and may remain after exit. A sudden parent or machine termination
can leave a bounded stage of at most 768 MiB. That
stage contains only copied runtime/project code and public synthetic fixtures. Because a later
process has no trustworthy inherited handle authority, it preserves such residue for explicit
maintenance instead of accepting a self-asserted marker or token as permission to delete it.

### Fidelity and hostile-input suite

The selected build must pass inspected synthetic goldens for:

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
- embedded-font cases and a documented missing-font/fallback case;
- malformed, truncated, password-required, script/XFA/interactive, and unsupported inputs; and
- extreme declared geometry, output-size multiplication, timeout, cancellation, memory, and
  destination-limit failures without a partial trusted result.

Golden refreshes require visual inspection and recorded approval. Exact byte comparison is
appropriate only where the selected engine and environment make it stable; otherwise a reviewed,
bounded pixel-difference oracle must record why tolerance is necessary.

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
