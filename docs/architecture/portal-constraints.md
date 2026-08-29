# Portal constraints and safe output names

- Status: PWS-0406 implementation contract, exercised by PWS-0407 package export
- Tasks: PWS-0406, PWS-0407
- Decisions: PWS-D008, PWS-D025, PWS-D029, PWS-D037
- Preflight authority: [Typed preflight rule engine](preflight-rule-engine.md)

## Claim and authority boundary

Portal constraints are user-reviewable packaging rules, not a portal integration or an acceptance
decision. PaperWorkShell can report that an exact planned artifact exceeds a configured byte, file,
or page limit, uses a disallowed organization or extension, or has an incompatible encryption
state. It cannot report that a portal will accept the package. Generic template values must be
compared with the current external instructions before export.

`paperworkshell.preflight.portal_constraints` is pure and path-free. It consumes immutable
constraint values, the canonical PWS-0404 output-plan projection, and explicit output observations.
It opens no document or filesystem object and performs no logging, dynamic execution, process
launch, or network access. The output names needed for evaluation are deliberately hidden from
representations and never become finding text or exception arguments.

The PWS-0404 `OUTPUT_PLAN` dependency remains authoritative for the case revision, opaque output
keys, order, page membership, and estimated bytes. PWS-0406 does not replace that canonical fact
with a custom value. Exact filename and encryption observations are correlated to every output key
and position before evaluation. PWS-0407 now supplies the final authority-bound plan and rebuilds
that same canonical fact with verified actual byte sizes. The evaluator remains intentionally
outside the freestanding engine registry so no caller can bypass that exact binding.

## Constraint model

The version-1 model supports the already released template and manifest vocabulary:

- combined PDF, separate PDFs, and ZIP output modes;
- a required-unencrypted or encryption-allowed policy;
- optional maximum top-level file count, total bytes, bytes per file, and pages per PDF;
- a template default mode used as guidance, not a persisted export selection; and
- fixed, declarative package and requirement filename stems.

Limits are positive safe integers when present. Absence means that no limit is configured; zero is
never interpreted as unlimited. Observed collections remain bounded to the released 4,096-output
preflight capacity even though a stored numeric limit may use the manifest schema's larger safe
integer range. Total-package and per-file byte limits are independent constraints, matching the
released manifest schema; an active value is evaluated at its own boundary.

The validated generic template maps through one fixed code adapter. Its
`safe_static_stem_with_counter_v1` value selects compiled PaperWorkShell behavior; it is never
evaluated as a pattern or expression. The adapter rejects unknown output-contract versions,
policies, modes, or inconsistent requirement stems.

The case editor persists only manifest-v2's existing constraint subset: allowed modes, encryption
policy, optional numeric limits, and requested output names. Template-only default-mode and
filename-policy declarations remain private reset authority. Editing a generic default creates a
clearly labelled case-specific override; it never changes the template or gains template-verified
status. The PWS-0407 planner receives one selected output organization and rejects it unless the
saved case constraints allow it; PWS-0408 owns presentation in the final package workflow.

The application workflow opens a detached state bound to the exact case revision, manifest digest,
CAS token, and private editor session. The organizer flushes earlier intent before opening, validates
the complete draft atomically, and alone queues and commits the candidate through the existing
autosave authority. A failed save retains both candidate and editor authority for exact retry; an
uncommitted draft can be cancelled without mutation. The scrollable modal provides labelled
keyboard controls, explicit optional-limit toggles, first-invalid focus, status announcements, and
a safe-default Cancel path.

## Filename safety and collisions

Output names are single components, never paths or archive entry paths. Validation performs NFC
normalization and rejects:

- empty, dot, or dot-dot components;
- Windows-forbidden component characters, `/`, `\\`, drive/stream separators, and percent signs
  that could create encoded-path ambiguity;
- control, formatting, bidi-control, and surrogate code points;
- trailing spaces or dots;
- Windows device basenames, including superscript `COM`/`LPT` forms; and
- values beyond the released 180 UTF-16-code-unit component limit or the supplied destination
  parent-path budget.

Unsafe user text is rejected with a content-free error rather than silently rewritten. Canonically
equivalent Unicode is normalized to NFC. For valid stems that collide under NFC plus case-folded
Windows comparison, `resolve_output_filenames` appends `-2`, `-3`, and so on in stable output order.
Only the colliding suffix case may shorten a stem, and only enough to retain the suffix and derived
`.pdf` extension inside the component/path budget. ZIP plans use that same resolver for their PDF
members. The distinct `resolve_zip_archive_filename` accepts exactly one position-one request and
derives the outer `.zip` component through the same safety checks. The resolved preview exposes the
counter index so the change is understandable.

The editor can prove component safety without knowing a destination. PWS-0407 repeats the length
check against the actual selected destination before generation and again in the final constraint
evaluation. ZIP member components use their archive namespace rather than inheriting the external
parent-path length; the outer archive alone uses the complete destination budget. The evaluator separates a
`PortalArchiveObservation` for the outer ZIP from the ordered PDF-member observations bound to the
canonical plan. Top-level count and total bytes use the archive; per-file size checks cover both the
archive and members; page/encryption checks apply to members; and `.zip`/`.pdf` extensions are
checked in their correct namespaces. A ZIP-oriented case-name draft permits at most one requested
archive name. PWS-0407 populates and authority-binds the separate archive and member observations
from the exact verified plan without changing their meaning or duplicating filename logic. See
[verified package export](package-export.md).

## Findings

Constraint violations are blocking findings with closed codes and concrete remediation. Unknown
output plans, missing size estimates under an active limit, and unknown encryption under a
required-unencrypted policy do not become a clean result. Evaluation checks independently:

- selected mode and organization;
- file count;
- total and per-file byte limits;
- per-file page limits;
- mode-derived extension;
- required-unencrypted policy; and
- Windows comparison collisions.

An empty finding set means only that these configured checks found no violation in the supplied
exact observations. It is not an acceptance, legal-sufficiency, legibility, privacy, or successful
export claim. PWS-0407 combines it with per-PDF reopen, parsing, page-count, encryption, structure,
and rendering reports plus destination publication evidence before package success under PWS-D029.

## Standalone and release-interface effects

The implementation uses only local PaperWorkShell template, manifest, preflight, application, and
UI contracts. It adds no account, portal connection, sibling dependency, shared schema, service,
SDK, CLI, RPC operation, file association, or release synchronization. The released manifest and
template schemas already contain the required subset, so PWS-0406 adds no schema version or
migration and does not change `docs/release/RELEASE_INTERFACE.md`.
