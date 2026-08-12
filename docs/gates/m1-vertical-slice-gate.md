# Milestone 1 reliable vertical-slice gate — PWS-0114

- Review status: Complete
- Gate conclusion: PASS
- Review date: 2026-08-12
- Evidence revision: `cf96a82c1a4b24bed980c2b324b9540c80cda48d`
- Review branch: `codex/pws-0114-m1-gate`
- Frozen decisions: PWS-D003, PWS-D009, PWS-D012, PWS-D013, PWS-D027, PWS-D029,
  PWS-D030, PWS-D041

## Scope and decision rule

This gate reviews the Milestone 1 architecture proof from case creation through immutable PDF
import, worker-rendered thumbnails, organizer intent, deterministic verified export, and cold
recovery. It passes only when every PWS-0101 through PWS-0113 prerequisite and PWS-0115 are done, one gate-owned
multi-source scenario proves the complete chain, required failure/privacy/accessibility evidence is
current, the final exact revision passes the quality suite, and the scenario passes the versioned
constrained resource policy. No criterion is waived by component-level evidence.

PWS-D041 supersedes the former requirement for an exact two-processor, 4-GiB,
integrated-graphics, bounded-volume, rotational-HDD environment. Physical low-end-device testing
has not been performed and is optional/non-gating. Automated constrained-profile evidence proves
bounded software behavior for the tested revision and synthetic workload; it does not emulate or
certify any CPU, RAM allocation, GPU, SSD, HDD, VM, or physical device.

The current product window exposes the accessible case, import, thumbnail, ordering, rotation,
removal, undo, redo, autosave, and recovery workflow. Verified PDF export is an application/worker
service in M1 and is not yet a window action. PWS-0408 and the Milestone 4 gate own that guided
end-user workflow.

This gate does not claim OCR, image intake, overlays, templates, preflight, compression,
permanent redaction, release packaging, PDF/UA conformance, portal acceptance, or legal validity.

Authoritative sources are [ROADMAP.md](../../ROADMAP.md), [TASKS.yaml](../../TASKS.yaml),
[DECISIONS.md](../../DECISIONS.md), the
[low-resource policy](../performance/LOW_RESOURCE_POLICY.md), the
[worker lifecycle](../architecture/worker-lifecycle.md), the
[PDF export contract](../architecture/pdf-export.md), and the
[startup recovery and diagnostics contract](../architecture/startup-recovery-and-diagnostics.md).

## Reviewed software environment

| Item | Current observation | Gate interpretation |
|---|---|---|
| Operating system | Windows 11 build 26200, x86-64 | Supported development and manual-accessibility host |
| Python | CPython 3.14.6, 64-bit | Supported pinned minor |
| Qt UI | PySide6-Essentials/Qt 6.11.1 | Selected widget runtime |
| Screen reader | NVDA 2026.1.1 Speech Viewer | Maintainer-operated keyboard/NVDA evidence |
| Resource policy | Constrained profile, versioned in production code | Numeric policy identity and results are required in the final report |
| Physical low-end device | Not tested | Optional/non-gating; no hardware-equivalence claim |

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
| PWS-0115 | Done | `cf96a82c1a4b24bed980c2b324b9540c80cda48d` | Enforced constrained resource policy and machine-readable evidence |

## Gate criteria

| Criterion | Current result | Required evidence |
|---|---|---|
| Every M1 prerequisite is done | Pass | PWS-0101 through PWS-0113 and PWS-0115 are `done` |
| Originals remain byte-for-byte unchanged | Pass | External and committed-source bytes plus SHA-256 agree before and after export/restart |
| The same manifest produces the same semantic result | Pass | Fresh-worker replay preserves semantic fingerprint, order, rotation, renders, and output bytes |
| Malformed input and worker termination fail cleanly | Pass | Live malformed render/export, forced termination, cold recovery, and no failed publication |
| Export passes reopen, structure, page-count, encryption, and render checks | Pass | Both verified exports contain all seven closed check codes |
| Routine logs contain no prohibited content | Pass | Diagnostic JSONL, summary, logging, stdout, and stderr reject raw and escaped canaries |
| Primary organizer path works without a mouse | Pass | Maintainer keyboard-only/NVDA scenario at actual 100% and 200% scaling |
| UI remains responsive for worker operations | Pass | Export runs off the Qt owner thread with measured heartbeats |
| Constrained resource policy is enforced | Pass | Report records policy identity, configured limits, observations, six passing budget results, and overall completion |
| Low-disk/cancellation/cleanup paths preserve state | Pass | Deterministic injected failures reject or recover without corrupting originals/manifests |

