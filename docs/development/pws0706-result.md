# Task result — PWS-0706: Delete-after-export and accessible companion outputs

**Status: done** (2026-11-02). All 21 registered verification commands passed
serially on the exact owned source checkout; original branch/index state remains
unchanged. Final canonical metadata, privacy and preservation receipts are owned
by `output/pws0706/completion-metadata-terminal-closure.json` and
`output/pws0706/peer-preservation-final.json`. This report is frozen before those
terminal checks and is not circularly rewritten afterward.

## Implemented

- Separate deletion inventory, unchecked acknowledgement, and explicit final
  action after a verified export. Current case identity, revision, hash, storage
  inventory, assisted access and retained output hashes are checked again before
  close/removal. Cancellation and restart never initiate deletion.
- A partial removal is reported and leaves an unavailable case. Verified external
  exports remain independent. No forensic-erasure claim is made.
- Optional UTF-8 text from exact exported PDF bytes, including verified ZIP
  members, through a bounded restricted worker operation. Privacy acknowledgement
  is required; OCR is not run and source caches are never consulted.
- Existing accessible plain-text summary retained; new controls use keyboard,
  accessible names/descriptions, scalable layout, safe defaults and spoken status.
  Assisted locking discards the inventory and clears consent.

## Changed files

- `BUNDLE_MANIFEST.json`: canonical hashes and current task readiness.
- `README.md`: user workflow and recovery limits.
- `TASKS.yaml`: task state and verification evidence.
- `docs/architecture/post-export-retention.md`: privacy, authority, bounds and failure policy.
- `docs/development/pws0706-result.md`: task completion record.
- `docs/development/pws0706-test-plan.md`: automated and native verification plan.
- `src/paperworkshell/__main__.py`: real desktop service composition and receipt callbacks.
- `src/paperworkshell/app/organizer.py`: current-session deletion and companion orchestration.
- `src/paperworkshell/app/package_workflow.py`: companion consent, freshness and receipt invalidation.
- `src/paperworkshell/app/post_export_deletion.py`: typed deletion review and secure storage safeguards.
- `src/paperworkshell/domain/post_export_deletion.py`: typed deletion review and secure storage safeguards.
- `src/paperworkshell/domain/text_companion.py`: bounded final-derivative text contract and worker adapter.
- `src/paperworkshell/export/compression_review.py`: verified compressed-result receipt.
- `src/paperworkshell/export/package_service.py`: exact derivative receipt, companion saving and preservation.
- `src/paperworkshell/localization/catalogs/ar-XB.json`: refreshed externalized application text.
- `src/paperworkshell/localization/catalogs/en-XA.json`: refreshed externalized application text.
- `src/paperworkshell/localization/catalogs/en.json`: refreshed externalized application text.
- `src/paperworkshell/pdf/text_companion.py`: bounded final-derivative text contract and worker adapter.
- `src/paperworkshell/storage/post_export_deletion.py`: typed deletion review and secure storage safeguards.
- `src/paperworkshell/ui/assisted_session.py`: discard destructive consent on lock.
- `src/paperworkshell/ui/backend.py`: serialized Qt requests and sanitized correlated results.
- `src/paperworkshell/ui/package_export.py`: post-export actions and privacy acknowledgement.
- `src/paperworkshell/ui/post_export.py`: accessible inventory and explicit destructive confirmation.
- `src/paperworkshell/ui/window.py`: desktop lifecycle, lock and delayed-result handling.
- `src/paperworkshell/worker/desktop_composition.py`: shared bounded worker extractor.
- `src/paperworkshell/worker/host.py`: versioned handler registration.
- `src/paperworkshell/worker/protocol/codec.py`: strict inbound operation validation.
- `src/paperworkshell/worker/protocol/state.py`: strict outbound result binding.
- `src/paperworkshell/worker/protocol/text_companion.py`: bounded final-derivative text contract and worker adapter.
- `src/paperworkshell/worker/text_companion_broker.py`: bounded final-derivative text contract and worker adapter.
- `src/paperworkshell/worker/text_companion_operation.py`: bounded final-derivative text contract and worker adapter.
- `tests/accessibility/test_post_export_accessibility.py`: deterministic synthetic regression coverage.
- `tests/integration/test_post_export_deletion_windows.py`: deterministic synthetic regression coverage.
- `tests/integration/test_text_companion_service_windows.py`: deterministic synthetic regression coverage.
- `tests/integration/test_text_companion_worker_windows.py`: deterministic synthetic regression coverage.
- `tests/security/test_text_companion_privacy.py`: deterministic synthetic regression coverage.
- `tests/unit/test_post_export_backend.py`: deterministic synthetic regression coverage.
- `tests/unit/test_post_export_deletion.py`: deterministic synthetic regression coverage.
- `tests/unit/test_post_export_window.py`: deterministic synthetic regression coverage.
- `tests/unit/test_text_companion.py`: deterministic synthetic regression coverage.
- `tests/unit/worker/test_text_companion_broker.py`: deterministic synthetic regression coverage.
- `tests/unit/worker/test_text_companion_protocol.py`: deterministic synthetic regression coverage.
- `tests/unit/worker/test_desktop_composition.py`: require companion capability in the real desktop handshake.

