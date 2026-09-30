# ADR 0010: Keep the bounded Pillow image decoder

- Status: Accepted for the PWS-0201 development boundary; packaged release not approved
- Review date: 2026-09-30
- Task: PWS-0201
- Decisions preserved: PWS-D007, PWS-D009, PWS-D011, PWS-D012, PWS-D013, PWS-D034, PWS-D041, PWS-D042
- Reviewed starting revision: `0298c840421f106f55b1ac7c0c2077f171846600`

## Context and chronology

This is a retrospective comparison requested by the maintainer. The existing PWS-0201
implementation already selected Pillow 12.3.0 and recorded completion at `83c9ae0`. It did not
contain the requested decoder-selection ADR. This record closes that documentation gap; it does
not claim the comparison preceded the original implementation or replace the current verification
report. No frozen decision, dependency version, manifest schema, or portfolio boundary changes.

The required capability is one JPEG or PNG page decoded from an authenticated immutable source
handle, with bounded metadata and pixels, eight EXIF orientations, explicit color behavior, and
toolkit-independent output. Standard-library framing checks cannot provide the complete JPEG
decoder, PNG reconstruction, or color/alpha normalization. A GUI decoder is not an acceptable
processing location.

## Decision and comparison

Keep the exact Pillow 12.3.0 Windows x64 wheel behind `imaging.pillow_decoder`. Allow only JPEG
and PNG in `Image.open(formats=...)`; retain the independent structural walk and restricted
one-shot worker. The existing synthetic tests and live AppContainer evidence exercise this route.
Qt was assessed from its public API and exact 6.11.1 source; no comparative performance or pixel
fidelity benchmark was run. The decision is based on the smaller change and already demonstrated
contract, not a claim that Pillow is inherently safer or faster.

| Criterion | Selected Pillow 12.3.0 | Admitted Qt/PySide6 6.11.1 alternative |
|---|---|---|
| Worker boundary | Existing reduced-handle decoder, fixed RGBA artifact, one-shot termination | Technically possible using a worker-local `QIODevice` and `QImageReader`; requires a new adapter, Qt worker resource closure, and import-boundary review |
| Windows delivery | Exact CPython 3.14 x64 wheel already locked; eight native extensions recorded separately | Existing Essentials/shiboken wheel admission covers the GUI; moving QtGui and JPEG plugin into the worker creates additional active packaging scope |
| JPEG/PNG semantics | Synthetic RGB, gray, palette, CMYK, alpha, and eight-orientation tests already cover the application contract | JPEG/PNG handlers exist; EXIF transform and color-space APIs are available, but the same application corpus and normalization contract would need proving |
| Errors and truncation | Independent framing scan plus truncated loading disabled; closed errors replace raw diagnostics | Exact JPEG handler inserts an end marker at exhausted input and writes native warnings; strict framing and diagnostic interception would still be necessary |
| Memory and CPU | Pixel/decoded-byte planning, metadata/text bounds, Job memory/CPU, deadline, cancellation and cleanup | `QImageReader` allocation limit and scaled reads are useful but do not replace total-process, metadata, temporary-space or CPU bounds |
| State outside adapter | Pure metadata and opaque pixels only | Must convert Qt image, transformation and color types into the same pure models |
| Replacement cost | Preserve tested integration and use a versioned profile for behavior changes | New runtime-scope admission and full adapter/live-worker/resource testing required before switching |

