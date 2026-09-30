# PWS-0501 — PDF feature inspection

Status: `done`. Implementation, automated review, and the verification recorded below are
complete. Results retain their stated scopes; the composite evidence is not a new full CI run.

## Implemented

### Typed feature inventory

The existing restricted-worker inspection now reports metadata, annotations/comments, and form
fields alongside signature markers, attachments, interactive content, actions, scripts, and XFA.
Encryption and structural/open state remain separate typed fields. Each feature uses the closed
states `detected`, `not_detected`, and `unknown`; the report contains only states and numeric
counts. PDF objects and values stay inside the adapter.

The transient report and `pdf.inspect` capability are version 2 within the existing RPC v1
transport. The v1 report decoder preserves its original six-feature meaning and supplies
`unknown` for the three added fields. V1 encoding rejects known new fields it would discard.
Production inspection requires the current capability and report version, so a legacy worker
cannot stand in for the expanded check. Malformed, inconsistent, duplicate-field, extra-field,
misbound, or future-version reports are rejected under the existing frame limit.

The adapter checks registered markers across the bounded object graph. It recognizes empty
`/Info` dictionaries, metadata stream/reference markers, annotation subtypes, form fields/widgets,
and XFA stream/packet forms without decoding metadata, comments, field values, or stream content
into the report. Syntax warnings and unsupported or malformed feature shapes preserve positive
detections while changing otherwise negative feature states to `unknown`.

### Fresh inspection in package review

Production package review now inspects every saved PDF source, including sources not assigned
to a package slot, sequentially through the existing restricted worker. Images are explicitly
not applicable. A committed-source read lease authenticates the saved manifest revision,
digest, and compare-and-swap token, retains exact manifest/source handles, and checks the source
hash before and after the one-shot worker. The child receives reduced read authority; the
broker terminates the worker before revalidating and releasing the lease.

Begin and refresh share the existing source-import timeout across the source sequence and
expose numeric progress and cancellation. Results stay in the active review, bound to case,
revision, ordered source IDs, media types, hashes, and saved signature states. They are not
written to the manifest or a persistent cache. A consent-only revision may reuse these facts
after exact source binding and current-snapshot checks; refresh obtains new results.

Recoverable inspection failure produces explicit unavailable facts. Cancellation, timeout,
changed sources, or changed manifest authority abort the review. A failed refresh clears the
old report and export permission while retaining only the snapshot needed for retry and case
locking. A late result after cancellation is not shown as a completed review. Pending previews
finish cancellation before a queued package inspection begins.

### Preflight and policy boundaries

Preflight supplies stable findings for detected and unknown metadata, annotations, and form
fields, while retaining the earlier encryption, attachment, action, script, XFA, signature, and
interactive-content rules. Annotation/form/signature structures can therefore reveal an
existing interactive-content blocker where the former review reported inspection unavailable.
Warning acknowledgement cannot override a blocker.

A fresh signature-marker result of `detected` or `unknown` blocks export when the saved source
state says `none_detected` or is missing from the review facts. The stable finding is
`PDF_SIGNATURE_STATE_REVIEW_REQUIRED`. This avoids silently bypassing unsigned-derivative
consent or relabeling a source. Saved marker/unknown states retain their existing consent rules.
No saved signature state is migrated and no cryptographic signature validation is claimed.

The [inspection contract](../architecture/pdf-structural-inspection.md) records inert
safe-profile placeholders for each feature. Feature removal, arbitrary form flattening,
password handling, and derivative sanitization are unavailable in this task. Later safe-output
profiles belong to PWS-0506; permanent removal remains behind the M6 security gate.

## Acceptance evidence

| Task criterion | Implemented behavior and focused evidence |
|---|---|
| Stable metadata, attachment, annotation/comment, action, script, form/interactivity, encryption, and signature states | Closed v2 model and wire contract; generated feature matrix; preflight finding registry and current-report worker checks |
| Unknown/uninspectable is not absent | Legacy reports, encrypted/malformed documents, syntax warnings, unknown feature kinds, malformed immediate members, and unavailable worker results retain explicit uncertainty |
| Inspection values stay out of routine logs | Private synthetic canaries checked against encoded reports, representations, stdout/stderr, logs, preflight summaries, and sanitized exception chains |
| Raw PDF objects remain in the adapter | Pure result models cross application/worker boundaries; committed source handles and bounded reports cross the broker; no GUI PDF parsing was added |

