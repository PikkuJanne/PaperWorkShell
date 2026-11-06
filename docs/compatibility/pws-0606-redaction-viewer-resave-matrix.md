# Current release-profile qualification notice

Approved source `7e2ea7ae1fd3eaf6a7882fc5f236f101e7424ab39ac57fd19dbca132eb9a44c9`: all five retained slots PASS in epoch `56ed5f2a9df780d3f7159cb3902840d00a27f5a69e34e4e2ad243e71ac057c47`. Receipts: `output/pws0608-repair/verified-viewer-status-v3.json`, `output/pws0608-repair/verified-viewer-inspection.json`, `output/pws0608-repair/ocr-noninterference-attestation-v1-01.json`. No original observation, artifact, environment fact or historical result below is rewritten or repeated. Candidate viewer testimony remains UNKNOWN; this is implementation/profile qualification. The prior current-gate notice below is historical.

Final metadata checks are recorded externally in output/pws0608-repair/final-files.json and output/pws0608-repair/final-closure-validation.json. This report does not claim outcomes for checks not yet run and does not embed their downstream digests: the inventory and receipt hash this report. Publication requires successful schema, task-graph, documentation, portfolio, full secret-scan and Git whitespace checks with unchanged code and Git identities.

## Retained observations and prior notice — preserved verbatim

The original successful reader observations remain accepted. The old failed-gate notice records its then-current context, not a reversal of those observations.

# PWS-0606 redaction viewer and save/resave matrix

## Scope and present evidence

**Current gate notice — 1 November 2026:** [PWS-0608](../gates/m6-permanent-redaction-gate.md)
failed independent release review. The accepted human observations, actual saved
files and exact-file OCR bindings below remain intact. Current automated viewer
inspection is stale: status exits one with a generation-provenance mismatch.
The historical method receipts below are not current approval or evidence for
new composed candidates. Preserve the completed session; no human replay is requested.

**Human observations: complete in all five readers on 17 October 2026.
Permanent redaction remains disabled.** Automated
verification, independent native OCR, and alternate renderer tests do not count as
human observations or as PWS-0608 independent approval. Missing, failed, stale, or
unsupported observations remain unverified and block release-method completeness.

The inspection compatibility follow-up preserves the completed observations and
the exact ten saved files, including their existing independent OCR evidence.
The strict canonical export profile remains unchanged. A separate explicit viewer
profile can inspect the original Adobe envelope and its inert bookkeeping;
current automated outcomes must come from new bound receipts, not from rewriting
the files or relabeling their historical failures.

The completed session had 45 checkpoints across five readers. The maintainer
reported that close to 40 steps was difficult and rejected that total burden as
human friendly. Preserve these completed observations. This procedure is retained
as evidence of what ran; it must not be copied into a future human session without
reducing the actual workload under the [human-testing policy](../development/human-testing.md).

The [M6 threat model](../security/redaction-threat-model.md) defines the supported
whole-page raster reconstruction and its limits. This matrix tests the new
derivative and a separate intentionally unsafe control. It makes no claim about
deleting originals, forensic erasure, OCR certainty, accessibility structure,
PDF/UA, portal acceptance, or cryptographic signatures. An edited signed source
can produce only an unsigned derivative.

## One stable entry point

Paste this complete line in PowerShell from any directory:

```powershell
uv --directory 'C:\projects\PaperWorkShell' run --locked python tools/dev.py pws0606-redaction-test
```

This command now shows fast, noninteractive status of the completed synthetic
session through `tools.pws0606_viewer_resave`. It does not start another human
test, regenerate the packet, or ask for versions, paths, hashes or JSON edits.
The original recording helper is pinned so its accepted observations retain their
exact guidance provenance. Its completed procedure is documented below as
historical evidence, not a request to run it again.

Reader and OS versions are reused from the accepted **16 October 2026** PWS-0506
test, which the maintainer confirmed unchanged on **17 October 2026**. The
[environment record](../development/human-test-environment-v1.json) retains those
facts and source hashes. The completed observations bind to that reused metadata;
no version prompt or reconfirmation is required. Previous test results were not copied. See
[the policy for all future human tests](../development/human-testing.md).

