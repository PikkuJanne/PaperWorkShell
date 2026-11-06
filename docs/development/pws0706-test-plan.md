# PWS-0706 verification plan

Task: **PWS-0706 — Implement delete-after-export and accessible companion outputs**.

This plan records coverage and the intended short native review. It is preparation,
not a passed test or a human handoff. The implementation and helper must be finished,
tested and frozen before observations are gathered. The task result will identify
actual commands, artifacts, source bindings and outcomes.

## Automated coverage

Use the registered `tools/dev.py` formatting, lint, import-boundary, typing, unit,
integration, security, accessibility, schema, task, localization, documentation and
portfolio checks. Targeted tests supplement those entry points while iterating.

Run native-capable lanes serially for one Windows user. Worker access recovery
uses a per-user journal and checks approved resource roots before recovering an
entry. Concurrent unit, integration, security or separate native worker runs with
different staged roots can fail closed with an access-recovery allowlist error.
Use the serial developer dispatcher; preserve a failed run and verify ordinary
cleanup before a fresh serial attempt. Do not relax the guard or edit ACLs to
make tests pass.

| Boundary | Required observations |
|---|---|
| Export authority | No deletion offer from a planned, blocked, partial, uncertain, cancelled or failed export; exact committed output inventory and fresh immutable case revision must agree |
| Confirmation | Inventory is separate from the destructive action; no default/prechecked consent; Cancel, Escape and window close leave the case intact |
| Assisted access | Locked, expired, revoked and stale grants refuse preparation and deletion; locking hides owned inventory/companion dialogs; delayed acceptance cannot regain authority |
| Retained outputs | Selected destination is outside case removal scope; missing, replaced, truncated or changed exports refuse deletion; original external sources and unrelated cases retain exact hashes |
| Removal | Complete deletion; partial file failure; locked/open file; active storage lease; revision change; retry after partial removal; process/service restart |
| Recovery | No automatic resumed deletion; surviving files are reported with a stable code and case reference; a case with a removed manifest is not advertised as normally reopenable |
| Companion privacy | Default off; explicit warning and request; extraction uses only final verified PDF bytes in the restricted worker; source OCR, removed pages, metadata and removed content are absent |
| Companion limits | Existing ResourcePolicy enforced; incremental pages; bounded UTF-8 output; cancellation, timeout, worker fault, low disk, destination collision and failed publication leave prior files intact |
| Accessible output | Visible summary and saved UTF-8 summary agree; companion content/read order has explicit limitations; selectable keyboard-readable plain text with no hidden diagnostics |
| UI | Accessible name, role, description and checked/disabled states; safe initial focus; logical Tab/Shift+Tab; progress/terminal announcements; 200% text, pseudo-localization and RTL preserve controls |

Inspect actual synthetic companion bytes. For any PDF pipeline change, retain
reopen/page-count/render checks. Do not refresh accepted golden artifacts merely to
pass a test. Routine logs and evidence must contain no private source values.

## Short native screen-reader review

Use one isolated synthetic profile and one prepared, verified one-page export.
Prepare the case, labels and destination automatically. The synthetic visible text
must be comfortably large. Use the installed NVDA with a new isolated configuration,
the normal Windows Qt platform and the exact frozen production source. Reuse only
environment facts whose provenance still applies; collect executable/runtime facts
automatically. Previous PWS-0702/0705 observations are historical evidence and are
not passes for these controls.

One coherent journey covers the new behavior:

1. Read the final summary and companion privacy explanation with NVDA. Reach the
   companion option by keyboard and request its plain-text output. Read the actual
   saved text through the accessible preview or local plain-text reader.
2. Open the deletion inventory. Hear the case copies to remove, exports that stay,
   source preservation and the limit on forensic erasure. Cancel once and observe
   that the case remains usable.
3. Reopen the inventory, explicitly confirm deletion and activate its destructive
   action. Hear the actual terminal result and focus recovery. The helper verifies
   that the synthetic export/text remain and the case is absent or reports a
   precisely described partial result.

The expected preparation is automatic; no paths, hashes, versions or JSON are
typed by the operator. Offer explicit Open-folder/Copy actions only if the journey
needs them. Clipboard writes require an explicit Copy action and the helper never
reads the clipboard. Each observed activity must be checkpointed immediately with
source/helper/artifact/environment bindings. A failed or deferred activity remains
recorded, and resuming continues from the unfinished activity.

Estimated burden before final-helper walkthrough: **18–24 keyboard actions and
4–6 active minutes**, including entering the prepared view, reading both text
surfaces, one cancelled inventory, the final confirmation and recording results.
No external PDF reader/device switch, manual save/reopen cycle, or repeated earlier
assisted-use/NVDA journey is needed. Automated tests cover failure injection,
restart, filesystem accounting and freshness checks. Recount the complete journey
against the final helper; redesign it if the estimate substantially increases.

This may be agent-operated synthetic NVDA observation when available. Such evidence
must be labelled as agent-operated, without a human-assessment, packaged-release,
PDF/UA or hardware certification claim. Any required human judgment still pending
must be stated explicitly rather than inferred from widget tests.

## Existing local tooling to reuse carefully

`tools/pws0702_screen_reader.py` demonstrates bounded, exclusive, chained
observation records and automatic environment collection. Its phases/task ID and
allowed artifact roots are specific to PWS-0702; do not record PWS-0706 as an old
phase or overwrite earlier attempts.

Ignored PWS-0705 native helper material under `output/pws0705-resume/native-final/`
demonstrates isolated synthetic profiles and exact source/runtime bindings. It is
bound to old source checkpoints and must not be launched as a current PWS-0706
helper. The prior NVDA scripts similarly own old process/profile receipts. Prepare
new task-specific names, refuse another running reader, retain all old records and
quit only an owned reader normally after verifying its process identity.

## Standalone boundary

All testing and data remain PaperWorkShell-local. No sibling repository, service,
data store, adapter or release approval is required. These are source-tree feature
observations; packaged data locations, uninstall residue and exact release facts
remain owned by Milestone 8.
