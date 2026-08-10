# Milestone 1 reliable vertical-slice gate — PWS-0114

- Review status: In progress; production vertical slice passed, trusted benchmark/demo and reference evidence pending
- Gate conclusion: PENDING
- Review date: 2026-08-10
- Evidence revision: Pending
- Review branch: `codex/pws-0114-m1-gate`
- Frozen decisions: PWS-D003, PWS-D009, PWS-D012, PWS-D013, PWS-D027, PWS-D029,
  PWS-D030

## Scope and decision rule

This gate reviews the Milestone 1 architecture proof from case creation through immutable PDF
import, worker-rendered thumbnails, organizer intent, deterministic verified export, and cold
recovery. It passes only when every PWS-0101 through PWS-0113 prerequisite is done, one
gate-owned multi-source scenario proves the complete uninterrupted chain, all required failure and
privacy evidence is current, the final exact revision passes the complete quality suite, and the
reference-profile run is recorded truthfully. No criterion is waived by component-level evidence.

The current product window exposes the accessible case, import, thumbnail, ordering, rotation,
removal, undo, redo, autosave, and recovery workflow. Verified PDF export is an application/worker
service in M1 and is not yet a window action. The gate therefore describes export as an
application-service architecture proof and does not claim that the current GUI provides an export
button or a complete end-user packet workflow. PWS-0408 and the Milestone 4 gate own that guided
workflow.

This gate does not claim OCR, image intake, overlays, templates, preflight, compression,
permanent redaction, release packaging, PDF/UA conformance, portal acceptance, or legal validity.

Authoritative sources are [ROADMAP.md](../../ROADMAP.md), [TASKS.yaml](../../TASKS.yaml),
[DECISIONS.md](../../DECISIONS.md), the
[worker lifecycle](../architecture/worker-lifecycle.md), the
[PDF export contract](../architecture/pdf-export.md), the
[startup recovery and diagnostics contract](../architecture/startup-recovery-and-diagnostics.md),
and the [M1 reference profile](../../benchmarks/reference-windows-v1.json).

## Reviewed environments

| Item | Current observation | Gate interpretation |
|---|---|---|
| Local operating system | Windows 11 build 26200, x86-64 | Functional development and manual-accessibility host |
| Python | CPython 3.14.6, 64-bit | Supported pinned minor |
| Qt UI | PySide6-Essentials/Qt 6.11.1 | Selected widget runtime |
| Screen reader | NVDA 2026.1.1 Speech Viewer | Maintainer-operated keyboard/NVDA evidence |
| Local processor allocation | 8 logical processors | Does not by itself satisfy the two-processor reference claim |
| Local physical memory | 16,527,003,648 bytes | Does not satisfy the 4,096-MiB reference claim |
| Local work volume | 980,393,390,080 bytes | Does not satisfy the at-most-8-GiB limited-storage claim |
| Reference profile | 2 logical processors, 4,096 MiB RAM, integrated graphics, limited storage, HDD or validated emulation | Final qualification remains pending |

The current workstation may provide a functional local-smoke record, two-core affinity, wall
timings, worker peak-memory snapshots, and workspace high-water usage. Those observations must not
be relabeled as the complete low-spec/HDD reference run. A VHD backed by SSD storage is not HDD
evidence under the accepted reference contract.

## Prerequisite review

| Task | Status | Evidence revision | Capability reviewed |
|---|---|---|---|
| PWS-0101 | Done | `31d5141` | Authenticated case workspace and lifecycle |
| PWS-0102 | Done | `28fcde5` | Limits and storage budgeting |
| PWS-0103 | Done | `3182132` | Source prechecks and stable failures |
| PWS-0104 | Done | `b5f6913` | Immutable source ingest and copy/hash proof |
| PWS-0105 | Done | `00ddd78` | Bounded PDF inspection and structural policy |
| PWS-0106 | Done | `74c1c7aae799195a87608a797d7136b88e80cc87` | Selected PDF renderer and fidelity evidence |
| PWS-0107 | Done | `6cbd6c2` | Restricted worker lifecycle and containment |
| PWS-0108 | Done | `e0563821a344c46dd0f456c3a1e56733d257dd5c` | Worker thumbnails and bounded cache |
| PWS-0109 | Done | `c6bad8a` | Atomic manifest persistence and autosave |
| PWS-0110 | Done | `a78dfb7` | Deterministic page intent and undo/redo |
| PWS-0111 | Done | `663c966` | Accessible desktop organizer and manual NVDA evidence |
| PWS-0112 | Done | `77972df` | Deterministic verified export and history |
| PWS-0113 | Done | `8a0b80c` | Cold startup recovery and sanitized diagnostics |

