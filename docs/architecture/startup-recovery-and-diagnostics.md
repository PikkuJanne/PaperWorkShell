# Startup recovery and sanitized diagnostics

- Status: Binding PWS-0113 implementation contract
- Task: PWS-0113
- Decisions: PWS-D027, PWS-D028, PWS-D039
- Persistence: [Atomic manifest repository and autosave contract](manifest-repository.md)
- Export: [Deterministic verified PDF export](pdf-export.md)
- Workspace: [Secure case-workspace contract](case-workspace.md)
- Worker: [Restricted worker lifecycle](worker-lifecycle.md)
- Diagnostic schema: [Sanitized diagnostic record and summary v1](../schemas/diagnostics-v1.md)
- Security flows: [Data-flow and trust-boundary model](../security/data-flows.md)

## Scope and claims

PWS-0113 provides a bounded startup scan that restores authoritative case state before it removes
non-authoritative artifacts. It also provides closed, structured routine diagnostics and a
human-readable summary containing only documented safe fields. These paths run in the trusted
desktop/storage boundary; they do not parse documents, start a worker, recreate an export, contact a
network service, or modify an external source or destination.

The startup result is suitable for a plain-language UI notice. It contains internal case UUIDs,
manifest revisions, closed status/issue codes, and numeric cleanup or repair counts. It never
contains a case path, original or destination filename, display name, document value, digest, or
raw exception. `complete` means the bounded scan found no case requiring attention; it is not a
claim that document contents or external destinations were revalidated.

## Layer ownership

| Layer | PWS-0113 responsibility |
|---|---|
| `domain.recovery` | Path-free case/startup results, closed issue/error codes, deterministic ordering, and bounded counts |
| `app.recovery` | Toolkit-free startup orchestration and aggregate sanitized diagnostic events |
| `storage.recovery` | Authenticated manifest-first case scan, stable/retired export-history recovery, export-retry settlement hook, source-orphan cleanup, thumbnail-stage cleanup, and case-temp cleanup |
| `domain.pdf_export_retry` | Versioned `PREPARED`/`COMMITTED` receipt envelopes, strict codec, bounded inventory, and reserved export IDs |
| `app.export_history` | History-only restart settlement and independent proof checks; no worker or destination port |
| `storage.export_retry` | Private case-journal publication, readback, classification, and exact receipt disposal |
| `diagnostics` | Closed records, canonical encoding, bounded logger, safe summary model, and deterministic human-readable rendering |

Storage never imports the application layer. Application services receive the recovery, retry,
history, and diagnostic implementations through structural ports. The diagnostics package does not
import storage, UI, worker, document adapters, networking, subprocess, path APIs, or Python's
general-purpose logging framework.

`paperworkshell.__main__.build_windows_startup_recovery_service` owns the complete concrete Windows
history, retry, manifest, and startup-store wiring at the desktop composition root, so export
settlement cannot be omitted. `app.recovery` remains storage-agnostic and receives only its
structural startup store plus an optional already-sanitized diagnostic sink. Desktop backend
construction completes this recovery before creating worker resources and passes the path-free
result into the organizer backend. Committed-receipt restart integration calls the same composition
factory.

## Startup order and authoritative state

Startup applies this order independently to each canonical internal case candidate:

1. Enumerate at most 4,096 case candidates through the authenticated cases-root boundary.
2. Load the current manifest through the PWS-0109 repository. A completely validated recoverable
   checkpoint may be promoted before any cleanup. A malformed, future-version, mismatched, or
   otherwise unavailable case is reported without cleaning any of its artifacts.
3. Validate stable export history against every manifest reference independently of retry-journal
   inventory. If the stable name is absent, only one canonical, authenticated UUIDv4 `.retired`
   predecessor may be restored. Generic `.tmp` files are unbound and never promoted. Multiple,
   malformed, corrupt, linked, streamed, or otherwise ambiguous retirement evidence is preserved
   and reported without generic temp cleanup.
