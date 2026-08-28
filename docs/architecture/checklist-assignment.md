# Checklist categories and user-confirmed assignment

- Status: Implemented PWS-0403 contract
- Task: PWS-0403
- Decisions: PWS-D002, PWS-D026, PWS-D030
- Template input: [`generic-portal-application` 1.1.0](../templates/generic-portal-application-v1.md)
- Related contracts: [safe template loading](template-loading.md),
  [template schema v1](../schemas/template-v1.md), and
  [case manifest schema v2](../schemas/case-manifest-v2.md)

## Outcome and authority

PWS-0403 connects inert template requirement definitions to the assignment and confirmation records
already released in the case manifest. A requirement defines a stable category ID, reviewed title
and guidance, count unit, and inclusive count range. A case assignment refers to that category ID
and groups one or more ordered case pages as one logical document. The template never contains an
assignment, classifier, formula, predicate, or completion action.

The application remains useful without automation: the user can add pages as a document, move the
document between categories, change document and page order, remove it, and explicitly confirm it.
Drag-and-drop is only a UUID-only shortcut for the same provisional Add operation exposed through
named keyboard controls. Neither action confirms the document.

Authority is deliberately split:

| Layer | Owns | Must not own |
|---|---|---|
| `templates` | Exact-byte loading, schema and semantic validation, immutable `TemplateSnapshot` | Case assignment or completion |
| `domain.checklist` | Pure definition, projection, commands, confirmation coupling, undo/redo, candidate construction | Template I/O, filesystem paths, UI, persistence |
| `app.checklist` | Exact template-to-domain conversion, safe editor DTOs, suggestion boundary, opening authority | Source names/content, template execution, storage writes |
| `ui.checklist` | Plain-text status presentation, keyboard controls, optional internal drag shortcut, focus and announcements | Manifest snapshots, candidates, automatic confirmation |
| Organizer/autosave | Exclusive Begin/Apply phase and durable compare-and-swap save | Reinterpreting checklist state |

## Exact template connection

`ChecklistWorkflowService` is configured with one `SafeTemplateLoader`, opaque `TemplateKey`, and
explicit lifecycle evaluation date. `begin(snapshot)` loads that exact key and converts the
resulting `TemplateSnapshot` into a pure `ChecklistDefinition`:

1. the domain `TemplateBinding` receives the snapshot's exact template ID, content version, and
   observed SHA-256 digest;
2. `requirement_order` supplies the complete category order;
3. every referenced requirement contributes its exact ID, `documents` or `pages` count unit, and
   inclusive minimum/maximum; and
4. only the reviewed title and procedural guidance cross into the presentation rows.

The conversion does not read output limits, filenames, warnings, provenance claims, confirmation
statements, paths, or URLs. It does not evaluate text or invent defaults.

An empty unbound case may have `template_ref: null`. Opening the editor alone does not bind it. The
first Add or automation Suggest command records the exact loaded binding with the new provisional
assignment. Undoing that complete first mutation restores the unbound baseline. A case containing
assignments without a template reference is rejected, as is any populated or already-bound case
whose template ID, content version, or digest differs from the loaded definition. Reopening thus
resolves category IDs only through the exact artifact the case recorded, never through a path or a
same-named current template.

## Assignment and confirmation lifecycle

One assignment represents one logical document and contains an ordered, non-empty tuple of case
page UUIDs. New commands cannot place an unknown, inactive, or already-assigned page. Loaded
inactive or overlapping legacy state remains visible as Needs review rather than being silently
rewritten.

The state transitions are explicit:

| Action | Origin | Review state | Confirmation effect |
|---|---|---|---|
| Add selected pages | `user` | `suggested` | None; category count is not satisfied |
| Provider suggestion | `automation` | `suggested` | None; category remains Needs review |
| Confirm / Accept suggestion | Preserved | `user_confirmed` | Creates one user, assignment-scoped `assignment-reviewed` record and couples its UUID to the assignment |
| Move category | Becomes `user` | `suggested` | Removes the old scoped confirmation; review is required again |
| Replace document pages | Becomes `user` | `suggested` | Removes the old scoped confirmation; review is required again |
| Reorder pages within the same document | Preserved | Preserved | Keeps the confirmation because membership is unchanged |
| Remove assignment | Removed | Removed | Removes its assignment-scoped confirmation |