The [Qt reader API](https://doc.qt.io/qt-6.11/qimagereader.html) describes allocation and transform
controls. The [6.11.1 JPEG source](https://raw.githubusercontent.com/qt/qtbase/v6.11.1/src/plugins/imageformats/jpeg/qjpeghandler.cpp)
shows CMYK handling, warning output and end-of-input behavior. The
[6.11.1 PNG source](https://raw.githubusercontent.com/qt/qtbase/v6.11.1/src/gui/image/qpnghandler.cpp)
and [color-space API](https://doc.qt.io/qt-6.11/qcolorspace.html) establish the other candidate
surfaces. The versioned API site may display a later 6.11 patch; exact source links anchor the
implementation review.

### Maintenance, security and licence

Pillow publishes maintained source, wheels and security fixes. Its
[12.3.0 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html)
include fixes for native image-operation memory errors and decompression/resource problems.
Those fixes justify exact pinning and future advisory review; they do not establish an absence
of vulnerabilities. Its [security guidance](https://pillow.readthedocs.io/en/stable/handbook/security.html)
recommends sandboxing, strict format selection, pixel/text bounds and OS memory limits, consistent
with the existing worker design. No viewer, expression evaluator, EPS or external-command path is
admitted by this task.

Pillow itself uses MIT-CMU. Embedded libraries have separate notices and terms; the wheel licence
is not a blanket MIT licence for every component. Qt's selected GUI route remains LGPL-3.0-only,
with corresponding-source, replacement/relinking and notice obligations in
[ADR 0006](0006-pyside6-ui-runtime.md). Qt also has a public security-response history: its
[ICNS advisory](https://www.qt.io/blog/security-advisory-recently-discovered-issue-in-icns-image-format-handling-impacts-qt)
documents an earlier parser crash and fixed versions. That historical unrelated-format issue is
not evidence of a JPEG/PNG flaw in 6.11.1; it illustrates why plugin scope and worker isolation
would remain mandatory for the Qt alternative.

### Additional alternative

[libvips](https://www.libvips.org/) is a serious maintained option with Windows binaries,
LGPL-2.1-or-later licensing and incremental image processing. It also introduces a new native
stack, many operations, optional loaders and threading/cache controls. No Windows artifact,
Python binding or narrow JPEG/PNG closure has been admitted here. There is no demonstrated
PWS-0201 need that justifies that replacement and its validation work. It remains unselected;
this is an architectural assessment, not a measured rejection. No broad computer-vision
framework is added.

## Exact dependency and native evidence

The selected artifact is `pillow-12.3.0-cp314-cp314-win_amd64.whl`, 7,237,707 bytes, SHA-256
`fdafc9cce40277e0f7a0feabce0ee50dd2fa1800f3b38015e51296b5e814048d`. The source archive is
`pillow-12.3.0.tar.gz`, SHA-256
`3b8182a766685eaa002637e28b4ec8d6b18819a0c71f579bf0dbaa5830297cce`. Both agree with
`uv.lock` and the main dependency inventory. The wheel was fetched from that inventory's exact
upstream URL and its size/hash verified during this review; native members and licence bytes
were compared with the installed distribution. No code from the downloaded archive was executed.

The [runtime-closure companion](../../packaging/pillow-runtime-closure-v1.json) records all eight
native-extension sizes and SHA-256 values, the wheel's complete licence-file hash, embedded
component evidence and source links. The offline `validate-pillow-runtime` command binds that
reviewed record to the lock, main inventory, installed distribution RECORD and actual native
bytes. It does not download dependencies or load optional codec extensions.

For this wheel, JPEG uses libjpeg-turbo 3.1.4.1. PNG reconstruction uses Pillow's own parser and
zlib-ng 2.3.3, not a libpng decoder. The wheel also carries libpng 1.6.58 for its broader native
closure. [Pillow's exact dependency table](https://raw.githubusercontent.com/python-pillow/Pillow/12.3.0/.github/dependencies.json)
and [Windows build recipe](https://raw.githubusercontent.com/python-pillow/Pillow/12.3.0/winbuild/build_prepare.py)
provide upstream version/build provenance. That recipe is supporting evidence, not a
reproducible-build attestation of the published binary.

The companion distinguishes directly exercised decode components, embedded codecs or font
support outside the selected operation, upstream recipe-only transitive versions, and external
runtime obligations. Optional APIs do not become admitted merely because a wheel contains them.
The optional libimagequant feature remains disabled. The full staged wheel is larger than the
selected JPEG/PNG processing surface; its unused native code is still an SBOM and release concern.

PWS-0801 must reconcile the shipped subset, optional AVIF/font dependencies, complete third-party
notices and any corresponding-source/replacement obligations with the final package. The wheel
has no separate DLL members, but its extensions import CPython, Windows and Microsoft CRT DLLs.
Their eventual package provenance belongs to the interpreter/platform release closure. This ADR
and companion are development admission evidence, not approval to redistribute the entire wheel
or a claim of a complete packaged SBOM.

## Supported page profile

- Accept complete single-frame baseline/progressive JPEG and supported PNG samples up to eight
  bits per channel. Reject 16-bit PNG before decode because the current conversion is not an
  approved precision-preserving normalization.
- Reject APNG, MPO/multi-image JPEG, invalid EXIF orientation and unsupported color modes with
  stable plain-language failures. Never silently select a frame.
- Read only bounded IFD0 orientation, record its closed intrinsic intent, and apply it once before
  user rotation and fit. Reopen rederives it from immutable source bytes. Do not persist camera,
  GPS, arbitrary EXIF, ICC, comments or text records.
- Normalize unprofiled RGB/gray/indexed/CMYK through the pinned conversion. CMYK is an
  uncalibrated conversion; it does not promise device or print-color fidelity.
- Reject embedded ICC and unsupported PNG color metadata before decode, including `gAMA`,
  `cHRM`, and PNG 3 `cICP`, `mDCV`, and `cLLI`. The latter records carry color-space or HDR
  display information that the fixed output interpretation does not apply
  ([PNG 3 color-space definitions](https://www.w3.org/TR/png-3/#cICP-chunk)). Accept the validated
  standard sRGB intent. No implicit ICC transform is performed; adding a calibrated conversion
  requires separate profile, resource, native-CMS and fidelity evidence.
- Composite page alpha over opaque white. Return packed opaque RGBA8 only; never expose a native
  image object or encoded image to the GUI.
- Ignore DPI for physical-page decisions. Reliable bounded DPI is not required by the current
  application model, so no arbitrary DPI or profile field is added to the manifest.

Encoded bytes, structural records, encoded metadata, expanded PNG text/profile data, dimensions,
source pixels, decoded bytes, planned transient buffers and output bytes each have explicit
checks. The same central resource policy supplies process/Job limits and the one-heavy-operation
coordinator. Cooperative phase checks do not claim to interrupt a native call; hard timeout and
whole-tree termination supply that boundary. Existing temporary capacity, cancellation, cleanup
and fresh-retry behavior remain mandatory.

## Evidence and consequences

The [intake architecture](../architecture/image-intake.md) and
[security test plan](../security/security-test-plan.md#pws-0201-jpegpng-intake-evidence) identify
the domain, decoder, protocol, broker, AppContainer, privacy and failure tests. Synthetic fixtures
make source hashes, orientation pixels, alpha and malformed/over-budget behavior reproducible.
They do not certify physical hardware or calibrated color.

A replacement must preserve the pure page/artifact contract, authenticate exact immutable source
handles, stay entirely in the restricted worker and pass the same success, failure, privacy,
resource and desktop journey evidence. A changed decoder or pixel policy requires a versioned
profile/cache identity and reviewed golden changes. No migration, OCR, cleanup, overlays,
installer, shared service or ThirdLife adapter is introduced by this decision.
