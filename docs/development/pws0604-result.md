# Task result — PWS-0604: Implement OCR regeneration with redaction-region exclusion

**Status:** done

## Implemented

- Added an independently scoped private OCR stage that reconstructs the selected
  immutable source page before recognizing anything. Only the authenticated,
  image-only, opaque-masked reconstruction reaches OCRmyPDF/Tesseract.
- Added the versioned `redacted-page-ocr-v1` profile, exact request fingerprint
  and correlated content-free report. Closed English, German and mixed selections
  use the existing admitted engines and language files.
- Reopen, image digest, geometry and rendered-pixel checks require OCR to preserve
  the sanitized appearance. Final character and word bounds must not intersect
  the exact transformed mask. An ambiguous or failed check discards the candidate.
- OCRmyPDF's incremental text graft is fully rewritten as a fresh private PDF
  before strict revision checks. Only the already-masked predecessor is read;
  no original source revision is imported, and at most two PDF candidates coexist.
- Preserved the restricted worker's bounded handles, network denial, memory,
  temporary storage, single heavy-operation gate, cancellation and cleanup.
  Missing language data has stable sanitized remediation and no fallback output.
- Kept all candidates unsigned and `awaiting_verification`. No mark promotion,
  final export, export-history mutation or release enablement is introduced.

The [profile contract](../architecture/redaction-ocr.md) records the supported
input-mask strategy, geometry, resource accounting, failure behavior and limits.

## Changed files

| Paths | Purpose |
|---|---|
| `src/paperworkshell/domain/redaction_ocr.py` | Closed versioned request/report and fingerprint |
| `src/paperworkshell/domain/pdf_export.py` | Stable missing-language remediation |
| `src/paperworkshell/pdf/redaction_ocr.py` | Masked-only OCR, image/geometry/render authentication, text exclusion and cleanup |
| `src/paperworkshell/worker/redaction_ocr_broker.py`, `redaction_ocr_composition.py`, `redaction_ocr_operation.py`, `redaction_ocr_wire.py` | Private retained-source transport, runtime admission and bounded candidate lifecycle |
| `src/paperworkshell/worker/protocol/redaction_ocr.py`; shared protocol models/codec/scope and host | Additive private capability, closed message validation and scoped dispatch |
| `tests/unit/test_redaction_ocr_models.py`, `test_redaction_ocr_adapter.py`, `tests/unit/worker/test_redaction_ocr_worker.py` | Invalid versions, bindings, authority, exclusion, failure and recovery |
| `tests/security/test_redaction_ocr_security.py`, `tests/integration/test_redaction_ocr_worker_windows.py` | Adversarial OCR, native search controls, constrained worker and lifecycle evidence |
| `docs/architecture/redaction-ocr.md`, `redaction-reconstruction.md`; `docs/schemas/worker-rpc-v1.md`; `docs/security/security-test-plan.md` | Profile, remaining gates and verification scope |
| `README.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json`, this report | Capability/status/evidence and canonical bundle hashes |
| `benchmarks/pws0207-quality-findings-v1.json`, `docs/development/scan-quality-evaluation.md` | Unchanged quality measurements rebound to the additive worker registry |

Inherited working changes and unrelated local files are preserved. No dependency,
source fixture, golden, resource-policy budget, frozen decision or released schema
meaning is changed.

## Verification

Verification uses an owned accepted-file snapshot under
`output/pws0604/verification`, preserving the original working location's existing
ownership/sharing behavior. The source checkpoints are
`298d2de0eb48f29710244ef0eecc784257985f75` and
`f59169b364a25380e2bfa3e7f8e477f63289870c`. The latter changes only the
PDF adapter's type/import boundary and the documented geometry limitation; the
worker's concrete admitted engine injection and processing behavior are unchanged.
The snapshot contains 1054 accepted files, with unrelated local documents and
`tmp/` excluded. Synthetic temporary data and pinned runtime staging stay ignored.

