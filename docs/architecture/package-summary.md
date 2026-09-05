# Package review, export, and accessible summary

PWS-0408 joins the saved checklist and [verified package exporter](package-export.md)
to the ordinary desktop workflow. This is source-tree pilot functionality, not a
packaged release guarantee.

## Authority and interaction

`app.package_workflow` owns one transient exclusive review. It flushes organizer
edits before planning, loads the exact built-in template, and evaluates the core
preflight registry against the saved case, checklist, template notices, and output
facts. File names and counts are planned facts; sizes remain unknown until actual
generation. A missing checklist stays readable but cannot authorize export.

The desktop exposes **Export package** through a button, menu, and Ctrl+E. Changing
output organization creates a new review identifier and resets acknowledgements.
Export requires that exact displayed identifier, fresh revision/hash/CAS authority,
and explicit human review. Saved requested filenames are never silently discarded
when the organization changes: an incompatible count requires a return to Constraints.
The actual destination path budget is recomputed before I/O; any resulting filename
change requires choosing a shorter destination, not silently substituting names.

Digital-signature review uses the existing signed-source policy. Explicit consent
is saved with CAS. The service returns the exact saved snapshot without reloading
and adopting a concurrent edit. Organizer history **and autosave** are rebuilt before
output writing. Any later authority change blocks export. This records permission
to create an unsigned derivative, not validation or preservation of a digital signature.

## Checks and limits

This review does not rerun full source inspection, page geometry analysis, or the
advisory quality service. Those facts are explicitly unavailable and their warnings
remain visible. Stored import flags are not inflated into full inspection evidence.
Generated PDFs still undergo the existing worker reopen, structure, page inventory,
encryption, order/rotation, and rendering verification before publication; actual
package file counts, bytes, names, and limits are checked by the exporter.

Workers are admitted lazily, run sequentially under the same app-scoped ResourcePolicy
and heavy-operation coordinator, and retain existing narrow handle grants and network
denial. PDF-only export does not require OCR installation. Mixed scan/image processing
and visual overlays in the same PDF group retain the PWS-0407 explicit rejection.
No resource budgets, engines, dependencies, schemas, or history meaning are changed.

The end-to-end pilot exposed a subset-export admission defect: a one-page output
from a multi-page immutable source requested only one page of parser capacity.
The structural, flattened, and searchable brokers now request the larger of the
output count and the largest authenticated input source count, still rejecting
anything above the unchanged central page cap. Exact output count remains verified
against the plan. This corrects per-operation source admission, not the resource policy.

## Output and privacy

`domain.package_summary` is a bounded display-only model. The adapter binds exact
template labels, checklist rules, plan, preflight dependency revisions, and actual
destination receipts. It never acts as an approval token. Representations hide
variable text; rendering is explicit plain text, never interpreted HTML. Unknown
facts and required human review remain unknown/reviewable after acknowledgement.

The final screen distinguishes committed, constraints-blocked, not-committed,
partial, and uncertain outcomes and names the exact known artifacts. If detailed
summary generation or late worker cleanup fails after publication, a bounded fallback
preserves the artifact inventory and clearly warns that detailed review or cleanup
could not be completed. Cancellation cannot erase already published files.

**Save summary** is optional, user-selected UTF-8 `.txt` publication with bounded
bytes, digest verification, free-space reserve, and no replacement of existing files.
It saves exactly the visible summary. It contains intended output filenames and
category labels; users should review it before sharing. It contains no hidden source
paths/names, source text, OCR text, identifiers, hashes, signature images, or diagnostics.
It is a companion, never a ZIP member and never counted as a portal upload artifact.
Saving is cancelable through the same visible operation controls. Cancellation or an
uncertain save never discards the known package result or falsely reports the companion
as saved; users are told to check the destination before retrying.
The explicitly optional technical manifest is not implemented; no new machine-readable
contract is needed for this task.

Package results and unsaved summaries remain transient and are not recovered after
restart. Close during export requests cancellation and retains the final screen for
review. Existing sources and history are not rewritten. There is no acceptance,
eligibility, legal, OCR-certainty, signature-validity, or permanent-redaction guarantee.

## Accessibility and evidence

Controls have accessible names, descriptions, visible focus, keyboard alternatives,
logical tab order, text wrapping/scaling, progress, cancellation, and safe recovery.
The read-only plain-text summary is selectable and keyboard scrollable. Review has no
timeout. Automated widget/bridge tests are not a substitute for first-time-user or
screen-reader usability evidence.

See the [human pilot protocol](../development/pws0408-pilot.md). Human acceptance
must be recorded separately; generating fixtures or passing automated tests cannot
mark that walkthrough complete.

## Standalone and release boundary

All work remains inside PaperWorkShell. User content stays in its own case and explicit
local derivatives. No account, cloud, sibling, shared runtime, adapter, or synchronized
release is introduced. The developer pilot launcher is not a public application CLI.
The release-interface sheet remains a draft, with unproved packaged facts left TBD.