## Gate criteria

| Criterion | Current result | Required evidence |
|---|---|---|
| Every M1 prerequisite is done | Pass | PWS-0101 through PWS-0113 are `done`; final task-graph validation must remain green |
| Originals remain byte-for-byte unchanged | Pass | The gate scenario records external and committed-source bytes plus SHA-256 before import and after export/restart |
| The same manifest produces the same semantic page result | Pass | PWS-0112 repeats one exact plan across fresh workers; the gate scenario additionally proves equal semantic fingerprints, order, rotation, rendered result, verified output hash, and bytes across history-only manifest revisions |
| Malformed input and worker termination fail cleanly | Pass in focused suites | Final report must cite live malformed render, active render termination, active export termination, cold recovery, and no failed destination publication |
| Export passes parse, structure, page count, encryption, and render checks | Pass | Gate scenario exposes the exact typed verification report and all seven current check codes for both exports |
| Routine logs contain no prohibited document content | Pass in gate scenario and focused suites | Gate scenario scans its canonical diagnostic JSONL, rendered summary, caplog, stdout, and stderr for distinct filename, path, destination, text, and metadata canaries in raw and JSON-escaped forms |
| Primary organizer path works without a mouse | Pass | Maintainer keyboard-only/NVDA scenario at actual 100% and 200% Windows scaling |
| UI remains responsive for worker operations | Thumbnail path pass; export runner pending | Gate evidence must record background export heartbeat/cancel behavior without claiming a GUI export action |
| Reference vertical slice passes on low-spec target | Pending | Conforming environment observation/attestation plus functional result and record-only measurements |

## Gate-owned vertical-slice scenario

The committed gate scenario uses only public synthetic fixtures and a newly created random case.
It:

1. Import at least two PDFs through the production desktop/backend composition.
2. Render bounded thumbnails in the restricted worker.
3. Apply a cross-source reorder, a rotation, removal, undo, redo, and atomic autosave.
4. Build the persisted export plan and export twice through fresh restricted workers.
5. Require strict reopen, structural inspection, expected page count, unencrypted output,
   every-page render, order/rotation equality, and final destination reopen.
6. Close all live services, run cold startup recovery, reopen the case, and reconstruct the same
   authoritative sequence and export fingerprint.
7. Recheck source bytes/hashes and prove no unpublished destination, generic temp, or routine-log
   content escaped its boundary.

The scenario will retain failure matrices in their focused owner tests. It will not duplicate
every malformed, disk, link, or forced-termination test inside the happy-path chain.

## Failure and recovery evidence

| Failure | Exact evidence | Expected invariant |
|---|---|---|
| Extension/content mismatch | `test_real_extension_mismatch_fails_without_source_mutation` | Rejected before parsing; no source or manifest mutation |
| Truncated committed PDF | `test_committed_truncated_pdf_is_rejected_without_source_mutation` | Stable failure; immutable source retained |
| Excessive source/request | Oversized import-precheck and thumbnail pixel-policy tests | Bounded rejection before expensive worker work |
| Malformed thumbnail source | `test_live_malformed_pdf_fails_safely_and_discards_unpublished_stage` | No authoritative cache publication |
| Worker killed during render | `test_live_worker_termination_during_render_recovers_with_fresh_service` | Last manifest/source unchanged; cold recovery; fresh success |
| Worker killed during export | `test_live_worker_termination_never_publishes_destination` | No destination/history publication; cold recovery; retry success |
| Manifest write failure | Atomic repository `disk_full` stage regression | Last valid manifest remains authoritative |
| Corrupt cache/restart leftovers | Startup-recovery integration/security suites | Safe cleanup is case-confined and path-free |

## Export verification inventory

The successful gate outcome must contain these closed checks:

