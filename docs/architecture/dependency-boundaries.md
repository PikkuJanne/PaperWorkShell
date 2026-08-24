# PaperWorkShell dependency boundaries

- Status: Binding implementation policy
- Task: PWS-0002
- Extensions: PWS-0102 local case lifecycle; PWS-0104 immutable ingest; PWS-0105 PDF
  structural inspection; PWS-0106 PDF page rendering; PWS-0107 restricted worker lifecycle;
  PWS-0108 bounded PDF thumbnails; PWS-0109 atomic manifest persistence; PWS-0110 page operations;
  PWS-0111 accessible organizer and desktop composition; PWS-0112 deterministic verified PDF
  export; PWS-0113 startup recovery and sanitized diagnostics; PWS-0201 bounded JPEG/PNG intake;
  PWS-0202 non-destructive image transformations; PWS-0204 bounded local OCR; PWS-0206
  verified searchable mixed-document output; PWS-0301 normalized page coordinates; PWS-0302
  visual overlay intent; and PWS-0303 deterministic overlay-page rendering
- Decisions: PWS-D002, PWS-D010, PWS-D011, PWS-D012, PWS-D013, PWS-D014, PWS-D015, PWS-D016, PWS-D017, PWS-D018, PWS-D027, PWS-D028, PWS-D036, PWS-D038, PWS-D039

## Purpose

PaperWorkShell separates pure product rules, UI-facing orchestration, the document worker, and document-engine adapters before any optional engine is installed. This keeps domain behavior testable without a GUI, prevents the desktop process from reaching low-level document parsers directly, and lets PDF, image, OCR, and rendering implementations change behind stable interfaces.

The enforced high-level direction is:

```text
desktop/UI -> app -> domain
                 -> worker client boundary

worker host -> domain
            -> pdf / imaging / ocr adapters
            -> storage / export / diagnostics services
```

## Package responsibilities

| Package | Responsibility |
|---|---|
| `domain` | Pure models and deterministic rules; no UI, worker, storage, or document-engine dependency |
| `app` | Toolkit-free use-case orchestration through domain types and service/client boundaries |
| `ui` | PySide6 Widgets presentation over application/domain contracts and validated display data; only QtCore, QtGui, and QtWidgets imports are admitted |
| `worker` | GUI-safe client/protocol boundaries plus separately composed host lifecycle, exact object brokerage, document operations, progress, cancellation, and limits; consuming integrations are documented in the [bounded PDF-thumbnail contract](pdf-thumbnails.md), [bounded JPEG/PNG intake contract](image-intake.md), [bounded local OCR contract](ocr-worker.md), [deterministic verified PDF-export contract](pdf-export.md), and [verified searchable mixed-output contract](searchable-mixed-output.md) |
| `pdf` | PDF interfaces and worker-side pikepdf/qpdf/PDFium/font-rendering implementations; implemented contracts are documented in the [PDF structural-inspection contract](pdf-structural-inspection.md), [PDF page-rendering contract](pdf-page-rendering.md), [deterministic overlay rendering contract](overlay-rendering.md), [deterministic verified PDF-export contract](pdf-export.md), and [verified searchable mixed-output contract](searchable-mixed-output.md) |
| `imaging` | Worker-only image inspection and transformation adapters; Pillow and its native objects never enter domain, app, or UI code |
| `ocr` | Worker-only OCRmyPDF/Tesseract orchestration and its exact private PDF-candidate reopen seam; third-party objects and recognized text never cross into domain, app, UI, storage, or GUI-safe worker modules; see the [bounded local OCR contract](ocr-worker.md) |
| `overlays` | Pure normalized visual-overlay intent, released wire codecs, deterministic ordering/visibility, and third-party-free rendering contracts; concrete font, image, and PDF engines remain in the exact worker-only PDF adapter documented in the [visual overlay domain model](overlay-domain-model.md) and [overlay rendering contract](overlay-rendering.md) |
| `preflight` | Typed, deterministic findings; never presentation dialogs |
| `export` | Trusted desktop-side adapters that publish already verified derivative bytes; deterministic planning remains pure domain logic and native assembly/verification remains worker-side |
| `templates` | Declarative, versioned template loading and validation |
| `storage` | Authenticated case workspaces, local lifecycle, bounded external/committed-source leases, immutable ingest/integrity, disposable thumbnail caching, no-replace derivative publication, and atomic persistence; implemented contracts are documented in [Secure case-workspace contract](case-workspace.md), [Local case-lifecycle contract](case-lifecycle.md), [Bounded import-precheck contract](import-prechecks.md), [Immutable source-ingest contract](immutable-ingest.md), [Atomic manifest repository and autosave contract](manifest-repository.md), [Bounded PDF-thumbnail contract](pdf-thumbnails.md), and [Deterministic verified PDF-export contract](pdf-export.md) |
| `diagnostics` | Closed, content-free records, canonical structured logging, and deterministic safe summary rendering; PWS-0805 owns later preview/confirmation/bundle writing |