These checks and the registered verification below satisfy the task's implementation acceptance
criteria. The remaining limits are recorded explicitly rather than treated as checked features.

## Inspection corpus

All inputs are synthetic. Tests generate new feature cases in temporary directories; no
applicant documents or new private binary fixtures are required. Existing synthetic PDFs cover
ordinary, malformed, encrypted, signed-marker, attachment, action/script, incremental,
image-only, mixed, geometry, duplicate, and Unicode-filename cases.

| Corpus group | Cases and asserted behavior |
|---|---|
| Metadata | Populated and empty document info; catalog/page metadata; typed metadata stream; presence without values |
| Annotations and forms | Text, popup, and free-text comments; widget and text field; XFA stream and packet array; signature field; dedicated feature states plus retained interactivity |
| Attachments and active structures | Associated files and embedded streams; JavaScript and cyclic action references; existing all-object/orphan-marker coverage |
| Unsupported/malformed shapes | Invalid info/metadata/annotation values; unknown annotation, field, and action kinds; invalid annotation, field, associated-file, embedded-file, and XFA packet members; observed markers retained, remaining negatives unknown |
| Limits and interruption | Input/page/object/depth/collection/node bounds, repeated cancellation probes, shared deadline, constrained Windows worker, termination and cleanup |
| Saved-source authority | Revision/digest/CAS/source mismatch; unavailable source; denied writes and reduced handles; post-worker revalidation; cleanup and subsequent recovery |
| Application and UI | Sequential PDF-only acquisition, progress, stale facts, unavailable results, consent reuse, signature mismatch blocker, late cancellation, queued preview, failed refresh and retry |

### Corrective findings addressed during review

- Fresh signature risk could disagree with saved consent/label state. The new bound saved-state
  fact and blocking finding prevent acknowledgement from bypassing that mismatch.
- Malformed array members could escape top-level marker shape checks even without qpdf warnings.
  Immediate-member checks now cover fields, annotations, associated files, embedded streams,
  and XFA packets under the existing collection budget.
- The live committed-source path exposed a media comparison against `pdf` instead of the
  manifest value `application/pdf`; the broker now uses the correct manifest media type.
- Nested storage/worker exceptions could retain private data in exception chains. Narrow
  inspection boundaries now detach raw context, cause, and traceback before returning safe
  errors; acquisition, handle access, revalidation, cleanup, and recovery have regressions.
- Refresh and cancellation needed explicit stale-result handling. Tests now prove old summaries
  are cleared, cancelled late results stay unpublished, and recovery returns keyboard focus.

## Changed files