Codex runs `python -m tools.pws0606_viewer_resave verify` for the separate automated
recheck; `python -m tools.pws0606_viewer_resave status` is its direct fast-status
equivalent. The checker preserves the preparation and all earlier attempts and
receipts, validates unchanged generation and observation bindings, rechecks the
packet under the strict canonical profile, then appends viewer-profile receipts
under `output/pws0606-verification-v1/viewer-verification/`. Expensive PDF checks
run only in `verify`; status validates existing receipts and exact hashes.
Independent OCR is separate evidence on every actual saved and resaved file.
A completed human step is not an automated pass. The developer
harness is separate from the product's document-processing UI and does not enable
redaction, publish an export, accept user documents, or start a network server.

| Symptom | Recovery |
|---|---|
| PowerShell shows `>>` | Press `Ctrl+C` and paste the complete single line, without Markdown fences or prompt characters. |
| An `ERROR` code appears | Preserve the workspace and report the bracketed code. Codex inspects it; do not delete artifacts or edit evidence to force a pass. |
| Status is pending or a receipt is stale | Codex runs the separate verification mode. Preserve the completed human observations; no human replay is needed for inspection-only implementation changes. |
| A file, generation input, probe plan or observation has changed | Preserve earlier artifacts and records. Codex identifies the changed binding and affected checks. Earlier evidence cannot silently apply to changed content or behavior. |
| A saved file is missing | Preserve all remaining copies and records. Codex recovers the exact accepted artifact where available; do not regenerate it over the recorded identity. |
| Automated inspection rejects a reader's output | Preserve the original file and failed receipt. Diagnose its original bytes; do not sanitize an unsupported output into a passing substitute. |

## Packet and controls

Preparation runs the production reconstruction and sanitizer adapters with the
central constrained resource policy, followed by the independent verifier. The
trusted reference is built from those exact reconstructed pages. Verification
checks that the final candidate and its full-save/resave preserve the reference's
pixels, text, geometry, and extracted image payloads while excluding prohibited
features and original affected-page payload fingerprints.

Original image/Form payloads and nontrivial content streams are fingerprinted even
when their values are not known canaries. A raster-only `q`/matrix/`Do` placement
stream can be the same before and after reconstruction; its independently
fingerprinted image payload carries the protected content. No source payload is
excused merely because a token search did not recognize it.

The prepared files are:

- `packet.pdf`: 16 sanitized pages containing every accepted corpus case.
- `reference.pdf`: the exact pre-final-sanitizer reference, used by automated
  comparisons; no additional human review is required for this oracle.
- `unsafe-cover.pdf`: **intentional failure control**, with a black visual cover
  and recoverable source text. Never treat it as a redacted derivative.
- `evidence.json`: source inventory and page map, code provenance, exact artifact
  sizes/SHA-256 values, method results, and initially NOT RUN manual slots.
- `resaves/W1.pdf` through `resaves/A1.pdf`: the actual copies saved by the named
  viewers, supplied during their respective slots.
- `resaves/W1-resaved.pdf` through `resaves/A1-resaved.pdf`: the second copies
  saved by those same viewers after reopening and reviewing their first copies.
- `attempts/`: exclusive numbered human observations bound to the evidence digest
  and to both exact saved-file and resaved-file digests.

The helper preserves accepted records and checkpoints. Improvements to its menus
must not overwrite earlier PDFs or evidence, hide a generation/probe-plan change,
or require accepted observations to be repeated solely for better ergonomics.