## Enforced import rules

`tools/check_import_boundaries.py` parses source files without importing them and applies these rules:

1. `domain` may import only Python standard-library modules, the root identity package, and `paperworkshell.domain` modules. All other first- and third-party imports are rejected.
2. UI direction is `paperworkshell.__main__` → `ui` → `app`; `app` cannot import either higher tier, and `ui` cannot import the composition entrypoint. All three must not directly import `paperworkshell.pdf`, `paperworkshell.imaging`, or `paperworkshell.ocr`. They may import the passive `worker` namespace and explicit `worker.client` or `worker.protocol` modules, but not worker-host modules. One exact worker-composition exception permits only `paperworkshell.__main__` to import `paperworkshell.worker.desktop_composition`; that GUI-loaded client composition may not import document adapters transitively. One exact derivative-publication exception permits that same composition root to import only `paperworkshell.export.pdf_destination`; neither exception grants application or UI modules general implementation access.
3. Worker, adapter, and service packages must not import `paperworkshell.app` or `paperworkshell.ui`; dependency direction never points back toward UI-facing code. GUI-safe `worker.client` and `worker.protocol` modules cannot import worker-host, document-adapter, or export-implementation modules. PDF, imaging, and OCR adapters must not import any part of the worker package.
4. Only the declared package layers are accepted. Adding another top-level layer requires updating this document, the checker, and positive/negative tests in the task that introduces it.
5. Third-party imports remain denied unless the owning task admits one exact package and the
   executable boundary names it. PWS-0105 admits `pikepdf` only in the `pdf` layer. PWS-0106 reuses
   that boundary for inherited page geometry and confines `pypdfium2` to the same worker-side
   `pdf` layer; `domain`, application/UI, GUI-safe worker client/protocol, storage, and every other
   adapter layer reject both engines. PWS-0111 admits only fully qualified `PySide6.QtCore`,
   `PySide6.QtGui`, and `PySide6.QtWidgets` imports in the `ui` layer; the root `PySide6` import,
   every other PySide6 module, and every non-UI PySide6 import remain rejected. PWS-0201 admits
   only the `PIL` package in the worker-only `imaging` adapter layer; domain, app, UI, storage,
   PDF, OCR, and worker orchestration remain unable to import it. PWS-0204 admits OCRmyPDF plus its
   required `pluggy` and `pydantic` imports only in the exact
   `paperworkshell.ocr.ocrmypdf_adapter` module, and admits `packaging.version` only in the exact
   `paperworkshell.ocr.tesseract_runner` module. Sibling OCR modules and every other layer remain
   unable to import those dependencies. PWS-0303 admits `fpdf`, `fontTools`, and `uharfbuzz` only
   in the exact `paperworkshell.pdf.overlay_renderer` module. The pure `overlays.rendering`
   contract and every sibling PDF module remain unable to import that font-rendering stack.
   Pikepdf remains confined to `pdf`; the exact
   `ocr` → `pdf.ocr_candidate_verifier` edge returns only a positive page count or closed failure
   after reopening the private candidate. No pikepdf object or general PDF feature crosses that
   seam. Every other layer retains its prior prohibition. The alternative
   standard-library GUI roots `tkinter`, `_tkinter`, and `turtle` are also rejected. The non-default
   PySide6-Addons renderer-evaluation group grants no source import boundary or product scope.