4. Inventory and settle durable PDF export retry receipts. This happens before generic case-temp
   cleanup because a receipt may be the only safe evidence classifying an interrupted export.
5. Retain an authenticated case authority and transaction guard, then revalidate the exact
   manifest identity, digest, and CAS token used to plan cleanup.
6. Revalidate stable/retired export history under that cleanup transaction. A history or retry
   recovery failure returns attention before source, thumbnail, or generic temp cleanup.
7. Scan at most 4,096 entries in `originals/`. Remove only canonical random UUIDv4 `.bin` objects
   not referenced by that exact manifest. Unexpected names, aliases, links, sparse objects,
   alternate streams, identity changes, and deletion failures are retained and reported.
8. Remove authenticated stale thumbnail stages through their owning bounded cache API.
9. Inventory and remove authenticated disposable objects below the fixed `temp/` root through the
   workspace quota policy. The root itself is preserved.
10. Reauthenticate the case boundary and return only closed statuses and numeric facts. If the
   manifest changed during cleanup planning, reload and retry once; continued change is reported
   for attention instead of guessing.

Cleanup authority never comes from a displayed path or journal payload. Every mutation remains
below one authenticated case root and uses the owning adapter's no-follow identity, link, stream,
permission, quota, and transaction checks. The scanner does not clean `journal/` generally,
rewrite `manifest.json` directly, remove a referenced source, or cross into a sibling case,
external original, or export destination.

## Durable PDF export retry states

An export crosses an irreversible external destination boundary before its case-local history and
manifest link are guaranteed to finish. The protected case journal therefore distinguishes two
states for the exact path-free history receipt:

| State | Meaning at restart | Permitted action |
|---|---|---|
| `PREPARED` | The receipt was durable before destination publication, but exact destination commit is not proven by this state | Retain as commit-ambiguous unless exact downstream case history or an exact manifest reference independently proves the same record |
| `COMMITTED` | Exact destination publication and reopen completed before the replayable marker was published | Retry only the idempotent case-local history append/manifest link, then remove the exact marker after success |

The prepared and committed envelopes carry the same canonical export-history record, case UUID,
export UUID, phase, versions, and record digest. They contain no destination path or filename.
Each envelope is capped at 16 KiB with a maximum JSON depth of 12 and 256 JSON nodes. Inventory is
bounded to 4,096 unique receipts and 8,192 phase entries; canonical filenames bind the export UUID
and phase. Invalid, duplicate, mismatched-phase, alias, linked, streamed, noncanonical, or
future-version state is not replayed. Its parseable export UUID remains reserved, and the startup
result requires attention.

`PREPARED` alone is deliberately insufficient evidence. A crash can leave that marker whether the
external rename did not happen or happened immediately before the process stopped. If the exact
record already exists in `export-history.json` or the manifest carries its exact reference, that
independent case-local state permits idempotent history repair and disposal of the prepared marker.
Otherwise the marker remains for review; startup does not publish a new PDF or label the external
destination as committed.

`COMMITTED` replay and proven-`PREPARED` settlement call only the history repository and manifest
CAS link. They never call source acquisition, export planning, worker launch, PDF assembly,
destination selection, destination commit, or external-path discovery. A failed append, link, or
exact marker removal stays pending for a later startup. Receipt settlement precedes general temp
cleanup, while durable journal markers remain outside the disposable temp tree.

## Recovery results

Each valid case is `current` or `recovered`; an invalid case is `unavailable` and carries no
revision or mutation count. Recovery may report these closed attention conditions:

- invalid, unsupported, or unrecoverable manifest state;
- a case that changed while cleanup was being authorized;
- retained source artifacts or partial thumbnail/temp cleanup;
- an unexpected cleanup coordinator failure that is distinct from a manifest change;
- a commit-ambiguous prepared export receipt;
- a replayable receipt whose history repair remains pending; or
- invalid export-retry journal state; or
- an export-recovery service failure that is not mislabeled as an invalid receipt; or
- missing, corrupt, mismatched, or ambiguous stable/retired export-history authority.

