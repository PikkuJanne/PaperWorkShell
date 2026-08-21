# Milestone 2 image and OCR gate - PWS-0209

- Review status: Complete
- Gate conclusion: PASS
- Review date: 2026-08-21
- Evidence revision: `ba6d1268be8e23a6dec521fe03481a81f2d5a717`
- Review branch: `codex/pws-0209-m2-gate`
- Frozen decisions: PWS-D007, PWS-D013, PWS-D016, PWS-D021, PWS-D026, PWS-D030,
  PWS-D041

## Scope and decision rule

This gate reviews Milestone 2 from content-detected JPEG/PNG intake through provisional scan
adjustments, offline Tesseract admission, bounded OCR, mixed searchable-PDF export, advisory page
quality review, signed-source protection, cancellation, and constrained-resource cleanup. It
passes only when every PWS-0201 through PWS-0208 prerequisite is done, the gate-owned mixed and
quality-OCR scenarios pass with the admitted local runtime, the complete test lanes and registered
quality contracts pass, the exact constrained signed-source workload passes, and the production UI
manual smoke records truthful keyboard/NVDA behavior.

PWS-D041 makes the central constrained software policy the hardware-independent gate. The results
below prove bounded behavior for the recorded revision and synthetic workloads; they do not emulate
or certify a named CPU, RAM amount, GPU, SSD, HDD, VM, or physical device.

The desktop exposes image import, offline-language review, scan-adjustment review, and page-quality
review. Searchable export remains an application/worker service rather than a desktop action;
PWS-0408 owns the guided export workflow. The agent-operated UI/NVDA smoke and the headless
production export evidence are therefore recorded separately and are not presented as one manual
end-to-end export interaction.

Authoritative sources are [ROADMAP.md](../../ROADMAP.md), [TASKS.yaml](../../TASKS.yaml),
[DECISIONS.md](../../DECISIONS.md), the
[low-resource policy](../performance/LOW_RESOURCE_POLICY.md), the
[OCR worker contract](../architecture/ocr-worker.md), the
[searchable mixed-output contract](../architecture/searchable-mixed-output.md), and the
[PWS-0209 NVDA smoke](../accessibility/pws-0209-m2-nvda-smoke.md).

## Reviewed environment

| Item | Observation | Gate interpretation |
|---|---|---|
| Operating system | Windows 11 build 26200, x86-64 | Supported development and manual-smoke host |
| Python | CPython 3.14.6, 64-bit | Supported pinned minor |
| Qt UI | PySide6-Essentials/Qt 6.11.1 | Selected production widget runtime |
| Screen reader | NVDA 2026.1.1 | Agent-operated speech-trace evidence; no human certification claim |
| OCR | Tesseract 5.5.3, English data, exact 39-file/27,588,734-byte admitted stage | Local/offline runtime used by gate-owned and constrained workloads |
| PDF engines | qpdf 12.3.2, OCRmyPDF 17.10.0, pikepdf 10.10.0, pypdfium2 5.12.1/PDFium 152.0.7947.0 | Pinned worker/export and independent verification stack |
| Resource policy | Version 1, constrained profile | Numeric limits and observations are mandatory |
| Physical low-end device | Not tested | Optional/non-gating; no hardware-equivalence claim |

## Prerequisite review

| Task | Status | Capability reviewed |
|---|---|---|
| PWS-0201 | Done | Content-detected immutable JPEG/PNG intake and bounded decode |
| PWS-0202 | Done | Deterministic provisional scan transforms |
| PWS-0203 | Done | Accessible before/after scan-review workflow |
| PWS-0204 | Done | Restricted local OCR and searchable-image PDF path |
| PWS-0205 | Done | Verified offline language-pack management |
| PWS-0206 | Done | Mixed born-digital/raster searchable export and verification |
| PWS-0207 | Done | Revision-bound advisory quality and OCR-review findings |
| PWS-0208 | Done | Signed-source confirmation, unsigned labeling, incremental scheduling, and measured resources |

## Gate criteria