6. Calls through Python's standard dynamic-import APIs are rejected because they bypass static ownership checks. PWS-0204 uses static imports after the restricted host has completed isolation; the host catches an unavailable optional OCR dependency and omits that capability rather than discovering another implementation.
7. Every package initializer is a passive namespace: it must not import or re-export product layers or implementation modules. Consequently, every package namespace remains importable without optional UI or document engines.
8. `diagnostics` may import only the standard library, the root identity package, and its own
   modules. It cannot import case storage, worker, UI, document adapters, export implementations,
   networking, subprocess control, filesystem path APIs, or Python's general-purpose logging
   framework. Application composition may emit already validated diagnostic records through a
   structural sink; that does not reverse the dependency direction.

Imports inside `TYPE_CHECKING`, conditional blocks, or exception handlers still count: a prohibited dependency is an architectural violation even when a particular execution path does not currently load it. Relative and absolute imports are normalized before comparison.

## Boundary evolution

The checker enforces dependency ownership, not public API design or process isolation. Later tasks add typed protocols, a distinct GUI-safe worker client, a worker-host composition root, versioned RPC, concrete adapters, and operating-system restrictions. Desktop composition will wire UI, application services, storage, and the worker client; worker composition will wire the host and document adapters. Package initializers must not bridge those composition roots.

PWS-0112 keeps pure, path-free plan/report/outcome types in `domain.pdf_export` and use-case ports in
`app.pdf_export`. Native assembly, strict reopen, qpdf inspection, and PDFium rendering stay in the
restricted worker's `pdf` adapter. The `export.pdf_destination` module is a trusted desktop-side
adapter over authenticated `storage.export_destination`; it sees only the user-selected destination
and the already verified bounded candidate, never a third-party PDF object. The complete split is
specified in the [deterministic verified PDF-export contract](pdf-export.md).

PWS-0113 keeps path-free recovery results in `domain.recovery`; `app.recovery` coordinates a
structural startup store and an optional already-sanitized diagnostic sink. `storage.recovery`
loads authoritative manifests, invokes an injected export-history settlement hook, and owns exact
case cleanup. Durable retry phases and their codec live in `domain.pdf_export_retry`, history-only
settlement stays in `app.export_history`, and protected marker I/O stays in
`storage.export_retry`. No restart path creates a `storage -> app` edge, invokes a PDF engine, or
passes an external destination into recovery.

`paperworkshell.__main__.build_windows_startup_recovery_service` now owns the concrete Windows
history, retry, manifest, and startup-store wiring at the desktop composition root. `app.recovery`
remains ports/service-only and storage-agnostic. Desktop backend construction completes the
composed recovery before it creates worker resources, then passes only the path-free result into
the organizer backend. Committed-receipt restart integration exercises this same root; no
application or UI module may import concrete storage to duplicate it.

The independent `diagnostics` layer accepts only closed typed records and renders only a bounded
summary snapshot. It has no filesystem-path constructor and does not collect or export a support
bundle. The later PWS-0805 workflow must compose snapshot, preview, confirmation, and local bundle
writing outside this pure safe-data boundary. See [Startup recovery and sanitized
diagnostics](startup-recovery-and-diagnostics.md).

The same task extends the private Windows worker client with a standard-library-only, versioned
ACL-recovery journal. New v2 ledgers record trusted launcher resource paths, exact Windows object
identities, the current-user guard SID, and guard baselines because exact cold-restart revocation
requires them; strictly decoded v1 ledgers retain only their legacy bounded recovery meaning. The
adapter excludes mutable Python bytecode caches from new authority, binds interpreter cache lookup
to a fresh empty profile-private namespace, and retains strict exact-identity handling for every
old-v2 object. It exposes only path-free numeric recovery results.
The journal has no case-storage, application, protocol, host, or diagnostics import edge, and the
worker never receives it. Its launch ordering is specified in the
[restricted worker lifecycle contract](worker-lifecycle.md).

PWS-0101 makes `storage` the first namespace with concrete product modules. Its Windows adapter is
private to storage; callers use the fixed workspace, permission, quota, and sanitized-error
contracts described in [Secure case-workspace contract](case-workspace.md). A lexically resolved
path is never an authorization token, and later lifecycle/import/worker/export tasks must add
narrow broker operations instead of importing `storage._windows` or introducing general recursive
mutation.

