# PaperWorkShell Frozen Decision Register

| Field | Value |
|---|---|
| Register version | 0.4.0 |
| Status | **Frozen** |
| Effective date | 2026-07-14 |
| Last amended | 2026-11-04 |
| Applies to | Product scope, architecture, security, release gates, and terminology |

A frozen decision is binding on implementation and planning until the maintainer explicitly approves a replacement. A technical obstacle is not permission to bypass a decision. Changes use the procedure at the end of this file and must update `ROADMAP.md`, `TASKS.yaml`, and affected prompts or schemas in the same change.

Status vocabulary:

- **FROZEN** — implement as written.
- **SUPERSEDED** — retained for history and linked to its replacement.
- **REJECTED** — evaluated and intentionally not adopted.

---

## Decision index

| ID | Decision |
|---|---|
| PWS-D001 | Product name is PaperWorkShell |
| PWS-D002 | Product is a guided packet builder, not a general PDF editor |
| PWS-D003 | Roadmap is dependency-based and gate-driven |
| PWS-D004 | Windows-first desktop delivery — historical, superseded by PWS-D043 |
| PWS-D005 | Offline-first, no account, no required service |
| PWS-D006 | Generic portal application is the first workflow |
| PWS-D007 | v0.1 inputs are PDF, JPEG, and PNG |
| PWS-D008 | Outputs are one PDF, multiple PDFs, or ZIP plus summaries |
| PWS-D009 | Imported originals are immutable and hashed |
| PWS-D010 | The versioned case manifest is authoritative |
| PWS-D011 | Editing is non-destructive until export |
| PWS-D012 | UI and document processing are separate processes |
| PWS-D013 | Worker processing has no network and bounded resources |
| PWS-D014 | Initial application stack is Python with PySide6 |
| PWS-D015 | PDF structure layer is pikepdf/qpdf behind adapters |
| PWS-D016 | OCR stack is OCRmyPDF with Tesseract behind adapters |
| PWS-D017 | PDF rendering is replaceable and selected by an evidence-based ADR |
| PWS-D018 | Form completion is overlay-first |
| PWS-D019 | Dynamic XFA and arbitrary PDF scripts are unsupported |
| PWS-D020 | Visual signatures, digital signatures, and qualified signatures remain distinct |
| PWS-D021 | Cryptographic signature creation is outside v0.1 |
| PWS-D022 | Permanent redaction is a separately gated security subsystem |
| PWS-D023 | Unsafe visual covering is never called redaction |
| PWS-D024 | Compression uses staged policy and a legibility floor |
| PWS-D025 | Templates are declarative, versioned, and non-executable |
| PWS-D026 | Automation may suggest, but the user confirms requirement satisfaction |
| PWS-D027 | Telemetry is off by default and routine logs contain no document content |
| PWS-D028 | Case storage uses random internal names and restrictive permissions |
| PWS-D029 | Exports never overwrite originals and are reopened and verified |
| PWS-D030 | Accessibility is designed from the first UI task |
| PWS-D031 | Assisted use is supported without multi-user administration in v0.1 |
| PWS-D032 | No AI capability is required for the core product |
| PWS-D033 | Eligibility, legal advice, identity services, and portal submission are non-goals |
| PWS-D034 | Dependencies are pinned, inventoried, and reviewed for licensing |
| PWS-D035 | Test corpora contain synthetic or properly anonymized data only |
| PWS-D036 | Schemas and RPC are versioned and migratable |
| PWS-D037 | User-facing validation claims remain concrete and limited |
| PWS-D038 | External tools receive structured arguments, never shell command strings |
| PWS-D039 | Diagnostics are sanitized and previewable before export |
| PWS-D040 | v0.1 ends at M4; v1.0 requires all release gates |
| PWS-D041 | Low-resource compatibility is enforced through bounded architecture and automated resource budgets |
| PWS-D042 | PaperWorkShell remains standalone and ThirdLife portfolio assembly is late-bound |
| PWS-D043 | Windows 11 x64-only product and release scope |

---

## Detailed decisions

## PWS-D001 — Product name is PaperWorkShell

**Status:** FROZEN

**Decision:** All new code, package identifiers, UI text, documentation, task IDs, and release artifacts use **PaperWorkShell**. Historical working names are not used as current product names.

**Consequences:** Repository naming, Python package naming, executable naming, and user-facing terminology must be checked during bootstrap. A platform-safe lowercase package identifier such as `paperworkshell` may be used internally.

