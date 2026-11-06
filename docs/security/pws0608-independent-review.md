# Current PWS-0608 qualification

Current source-tree qualification: **PASS**, source `7e2ea7ae1fd3eaf6a7882fc5f236f101e7424ab39ac57fd19dbca132eb9a44c9`, checkpoint `9a4a74c08efd0bc30c9439288f511ef37dba755c`. Independent approval: `output/pws0608-repair/independent-final-source-approval-v1.json`. The supported M6 source-tree removal export is qualified for exactly one active affected PDF page, with documented crop/quarter-turn and bounded payload limits. This is not a packaged/public v1.0 release; M7 and M8 remain pending under PWS-D040.

The explicit redaction-runtime-verification-v1 profile requires five built-in methods (objects/revisions/features, text extraction, image extraction, rendering and fresh full resave) plus the fixed private English (eng) OCR audit on the exact candidate and full resave. All six must pass. The output stays image-only, with no regenerated searchable text. Named-viewer testimony for this new file remains UNKNOWN; compatibility is mandatory release-profile qualification, not invented per-file observation. The original canonical/viewer version-1 profiles retain all-seven release_ready semantics.

Publication also requires the compiled feature switch and exact qualified runtime profile, current reviewed intent/source integrity, broker-owned single-use candidate authority and an authenticated no-replace destination. Review of saved marks and warnings is required for every export; an extra unsigned-derivative confirmation is required only for source signature markers with consent still pending. All outputs are unsigned. Reports, environment variables, manifests and UI inputs cannot grant approval. Saved marks remain unapplied; ordinary export paths retain active-mark blockers.

Destination uncertainty is latched before context unwind and remains UNCERTAIN with no automatic retry across closer failures or exception suppression. A confirmed committed derivative stays committed; it is not relabelled as unpublished when later cleanup fails. Private worker cleanup before publication still blocks publication on failure. These distinct states are covered by current source-bound service regressions; no new error codes or schema meanings are introduced.

Evidence: approved native `output/pws0608-repair/verified-native-desktop-plain.json`; current viewer status/verify `output/pws0608-repair/verified-viewer-status-v3.json` / `output/pws0608-repair/verified-viewer-inspection.json`; OCR noninterference `output/pws0608-repair/ocr-noninterference-attestation-v1-01.json`; UI `output/pws0608-repair/native-success-ui-nvda-observations-v2.json`. Exact commands, source epochs and limitations are in `docs/development/pws0608-remediation.md`.

Final metadata checks are recorded externally in output/pws0608-repair/final-files.json and output/pws0608-repair/final-closure-validation.json. This report does not claim outcomes for checks not yet run and does not embed their downstream digests: the inventory and receipt hash this report. Publication requires successful schema, task-graph, documentation, portfolio, full secret-scan and Git whitespace checks with unchanged code and Git identities.

M6-R1 is resolved through explicit exact-file runtime checks and mandatory release-profile qualification; M6-R2 through the real qualified desktop route and tested completion announcement; M6-R3 through current-source inspection and immutable exact-file OCR lineage. The original rejection is retained below and is not relabelled. Exactly one page may be in the active sequence and it must be the affected PDF page; even an additional unmarked active page is unsupported. Its source PDF may contain other pages, but must be complete and unencrypted. Active overlays, deskew/contrast/background cleanup and unsupported payload/filter combinations fail closed; only the proved crop and quarter-turn intent with bounded unfiltered/single-Flate payloads is supported. Every derivative is fresh, image-only and unsigned; searchable text, forms and accessibility structure are not promised. There is no universal hostile-PDF safety, forensic-erasure, OCR-certainty, hardware, portal-acceptance, legal-validity or PDF/UA claim.

## Historical first review — preserved verbatim

The following is the original failed review epoch, not the current qualification. Its rejection and earlier limitations are preserved; see docs/development/pws0608-result.md for the immutable original completion record.

# PWS-0608 independent permanent-redaction review

- Review date: 2026-11-01
- Decision: **REJECT RELEASE; permanent removal must remain disabled**
- Reviewed implementation inventory: `output/pws0608/qa-files.json`, 1,115 files,
  SHA-256 `6eb9ea7525d478e7699a47852bd165d72e0a3166b26e307809f7f54211b1a27d`
- Local parent: `2e70ec0e7174dab342f6df43d7b4d685f84a3259`; this commit alone
  does not identify the inherited working-tree implementation.
- Gate authority: PWS-D022, PWS-D023, PWS-D029 and PWS-D040 in
  [DECISIONS.md](../../DECISIONS.md), and [PWS-0608](../../TASKS.yaml).

Three separate automated agents performed read-only audits of the publication
authority, verifier/corpus, and release evidence. They did not implement this
change, modify the reviewed implementation, run competing native suites, or ask
for new human observations. All withheld release approval. This is an independent
automated source/evidence review, not third-party human security certification.
The primary agent owns fresh test execution and the
[gate report](../gates/m6-permanent-redaction-gate.md).

## Findings and resolution requirements

### M6-R1 — Required methods cannot pass for real composed candidates

