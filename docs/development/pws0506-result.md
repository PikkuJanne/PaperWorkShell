# Task result — PWS-0506: conservative portal profile and compatibility matrix

**Status:** review. Final automated acceptance and exact-output reader observations are pending.

## Scope and plan

Selected PWS-0506 at `409c0ef` after confirming PWS-0501, PWS-0504 and PWS-0505
are done. Work is on `codex/pws-0506-portal-compatibility`. Unrelated untracked
maintainer files remain untouched.

Add a versioned portal profile to package export, enforce its policy in restricted
workers, preserve existing export-profile meanings, and prepare an exact-byte
synthetic reader and local upload-constraint matrix. Required manual reader evidence
includes Adobe Reader/free mode, Edge, Chrome, Firefox and a representative Android
reader. Older accepted PWS-0307 evidence stays bound to its original artifact.

## Implemented

- Added explicit portal base profile/plan v3 and report versions 3/4, preserving
  the meanings of v1/v2. Package flattened, searchable and target-size paths carry
  the same policy. Ordinary image sources retain the searchable fallback.
- Added bounded worker-side final checks for encryption, retained interactivity,
  attachments, nested metadata, external streams, optional content and malformed
  feature/type structures. Unsupported features prevent publication. Source
  version/extension checks run before copying; assembly writes PDF 1.7 without
  forcing a version downgrade. Optimization and image stages recheck the policy.
- Bound report proof to the requested profile at the worker boundary. Existing
  history/retry v1 stores its established check projection; reopening history
  does not reconstruct a transient portal claim.
- Added a synthetic three-page corpus, six PDFs and one ZIP, exact expected
  artifact hashes, 22 local upload-constraint checks, a resumable reader recorder
  and a single guided command. Reader slots require actual observations.

See [ADR 0014](../adr/0014-conservative-portal-profile.md), the
[feature policy](../architecture/portal-export-profile.md) and the
[compatibility matrix](../compatibility/pws-0506-portal-matrix.md).

## Verification

### Focused and local checks

Commands use `uv run --locked --no-sync` and the repository entry points unless
the named focused pytest selection is shown.

| Command | Observed result |
|---|---|
| `python -m pytest -q tests/unit/test_portal_pdf_policy.py tests/security/test_portal_pdf_policy_security.py tests/integration/test_portal_pdf_worker_windows.py --tb=short` | 72 passed in 58.26 seconds, including the final malformed-Type regressions and both constrained worker paths. |
| Focused profile, target-size fixture, lifecycle and protocol selection | 137 passed in 1.22 seconds. Legacy/current/future versions, crossed reports, downgrade rejection, retry projection and image fallback covered. |
| `python tools/dev.py test-security` | 721 passed; six existing local symbolic-link privilege skips; 277.51 seconds. The final five malformed-Type cases were added afterward and passed in the focused run and CI. |
| `python tools/dev.py test-accessibility` | 74 passed in 7.05 seconds. |
| `format-check`, `lint`, `typecheck` | Passed; strict typing covers 664 files. |
| `check-import-boundaries`, `validate-schemas`, `validate-dependencies`, `validate-task-graph`, `validate-docs`, `validate-portfolio-boundary` | Passed; 13 layers, 82 tasks, 243 dependencies, 37 bundled canonical files. |
| `python -m tools.pws0506_portal_compatibility prepare` | Final source binding prepared successfully; all seven hashes and 22 constraint checks match. |
| `python tools/dev.py quality-self-test` | All five deliberate violations detected, with test data cleaned up. |
| `python -m pytest -q tests/security/test_secret_scanning.py -k allowlist --tb=short` | Two tests and four subtests passed, covering both exact public-token exceptions and rejection of near misses and other credential patterns. |
| `git diff --check` | Passed at implementation checkpoints. |

