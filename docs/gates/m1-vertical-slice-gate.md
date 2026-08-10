# Milestone 1 reliable vertical-slice gate — PWS-0114

- Review status: Blocked after a successful trusted current-host run; conforming reference-profile evidence remains unavailable
- Gate conclusion: BLOCKED
- Review date: 2026-08-10
- Evidence revision: `7f75cf26a5a23e44ad069b47953bcf7cd502e73c`
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
| Local memory observation | 16,527,003,648 bytes; no 4,096-MiB allocation attestation | Does not establish the required 4,096-MiB reference allocation |
| Local work volume | 980,393,390,080 bytes | Mismatches the at-most-8,589,934,592-byte limited-storage requirement |
| Local graphics/storage media | Not attested | Does not establish integrated graphics or physical HDD/independently validated emulation |
| Reference profile | 2 logical processors, 4,096 MiB RAM, integrated graphics, limited storage, HDD or validated emulation | Final qualification is externally blocked |

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
| Malformed input and worker termination fail cleanly | Pass | Full integration/security suites cite live malformed render, active render termination, active export termination, cold recovery, and no failed destination publication |
| Export passes parse, structure, page count, encryption, and render checks | Pass | Gate scenario exposes the exact typed verification report and all seven current check codes for both exports |
| Routine logs contain no prohibited document content | Pass in gate scenario and focused suites | Gate scenario scans its canonical diagnostic JSONL, rendered summary, caplog, stdout, and stderr for distinct filename, path, destination, text, and metadata canaries in raw and JSON-escaped forms |
| Primary organizer path works without a mouse | Pass | Maintainer keyboard-only/NVDA scenario at actual 100% and 200% Windows scaling |
| UI remains responsive for worker operations | Pass for the architecture proof | Both exports ran off the Qt owner thread; 2,903 main-thread heartbeats were recorded with a 141,731,100-ns maximum observed gap, without inventing a product target or claiming a GUI export action |
| Reference vertical slice passes on low-spec target | Blocked | The functional run passed, but the observed host mismatched processor/storage requirements and lacked RAM/graphics/media attestations |

## Gate-owned vertical-slice scenario

The committed gate scenario used only public synthetic fixtures and a newly created random case.
It:

1. Imported two PDFs through the production desktop/backend composition.
2. Rendered bounded thumbnails in the restricted worker.
3. Applied a cross-source reorder, a rotation, removal, undo, redo, and atomic autosave.
4. Built the persisted export plan and exported twice through fresh restricted workers.
5. Required strict reopen, structural inspection, expected page count, unencrypted output,
   every-page render, order/rotation equality, and final destination reopen.
6. Closed all live services, ran cold startup recovery, reopened the case, and reconstructed the same
   authoritative sequence and export fingerprint.
7. Rechecked source bytes/hashes and proved no unpublished destination, generic temp, or routine-log
   content escaped its boundary.

The scenario retains failure matrices in their focused owner tests. It does not duplicate
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

That procedure did not prove an export button in the current product window. The canonical gate
runner completed the application-service architecture demonstration, including failed-worker owner
tests, cold reopen, deterministic retry, seven typed checks, and unchanged source hashes. An agent
rendered every page of the retained derivative with Poppler: page 1 contains the expected synthetic
text carrier and page 2 contains the expected rotated four-quadrant calibration graphic. This is
truthfully recorded as agent-operated artifact inspection; maintainer review of the retained
synthetic derivative on the conforming reference host remains pending.

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

The trusted current-host sample is canonical JSON bound to repository revision
`7f75cf26a5a23e44ad069b47953bcf7cd502e73c`, clean tree
`bedc9149455ac673e2cf6d449dc7e097906bcb3b`, run ID
`b3d1f12e-d210-41f8-9dd1-33dfc004d61d`, and the checked-in profile/schema/fixture,
production worker configuration, renderer profile, protocol, and engine identities. The report is
record-only and contains no pass/fail product-performance target.

