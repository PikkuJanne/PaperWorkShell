# PWS-0804 — case migration and release-to-release recovery

**Status: done.** Implementation, focused verification, independent review and
shared source-quality closure are complete; packaged release qualification remains separate.
Dependencies PWS-0801 and PWS-0109 are done. This work proceeds independently of
PWS-0802's pending production-signing and runtime-licence actions, as explicitly
directed by the maintainer.

## Implemented

- Closed, immutable v1-to-v2-to-v3 migration registry and copy-only current-version
  runner, reusing accepted transforms and the existing single-revision durable
  Windows transaction. No schema meaning or version changed.
- Bounded create-only retention of exact pre-migration source-manifest bytes in
  protected journal storage. Existing exact backups are reused; different or
  unsafe objects fail closed without replacement.
- A six-case, hash-bound synthetic compatibility index reusing accepted schema
  examples; dedicated pure/native/security checks and an explicit
  [migration/backup/rollback contract](../architecture/case-migration.md).
- Explicit unsupported arbitrary external workspace/project opening and transfer;
  the normal visible managed-case reopen path remains available. No cross-product
  interpretation, adapter, service or shared data is introduced.

## Changed files

- `src/paperworkshell/domain/manifest_migrations.py`: closed route and one-revision
  runner over existing accepted transforms.
- `src/paperworkshell/storage/manifest_repository.py`: use the route and preserve
  exact validated source bytes before the first migration checkpoint.
- `src/paperworkshell/storage/manifest_migration_backup.py`: narrow authenticated,
  quota-bound, create-only backup adapter.
- `tests/helpers/release_migration.py`, `tests/unit/test_release_manifest_migrations.py`,
  `tests/integration/test_release_case_migration_windows.py`,
  `tests/security/test_release_case_migration_security.py`, and
  `tests/fixtures/manifests/release-compatibility-v1.json`: synthetic source,
  preservation, version, failure, resource and privacy evidence.
- `docs/architecture/case-migration.md` and this result: ownership, retention,
  recovery, unsupported downgrade/transfer and precise evidence limits.

## Verification

- Canonical `test-unit` with process-local selection `release_manifest_migrations
  or test_manifest_migrations or import_boundaries` — PASS, 146 passed and
  7,394 deselected, 17.97 seconds. This includes the closed registry, all six
  complete schema examples, current idempotence, exact fixture hashes and external
  project-path refusal before storage.
- Canonical `test-integration` selecting `release_case_migration or
  manifest_v2_migration or manifest_repository_windows` — PASS, 32 passed and
  673 deselected, 11.44 seconds. Normal/constrained resource policy, source and
  backup preservation, old/current versions, checkpoint interruption, disk-full,
  low-space refusal, cancellation and unchanged historical persistence tests.
- Canonical `test-security` selecting `release_case_migration or
  manifest_repository_security` — PASS, 17 passed and 1,179 deselected,
  4.97 seconds; future/corrupt state, changed/directory/hardlink backup refusal,
  post-rename interruption and content-log absence.
- Canonical `lint`, `typecheck`, `format-check`, `validate-docs`,
  `validate-schemas` and `validate-task-graph` — PASS at their recorded source
  epochs. Typing covered 885 source files; the later format check covered 889.
- Full canonical `test-unit` — FAIL, 7,536 passed, two skipped and two failed in
  998.02 seconds. The failed nodes are
  `test_pws0207_quality_evaluation.py::test_checked_in_baseline_is_canonical_and_matches_current_corpus`
  (recorded implementation-index mismatch) and
  `test_validate_portfolio_boundary.py::PortfolioBoundaryTests::test_checked_in_boundary_passes`
  (bundle canonical-file hash and byte-count mismatch). No benchmark or accepted
  evidence was changed to hide these failures. Both synthetic-symlink skips
  report the host's missing symlink-creation permission.
- The before/after full-unit observations and comparison are retained under
  ignored `output/pws0804/`: `source-epoch-before-unit.json`,
  `source-epoch-after-unit.json`, `full-unit-epoch-comparison.json` and
  `test-unit-full.txt`. The log SHA-256 is
  `3dbf3239dacde94e5e6bbef571c72724f1b1fe836b0fa9bbbf23178679d79f5f`.
  Git HEAD and index stayed unchanged. All nine PWS-0804 production, test,
  fixture and architecture files stayed byte-identical between observations;
  seven other/documentation paths changed and six installer-related paths were
  added while shared work continued. This is an observed source epoch, not a
  frozen final installer-candidate or final shared-tree qualification.
- Canonical `validate-fixtures` — FAIL CLOSED with
  `FIXTURE_REPOSITORY_SCAN_FAILED` / `SECRET_SCAN_INPUT_INVALID` during shared
  repository edits. No content finding or privacy pass is inferred. A final
  unchanged-source scan remains required; scan policy, exclusions and resource
  budgets were not relaxed.

