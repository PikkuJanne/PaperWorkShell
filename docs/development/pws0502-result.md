# Task result — PWS-0502: Safe object/stream optimization baseline

**Status: `done`.** Implementation is committed and pushed as
`9b7514830a8a89329cc994b08d42360fb8980d37`. Both Windows CI jobs passed on that revision, and the
run conclusion is successful. The evidence below preserves the seven capability skips and the
separate local platform failures. Final metadata checks for the documentation/task closure are
listed separately; no packaged release or human acceptance is claimed.

## Plan

The task depends on completed PWS-0501 inspection and PWS-0112 verified export. The selected
implementation adds one fixed, conservative optimization attempt after the existing derivative
has passed its baseline checks. It reuses the admitted pikepdf/qpdf and PDFium adapters, the
restricted worker, central resource limits, and existing destination publication path.

The intended proof is a semantic and visual comparison of baseline and candidate, strict reopen
and structural checks, measured size selection, and failure tests showing that an ineffective or
invalid candidate cannot replace a good output. Focused model, wire, history, package, privacy,
and constrained-worker tests cover the boundaries around that operation. No dependency upgrade,
image re-encoding policy, or persistent schema migration is required.

## Implemented

### Fixed profile and unused-object policy

`safe_object_stream_v1` compresses unfiltered streams losslessly and generates object streams.
The writer disables content normalization, linearization, metadata-version rewriting, generalized
stream decoding, and Flate recompression. It does not downsample, change image quality or color,
rasterize text/vector pages, remove reachable metadata or attachments, or implement permanent
redaction. Existing source-feature policy during baseline assembly remains applicable.

Every trailer-reachable object and resource entry is retained, including resources not invoked
by a page content stream. The writer may omit unreachable objects. Storage-only cross-reference
details and file identifiers may change; they are excluded from the semantic comparison. This
policy does not claim secure deletion or removal of sensitive information.

The complete policy is in [Safe PDF object and stream optimization](../architecture/pdf-safe-optimization.md).

### Verification and selection

The PDF/image, flattened-overlay, and searchable export worker paths call the same optimizer
after baseline verification, within their existing restricted operation. A candidate is written
to a separate private file capped at baseline size minus one. Only a strictly smaller candidate
can be selected, after all of these checks:

- Strict reopen, expected page count, unchanged unencrypted output policy, bounded object count,
  and syntax checks.
- Canonical comparison of the complete trailer-reachable graph. Text/font structures, ordered
  content, overlays, metadata, annotations, attachments, and shared/cyclic references contribute
  to this comparison. Unfiltered and single Flate streams without decode parameters are compared
  by decoded bytes; other filters and encoded bytes remain exact.
- The existing fixed qpdf structural check without recovery or warnings.
- Sequential PDFium rendering and exact raster/geometry comparison of every baseline/candidate
  page under the existing bounded verification profile.
- Final size and hash checks for both baseline and candidate.

An ineffective rewrite retains the exact verified baseline and reports `no_reduction`. Insufficient
remaining candidate capacity reports `skipped_budget`. An invalid smaller candidate, parser fault,
disk failure, cancellation, timeout, or cleanup failure fails export. The baseline and imported
originals remain unchanged. The selected bytes then use the existing bounded transfer, broker
validation after worker termination, and destination reopen/no-replace publication.

### Resource and authority handling

The worker authenticates baseline size/hash and plain-file identity before and after the attempt.
It inventories private operation extents under object/depth bounds and rejects links, reparse
points, and non-regular files. Existing sources, baseline, remaining scratch, transferred output
capacity, and caller-owned raster/status sections count against the operation's temporary quota.
The complete possible candidate extent must fit before native writing starts, and the existing
free-space reserve remains enforced.

The semantic byte allowance is at most one quarter of the operation memory budget. Incremental
Flate decoding bounds each chunk and rejects expansion beyond that allowance, using one sentinel
byte to detect overflow. Existing worker
memory/CPU limits and deadlines still cover native allocations. Rendering remains sequential;
the task introduces no extra worker, queue, cache, concurrency, or resource-policy increase.