Counts distinguish removed source orphans, retained source artifacts, removed thumbnail stages,
temp files/directories, repaired/pending/ambiguous/invalid export receipts, and released logical
bytes. These are operation facts, not evidence of secure erasure. A recovery failure preserves the
last valid manifest and imported originals and returns a stable sanitized error.

Cleanup reports retain every deletion and released-byte count already confirmed before a later
ordinary failure. An unexpected source, thumbnail, or temp item failure stops or returns a partial
phase report rather than erasing those facts; later mutation does not continue after an
unclassified coordinator-level failure. Process-control exceptions still propagate.

Restoring an authenticated retirement changes the case status to `recovered`, including when no
receipt exists. `repaired_export_history` counts only exact receipt records settled through the
history/manifest services, so a retirement-only restore leaves that count at zero. Any unresolved
history authority or retry-inventory/service failure returns before generic temp cleanup and
preserves all retirement, stage, and unrelated temp evidence for review.

## Sanitized structured diagnostic schema

Routine logging accepts a validated `DiagnosticRecord`, not a format string, arbitrary mapping,
path, logging `extra`, document value, or exception object. Its canonical ASCII JSON object has
only these fixed fields:

| Field | Safe value |
|---|---|
| `record_version` | Exact supported integer version |
| `occurred_at` | UTC timestamp |
| `event` | Closed event code |
| `case_id` | Optional internal UUIDv4, only for case-bound events |
| `operation_id` / `operation_kind` | Optional internal UUIDv4 and closed kind, only for operation events |
| `outcome` / `failure_code` | Closed terminal outcome and generic code with event-consistency checks |
| `versions` | Unique allowlisted application, Python, schema, protocol, and engine numeric versions |
| `counts` | Unique allowlisted nonnegative integer counts |
| `resources` | Unique allowlisted duration, CPU, peak-memory, temporary-storage, and released-storage integers |

One encoded record is at most 16,384 bytes. Numeric facts are nonnegative integers no greater than
9,007,199,254,740,991. A record has at most 16 versions, 32 counts, and 32 resource observations;
versions contain one through four numeric components, each no greater than 999,999,999. Duplicate
categories and inconsistent event fields fail closed.

The in-memory logger retains 256 records by default and allows a configured maximum of 4,096. It
copies and revalidates every record, stores the same canonical JSON line it can mirror to an
already-open binary sink, counts displaced records and sink failures, and never owns or closes the
sink. Ordinary sink exceptions are discarded and become a closed diagnostic error without
retaining their message or traceback values; process-control exceptions deliberately propagate.

The current event registry covers application, recovery scan/case cleanup, worker lifecycle, and
operation lifecycle events. Startup recovery emits aggregate case and cleanup counts plus released
case-storage bytes and uses the generic `RECOVERY_ATTENTION_REQUIRED` code for a valid partial scan.
Diagnostic emission is best-effort for ordinary failures: such a logger failure must not replace a
recovery result or make case mutation less safe, while process-control exceptions deliberately
propagate.

Routine records and summaries exclude OCR text, document text, form values, names, addresses,
original or destination filenames, full paths, metadata values, hashes/digests, thumbnails,
signature images, source/derivative bytes, native tool output, free-form messages, and raw
exceptions or stack values.

## Human-readable summary and PWS-0805 boundary

`DiagnosticSummary` is an immutable, revalidated snapshot of:

- summary version and UTC generation time;
- record, dropped-record, and sink-failure counts;
- up to 16 allowlisted numeric versions;
- up to 4,096 unique internal operation UUIDs;
- up to 32 unique allowlisted counts and generic failure codes; and
- up to 32 unique numeric resource observations.