| Packet page(s) | Source carrier covered by the automated corpus | Visible preservation control |
|---|---|---|
| 1 | Annotation/comment and alternate appearance | `PWSKEEPANNOT` |
| 2 | Embedded attachment removed globally | `PWSKEEPFILE` |
| 3 | Born-digital text | `PWSKEEPTEXT` |
| 4 | Embedded raster image | `PWSKEEPIMAGE` |
| 5 | Form value/widget/appearance | `PWSKEEPFORM` |
| 6–9 | Cropped pages at intrinsic 0/90/180/270 degrees | `PWSKEEPGEOZERO/ONE/TWO/THREE` |
| 10 | Hidden optional-content layer | `PWSKEEPLAYER` |
| 11 | Invisible OCR plus raster source | `PWSKEEPOCR` |
| 12 | Real earlier incremental revision | `PWSKEEPREVISION` |
| 13 | Info and XMP metadata removed globally | `PWSKEEPMETA` |
| 14 | Scanned raster text | `PWSKEEPSCAN` |
| 15 | Unsafe-cover source reconstructed into an actual sanitized raster | `PWSKEEPCOVER` |
| 16 | PNG normalized by the admitted image/raster adapters | `PWSKEEPPNG` |

The malformed fourteenth corpus class is rejected separately and is absent from
the packet. Source positive controls prove that all original canaries are
recoverable through their stated carriers. Untreated sources and intentionally
unsafe candidates must fail verification. A blank derivative cannot pass the
independent visible-preservation checks.

Affected pages in this packet have no regenerated searchable OCR layer. Their
outside-mask keep controls remain visibly legible. Pages 2 and 13 retain ordinary
searchable text and provide search/copy positive controls. Loss of selectable text
on reconstructed pages is an explicit raster-method limitation. Separate native
OCR tests verify recognition from the actual sanitized pixels before and after
resave; recognition remains uncertain and does not replace extraction tests.

## Completed human procedure for every named reader

The following records the session already performed. Its 45 checkpoints are not
approved as a template for future sessions and must not be repeated for this
inspection compatibility change.

1. Select its slot and use the named reader configuration below. Its known version
   is supplied automatically from the environment record. No About-screen visit,
   numeric entry, account, profile name, username, private path, or device serial is
   needed.
2. Open `unsafe-cover.pdf` locally. Set **whole-word matching off**. Use `C` where
   offered to copy the single query `PWSRED`, then paste it into the reader's search
   box. The covered synthetic target must be found.
   Select/copy the covered text into an unsaved local text view and confirm that
   the same synthetic value is recoverable despite the black rectangle. Close
   the text view without saving. This is the intentional failure control, and a
   reader that cannot perform the positive control has an unknown search/copy
   result. Close the unsafe control before inspecting the candidate.
3. Open `packet.pdf`, confirm 16 pages, and visit all pages in order and back to
   page 1. Use a comfortable, readable zoom; enlarge further only where needed.
   Confirm each listed keep control remains visible, geometry/orientation and crop
   are correct, and each intended covered region is opaque. Hidden-only and
   document-wide carriers may have no newly visible rectangle.
4. Keep **whole-word matching off** and search `PWSRED` first. It must have no
   matches. The helper permits this shared query only after checking that every
   corpus canary begins with `PWSRED`; there is no list of values to transcribe.
   Next search `PWSKEEP`; use `K` to copy that query where offered. The visible
   positive controls on pages 2 and 13 must be found. This step checks search
   matches; actual document copying occurs in the next step.
5. Immediately before the negative copy check, copy the actual full `PWSKEEPFILE`
   or `PWSKEEPMETA` text **from the document** into a fresh unsaved local text view.
   Confirm it is the full visible control, not merely the search query. Do this
   positive-copy/benign priming action once whenever this negative-copy step is
   entered, including on resume. Then, on every affected page, attempt
   selection/copy across each mask, and also use Select All/Copy. Paste into a
   fresh unsaved local text view; no `PWSRED` target may be recovered. A failed
   copy can leave the previous clipboard value unchanged. The benign control
   prevents an old search query or unsafe-control value from being mistaken for
   recovered document content. Do not copy the search query again between priming
   and this check. The helper never reads the clipboard. Resuming this step does
   not require accepted page review to be repeated. Close the text view without
   saving. Merely seeing a black rectangle does not pass the check.
6. Inspect the reader's layers, attachments, comments, form, and document-property
   panels where exposed. No original layer, attachment, comment, source form value,
   source metadata, script, or action may remain. Try the covered regions with the
   ordinary selection tool; no hidden field/action or alternate appearance may
   activate. Absence of a panel is recorded only as unavailable; the independent
   structural verifier supplies those internal-carrier checks. The reader's own
   menus and tools are not document interactivity.