Read-only review found that a negative remaining quota was initially treated as a budget skip.
The helper now rejects an already over-budget footprint with `PDF_EXPORT_LIMIT_EXCEEDED`, while
a nonnegative footprint that cannot hold another candidate keeps the baseline. The root agent
reported the dedicated regression failing before this correction and passing afterward.

### Report, summary, and history compatibility

`PdfOptimizationReport` version 1 contains the closed profile and outcome, baseline/candidate/
selected byte sizes, and verified page count. Base `PdfExportVerificationReport` version 2 requires
this record, binds selected size to output size, and requires all output pages to have been checked
when the optimized candidate is selected. Other outcomes claim zero candidate-verified pages.

Legacy base report version 1 retains its exact fields and behavior. Both versions have strict
decoders; unknown versions, extra or duplicate fields, impossible size relationships, unknown
profiles/outcomes, and inconsistent counts fail closed. Existing flattened/searchable envelopes
carry the versioned base report. Plan and transport schemas keep their prior meanings.

Current export claims and package summaries show the measured size reduction or explain why the
verified baseline was retained. Planned summaries do not infer an optimization result. History
and retry-journal version 1 continue to store the exact final size, digest, and original verification
checks. Journal preparation compares all those facts while preserving the current report in
memory; serialized legacy bytes are unchanged. Restart history-only recovery therefore returns
the legacy verification facts without recreating transient optimization details. There is no
case/history migration and no claim that recovery reran optimization.

## Acceptance evidence

| Criterion | Implemented evidence | Result |
|---|---|---|
| Preserve page count, intended rendering, text presence, overlays, and encryption policy | Complete reachable-graph comparison plus exact all-page raster/geometry checks; synthetic mutation and policy regressions | Passed in the full core suites and supported live PDF/image/flattened worker scenarios at the implementation revision |
| Reopen, structurally check, and render output | Candidate strict reopen/syntax checks, fixed qpdf check, sequential PDFium comparisons, final hash/size authentication | Passed; CI executed all three flattened-worker cases, the image-worker case, and both standard PDF-worker cases |
| A larger or invalid optimized result cannot replace good output | Smaller-than-baseline write cap; ineffective-result fallback; invalid-candidate failure; separate baseline and candidate; existing no-replace publication | Passed; full unit/security failure-path coverage and live output-bound/termination scenarios |

The searchable operation's integration and report propagation are covered by core tests. Its
new live optimization assertion did not execute because the admitted OCR runtime was absent;
that existing capability skip is documented below and is not claimed as live OCR evidence.

The synthetic corpus covers visible and invisible text, ToUnicode mappings, Form overlays,
image streams, metadata/comments, reachable unused resources, shared/cyclic references, opaque
Flate streams with decode parameters, exact DCT JPEG bytes, and an unreachable raw PDF stream.
Fault cases cover semantic mutation, page/encryption policy changes, second-page raster mismatch,
resource limits, interruption, low disk/write failures, cleanup failure, private exception values,
and fresh retry. These are implementation checks, not a new human pilot or release gate.

## Changed files

This inventory was checked against all 36 changed files in implementation commit
`9b7514830a8a89329cc994b08d42360fb8980d37`; every changed file is named below. Later closure edits
are limited to the two documentation files, task state, and bundle metadata listed in the
closure section. No later implementation is included.