**Release blocking; unresolved.**

`src/paperworkshell/pdf/redaction_export.py`, lines 180–200, invokes the verifier
without independent OCR or viewer runners. The broker's
`_require_supported_evidence` in
`src/paperworkshell/worker/redaction_export_broker.py`, lines 166–175, rejects
asserted passes for those methods because this composition has no such authority.
`src/paperworkshell/app/redaction_export.py`, lines 340–345, correctly blocks
publication when any required method is unknown. Enabling the build switch would
not make a real candidate publishable.

The current behavior fails closed. No content-recovery exploit is established by
this finding. Historical corpus, OCR and reader evidence cannot be attached to
different candidate bytes to fill the gap.

Resolution requires a defined, authenticated authority for every required method
on the supported real export path, bound to exact candidate/resave bytes, intent,
profile and applicable engines. Missing, stale, forged and failed evidence must
continue to block commit. Prove the path with actual constrained native tests and
repeat independent review. Do not weaken the seven-method gate or turn historical
observations into per-candidate assertions.

### M6-R2 — The desktop does not invoke the composed export service

**Release blocking; unresolved.**

Production call-site inspection finds `RedactionExportService` only in its
definition module, `src/paperworkshell/app/redaction_export.py`. The worker
composition is exercised by integration tests. The desktop's
`src/paperworkshell/ui/window.py`, around lines 1014 and 3568, offers intent-only
editing and a blocked `RELEASE_PENDING` report. It has no actual reviewed removal
export invocation.

The disabled UI is truthful. A release claim that users can export permanent
removal is unsupported. Resolve this through the supported production composition
and accessible end-to-end workflow, preserving exact reviewed intent, unsigned
derivative consent, progress, cancellation, sanitized errors, no-replace commit,
and existing ordinary-export blockers. Test success and failure using real ports;
synthetic trusted-port tests alone cannot prove that desktop journey.

### M6-R3 — Retained viewer inspection is not current

**Release blocking evidence gap; unresolved.**

The stable PWS-0606 status entry point exits one with
`PWS0606_VIEWER_RECHECK_FAILED`. The direct retained-session check reports
`PWS0606_GENERATION_CHANGED` at `tools/pws0606_viewer_resave.py`, line 97.
The broad provenance collection at `tools/pws0606_redaction_verification.py`,
line 123, now includes three additional composition modules:

- `src/paperworkshell/domain/redaction_export.py`
- `src/paperworkshell/domain/redaction_export_candidate.py`
- `src/paperworkshell/pdf/redaction_export.py`

Four admitted inspection modules also differ from the original preparation:
`domain/redaction_verification.py`, `pdf/redaction_verification.py`,
`pdf/redaction_verification_resources.py`, and
`pdf/redaction_verification_structure.py`, all under `src/paperworkshell/`.
The old epoch must not be described as a current automated pass.

The original packet, reference, unsafe control, all five accepted observations,
ten actual saved files and original exact-file OCR bindings remain intact.
No original non-inspection generation input changed. The failure is stale
provenance/evidence, not an observed canary recovery. Detailed read-only checks
are retained under `output/pws0608/` and summarized in the gate report.

Resolve the narrow preparation-versus-inspection lineage with fail-closed
regressions, then use the existing separate verification mode to append new
canonical-packet and actual-viewer-file receipts. Preserve all originals, failed
receipts and human observations. Do not regenerate the packet or repeat the
accepted session merely to refresh inspection. Any future human work must follow
the [human-testing policy](../development/human-testing.md).

## Reviewed safeguards and claim limits

The audits found no additional demonstrated content-recovery or publication
bypass in the reviewed code. They inspected whole-page burn-in before encoding,
fresh page resources, physical object/xref/revision coverage, prohibited carriers,
unused images and font/CMap resources, text/glyph/image/pixel comparisons, full
resave inspection, source/intent correlation, broker-owned single-use candidates,
current-manifest checks and no-replace destination behavior. This finding of no
demonstrated bypass is not proof that every hostile PDF is safe.

The current private composition supports exactly one active affected PDF page,
supported crop/quarter-turn intent and a bounded unfiltered/single-Flate payload
profile. It rejects active overlays, unsupported cleanup/filters and multiple
active pages. It does not compose regenerated OCR. These limits are documented in
[the export authority description](../architecture/redaction-export-gate.md).

Production verification proves agreement with the authenticated reconstructed
reference and checks source payload fingerprints; it has no known-plaintext
canary list for arbitrary user documents. Independent mask, geometry, corpus and
OCR evidence therefore remains necessary. Signature warnings correctly describe
unsigned derivatives; rasterization warnings explain possible form and
accessibility-structure loss. Originals and saved intent remain unchanged.

There is no authorization to enable permanent removal, no accepted waiver, and
no new hardware, OCR-certainty, PDF/UA, legal-validity, portal-acceptance,
forensic-erasure, packaged-release or ThirdLife-compatibility claim. Reconsider
PWS-0608 only after all three findings have current resolution evidence and a
new independent approval.
