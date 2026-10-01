# Task result — PWS-0502: Safe object/stream optimization baseline

**Status: `in_progress`.** Implementation and focused checks are recorded below. Final registered
quality checks, live-worker evidence, and closure review remain pending. This draft does not mark
the task done, claim a final full-suite result, or establish release acceptance.

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
Flate decoding checks that allowance before producing the next bounded chunk. Existing worker
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

## Acceptance evidence under review

| Criterion | Implemented evidence | Remaining closure work |
|---|---|---|
| Preserve page count, intended rendering, text presence, overlays, and encryption policy | Complete reachable-graph comparison plus exact all-page raster/geometry checks; synthetic mutation and policy regressions | Consolidate final registered checks and live-worker results |
| Reopen, structurally check, and render output | Candidate strict reopen/syntax checks, fixed qpdf check, sequential PDFium comparisons, final hash/size authentication | Record verified constrained-worker runs for the production paths |
| A larger or invalid optimized result cannot replace good output | Smaller-than-baseline write cap; ineffective-result fallback; invalid-candidate failure; separate baseline and candidate; existing no-replace publication | Close failure/cleanup and publication evidence on the final implementation revision |

The synthetic corpus covers visible and invisible text, ToUnicode mappings, Form overlays,
image streams, metadata/comments, reachable unused resources, shared/cyclic references, opaque
Flate streams with decode parameters, exact DCT JPEG bytes, and an unreachable raw PDF stream.
Fault cases cover semantic mutation, page/encryption policy changes, second-page raster mismatch,
resource limits, interruption, low disk/write failures, cleanup failure, private exception values,
and fresh retry. These are implementation checks, not a new human pilot or release gate.

## Changed files

This inventory reflects the draft worktree diff and new files when this report was prepared.
Closure evidence files and any later implementation changes must be reconciled before completion.

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
| Documentation and task state | `docs/architecture/pdf-safe-optimization.md`, `docs/architecture/pdf-export.md`, `docs/schemas/worker-rpc-v1.md`, `README.md`, `KNOWN_LIMITATIONS.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json`, `docs/development/pws0502-result.md`: fixed policy, compatibility, limitations and provisional task evidence |

## Verification — provisional

The following focused runs overlap. Their counts must not be added together or represented as a
single full-suite result. They ran against the evolving worktree; final revision and registered
command evidence are pending consolidation by the root agent. No live-worker result is claimed
by these rows.

| Command or reported scope | Result | Evidence scope |
|---|---|---|
| `uv run --locked --no-sync pytest tests/unit/test_pdf_optimization.py tests/unit/test_pdf_export.py tests/unit/test_pdf_export_retry.py tests/unit/test_pdf_export_history.py tests/unit/worker/test_pdf_export_report.py tests/unit/worker/test_flattened_pdf_export_report.py tests/unit/worker/test_searchable_pdf_export_wire.py -q --tb=short` | 181 passed, 1.16 s | Pure model, report codec, nested wrappers, canonical history and retry |
| `uv run --locked --no-sync pytest tests/unit/test_pdf_export_service.py tests/unit/test_pdf_export_history.py tests/unit/test_pdf_export_retry.py tests/unit/test_pdf_export_retry_recovery.py tests/unit/test_package_pdf_export.py tests/unit/test_searchable_pdf_export_service.py tests/unit/test_flattened_pdf_export_app.py tests/unit/test_package_summary.py tests/integration/test_package_export.py -q --tb=short` | 214 passed, 2.28 s | In-process/fake producer application, history/retry, package/destination and summary checks; no worker launch |
| `uv run --locked --no-sync pytest tests/unit/test_pdf_optimization_adapter.py tests/security/test_pdf_optimization_safety.py -q --tb=short` | 36 passed, 1.30 s; no skips | Adapter owner's final focused semantic, resource, fault, cleanup and privacy batch |
| Worker integration scope reported by root: `tests/unit/worker/test_pdf_export_worker_adapter.py`, `tests/unit/worker/test_flattened_pdf_export_operation.py`, `tests/unit/worker/test_searchable_pdf_export_operation.py` | 63 passed | Provisional overlapping count; not the adapter owner's final two-file batch |
| Worker optimization helper scope reported by root: `tests/unit/worker/test_pdf_optimization_worker.py` | 11 passed, 0.40 s | Selection/quota/authority coverage, including the corrected negative remaining-quota case; helper file renamed to avoid collision with the domain test module |
| Package summary scope reported by root: `tests/unit/test_package_summary.py` | 33 passed, 0.63 s | Provisional summary coverage |
| Focused Ruff formatting/lint and strict mypy on the ten domain/codec/test files owned by the report agent | Passed; mypy checked 10 files | Narrow checks, not the complete registered quality commands |
| Adapter owner's focused Ruff formatting/lint and strict mypy | Passed; mypy checked 3 files | Adapter and its two focused test files |
| Full-tree static and validation checks reported by root | Format, lint, typing, import boundaries, schemas, docs, task graph, and dependency checks passed | Typing checked 609 files; import boundaries covered 210 source files; exact final command/revision rows pending consolidation |

The registered unit lane is currently running with
`PYTEST_ADDOPTS='-k "export or optimization or package_summary" --tb=short --durations=5'`
and `uv run --locked --no-sync python tools/dev.py test-unit`: 1,186 tests were selected and 3,774
deselected. Its final result is pending and is intended to supersede the overlapping earlier
focused counts. The separate seven-case live integration run is also in progress; initial
failures require diagnosis, so no live integration pass is claimed here.

Final registered command results, relevant integration/security/accessibility evidence,
portfolio-boundary and fixture/secret checks, and clean diff/evidence checks must be recorded before
task completion. Extended live-worker assertions being present in the diff are not evidence that
those scenarios have passed. Any source repair after a passing check needs appropriate revalidation.

### Local environment and visual inspection

Windows Code Integrity events 3033/3077 show the host blocking the pinned HarfBuzz native extension
in the staged worker runtime because it does not meet the active signing policy. The copied test
environment also encountered a blocked compiled mypy module; the repository environment's full
strict typecheck passed. No system policy, binary, dependency pin, or worker restriction was changed
to work around these denials. The seven-case live run remains pending its final diagnostics.

A generated two-page synthetic baseline was optimized with the actual pinned qpdf checker and
PDFium. It reduced from 51,036 to 2,349 bytes. Both pages were also rendered for visual inspection
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
operation control. Final accessibility regression evidence is still pending; this draft claims no
new manual NVDA observation, PDF/UA result, or hardware certification.

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

- PWS-0502 remains `in_progress` pending final verification and evidence closure.
- PWS-0501 and PWS-0112 remain completed prerequisites.
- PWS-0503 is the next task this change will unblock after PWS-0502 is complete; it remains
  `planned` in this draft. No later task is marked ready by this report.