| Area | Files and purpose |
|---|---|
| Domain contracts | `src/paperworkshell/domain/pdf_optimization.py`: closed optimization profile/outcome/report; `src/paperworkshell/domain/pdf_export.py`: versioned base report and claims; `src/paperworkshell/domain/pdf_export_retry.py`: exact legacy history projection |
| PDF adapter | `src/paperworkshell/pdf/optimization_adapter.py`: bounded rewrite, graph comparison, strict reopen/structure/render verification, fallback and cleanup |
| Worker integration | `src/paperworkshell/worker/pdf_optimization.py`: quota, identity and selection wrapper; `src/paperworkshell/worker/pdf_export_operation.py`, `src/paperworkshell/worker/flattened_pdf_export_operation.py`, `src/paperworkshell/worker/searchable_pdf_export_operation.py`: fixed post-verification stage; `src/paperworkshell/worker/pdf_export_report.py`: strict legacy/current report codec |
| Package presentation | `src/paperworkshell/export/package_summary.py`: display only recorded optimization facts |
| Domain/adapter tests | `tests/unit/test_pdf_optimization.py`, `tests/unit/test_pdf_export.py`, `tests/unit/test_pdf_export_retry.py`, `tests/unit/test_pdf_optimization_adapter.py`: closed models, version/fact binding, legacy retry bytes, semantic preservation and failure cases |
| Worker tests | `tests/unit/worker/pdf_optimization_fixtures.py`, `tests/unit/worker/test_pdf_optimization_worker.py`, `tests/unit/worker/test_pdf_export_worker_adapter.py`, `tests/unit/worker/test_flattened_pdf_export_operation.py`, `tests/unit/worker/test_searchable_pdf_export_operation.py`: integration seams, candidate selection, quota, source identity and sanitized failure |
| Report compatibility tests | `tests/unit/worker/test_pdf_export_report.py`, `tests/unit/worker/test_flattened_pdf_export_report.py`, `tests/unit/worker/test_searchable_pdf_export_wire.py`: legacy/current base report, nested envelopes, strict bounds and invalid evidence |
| Summary/privacy tests | `tests/unit/test_package_summary.py`: measured outcomes and no preview claim; `tests/security/test_pdf_optimization_safety.py`: hostile/fault behavior, unchanged baseline, cleanup, private-value exclusion and recovery |
| Windows integration tests | `tests/integration/test_pdf_export_worker_windows.py`, `tests/integration/test_image_pdf_export_worker_windows.py`, `tests/integration/test_flattened_pdf_export_worker_windows.py`, `tests/integration/test_searchable_pdf_export_worker_windows.py`: production-path optimization report and selected-output assertions |
| Documentation and task state | `docs/architecture/pdf-safe-optimization.md`, `docs/architecture/pdf-export.md`, `docs/schemas/worker-rpc-v1.md`, `README.md`, `KNOWN_LIMITATIONS.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json`, `docs/development/pws0502-result.md`: fixed policy, compatibility, limitations and task evidence |

## Verification

The [Windows CI core job](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36823185868/job/110242998778)
passed `python tools/dev.py check --ci-group core` against exact implementation commit
`9b7514830a8a89329cc994b08d42360fb8980d37`. The sanitized local copy of its log is
`output/pws0502-ci-core.log`. This covers the full unit, security, and accessibility suites plus
all registered core checks. It supersedes the earlier overlapping focused unit/security results.

The [integration job](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36823185868/job/110242998573)
also passed at that exact revision. [CI run 36823185868](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36823185868)
has conclusion `success`. The combined results are **6,126 passed and 7 capability skips** across
the four test suites. The core and integration partitions together ran the full registered
`check` command; the local host failures remain separately recorded.

### Completed core job

The registered core group invoked the external commands below through
`uv run --locked --no-sync`; `scan-secrets` is the registered in-process scanner.