| Check code | Meaning |
|---|---|
| `PDF_REOPENED` | The worker candidate strictly reopened as PDF |
| `STRUCTURE_CLEAN` | qpdf structural inspection passed |
| `PAGE_COUNT_MATCHED` | Actual and planned page counts matched |
| `UNENCRYPTED` | No output encryption remained |
| `ALL_PAGES_RENDERED` | Every output page rendered successfully |
| `ORDER_ROTATION_MATCHED` | Render comparison matched planned source order and rotations |
| `OUTPUT_COMMIT_REOPENED` | The final no-replace destination reopened after commit |

## Privacy evidence

The gate-owned scenario uses distinct synthetic canaries for display names, external
filenames, absolute paths, destination names, document text, and metadata values. It scans
the actual typed diagnostic JSONL, summary rendering, captured Python logging, stdout, and stderr
for raw and JSON-escaped forms. Manifest/source/export artifacts are not routine logs and are not
scanned as though they were diagnostic output.

Existing defense-in-depth evidence remains in `test_diagnostics_privacy.py`,
`test_pdf_thumbnail_service_privacy.py`, `test_source_ingest_security.py`, and
`test_page_organizer_privacy.py`.

## Accessibility and manual evidence

The maintainer completed the PWS-0111 keyboard-only and NVDA Speech Viewer procedure at actual
100% and 200% Windows display/text scaling with no observed focus, pointer dependency, clipping,
reachability, name/role/state, progress, cancellation, recovery, privacy, or scaling issue. The
exact record is [PWS-0111 NVDA smoke](../accessibility/pws-0111-nvda-smoke.md).

That procedure did not prove an export button in the current product window. The remaining M1
manual activity is a gate-runner architecture demonstration: inspect the synthetic verified
derivative for expected order/rotation/page count, observe the deliberate failed-worker path
publish no destination, cold-reopen the same synthetic case, retry successfully, and record the
seven checks plus unchanged source hashes. Its result remains pending.

## Reference measurements

The measurement policy is record-only; there is no approved numeric product-performance target.
The final evidence must bind observations to exact repository, profile, fixture, and engine
identities and record:

- total and import/thumbnail/autosave/export/recovery wall time;
- background export heartbeat or cancellation latency;
- configured worker process/job limits and observed peak process/job memory;
- case/cache/temp/output usage at defined checkpoints;
- page/preview/output counts and output bytes;
- semantic fingerprint and deterministic equality;
- recovery status and authoritative reopened state.

A single cold sample is acceptable when labeled `sample_count: 1`. Missing CPU/I/O accounting
must remain omitted rather than inferred from unavailable worker telemetry.

## Automated evidence

| Command | Current result |
|---|---|
| `uv run --locked --no-sync python tools/dev.py check` | Baseline pass at PWS-0111: 1,987 unit, 351 integration with 2 capability skips, 256 security with 6 capability skips, 19 accessibility, and all static/validation gates; final PWS-0114 exact-tree rerun pending |
| `uv run --locked --no-sync pytest -q tests/integration/test_m1_vertical_slice_windows.py` | Pass: 1 test in 719.95 seconds on the development workstation |
| `uv run --locked --no-sync python tools/dev.py benchmark-m1-vertical-slice` | Pass: canonical record-only host observation; vertical slice truthfully `not-run`; processor and limited-storage requirements mismatched and RAM/graphics/media unattested |
| GitHub Actions local-equivalent suite | PWS-0111 final runs passed in 35m28s and 25m02s; PWS-0114 exact-revision runs pending |
| Reference-profile vertical-slice command | Closed schema and host observation implemented; trusted live-runner and conforming-host execution pending |

## Known limitations and owned future work

- The current M1 desktop does not expose verified export as a window action. This report will not
  claim otherwise. Guided end-user export integration remains owned by the packet workflow.
- The current development workstation is not the complete low-spec/HDD reference environment.
- Same-account administrator compromise and OS-managed forensic-erasure limitations remain those
  already accepted by the threat model.
- Optional image intake, OCR, overlays, template/preflight, compression, redaction, and release
  capabilities remain gated by later milestones.

## Gate conclusion

**PENDING.** Prerequisites, the maintainer keyboard/NVDA gate, and the gate-owned production
scenario—including its actual typed-log privacy scan and cleanup proof—pass. PWS-0114 remains open
until the trusted measured/demo runner, background export responsiveness evidence, final
exact-revision quality and CI runs, gate-specific manual derivative/recovery inspection, and
conforming reference-profile measurement are recorded without unresolved blocking defects.