## PWS-D002 — Product is a guided packet builder, not a general PDF editor

**Status:** FROZEN

**Decision:** PaperWorkShell optimizes for producing a submission-ready document package through a guided workflow. General paragraph editing, arbitrary low-level PDF manipulation, and a toolbox-first interface are outside the product contract.

**Consequences:** New feature proposals must be justified by the packet workflow. UI navigation follows task stages rather than exposing every engine capability as a toolbar command.

## PWS-D003 — Roadmap is dependency-based and gate-driven

**Status:** FROZEN

**Decision:** Work is selected from `TASKS.yaml` according to dependencies and milestone gates rather than calendar promises.

**Consequences:** A milestone is not complete because time elapsed. Gate tasks record evidence. Parallel work is allowed only when dependencies and edit surfaces permit it.

## PWS-D004 — Windows-first desktop delivery

**Status:** SUPERSEDED for current platform scope by `PWS-D043` on 2026-11-04. The original Windows-first decision and its `PWS-D041` low-resource amendment remain below as historical records.

**Decision:** The first supported distribution target is a Windows desktop application. Architecture remains portable, but macOS and Linux packaging do not block v0.1.

**Consequences:** Windows path behavior, signing, installer behavior, NVDA, display scaling, and low-resource behavior are first-class engineering targets. Platform-specific code stays behind adapters. `PWS-D041` replaces only the former mandatory physical-hardware interpretation of low-resource evidence; Windows-first delivery itself is unchanged.

**Supersession:** The retained wording above is not a current commitment to other operating systems or architectures. The explicit maintainer-approved replacement is `PWS-D043`, documented in [ADR 0019](docs/adr/0019-windows-11-x64-only-scope.md). No historical platform plan or missing test is relabeled as a pass.

## PWS-D005 — Offline-first, no account, no required service

**Status:** FROZEN

**Decision:** All core document processing and case management work without an account, cloud service, or network connection.

**Consequences:** The product must not require sign-in, remote OCR, cloud storage, or server-side validation. Optional future update checks must be separable from document processing and must never transmit document content.

## PWS-D006 — Generic portal application is the first workflow

**Status:** FROZEN

**Decision:** The first complete template is a generic application packet with an application form, identity document, proof of address, and supporting evidence under file-count, naming, and size constraints.

**Consequences:** Jurisdiction-specific eligibility logic and direct portal integrations are deferred. The engine and schema remain generic.

## PWS-D007 — v0.1 inputs are PDF, JPEG, and PNG

**Status:** FROZEN

**Decision:** The supported v0.1 import formats are PDF, JPEG, and PNG, validated by content rather than extension.

**Consequences:** HEIC, TIFF, DOCX, ODT, email import, direct scanner input, and other formats receive explicit unsupported messages. New decoders require threat-model and packaging review.

## PWS-D008 — Outputs are one PDF, multiple PDFs, or ZIP plus summaries

**Status:** FROZEN

**Decision:** PaperWorkShell exports one combined PDF, several named PDFs, or a ZIP of PDFs, together with a human-readable package summary and optional technical manifest.

**Consequences:** Direct upload and print-driver behavior are not required for v0.1. ZIP creation must use safe, normalized names and no path traversal.

## PWS-D009 — Imported originals are immutable and hashed

**Status:** FROZEN

**Decision:** Import copies each source into the case workspace, calculates SHA-256, and treats that copy as immutable. User originals outside the case are never modified.

**Consequences:** All edits produce manifest operations or derivatives. Integrity checks can detect accidental mutation. Delete operations remove references or case copies according to retention policy, not rewrite source content.

## PWS-D010 — The versioned case manifest is authoritative

**Status:** FROZEN

**Decision:** A validated, versioned manifest records source references, page sequence, transforms, overlays, checklist assignments, constraints, confirmations, and export history references.

**Consequences:** UI state is reconstructed from the manifest. Autosave is atomic. Manifest schemas and migrations are testable independent of the UI.

## PWS-D011 — Editing is non-destructive until export

**Status:** FROZEN

**Decision:** Reorder, rotate, crop, remove, split, combine, cleanup, and overlay operations are recorded as intent and replayed during preview/export rather than repeatedly rewriting PDFs.

**Consequences:** Undo/redo and crash recovery operate on commands or manifest revisions. Cache files are disposable and reproducible.