PWS-0103 adds pure import policy in `domain.import_precheck` and a narrow external-source adapter in
`storage.source_precheck`. The private Win32 implementation authenticates one retained read-only
handle; it is not a general filesystem API. Preliminary PDF/image framing checks are deliberately
bounded and do not move document-engine parsing into the desktop process. The lease and deferrals
are specified in [Bounded import-precheck contract](import-prechecks.md).

PWS-0102 adds pure lifecycle result/error types in `domain.case_lifecycle`, a structural storage
protocol and one-active-case coordinator in `app.case_lifecycle`, and a concrete authenticated
Windows implementation in `storage.case_lifecycle`. Storage depends on domain contracts but never
imports `app`; desktop composition will inject the concrete store. Atomic initial publication,
retained leases, full runtime manifest validation, recovery-aware open/list, and case-scoped removal
are specified in the [Local case-lifecycle contract](case-lifecycle.md). The runtime validator uses
the standard library and a fingerprint-bound packaged schema rather than repository tools or a
third-party schema engine.

PWS-0104 adds path-free metadata, duplicate/integrity results, progress, and closed errors in
`domain.source_ingest`; `app.source_ingest` orchestrates only the structural prepare lease,
inspection, commit, progress/cancellation, and integrity protocols. `storage` implements the
retained-handle copy/hash and authenticated random case publication, then delegates its complete
manifest/checkpoint commit to the PWS-0109 transaction. Storage still does not import `app`, and
application/domain code does not import the private Windows adapter. Complete PDF/image inspection
arrives as typed metadata from its owning adapter through composition; storage never imports an
engine or worker host. The two-phase behavior is specified in the
[Immutable source-ingest contract](immutable-ingest.md).

PWS-0109 adds path-free repository/autosave contracts and complete validation in `domain`,
toolkit-free coalescing in `app`, and authenticated journal/replacement/recovery in `storage`.
Neither pure layer imports Windows, UI, renderer, or PDF code. The fixed checkpoint and CAS contract
are specified in the [Atomic manifest repository and autosave contract](manifest-repository.md).

PWS-0110 adds typed page intent, canonical replay, immutable session undo/redo, and defensive
manifest-candidate construction only in `domain.page_operations`. It consumes the path-free
PWS-0109 snapshot contract but does not import `app`, `storage`, worker, renderer, PDF, or UI code.
The command and concurrency rules are specified in the
[Page operation, replay, undo, and redo contract](page-operations.md).

PWS-0202 extends those pure page operations with version-coupled crop, deskew, contrast, and
background-cleanup intent and adds pure fixed-width transform/cache contracts in
`domain.image_transforms`. Pixel processing remains in the worker-only `imaging` adapter; neither
domain module imports Pillow, storage, worker orchestration, or UI code. Current manifest v2 is a
separately fingerprinted schema, while storage alone owns the copy-only transactional migration
from fully validated frozen v1 input.

PWS-0301 adds `domain.page_coordinates` as the pure standard-library authority for the released
normalized-box codec and staged normalized/PDF-user/raster/screen transforms. It imports no UI,
renderer, PDF engine, image engine, worker, storage, or adapter implementation. Existing page
operations explicitly re-export the canonical box type; image-transform intent retains its
closed error boundary while sharing the one million-unit constant. MediaBox/CropBox parsing stays
in the PDF adapter, raster allocation stays with the owning renderer, and callers pass only
validated primitive geometry into the model. Exact transform order, clipping policies, `/UserUnit`
distinction, and tolerance bounds are specified in the
[normalized page coordinate model](page-coordinate-model.md).

PWS-0302 adds `overlays.models` as a pure standard-library binding over the overlay-v1 records
already released in both manifest schemas. It depends only on pure domain coordinate and manifest
support, preserves persisted array order while deriving draw order from per-page `z_index`, and
derives crop/page visibility without changing saved intent. It neither imports a renderer nor
decodes signature-image bytes. The task makes no schema-version transition: v1-to-v2 migration
deep-copies overlay and asset state, and both reviewed manifest fingerprints remain unchanged. The
complete contract is specified in the [visual overlay domain model](overlay-domain-model.md).

