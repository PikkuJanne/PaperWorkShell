## Task result — PWS-0702: Complete screen-reader names, state, and announcements

**Status:** done — meaningful native names/state, bounded announcements and actionable recovery are implemented. All six NVDA scenario areas have cumulative agent-operated evidence; original attempts and failures retain their source-specific scope.

### Implemented

- Added one bounded screen-reader announcer shared by the organizer, scan, compression and package workflows. It keeps no history or queue, deduplicates identical messages and samples changed progress phase/quarter checkpoints no more often than every two seconds. Actual completion, error and cancellation outcomes remain immediate. Cancellation is requested once and subsequent stale progress is ignored.
- Exposed selected case/revision/count, source-document/page ordinals, rotation, preview/quality, crop and bounded overlay/removal-intent state. Typed organizer projections use existing validated manifests and closed enums; they expose no filename, overlay payload or document text.
- Made current status, instructions, preview availability, OCR warnings, signature-image consent and unsigned-derivative guidance available through native accessible names. Existing roles, values, selection and keyboard actions remain native Qt interfaces.
- Corrected stale error names and moved failure focus to enabled Import, editor launch, Apply/Save, Refresh, Prepare, preview or Close recovery controls as appropriate. Pending-save recovery preserves its actionable status focus.
- Added an append-only, resumable developer recorder with source/environment/fixture/artifact bindings and stale/missing-evidence refusal. It is explicitly not a finished human-test handoff.

### Changed files

All paths below describe the PWS-0702 delta relative to the accepted PWS-0701 working-source identity, not the much older root index. The unrelated existing working tree and root Git state were preserved.

- `src/paperworkshell/ui/announcements.py`: bounded event policy.
- `src/paperworkshell/app/organizer.py`, `src/paperworkshell/ui/models.py`: typed semantic projections and native page descriptions.
- `src/paperworkshell/ui/window.py`: case/status names, announcements, stale-error correction and editor-launch recovery focus.
- `src/paperworkshell/ui/package_export.py`, `compression_review.py`, `scan_review.py`: current progress/preview/outcome semantics and recovery actions.
- `src/paperworkshell/ui/checklist.py`, `constraints.py`, `overlay_tools.py`, `redaction_tools.py`: current status and failed-save recovery.
- `src/paperworkshell/ui/ocr_languages.py`, `signature_images.py`: availability, warnings, consent and status names.
- `tests/accessibility/test_page_screen_reader_semantics.py`, `test_screen_reader_announcements.py`, `test_screen_reader_dialogs.py`: new native interface/event/recovery tests.
- `tests/accessibility/test_accessibility_baseline.py`, `test_checklist_accessibility.py`, `test_compression_review_accessibility.py`, `test_dialog_keyboard_audit.py`, `test_editor_keyboard_audit.py`, `test_ocr_language_accessibility.py`, `test_quality_review_accessibility.py`, `test_redaction_tools_accessibility.py`, `test_scan_review_accessibility.py`: current name/focus regressions and compatibility assertions.
- `tests/unit/test_organizer_screen_reader_projection.py`, `test_pws0702_screen_reader.py`: new pure projection and recorder success/failure tests.
- `tests/unit/test_ocr_language_dialog.py`, `test_package_export_dialog.py`, `test_scan_review_dialog.py`: affected dialog expectations.
- `tools/pws0702_screen_reader.py`: bound developer observation recorder.
- `docs/accessibility/pws-0702-nvda-audit.md`, `README.md`: accessible-state inventory, scenario, actual observations and discovery.
- `docs/development/pws0702-result.md`, `TASKS.yaml`, `BUNDLE_MANIFEST.json`: completion evidence and matching task/document inventory.

### Verification

Commands use the stable repository entry points. Selected suites below are labelled with their scope; no full integration or security pass is inferred.