- `benchmarks/pws0207-quality-findings-v1.json`: revision-only provenance renewal; all accepted measurements unchanged.

- `docs/development/quality-gates.md`: serialize Windows native-capable lanes sharing an access journal.

- `docs/schemas/worker-rpc-v1.md`: document the additive opt-in operation and strict payload/result contract.

- `src/paperworkshell/worker/client/client.py`: preserve default capability registration while adding explicitly requested optional operations.

The initial working-tree baseline, full path inventory and source hashes remain
under `output/pws0706/`; unrelated earlier work is preserved.

## Verification

Final serial verification passed against the exact owned checkout at
`output/pws0706/verification-worktree`, checkpoint
`f65602e951e8ef94016fad71101d33e873d9c2ad`. Its current 1,057-file implementation,
tests, schemas, workloads and lock metadata match the canonical working tree:
`bac718af2981b760e16353ea17f703c24ea3636a060a49a8fe9c4f20911a6365`.
Every registered command receipt records unchanged before/after source hashes in
`output/pws0706/qa-owned/results.json`. The original branch, HEAD, index and refs
are preserved; no original-root index-bound full-suite pass is claimed.

| Command or observation | Current result |
|---|---|
| `tools/dev.py format-check`, `lint`, `typecheck`, `check-import-boundaries` | PASS; 837 Python surfaces; no typing/import error |
| `validate-schemas`, `validate-task-graph`, `validate-localization`, `validate-docs`, `validate-portfolio-boundary` | PASS; 82 tasks, 243 dependencies, 2,761 externalized messages |
| `validate-dependencies`, `validate-pillow-runtime`, `validate-m1-benchmark-report`, `validate-pws0208-resource-workload`, `validate-pdf-renderer-benchmark`, `quality-self-test` | PASS; existing policies and deliberate negative controls retained |
| `tools/dev.py test-integration` with recorded relevant selection | PASS; 237 passed, one existing directory-link capability skip, 446 deselected; 327.71 seconds |
| `tools/dev.py test-unit` | PASS; all 7,091 tests, no skips; 822.87 seconds |
| `tools/dev.py test-security` | PASS; 1,016 passed, six existing link-capability skips; 404.47 seconds |
| `tools/dev.py test-accessibility` | PASS; all 324 tests, no skips; 27.32 seconds |
| `tools/dev.py validate-fixtures`, `scan-secrets` | PASS; 60 fixtures, 15 analytical/six transform/three overlay references; 3,516 historical blobs and 1,218 current files; fixture 278.63 seconds, secrets 245.52 seconds |
| Explicit opt-in worker regression suite | PASS; all 1,453 worker unit tests; 10.97 seconds |
| Real desktop-capability worker: cropped PDF, ZIP member, changed-byte refusal | PASS; all three serial tests; 58.21 seconds; actual terminal RPC required |
| Locked-closure privacy regression | Before fix: two locked cases failed, two ordinary cases passed. Current related unit 95, full accessibility 324, and relevant privacy 10 passed |
| Agent-operated synthetic NVDA review | PASS_SCOPED; four immediate observations, cancel then actual confirmed synthetic-case removal, retained-output hashes checked, owned processes normally exited |