PWS-0303 adds third-party-free request, geometry, resource, decoded-raster, destination, control,
placement, and result contracts in `overlays.rendering`. The exact
`pdf.overlay_renderer` adapter alone imports fpdf2, fontTools, uharfbuzz, and pikepdf. Its exact
runtime gate also authenticates Pillow (used transitively by fpdf2 to parse only the
adapter-generated PNG) and qpdf (the native library behind pikepdf), without importing `PIL` or
weakening the imaging-layer boundary. It consumes only validated overlay projections and
caller-owned metadata-free RGBA8 streams, emits one bounded
transparent overlay PDF page, sanitizes and strictly reopens it, then returns content-free facts.
It neither opens a case asset by path nor accepts a JPEG/PNG source. PWS-0305's
[`storage.signature_images`](signature-image-assets.md) adapter authenticates the immutable encoded
case asset and its `image.signature_raster` worker boundary decodes it before constructing the
pixel-bound raster contract; PWS-0307 must import the verified page as a Form XObject and flatten it.
The complete split is specified in the [deterministic overlay rendering
contract](overlay-rendering.md).

PWS-0204 adds path-free OCR request, progress, error, report, and volatile-candidate contracts in
`domain.ocr`. `worker.ocr_broker` owns source-lease authentication, exact reduced handle transfer,
one-shot lifecycle, and broker-side candidate authentication; it imports no OCR engine.
`worker.ocr_operation` owns the restricted host operation and invokes the worker-only
`ocr.ocrmypdf_adapter`, which statically imports the exact admitted OCRmyPDF/Tesseract support
closure. The adapter calls only `pdf.ocr_candidate_verifier` to reopen its private candidate; that
narrow PDF seam converts pikepdf state to a page count or closed failure, and no third-party object
crosses it. The operation produces only a bounded broker-owned volatile PDF candidate, not OCR
text, a case artifact, manifest mutation, searchable-output claim, or export. Exact execution and
deferrals are specified in the [bounded local OCR worker contract](ocr-worker.md).

PWS-0206 adds pure manifest-projection, eligibility, per-page text fact, verification-report, and
outcome contracts in `domain.searchable_pdf_export`. The application coordinates only structural
manifest/source/searchable-broker/publication/history ports. `pdf.searchable_export_adapter` is the
exact restricted-worker composition seam allowed to invoke the existing Pillow normalization/
transform adapters and OCRmyPDF adapter for one private page; it does not route work through the
`image.*` or `ocr.process` RPCs. Pikepdf/qpdf reopen, PDFium render comparison, and PDFium text-
presence checks remain worker/PDF-adapter work; extracted text and third-party objects never cross
into domain, app, storage, or UI. This is an additive versioned capability: it neither redefines
`ocr.process` v1 or `pdf.export` v1 nor changes manifest v2/export-history v1 meaning. The page-
local contract and resource bounds are specified in
[Verified searchable mixed-document output](searchable-mixed-output.md).

`worker.client.tesseract_runtime` is a standard-library-only helper for the private Windows
launcher. It authenticates the code-owned manifest fingerprint and one explicitly configured
runtime closure before those paths can join the launcher's exact-object grant. It does not import
an OCR/PDF adapter, accept a case/document path, start a process, or weaken the GUI-safe client's
prohibition on host/document-engine imports.

PWS-0107 makes the worker split concrete. GUI-safe `worker.client` modules own only cached lifecycle
state and abstract process/transport contracts; `worker.protocol` owns bounded framing, bootstrap,
typed messages, and pure conversation state. The private Windows launcher, host composition,
runtime coordinator, and operation handlers stay outside those GUI-safe subpackages. A
zero-capability AppContainer, retained Job Object, explicit three-handle inheritance, fixed launch
grant, minimal environment, and fresh-generation restart are specified in the
[Restricted worker lifecycle contract](worker-lifecycle.md) and
[ADR 0004](../adr/0004-windows-worker-isolation.md).

PWS-0105 introduces the first document operation without creating a `storage -> worker` or
`worker -> storage` implementation edge. Storage registers the already sealed PWS-0104 pending
source and lends one independently positioned exact read handle through a structural protocol.
Trusted composition duplicates that handle and a new result-pipe writer into a ready worker with
explicit reduced access. `worker.pdf_operation` composes the host-side adapter; the `pdf` adapter
itself remains independent of worker/storage code. GUI-safe client/protocol modules know only typed
handle-purpose and operation contracts, not the private storage Windows adapter.

