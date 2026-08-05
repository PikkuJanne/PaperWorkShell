# PaperWorkShell dependency boundaries

- Status: Binding implementation policy
- Task: PWS-0002
- Extensions: PWS-0102 local case lifecycle; PWS-0104 immutable ingest; PWS-0105 PDF
  structural inspection; PWS-0106 PDF page rendering; PWS-0107 restricted worker lifecycle;
  PWS-0108 bounded PDF thumbnails
- Decisions: PWS-D002, PWS-D010, PWS-D012, PWS-D013, PWS-D014, PWS-D015, PWS-D016, PWS-D017, PWS-D036, PWS-D038

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
| `worker` | GUI-safe client/protocol boundaries plus separately composed host lifecycle, exact object brokerage, document operations, progress, cancellation, and limits; thumbnail integration is documented in the [bounded PDF-thumbnail contract](pdf-thumbnails.md) |
| `pdf` | PDF interfaces and worker-side pikepdf/qpdf implementations; implemented and in-progress contracts are documented in the [PDF structural-inspection contract](pdf-structural-inspection.md) and [PDF page-rendering contract](pdf-page-rendering.md) |
| `imaging` | Image inspection and transformation interfaces/implementations |
| `ocr` | OCR interfaces and worker-side OCRmyPDF/Tesseract implementations |
| `overlays` | Normalized visual-overlay intent and rendering boundaries |
| `preflight` | Typed, deterministic findings; never presentation dialogs |
| `export` | Output planning, verification, and derivative commit behavior |
| `templates` | Declarative, versioned template loading and validation |
| `storage` | Authenticated case workspaces, local lifecycle, bounded external/committed-source leases, immutable ingest/integrity, disposable thumbnail caching, and atomic persistence; implemented contracts are documented in [Secure case-workspace contract](case-workspace.md), [Local case-lifecycle contract](case-lifecycle.md), [Bounded import-precheck contract](import-prechecks.md), [Immutable source-ingest contract](immutable-ingest.md), and [Bounded PDF-thumbnail contract](pdf-thumbnails.md) |
| `diagnostics` | Sanitized logging and previewable diagnostic data |

## Enforced import rules

`tools/check_import_boundaries.py` parses source files without importing them and applies these rules:

1. `domain` may import only Python standard-library modules, the root identity package, and `paperworkshell.domain` modules. All other first- and third-party imports are rejected.
2. UI direction is `paperworkshell.__main__` → future `ui` → `app`; `app` cannot import either higher tier, and `ui` cannot import the composition entrypoint. All three must not directly import `paperworkshell.pdf`, `paperworkshell.imaging`, or `paperworkshell.ocr`. They may import the passive `worker` namespace and explicit `worker.client` or `worker.protocol` modules, but not worker-host modules.
3. Worker, adapter, and service packages must not import `paperworkshell.app` or a future `paperworkshell.ui`; dependency direction never points back toward UI-facing code. GUI-safe `worker.client` and `worker.protocol` modules cannot import worker-host, document-adapter, or export-implementation modules. PDF, imaging, and OCR adapters must not import any part of the worker package.
4. Only the declared package layers are accepted. Adding another top-level layer requires updating this document, the checker, and positive/negative tests in the task that introduces it.
5. Third-party imports remain denied unless the owning task admits one exact package and the
   executable boundary names it. PWS-0105 admits `pikepdf` only in the `pdf` layer. PWS-0106 reuses
   that boundary for inherited page geometry and confines `pypdfium2` to the same worker-side
   `pdf` layer; `domain`, application/UI, GUI-safe worker client/protocol, storage, and every other
   adapter layer reject both engines. The alternative standard-library GUI roots `tkinter`,
   `_tkinter`, and `turtle` are also rejected. Future ownership is reserved—but not approved—for
   PySide6 in a dedicated UI layer and OCRmyPDF/Tesseract integration in `ocr`. The non-default
   PySide6-Addons renderer-evaluation group grants no source import boundary or product scope.
6. Calls through Python's standard dynamic-import APIs are rejected because they bypass static ownership checks. A later adapter task may add a narrow, tested exception only when an admitted dependency requires one.
7. Every package initializer is a passive namespace: it must not import or re-export product layers or implementation modules. Consequently, every package namespace remains importable without optional UI or document engines.

Imports inside `TYPE_CHECKING`, conditional blocks, or exception handlers still count: a prohibited dependency is an architectural violation even when a particular execution path does not currently load it. Relative and absolute imports are normalized before comparison.

## Boundary evolution

The checker enforces dependency ownership, not public API design or process isolation. Later tasks add typed protocols, a distinct GUI-safe worker client, a worker-host composition root, versioned RPC, concrete adapters, and operating-system restrictions. Desktop composition will wire UI, application services, storage, and the worker client; worker composition will wire the host and document adapters. Package initializers must not bridge those composition roots.

The passive `export` namespace is not a low-level document adapter, but no `export` submodule is currently GUI-safe. Application code may eventually use pure export-plan types and interfaces, while writing, reopening, rendering, and verification remain worker-side behavior. PWS-0112 must name safe planning modules and extend the executable policy before adding them; PWS-0002 does not guess that future module split.

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
imports `app`; desktop composition will inject the concrete store. The adapter parses only the
bounded lifecycle envelope and does not import repository tools or a third-party schema engine.
Atomic initial publication, retained leases, and case-scoped removal are specified in the
[Local case-lifecycle contract](case-lifecycle.md). PWS-0109 retains ownership of general runtime
manifest validation, autosave/journal behavior, migration, and arbitrary later transactions.

PWS-0104 adds path-free metadata, duplicate/integrity results, progress, and closed errors in
`domain.source_ingest`; `app.source_ingest` orchestrates only the structural prepare lease,
inspection, commit, progress/cancellation, and integrity protocols. `storage` implements the
retained-handle copy/hash, authenticated random case publication, and narrow import-only manifest
append. Storage still does not import `app`, and application/domain code does not import the private
Windows adapter. Complete PDF/image inspection arrives as typed metadata from its owning adapter
through composition; storage never imports an engine or worker host. The two-phase and
PWS-0109/PWS-0110 handoffs are specified in the
[Immutable source-ingest contract](immutable-ingest.md).

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
independent of worker/storage code and is still the only layer that imports pikepdf/pypdfium2.

`worker.protocol` knows only the additive `pdf.thumbnail` capability, closed scalar/reference
contract, and output correlation. The GUI-safe profile-identity module contains only fixed project
metadata and SHA-256 computation; it imports no native engine. Concrete stage creation, Windows
handle reduction, worker-private source copying, rendering, and publication stay behind their
existing private/platform boundaries. The full split is specified in the
[bounded PDF-thumbnail service contract](pdf-thumbnails.md).

If a future task needs a new cross-package edge, that task must show why the edge preserves the frozen architecture, update this document and the executable policy together, and add a positive and negative boundary test. It must not suppress an individual import violation.
