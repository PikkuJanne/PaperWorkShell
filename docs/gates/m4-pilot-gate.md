# Milestone 4 v0.1 pilot gate — PWS-0409

- Review status: In progress
- Gate conclusion: **IN PROGRESS — no v0.1 approval yet**
- Review date: 2026-09-30
- Full automated-suite revision: `248c26ed56679b5bd4ba3faadc13c2410347960f`
- Gate closure revision: Pending
- Review branch: `codex/pws-0409-pilot-gate`
- Frozen decisions: PWS-D003, PWS-D006, PWS-D022, PWS-D023, PWS-D030,
  PWS-D033, PWS-D040, PWS-D041

## Scope and decision rule

This gate reviews the standalone guided packet workflow through template loading,
user-confirmed checklist assignment, preflight, constraints, combined PDF/separate
PDF/ZIP export, and an accessible human-readable summary. It also checks that
deferred features remain outside the v0.1 product claims.

The evidence is composite: the maintainer's accepted PWS-0408 walkthrough, earlier
milestone records, the current full automated suite, focused constrained storage
and recovery tests, and a separate desktop/NVDA/manual record. These are
not presented as one fresh human session or one newly measured whole-packet run.
The gate remains open until the required desktop scenarios and final changed-file
validation are recorded and reviewed.

Authority is [ROADMAP.md](../../ROADMAP.md), [TASKS.yaml](../../TASKS.yaml), and
[DECISIONS.md](../../DECISIONS.md). The [package export contract](../architecture/package-export.md),
[summary contract](../architecture/package-summary.md),
[resource policy](../performance/LOW_RESOURCE_POLICY.md), and
[known limitations](../../KNOWN_LIMITATIONS.md) define the supported behavior.

This is the M4 source-tree pilot gate. The packaged M8 release interface, installer,
SBOM, licence/notice closure, update/uninstall matrix, and ThirdLife compatibility
cut remain separate work with their unproved facts left `TBD`.

## Prerequisite review

All prerequisites below are recorded `done` in the task graph. Their historical
evidence remains bound to its recorded revision; the present full suite is identified
separately below.

| Earlier gate | Status | Recorded evidence |
|---|---|---|
| [PWS-0010 / M0](m0-foundation-gate.md) | Done / PASS | Evidence `ebe66c856aae1932d89d233606012e0d35c122af`; task-state closure `f9e910db8c7e2a3b9d9c9705de735e0373d25b78` |
| [PWS-0114 / M1](m1-vertical-slice-gate.md) | Done / PASS | `cf96a82c1a4b24bed980c2b324b9540c80cda48d` |
| [PWS-0209 / M2](m2-image-ocr-gate.md) | Done / PASS | `ba6d1268be8e23a6dec521fe03481a81f2d5a717` |
| [PWS-0308 / M3](m3-overlay-gate.md) | Done / PASS | Gate `3ba5e978f20c00dd860f6baa6f2e798d0e8725ea`; full product lanes `da349eb57c117956aec940c6e8a2f54e668eaba2` |

| M4 task | Status | Task evidence revision | Capability |
|---|---|---|---|
| PWS-0401 | Done | `5e0e7101cdd1af9c865fd616903b2d74a23720f4` | Safe declarative template loader and validator |
| PWS-0402 | Done | `8f23200e1e5d6f5fc05b40ff024dae01b1d8b659` | Generic portal application template |
| PWS-0403 | Done | `52ee0a13292ea1b109e9a24b81b6535eb80d513d` | Checklist and user-confirmed assignment |
| PWS-0404 | Done | `64996458677612ab52f7c0444fdeb2c3b92cbced` | Typed preflight rules and exact authority |
| PWS-0405 | Done | `870960c8e32811eb117a73d06826c59ff6521f80` | Completeness, legibility, privacy, and compatibility rules |
| PWS-0406 | Done | `3d36b7368ac76ee2a25c29538a7e6dc19c660bfd` | Portal constraints and safe filenames |
| PWS-0407 | Done | `fa4674de487219e3cf79d619024333588bde2b59` | Combined PDF, separate PDFs, and ZIP planning/publication |
| PWS-0408 | Done | Implementation evidence `0b2694cf5a45018c7cd0d0314cc3f3d56641304d`; maintainer acceptance 2026-09-30 | Desktop package review, summary, and existing first-time/keyboard walkthrough |

