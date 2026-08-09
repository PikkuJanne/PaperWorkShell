# PaperWorkShell Binding Roadmap

| Field | Value |
|---|---|
| Roadmap version | 0.1.0 |
| Status | **Binding** |
| Product name | **PaperWorkShell** |
| Planning model | Dependency-based milestones with explicit exit gates |
| First pilot release | v0.1 after Milestone 4 |
| Hardened public release target | v1.0 after Milestone 8 |
| Last frozen | 2026-07-14 |

This roadmap is the main execution contract for PaperWorkShell. It converts the product concept into an implementation sequence that a coding agent can follow one task at a time. `TASKS.yaml` is the executable task graph; this document explains the intent, sequencing, gates, and non-negotiable outcomes behind that graph.

Normative terms are used deliberately:

- **MUST** means a release or security requirement.
- **MUST NOT** means prohibited behavior.
- **SHOULD** means the default unless a documented technical constraint requires an exception.
- **MAY** means optional behavior that does not alter frozen scope.

Changes to binding scope, milestone order, or a frozen decision require the change process in `DECISIONS.md`. Routine implementation detail may evolve inside those boundaries.

---

## 1. Product contract

### 1.1 Product outcome

PaperWorkShell is a free, local-first, Windows-first desktop application that guides a person from mixed raw documents to a complete, legible, privacy-safe, portal-ready submission package.

The product is successful when a first-time user can take PDFs and phone photographs, organize them, improve scans, add simple visual form marks, check submission constraints, and export one or more verified files without using a paid PDF suite or uploading sensitive content to an unrelated service.

PaperWorkShell is **not** a general-purpose PDF editor. The product optimizes for a task sequence:

**Add → Organize → Improve → Fill → Protect → Check → Export**

### 1.2 Primary users

The first releases optimize for:

1. **Independent users** preparing an application on their own, potentially on an older Windows computer.
2. **Assisted users** working with a librarian, volunteer, social worker, community organization, or family member.

Full multi-user caseworker administration, identities, role-based access control, and centralized retention are outside the first desktop release.

### 1.3 Pilot workflow

The initial workflow is a generic portal submission package containing:

- An application form
- Identity evidence
- Proof of address
- Supporting documents
- One combined PDF or a limited set of named PDFs
- Optional ZIP packaging
- A maximum total size and/or maximum size per file

The engine remains generic. Templates may describe specific workflows later, but templates MUST remain declarative, versioned, non-executable, and limited to packaging guidance rather than eligibility or legal decisions.

### 1.4 v0.1 definition

The v0.1 pilot consists of Milestones 0 through 4. It MUST provide:

- Local case creation, save, reopen, deletion, autosave, and crash recovery
- PDF, JPEG, and PNG intake
- Immutable source preservation with SHA-256 hashes
- Page thumbnails, ordering, rotation, removal, splitting, and combining
- Local scan cleanup and OCR
- Visual overlays for text, dates, marks, initials, and signature images
- A generic application template and checklist
- Preflight for missing items, basic legibility, compatibility, filenames, file counts, and size constraints
- Combined PDF, separate PDF, and ZIP export
- Export reopen, parse, page-count, rendering, and structural validation
- Sanitized diagnostics with no document content
- Complete keyboard alternatives for core operations

The v0.1 pilot MUST NOT advertise permanent redaction, digital signing, qualified electronic signatures, arbitrary AcroForm support, XFA support, cloud storage, portal submission, eligibility evaluation, or legal advice.

### 1.5 v1.0 definition

A hardened v1.0 requires completion of all milestones, including the redaction security gate, accessibility and assisted-mode gate, signed packaging, dependency inventory, migration tests, and release-candidate validation.

---

## 2. Source-of-truth order

For repository work, use this precedence:

1. The maintainer's current explicit instruction
2. `DECISIONS.md` frozen decisions
3. This `ROADMAP.md`
4. The selected task in `TASKS.yaml`
5. `AGENTS.md` operating rules
6. Tests and public interfaces already accepted into the repository
7. Other documentation and comments

A lower item MUST NOT silently override a higher item. A conflict is resolved by updating the higher-level artifact through the documented change process, not by coding around it.

---

## 3. Frozen scope boundaries

