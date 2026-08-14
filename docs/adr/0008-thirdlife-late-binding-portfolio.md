# ADR 0008: Keep PaperWorkShell standalone and bind ThirdLife assembly to frozen releases

- Status: Accepted
- Proposed: 2026-08-14
- Accepted: 2026-08-14
- Task: PWS-0809
- Decisions affected: PWS-D042 (new binding decision); PWS-D001–PWS-D041 preserved
- Portfolio source: *ThirdLife Software Portfolio — Two-Team Development Roadmap and Boundary Architecture*, version 2.0, 2026-08-14, as mapped by the supplied `docs/portfolio/SOURCE_TRACEABILITY.md`

## Context

The maintainer directed PaperWorkShell to become part of the broader ThirdLife project and supplied a versioned steering bundle. That bundle defines a late-binding portfolio model: PaperWorkShell remains a complete standalone Team A / A1 product, while a future Team B / B4 deployment-and-assembly project may consume an exact frozen release through public artifacts and documented black-box behavior.

The current PaperWorkShell decisions already establish a guided local product, ordinary PDF and ZIP outputs, immutable originals, local case ownership, offline operation, bounded processing, sanitized diagnostics, independent case migrations, and gated releases. No existing frozen decision requires replacement. The local register did not, however, state who owns later portfolio adapters, forbid live cross-project task and runtime dependencies, or assign the minimal release-interface and evidence envelope needed for a future black-box compatibility cut.

The supplied alignment prompt explicitly authorizes this narrow governance change. It does not authorize a ThirdLife runtime integration, a shared platform, a portfolio-only command surface, a change to document-processing scope, or guessed release facts. The ADR was therefore proposed and accepted in the same maintainer-authorized change, following the precedent used for an already approved narrow decision amendment.

## Decision

Add PWS-D042 as one composite portfolio-boundary decision instead of duplicating or semantically rewriting existing decisions.

PaperWorkShell remains independently buildable, testable, installable, usable, updateable, recoverable, migratable, supportable, uninstallable, and releasable without ThirdLife or a sibling application. PaperWorkShell development receives no sibling source, database, service, active branch, private fixture, or release-gate dependency. It creates no shared user-content store, SDK, runtime schema, job engine, plugin framework, background service, or synchronized portfolio release.

A future ThirdLife B4 compatibility cut may use an exact frozen installer or package, hashes, public documentation, synthetic sample artifacts, and documented black-box actions. ThirdLife owns later catalogue entries, profiles, compatibility data, workarounds, and optional version-bounded adapters. The default contact surface is ordinary product behavior: install, launch, user-visible opening of an explicitly selected supported file, documented workspace behavior when independently supported, ordinary export and import, and human guidance. Every potential shallow action retains a complete manual fallback.

PaperWorkShell will publish a versioned human-readable release interface and a matching evidence index for exact preview and stable candidates. These are release documents, not a runtime API or shared schema. Unknown application identity, publisher, licence, installer, association, command-line, project-export, path, support-contact, and maintenance facts remain unresolved until their existing release tasks select and prove them. Unsupported behavior is stated explicitly rather than implemented speculatively.

Only PaperWorkShell migrates PaperWorkShell cases. A future ThirdLife component may retain its own catalogue, installation, compatibility, and support metadata, but it may not read or index PaperWorkShell documents, OCR text, overlays, signatures, redaction state, private manifests, or case records. PaperWorkShell releases and versions independently and does not wait for ThirdLife adoption.

The decision is implemented in governance under PWS-0809. Existing M8 tasks are extended rather than duplicated: they select and prove stable identity; package and remove the application; authenticate independent updates; own migrations; create separate sanitized diagnostics; publish release documentation; execute the public black-box candidate matrix; and freeze the complete standalone release envelope at the existing PWS-0808 gate.

## Existing decision mapping

| Portfolio requirement | Existing local foundation | Added specialization |
|---|---|---|
| Standalone independence | PWS-D002, PWS-D005, PWS-D031, PWS-D033 | No ThirdLife or sibling build, runtime, test, install, or release prerequisite |
| Project-local gates | PWS-D003 | No cross-repository dependency or permanent integration lane |
| Ordinary interfaces first | PWS-D008, PWS-D033, PWS-D037 | Standard install, launch, selected-file opening, exports, and manual fallback precede adapters |
| Private data ownership | PWS-D009, PWS-D010, PWS-D027, PWS-D028 | ThirdLife does not inspect PaperWorkShell content or private case records |
| Independent migrations | PWS-D036 | Only PaperWorkShell migrates or rolls back PaperWorkShell cases |
| Sanitized support evidence | PWS-D027, PWS-D039 | Product and future ThirdLife support artifacts remain separate |
| Independent release | PWS-D034, PWS-D040 | Frozen public artifacts enter B4 later; PaperWorkShell does not wait for adoption |

