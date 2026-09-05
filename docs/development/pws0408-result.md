# PWS-0408 implementation and pilot evidence

This report is being finalized with the verification run. Human acceptance remains
pending; nothing in the automated or visual developer evidence approves a v0.1 release.

## Implemented

- Revision-bound desktop package review, explicit human acknowledgement, and safe
  signed-source consent before producing unsigned derivatives.
- Accessible plain-text planned/final summaries and an optional user-selected `.txt`
  companion with no hidden diagnostics or replacement of existing files.
- Actual combined-PDF, separate-PDF, and ZIP composition under the existing constrained
  policy, preserving partial/uncertain outcomes through post-publication failures.
- Corrected immutable-source page admission and progress translation for subset exports;
  central resource limits and exact output verification remain unchanged.
- A fresh isolated synthetic [human pilot](pws0408-pilot.md), with readable visual targets,
  one launch command, and explicit resume/recovery.

## Changed files

- `src/paperworkshell/domain/package_summary.py`: bounded, content-safe presentation models.
- `src/paperworkshell/export/package_summary.py`, `package_service.py`: exact evidence
  projection, recovery inventory, actual publication composition, and companion saving.
- `src/paperworkshell/app/package_workflow.py`, `organizer.py`, `signed_source_policy.py`:
  exclusive review authority, explicit acknowledgements, atomic consent snapshot, and
  organizer/autosave reconciliation before writing outputs.
- `src/paperworkshell/preflight/engine.py`: read-only consumed-dependency fingerprint access.
- `src/paperworkshell/__main__.py`: lazy restricted-worker and package service composition.
- `src/paperworkshell/ui/package_export.py`, `backend.py`, `window.py`: keyboard-accessible
  dialog, correlated commands, destination selection, progress/cancellation, final inventory,
  companion saving, safe close and recovery.
- `src/paperworkshell/worker/pdf_export_broker.py`, `flattened_pdf_export_broker.py`,
  `searchable_pdf_export_broker.py`, `searchable_pdf_export_operation.py`: bounded source
  capacity and truthful output progress for subsets of larger originals.
- `tools/pws0408_pilot.py`, `tools/dev.py`: developer-only isolated human pilot entry point.
- `tools/check_import_boundaries.py` and `tests/unit/test_import_boundaries.py`: exact
  composition-root exceptions with application/UI/descendant rejection tests.
- New tests: `tests/helpers/package_pilot.py`; package workflow, summary, composition,
  backend bridge, window, dialog, pilot helper, and source-page-budget unit tests;
  `tests/integration/test_package_pilot.py`, `test_package_summary_destination_windows.py`;
  `tests/security/test_package_summary_privacy.py`;
  `tests/accessibility/test_package_export_accessibility.py`.
- Existing preflight-engine, signed-source, worker-broker/operation, and desktop startup
  recovery tests: regression coverage and updated composition test doubles.
- `README.md`, `docs/architecture/package-summary.md`, `dependency-boundaries.md`,
  `docs/development/pws0408-pilot.md`, this report, `docs/release/RELEASE_INTERFACE.md`,
  `TASKS.yaml`, and `BUNDLE_MANIFEST.json`: contracts, human protocol, truthful release
  context, task evidence, and planning-bundle integrity.

## Verification

Full-suite results and final source revision will be inserted after successful completion.
The separate first-time-user and keyboard-only human result is **not recorded**.

## Security, privacy, and accessibility

Originals are retained unchanged. Production parsing remains in restricted workers, with
unchanged network denial, central caps, sequential admission, cancellation, and cleanup.
Output names/categories are explicit user-visible content; private source names, paths,
identifiers, hashes, OCR text, signature images, and raw exceptions are excluded from
summaries and routine representations. No new dependency, schema, migration, or font asset
is introduced. Source inspection, page details, and quality facts not gathered for this
review remain explicitly unverified.

Normal review and enlarged final-summary screenshots were visually inspected using
synthetic state. The four large-labelled human-input pages were independently rendered
and inspected. These are developer visual checks, not human accessibility acceptance,
NVDA evidence, PDF/UA conformance, or hardware certification.

## Portfolio and release interface

PaperWorkShell remains standalone. User data remains owned by its own case and explicit
local derivatives. No sibling dependency, shared interface, network service, or ThirdLife
adapter is introduced. The release sheet records source-tree summary functionality only;
packaged facts remain TBD. The pilot launcher is not an application CLI contract.

## Residual risks and decisions

- First-time-user and keyboard-only walkthrough acceptance is pending. A passing human
  result closes this remaining evidence; a defect requires correction and focused retest.
- Source/quality analysis is not rerun by this review; unknowns stay visible. The broader
  PWS-0409 gate must assess these clearly disclosed limits with the complete pilot matrix.
- Final results are transient; save the text summary before closing when it is needed.
  Durable package history would require an explicit versioned schema/migration decision.
- Mixed scan/image processing and overlays in one output retain the PWS-0407 rejection.
  The optional technical manifest remains unimplemented; no machine-readable contract is
  being frozen through this task.
- Acceptance of PWS-0408 makes PWS-0409 eligible; it does **not** approve a v0.1 release.