## Gate-owned scenario

The scenario uses only public synthetic fixtures and a newly created random case. It:

1. Imports two PDFs through production composition.
2. Renders bounded, demand-driven thumbnails in restricted workers.
3. Applies cross-source reorder, rotation, removal, undo, redo, and atomic autosave.
4. Builds the persisted export plan and exports twice through fresh restricted workers.
5. Requires strict reopen, structural inspection, expected page count, unencrypted output,
   every-page rendering, semantic order/rotation equality, and destination reopen.
6. Closes services, runs cold startup recovery, reopens the case, and reconstructs the same
   authoritative sequence and export fingerprint.
7. Rechecks source bytes/hashes and proves no unpublished destination, temporary artifact, or
   prohibited routine-log content escaped its boundary.
8. Applies the constrained policy and fails if a mandatory budget is absent, unknown, or exceeded.

Focused owner tests retain the larger malformed, disk, link, and forced-termination matrices. The
happy path does not duplicate every component-level failure test.

## Failure and recovery evidence

| Failure | Exact evidence | Expected invariant |
|---|---|---|
| Extension/content mismatch | `test_real_extension_mismatch_fails_without_source_mutation` | Rejected before parsing; no source/manifest mutation |
| Truncated committed PDF | `test_committed_truncated_pdf_is_rejected_without_source_mutation` | Stable failure; immutable source retained |
| Excessive source/request | Import-precheck and thumbnail pixel-policy tests | Bounded rejection before expensive work |
| Malformed thumbnail source | `test_live_malformed_pdf_fails_safely_and_discards_unpublished_stage` | No authoritative cache publication |
| Worker killed during render | `test_live_worker_termination_during_render_recovers_with_fresh_service` | Source/manifest unchanged; fresh success |
| Worker killed during export | `test_live_worker_termination_never_publishes_destination` | No destination/history publication; retry succeeds |
| Manifest write failure | Atomic repository `disk_full` regression | Last valid manifest remains authoritative |
| Low free space / mid-operation disk full | Constrained storage fault matrix | Stable failure, bounded cleanup, originals/manifests preserved |
| Corrupt cache/restart leftovers | Startup-recovery integration/security suites | Safe cleanup is case-confined and path-free |

## Export verification inventory

Successful output contains `PDF_REOPENED`, `STRUCTURE_CLEAN`, `PAGE_COUNT_MATCHED`, `UNENCRYPTED`,
`ALL_PAGES_RENDERED`, `ORDER_ROTATION_MATCHED`, and `OUTPUT_COMMIT_REOPENED`.

## Accessibility and privacy evidence

The maintainer completed the PWS-0111 keyboard-only and NVDA Speech Viewer procedure at actual
100% and 200% Windows display/text scaling with no observed focus, pointer dependency, clipping,
reachability, name/role/state, progress, cancellation, recovery, privacy, or scaling issue. See the
[PWS-0111 NVDA smoke](../accessibility/pws-0111-nvda-smoke.md).

The gate uses distinct synthetic canaries for display names, external filenames, absolute paths,
destination names, document text, and metadata. It scans typed diagnostics, summaries, captured
logging, stdout, and stderr for raw and JSON-escaped forms. Artifacts are not routine logs.

## Constrained resource evidence