## PWS-D012 — UI and document processing are separate processes

**Status:** FROZEN

**Decision:** The GUI process orchestrates user interaction and pure domain logic. A separate worker parses, renders, converts, OCRs, compresses, inspects, and exports untrusted documents.

**Consequences:** The process boundary exists in Milestone 1, not as later hardening. UI modules must not directly depend on low-level PDF parsing APIs.

## PWS-D013 — Worker processing has no network and bounded resources

**Status:** FROZEN

**Decision:** The document worker is denied network access and is constrained by active-case filesystem scope, timeouts, memory, CPU, page count, image dimensions, and temporary-storage quotas as platform support permits.

**Consequences:** Operations fail with stable, sanitized errors when limits are exceeded. A security test verifies network-egress denial.

## PWS-D014 — Initial application stack is Python with PySide6

**Status:** FROZEN

**Decision:** The initial desktop implementation uses Python and PySide6, with an exact supported CPython minor and dependency workflow pinned during `PWS-0001`.

**Consequences:** Distribution and LGPL obligations are documented. Domain logic remains UI-independent so a future UI replacement is possible.

## PWS-D015 — PDF structure layer is pikepdf/qpdf behind adapters

**Status:** FROZEN

**Decision:** pikepdf and qpdf provide the initial PDF structure, assembly, repair, inspection, optimization, and structural-check layer behind internal interfaces.

**Consequences:** Application code does not scatter direct pikepdf calls. Library-specific behavior receives contract tests and stable internal error mapping.

## PWS-D016 — OCR stack is OCRmyPDF with Tesseract behind adapters

**Status:** FROZEN

**Decision:** OCRmyPDF orchestrates local OCR and scan-improvement work with Tesseract recognition, both hidden behind an internal OCR adapter.

**Consequences:** Language data is modular. OCR runs in the worker, supports cancellation, and reports uncertainty as a review signal rather than fact.

## PWS-D017 — PDF rendering is replaceable and selected by an evidence-based ADR

**Status:** FROZEN

**Decision:** Rendering is accessed through an internal interface. The concrete renderer is selected in `PWS-0106` using security, license, packaging, performance, and fidelity evidence.

**Consequences:** No feature may depend on renderer-specific types outside the adapter. A renderer change must not alter domain or manifest formats.

## PWS-D018 — Form completion is overlay-first

**Status:** FROZEN

**Decision:** v0.1 form completion uses controlled visual overlays for text, dates, checkmarks, crosses, initials, signature images, and simple marks, flattened during export.

**Consequences:** Existing AcroForm fields may be detected or used experimentally where safe, but arbitrary native form behavior is not required for v0.1.

## PWS-D019 — Dynamic XFA and arbitrary PDF scripts are unsupported

**Status:** FROZEN

**Decision:** Dynamic XFA, JavaScript-driven forms, calculation-heavy forms, rich-text form behavior, cryptographic signature fields, and arbitrary document scripting are unsupported editing targets.

**Consequences:** Preflight detects and explains unsupported interactive content. The original remains available for external handling.

## PWS-D020 — Visual signatures, digital signatures, and qualified signatures remain distinct

**Status:** FROZEN

**Decision:** UI and documentation distinguish a placed signature image, a PDF cryptographic signature, and a regulated qualified electronic signature.

**Consequences:** The initial action is named along the lines of “Place signature image.” No text implies that a visual mark creates cryptographic or qualified status.

## PWS-D021 — Cryptographic signature creation is outside v0.1

**Status:** FROZEN

**Decision:** v0.1 detects existing digital signatures and warns before modification but does not create, validate, timestamp, or manage certificates for digital signatures.

**Consequences:** Any modified signed source becomes an explicit unsigned derivative. Future signing work is a separate project and may use an external trust-service handoff.

## PWS-D022 — Permanent redaction is a separately gated security subsystem

**Status:** FROZEN

**Decision:** Permanent redaction is developed only in Milestone 6 and remains disabled until all extraction, revision, metadata, attachment, OCR, viewer, and resave tests pass plus independent review approves the gate.

**Consequences:** Redaction cannot be pulled into v0.1 for schedule reasons. Failed verification blocks export.

## PWS-D023 — Unsafe visual covering is never called redaction

**Status:** FROZEN

**Decision:** A rectangle, highlight, annotation, or overlay that leaves source content intact is never labeled as permanent removal or redaction.