| Area | First supported boundary | Deferred or excluded boundary |
|---|---|---|
| Platform | Windows-first desktop | macOS/Linux packaging after Windows workflow stabilizes |
| Connectivity | Fully usable offline | Cloud processing and account-backed synchronization |
| Inputs | PDF, JPEG, PNG | HEIC, TIFF, DOCX, ODT, email import, scanner protocols |
| Outputs | One PDF, several PDFs, ZIP, package summary | Direct portal submission |
| Editing | Non-destructive page operations and visual overlays | Paragraph editing of born-digital PDFs |
| Forms | Overlay-first; detect existing fields where useful | Dynamic XFA, arbitrary scripts, calculation-heavy forms |
| Signatures | Signature-image placement with explicit terminology; detect cryptographic signatures | Creating or validating qualified signatures in v0.1 |
| OCR | Local OCRmyPDF/Tesseract orchestration | Cloud OCR or AI-required recognition |
| PDF layer | pikepdf/qpdf behind adapters | UI code manipulating low-level PDF objects directly |
| Redaction | Designed and tested only in Milestone 6 | Shipping unverified visual covering as redaction |
| AI | Not required | Automatic decisions about whether a requirement is satisfied |
| Telemetry | Off by default; no content telemetry | Silent analytics or document upload |
| Legal/eligibility | Packaging guidance only | Legal advice, entitlement decisions, acceptance guarantees |

---

## 4. Architecture blueprint

### 4.1 Required process boundaries

PaperWorkShell uses at least two processes from the first vertical slice:

1. **Desktop/UI process**
   - PySide6 interface
   - Application orchestration
   - Pure domain models
   - Case navigation and user interaction
   - No direct parsing of untrusted PDF or full-resolution image content

2. **Restricted worker process**
   - PDF parsing and rendering
   - Image decoding and transformation
   - OCR
   - Compression and optimization
   - Metadata inspection
   - Export assembly and verification
   - No network access
   - Restricted access to the active case directory
   - Structured RPC messages, never shell command strings
   - Per-operation timeout, memory, page-count, dimension, and temporary-storage limits

A broker or worker client in the UI process MUST translate typed application requests into versioned RPC commands. External document libraries MUST be hidden behind internal adapter interfaces so that rendering or OCR components can be replaced without rewriting domain and UI code.

### 4.2 Dependency direction

The intended dependency direction is:

```text
UI -> Application services -> Domain models
                         -> Worker client -> RPC schema
Worker host -> PDF/Image/OCR adapters -> External libraries
Storage adapters -> Case manifest and filesystem
Export verifier -> PDF/Image adapters
```

Rules:

- Domain code MUST NOT import PySide6, pikepdf, OCRmyPDF, Tesseract bindings, or platform packaging code.
- UI code MUST NOT open or parse source PDF object structures.
- Worker adapters MUST NOT depend on UI widgets.
- Template and manifest schemas MUST be versioned and independently testable.
- Long-running work MUST be cancellable and report structured progress.

### 4.3 Initial repository shape

The bootstrap tasks should converge on this shape while preserving reasonable tool-specific variation:

```text
PaperWorkShell/
├── AGENTS.md
├── CODEX_START_PROMPT.md
├── DECISIONS.md
├── README.md
├── ROADMAP.md
├── TASKS.yaml
├── pyproject.toml
├── src/paperworkshell/
│   ├── app/
│   ├── domain/
│   ├── worker/
│   ├── pdf/
│   ├── imaging/
│   ├── ocr/
│   ├── overlays/
│   ├── preflight/
│   ├── export/
│   ├── templates/
│   ├── storage/
│   └── diagnostics/
├── schemas/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   ├── accessibility/
│   ├── fixtures/synthetic/
│   └── golden/
├── docs/adr/
├── tools/
└── packaging/
```

### 4.4 Case workspace model

Every case uses random internal identifiers and a versioned manifest. A representative workspace is:

```text
<case-root>/<case-id>/
├── manifest.json
├── journal/
├── originals/
│   └── <random-document-id>.bin
├── cache/
│   ├── thumbnails/
│   └── previews/
├── temp/
└── export-history.json
```

Invariants:

- Imported originals are copied once, hashed, marked immutable by application policy, and never rewritten.
- Original display names exist only in the protected manifest; internal filenames are random.
- Page edits are manifest operations, not repeated in-place PDF rewrites.
- Exports always target a new path and never overwrite an imported original.
- Temporary data is scoped to the active case and removed as soon as practical.
- Crash recovery reconstructs application state from the last valid manifest plus journal records.

### 4.5 Manifest model

The manifest is the authoritative description of:

- Schema version and case identity
- Imported source records and hashes
- Source page references
- Page sequence
- Non-destructive transforms
- Visual overlays
- Checklist assignments
- User confirmations
- Output constraints
- Export history references