| Command/check within the core group | Result |
|---|---|
| `ruff format --check .` | Passed; 609 files already formatted |
| `ruff check .` | Passed |
| `python tools/check_import_boundaries.py` | Passed; 13 layers, 210 source files |
| `python -m mypy src tools tests` | Passed; 609 source files |
| `pytest tests/unit` | **4,960 passed in 821.62 s; no skips** |
| `pytest tests/security` | **598 passed in 302.78 s; no skips** |
| `pytest tests/accessibility` | **71 passed in 4.42 s; no skips** |
| `python -m tools.validate_synthetic_fixtures` | Passed; 46 public synthetic fixtures, 15 analytical renderer references, 6 approved image-transform references, 3 approved overlay references, 237,165 corpus bytes; scanned 2,703 historical blobs, 868 index blobs and 898 working files |
| `python -m tools.validate_schemas` | Passed; task, case-manifest v1/v2, fixture-index v1, template v1 and generic packet scenarios, worker-RPC v1, dependency-inventory v1, M1 report v1/v2/v3, and export-history v1 schemas |
| `python tools/validate_tasks.py` | Passed; 82 tasks, 243 dependencies, 9 milestones, 42 decisions |
| `python -m tools.validate_pillow_runtime` | Passed; locked wheel, 8 native members, licence and component record |
| `python -m tools.validate_docs` | Passed; local links, references and security traceability |
| `python -m tools.validate_portfolio_boundary` | Passed; 82 tasks, 243 dependencies, 24 bundled canonical files |
| `python -m tools.m1_vertical_slice_benchmark validate` | Passed; constrained-resource M1 report schema v3 |
| `python -m tools.pws0208_resource_evidence --workload benchmarks/pws0208-searchable-mixed-workload-v1.json` | Passed; constrained incremental workload |
| `python -m tools.pdf_renderer_benchmark validate` | Passed; 15 analytical goldens, 2 semantic comparisons, 3 stable outcomes, 2 renderer identities and the software-enforced budget |
| `scan-secrets` | Passed; reachable object/source-path coverage, 2,703 historical blobs, 288 historical commit/tag objects, 2,738 historical path/blob pairs, 868 index blobs, 898 tracked working files and 0 nonignored untracked files |
| `python -m tools.quality_self_test` | Passed; all 5 deliberate format/type/schema/graph/secret violations detected without retaining test data |

The separate local `validate-dependencies` check also passed. It is excluded from the default
`check` group, so that result is not attributed to the CI core command.

### Completed integration job and capability skips

`python tools/dev.py check --ci-group integration` invoked
`uv run --locked --no-sync pytest tests/integration`: **497 passed and 7 skipped in 4,649.79 s**.
The job took 1 h 18 min 35 s including setup. Its sanitized log is
`output/pws0502-ci-integration.log`.

All three cases in `test_flattened_pdf_export_worker_windows.py`, the case in
`test_image_pdf_export_worker_windows.py`, and both cases in `test_pdf_export_worker_windows.py`
passed. This closes the required platform checks for the implemented safe optimization profile,
including the constrained flattened-export cases that the local host blocked before execution.

The seven skips use pre-existing capability checks. Locations below are the skip sites reported
by pytest under `tests/integration/`:

| Test(s) | Reported skip location | Missing capability |
|---|---|---|
| `test_exact_ocr_admission_succeeds_inside_appcontainer` | `test_ocr_admission_appcontainer_windows.py:80` | Admitted PWS-0204 Tesseract runtime not staged |
| `test_live_ocr_worker_emits_verified_volatile_candidate_and_preserves_case` | `test_ocr_worker_windows.py:139` | Admitted PWS-0204 Tesseract runtime not staged |
| `test_production_adapter_reports_incremental_scheduler_peaks`; `test_live_constrained_signed_source_resource_workflow` | `test_pws0208_resource_workload_windows.py:137` (2 skips) | PWS-0208 live runner private artifact stage absent |
| `test_live_mixed_pdf_jpeg_png_export_is_searchable_verified_and_content_free` | `test_pws0209_mixed_searchable_export_gate_windows.py:195` | Exact admitted PWS-0209 OCR environment absent |
| `test_live_quality_ocr_review_returns_counts_and_recovers_after_cancellation` | `test_quality_ocr_review_worker_windows.py:74` | Admitted PWS-0209 Tesseract runtime not staged |
| `test_live_searchable_mixed_export_preserves_text_ocrs_images_and_verifies_output` | `test_searchable_pdf_export_worker_windows.py:167` | Admitted PWS-0204 Tesseract runtime not staged |