| Measurement | Current-host observation |
|---|---|
| Total vertical-slice wall time | 729,791,790,500 ns (729.792 s); includes the deterministic replay export and final assertions |
| Timed phases | case create 127,776,900 ns; import 181,187,956,000 ns; thumbnails 274,575,004,600 ns; organize/autosave 90,829,295,600 ns; first verified export 90,881,696,600 ns; cold reopen/recovery 347,983,900 ns |
| Qt responsiveness record | 2,903 heartbeats; maximum observed gap 141,731,100 ns; both exports completed off the owner thread |
| Source-inspection worker | 36,012,032-byte process peak; 37,232,640-byte Job peak; 805,306,368/1,073,741,824-byte configured process/Job limits |
| Thumbnail worker | 35,913,728-byte process peak; 37,130,240-byte Job peak; same configured limits |
| Verified-export worker | 35,581,952-byte process peak; 36,802,560-byte Job peak; same configured limits |
| Workspace high-water | 118,690 case bytes, 109,848 cache bytes, 0 temp bytes, 15 entries, maximum observed depth 2 |
| Workload/output | 2 sources, 3 source pages, 2 active/output pages, 3 previews, 2 deterministic outputs; retained output 897 bytes |
| Result identities | output SHA-256 `8825cbc568f339ffa86fccf7f8f64400c69b2c82c09d651e903f6d2529bdc6ea`; semantic fingerprint `f552153412abff9037e8f3d203969912637619689d8e349dffe8e9a08ee40171` |
| Privacy/recovery | five distinct canaries absent from routine surfaces; recovery `current`; reopened sequence equal; no temp bytes at any checkpoint |

## Automated evidence

| Command | Current result |
|---|---|
| `uv run --locked --no-sync python tools/dev.py check` | Pass before the publication-only correction: 2,102 unit; 353 integration with 2 capability skips; 271 security with 7 capability skips; 19 accessibility; format, lint, import boundaries, mypy, schemas, task graph, docs, reference/benchmark validation, secret scan, and five deliberate self-tests |
| `uv run --locked --no-sync pytest -q tests/unit/test_m1_vertical_slice_live_runner.py tests/unit/test_m1_vertical_slice_benchmark.py tests/security/test_m1_vertical_slice_benchmark_security.py` | Pass after the correction: 100 tests with 1 disclosed reparse-capability skip; repository-defined typecheck passed 278 files |
| `uv run --locked --no-sync python tools/dev.py run-m1-vertical-slice` | Pass at `7f75cf2`: fixed production scenario and report-last publication completed in 733.3 seconds |
| Poppler `pdfinfo` plus all-page `pdftoppm` rendering | Pass: two unencrypted Letter pages, PDF 1.5; both pages rendered and were visually inspected; derivative SHA-256 matches the report |
| GitHub Actions local-equivalent suite | PWS-0114 exact-revision runs pending |
| Conforming reference-profile vertical slice | Blocked: current host reports 8 processors and a 980,393,390,080-byte work volume, with RAM/graphics/media attestations absent |

## Known limitations and owned future work

- The current M1 desktop does not expose verified export as a window action. This report will not
  claim otherwise. Guided end-user export integration remains owned by the packet workflow.
- The current development workstation is not the complete low-spec/HDD reference environment.
- Same-account administrator compromise and OS-managed forensic-erasure limitations remain those
  already accepted by the threat model.
- Optional image intake, OCR, overlays, template/preflight, compression, redaction, and release
  capabilities remain gated by later milestones.

## Gate conclusion

**BLOCKED.** Prerequisites, the maintainer keyboard/NVDA gate, the gate-owned production scenario,
the trusted measured runner, background-export heartbeat evidence, typed-log privacy scan, cleanup
proof, deterministic verified export/recovery, and agent-operated derivative inspection pass. No
current P0-P2 product defect remains. PWS-0114 cannot truthfully pass on this workstation because
the observed processor/storage profile mismatches the frozen low-spec reference and required
memory, integrated-graphics, and HDD/validated-emulation attestations are absent. The smallest
maintainer action is to run the same clean revision on a conforming reference environment with the
three explicit attestations, inspect the retained synthetic derivative, and provide the resulting
canonical report for final gate closure. PWS-0201 remains dependency-blocked.