The worker has no case-root ACL or lexical case path. It copies and rehashes the exact brokered
source into worker-private ephemeral storage, closes the case handle, parses that stream, and
writes one bounded correlated result frame. The trusted broker terminates the one-shot generation
before releasing source/result authority and validates that frame before typed metadata can reach
application code. Later operations must repeat this pattern with their own exact access profiles;
PWS-0105 is not approval for a general directory capability, a path in RPC, or arbitrary handle
parameters. See the [PDF structural-inspection contract](pdf-structural-inspection.md).

A new external engine is not admitted by editing code alone: the dependency policy and licensing
review in PWS-0005 and the engine-specific roadmap task must approve it first. PWS-0106 now assigns
worker-only renderer ownership through the
[PDF page-rendering contract](pdf-page-rendering.md), implements the renderer-neutral adapter with
pypdfium2/PDFium, and records the accepted evidence-based selection in
[ADR 0005](../adr/0005-pdf-renderer-selection.md). The exact Qt PDF fallback remains evaluation-only
with no source-import boundary, product scope, or enabled shipment relationship.

PWS-0111 adds the first concrete `ui` layer and admits only PySide6-Essentials' QtCore, QtGui, and
QtWidgets modules there. Toolkit objects do not cross into `app`, `domain`, worker, storage, or
document adapters. The UI receives only typed application state and the PWS-0108 validated RGBA8
display contract; it does not parse PDFs, open arbitrary paths, or import Qt PDF. The exact
`__main__` → `worker.desktop_composition` exception is the desktop composition root, not a new
application/UI dependency direction. PWS-0114 adds only the exact
`__main__` → `export.pdf_destination` publication seam needed to wire the existing structural
application port; all other export implementations remain prohibited. Artifact, LGPL, source, and
Windows packaging consequences are recorded in [ADR 0006](../adr/0006-pyside6-ui-runtime.md).

PWS-0108 adds pure cache-key/artifact/snapshot/progress contracts in `domain.pdf_thumbnails` and
serial, toolkit-free batch orchestration in `app.pdf_thumbnails`. The application depends only on a
structural one-page generator protocol; it does not import concrete storage, Windows, worker-host,
PDF-engine, or cache implementations. UI-facing snapshots contain validated fixed RGBA8 bytes but
no engine object, path, filename, source digest, or arbitrary encoded image.

The concrete thumbnail broker is worker-side trusted composition over structural committed-source,
cache-stage, worker-client, and process-lifecycle ports. `storage.source_ingest` authenticates and
lends one current manifest-bound PDF handle; `storage.thumbnail_cache` owns quota reservation,
exact stage handles, fixed artifact readback, no-replace publication, invalidation, and cleanup.
Storage does not import a worker implementation, and the worker broker does not acquire a general
storage manager or case path. The host operation invokes the existing renderer-neutral PDF adapter
through an exact source handle and exact write-only stage handle; the `pdf` package remains
independent of worker/storage code. PWS-0204 adds only the exact
`ocr` → `pdf.ocr_candidate_verifier` page-count seam; it does not grant the OCR layer renderer
ownership or a general PDF API.

For the PWS-0108 slice, `worker.protocol` owns the additive `pdf.thumbnail` capability, closed
scalar/reference contract, and output correlation. The present registry also contains the exact
`pdf.inspect`, `pdf.export`, and `image.normalize` operations described by their owning contracts;
none loosens the shared envelope or reference grammar. The GUI-safe profile-identity module contains only fixed project
metadata and SHA-256 computation; it imports no native engine. Concrete stage creation, Windows
handle reduction, worker-private source copying, rendering, and publication stay behind their
existing private/platform boundaries. The full split is specified in the
[bounded PDF-thumbnail service contract](pdf-thumbnails.md).

If a future task needs a new cross-package edge, that task must show why the edge preserves the frozen architecture, update this document and the executable policy together, and add a positive and negative boundary test. It must not suppress an individual import violation.