Manifest mutations MUST be atomic, validated against a JSON Schema, and journaled enough to recover from interrupted writes. Migrations MUST preserve source references and user intent.

### 4.6 Export pipeline

Every export follows a deterministic staged pipeline:

1. Validate manifest and template.
2. Resolve page sequence and output grouping.
3. Render or copy pages according to operation policy.
4. Apply overlays and required flattening.
5. Perform profile-specific cleanup or optimization.
6. Write to a new temporary export path.
7. Reopen the produced file.
8. Parse and structurally validate it.
9. Verify expected page count, size, encryption state, attachments policy, and output naming.
10. Render representative or all pages according to the verification profile.
11. Atomically move the verified output to the chosen destination.
12. Record the export result without storing sensitive content in logs.

A structural success does not permit the UI to claim substantive acceptance by a portal. Messages MUST state concrete checks and remaining review items.

---

## 5. Non-negotiable security and privacy invariants

These apply to every milestone:

1. Source documents and images are untrusted inputs.
2. The worker has no network access during document processing.
3. The GUI does not parse complex source content directly.
4. No document text, OCR output, form value, name, address, original filename, full path, thumbnail, or signature image appears in routine logs.
5. External processes are invoked with structured argument arrays, never interpolated shell commands.
6. Inputs are checked by content signature rather than file extension alone.
7. File size, page count, image dimensions, decompression, CPU time, memory, and temporary storage are bounded.
8. Imported originals are never modified or overwritten.
9. Digitally signed inputs are detected; any edited derivative is clearly unsigned.
10. A black rectangle or annotation is never represented as permanent redaction.
11. A failed redaction verification blocks redacted export.
12. Templates contain data only and cannot execute code.
13. Diagnostics are inspectable before the user exports them.
14. Tests and public fixtures use synthetic or appropriately anonymized documents, never real applicant records.
15. No background telemetry or content upload is enabled by default.

---

## 6. Release train and critical path

| Milestone | Release meaning | Primary outcome | Gate task |
|---|---|---|---|
| M0 — Product contract and foundation | Development may begin safely | Toolchain, schemas, architecture boundaries, threat model, corpus | `PWS-0010` |
| M1 — Reliable vertical slice | Architectural proof | Case → import PDF → organize → export → reopen/verify → recover | `PWS-0114` |
| M2 — Image intake and OCR | Mixed-document workflow | JPEG/PNG, cleanup, local OCR, quality warnings | `PWS-0209` |
| M3 — Filling and overlays | Basic form completion | Reliable visual overlays and flattened output | `PWS-0308` |
| M4 — Templates and preflight | **v0.1 pilot** | Guided generic packet, constraints, multi-output export, summary | `PWS-0409` |
| M5 — Compression and compatibility | Portal robustness | Target-size policy, sanitization inspection, viewer profile | `PWS-0507` |
| M6 — Permanent redaction | Security-gated capability | Reconstructed pages, adversarial verification, blocked failure | `PWS-0608` |
| M7 — Accessibility, localization, assisted mode | Inclusive pilot readiness | Keyboard, NVDA, scaling, consent, retention controls | `PWS-0707` |
| M8 — Release hardening | **v1.0 candidate** | Installer, updates, SBOM, migrations, documentation, RC matrix | `PWS-0808` |

The primary critical path is:

```text
PWS-0001 -> PWS-0002 -> PWS-0006/PWS-0008 -> PWS-0010
-> PWS-0107 -> PWS-0108 -> PWS-0111 -> PWS-0112 -> PWS-0114
-> PWS-0204 -> PWS-0206 -> PWS-0209
-> PWS-0301 -> PWS-0303 -> PWS-0307 -> PWS-0308
-> PWS-0401 -> PWS-0404 -> PWS-0407 -> PWS-0408 -> PWS-0409
```

Milestones after M4 may be developed on separate branches, but no gate may be declared complete while a required dependency is incomplete or a security invariant is waived.

---

## 7. Milestone plans

## M0 — Product contract and foundation

### Objective

Create a repository that encodes architectural and security constraints before document-processing feature work starts.

### Workstreams

- Toolchain and repository bootstrap
- Layered source layout
- Local quality gates
- Threat model and data-flow diagrams
- Dependency and licensing policy
- Case manifest JSON Schema v1
- Template schema v1
- Worker RPC schema v1
- Synthetic document corpus and fixture policy