Early native attempts caught unsupported use of a directory-only ACL function
on file handles and a Windows writer/reopen sharing conflict in the new backup
adapter. Both were corrected without changing the existing directory ACL guard,
permissions or accepted tests. These failures are not relabelled passes. No
worker test or human observation was used for these results.

### Independent review correction after the full-unit epoch

An independent review reproduced an attribute-only journal-handle race: renaming
the empty journal outside the authenticated case during the stage-write cut
point could publish the exact retained source bytes there. The new three-target
regression ran before correction: journal and temporary-directory renames both
escaped, while the case rename was blocked (two failed, one passed). Its raw
receipt is `output/pws0804/test-security-rename-before-fix.txt`.

The adapter now holds full traversal pins on case, journal and temporary
directories for the complete backup operation, verifies their expected identities,
and preserves restrictive directory ACL and alternate-stream checks. The
regression explicitly forbids the exact source-manifest bytes outside the case,
requires each attempted rename to be blocked, and verifies successful migration
and exact local backup afterward. The focused canonical security lane now passes
20 tests with 1,220 deselected in 5.88 seconds; its new receipt is
`output/pws0804/test-security-after-directory-pin.txt`. This is a later source
delta: the earlier full-unit result remains historical and does not qualify the
corrected adapter. Final shared QA remains coordinator-owned.

The corrected adapter's canonical focused integration lane passes 32 tests with
690 deselected in 11.88 seconds. Independent replay then confirmed the journal
rename was denied with Windows sharing error 32, the outside directory was absent,
migration reached version 3 and the exact local source backup remained. No
further blocking defect was found in that independent route/future-dispatch/CAS/
quota/forward-recovery review. These results do not overwrite the earlier raw
failures or full-unit source epoch.

## Security, privacy, accessibility and resource notes

No document parsing or immutable source rewrite occurs. Complete manifests and
backups stay private and never enter routine logs. Future/invalid records fail
before migration writes; unknown backup objects are retained. Existing handle,
identity, stream and reparse/hardlink checks are preserved. At most two backups
exist, each under the existing 8 MiB cap and complete-case quota. No cache, queue,
worker process or budget grows. No new user control needs human accessibility
testing. Current success/failure messages use the existing safe error registry.

## Portfolio and release-interface impact

PaperWorkShell exclusively owns its private case schemas, backups and migration.
Standalone use, independent versions and ordinary document import/export remain
complete without ThirdLife. No sibling data access or compatibility claim is
created. Exact packaged data-location and matrix evidence stays with the existing
M8 release owners; this is source/native-storage evidence, not a packaged-release
or physical-hardware qualification.

## Residual boundaries and task graph

Automatic downgrade/old-backup restoration is intentionally unsupported to avoid
discarding later intent. The tested failure path retains the old state before
checkpoint commit and uses exact forward recovery afterward. Public portable
workspace/project transfer remains unsupported. No accepted fixture/golden,
schema, original, root licence, installer, catalog, Git index/HEAD/ref or sibling
repository was changed. The coordinator owns TASKS.yaml and bundle updates;
PWS-0803 still waits for PWS-0802. The shared closure below records the final task state.

## Coordinator closure — 2026-11-03

The full canonical unit lane in an isolated copy of the **current local source**
passed 7,648 tests with two disclosed host-symlink capability skips in 1,034.87
seconds. Checkpoint `338013a97781991dab8f37c1db521f36f42589f8` has an independent
index/object store; the original root HEAD/index/refs and quality corpus/metrics
remain unchanged. Only the QA copy's benchmark revision binding changed. This
does not overwrite the original-root two-failure observation recorded above.

All 319 application source files, including this migration implementation, remain
identical after that full run. Later installer-only source changes passed their
separate 250-test unit selection (one disclosed symlink skip and 122 subtests),
79 affected security tests and 31 affected native integration tests. The migration
feature's earlier 146 focused unit, 32 native and 20 security passes remain bound
to their own source epochs; counts are not combined into an invented full suite.

Canonical format/lint/strict typing (918 files), import boundaries, schemas,
task/docs/portfolio/dependency/localization and resource/benchmark validators pass.
All 346 accessibility tests pass. The final repository privacy/fixture scan and
all-ref secret scan pass: 3,524 historical blobs, unchanged 1,012 index blobs and
2,112 working files. New-source installer privacy/build-path failures and their
regressions are retained in the PWS-0802 report; they do not alter migration data.

Task PWS-0804 is done. PWS-0805 is independently done in the same shared closure.
No further dependency-ready task exists while PWS-0802 is blocked on its actual
release requirements. PWS-0803 remains planned. No sibling data, shared runtime,
public project-transfer interface, released schema meaning or accepted golden was changed.