| Area | Files and purpose |
|---|---|
| Domain and PDF adapter | `src/paperworkshell/domain/pdf_inspection.py`, `src/paperworkshell/pdf/pikepdf_adapter.py`: v2 inventory, compatibility decoder, conservative marker traversal |
| Worker and protocol | `src/paperworkshell/worker/pdf_source_inspector.py`, `src/paperworkshell/worker/desktop_composition.py`, `src/paperworkshell/worker/protocol/models.py`, `tools/validate_worker_rpc.py`: committed-source orchestration, current capability, source/report binding and sanitized failures |
| Storage | `src/paperworkshell/storage/source_ingest.py`: authenticated committed PDF read lease, integrity checks, bounded cleanup and sanitized errors |
| Preflight | `src/paperworkshell/preflight/facts.py`, `src/paperworkshell/preflight/core_rules.py`: source-bound inventory, feature findings and signature-state mismatch blocker |
| Application/composition | `src/paperworkshell/app/package_workflow.py`, `src/paperworkshell/app/organizer.py`, `src/paperworkshell/__main__.py`: fresh review acquisition, retry state and production injection |
| Desktop UI | `src/paperworkshell/ui/backend.py`, `src/paperworkshell/ui/window.py`, `src/paperworkshell/ui/package_export.py`: threaded begin/refresh, progress/cancel, stale-result handling and recovery focus |
| Domain/adapter/preflight tests | `tests/unit/test_pdf_inspection_domain.py`, `tests/unit/test_pikepdf_adapter.py`, `tests/unit/test_preflight_core_rules.py`, `tests/unit/test_preflight_facts.py`, `tests/security/test_preflight_privacy.py`: feature matrix, malformed/unknown states, wire compatibility, binding, blocker policy and prohibited-value canaries |
| Broker/application/UI tests | `tests/unit/worker/test_committed_pdf_inspection.py`, `tests/unit/worker/test_pdf_inspect_protocol.py`, `tests/unit/test_package_review_inspection.py`, `tests/unit/test_package_export_backend_bridge.py`, `tests/unit/test_package_export_window.py`: committed authority, current protocol, sanitized errors, fresh review, cancellation and recovery |
| Windows integration/accessibility tests | `tests/integration/test_pdf_feature_inspection_storage_windows.py`, `tests/integration/test_pdf_inspection_worker_windows.py`, `tests/integration/test_package_pilot.py`, `tests/accessibility/test_package_review_inspection_accessibility.py` |
| Documentation/evidence | `docs/architecture/pdf-structural-inspection.md`, `docs/architecture/preflight-rule-engine.md`, `docs/architecture/package-summary.md`, `docs/schemas/worker-rpc-v1.md`, `README.md`, `KNOWN_LIMITATIONS.md`, `docs/development/pws0501-result.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json`: current contracts, limitations, acceptance evidence and task graph |
| Existing quality evidence | `benchmarks/pws0207-quality-findings-v1.json`, `docs/development/scan-quality-evaluation.md`: rebind unchanged metric rows to the changed shared implementation and record provenance |

## Security, privacy, resources, and accessibility

Document parsing remains in the network-denied AppContainer worker. No source/output mutation,
new parser dependency, shell command construction, resource-budget increase, persistent report,
case-schema migration, or license change is introduced. The current resource policy bounds
worker memory/CPU/processes, input size, graph work, temporary storage, and result frames.
Sequential source inspection adds no unbounded queue or concurrent worker pool. Current
manifest authority and complete source hashes are rechecked before results become current facts.

The existing [development-runtime startup limitation](../../KNOWN_LIMITATIONS.md) still applies:
Windows inventories and grants runtime access before worker creation. Those ACL passes have
finite object/depth bounds but no elapsed-time guarantee, and can delay cancellation while a
large development environment is prepared. Operation-deadline and active-worker cancellation
evidence must not be read as a bound on that setup phase. This task does not change the launcher.

Reports and UI findings contain states, IDs, stable codes, and numeric progress/counts. Metadata,
comment/field/attachment contents, filenames, paths, script bodies, certificate data, raw parser
warnings, and nested private error text do not become report values or routine diagnostics.
Cleanup evidence concerns application resources; it does not claim forensic erasure.

The package shortcut remains Ctrl+E. Begin and refresh run through the backend thread and expose
named, keyboard-accessible Cancel controls with visible focus and progress announcements.
Cancellation/retry tests check actionable focus on Export package or Refresh review, and a slow
fake inspector proves the GUI event loop keeps ticking. Automated checks exercise doubled font
size, accessible button role/name/state, keyboard Space cancellation, pending-preview ordering,
late-ready rejection, and retry with a fresh cancellation token.

These are widget/threaded accessibility checks. They do not constitute new manual NVDA
acceptance; the earlier pilot evidence retains its original scope. Constrained-profile checks
prove bounded software behavior on the tested host, not hardware certification or a supported
device promise.

## Portfolio boundary and release interface

The feature improves ordinary local package review when ThirdLife and sibling applications are
absent. PaperWorkShell retains sole ownership of source documents, manifests, transient review
facts, consent, and derivatives. The work uses no sibling repository, service, private fixture,
shared schema, synchronized release, or portfolio-specific API. Existing standalone launch and
ordinary file workflows remain the product boundary.

This task versions an internal worker capability/report, not a public portfolio handoff. It
creates no installer, update, file-association, hardware-support, portal-acceptance, or packaged-
release guarantee. Unproved public release-interface facts remain `TBD` in their owning work.