**Consequences:** The UI may offer ordinary shapes or highlighting, but terminology and icons must prevent confusion with the security feature.

## PWS-D024 — Compression uses staged policy and a legibility floor

**Status:** FROZEN

**Decision:** Target-size export first removes safe overhead, then incrementally changes images with previewable policy. It stops at a configured legibility floor and may report that a target is unsafe or unattainable.

**Consequences:** PaperWorkShell never silently destroys readability merely to hit a byte limit. The user can see affected pages and policy choices.

## PWS-D025 — Templates are declarative, versioned, and non-executable

**Status:** FROZEN

**Decision:** Templates contain schema-validated data for instructions, categories, counts, order, naming, size, output, warnings, confirmations, locale, provenance, and expiry. They contain no executable code or scripts.

**Consequences:** Unknown fields and invalid versions fail safely. Community templates are inspectable and can be checksum- or signature-verified.

## PWS-D026 — Automation may suggest, but the user confirms requirement satisfaction

**Status:** FROZEN

**Decision:** Classification or OCR may suggest categories, duplicates, blank pages, or quality concerns. The user confirms document assignment and substantive review.

**Consequences:** No automated classifier silently marks a legal or administrative requirement complete. Confidence is presented as uncertainty.

## PWS-D027 — Telemetry is off by default and routine logs contain no document content

**Status:** FROZEN

**Decision:** There is no default content telemetry. Routine logs use internal identifiers, engine versions, operation timing, counts, resource data, generic codes, and sanitized stacks only.

**Consequences:** Logs exclude OCR text, form values, names, addresses, original filenames, full paths, metadata values, thumbnails, and signature images. Tests enforce prohibited-field absence.

## PWS-D028 — Case storage uses random internal names and restrictive permissions

**Status:** FROZEN

**Decision:** Cases live under the current OS user profile with restrictive permissions and random internal filenames. Display names are retained only in the protected manifest.

**Consequences:** Temporary and cache directories are per case. Delete-after-export and later assisted retention controls operate at case scope. The product never promises forensic erasure.

## PWS-D029 — Exports never overwrite originals and are reopened and verified

**Status:** FROZEN

**Decision:** Export writes a new derivative, reopens it, parses it, checks expected page count and policy, and renders it before reporting success.

**Consequences:** Verification failures do not replace a previous good export. Success messages state concrete checks rather than universal validity.

## PWS-D030 — Accessibility is designed from the first UI task

**Status:** FROZEN

**Decision:** Core operations have keyboard alternatives, logical focus, accessible labels, scalable UI, non-color status, progress announcements, and cancelable long work from their first implementation.

**Consequences:** Drag-and-drop is optional convenience, never the only path. Windows testing includes keyboard-only operation and NVDA. Output OCR is not claimed to be PDF/UA conformance.

## PWS-D031 — Assisted use is supported without multi-user administration in v0.1

**Status:** FROZEN

**Decision:** The desktop design accounts for consent, deletion, handover, sanitized diagnostics, and shared-computer retention, but v0.1 does not add organization accounts, roles, multi-case staff dashboards, or centralized audit.

**Consequences:** Assisted-mode controls arrive in Milestone 7 without creating a server product.

## PWS-D032 — No AI capability is required for the core product

**Status:** FROZEN

**Decision:** The complete core workflow must function with deterministic local document tooling. AI classification or correction may be evaluated later as optional, local, and confirmatory.

**Consequences:** Milestone completion cannot depend on cloud models or probabilistic automation.

## PWS-D033 — Eligibility, legal advice, identity services, and portal submission are non-goals

**Status:** FROZEN

**Decision:** PaperWorkShell packages documents. It does not determine eligibility, provide legal advice, act as an identity provider, guarantee acceptance, or submit documents to external portals.

**Consequences:** Templates and messages remain procedural. The final summary identifies packaging checks and user review needs only.

## PWS-D034 — Dependencies are pinned, inventoried, and reviewed for licensing

**Status:** FROZEN

**Decision:** Runtime and build dependencies are pinned for releases, their licenses and distribution obligations are recorded, release hashes are verified where applicable, and an SBOM is generated before v1.0.

**Consequences:** A dependency cannot be added solely for convenience without security, license, maintenance, and packaging review.

## PWS-D035 — Test corpora contain synthetic or properly anonymized data only

**Status:** FROZEN

**Decision:** Public and routine development fixtures do not contain real applicant, financial, medical, identity, employment, or signature data.