The first full local unit run passed 5,518 tests and exposed 38 failures in old
capability/report fixtures and the revision-bound quality baseline. Fixtures now
advertise the additive v3 capability, reject future v4 operations, and generate
v4 optimized reports for portal plans. The unchanged quality recorder was replayed
against `96e7563af9075f082da51b4c62d6ec18de65071b`; JSON comparison confirmed that
only the recorded revision changed. All eight metrics remain identical. See
[quality provenance](scan-quality-evaluation.md).

Five additional malformed-Type regressions were observed failing before the
narrow native validation fix. The fix rejects present non-name values before
native string conversion. No resource budget or existing validation was relaxed.

### Clean Windows CI checkpoint

[Run 36967849678](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36967849678)
tested source commit `ce4868cf3bbc0f86d524420324c02fa3f5ecfc47`:

- Core passed 5,557 unit, 732 security and 74 accessibility tests, along with
  formatting, lint, typing, import, fixture, schema, task, documentation, portfolio
  and resource-evidence checks. The history secret scanner then reported a
  potential finding; this run is not a complete core acceptance result. Redacted
  diagnosis isolated it to the public `pws0506-corpus-v1` compare-and-swap token
  in the synthetic corpus generator, matched by the generic API-key rule. No
  credential was introduced. The exact rule-scoped exception passed near-miss
  and other-rule rejection tests; the new regression was observed failing before
  the exception. No path or global suppression was added and history was not rewritten.
- Integration passed 550 tests with seven existing unavailable-runtime/artifact
  skips and one failure in 5,046.27 seconds. The target-size reference baseline
  still used the legacy profile while production used the portal profile. The
  test now requests the portal profile and requires its proof; exact digest and
  size assertions remain. The corrected live test passed locally in 134.63 seconds;
  the final full rerun is pending.

### Native, visual and artifact evidence

The real constrained worker tests prove both successful policy verification and
rejection of hidden nested metadata. They check worker termination, shared gate
release, measured memory ceilings, unchanged source/import hashes, unchanged
manifest and complete case-tree cleanup. Existing package/storage tests cover
cancellation, timeout, destination collisions, low disk and retained-stage cleanup.

Final ignored workspace: `output/pws0506-portal-compatibility/a3741d06357b/`.
Its evidence SHA-256 is
`b046984e5ddee6a30704932cf9220a884e8d1b2343ee599541b793b9ee0aef52`.
The binding covers actual product source bytes, corpus helpers and `uv.lock` at
source commit `ce4868cf3bbc0f86d524420324c02fa3f5ecfc47`.

All six PDFs passed production reopen, qpdf and all-page PDFium checks. The final
seven artifact hashes match the earlier generated and visually inspected bytes.
Poppler PNG review covered all baseline pages and both compressed scan variants:
the large labels, crop/orientation, centered translucent ellipse, check/cross,
signature aspect and scan text are visible and aligned. Grayscale removes raster
color while the blue vector border remains colored. The scan's smaller print
remains visible in these synthetic samples; this does not certify general
readability. No approved golden was changed.

Separate PDFs match their baseline pages; ZIP members match exact separate bytes.
The 22 local constraint tests include actual size/file/page boundaries, invalid
mode/extension/encryption observations, safe filenames, CRC and exact members.
No real portal was contacted or accepted an upload.

## Security/privacy/accessibility notes

Untrusted PDF parsing remains worker-owned; new policy checks use bounded traversal
and existing ResourcePolicy. Routine diagnostics contain no document values. No
dependency or licence change was introduced. There is no new product UI control; the
guided developer procedure is plain text and has no review timeout.

## Portfolio boundary and release-interface notes

Standalone export usefulness and PaperWorkShell ownership of cases remain intact.
No sibling repository, service, shared schema or adapter is introduced. Release
documents retain their unproved `TBD` facts and make no new compatibility claim.

## Residual risks or deferrals

All five manual reader slots remain NOT RUN. Adobe Reader launched but showed a
Windows Font Capture application error before any corpus PDF was opened. Permission
to inspect that system dialog and a later Chrome launch timed out. No reader pass
was inferred. Edge/Firefox and Android observations remain pending; no Android device
or permitted remote test portal was supplied during implementation.