## Verification results

Implementation checkpoint: `3b195294e05d212c1570d7a8e608cb8b564ffff2`.
The later `988ec715021b846cee045344ede42d1c422228f0` changes only the existing quality baseline's
revision binding and its provenance documentation. The baseline recorder and exact-revision
validator confirmed that all eight metric rows, corpus/profile identities, and resource fields
are unchanged. Six of its 27 bound shared implementation files changed for PWS-0501; the other
21 retain their earlier bytes. The baseline's 40 tests passed in 61.27 seconds. No golden,
algorithm, validator rule, or budget was relaxed.

The following results overlap and must not be added into a unique-test total. The broad runs
began before the last eight adapter cases were added; the final 241-test focus covers that
change. All broad-run failures were resolved and their affected cases passed serially.

| Scope | Command or exact test selection | Observed result |
|---|---|---|
| Application, UI, and focused accessibility | `uv run pytest tests/unit/test_package_workflow.py tests/unit/test_package_review_inspection.py tests/unit/test_package_export_backend_bridge.py tests/unit/test_package_export_window.py tests/accessibility/test_package_export_accessibility.py tests/accessibility/test_package_review_inspection_accessibility.py` | 55 passed in 2.69 s, after saved-signature-state wiring and cancellation/retry regressions |
| Final combined domain/adapter/preflight/privacy/worker/storage focus | `python -m pytest -q tests/unit/test_pdf_inspection_domain.py tests/unit/test_pikepdf_adapter.py tests/unit/test_preflight_core_rules.py tests/unit/test_preflight_facts.py tests/security/test_preflight_privacy.py tests/unit/worker/test_committed_pdf_inspection.py tests/unit/worker/test_pdf_source_inspector.py tests/unit/worker/test_pdf_inspect_protocol.py tests/unit/worker/test_desktop_composition.py tests/integration/test_pdf_feature_inspection_storage_windows.py` | 241 passed in 10.63 s; supersedes the earlier 175 adapter/core/privacy, 17 storage, and 16 committed-broker focused runs |
| Earlier real Windows inspection worker | `.venv/Scripts/python.exe -m pytest -q tests/integration/test_pdf_inspection_worker_windows.py --durations=5` | 3 passed in 88.74 s before the final malformed-member change; superseded for final-source live evidence by the serial integration retry |
| Registered accessibility suite | `.venv/Scripts/python.exe tools/dev.py test-accessibility` | 71 passed in 5.28 s |
| Broad unit run | `python tools/dev.py test-unit` | 4,834 passed and 2 failed in 2,041.32 s; failures were the old baseline binding and a corpus-generation deadline during concurrent load |
| Serial unit repair verification | `PYTEST_ADDOPTS='-k "test_checked_in_baseline_is_canonical_and_matches_current_corpus or test_generator_is_byte_deterministic_and_records_actual_production_checks" --tb=short --durations=5'`, then `python tools/dev.py test-unit` | Both selected tests passed in 189.40 s; 4,842 deselected. Both corpus generations completed with identical bytes and the original deadlines/goldens |
| Broad integration run | `python tools/dev.py test-integration` | 476 passed, 17 failed, 10 skipped in 2,563.95 s; worker launch/preparation failures during overlapping suites; all 17 failures subsequently passed |
| Serial integration repair verification | `PYTEST_ADDOPTS='--last-failed --last-failed-no-failures=none --tb=short --durations=10'`, then `python tools/dev.py test-integration` | All 17 selected cases passed in 5,616.83 s; exactly the 17 failed integration nodes were selected, with 41 other test files excluded by pytest's cache selection |
| Earlier broad security attempt | `python tools/dev.py test-security` | 579 passed, 3 failed, 6 skipped in 443.09 s; the three live isolation cases met the conflicting-runtime ACL recovery refusal; superseded by the full serial run |
| Final full security suite | `PYTEST_ADDOPTS='--tb=short --durations=8'`, then `python tools/dev.py test-security` | 582 passed, 6 capability skips, zero failures in 377.16 s; includes all three previously failing live isolation cases |

