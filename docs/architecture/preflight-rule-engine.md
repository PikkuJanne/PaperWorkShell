# Typed preflight rule engine

- Status: PWS-0404 engine plus PWS-0405/PWS-0406 policy contracts
- Tasks: PWS-0404, PWS-0405, PWS-0406
- Decisions: PWS-D025, PWS-D026, PWS-D037
- Checklist input: [Checklist assignment architecture](checklist-assignment.md)
- Structural input: [PDF structural inspection](pdf-structural-inspection.md)
- Quality input: [Advisory page-quality review](quality-review.md)
- Portal constraints: [Portal constraints and safe output names](portal-constraints.md)

## Scope and claim boundary

`paperworkshell.preflight` is a pure, read-only decision-support layer. It converts exact typed
facts into blocking errors, required user confirmations, and warnings. A report describes only the
registered checks that ran. It never claims that a packet is valid, complete in a legal sense,
safe, accepted by a portal, or ready for every destination.

Every report carries the authority SHA-256, engine SHA-256, and exact sorted rule inventory in
addition to its findings. Those fields permit correlation, but mutation/export authorization still
consumes the enclosing `PreflightEvaluation` and exact expected authority rather than trusting a
detached report merely because its finding tuple is empty.

PWS-0404 provides the rule contract, deterministic engine, canonical fact boundaries, and test
harness. PWS-0405 supplies the registered completeness, compatibility, privacy-boundary, and
legibility policies. PWS-0406 supplies generic constraint models, output observation checks, safe
filename resolution, and the manual editor contract. PWS-0407 remains responsible for constructing
the exact multi-artifact plan and binding its final filename/encryption observations to engine
authority.

Templates remain declarative data. A validated template may supply inert warning or confirmation
text, identifiers, scopes, and constraints. It cannot supply a callable, expression, import,
command, callback, script, or rule implementation. Only registered PaperWorkShell code implements
`PreflightRule`.

## Authority and canonical inputs

Every `PreflightInput` is bound to one `PreflightAuthority` containing:

- the internal case UUID;
- exact saved manifest revision and SHA-256;
- the exact selected `TemplateBinding`, or an explicit absence;
- a complete ordered scope catalog for the current case, requirement, source, logical assignment,
  page, and provisional output targets.

Callers do not supply cache digests. The input computes its semantic SHA-256 from canonical ASCII
JSON containing the authority, dependency kind, concrete frozen dataclass type, and every declared
dataclass field. Mutable collections, arbitrary objects, non-UTC datetimes, invalid Unicode,
unsupported enum/value types, excessive nesting, and excessive node counts fail before a rule can
run. Hashing every field is important: an underscore-prefixed field remains rule-observable and
therefore cannot be omitted from incremental identity.

The canonical production projections are:

| Dependency | Path-free facts |
|---|---|
| Case | Template binding, ordered source ID/media/digest bindings, active page order, and explicit current/unavailable page geometry plus bounded overlay-intent counts |
| Template | Explicit current/not-selected/unavailable state plus exact binding and inert notices |
| Checklist | Explicit current/unavailable state plus pure definition and saved projection |
| Source inspections | Every ordered source with exact digest/media binding and either the full typed `PdfInspectionResult`, explicit unavailable state, or image not-applicable state |
| Quality findings | Explicit current/unavailable state, exact page order/profile, and typed advisory findings |
| Output plan | Explicit current/unavailable state, deterministic opaque output keys, page membership, bounded numeric estimates, and exact current/unavailable overlay-flattening page coverage |

These projections exclude display names, original filenames, storage names, paths, proposed output
filenames, OCR/document text, metadata values, thumbnails, signature images, and raw exceptions.
Engine prevalidation correlates any canonical facts present: template bindings must agree; source
IDs, media types, and digests must match; checklist and quality page orders must match the case;
requirement/assignment/output scopes must match the catalog; and every planned output page must be
active. Each canonical DTO is accepted only in its exact dependency slot, and any canonical
non-case fact requires the authoritative canonical case projection. Missing or unavailable
analysis is explicit and is never converted to a clean result.

`PdfInspectionResult` is intentionally retained in the ephemeral source-inspection projection
because the current manifest/import summary does not retain attachment, action, script, XFA,
interactive-content, and structural states. The PWS-0405 compatibility rule consumes the full
result and reports unavailable inspection explicitly. Its application adapter must acquire a
current result through bounded worker reinspection or a separately approved retained-result
design; it must not infer absence from the lossy manifest summary.

## Core policy registry

`core_preflight_rules()` returns five deterministic policies:

1. **Completeness** blocks missing confirmed minimums, confirmed maximum violations, unassigned
   pages, empty logical documents, unavailable checklist/template/output-plan facts, and no selected
   template. Suggested assignments and checklist states needing review remain explicit confirmation
   requests.
2. **Template notices** gives every non-informational declaration a bounded position-specific
   finding identity. Multiple declarations of the same kind at the same scope therefore cannot
   collapse into one result. Templates still provide inert data only.
3. **Compatibility** blocks encrypted/malformed/structurally failed PDFs and detected unsupported
   XFA, scripts, actions, or interactive content. Attachments, structural warnings, metadata limits,
   signature markers, and unavailable/unknown inspection remain visible warnings. A signature
   marker always states that a modified or exported copy is an unsigned derivative.