7. Use the reader's ordinary **Save As/Save a copy** to `resaves/<slot>.pdf`.
   Use the helper's explicit Copy action to paste the fixed path, or Open folder
   to find the prepared location. Do not type paths, print to PDF, rasterize in
   another program, reprocess through PaperWorkShell, or copy the original file
   with the filesystem.
   Close the original and reopen the saved copy in the same named reader. Repeat
   steps 3–6 on that copy, in the same order. If a viewer has no save-copy operation,
   leave its save/resave observation unknown instead of substituting another viewer.
8. With that first saved copy open in the **same named reader**, use its ordinary
   **Save As/Save a copy** again, this time to `resaves/<slot>-resaved.pdf` as printed
   by the helper. Preserve both files. Close the first saved copy, open this second
   saved copy in that reader, and repeat steps 3–6 in the same order. The verifier's
   own pikepdf full save is a separate automated check and cannot supply either
   human-operated save-copy step.
9. Confirm the displayed human steps as they are completed. The helper retains
   those observations and both files' bindings without running expensive PDF
   processing in the recording loop. Fast status distinguishes human progress
   from pending automated evidence. A problem or missing file is not a pass.
10. After the human session, Codex runs the separate automated verification mode.
   It must independently reopen **both** actual viewer-saved files against the
   trusted reference, repeat extraction, image/object/revision/feature checks,
   rendering and a fresh full save for each, and retain receipts bound to the exact
   files and processing. An unchanged-content automated proof may be reused when
   all of its file/reference/probe/processor/engine bindings remain valid. The
   helper separately validates the current human observation and environment and
   binds them to that proof before deriving combined status; metadata alone does
   not require another PDF run. Codex must also perform and record
   applicable independent OCR on **every actual saved and resaved file**, bound to
   its exact digest and supported source-positive controls. This includes both
   files from all five slots. Human observations and built-in checks do not supply
   independent OCR evidence. Finishing the menu alone does not complete PWS-0606;
   unknown or failed OCR keeps the task in review.

For A1, use an ordinary local transfer such as USB. Transfer only these synthetic
files and return both actual saved copies to their printed paths. Do not use
personal documents or a cloud share. A changed input transfer must be corrected
before review. No real document or clipboard content belongs in the evidence.

## Accepted human matrix and historical automated results

| Slot | Required configuration | Reader/OS version | Original review | First saved-copy review | Second saved-copy review | Historical canonical PDF checks of both files | Independent OCR of both actual files | New viewer-profile recheck |
|---|---|---|---|---|---|---|---|---|
| W1 | Adobe desktop Reader/free/reduced mode | 2026.002 / 10.0.26300 | PASS | PASS | PASS | FAIL — unsupported Adobe envelope/carriers | PASS | PASS — both exact files |
| W2 | Microsoft Edge built-in PDF viewer | 155.0.4283.45 / 10.0.26300 | PASS | PASS | PASS | PASS | PASS | PASS — both exact files |
| W3 | Google Chrome built-in PDF viewer | 155.0.8059.80 / 10.0.26300 | PASS | PASS | PASS | PASS | PASS | PASS — both exact files |
| W4 | Mozilla Firefox built-in PDF viewer | 157.0.1 / 10.0.26300 | PASS | PASS | PASS | PASS | PASS | PASS — both exact files |
| A1 | Adobe Acrobat Reader for Android | 26.9.2.50367 / 16 | PASS | PASS | PASS | FAIL — unsupported Adobe envelope/carriers | PASS | PASS — both exact files |

W1 excludes paid Standard/Pro and unconfirmed modes. W2–W4 exclude delegated
extensions. A1 is a representative output-reader observation, not support for
running PaperWorkShell on Android. No macOS compatibility claim is introduced.

The latest attempt for every slot must pass against one unchanged evidence digest
and both saved copies' current digests. Independent OCR of all ten actual files
must also pass and be recorded separately. Codex inspects those records before this
tracked report can be updated. An automated renderer result cannot populate a
human slot. Even a completed matrix supplies only method evidence; it cannot
approve PWS-0608, authorize publication, or enable permanent redaction.