Five skips concern admitted OCR runtime/environment capabilities; two concern the private
PWS-0208 artifact stage. In particular, the new searchable-worker optimization assertion was not
executed. Neither that assertion nor any new live OCR behavior is counted as verified here.

### Completed local worker run

The seven-case run covering `tests/integration/test_pdf_export_worker_windows.py`,
`tests/integration/test_image_pdf_export_worker_windows.py`,
`tests/integration/test_flattened_pdf_export_worker_windows.py`, and
`tests/integration/test_searchable_pdf_export_worker_windows.py` finished with **3 passed,
3 failed, and 1 skipped in 712.55 seconds**:

- Passed: image reopen/export without OCR resources; repeatable standard PDF export with verified
  output, recorded history and preserved originals; worker termination without destination
  publication.
- Failed: both constrained flattened-export parameter cases and the flattened output-bound
  failure case. All three stopped during worker handshake with `ASSEMBLY_FAILED`, before the
  optimizer ran. They do not establish optimizer or constrained flattened-worker success.
- Skipped: the searchable export case because the admitted PWS-0204 Tesseract runtime was absent.
  This is not searchable optimization execution evidence.

The host's Code Integrity denial and unchanged policy are recorded below. The successful
exact-commit CI integration job supplies the required platform verification; it does not change
the outcome or policy limitations of this local run.

### Earlier local checks

These earlier runs overlap the full core job and must not be added to its counts:

- The registered selected unit run used
  `PYTEST_ADDOPTS='-k "export or optimization or package_summary" --tb=short --durations=5'`
  followed by `uv run --locked --no-sync python tools/dev.py test-unit`: 1,186 passed,
  3,774 deselected, no skips in 723.56 s.
- `uv run --locked --no-sync pytest tests/unit/test_pdf_export_service.py tests/unit/test_pdf_export_history.py tests/unit/test_pdf_export_retry.py tests/unit/test_pdf_export_retry_recovery.py tests/unit/test_package_pdf_export.py tests/unit/test_searchable_pdf_export_service.py tests/unit/test_flattened_pdf_export_app.py tests/unit/test_package_summary.py tests/integration/test_package_export.py -q --tb=short`:
  214 passed in 2.28 s. This included in-process/fake producer package/destination integration
  checks; it launched no worker. Its unit subset is superseded by the full core unit result.
- The local full accessibility suite passed 71 tests in 6.21 s before the core CI rerun.
  Earlier narrow model/codec, adapter/security, worker-seam and summary batches are superseded
  by the full unit/security results above.

These local observations supplement the complete CI result. Any source repair after the tested
implementation revision requires appropriate revalidation.

### Automated review and closure metadata

Three automated agent reviews of exact implementation commit
`9b7514830a8a89329cc994b08d42360fb8980d37` reported no actionable findings in their final review
scopes. These are automated development reviews, not independent human acceptance or manual NVDA
evidence. The earlier quota finding was corrected before this implementation commit.

Post-implementation closure changes are limited to `docs/architecture/pdf-safe-optimization.md`,
`docs/development/pws0502-result.md`, `TASKS.yaml`, and `BUNDLE_MANIFEST.json`. Runtime and test
sources remain at the exact CI-tested implementation revision.

**All five documentation/task/bundle closure checks below passed.** The final graph contains
82 tasks and 243 dependencies; the bundle validates all 24 canonical files. The post-CI diff
contains only the four closure files listed above.

```text
uv run --locked --no-sync python tools/dev.py validate-schemas
uv run --locked --no-sync python tools/dev.py validate-docs
uv run --locked --no-sync python tools/dev.py validate-task-graph
uv run --locked --no-sync python tools/dev.py validate-portfolio-boundary
git diff --check
```

