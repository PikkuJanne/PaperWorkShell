# Task result — PWS-0605: Implement redaction derivative sanitization

**Status:** done

## Implemented

- Added the private versioned `redaction-sanitization-v1` profile and an isolated
  single-worker operation that creates a fresh catalog, page tree and PDF rewrite.
- Excluded attachments, annotations/comments, actions/scripts, form fields and
  values, document metadata, signatures, orphan objects and prior revisions.
  Sanitized candidates always remain unsigned and `awaiting_verification`.
- Retained supported page content and regenerated searchable OCR. Closed rendering
  dictionaries, bounded decoded image samples, canonical Unicode CMaps and
  approved Noto TrueType metadata prevent opaque metadata copying. Unsupported
  structures fail closed; see the [profile](../architecture/redaction-sanitization.md).
- Added authenticated source/request/report/output binding, reduced handle rights,
  one outstanding candidate, cancellation, timeout, resource limits and cleanup.
  A terminal-result/cancellation-ack race now waits within the existing client grace.
- Encoded each validated image before copying the next page, so the retained PDF
  holds compressed streams and only the current image has transient decoded samples.
  A two-page test inspects that representation before saving; encoding failures and
  cancellation preserve the source and leave no candidate. Resource budgets are unchanged.
- Canonicalized page/Form content through a bounded streaming token filter. It
  removes lexical comments and rejects inline images, marked-content metadata,
  unsupported operators, malformed operands and invalid text-object boundaries.
  Independent probes reproduced the original carriers and confirmed their rejection
  or removal, while preserving literal text and the generated OCR appearance.
- Reused pinned fonttools 4.63.0 through one exact adapter import. Its inventory and
  executable dependency policy now require hostile-input isolation, adapter and
  contract tests. No dependency version, native engine or lockfile changed.

## Changed files

| Paths | Purpose |
|---|---|
| `src/paperworkshell/domain/redaction_sanitization.py` | Closed request, count-only inventory, bound report |
| `src/paperworkshell/pdf/redaction_sanitization.py` | Fresh allowlisted rewrite and bounded program/image normalization |
| `src/paperworkshell/worker/redaction_sanitization_{broker,composition,operation,wire}.py` | Private handle-only transport and candidate lifecycle |
| `src/paperworkshell/worker/protocol/redaction_sanitization.py`; shared host, models, codec, state and scope | Explicit capability, strict authority and correlation |
| New sanitizer model/adapter/worker unit tests, security tests and native integration tests | Success, malformed input/results, hostile carriers, cleanup and constrained lifecycle |
| `tools/check_import_boundaries.py`, `tools/validate_dependencies.py`; corresponding unit tests | Exact fontTools admission and fail-closed dependency classification |
| `packaging/dependency-inventory-v1.json`; architecture/dependency/RPC/security documentation | Truthful untrusted-font boundary and profile limits |
| `README.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json`, this report | Status and evidence closure |
| `benchmarks/pws0207-quality-findings-v1.json`, `docs/development/scan-quality-evaluation.md` | Unchanged quality measurements rebound to the additive worker registry |

Inherited M5/M6 working changes and unrelated local files are preserved. The source
checkpoints for this task are `9d382b2` (complete initial implementation) and
`de814ed` (the reviewed image-retention correction), followed by `2e70ec0`
(the reviewed content-token correction). Verification uses an owned accepted-file copy
under `output/pws0605/verification`, preserving the original checkout's existing
ownership/sharing behavior. No sibling checkout or private fixture is required.

## Verification

The full repository run uses the authenticated `9d382b2` snapshot. The final narrow
image-retention and content-token corrections are separately covered by the
following focused command:

```text
uv run --locked --no-sync python -m pytest -q tests/unit/test_redaction_sanitization_models.py tests/unit/test_redaction_sanitization_adapter.py tests/unit/worker/test_redaction_sanitization_worker.py tests/security/test_redaction_sanitization.py tests/unit/test_import_boundaries.py
```

It passed all 268 tests in 8.96 seconds. The complete broad run and the final native
rerun both passed. Broad evidence uses the initial checkpoint; both reviewed adapter
corrections use final focused tests and the actual constrained worker pipeline.

| Command / evidence | Result |
|---|---|
| `uv run --locked --no-sync python tools/dev.py check` at `9d382b2` | Pass; every registered check, including both repository scanners and five deliberate quality self-tests |
| `tools/dev.py test-unit`, within that run | 5930 passed, zero skips; 649.13 seconds |
| `tools/dev.py test-integration`, within that run | 568 passed, six existing skips; 5431.66 seconds; all nine OCR redaction cases, reconstruction and both sanitizer cases passed |
| `tools/dev.py test-security`, within that run | 808 passed, six existing skips; 364.83 seconds |
| `tools/dev.py test-accessibility`, within that run | 101 passed, zero skips; 7.98 seconds |
| Final focused command above at `2e70ec0` | 268 passed; 8.96 seconds |
| `python -m pytest -q tests/integration/test_redaction_sanitization_worker_windows.py` at `2e70ec0` | Two passed, zero skips; 167.82 seconds |
| Final `tools/dev.py format-check`, `lint`, `typecheck`, `check-import-boundaries` | Pass; 718 Python files, 254 production sources, 13 layers |
| Final `tools/dev.py validate-fixtures` | Pass; 60 sources, 15 analytical, six transform and three overlay references; 3143 historical blobs, 1012 index blobs, 1068 accepted files |
| Final `tools/dev.py scan-secrets` | Pass; 3143 historical blobs, 319 commit/tag objects, 3180 historical path/blob pairs, 1012 index blobs, 1043 tracked and 25 accepted untracked files |
| `tools/dev.py validate-dependencies` | Pass separately; one complete inventory and four rejected examples |
| Schema, task-graph, documentation and portfolio validators; `git diff --check` | Pass, with closure metadata checked after the status update |