## Executed evidence — 17 October 2026

All five human observations pass their exact artifact, helper, processing, query
and environment bindings. The original evidence SHA-256 is
`63573f59e128d52c83ff1bfc34f1da0f4c39d1097181624e4a145d35721194f1`;
the unchanged helper SHA-256 is
`3e5b01954745fb1611a2eec35a4bdb6e1afebda76296caa7462dd1f0eb682381`.
No completed human observation needs an ergonomic retest.

The historical canonical-profile run passed six actual browser copies. Four Adobe
desktop and Android copies failed closed with `RV_STRUCTURE_UNSUPPORTED` and resave
`RV_FEATURE_RETAINED`: Adobe adds a linearized envelope, `/Prev`, Info dates and
XMP outside the supported profile. The envelope diagnosis demonstrated no
canary recovery. A diagnostic rewrite cannot certify the original saved file.
Those four failures remain recorded as the original automatic-method
compatibility diagnosis, separate from the completed human observations. They
are not deleted or relabeled by the viewer-profile follow-up.

Independent native OCR passes all ten actual artifacts: three exact-byte content
proofs cover 48 content-pages and bind to 160 artifact-pages. Fourteen visually
applicable canary source controls are recognized exactly, with fixed matching
preprocessing on the actual output; attachment/Info/XMP canaries are explicitly
nonvisual OCR cases assigned to structural methods. All sixteen outside controls
per content are recognized; no removal-prefix/full-canary text is recovered.
This does not override failed structural methods or approve PWS-0608.

Count/hash-only receipts are under `output/pws0606-human-complete/`:
`human-observation-audit.json`, `builtin-results.json` and
`saved-file-ocr-v1.json`. The OCR receipt SHA-256 is
`318bd196d643bdaac98be46b025c70d69ec511a6b39677f5aa70f60f0f7749d7`.
The exact native run used 158 bounded calls in 45.322 seconds and rechecked its
input, implementation, environment and admitted runtime identities. Temporary
files were cleaned; source and actual saved PDFs remain unchanged.

| Slot | Human attempt SHA-256 | First actual save SHA-256 | Second actual save SHA-256 |
|---|---|---|---|
| W1 | d0a3d481e4bcd0a9976af2ee98b008c34c4cd412c308a998888801faffa0c1cc | 65ddf940b1b09b213c98c44b620d724c21a2328dba1d6893f12eae4610ca927c | 2156aaa54e9302dd3e59ab8245559d833a95db974057d1aa9ab6dcaa318ea44b |
| W2 | d434c6196e6694c55640324873b2c2d442020c768bb881041901c26df0c9c173 | 721955e87c133059225fd882640cbd0443def2f375e0c4d5bdafac9796551ec3 | 721955e87c133059225fd882640cbd0443def2f375e0c4d5bdafac9796551ec3 |
| W3 | cf28cbc385f58e8253e438ecf1c0e207c83e2babdce6c644ad3556aedc2d8bdc | 721955e87c133059225fd882640cbd0443def2f375e0c4d5bdafac9796551ec3 | 721955e87c133059225fd882640cbd0443def2f375e0c4d5bdafac9796551ec3 |
| W4 | c7a9a113a2b6e19ce4ecd3ee201290718c28b6fc64f7ad55711a2ca460c793ef | 721955e87c133059225fd882640cbd0443def2f375e0c4d5bdafac9796551ec3 | 721955e87c133059225fd882640cbd0443def2f375e0c4d5bdafac9796551ec3 |
| A1 | 4e67682c5072c4cb0e343fac68d32515cf679b25acf6cb39ca21016e03a30bbf | 65ddf940b1b09b213c98c44b620d724c21a2328dba1d6893f12eae4610ca927c | 65ddf940b1b09b213c98c44b620d724c21a2328dba1d6893f12eae4610ca927c |

## Viewer-profile inspection compatibility