**Consequences:** Test generation and anonymization are documented. Bug reports using sensitive samples stay outside the repository and follow an explicit handling process.

## PWS-D036 — Schemas and RPC are versioned and migratable

**Status:** FROZEN

**Decision:** Case manifests, templates, and worker RPC messages carry explicit versions. Compatibility behavior and migrations are covered by fixtures and tests.

**Consequences:** Unknown future versions fail without mutation. Supported migrations preserve immutable-source identity and operation intent.

## PWS-D037 — User-facing validation claims remain concrete and limited

**Status:** FROZEN

**Decision:** PaperWorkShell reports facts such as file count, size, assigned categories, detected issues, and checks performed. It does not claim “everything is valid” or guarantee portal acceptance.

**Consequences:** Wording reviews are part of preflight and package-summary tasks. Errors and warnings distinguish machine checks from human confirmations.

## PWS-D038 — External tools receive structured arguments, never shell command strings

**Status:** FROZEN

**Decision:** Worker subprocesses are launched without shell interpolation, using explicit executable paths, argument arrays, controlled environments, and bounded input/output handling.

**Consequences:** Command-injection tests are mandatory. Template or filename text cannot become executable command content.

## PWS-D039 — Diagnostics are sanitized and previewable before export

**Status:** FROZEN

**Decision:** Routine diagnostics follow the logging prohibition. A user-authorized diagnostic bundle shows an exact human-readable inventory before it is created.

**Consequences:** Diagnostic creation is opt-in, local, and testable. Content samples are excluded unless a future explicit workflow receives separate approval.

## PWS-D040 — v0.1 ends at M4; v1.0 requires all release gates

**Status:** FROZEN

**Decision:** Milestones 0–4 define the v0.1 pilot. Milestones 5–8 define compatibility, redaction, inclusion, and release hardening required for the intended v1.0 candidate.

**Consequences:** Permanent redaction is not a v0.1 promise. A public v1.0 label is not used while a required gate is incomplete.

## PWS-D041 — Low-resource compatibility is enforced through bounded architecture and automated resource budgets

**Status:** FROZEN

**Decision:** PaperWorkShell remains Windows-first and must use conservative, bounded resource behavior by default. Automated suites enforce versioned memory, temporary-storage, cache, queue, concurrency, responsiveness, cancellation, and low-disk budgets with synthetic workloads and injectable constraints. Testing on physical low-end devices is useful when available but is not a milestone or release prerequisite.

**Consequences:** Core functionality cannot require a discrete GPU. Heavy document work remains outside the GUI process. Full packets are not eagerly rasterized or retained at full resolution. Caches, queues, subprocesses, and temporary storage have explicit limits and fail safely when exceeded. Performance evidence distinguishes measured software budgets from physical-hardware certification. PaperWorkShell must not describe a named low-end device, processor count, RAM amount, GPU class, SSD, or HDD as verified or certified unless that physical configuration is actually tested later. The accepted rationale, migration, and affected surfaces are recorded in `docs/adr/0007-low-resource-budgets.md`.

## PWS-D042 — PaperWorkShell remains standalone and ThirdLife portfolio assembly is late-bound

**Status:** FROZEN

**Decision:** PaperWorkShell is an independently useful, independently versioned Team A / A1 product in the ThirdLife portfolio. Its development, build, tests, installation, launch, update or repair, document workflow, recovery, migration, support, uninstall, and release gates do not require ThirdLife, a sibling product, a sibling repository or branch, a sibling database or service, or a private cross-project fixture. PaperWorkShell has no permanent integration lane and introduces no shared user-content database, workspace, SDK, runtime schema, universal job engine, plugin framework, background service, monorepo mandate, or synchronized release cadence for portfolio integration.

Future ThirdLife Deployment and Suite Assembly work may consume only an exact frozen PaperWorkShell release through its public installer or package, hashes, human-readable release documents, synthetic samples, and documented black-box behavior. ThirdLife owns any later catalogue entry, profile, compatibility rule, workaround, or optional version-bounded adapter. Normal installation, launch, user-visible opening of a user-selected supported file, documented workspace behavior where independently supported, ordinary export and import, and human guidance precede a custom interface. A CLI, URI, RPC command, file association, machine-readable export, or manifest field requires independent PaperWorkShell user, testing, support, accessibility, or administration value and the normal local decision process; hypothetical sibling convenience is insufficient.