## Alternatives considered

### Add a live PaperWorkShell-to-ThirdLife integration

Rejected. It would make the standalone product depend on an unfinished assembly layer, enlarge the privacy and security boundary, and couple release schedules and migrations.

### Create a shared SDK, schema, workspace, or handoff package now

Rejected. Ordinary input and output files plus documented black-box behavior already provide the correct late-binding surface. A shared platform would create permanent maintenance and data-ownership obligations without demonstrated PaperWorkShell value.

### Add a ThirdLife-specific CLI, URI, RPC, or hidden automation path

Rejected. Future suite convenience alone does not justify a new trusted command surface. Any later product-local interface requires independently useful PaperWorkShell value and its own approved decision.

### Defer all release-envelope work until B4

Rejected. Stable identity, predictable install and removal, data-location documentation, synthetic samples, known limitations, and sanitized support evidence are standalone release-quality obligations. B4 should consume them, not define them retroactively.

### Split the boundary into many new frozen decisions

Rejected. The existing register already covers most security, privacy, data, output, migration, and release behavior. One focused decision records the portfolio ownership and late-binding rule without duplicating those contracts.

## Impacts

### Security and privacy

The decision narrows rather than expands trust boundaries. No new runtime, network, process, parser, data store, credential, telemetry, or content-sharing path is authorized. Public evidence must exclude private usernames, full local paths, credentials, document or OCR text, form values, metadata, thumbnails, signatures, and real personal data. ThirdLife never receives authority to inspect or migrate cases.

### Accessibility and low-resource behavior

All existing keyboard, NVDA, scaling, cancellation, progress, and resource-budget gates remain mandatory. A future adapter cannot replace the normal accessible workflow or become necessary for product use. No physical-hardware claim is added.

### Packaging, licensing, and maintenance

PWS-0801 and PWS-0802 still select and prove exact release dependencies, application identity, installer technology, signatures, notices, and packaged contents. Versions and update cadence remain PaperWorkShell-owned. This ADR adds no dependency and does not choose unresolved publisher, licence, installer, signing, association, or support values.

### Migration and compatibility

PaperWorkShell remains the only authority for its cases and migrations. Passing a user-selected path later does not authorize an external product to interpret or repair private state. A newer PaperWorkShell version remains unverified for ThirdLife until a future B4 cut tests that exact release or supported range.

### Task graph and release gates

PWS-0809 records the governance alignment independently of future packaged-release evidence. It depends only on the completed local foundation gate PWS-0010 and remains in progress until every supplied document, canonical prompt, validation guard, and bundle record is integrated and verified. PWS-0808 gains PWS-0809 as a direct dependency, as required for complete M8 gate coverage. Existing M8 tasks gain precise release-envelope responsibilities. No task depends on ThirdLife, a sibling repository, an adapter, or an external compatibility result.

## Affected surfaces

- Canonical governance: `DECISIONS.md`, `ROADMAP.md`, `TASKS.yaml`, `AGENTS.md`, and `CODEX_START_PROMPT.md`
- Portfolio and release documentation: `THIRDLIFE_ALIGNMENT.md`; `docs/release/README.md`, `docs/release/RELEASE_INTERFACE.md`, `docs/release/RELEASE_EVIDENCE.md`, and `docs/release/STANDALONE_RELEASE_CHECKLIST.md`; and `docs/portfolio/THIRDLIFE_FUTURE_NOTES.md` and `docs/portfolio/SOURCE_TRACEABILITY.md`
- Developer and product orientation: `README.md`
- Bundle metadata and validation evidence: `BUNDLE_MANIFEST.json` and repository documentation/task validators

No production code, manifest schema, RPC schema, installer, CLI, URI handler, file association, case migration, or ThirdLife adapter is introduced by this governance task.

## Rollback

Removing the standalone and late-binding boundary would require another explicit frozen-decision change with portfolio, security, privacy, accessibility, packaging, migration, and maintenance analysis. Deleting the release documents or quietly adding a cross-project dependency is not a valid rollback.