The explicit `redaction-viewer-resave-verification-v1` request profile supports
inspection of these retained reader outputs. The default
`redaction-verification-v1` sanitizer/export profile remains strict. As described
in the [verifier architecture](../architecture/redaction-verification.md), the
viewer profile proves the original linearized physical envelope, its two classic
xref sections, all indexed objects, and closed numeric hint tables. Its forward
`/Prev` is accepted only as the proved linearization link; genuine previous
revisions, arbitrary auxiliaries and unsupported carriers still fail.

The only additional metadata is the closed observed Adobe XMP serialization and
paired Info dates: valid linked dates, two canonical version-4 UUIDs and
`application/pdf`, with fixed namespaces, fields, toolkit, prolog, layout and
padding. Raw/decoded metadata and supported date/UUID decodings are scanned;
unknown properties, entities, DTDs, comments and hidden padding channels fail.
Only redundant CropBox equal to MediaBox, integer Rotate zero, and exact
13-significant-digit truncation of authenticated MediaBox geometry are
normalized. The latter is limited to reference magnitudes from 10 up to 1000
points with a difference below `1e-10` points. Page content operators, unused
resources, fonts and CMaps remain exact; full-page pixels and text geometry are
still checked independently.

The separate checker first revalidates the unchanged packet under the canonical
profile, then checks each exact actual saved file and its own fresh internal full
resave under the viewer profile. It preserves metadata bytes during that internal
resave instead of sanitizing an unsupported input. Its append-only epoch and
per-slot/copy receipts are stored in
`output/pws0606-verification-v1/viewer-verification/`, bound to current inspection
inputs, unchanged generation and preparation, original human observations,
environment provenance, exact files, reference and probes. 

The final viewer-profile run passes all five built-in methods on all ten exact actual
files, including each fresh internal full resave. Append-only receipts remain under
`output/pws0606-verification-v1/viewer-verification/`. The separate actual-file
method matrix is `output/pws0606-adobe/actual-file-method-matrix-v1.json`, SHA-256
`ce4aa34996c723966bf7f497ce3ae5ab6c90dadcda4ef96c454ede264f86a267`. Its OCR revalidation covers all 160 actual artifact-pages.
It binds the unchanged human observations and OCR proof without asserting external
method results for internal qpdf-generated bytes. All original PDFs, observations
and historical failed receipts are preserved. PWS-0606 is done; PWS-0607 is ready.
Permanent-removal export remains disabled pending integration and PWS-0608 approval.


The existing human observations and independent OCR remain evidence for the
exact ten actual reader saves. They do not claim human interaction or external
OCR on the verifier's internal qpdf bytes. No external method runner is supplied
by this recheck, so independent OCR and viewer fields in its built-in reports
remain `unknown`; separate exact-file evidence supplies the completed session's
coverage. Completing PWS-0606 makes PWS-0607 the next task. Permanent-removal
export remains disabled pending worker/export integration and independent
PWS-0608 approval.

## Repeatable automated evidence

Run the repository quality commands for formatting, lint, typing, unit,
integration, security, accessibility, schema, task-graph, documentation, and
portfolio-boundary checks. Focused native scenarios are:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/security/test_redaction_source_recovery.py tests/security/test_redaction_verification_corpus.py tests/integration/test_redaction_verification_pipeline.py
```

Stage the admitted Tesseract closure and language pack using the existing OCR
development procedure, then supply `PWS_TESSERACT_EXECUTABLE` and
`PWS_TESSDATA_ROOT`. The alternate QtPdf test requires the locked
`renderer-evaluation` group. A capability skip remains missing evidence and cannot
be counted as a redaction pass. Native OCR must recover the untreated source
canary and preserved outside-mask text; it must fail an unsafe source and find no
target in the sanitized derivative or its actual resaved bytes. QtPdf and PDFium
must agree on the rotated/cropped, visual-cover, and PNG candidates before and
after full save. QtPdf shares a PDFium engine family; this is an alternate API and
build path, not proof of an unrelated rendering implementation or a human viewer.

All tests and artifacts remain repository-local and synthetic. The procedure adds
no sibling dependency, shared database, adapter, service, or release-interface
claim. Exact packaged-candidate facts remain owned by the existing release tasks.