| Criterion | Result | Evidence |
|---|---|---|
| Every M2 prerequisite is done | Pass | PWS-0201 through PWS-0208 are `done` |
| OCR does not block the UI and can be cancelled safely | Pass | Production UI remained responsive, Escape announced requested/safe cancellation, no result was applied, controls recovered, and the live quality worker cancelled after start then retried successfully |
| Mixed born-digital/scanned output is verified and searchable where reported | Pass | Gate-owned four-page PDF/JPEG/PNG scenario reported one existing-text page and three OCR-verified pages; all pages had extractable text and seven export checks passed |
| Signed inputs are protected and edited derivatives are clearly unsigned | Pass | Source-scoped confirmation remains mandatory; the constrained signed-marker scenario preserved originals and produced a clearly unsigned derivative |
| Quality findings remain review signals | Pass | The UI and NVDA reported `Needs review`, called results automatic suggestions, and explicitly said they do not complete a requirement |
| OCR workloads meet constrained budgets or fail safely | Pass | Exact report recorded bounded success, cancellation, correlated one-byte limit refusal, cleanup, and retry under policy v1 |
| Keyboard and enlarged-layout review is complete | Pass | Production JPEG/language/scan/quality workflow was keyboard-operated at effective approximately 200-percent Qt scale with NVDA speech evidence and no unreachable control |

## Gate-owned scenarios

### Mixed searchable export

The production scenario creates a random case, imports a synthetic two-page PDF plus deterministic
JPEG and PNG sources, and exports four pages through the restricted searchable-PDF worker. It
requires:

1. one `EXISTING_TEXT_PRESERVED` result and three `OCR_TEXT_VERIFIED` results;
2. extractable text on all four pages without claiming OCR accuracy;
3. strict reopen, qpdf structure, page count, unencrypted state, all-page render,
   order/rotation comparison, and destination-reopen checks;
4. unchanged external and case originals;
5. only the expected export-history manifest mutation;
6. complete worker-private-temp measurement, empty case temp/retry state, worker-tree termination,
   and content-free reports/progress/history.

The scenario passed on exact review revision `ba6d1268be8e23a6dec521fe03481a81f2d5a717`.

### Quality OCR cancellation and retry

The production quality-review composition receives one bounded synthetic RGBA sample. It returns
only recognized-word count and mean-confidence aggregates, never OCR text or geometry. The gate
requires an initial success, cancellation after worker start, zero active/waiting heavy operations,
clean closure, and a deterministic fresh success on the same revision/sample. The exact-revision
scenario passed.

The gate fixed one installed-runtime compatibility defect discovered by the live path. The admitted
Tesseract stage contains `pdf`, `hocr`, and `txt` configs but no `tsv` config. The previous final
`tsv` argument therefore exited successfully with plain text, which the strict TSV parser correctly
rejected. The operation now requests the built-in parameter
`-c tessedit_create_tsv=1`; the runner allowlist accepts only that exact private-PPM/stdout shape.
Unit, integration, and privacy tests bind the nine-argument invocation and bounded output behavior.

## Searchability, signature, and export evidence

The four-page gate-owned output passed `PDF_REOPENED`, `STRUCTURE_CLEAN`, `PAGE_COUNT_MATCHED`,
`UNENCRYPTED`, `ALL_PAGES_RENDERED`, `ORDER_ROTATION_MATCHED`, and
`OUTPUT_COMMIT_REOPENED`. Its born-digital page render matched the source render, and every output
page reopened and rendered through PDFium.

The separately measured signed-source workload contains five born-digital/raster pairs, one
standalone raster page, and one conservative unsigned `/FT /Sig` marker page. It produced a
128,181-byte **unsigned derivative**, SHA-256
`63fe1afb3b5213ceb2605764748c6ffb88719fb6f9f8d370f9bdd76c99b67365`.
qpdf reported no syntax/stream errors, `pdfinfo` reported 12 unencrypted Letter pages, and Poppler
rendered every page. Visual inspection found legible expected content and geometry with no clipping,
overlap, blank corruption, or unreadable page. The marker fixture is not a valid cryptographic
signature and no legal-signature claim is made.

## Failure, recovery, privacy, and accessibility evidence

| Path | Result |
|---|---|
| Missing local language | English was installed/verified; German remained unchecked/not installed with local-only offline remediation and no download |
| Scan-preview cancellation | Requested while work was active; no adjustment applied, previews reported cancelled/not produced, and controls recovered |
| Quality-review cancellation | Requested while active; no snapshot applied, cleanup completed, and retry on the same revision succeeded |
| Resource refusal | Exact correlated `PDF_EXPORT_LIMIT_EXCEEDED`; no destination/history publication and complete cleanup |
| Signed-source modification | Source-scoped confirmation required; missing/stale/wrong confirmation fails closed; original retained |
| Routine privacy | Gate models/reports/errors/progress/history reject OCR/document text, display names, paths, content digests, and raw native failures |
| Worker cleanup | Complete tree termination confirmed after cancellation/limit; persistent ACL-recovery ledger empty after automated and manual runs |
| UI announcements | NVDA trace included progress, cancellation requested, safe completion/cancellation, retry availability, and advisory finding wording |