The final report must record the resource-policy name/version, exact configured limits, observed
process-tree peak RSS, worker Job peaks, case-workspace/cache/temporary high-water, preview/cache bounds,
heavy-operation concurrency, heartbeat gap, and a closed result for every budget exercised by the
canonical workload. The canonical child measures active worker-render cancellation and runs the
focused low-disk, slow-I/O, and worker restart nodes before its authenticated result is accepted.
Worker-private temporary-storage peaks remain prospectively bounded by per-operation limits rather
than sampled separately in report v2. Missing or unknown required evidence fails the gate.

The canonical report at revision `cf96a82c1a4b24bed980c2b324b9540c80cda48d` decoded with zero
issues and recorded:

| Hard budget | Observed | Limit | Result |
|---|---:|---:|---|
| Complete process-tree RSS | 244,023,296 bytes | 1,342,177,280 bytes | Pass |
| Combined thumbnail/preview cache | 166,296 bytes | 134,217,728 bytes | Pass |
| Case-workspace temporary storage | 650 bytes | 536,870,912 bytes | Pass |
| Heavy-operation concurrency | 1 | 1 | Pass |
| Maximum controlled UI heartbeat gap | 147 ms | 250 ms | Pass |
| Protocol cancellation acknowledgement | 6 ms | 2,000 ms | Pass |

The run took 819,776 ms; duration is record-only, not a heterogeneous-machine hard gate. It also
recorded zero heartbeat violations, one-shot worker recycling with all workers terminated, passing
low-disk/slow-I/O/recovery/privacy outcomes, deterministic repeated output, two output pages, and
the retained output SHA-256
`8825cbc568f339ffa86fccf7f8f64400c69b2c82c09d651e903f6d2529bdc6ea`.

The earlier successful functional run at revision `77adff38e467cd033881e006c3c08408d3b33cd7`
remains historical architecture evidence: unchanged sources, deterministic two-page exports,
successful cold recovery, seven export checks, clean routine surfaces, and agent-rendered visual
inspection all passed. Its former hardware mismatch is no longer a gate criterion, but its
record-only observations are not substituted for the new constrained-profile run.

## Automated evidence required for closure

| Command | Required result |
|---|---|
| `uv run --locked --no-sync python tools/dev.py validate-m1-benchmark-report` | Pass |
| Focused constrained-profile unit/integration/security/accessibility tests | Pass: 414 tests and 350 subtests; focused real cancellation regression also passes |
| `uv run --locked --no-sync python tools/dev.py run-m1-vertical-slice` | Pass; published the exact-revision report and retained derivative |
| `tools/dev.py test-unit`, `test-integration`, `test-security`, and `test-accessibility` | Pass: 2,114 unit; 368 integration with 2 disclosed capability skips; 268 security with 6 disclosed capability skips; 19 accessibility |
| Registered format, lint, boundary, type, fixture, schema, task, docs, report, renderer, secret, and quality-self-test commands | Pass; the commands were preserved individually because the monolithic wrapper's external 30-minute ceiling was shorter than the combined lanes |
| Poppler `pdfinfo` plus all-page `pdftoppm` rendering | Pass; two unencrypted pages, legible Letter text followed by a 72×96-pt calibration page rotated 90°, with no clipping, overlap, blank corruption, or unreadable output |

## Known limitations and owned future work

- The current M1 desktop does not expose verified export as a window action; guided end-user export
  remains owned by PWS-0408.
- Physical low-end hardware has not been tested. Such testing is optional, non-gating, and cannot
  be inferred from constrained-profile results.
- Same-account administrator compromise and OS-managed forensic-erasure limitations remain those
  accepted by the threat model.
- Image intake, OCR, overlays, template/preflight, compression, redaction, and release capabilities
  remain gated by later milestones.

## Gate conclusion

**PASS.** PWS-D041 removes the unavailable physical-hardware blocker without weakening the
software resource contract. Every prerequisite, the maintainer keyboard/NVDA evidence, production
scenario, privacy and recovery checks, deterministic verified export, constrained resource report,
focused failure paths, final quality suite, and exact-revision artifact inspection pass. Physical
low-end hardware was not tested and is optional/non-gating; this result proves bounded software
behavior for the recorded revision and synthetic workload, not hardware certification.