### Local environment and visual inspection

Windows Code Integrity events 3033/3077 show the host blocking the pinned HarfBuzz native extension
in the staged worker runtime because it does not meet the active signing policy. The copied test
environment also encountered a blocked compiled mypy module; the repository environment's full
strict typecheck passed. No system policy, binary, dependency pin, or worker restriction was changed
to work around these denials. The completed local run retains its three pre-optimizer failures.
The exact-commit Windows integration CI job passed those three flattened-worker cases in its
configured environment; this does not establish that the local signing-policy limitation is fixed.

A generated two-page synthetic baseline was optimized in an in-process adapter/visual check with
the actual pinned qpdf checker and PDFium. It reduced from 51,036 to 2,349 bytes. Both pages were
also rendered for visual inspection
at three pixels per point: the 588-by-468 and rotated 468-by-588 images matched byte-for-byte.
Inspection showed unchanged visible text, blue vector/Form mark, image patch, annotation icon,
crop and rotation. Hidden text and reachable object preservation are covered by the separate
semantic regressions. Local review PDFs/PNGs remain under ignored `output/pws0502-visual-review/`;
no golden or repository fixture was refreshed.

## Security, privacy, and accessibility notes

Parsing, rewriting, graph comparison, and rendering stay inside the network-denied document
worker. Original paths and bytes are never rewrite targets. New report fields contain only closed
tokens and bounded numbers; raw PDF objects, text, metadata values, paths, names, image content,
and native exception values do not become report fields or routine logs. Candidate authority is
bound to verified size/digest and the existing transfer/publication lifecycle.

Current summary/result wording is plain text using the existing accessible report surfaces. No
new control or permission prompt is introduced. Cancellation and deadlines reuse the existing
operation control. The full registered accessibility suite passed 71 tests locally in 6.21 seconds
and in the exact-commit core CI job in 4.42 seconds. This does not claim new manual NVDA observation,
PDF/UA results, or hardware certification.

## Portfolio boundary and release-interface notes

The stage serves PaperWorkShell's ordinary local export workflow and processes PaperWorkShell-owned
data. It adds no sibling dependency, shared runtime/database/service, ThirdLife adapter, public
CLI, or portfolio handoff format. No packaged artifact, installer, signing, compatibility-cut,
or public release-interface fact is established here; unproved release facts remain `TBD`.

## Residual risks and intentional deferrals

- The profile deliberately supports a narrow set of representation changes. The pinned native
  writer can rewrite an `ASCIIHexDecode` stream to Flate even with generalized decoding disabled.
  The comparator rejects that opaque encoding change, fails export, and preserves the baseline;
  it does not guess that unsupported filter representations are equivalent. A regression records
  this behavior.
- External-file stream references (`/F`, `/FFilter`, `/FDecodeParms`) also fail closed.
- A compact baseline or insufficient candidate storage may produce no reduction. No target-size
  or universal reader-compatibility guarantee is made. Image-heavy analysis/re-encoding and
  target-size search remain PWS-0503/PWS-0504 work.
- Optimization statistics are transient. Legacy history recovery retains final byte size, digest,
  and checks without recreating before/candidate measurements.
- Development runtime inventory/ACL setup can still delay worker startup and cancellation before
  the active operation begins; see [known limitations](../../KNOWN_LIMITATIONS.md). This task does
  not change that launcher behavior.
- Native parser resource limits, constrained tests, and raster verification are software evidence;
  they do not establish secure erasure, legal validity, signature validation, portal acceptance,
  PDF/UA, or named hardware support.

## Task graph

- PWS-0502 is `done` with implementation and CI evidence at
  `9b7514830a8a89329cc994b08d42360fb8980d37`.
- PWS-0501 and PWS-0112 remain completed prerequisites.
- Next dependency-ready task: PWS-0503, image-heavy page analysis and staged re-encoding. The
  task closure marks it `ready`; no PWS-0503 or later implementation is included in this change.