The single guided command in the compatibility matrix prepares or resumes the
same exact artifacts and records only unfinished slots. No repeat of accepted
PWS-0307 evidence is requested. Source-font substitution, unsupported static
features and recipient-specific rules remain explicit limitations. Native policy
failures keep the existing sanitized worker error; recovery guidance is documented.

## Task graph

PWS-0506 is in review while final automated acceptance and the named reader
observations remain pending. PWS-0507 remains planned. There is no
dependency-ready task in the current milestone while PWS-0506 awaits acceptance.

## Changed files

| Files | Purpose |
|---|---|
| `src/paperworkshell/domain/pdf_export.py`, `searchable_pdf_export.py`, `pdf_export_history.py`, `pdf_export_retry.py` | Versioned profile/report contracts and unchanged persisted history semantics |
| `src/paperworkshell/app/package_pdf_export.py`, `pdf_export.py`, `target_size_pdf_export.py`, `src/paperworkshell/overlays/exporting.py` | Package profile selection, rebuilt-plan preservation and exact proof binding |
| `src/paperworkshell/pdf/portal_policy.py`, `pikepdf_adapter.py`, `export_adapter.py`, `flattened_export_adapter.py`, `searchable_export_adapter.py`, `optimization_adapter.py`, `image_stage_adapter.py` | Bounded native policy and re-verification after every applicable rewrite |
| `src/paperworkshell/worker/desktop_composition.py`, `host.py`, `operations.py`, `pdf_export_broker.py`, `pdf_export_operation.py`, `pdf_export_plan.py`, `pdf_export_report.py`, `pdf_image_stage_broker.py`, `pdf_optimization.py`, `searchable_pdf_export_broker.py`, `searchable_pdf_export_plan.py`, `protocol/codec.py`, `protocol/models.py`, `protocol/state.py` | Additive operation v3 and validated transport/report ownership |
| `tests/unit/test_portal_pdf_policy.py`, `test_portal_pdf_contracts.py`, `tests/security/test_portal_pdf_policy_security.py`, `tests/integration/test_portal_pdf_worker_windows.py` | Success, hostile-input, version, downgrade, cancellation, limits and real-worker regressions |
| `tests/unit/test_searchable_export_adapter.py`, `test_target_size_pdf_export.py`, `test_validate_worker_rpc.py`, `tests/unit/worker/test_client_lifecycle.py`, `test_pdf_export_composition.py`, `test_pdf_inspect_protocol.py`, `test_pdf_thumbnail_protocol.py`, `tests/integration/test_target_size_worker_windows.py` | Extend existing fixtures to the new exact profile/capability contract |
| `tools/pws0506_portal_compatibility.py`, `tools/dev.py`, `tests/unit/test_pws0506_portal_compatibility.py` | Exact synthetic corpus, local upload checks, stable guided procedure and evidence validation |
| `tools/validate_worker_rpc.py`, `docs/schemas/worker-rpc-v1.md` | Document and validate additive v3 while preserving RPC envelope v1 |
| `.gitleaks.toml`, `tests/security/test_secret_scanning.py` | Exact public corpus-token exception with near-miss and other-rule rejection regressions |
| `docs/adr/0014-conservative-portal-profile.md`, `docs/architecture/portal-export-profile.md`, `docs/architecture/pdf-export.md`, `docs/compatibility/pws-0506-portal-matrix.md`, `README.md`, `KNOWN_LIMITATIONS.md` | Policy, recovery, compatibility procedure and claim boundaries |
| `benchmarks/pws0207-quality-findings-v1.json`, `docs/development/scan-quality-evaluation.md` | Unchanged quality metrics rebound to changed worker composition/protocol surfaces |
| `TASKS.yaml`, `BUNDLE_MANIFEST.json`, this report | Task state, exact canonical hashes and verification evidence |