4. **Quality** projects every PWS-0206 advisory signal—including OCR failure/low confidence, blur,
   likely blank, clipping, orientation, duplicate, and low resolution—as a review warning. It never
   strengthens a heuristic into a completeness decision.
5. **Privacy boundary and geometry** warns above the conservative 14,400-point page-dimension
   review boundary, blocks overlays explicitly projected as unapplied or absent from the exact
   output flattening plan, and warns that ordinary shapes do not prove permanent removal. Missing
   page-detail or output-flattening authority remains an explicit unverified result. Depending on
   both canonical case and output-plan inputs makes any plan change invalidate this rule.

The page-size boundary is a versioned conservative compatibility review threshold, not a portal
limit or support guarantee. Metadata removal and final overlay flattening remain export-time checks;
PWS-0405 reports them as unverified when no exact evidence exists. Permanent redaction is not
implemented or claimed.

## Rule and finding contract

A rule descriptor contains a stable rule ID and version, its exact dependency set, and its static
finding definitions. Each definition fixes a stable code, one of the three severities, bounded
plain-language message and remediation text, a closed remediation action, and—only for a required
confirmation—a typed review-request code. Dynamic rule output contains only a definition code,
catalogued primary/optional related scope, and bounded numeric evidence.

The related scope permits a suspected-duplicate warning to navigate to both pages without placing
page text or fingerprints in the finding. Output scopes use deterministic opaque plan keys, never
filenames or paths. Assignment scopes are the existing logical-document identity; preflight does
not invent a second document model.

Canonical order is independent of registration and emission order:

1. blocking errors;
2. required confirmations;
3. warnings;
4. scope kind and current user-visible position;
5. primary/related opaque identity, finding code, and rule ID.

Exact duplicate targets collapse. A duplicate identity with conflicting payload fails closed.
Unknown codes, stale scopes or positions, malformed/subclass results, excessive results, and
undeclared dependency access also fail closed. Results are never truncated because truncation
could hide a blocking condition.

## Incremental evaluation and resource bounds

The engine owns no global, persistent, cross-case, or multi-revision cache. The caller may provide
one prior immutable `PreflightEvaluation`. For the exact same authority, a rule result is reused
only when both its descriptor SHA-256 and every declared dependency SHA-256 are unchanged. A changed
dependency reruns only rules that declared it. A changed saved authority forces a full evaluation;
a cross-case prior evaluation is rejected. Cold and incremental evaluation for identical inputs
must produce equivalent finding sets.

This deliberately coarse invalidation is cheap enough for pure rules and easier to audit than a
page/source rule graph. Finer cross-revision reuse would require new reconciliation rules for every
scope and is deferred until measured need demonstrates value.

Bounds cover the released schema maxima: 4,096 sources, assignments, pages, and output targets;
128 template requirements; 16,513 total scopes; up to eight quality findings for each of 4,096
pages; 65,536 findings from one rule; and 131,072 findings overall. One output-mode plan may
partition at most 4,096 active page references across its outputs; repeating a page across output
items is rejected, which also keeps every accepted plan below the canonical hashing budget.
Exceeding a bound is a typed failure, never silent omission. No `ResourcePolicy` profile changes,
executors, queues, subprocesses,
files, or caches are introduced by PWS-0404.

## Security, privacy, and accessibility projection

The pure package performs no logging, printing, filesystem access, process launch, networking,
dynamic import, or code evaluation. Unexpected rule failures—including corrupted or subclassed
`PreflightError` values—are rebuilt as closed errors with sanitized arguments and no exception
cause/context. Safe representations omit messages, remediation text, hashes, private fact payloads,
and template notice text. Routine diagnostics may later record only separately reviewed aggregate
counts and closed codes; PWS-0404 adds no diagnostic fields.

The later UI must present severity in text, expose navigable primary/related scopes, use the closed
remediation action, keep template text plain, preserve keyboard access and focus, and announce
progress/errors/completion. Selecting a row or running a rule never records a confirmation or
changes checklist state.

## Confirmation lifetime

A `PreflightConfirmationRequirement` is only a request to record that the user reviewed a
statement. It is not proof that the statement is true and is not a persistence receipt. Any future
satisfaction check must consume the complete evaluation authority, rule descriptor identity,
finding code, exact scope, and template binding; a detached report or confirmation code is
insufficient.

Template schema v1 has no independent statement version, while manifest confirmation version and
statement version have different meanings. PWS-0404 therefore does not persist or automatically
satisfy general template confirmations. Durable cross-session confirmation would require an
explicit schema/version/migration and invalidation decision. Existing assignment-review ownership
remains in the checklist workflow.

## Standalone and release-interface effects

The engine consumes only PaperWorkShell-owned local domain values. It creates no network, sibling,
ThirdLife, shared SDK, shared schema, service, database, plugin, CLI, RPC, file association, or
release-coupling dependency. No case/template/worker schema or migration changes are required, and
`docs/release/RELEASE_INTERFACE.md` is unaffected. Removing any future portfolio adapter leaves the
normal PaperWorkShell preflight path complete.