### Exit criteria

- A supported CPython minor and dependency workflow are pinned and documented.
- The package imports and baseline tests run on Windows development environments.
- Static analysis, formatting, typing, unit tests, and secret scanning have local commands.
- Architecture boundaries are represented in source layout and import tests.
- Every initial external dependency has a license and distribution note.
- Manifest, template, and RPC schemas validate known-good and known-bad fixtures.
- The threat model covers malicious inputs, logs, temporary files, signatures, templates, export corruption, and dependency compromise.
- The public test corpus contains no real applicant data.
- `PWS-0010` records a passed gate review.

## M1 — Reliable end-to-end vertical slice

### Objective

Prove the non-destructive case model and worker boundary before adding OCR or form editing.

### Demonstration

A user creates a case, imports several PDFs, views thumbnails, reorders and rotates pages, removes a page, exports one combined PDF, reopens and validates it, then restarts the application and recovers the same case.

### Exit criteria

- Original files remain byte-for-byte unchanged and their hashes still match.
- Actual file type is checked before parsing.
- Malformed and resource-excessive inputs fail with stable error codes and do not corrupt the case.
- The UI remains responsive while the worker renders thumbnails and exports.
- Page ordering is deterministic from the same manifest.
- Undo and redo work for page-order operations.
- Autosave uses atomic manifest replacement.
- Killing the worker does not destroy or orphan the case.
- Every exported PDF is reopened, structurally checked, page-count checked, and rendered.
- Routine logs contain no prohibited content.
- The reference vertical-slice test passes on the low-spec target.

## M2 — Image intake and OCR

### Objective

Support realistic mixed input while preserving reviewability and bounded resource use.

### Demonstration

A case combines born-digital PDFs, image-only PDFs, JPEGs, and PNGs. The user crops, rotates, deskews, previews cleanup, selects OCR languages, cancels and restarts OCR safely, and receives quality warnings.

### Exit criteria

- JPEG and PNG are normalized into internal page representations without changing the originals.
- Cleanup transformations are non-destructive and show before/after previews.
- OCR runs in the worker and never blocks the UI event loop.
- OCR work can be cancelled without corrupting the manifest or cache.
- Missing language data produces a clear remediation message.
- Mixed born-digital and scanned pages retain appropriate text behavior.
- Large jobs process incrementally instead of loading all full-resolution pages into memory.
- Signed PDFs are detected before OCR or page modification.
- Low-resolution, likely blur, clipping, blank-page, orientation, duplicate, and OCR-failure warnings are presented as review signals, not certainty.

## M3 — Filling and overlays

### Objective

Provide dependable visual form completion without taking on arbitrary PDF form behavior.

### Demonstration

A user places text, date, checkmark, cross, initials, and a signature image on rotated and cropped pages using mouse or keyboard; the output is flattened and renders consistently in the supported reader matrix.

### Exit criteria

- The coordinate model correctly maps screen, normalized page, rendered image, and PDF point coordinates.
- Rotation, crop box, media box, and scaling cases are covered by automated tests.
- Overlay operations are part of manifest history and support undo/redo.
- Unicode text is rendered predictably with an approved font strategy.
- Overlay placement has keyboard alternatives and visible focus.
- Signature image terminology never implies cryptographic signing.
- Flattened output is verified in the defined viewer matrix.

## M4 — Templates and preflight

### Objective

Turn the component set into the complete guided PaperWorkShell workflow and produce the v0.1 pilot.

### Demonstration

A first-time user completes the generic application packet without another PDF tool: assigns documents to checklist categories, resolves warnings, meets filename and size rules, exports one or several PDFs plus a summary, and reopens the case later.

### Exit criteria

- Templates are schema-validated, declarative, versioned, and unable to execute code.
- The generic application template covers form, identity, address, supporting evidence, ordering, filenames, file counts, and total size.
- Suggested classification never marks a requirement complete without user confirmation.
- Preflight distinguishes errors, warnings, and confirmations.
- Missing categories, unassigned pages, empty documents, basic legibility issues, unsupported interactive content, attachments, encryption state, filename rules, file counts, page counts, and size rules are evaluated.
- Export planner produces one PDF, multiple named PDFs, or ZIP according to template and user constraints.
- A human-readable package summary states exactly what was checked and what still needs human review.
- The primary workflow is usable without drag-only actions.
- The v0.1 pilot corpus passes end-to-end tests on the reference machine.

## M5 — Compression and compatibility

### Objective