The exact acceptance instruction was “PWS-0408 reviewed, and accepted. Please
continue.” The [PWS-0408 acceptance record](../development/pws0408-result.md#maintainer-acceptance)
does not invent an additional tested revision, environment, individual step result,
or NVDA observation from that instruction. Accepted evidence is not repeated solely
to improve test ergonomics.

## Gate criteria

| Criterion | Current result | Evidence and remaining work |
|---|---|---|
| Every M4 prerequisite and earlier gate is done | Pass | Task graph and prerequisite records above |
| First-time user completes the generic packet without another PDF application | Accepted prerequisite | Maintainer accepted the existing PWS-0408 first-time/keyboard walkthrough; full automated suite includes the real-worker package pilot |
| Outputs are verified and constraints reported concretely | Automated evidence passes | Restricted-worker reopen/structure/render checks and package tests pass; summary distinguishes planned and saved facts, unknown checks, warnings, and required human review |
| Permanent redaction absent/disabled; ordinary shapes distinct | Source/automated review passes | Closed overlay kinds reject redaction; ordinary-shape intent and UI descriptions explicitly deny permanent-removal semantics |
| No cloud/account, eligibility, legal-advice, qualified-signature, or direct-submission claim | Source/document review passes | Local workflow, template boundary notice, signature-image wording, package summary, and updated limitations; no positive prohibited claim found in reviewed UI |
| Core actions have keyboard alternatives; routine logs exclude prohibited content | Pass | 69 accessibility and 579 security tests passed; accepted PWS-0408 keyboard prerequisite; fresh agent-operated NVDA/manual record below |
| Full suite and constrained-resource/failure scenarios | Automated evidence passes with disclosed scope | Exact-revision full CI and focused 55-test composite below; seven optional OCR/private-stage integration skips are disclosed |
| Manual signed-input, malformed-input, active cancellation, and recovery scenarios | Pass | Current production desktop, NVDA speech observations, independent output/source checks, and fresh review after cancellation |
| Final gate documentation and changed-file validation | In progress | Gate report, acceptance records, limitations, task graph, and bundle consistency require final review |

## Current full automated evidence

[CI run 36736040250](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36736040250)
passed both canonical quality groups at exact revision
`248c26ed56679b5bd4ba3faadc13c2410347960f` on 2026-09-30. The runner was Windows
Server 2025 Datacenter `10.0.26100`, image `20260922.246.2`, with CPython 3.14.7 x64,
the locked dependency set, copy-owned worker runtime, and verified qpdf 12.3.2.
The workflow runs `python tools/dev.py check --ci-group core` and the corresponding
`--ci-group integration` on separate runners; together they contain the unchanged
canonical check registry.

| Lane | Result | Duration |
|---|---|---|
| Unit | 4,731 passed | 943.39 seconds |
| Integration | 475 passed, 7 skipped | 2,978.65 seconds |
| Security | 579 passed | 288.61 seconds |
| Accessibility | 69 passed | 4.30 seconds |
| **Total** | **5,854 passed, 7 skipped** | Lane durations are not one sequential elapsed measurement |

Other canonical checks passed: formatting and lint; strict typing of 597 files;
import boundaries across 13 layers and 207 source files; schemas; task graph;
Pillow native-runtime admission; documentation; portfolio/bundle consistency;
M1 benchmark schema, PWS-0208 constrained-workload definition, and renderer evidence
validation; synthetic fixture validation; secret scanning; and all five deliberate
quality self-test violations. The workload/schema validators confirm recorded
definitions and evidence; they do not create fresh performance measurements.

Fixture validation covered 46 synthetic fixtures, 15 analytical renderer references,
six approved image-transform references, and three approved overlay references.
Secret scanning covered reachable history, index, and tracked working files with no
nonignored untracked files in that CI snapshot.

The seven integration skips were capability disclosures:

| Skipped coverage | Count | Reason |
|---|---|---|
| PWS-0204 OCR admission, OCR worker, and searchable export worker | 3 | Exact admitted Tesseract runtime not staged |
| PWS-0208 live resource workload | 2 | Private artifact stage absent |
| PWS-0209 mixed searchable gate | 1 | Exact admitted OCR environment absent |
| PWS-0209 quality OCR worker | 1 | Exact admitted Tesseract runtime not staged |

These were not fresh successful OCR executions. Earlier accepted M2 live OCR and
resource evidence retains its original revision/environment. The current complete
suite does include the PDF package pilot and the non-OCR image import/export paths.

Local retained CI logs are `output/pws0201-ci-36736040250-core.log` and
`output/pws0201-ci-36736040250-integration.log`. They are ignored working evidence;
the hosted run above identifies the durable full-suite record.

## Fresh constrained storage and recovery composite

The 2026-09-30 focused composite passed **55 tests, zero skips, in 7.97 seconds**.
It launched no restricted document worker. It combines real Windows destination
handles, injected storage faults, GUI responsiveness checks, and desktop-process
restart recovery. Package orchestration uses the central
`CONSTRAINED_RESOURCE_POLICY_V1`; candidate PDF generation in those orchestration
tests is stubbed. This proves the named boundaries without claiming a new complete
worker-Job or whole-packet memory measurement.

The only test change is three parameterized cases in
`tests/integration/test_package_export.py` for combined PDF, separate PDFs, and ZIP.
Each sets available space to one byte below the first artifact plus the policy's
256 MiB reserve, verifies refusal with no published or temporary artifact, restores
available space, and verifies successful retry with reopened destination receipts.
The production policy and its limits are unchanged.

| Composite coverage | Test source |
|---|---|
| Package staging disk-full, permission, cancellation, deadline, cleanup, and publication receipts | `tests/integration/test_export_package_destination_windows.py` |
| Combined/separate/ZIP ordering, limits, exact partial inventory, ZIP cancellation, and new constrained reserve/retry cases | `tests/integration/test_package_export.py` |
| Summary reserve refusal and failed-write cleanup | `tests/integration/test_package_summary_destination_windows.py` |
| Import copy, thumbnail cache, and PDF destination disk-full cleanup | Selected cases from `test_source_ingest_windows.py`, `test_thumbnail_cache_storage_windows.py`, and `test_export_destination_windows.py` |
| Slow atomic manifest replacement and four slow-I/O GUI responsiveness cases | Selected cases from `test_manifest_repository_windows.py` and `test_organizer_ui_lifecycle.py` |
| Exact private-temp capacity boundary, mid-render/export disk-full cleanup, and fresh retry | Selected cases from `tests/unit/worker/test_private_temp.py`, `test_pdf_thumbnail_worker_adapter.py`, and `test_pdf_export_worker_adapter.py` |
| Cold startup recovery and forced desktop exit during autosave followed by fresh-process recovery | Two cases from `tests/integration/test_desktop_startup_recovery_windows.py` |

The recorded invocation was:

```powershell
$testNodes = Get-Content output/pws0409-composite-20260930/nodes.txt
.venv/Scripts/python.exe -m pytest -q @testNodes --durations=10 --junitxml=output/pws0409-composite-20260930/final-results.xml
```

Ignored evidence in `output/pws0409-composite-20260930/` contains the exact selectors,
`final-focused-nonlive.log`, `final-results.xml`, and `typecheck.log`. At the focused
run, the changed test file had SHA-256
`dc3840ae53f5151fb2447972b79f8f6d1b135af2988c81069c61b9c22d8e7ba0`;
`nodes.txt` had SHA-256
`eaa92f593dc9afe3812ef6a9439c725bd54827aa6eff0a1bdb3045a063746f80`;
the final JUnit record had SHA-256
`cde62ce73e123e1c0e8acf41b9dea6cfa5f58675a136cb4623b96d4b5c173175`.
Canonical typecheck passed for 597 files, and changed-file Ruff format/check and
`git diff --check` passed.

## Desktop and accessibility observations

The accepted PWS-0408 walkthrough is the first-time-user and keyboard prerequisite.
The [current desktop/NVDA record](../accessibility/pws-0409-pilot-nvda-smoke.md)
records explicit signed-source consent, two malformed-input boundaries, active
cancellation, clean restart/reopen, and a verified export after failure. NVDA read
review/consent states, progress, completion, summary saving, cancellation, and
recovery instructions. The full dependency closure produced one four-page,
3,545-byte unsigned derivative with clean qpdf, every-page render/order/rotation,
and saved-byte checks. Independent reopening/rendering and source-hash comparisons
passed; no original changed.

The current run used Windows 11 Pro 10.0.26200, CPython 3.14.6, Qt 6.11.1,
NVDA 2026.1.1, the constrained policy, and copy-owned locked worker dependencies.
An initial incomplete staging setup omitted the overlay dependency closure and
failed before worker readiness. It was corrected and the export/cancellation
checks repeated with the package integration harness's complete dependency set.
This was a test-setup correction, with no production changes or weaker controls.

Cancellation kept the dialog open until the terminal result. Its conservative
message did not certify the destination; inspection confirmed the destination was
empty. A fresh review reopened with four pages, persisted confirmed assignments,
and a new human acknowledgement required. Forced process-exit/autosave recovery
is separately automated in the 55-test composite. No manual crash is claimed.

Earlier M1/M2/M3 keyboard and NVDA records remain historical evidence. Neither
automated widget tests nor an agent-operated speech trace establishes human
screen-reader certification, universal usability, PDF/UA, or tagged-output reading
order.

## Product boundaries and known limitations

The audit inspected the product UI, built-in template, overlay model/schema, package
summary, README, limitations, and release sheet:

- The overlay kind set has no redaction operation. Ordinary shapes carry fixed
  `visual_overlay_only_not_permanent_removal` intent. The overlay editor and preflight
  describe them as visual marks, and security tests reject redaction claims.
- Signature-image placement remains a visual mark. Signed inputs require explicit
  consent before modified unsigned derivatives; the application does not validate
  or create cryptographic or qualified signatures.
- The template and package review make packaging checks only. They do not decide
  eligibility, give legal advice, verify identity, submit to a portal, or guarantee
  acceptance. The source-tree workflow requires no cloud service or product account.
- Package review does not rerun full source inspection, page geometry, or advisory
  quality analysis. Unavailable facts remain unverified after acknowledgement.
- Scan/image processing and active overlays in one PDF group are rejected. Package
  results and unsaved summaries are transient; users can explicitly save a text
  companion. No optional technical manifest or portable case-transfer format exists.
- Image formats, profiles, bit depth, output geometry, development startup delays,
  and resource ceilings remain documented in [KNOWN_LIMITATIONS.md](../../KNOWN_LIMITATIONS.md)
  and [LOW_SPEC.md](../../LOW_SPEC.md).

M5 owns later compression/compatibility work, M6 owns permanent redaction, M7 owns
the complete accessibility/localization/assisted-mode gate, and M8 owns packaged
release hardening. No hardware, legal, portal-acceptance, secure-erasure, PDF/UA,
packaged-release, or ThirdLife compatibility claim follows from this source-tree gate.

## Changed evidence and final validation boundary

No product runtime, dependency, schema, resource policy, worker security control,
or output verification behavior changed during this gate review. Current changes
record PWS-0408 acceptance, clarify limitations and release wording, add the three
reserve/retry cases, and assemble gate evidence. The test change passed the focused
composite and canonical typing; the acceptance-document update passed the registered
documentation validator. Final gate-report, task-graph, bundle, and clean-tree
validation remain to be recorded.

The full CI result belongs to `248c26e`, before these test/documentation changes.
It is not labeled as a full-suite run on the eventual gate closure commit.

## Portfolio boundary and release interface

PaperWorkShell remains standalone. Cases, originals, intent, signatures, and explicit
local derivatives remain owned by PaperWorkShell and the user. No sibling repository,
shared runtime, database, adapter, server, or synchronized release is required.
The release sheet records only the accepted source-tree summary workflow; exact
packaged facts remain `TBD`. Developer pilot commands are not a public application
CLI contract. No new ThirdLife interface or compatibility cut is introduced.

## Gate conclusion and next step

**IN PROGRESS.** Prerequisites are done; current full automated evidence and the
focused constrained composite pass within their disclosed scopes. Required current
desktop observations and final evidence validation remain open. PWS-0409 must not
be marked done or presented as v0.1 approval until those items are reviewed.

After a passing PWS-0409 closure, PWS-0501 is the next dependency-ready task under
the repository selection order. It remains dependent on this gate while the gate
is in progress.

## Focused composite selectors

The complete reusable selector inventory is:

```text
tests/integration/test_export_package_destination_windows.py
tests/integration/test_package_export.py
tests/integration/test_package_summary_destination_windows.py
tests/integration/test_source_ingest_windows.py::test_disk_full_during_import_copy_removes_partial_stage_and_preserves_state
tests/integration/test_thumbnail_cache_storage_windows.py::test_disk_full_during_thumbnail_write_cleans_partial_stage_and_restart_is_clean
tests/integration/test_export_destination_windows.py::test_disk_full_after_prefix_removes_only_the_exact_stage
tests/integration/test_manifest_repository_windows.py::test_slow_atomic_rename_keeps_prior_manifest_authoritative_until_commit
tests/integration/test_organizer_ui_lifecycle.py::test_artificially_slow_storage_flush_does_not_block_the_gui_event_loop
tests/integration/test_organizer_ui_lifecycle.py::test_slow_storage_import_keeps_progress_visible_and_cancellation_available
tests/integration/test_organizer_ui_lifecycle.py::test_artificially_slow_storage_read_does_not_block_the_gui_event_loop
tests/integration/test_organizer_ui_lifecycle.py::test_artificially_slow_directory_scan_does_not_block_the_gui_event_loop
tests/unit/worker/test_private_temp.py::test_capacity_preflight_accepts_exact_boundary_and_rejects_one_byte_less
tests/unit/worker/test_pdf_thumbnail_worker_adapter.py::test_mid_render_private_disk_full_cleans_partial_and_fresh_retry_recovers
tests/unit/worker/test_pdf_export_worker_adapter.py::test_mid_export_private_disk_full_removes_partial_and_fresh_retry_recovers
tests/integration/test_desktop_startup_recovery_windows.py::test_cold_desktop_restart_recovers_before_worker_composition
tests/integration/test_desktop_startup_recovery_windows.py::test_forced_ui_exit_during_autosave_recovers_in_fresh_desktop_process
```
