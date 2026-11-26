# Threat-to-decision and task traceability

- Status: Binding foundation mapping
- Task: PWS-0004
- Threat definitions: [foundation threat model](threat-model.md#threat-catalogue)
- Evidence plan: [initial security test plan](security-test-plan.md)

Every foundation threat has at least one frozen decision and one implementation task or explicit
future gate. A reference means the linked task owns implementation/evidence; it does not mean the
control exists before that task is done. High-impact threats may not be waived by prose or by a
user warning.

Every listed control is **planned** until its owning implementation task records passing evidence.
A milestone gate reviews accumulated evidence; the gate itself is never a mitigation.

The [PWS-0601 redaction extension](redaction-threat-model.md) expands TM-017 with carrier-specific
threats, planned automated/manual verification, and an exhaustive ROADMAP M6 criteria mapping.
PWS-0602 through PWS-0607 own runtime controls; PWS-0608 retains independent release approval.

The PWS-0201 current review extends TM-001/TM-002 parser and resource evidence with explicit
unsupported color/precision rejection, TM-005/TM-014/TM-023 with ordinary image PDF export and
immutable-source verification, and TM-020 with exact Pillow native-member admission checks.
Its [coverage index](../development/image-intake-coverage.md) and
[review report](../evidence/pws0201-current-review.md) distinguish planned checks from results.

| Threat | Frozen decisions | Implementation tasks and gates | Planned evidence focus |
|---|---|---|---|
| TM-001 | PWS-D012, PWS-D013 | PWS-0103, PWS-0105, PWS-0106, PWS-0107, PWS-0114, PWS-0201, PWS-0202, PWS-0204, PWS-0206, PWS-0207 | Hostile parser/transform/OCR/searchable-output and bounded OCR-review TSV/report corpus, exact prepared-source or volatile-sample authority, one-shot worker crash/termination, containment |
| TM-002 | PWS-D013 | PWS-0103, PWS-0105, PWS-0106, PWS-0107, PWS-0201, PWS-0202, PWS-0203, PWS-0204, PWS-0206, PWS-0207, PWS-0208, PWS-0209, PWS-0303, PWS-0401 | Byte/page/object/graph/pixel/decompression/time/memory/temp/result/status/tool-output/template limits; shared preview accounting; sequential one-sample quality review; bounded overlay raster/output/object traversal; cancellation and deadline |
| TM-003 | PWS-D012 | PWS-0002, PWS-0008, PWS-0107, PWS-0114, PWS-0202, PWS-0204, PWS-0206, PWS-0207 | Import-boundary and separate-process integration tests, including handle-only one-page OCR review |
| TM-004 | PWS-D005, PWS-D013, PWS-D027 | PWS-0107, PWS-0204, PWS-0207, PWS-0114, PWS-0209, PWS-0803 | Worker/OCR-review descendant egress denial and updater privacy capture |
| TM-005 | PWS-D009, PWS-D029 | PWS-0104, PWS-0112, PWS-0114, PWS-0202, PWS-0203, PWS-0204, PWS-0206 | External/case source hashes, pre-Apply manifest invariance, and no-overwrite transform/OCR/searchable-export tests |
| TM-006 | PWS-D028, PWS-D029 | PWS-0101, PWS-0102, PWS-0104, PWS-0202, PWS-0204, PWS-0206, PWS-0401, PWS-0407 | Traversal/device/reparse/permission, exact OCR/searchable/template authority, and cleanup containment |
| TM-007 | PWS-D010, PWS-D036 | PWS-0006, PWS-0109, PWS-0804 | Schema versions, atomic failure, journal recovery, migrations |
| TM-008 | PWS-D012, PWS-D013, PWS-D036, PWS-D038 | PWS-0008, PWS-0107, PWS-0202, PWS-0204, PWS-0206, PWS-0207 | Malformed/oversized/versioned RPC, replay/state, transform/OCR/searchable/quality-report binding, object-free result, termination |
| TM-009 | PWS-D038 | PWS-0008, PWS-0103, PWS-0105, PWS-0107, PWS-0204, PWS-0207, PWS-0401 | Injection strings and inert template text, fixed qpdf/Tesseract argv and absolute identity, working directory, controlled search paths/environment, descendant/output limits |
| TM-010 | PWS-D025, PWS-D033, PWS-D036 | PWS-0007, PWS-0401, PWS-0402 | Script/expression/path/network/future-version template rejection |
| TM-011 | PWS-D027, PWS-D039 | PWS-0113, PWS-0202, PWS-0203, PWS-0204, PWS-0206, PWS-0207, PWS-0303, PWS-0305, PWS-0401, PWS-0805 | Closed diagnostic schema and prohibited canaries across logs, errors, transforms, review accessibility metadata, quality samples/fingerprints, OCR/extracted/overlay/template text and digests, font-subset glyph records, crashes, and later bundles |
| TM-012 | PWS-D028, PWS-D039 | PWS-0101, PWS-0108, PWS-0113, PWS-0202, PWS-0203, PWS-0204, PWS-0206, PWS-0207, PWS-0705, PWS-0706 | Ordered case-scoped crash/transform/review/OCR/searchable-partial cleanup, ephemeral quality-state invalidation and late-result suppression, invalid-case isolation, cache isolation, persistent worker ACL-lease repair, retention inventory |
| TM-013 | PWS-D028, PWS-D031 | PWS-0705, PWS-0706, PWS-0707 | Consent, lock, expiry, case isolation, handover/deletion |
| TM-014 | PWS-D029, PWS-D037 | PWS-0112, PWS-0206, PWS-0407, PWS-0114, PWS-0409 | Corrupt/partial/wrong-page/false-text-status output blocked before commit |
| TM-015 | PWS-D008, PWS-D028, PWS-D029 | PWS-0101, PWS-0112, PWS-0406, PWS-0407 | Filename/ZIP traversal, collision, race, no-overwrite tests |
| TM-016 | PWS-D020, PWS-D021, PWS-D037 | PWS-0105, PWS-0204, PWS-0206, PWS-0208, PWS-0209, PWS-0305, PWS-0308 | Signed-source detection, PWS-0204 fail-closed OCR gate, later confirmation, unsigned-derivative and signature-image wording |
| TM-017 | PWS-D022, PWS-D023 | PWS-0405, PWS-0601, PWS-0603, PWS-0606, PWS-0607, PWS-0608 | Visual-cover rejection, canary extraction, blocked failure, independent gate |
| TM-018 | PWS-D027, PWS-D039 | PWS-0113, PWS-0805 | Safe summary baseline; later exact inventory preview, opt-in, local-only bundle, and canary scan |
| TM-019 | PWS-D028, PWS-D031, PWS-D037 | PWS-0101, PWS-0102, PWS-0113, PWS-0705, PWS-0706 | Case-ID scoping, invalid-case isolation, partial failure, erasure wording, restart |
| TM-020 | PWS-D034 | PWS-0005, PWS-0204, PWS-0207, PWS-0303, PWS-0801, PWS-0802, PWS-0803, PWS-0808 | OCR and overlay font/render runtime-closure admission, font/data identity and licensing, session-latched review composition, pin/hash/signature, license inventory, SBOM, artifact match |
| TM-021 | PWS-D005, PWS-D013, PWS-D027, PWS-D034, PWS-D042, PWS-D043 | PWS-0803, PWS-0807, PWS-0808 | Offline signature and exact-byte verification, trust rotation, expiry/channel/rollback, package-swap rejection, installed interruption and recovery, and privacy capture |
| TM-022 | PWS-D012, PWS-D013, PWS-D027, PWS-D038 | PWS-0008, PWS-0105, PWS-0107, PWS-0113, PWS-0202, PWS-0203, PWS-0204, PWS-0206, PWS-0207, PWS-0401 | Closed typed result/frame/template-snapshot validation, UUID/transform/review/OCR/searchable/quality correlation, hostile counts, discarded pixels/text/tool output, sanitized exceptions, fail-closed ACL recovery |
| TM-023 | PWS-D009, PWS-D029 | PWS-0103, PWS-0104, PWS-0105, PWS-0108, PWS-0112, PWS-0202, PWS-0203, PWS-0204, PWS-0206, PWS-0207, PWS-0401, PWS-0407 | Source handle identity/digest/reopen; result/operation/review-generation and quality case/revision/page-tuple correlation; OCR volatile-output and searchable page-status authentication; cache/preview/destination/template alias, snapshot, race, and terminal recheck tests |
| TM-024 | PWS-D026, PWS-D032, PWS-D033, PWS-D037 | PWS-0203, PWS-0204, PWS-0206, PWS-0207, PWS-0403, PWS-0404, PWS-0408, PWS-0409 | Tentative scan suggestions require explicit previewed Apply; OCR candidate/final text-presence separation, per-page review outcomes, deterministic review signals, confirmation ownership, and prohibited-claim/wording checks |
| TM-025 | PWS-D008, PWS-D019, PWS-D025 | PWS-0105, PWS-0303, PWS-0401, PWS-0405, PWS-0501 | All-object and recursively nested XFA/script/action/form/attachment markers, generated-overlay active-content rejection, unknown-state handling, archive/revision inventory |
| TM-026 | PWS-D027, PWS-D028 | PWS-0006, PWS-0104, PWS-0305, PWS-0408, PWS-0501, PWS-0805 | Random names, metadata/assets, intended-output summaries, prohibited-log values |
| TM-027 | PWS-D024, PWS-D026, PWS-D037 | PWS-0503, PWS-0504, PWS-0505, PWS-0507 | Staged compression, measured floor, affected-page preview, unsafe/unattainable target result |
| TM-028 | PWS-D027, PWS-D035, PWS-D039 | PWS-0003, PWS-0009, PWS-0113, PWS-0202, PWS-0206, PWS-0207, PWS-0805, PWS-0806 | Synthetic provenance, approved transform/OCR fixture review, recipe-only quality corpus, repository/CI scanning, restricted sensitive-sample handling |

## Gate ownership

- PWS-0010 reviews this mapping before document-engine implementation begins.
- PWS-0114 proves the first worker/import/export/recovery boundary.
- PWS-0209 proves local OCR, signed-source, and incremental resource behavior.
- Within PWS-0303, shared raster/output/object-graph budgets plus cancellation/deadline probes trace
  to TM-002; text, glyph-name, path, pixel, and native-error log canaries trace to TM-011; exact
  font, library, native qpdf, and license identities trace to TM-020; and recursive direct/indirect
  generated-PDF inspection with exact resource structure traces to TM-025.
- Within PWS-0204, prospective-capacity and partial-write/retry evidence traces to TM-002 and
  TM-012; direct-child timeout escalation traces to TM-002 and TM-009; strict encrypted/no-recovery
  PDF reopen traces to TM-001 and TM-022; and exact runtime hardlink/special-entry refusal traces to
  TM-006, TM-020, and TM-023.
- Within PWS-0206, bounded plan/status/candidate handling traces to TM-002 and TM-022; page-local
  OCR failure and limited text-presence wording trace to TM-024; signed/unknown fallback and the
  unsigned-derivative label trace to TM-016; and exact synthetic raster provenance traces to
  TM-028. The real locked OCR/PDF closure remains required for extraction/render evidence.
- Within PWS-0207, one-sample sequencing, capped prior-page fingerprints, cancellation, and total
  deadline trace to TM-002; the separate handle-only worker and private TSV parser trace to TM-001
  and TM-003; inherited AppContainer/Job egress and fixed-command controls trace to TM-004 and
  TM-009; the closed `ocr.review` registry/report bindings trace to TM-008 and TM-022; ephemeral
  state, private-PPM cleanup, and prohibited pixel/fingerprint/OCR-text handling trace to TM-011 and
  TM-012; exact snapshot/operation/revision/page/sample/presentation correlation traces to TM-022
  and TM-023; limited wording and no checklist mutation trace to TM-024; and the in-memory
  recipe-only labeled corpus traces to TM-028. Only an expected local-unavailable admission result
  is session-latched, and restart creates a fresh authority rather than silently accepting changed
  runtime resources; safety and infrastructure faults remain terminal.
- Within PWS-0401, exact-byte catalog binding, immutable snapshots, and same-handle local file
  authentication trace to TM-010 and TM-023; bounded JSON/schema/semantic/lifecycle validation
  traces to TM-002 and TM-010; executable/path/trust-member and zero-side-effect probes trace to
  TM-009, TM-010, and TM-025; sanitized errors and unchanged-case canaries trace to TM-011 and
  TM-022. Embedded provenance remains an explicitly labeled claim and the loader has no case writer.
- PWS-0409 confirms v0.1 keeps permanent redaction disabled and uses concrete claims.
- PWS-0507 reviews size reduction, legibility, determinism, and compatibility evidence.
- PWS-0608 is the independent permanent-redaction release gate; no earlier task may substitute for
  it.
- PWS-0707 reviews shared-computer, accessibility, retention, and deletion behavior.
- PWS-0803 verifies root-authorized metadata and exact runtime ZIP bytes locally under TM-020
  and TM-021. Private staging, exact slot identity and the launch lease address TM-021 and
  TM-023. Offline update operation and separate worker egress policy address TM-004. See the
  [update verification plan](security-test-plan.md#pws-0803-update-verification).
- PWS-0808 reviews dependencies, installer/update, migrations, diagnostics, and packaged artifacts.

When a task changes threat ownership, update the threat model, this table, the test plan, and the
task graph together. Unknown or missing IDs fail `validate-docs`.