The manual record is [PWS-0209 Milestone 2 keyboard and NVDA smoke](../accessibility/pws-0209-m2-nvda-smoke.md).

## Constrained resource evidence

The exact report `PWS-0208-resource-evidence.json`, SHA-256
`02fcc57495919ff0102c9bd10b7c3d41fd948c6879f7c8e852e07748f81d7619`, binds review revision
`ba6d1268be8e23a6dec521fe03481a81f2d5a717`, workload SHA-256
`ce04fcca1b67fa212d790ddda5074baf82f2d55e8212fc5467f879fbab8dd70a`, policy v1,
and an overall pass.

| Budget/evidence | Observed | Limit/result |
|---|---:|---:|
| Complete process-tree peak RSS | 330,903,552 bytes | 1,342,177,280 bytes - Pass |
| Worker-process peak commit | 103,374,848 bytes | 536,870,912 bytes - Pass |
| Worker-Job peak commit | 123,486,208 bytes | 805,306,368 bytes - Pass |
| Worker-private-temp peak | 740,444 bytes, 1,200 complete samples at 50 ms | 536,870,912 bytes - Pass |
| Active full-resolution page buffers | 1 | 1 - Pass |
| Active page results | 1 | 1 - Pass |
| Heavy-operation concurrency | 1 | 1 - Pass |
| Cancellation acknowledgement | 13 ms | 2,000 ms - Pass |

The fixed run completed success, page-boundary cancellation, correlated limit refusal, cleanup, and
retry in 204,075 ms. Duration is record-only. It proves bounded software behavior for this exact
synthetic workload, not performance or support on a named physical device.

## Automated evidence

| Command | Result |
|---|---|
| `tools/dev.py format-check`, `lint`, `check-import-boundaries`, `typecheck` | Pass; 434 files formatted/linted/typed and 13 layers across 155 source files valid |
| `tools/dev.py test-unit` | Pass; 3,530 tests on `ba6d126` |
| `tools/dev.py test-integration` | Pass; 394 tests with 4 disclosed link/private-stage skips on `9095ae3`; the only later change was the corrected security assertion, and both gate-owned live tests then passed again on `ba6d126` |
| `tools/dev.py test-security` | Pass; 353 tests with 6 disclosed Windows link-capability skips on `ba6d126` |
| `tools/dev.py test-accessibility` | Pass; 39 tests on `ba6d126` |
| `tools/dev.py run-pws0208-resource-workload` | Pass on clean exact revision `ba6d126`; executes the ordinary lane's two private-stage skips |
| Registered fixture/schema/task/docs/portfolio/dependency/evidence/renderer/secret/self-test validators | Pass; 45 deterministic fixtures, 15 renderer references, 6 transform references, 2,066 historical blobs, and all 5 deliberate violations checked |
| qpdf, pdfinfo, and all-page Poppler render inspection | Pass; 12 unencrypted pages, structurally clean and visually intact |

## Known limitations and claims not made

- The desktop does not yet expose searchable export as a window action; PWS-0408 owns it.
- The manual smoke was agent-operated and is not human screen-reader certification.
- The enlarged run used effective Qt scaling, not an actual 200-percent Windows setting.
- The synthetic evaluation intentionally retains documented quality false positives/negatives; it
  is a regression baseline, not OCR or heuristic accuracy certification.
- No PDF/UA, OCR certainty, legal-signature validity, portal acceptance, physical-hardware
  performance, secure erasure, or release-package claim is made.

## Portfolio boundary and release interface

Milestone 2 remains independently useful and fully operable as standalone PaperWorkShell. Cases,
originals, OCR data, review findings, derivatives, and manifests remain locally owned by
PaperWorkShell/the user. No sibling repository, shared runtime, service, schema, database, adapter,
release synchronization, or app-to-app interface was introduced. The human-readable release
interface remains unchanged, and no ThirdLife future note is warranted.

## Gate conclusion

**PASS.** Every prerequisite is done. Installed local OCR is admitted offline, restricted,
cancelable, content-free at public boundaries, and recoverable. The mixed output is searchable only
where independently verified, signed-source modifications remain explicit unsigned derivatives,
quality signals remain advisory, constrained resource/failure paths close safely, and the complete
automated plus recorded manual evidence satisfies PWS-0209 without broadening product claims.