The six integration skips are three existing Windows link-capability checks,
two PWS-0208 private artifact-stage checks and one exact PWS-0209 environment
check. The six security skips are existing Windows link-capability checks. No
new sanitizer or native OCR case was skipped.

The final native command uses the existing admitted runtime:

```powershell
$env:PWS_TESSERACT_EXECUTABLE='C:\projects\PaperWorkShell\output\pws0604\tesseract\bin\tesseract.exe'
$env:PWS_TESSDATA_ROOT='C:\projects\PaperWorkShell\output\pws0604\tesseract\share\tessdata'
uv run --locked --no-sync python -m pytest -q tests/integration/test_redaction_sanitization_worker_windows.py
```

Live Windows worker tests ran serially. The broad run spent substantial time in
existing resource-grant/recovery passes; content-free progress probes confirmed
forward progress. No recovery journal was deleted, allowlist widened, ownership
changed or resource budget raised. The final read-only fixture and secret scans
shared the frozen owned copy after native execution had completed.

The first owned-copy run exposed two verification setup issues: its optional,
already-locked renderer-evaluation group was absent, and the bundle readiness
summary still listed the task after it entered review. Installing that existing
group with `uv sync --locked --group renderer-evaluation` and refreshing the summary
resolved both. The setup retry passed 39 tests and 359 subtests, including the live
renderer cases. Package pins and lockfile bytes were preserved; the incomplete
first run is not completion evidence.

The unchanged PWS-0207 quality evaluator rebound its evidence to `9d382b2` because
the additive worker registry is part of its implementation identity. A JSON
comparison proved that only the revision changed, with every measurement, digest
and resource field preserved. The subsequent adapter corrections do not
change any of those evaluated implementation surfaces.

The reviewed synthetic OCR sample retained identical extracted text and rendered
pixels after rewriting. Its metadata and revision-marker inventory became empty.
The native pipeline separately checks admitted OCR generation, sanitization,
searchability, canary absence, image appearance and retained-source immutability.

The broad verification inventory is `output/pws0605/verification-input-files.json`
(SHA-256 `3af86a00aece47c3fa4ccb0bf878b0151917f2f6fa2044ec0fa25369f3d2be2c`).
All 1068 files remained unchanged through that run. The final verification inventory
is `output/pws0605/final-verification-input-files.json`
(SHA-256 `acd5af436926513187b6b8a6e7226559d4776faa54e8d338f4fd34e463ad3173`).
All 1068 final inputs matched the working and owned locations after final tests
and scans. All 26 task checkpoint files also matched their Git blobs byte for byte.
The delivery inventory and audit are in `output/pws0605/snapshot-delivery-files.json`
and `output/pws0605/delivery-verification.json`; later changes close only this report,
task evidence/readiness and bundle hashes. All 212 protected files and 1035 unrelated
accepted files match the prior accepted delivery.

## Security/privacy/accessibility notes

Reports contain only internal IDs, versions, digests, counts and closed codes.
Removed values, paths, filenames, document text, OCR text and raw font/library
diagnostics are excluded. Input, manifest and existing output paths are preserved.
This worker-only change adds no UI; existing accessible review and all ordinary
and alternate export blockers remain active. Resource evidence proves bounded
software behavior, not certification of any physical hardware.

## Portfolio boundary and release-interface notes

PaperWorkShell remains independently useful and owns its private documents, cases,
OCR and publication authority. No sibling dependency, adapter, shared runtime
schema or service is introduced. Draft packaged-release facts remain unchanged.

## Residual risks or deferrals

The initial profile deliberately rejects unsupported complex fonts, JPEG/ICC image
payloads, inline images, marked-content operators, text objects split across streams,
optional content and ambiguous rendering structures. Sanitization alone
does not remove page text or pixels: affected pages must first be reconstructed.
All final assembled derivatives, including unaffected pages, require sanitization
and independent whole-file verification. PWS-0606 owns multi-method verification,
PWS-0607 publication/report authority, and PWS-0608 independent release approval.
Permanent-removal export remains disabled. No universal redaction, OCR accuracy,
digital-signature validity, forensic-erasure, PDF/UA or portal-acceptance claim follows.

## Task graph

- Updated PWS-0605 to `done` with command, source and preservation evidence.
- Next ready task: **PWS-0606 — Implement multi-method redaction verification suite**.
