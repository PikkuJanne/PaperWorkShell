# PaperWorkShell dependency boundaries

- Status: Binding implementation policy
- Task: PWS-0002
- Decisions: PWS-D002, PWS-D010, PWS-D012, PWS-D014, PWS-D015, PWS-D016, PWS-D017

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
| `worker` | GUI-safe client/protocol boundaries plus separately composed host lifecycle, progress, cancellation, and limits |
| `pdf` | PDF interfaces and worker-side pikepdf/qpdf implementations |
| `imaging` | Image inspection and transformation interfaces/implementations |
| `ocr` | OCR interfaces and worker-side OCRmyPDF/Tesseract implementations |
| `overlays` | Normalized visual-overlay intent and rendering boundaries |
| `preflight` | Typed, deterministic findings; never presentation dialogs |
| `export` | Output planning, verification, and derivative commit behavior |
| `templates` | Declarative, versioned template loading and validation |
| `storage` | Authenticated case workspaces and atomic persistence; its implemented PWS-0101 contract is documented in [Secure case-workspace contract](case-workspace.md) |
| `diagnostics` | Sanitized logging and previewable diagnostic data |

## Enforced import rules

`tools/check_import_boundaries.py` parses source files without importing them and applies these rules:

1. `domain` may import only Python standard-library modules, the root identity package, and `paperworkshell.domain` modules. All other first- and third-party imports are rejected.
2. UI direction is `paperworkshell.__main__` → future `ui` → `app`; `app` cannot import either higher tier, and `ui` cannot import the composition entrypoint. All three must not directly import `paperworkshell.pdf`, `paperworkshell.imaging`, or `paperworkshell.ocr`. They may import the passive `worker` namespace and explicit `worker.client` or `worker.protocol` modules, but not worker-host modules.
3. Worker, adapter, and service packages must not import `paperworkshell.app` or a future `paperworkshell.ui`; dependency direction never points back toward UI-facing code. GUI-safe `worker.client` and `worker.protocol` modules cannot import worker-host, document-adapter, or export-implementation modules. PDF, imaging, and OCR adapters must not import any part of the worker package.
4. Only the declared package layers are accepted. Adding another top-level layer requires updating this document, the checker, and positive/negative tests in the task that introduces it.
5. PWS-0002 keeps the runtime dependency set empty, so all third-party imports from product code are rejected. The alternative standard-library GUI roots `tkinter`, `_tkinter`, and `turtle` are also rejected. Future ownership is reserved—but not approved—for PySide6 in a dedicated UI layer, pikepdf in `pdf`, and OCRmyPDF/Tesseract integration in `ocr`. Renderer ownership remains undecided.
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

A new external engine is not admitted by editing code alone: the dependency policy and licensing review in PWS-0005 and the engine-specific roadmap task must approve it first. Renderer ownership remains intentionally unspecified until PWS-0106 records its evidence-based ADR.

If a future task needs a new cross-package edge, that task must show why the edge preserves the frozen architecture, update this document and the executable policy together, and add a positive and negative boundary test. It must not suppress an individual import violation.