| Command | Result |
|---|---|
| `python tools/dev.py format`, `lint`, `typecheck` | PASS; 786 files formatted/checked/typed. Final code bytes remained frozen through native evidence and QA. |
| `python tools/dev.py check-import-boundaries` | PASS; 13 layers, 278 production Python modules. |
| `python tools/dev.py test-accessibility` | **209 passed**, no skips/selection; `output/pws0702-accessibility-final4.log`. |
| `python -B -m tools.dev test-unit` in the source-bound QA checkout | **6689 passed** in 826.40 seconds, no skips/selection; all 40 baseline-evaluation cases passed. Receipt `output/pws0702-qa/test-unit-full-v4.json`, SHA-256 `9fcfc210c1f4d0df39879f3a06ce2a38038a75943d1b2b24f4b085ab60070b01`. |
| `python tools/dev.py test-security` with `PYTEST_ADDOPTS='-q -k "page_organizer_privacy or overlay_editor_privacy or core_overlay_tools_privacy or redaction_intent_security"'` | **20 passed, 991 deselected**; `output/pws0702-security-selected.log`. |
| `python tools/dev.py test-integration` with `PYTEST_ADDOPTS='-q -k "organizer_ui_lifecycle or quality_entry_points"'` | **15 passed, 568 deselected**; `output/pws0702-integration-selected.log`. |
| `python tools/dev.py validate-schemas`, `validate-task-graph`, `validate-docs`, `validate-portfolio-boundary` | PASS before metadata publication; final metadata results and exact hashes are separately recorded in `output/pws0702-finalize/metadata-validation.json`. Graph: 82 tasks, 243 dependencies, 9 milestones, 42 decisions. |
| `python tools/dev.py scan-secrets` at frozen final production source | PASS across all root refs/history/index/working paths: 3,347 historical blobs, 330 commit/tag objects, 3,393 historical path/blob pairs, 1,012 index blobs, 1,043 tracked and 134 nonignored untracked working files. Log `output/pws0702-scan-secrets-frozen-v4.log`. The final metadata-inclusive rerun is independently captured by `output/pws0702-finalize/final-scan.json`; no history, rules or budgets were narrowed. |

The stale-error regression failed before repair (**1 failed**), and all five editor-launch recovery cases failed before repair (**5 failed**). Those logs remain `output/pws0702-stale-error-before.log` and `output/pws0702-recovery-before.log`. Final accessibility and full unit checks include the repairs.

The original root full-unit run remains **FAIL — 6,688 passed, 1 failed** in 1,247.68 seconds, at its earlier source epoch. Its only failure was the inherited PWS-0207 baseline implementation/index binding. It was not suppressed or relabelled. A real complete source-bound checkout, retaining all 105 own refs and 6,099 reachable objects, provided a genuine implementation revision for the unchanged baseline validator:

- Production/task checkpoint: `611b764b4c66113cfe400f964d26e6f6fbe51c7b`.
- Generated evidence checkpoint: `59c23c9ba34968c448799e172255e673c95ff34e`.
- Prepared receipt: `output/pws0702-qa/prepared-source-bound-qa-v4.json`, SHA-256 `cb97d6c01d9d74ffffde456205eaf5282d3b5a41417c76e2a0d30ab477ad6187`.
- Actual candidate baseline SHA-256 `3723fa6d6cf145eac6c21dab54fa2a344fd487b44cf2bc8acc8953be51f6ea66`; only its revision differs. Corpus, metrics, profiles and resources match the original byte-for-byte in meaning. No new heuristic evaluation or accuracy claim is invented.
- Root baseline SHA-256 remains `56c2579b41fffdf8f850d3f0bb91600b9e5c93e3a5e42c7d5056bd4ed1e1629f`. Root HEAD, raw/semantic index, all refs/reachable objects, source and unrelated working bytes were checked before and after the full run. All three initial and both after-guards passed. The QA tree excludes only 26 preserved unrelated temporary/DOCX paths; the root retains them all.