| Command / selection | Result |
|---|---|
| `tools/dev.py test-unit` | 5853 passed, zero skips, 828.29 seconds |
| Focused model/adapter/worker/protocol/import/security selection after the boundary correction | 247 passed and 48 subtests, 8.76 seconds |
| `pytest -q tests/integration/test_redaction_ocr_worker_windows.py` | Nine passed, zero skips, 753.78 seconds; eight native language/geometry/scan/quality cases and the real-child lifecycle/recovery case |
| Related integration selection and isolated retry | 40 initially passed; all eight cases blocked by concurrent recovery guard passed serially in 382.54 seconds, with zero skips |
| `tools/dev.py test-security`, isolated final run | 783 passed and six existing Windows link-capability skips, 369.49 seconds; every new test passed without a skip |
| `tools/dev.py test-accessibility` | 101 passed, zero skips, 10.02 seconds |
| `tools/dev.py format-check`, `lint`, `typecheck`, `check-import-boundaries` | Pass after final source correction; 706 Python files, 13 layers and 247 production files |
| `tools/dev.py validate-schemas`, `validate-task-graph`, `validate-docs`, `validate-portfolio-boundary` | Pass; frozen/current/future/invalid schemas, 82 tasks, 243 dependencies and 47 canonical bundle files |
| `tools/dev.py validate-dependencies`, `validate-pillow-runtime`, `validate-m1-benchmark-report`, `validate-pws0208-resource-workload`, `validate-pdf-renderer-benchmark`, `quality-self-test` | Pass; all five deliberate quality violations detected |
| Unchanged PWS-0207 evaluator `record` and `validate`, plus its 40 unit tests | Pass; JSON comparison proves only the implementation revision changed |
| `tools/dev.py validate-fixtures` | Pass; 60 sources, 15 analytical, six transform and three overlay references, 3110 historical blobs, 999 index blobs and 1054 working files |
| `tools/dev.py scan-secrets` | Pass; 3110 historical blobs, 316 commit/tag objects, 3147 path/blob pairs, 999 index blobs and all 1054 accepted working files |

The full unit run precedes the type-only boundary
correction; its affected surfaces were then exercised by the 247-case selection
and strict typing, with the final security/native reruns using the corrected source.

The related integration selection covers host protocol, private temporary access,
ordinary OCR and native engine admission, PDFium rendering, reconstruction,
ordinary searchable export, malformed RPC, timeout, forced termination, fresh
generation/resource snapshots and the final German cropped OCR scenario. Logs
are in `output/pws0604/integration-regression.log` and
`output/pws0604/integration-serial.log`. Replay the related selection with
`python -m pytest` over:

- `tests/integration/test_worker_host_runtime.py`
- `tests/integration/test_worker_windows_temp_access.py`
- `tests/integration/test_ocr_worker_windows.py`
- `tests/integration/test_ocr_admission_appcontainer_windows.py`
- `tests/integration/test_redaction_reconstruction_worker_windows.py`
- `tests/integration/test_searchable_pdf_export_worker_windows.py`
- `tests/integration/test_pdfium_renderer.py`
- `tests/integration/test_worker_windows_containment.py::test_actual_python_host_malformed_rpc_fails_closed_with_protocol_error`
- `tests/integration/test_worker_windows_containment.py::test_actual_python_host_timeout_requires_restart_and_exits`
- `tests/integration/test_worker_windows_containment.py::test_force_termination_ends_the_retained_job_tree`
- `tests/integration/test_worker_windows_containment.py::test_fresh_launch_after_termination_uses_new_binding_and_negotiates`
- `tests/integration/test_worker_windows_containment.py::test_live_worker_resource_snapshot_reports_applied_limits_and_closes_cleanly`
- `tests/integration/test_redaction_ocr_worker_windows.py::test_native_regeneration_excludes_canaries_and_preserves_searchable_visible_controls[german-digital-90-crop]`

Run live Windows suites serially. The eight-case retry uses reconstruction,
searchable export, the five containment nodes and the German OCR node above.