Confirmation time cannot predate assignment time. The application derives event times from an
injected UTC clock. One thread-safe sequencer is shared with manifest autosave and advances by one
microsecond when needed to remain monotonic relative to the opening manifest, earlier local events,
and the next saved `updated_at`, including after a wall-clock rollback. UUID allocation is injected,
bounded, v4-only, and checked against the complete manifest namespace by the domain.

## Deterministic textual statuses

Only active, explicitly user-confirmed assignment state contributes to a requirement count.
`documents` counts logical assignments, so a two-sided document is still one document. `pages`
counts distinct active confirmed pages.

The four statuses are closed text states, not colors:

- **Needs review** when a selected assignment contains an inactive or overlapping page, an
  automation suggestion remains pending, or the confirmed count exceeds the maximum.
- **Missing** when a required category has no selected assignment and no confirmed count.
- **Added** when user-added content still needs confirmation or confirmed content remains below the
  minimum.
- **Ready** when the inclusive confirmed count is satisfied and no review condition remains. An
  optional category with minimum zero is Ready while empty.

These statuses describe checklist packaging state only. Ready does not establish eligibility,
document correctness, identity, legal sufficiency, OCR certainty, portal acceptance, or successful
export.

## Bounded suggestion hook

The optional `ChecklistSuggestionProvider` receives only a `ChecklistSuggestionContext` containing
the internal case UUID, manifest revision, ordered requirement IDs, active page UUIDs, and currently
unassigned page UUIDs. It receives no manifest, candidate, source or storage name, path, thumbnail,
document bytes, OCR text, template prose, or external capability. A result is a bounded tuple of at
most 128 `(requirement_id, page_ids)` proposals.

The provider cannot choose origin, review state, confirmation state, timestamp, or record UUID.
The application always translates a proposal to `origin: automation` and `status: suggested`.
Invalid, overlapping, unknown, inactive, excessive, or malformed proposals fail through stable
content-free errors. An automation suggestion can change a category to Needs review but can never
make it Ready; only the user's separate Accept action creates the coupled confirmation.

## GUI-safe editor and persistence boundary

`ChecklistEditorState` is frozen, toolkit-free, revision-bound, and path-free. Its public rows
contain only:

- reviewed requirement title/guidance, count facts, textual status, and internal assignment UUIDs;
- active page UUID, one-based case position, and current assignment UUID; and
- assignment UUID, requirement ID, ordered page UUIDs, origin, review state, and one derived boolean
  indicating that inactive or overlapping pages require renewed review.

Its public projection and safe representation expose no `ManifestSnapshot`, candidate document,
template document, digest, CAS token, source name, original filename, storage name, path, unrelated
manifest collection, or document content. The private immutable `ChecklistCommandHistory` retains
the exact template ID/version/digest binding plus checklist/page UUIDs, closed tokens, counts,
timestamps, and bounded deltas needed to authenticate and reconstruct a candidate. The digest never
enters a rendered row, announcement, safe representation, or routine log; the manifest CAS token
remains private to the workflow service. Announcements come from a closed, content-free registry.

All editing methods return a new state and run locally in the dialog. Add, Suggest, Confirm, Accept,
Reject, Move, Remove, Replace pages, both reorder forms, Undo, and Redo therefore require no worker
or backend call. Loaded state is the non-undoable baseline; session commands are bounded and exactly
undoable/redoable, including their original UUIDs and timestamps. `changed` compares current
durable checklist meaning with the opening value rather than merely testing whether an undo stack
exists.

The organizer owns an exclusive editing phase:

1. flush any pending organizer candidate;
2. call Begin and retain the returned safe opening state while the service privately retains the
   exact manifest digest/CAS authority and pristine checklist history;
3. let the dialog edit its detached state locally;
4. on Apply, require the candidate history to be an authenticated session extension of the
   pristine opening and require the current manifest case, revision, digest, and CAS token to match;
5. privately reconstruct the candidate, queue it, and immediately flush it; and
6. call `complete` only after durable success (or an accepted no-change result).

Candidate construction does not consume opening authority, so a failed autosave may retry the same
Apply without losing history. After a failed Apply, even undoing all local edits leaves Apply
available so the backend can replace the retained failed candidate with the opening checklist
meaning; Cancel remains unavailable once persistence has started. Loaded inactive assignment pages
are reported as Needs review, omitted from active-page ordering controls, and cannot be reordered.
`complete`, Cancel, and Abort release authority explicitly. No candidate or manifest crosses back
into the GUI. Concurrent page, checklist, namespace, or authority changes fail before mutation.

## Schema and migration decision

No schema version change is required. Case-manifest v1 and v2 already contain the same closed
`template_ref`, assignment, assignment-scope, and confirmation record shapes used here. The only v2
schema change was scan-transform payloads, and the accepted v1-to-v2 migration already preserves
template references, assignments, confirmations, UUIDs, page references, collection order, and
constraints exactly. PWS-0403 supplies runtime meaning for those existing records; it does not edit
their released meaning in place.

Template schema v1 also already expresses every required category definition: ID, title, guidance,
count unit/range, and total order. Checklist state belongs to the case, not the template, so adding
state fields to template v1 would be incorrect. The complete workflow is a content-version 1.1.0
artifact using schema v1, not a new grammar.

A future request for conditional requirements, classifier rules, confidence scores, nested
categories, or a different durable assignment/confirmation meaning would not fit this contract.
Its implication would be a separately reviewed schema/version transition, migration and
future-version fixtures, security analysis of any new interpretation, and explicit preservation of
existing exact template bindings. It must not be introduced as executable template logic or an
in-place edit.

## Accepted interfaces and verification constraints

The implementation is constrained by the released template loader and snapshot types, both case
manifest schemas, the v1-to-v2 migration preservation tests, the application/domain import-boundary
tests, and the existing organizer compare-and-swap/autosave contract. Unit evidence covers exact
template conversion, null/exact/mismatched binding behavior, all four statuses, two-step manual
confirmation, automation suggestions, assignment confirmation coupling, unassigned visibility,
move/remove/reorder, local undo/redo, save candidate/reopen behavior, authority conflicts, retry
after simulated persistence failure, and content-free failures/representations. UI and
accessibility evidence covers named keyboard alternatives, logical focus order, non-color text,
plain guidance, drag equivalence, live announcements, Cancel, Apply retry, and scaling.

## Upcoming decisions and implications

- **PWS-0404 preflight consumption:** preflight may turn these projections into typed findings, but
  a finding or heuristic must not confirm an assignment. The implication is that checklist
  authority stays here while preflight remains a read-only consumer.
- **PWS-0406 constraint and filename evaluation:** it will decide how confirmed assignment groups
  map to configured file/count/size/name checks. The implication is new findings and remediation,
  not new assignment states or template execution.
- **PWS-0407 output planning:** it must define how confirmed ordered logical documents feed combined
  and separate outputs and what blocks export when checklist review remains. The implication is a
  deterministic downstream plan; it must preserve assignment/page order and cannot silently accept
  pending suggestions.
- **Any richer category grammar:** conditional or inferred requirements would materially change the
  trust and versioning model. The implication is a frozen-decision and schema-transition review,
  not an incremental PWS-0403 extension.

This feature adds no dependency, worker operation, network access, cache, new storage location,
ThirdLife adapter, shared schema, or release-interface coupling. PaperWorkShell remains the sole
owner of its local template bindings, assignments, and confirmations.