The two earlier full secret scans remain failures because canonical source changed while enumeration was running (`SECRET_SCAN_INPUT_INVALID`). The frozen-source full scan passed. No protected input or golden file was normalized and no required test was skipped.

### Native NVDA evidence

The [audit and findings log](../accessibility/pws-0702-nvda-audit.md) records the actual speech observations and their scope. Its cumulative index SHA-256 is `a6badd6cb59240973e28c45f3792b9fc0a654e9a79379ad288675877d7bf1fc1`. Original successful v2 case/page and Fill/Protect observations remain original, with an exact limited source-invariance proof. The final v4 recorder's four observed areas ran in an ordinary fresh v5 production environment. Its own two remaining phases stay pending; no old pass was copied into a new record and no single uninterrupted final-source scenario is claimed.

The fresh supported packet actually completed normal source inspection and export. Its four-page PDF is 2,020 bytes, SHA-256 `9719fb9c9369b3cc318677302cd74d5d1ecb8a35b1a9076a5a10a62d96350eca`. Independent pikepdf/qpdf checks, extraction of fixed synthetic targets, four PDFium renders and visual inspection passed. The 6,069-byte saved summary SHA-256 is `8c08d9a76a35a18e1c5bf365ce6aa4d025a38ef38a23ab6f5243f7657c8febb8`; it exactly equals the selected displayed text plus one LF. Closing returned focus to Review and export package.

The strict post-native audit passed with all protected originals/manifests and production bytes preserved, no process matching its recorded ownership filter, no resource journal, and all five case temp directories empty. A separate later snapshot found no qpdf/Tesseract process; the filter alone cannot prove absence of every possible earlier child. Independent review rehashed 29 referenced artifacts and revalidated 286 production files and all five cases: `output/pws0702-screen-reader/native-evidence-independent-review-v1.json`, SHA-256 `309e86c667461036f3f428927af2ab36595620c5e652a580e1edc6385f2fb3a8`. Neither audit replaces output parsing/rendering. Earlier incomplete attempts, failed multi-source export, first partial verifier and first conservative audit remain preserved as such.

### Security/privacy/accessibility notes

- No new parsing, production document I/O, export/rendering/worker policy, dependency, schema, migration, budget, queue or cache was introduced. The projection is bounded by the existing overlay limit; the announcer retains one checkpoint/message only. Existing selected privacy and lifecycle checks passed.
- Routine logs contain no speech transcript, OCR/document payload, overlay value, filename or private path from this work. All new fixtures and exported targets are synthetic. Output inspection occurred in the developer verification environment, through existing adapters.
- Errors retain stable safe codes and plain recovery messages. Visual marks, saved unapplied removal intent, actual cancellation, unsigned derivatives and remaining human review keep distinct truthful meanings.
- Native evidence is agent-operated NVDA developer evidence on the recorded environment, not human testing, PDF/UA, named-device certification or every-screen-reader compatibility. The recorder is not handed to a person; future human use requires a finished policy-compliant guide and a whole-journey burden rehearsal.

### Portfolio boundary and release-interface notes

PaperWorkShell remains useful and complete alone. There is no sibling lookup, adapter, shared service, runtime schema, data ownership change or synchronized version. Release-interface facts remain TBD; no packaging, installer or independent release gate is asserted by the UI evidence.

### Residual risks or deferrals

The first multi-source packet failed without publication and its cause remains unproved. The fresh supported one-source packet establishes the affected terminal UI/summary path, not that earlier composition or mixed image/overlay export. Slow normal containment setup and that composition remain qualification observations for PWS-0807. No resource/security relaxation or invented causal explanation was used. Accepted older human evidence is preserved and no human retest was requested.

### Task graph

- Updated PWS-0702 to **done** after automated and native applicability review.
- Marked newly unblocked PWS-0705 **ready**; PWS-0703 remains ready.
- Next ready task by milestone, priority and critical path: **PWS-0705 — Implement assisted-use consent, lock, and retention policy** (P0; PWS-0703 is P1).