The verification-input inventory is `output/pws0604/verification-input-files.json`
(SHA-256 `60c7abc4c601377c4d6341fab5caa06b28c2cdb7ff24c0e3613f08ddf356bef9`).
An independent SHA-256 audit found all 891 selected source/test/schema/template/
benchmark/configuration files identical in the working and owned verification
locations, and all 217 protected source-fixture/golden/schema/template/dependency/
policy/frozen-contract files unchanged from the previous task's delivery inventory.
The receipt is `output/pws0604/source-verification.json`.

Metadata closure updates only this report, task state/evidence and canonical
bundle hashes/readiness. The schema, graph, documentation, portfolio and dependency
checks are repeated for that closure. The final accepted-file inventory is
`output/pws0604/snapshot-delivery-files.json`; processing source, tests, fixtures,
dependencies and policies remain unchanged.

Pinned native provisioning verified the exact keyring and all 31 package/signature
size/hash records, with GPG `GOODSIG` and `VALIDSIG` for the required fingerprint
before selected-member extraction. The admitted stage contains 40 exact files
(base closure plus German); native admission reports Tesseract 5.5.3 and English/German.
The acquisition receipt is `output/pws0604/tesseract-acquisition-receipt.json`.
OCRmyPDF remains the locked 17.10.0 dependency. No dependency or language-data
version was changed. The synthetic preview was visually inspected; native checks
prove that the final fresh OCR candidate renders identically to its reconstruction.

### Findings resolved during verification

- Actual OCRmyPDF output contains an incremental text graft. A bounded fresh
  full-save removes that generated history before the unchanged strict revision
  check. A regression proves source and hidden OCR canaries remain absent.
- A private-directory collision could otherwise clean an unowned raced-in tree.
  Ownership now transfers only after successful exclusive directory creation;
  the regression preserves the preexisting directory and its contents.
- The full security suite caught a concrete OCR adapter import from the PDF
  layer. A narrow typed protocol replaces it; the existing layering regression
  failed before the fix and passed afterward. No allowlist or security check was
  weakened.
- Concurrent Windows security/integration sessions intentionally encounter the
  global ACL recovery allowlist guard because their retained runtime roots differ.
  The journal was inspected read-only and found empty after normal cleanup.
  All eight blocked integrations and the full security suite passed serially;
  no journal was deleted and no
  allowlist was broadened.
- A German synthetic retained-text control initially exceeded its page width.
  It was shortened to a comfortably fitting, unambiguous target. Source fixtures
  and approved goldens were not changed.

## Security/privacy/accessibility notes

The covered source samples are replaced before recognition. Image identity and
independent render checks prevent a blank or altered appearance from passing.
Search tests require positive recognition controls and retained text. The output
is a private intermediate, not a verified redacted export. Reports and routine
errors carry only approved identities, digests, counts, versions and codes.
Originals and manifests remain unchanged. This worker-only change adds no UI;
existing accessible intent review and export blockers remain in force.

## Portfolio boundary and release-interface notes

Standalone PaperWorkShell owns source content, cases, OCR, migrations and final
publication authority. There is no sibling dependency, adapter, shared runtime
schema or service. Draft packaged-release facts remain unchanged and unproved.

## Residual risks or deferrals

Complete derivative sanitization, independent multi-method verification, final
export/report authority and independent release approval remain PWS-0605 through
PWS-0608. OCR does not guarantee recognition accuracy, PDF/UA conformance, digital
signature validity, secure erasure or portal acceptance.

The inherited [PWS-0603 reconstruction](pws0603-result.md) exact-render check can
reject some fractional physical crop sizes before OCR. This task preserves that
fail-closed behavior; successful native crop evidence uses the documented synthetic
384 by 288 point pages. It does not claim that every fractional geometry is accepted.

## Task graph

- PWS-0604 is done after verification and evidence closure.
- Next dependency-ready task: PWS-0605.