`build_diagnostic_summary` accepts only a frozen tuple of at most 4,096 validated records. It
requires one consistent numeric value per version category, deduplicates operation IDs and closed
codes, sums counts and cumulative resource observations, and takes the maximum peak-memory observation.
`SanitizedDiagnosticLogger.snapshot_summary` captures its bounded records and numeric drop/sink
failure counters under one lock before calling that builder.

Its renderer uses fixed trusted labels and deterministic ordering. It does not read logs, inspect a
case, collect files, or rediscover mutable state while rendering.

PWS-0113 stops at this structured logging and summary baseline. It does **not** create a diagnostic
bundle or imply that routine logs are automatically exported. PWS-0805 separately owns the
user-authorized workflow that freezes an exact file/category snapshot and digests, displays its
human-readable inventory, obtains explicit confirmation, writes only that snapshot to a local
destination, and verifies cancel/permission/partial-write behavior. No upload, email, telemetry,
or network action is part of either boundary.

## Worker interruption boundary

The case scan does not reconnect to or replay an interrupted worker. The retained Job Object and
existing worker lifecycle remain responsible for terminating a generation and its descendants;
fresh work uses a new profile, process, protocol identities, and capabilities. Persistent
runtime/code ACL repair is a separate Windows-launcher recovery boundary and finishes under its
protected cross-process mutex before a new grant or launch can proceed. Its private persistent
record is durable before the first guard or SID mutation. New v2 ledgers bind only approved host
resources, exact Windows object identities, the profile SID, exact owner identity, current-user
peer SID, original auto-inheritance states, and per-file shared-guard baselines. Strictly decoded
legacy v1 ledgers retain their inherited-root recovery meaning. No case path or case ACL belongs in
either ledger version.

Windows ACL integration evidence forcibly terminates a real helper owner at `PREPARED`,
partial-grant, `ACTIVE`, and `REVOKING` cut points while it retains the mutex, observes
`WAIT_ABANDONED` from recovery in the separate parent process, and proves repair before a new
grant. The exact-object v2 cases must prove SID absence, retention of one shared guard while
another authenticated `ACTIVE` lease covers the object, final restoration of its recorded guard
baseline, and auto-inheritance restoration before the ledger clears. Legacy-v1 and insecure,
corrupt, unknown-version, peer-SID/allowlist, conflicting-baseline, and object-identity cases remain
fail closed. The test deletes the abandoned profile separately for test hygiene; production cold
recovery repairs only the recorded runtime/code ACL lease. It is not general ACL repair or
worker-operation replay. Complete details are in the [worker lifecycle](worker-lifecycle.md) and
[ADR 0004](../adr/0004-windows-worker-isolation.md).

## Verification ownership

Pure tests cover recovery-result invariants, deterministic ordering, diagnostics event matrices,
canonical encoding, capacity bounds, copy-before-retain behavior, summary rendering, receipt
phase/correlation rules, and history-only restart settlement. Windows integration tests cover
manifest-first recovery, invalid-case isolation, source/thumbnail/temp cleanup, receipt settlement
before generic temp cleanup, independent manifest-to-history validation, authenticated unique
retirement restoration before committed replay, strict-prefix retirement cleanup, committed replay
without worker/destination calls, proven prepared settlement, retained ambiguous preparation, and
exact marker cleanup. Security tests keep external and sibling sentinels unchanged, preserve all
temp evidence for ambiguous or linked retirement owners, reject linked/aliased recovery candidates,
and scan every public diagnostic seam and rendered output with distinct prohibited values.

Separate worker ACL unit/integration/security suites prove its canonical path-private journal,
pre-mutation durable phase, forced owner-process termination while holding a test-isolated mutex,
separate-process `WAIT_ABANDONED` recovery, fail-closed identity/hardlink/policy/corruption
handling, and unchanged live AppContainer/Job/descendant containment.

This evidence does not claim physical-media durability, forensic erasure, successful repair of an
arbitrary corrupt case, rediscovery of an external destination, automatic cold deletion of an
abandoned AppContainer profile/local temporary tree, or creation of the future PWS-0805 support
bundle.