PaperWorkShell publishes a versioned human-readable `docs/release/RELEASE_INTERFACE.md` and a matching `docs/release/RELEASE_EVIDENCE.md` for preview and stable candidates. These documents describe an exact release and are not a shared runtime schema or an application API. PaperWorkShell owns its case migrations and rollback behavior. A future ThirdLife release may keep catalogue, installation, compatibility, and its own support metadata, but it does not read or index PaperWorkShell documents, OCR text, overlays, signatures, redaction state, private manifests, or case records. Removing or disabling a future adapter must leave the complete normal PaperWorkShell path available.

**Consequences:** The PaperWorkShell task graph remains project-local, and composite portfolio journeys or future adapter ideas are non-gating observations rather than dependencies. PaperWorkShell versions and releases independently and does not wait for ThirdLife adoption. No ThirdLife compatibility claim exists until a future B4 compatibility cut tests an exact frozen release. Release documents keep unknown facts as `TBD` or state “not supported” with a reason until real packaged-build evidence exists. A change to the PaperWorkShell **Owns** or **Does not own** boundary requires an explicit maintainer-approved portfolio-alignment decision rather than an incidental task edit. Existing decisions PWS-D001–PWS-D041 remain in force; this decision specializes PWS-D003, PWS-D005, PWS-D008, PWS-D010, PWS-D027, PWS-D028, PWS-D033, PWS-D036, PWS-D039, and PWS-D040 for the late-binding portfolio boundary. The accepted rationale and source mapping are recorded in `docs/adr/0008-thirdlife-late-binding-portfolio.md`.

---

## PWS-D043 — Windows 11 x64-only product and release scope

**Status:** FROZEN; explicitly approved by the maintainer on 2026-11-04. Replaces `PWS-D004` for current platform scope.

**Decision:** PaperWorkShell's application, packaging, update and required project-qualification target is **Windows 11 on x86-64 (x64)**. Native ARM64 and x64 emulation on ARM64 are outside project scope. An ARM64 build, test environment, qualification result or future ARM task is not a prerequisite for `PWS-0802`, `PWS-0803`, an M8 gate or public release. Other operating systems are outside the current supported application target; portable internal boundaries create no additional platform commitment.

**Consequences:** Exact supported editions and builds within Windows 11 x64 still require release-grade evidence. Scope selection does not grant a test pass, trusted signature, public-release approval or universal accessibility claim. Earlier ARM plans, unrun cells and immutable R7 artifacts remain historical records, not completed or deferred requirements. There is no new mandatory ARM task. Accepted exported-document reader observations remain evidence about those files, not another supported PaperWorkShell application platform.

Security, privacy, accessibility, licensing, bounded-resource behavior under `PWS-D041`, case ownership and independent standalone operation under `PWS-D042` remain unchanged. No case, template or RPC schema migration is introduced. The rationale, supersession, affected surfaces and rollback procedure are recorded in [ADR 0019](docs/adr/0019-windows-11-x64-only-scope.md).

---

## Deliberately variable implementation parameters

The following are implementation parameters rather than permission to change product boundaries:

- Exact supported CPython minor and package-management workflow, selected and pinned in `PWS-0001`
- Concrete PDF renderer, selected by `PWS-0106`
- Exact Windows isolation primitives available at each release stage
- Specific font family and embedding method for overlays, proven in `PWS-0303` and `PWS-0307`
- Numeric resource limits and quality thresholds, measured against approved synthetic workloads and versioned resource policies
- Exact installer technology and authenticated update mechanism, selected in Milestone 8
- Exact reverse-domain application identity and optional file-association policy, selected and evidenced by `PWS-0802`

Each selection requires an ADR in `docs/adr/`, evidence, tests, and no conflict with the frozen decisions above.

---

## Change procedure

To replace or amend a frozen decision:

1. Create an ADR with status **Proposed** and identify the decision ID.
2. State the concrete evidence, affected users, alternatives, security/privacy/accessibility/licensing impact, and migration impact.
3. Show exact edits required in `DECISIONS.md`, `ROADMAP.md`, `TASKS.yaml`, schemas, and prompts.
4. Obtain explicit maintainer approval.
5. Mark the old decision **SUPERSEDED** rather than deleting it.
6. Add the replacement decision with a new ID or a clearly versioned amendment.
7. Update the task graph and tests in the same change.

Codex MUST NOT perform this procedure autonomously merely to simplify implementation.