Registered static commands passed: `format-check`, `lint`, `check-import-boundaries`, and
`typecheck` through `python tools/dev.py`. Formatting and strict typing covered 601 Python files;
the import check covered 13 layers and 207 source files. The final combined focus includes seven
malformed-member regressions demonstrated failing before the fix, signature mismatch/binding
cases, nine privacy canaries, source authority, termination ordering, sanitized errors, and
storage recovery.

Registered validation also passed: `validate-schemas`, `validate-docs`, `validate-task-graph`,
`validate-portfolio-boundary`, `validate-dependencies`, `validate-pillow-runtime`,
`validate-m1-benchmark-report`, `validate-pws0208-resource-workload`,
`validate-pdf-renderer-benchmark`, and `quality-self-test`. The graph has 82 tasks, 243 dependency
edges, nine milestones, and 42 frozen decisions; the bundle contains 23 canonical files. All
five deliberate quality violations were detected. `git diff --check` passed.

`python tools/dev.py validate-fixtures` and `python tools/dev.py scan-secrets` passed serially in
a clean, full-history, single-branch clone at `3b19529`. Fixture validation covered 46 synthetic
fixtures, 15 analytical, six image, and three overlay references, with 237,165 corpus bytes.
Repository scans covered 2,662 historical blobs, 858 index blobs, and 888 tracked working files;
the secret scan additionally checked 282 commit/tag objects and 2,697 historical path/blob
pairs, with no non-ignored untracked file. Unrelated worktree snapshot refs were excluded by
the branch clone, without changing the shared repository or scanner policy. Later changes
only rebind the unchanged quality baseline and update documentation/task/bundle evidence.

All live-worker suites on this host must run serially: even the unit renderer benchmark starts
real workers. Overlapping suites with different staged runtime roots trigger the authenticated
ACL recovery allowlist guard. An additional four-test inspection/pilot attempt met the same
refusal and is not passing evidence. No journal, ACL check, resource budget, timeout, or golden
was weakened to recover. Serial retries use the CI copy-owned CPython setup and locked
environment; the M1 and organizer cases retain their full production runtime configuration.
The final retry covered two M1 cases, two organizer cases, the package pilot, one export-adapter
case, two export-worker cases, all three inspection-worker cases, four thumbnail-worker cases,
and two qpdf-permission cases. The package pilot exercised fresh inspection and every package
export mode. The final constrained inspection case verified feature states, privacy canaries,
source/manifest hashes, worker termination, and temporary cleanup against the final source.

The live cases were slow on this host: the cold-restart M1 round-trip took 2,057.24
seconds, the two organizer cases 670.14 and 673.99 seconds, and the package pilot 837.02 seconds.
The constrained inspection case took 105.32 seconds. These are observed test durations, not
hardware-support or startup-latency guarantees. Runtime inventory/ACL preparation remained
unchanged; the existing startup limitation described above is retained.

The initial integration skips were three unavailable Windows link-creation cases, five absent
admitted OCR runtime/environment cases, and two tests requiring a private PWS-0208 live-runner
artifact stage. The final security skips were six unavailable file/directory link cases.
These environment-specific omissions do not count as exercised scenarios or new OCR evidence.

Automated agents reviewed the implementation during development and closure evidence at
`988ec715`. The final 40-file list and evidence wording received an additional read-only check.
No unresolved finding remained. This is automated agent review, not new human or manual NVDA
acceptance.

Closure changes only documentation, task evidence/status, and bundle hashes. The closing checks
are `python tools/dev.py validate-schemas`, `validate-docs`, `validate-task-graph`,
`validate-portfolio-boundary`, and `git diff --check`; their results are recorded in task evidence.

## Residual limits and task graph

Inspection detects registered structural markers; `not_detected` is not proof that all hidden
content is absent. Unknown/unavailable facts stay visible. The task neither removes detected
features nor verifies a derivative's privacy, validates signatures, repairs PDFs, or guarantees
portal acceptance. Existing export verification remains separately required.

PWS-0501 is `done`. PWS-0502 — Implement safe object/stream optimization baseline — is the next
dependency-ready task and is marked `ready`. No PWS-0502 implementation or later milestone gate
work is included in this change.