The integration selection is recorded exactly in the receipt and covers package
exports, case lifecycle, post-export deletion/companions, quality entry points,
worker clients/protocol and Windows containment. It is not the entire integration
suite. Capability skips are reported, not converted to passes.

The NVDA helper used production dialogs, the secure deletion service and a
validated seeded one-source/one-page synthetic case. Its PDF was separately
reopened, page-count checked and rendered; native preparation did not claim the
full ordinary importer/exporter workflow. Saved PDF, summary, companion and
external-input hashes remained correct after removal. Receipts and screenshots
are under `output/pws0706/native/sessions/final-20261102-02/`.
The exact source-scope proof retains the desktop-composition change, and subsequent
controller/protocol changes are separately tested. The helper never instantiated
those controllers. No full desktop/NVDA, human, packaged-release or hardware
certification is inferred. The agent journey took 429.01 seconds including tool
recovery, with 34 successful and five rejected automation actions; this does not
validate the proposed 24-action/six-minute human estimate.

The quality baseline renewal is provenance-only: unchanged recorder/validator and
canonical unit checks passed in the real separate worktree index. JSON differs
only at `revision`; every other JSON field is identical. Proofs and original bytes remain under
`output/pws0706/quality-refresh-artifacts/`.

### Earlier outcomes remain historical

- Evolving-source full-unit runs remain FAIL: 33 failed / 7,046 passed / one error,
  then 27 failed / 7,060 passed. Default-capability compatibility and old index
  provenance were corrected; all final unit tests pass. The early renderer
  access-recovery refusal caused a withdrawn-stage cascade; later renderer tests
  passed unchanged. No renderer harness or ACL guard was weakened.
- The overlapping integration selection remains FAIL: nine failed / 228 passed /
  one skipped. Four failures explicitly reported `access.recovery_allowlist`;
  the first sanitized launch failure's exact cause remains unknown. The later
  serial selection passes every affected case. Per-user journal observations
  were empty; no manual ACL recovery or cleanup was used.
- Early worker fixtures lacked staged PDF engines, and an early refusal assertion
  did not establish a terminal RPC. Both test issues were corrected. A later
  concurrent native opt-in run had three launch-stage failures and is retained;
  the fresh serial three-case run passes. One new pure test initially expected a
  return rather than the specified sanitized exception; its failed assertion and
  corrected result are preserved.
- Initial crop, Unicode and stale-receipt reproductions, the two failing
  locked-summary regressions and the paused first native session remain recorded.
  Earlier failures and limited scopes are never relabelled as current passes.

## Security/privacy/accessibility notes

See [post-export retention](../architecture/post-export-retention.md). Only
synthetic data is used. Text/filenames remain user-facing content and are excluded
from routine diagnostics. External originals are not modified. A lock cannot
restore a dismissed destructive confirmation after unlock.

## Portfolio boundary and release-interface notes

Standalone PaperWorkShell owns cases, derivative exports and retention. There is
no sibling dependency, shared data/service/schema or adapter. No packaged data
location, uninstall fact or ThirdLife compatibility claim is proved here.

## Residual risks or deferrals

Actual case removal is not transactional. After partial removal, remaining case
files are unavailable for normal reopening; no automatic destructive retry occurs.
The separate verified export is the retained usable result. Plain text can omit
images/visual marks and have OCR/reading-order errors; users must review it.

## Task graph

PWS-0706 is **done**. All PWS-0707 prerequisites are done, so PWS-0707 is
**ready** and is the next dependency-ready task. No later task was started.