Meet common portal constraints without silently destroying legibility or retaining prohibited PDF features.

### Exit criteria

- Metadata, embedded files, annotations, actions, JavaScript, encryption, and interactive content can be inspected and reported.
- Stream and object optimization occurs before destructive image changes.
- Image-heavy pages are identified and handled incrementally.
- Target-size export uses staged re-encoding/downsampling and records the selected policy.
- The user receives a realistic preview of pages that will change.
- A quality floor prevents automatic over-compression.
- Unattainable targets produce an actionable message rather than an unreadable output.
- Conservative portal output is tested in the defined reader and upload matrix.

## M6 — Permanent redaction

### Objective

Ship permanent redaction only after a separate adversarial security program proves the supported implementation.

### Required implementation boundary

The first supported method reconstructs each affected page from a sanitized raster, burns redaction regions into that raster, discards original page objects for the derivative, rebuilds the page, and creates OCR only from non-redacted regions. Object-level redaction is outside this gate unless separately proven.

### Exit criteria

- The redaction corpus includes born-digital text, scanned text, images, annotations, form values, attachments, metadata, incremental revisions, and malformed PDFs.
- Redacted text cannot be selected, copied, searched, or extracted.
- Image extraction cannot reveal redacted source pixels.
- Content stream and incremental-revision inspection cannot recover original affected-page objects.
- Attachments, comments, form values, actions, and metadata do not contain the redacted value.
- OCR cannot recreate text beneath a redaction.
- Output renders consistently in multiple viewers and remains redacted after resave.
- A verification failure blocks export and emits a sanitized report.
- Signed sources produce only explicitly unsigned derivatives.
- An independent review approves `PWS-0608` before the feature is enabled in release builds.

## M7 — Accessibility, localization, and assisted mode

### Objective

Make the complete primary workflow operable by keyboard and screen reader, and safe for shared-computer assistance.

### Exit criteria

- The primary workflow can be completed without a mouse.
- NVDA announces document status, progress, warnings, and errors meaningfully.
- No meaning depends on color or drag-and-drop alone.
- UI scaling at 200% does not hide required controls or truncate critical content.
- High contrast and reduced motion are respected.
- Localization does not require application-code changes for text updates.
- Layout is prepared for right-to-left languages.
- Assisted mode explains consent and retention before import.
- Automatic expiry and delete-after-export behavior are testable and understandable.
- Generated package summaries are accessible; a plain-text OCR companion is available where appropriate.

## M8 — Release hardening

### Objective

Produce a supportable, signed, updateable, and migratable Windows release.

### Exit criteria

- Runtime and build dependencies are pinned; an SBOM and license inventory are generated.
- Installer and application binaries are signed under the approved release process.
- Update metadata and packages are authenticated; document-processing components cannot be silently replaced from an untrusted source.
- Supported manifest migrations are reversible or safely fail before mutating a case.
- User-authorized diagnostic export shows its exact contents before creation.
- Installation requires no development tools.
- Uninstall behavior explains and preserves or removes remaining cases according to an explicit user choice.
- Security reporting, vulnerability handling, user documentation, and support documentation are published.
- The release-candidate matrix passes supported readers, reference hardware, low disk, interruption, migration, accessibility, and security tests.

---

## 8. Global definition of done

A task is complete only when all applicable items are true:

1. Its dependencies are complete.
2. Acceptance criteria in `TASKS.yaml` are demonstrably satisfied.
3. Code follows the architecture and security invariants.
4. New public behavior has tests at the lowest useful level plus integration coverage where boundaries are crossed.
5. Error paths are tested, not only the happy path.
6. No real personal document enters the repository, logs, screenshots, or fixtures.
7. Static analysis, formatting, type checks, unit tests, and relevant integration/security tests pass.
8. Documentation, schemas, and task state are updated in the same change.
9. Generated or cached artifacts are not committed unless the repository policy explicitly requires them.
10. The change is reviewable as one coherent task and does not mix unrelated refactoring.
11. User-facing errors are plain-language while retaining stable internal error codes.
12. A completion report identifies changed files, commands run, results, residual risks, and the next unblocked task.

A milestone gate additionally requires every milestone task to be complete or explicitly waived through the frozen-decision change process, with a recorded gate report and no unresolved release-blocking defect.

---

## 9. Test strategy

### 9.1 Test layers

| Layer | Purpose |
|---|---|
| Unit | Manifest operations, coordinates, validation rules, naming, page sequence, pure domain behavior |
| Contract | JSON Schema, RPC compatibility, adapter behavior, stable error codes |
| Integration | Worker lifecycle, parsing, rendering, OCR, export, reopen, cancellation, crash recovery |
| Property-based | Page order invariants, operation replay, filenames, normalized coordinates, migration round trips |
| Golden rendering | Known visual output for rotation, crop, overlays, OCR, and reconstruction |
| Security | Path traversal, command injection, parser failures, resource limits, network egress, log content, redaction extraction |
| Accessibility | Keyboard traversal, focus order, labels, announcements, scaling, no color-only state |
| Compatibility | Adobe Reader, Edge, Chrome, Firefox, representative Android reader, later macOS Preview |
| Performance | Two cores, 4 GB RAM, integrated graphics, limited disk, HDD scenario |

### 9.2 Required synthetic corpus classes

The corpus MUST include born-digital, image-only, mixed, rotated, differently sized, cropped, transparent, CMYK, low-resolution, very large, blank, duplicate, protected, signed, attached, annotated, scripted, malformed, incrementally saved, Unicode-named, long-path, right-to-left, multilingual, and handwriting-demonstration cases.

### 9.3 Failure simulations

At minimum, automate:

- Worker crash and forced termination
- UI restart during an autosave boundary
- Disk full during import, cache, and export
- Timeout and memory limit
- Missing OCR language data
- Unsupported or encrypted input
- Malformed cross-reference data
- Export destination permission failure
- Corrupted cache that can be regenerated
- Network-egress attempt from worker
- Diagnostic export inspection

---

## 10. Initial execution order

Codex should normally begin with `PWS-0001` and follow dependency readiness. A practical first sequence is:

1. `PWS-0001` — Bootstrap repository and pin the development toolchain.
2. `PWS-0002` — Establish source layout and dependency boundaries.
3. `PWS-0003` — Add local quality gates and CI-equivalent commands.
4. `PWS-0004` — Commit the threat model and data-flow model.
5. `PWS-0005` — Establish dependency and licensing policy.
6. `PWS-0006` — Define case manifest JSON Schema v1.
7. `PWS-0007` — Define template schema v1.
8. `PWS-0008` — Define worker RPC schema v1.
9. `PWS-0009` — Add the synthetic fixture harness.
10. `PWS-0010` — Run and record the M0 gate.
11. `PWS-0101` — Implement secure case workspace conventions.
12. `PWS-0102` — Implement case lifecycle service.

Parallel work is permitted only when tasks have no incomplete dependencies and do not edit the same architectural surface. The default Codex behavior is one task per change.

---

## 11. Risk register

| Risk | Impact | Required response |
|---|---|---|
| PDF complexity exceeds supported profile | Corruption, inconsistent output | Constrain profiles, preserve originals, adapter boundaries, explicit unsupported messages |
| Parser exploit | Host compromise | Worker isolation, no network, resource limits, maintained dependencies, hostile corpus |
| Redaction failure | Sensitive-data exposure | Separate milestone, page reconstruction, adversarial verification, export blocking |
| Signature confusion | Legal/user misunderstanding | Exact terminology, signature detection, unsigned derivative warnings |
| OCR error in critical values | Incorrect submission | Review warnings, no certainty claims, human confirmation for names/dates/reference numbers |
| Compression destroys readability | Rejected or unusable packet | Staged policy, preview, quality floor, fail honestly when target is unsafe |
| Accessibility added too late | Expensive rewrite | Keyboard and screen-reader requirements in every relevant task and gate |
| Packaging size and dependency friction | Poor adoption | Modular language packs, deliberate renderer choice, release packaging tests |
| Portal templates become stale | Rejected uploads | Version, verification date, expiry, human-readable rules, no executable updates |
| Logs leak content | Privacy breach | Stable IDs, prohibited-field tests, user-previewed diagnostics |
| Scope expansion | Delayed core workflow | Enforce frozen non-goals and milestone gates |

---

## 12. Change control

A proposed change to this roadmap or `DECISIONS.md` must include:

1. The decision or roadmap section affected.
2. The user problem or technical evidence requiring the change.
3. Security, privacy, accessibility, licensing, migration, and schedule consequences.
4. Alternatives considered.
5. Exact task-graph changes, including dependencies and gates.
6. Migration or compatibility treatment for existing cases.
7. Maintainer approval.

After approval, update `DECISIONS.md`, this roadmap, `TASKS.yaml`, related schemas, and prompts in the same change. Never let code become the only record of a changed product boundary.
