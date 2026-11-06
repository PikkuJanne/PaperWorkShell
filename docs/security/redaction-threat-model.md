# Permanent-redaction threat model and verification plan

- Task: PWS-0601
- Status: Design and adversarial-test contract; permanent redaction remains disabled
- Authority: [frozen decisions](../../DECISIONS.md), especially PWS-D022, PWS-D023, and
  PWS-D035; [binding M6 boundary and exit criteria](../../ROADMAP.md#m6--permanent-redaction)
- Foundation: [threat model](threat-model.md), [traceability](threat-traceability.md), and
  [security test plan](security-test-plan.md)

This extension defines the claim that later M6 implementation must prove. It records planned
verification, rather than passing evidence for a redaction engine. PWS-0601 adds no redaction
operation, UI state, manifest meaning, export profile, or release enablement. PWS-0602 through
PWS-0607 own those implementations; only independent approval of PWS-0608 can permit release
enablement. A visually covered source, a corpus test passing, or this document being complete
cannot approve that gate.

## Supported claim and its limits

The first supported method reconstructs the **whole affected page** from a sanitized raster.
The worker renders the page, maps reviewed removal regions into the complete rendered page,
burns their union into the pixels, and only then encodes a new page image. The derivative must
discard original affected-page objects, including original text streams, images, fonts carrying
content, masks, alternate appearances, and resources. Copying the original page and adding a
rectangle, annotation, clip, optional-content switch, or opaque layer never satisfies this method.
Object-level redaction is outside this gate.

Searchable text on reconstructed pages may be regenerated only from non-redacted regions of
the sanitized pixels. Source text, source OCR, annotation text, form values, or a cached OCR layer
must never be reused as OCR input or output. The mask strategy must prove that OCR receives no
covered source pixels and accepts no text intersecting a removal region. OCR uncertainty remains
"Needs review"; OCR recognition success does not establish removal.

The claim concerns reviewed regions and prohibited retained carriers in the **new derivative**.
It does not mean that every occurrence of a value elsewhere was found automatically. Users must
review and mark additional visible occurrences on other pages. Hardened derivative sanitization
must remove attachments, annotations/comments, actions/scripts, retained form values, source
metadata, and source incremental revisions; a hidden carrier cannot be excused because its value
also occurred outside a marked rectangle. Unaffected pages and document-wide resources remain
subject to that sanitization and the whole-file verifier. The implementation must document the
supported geometry, image quality, OCR languages, and input limits from actual evidence.

The claim never extends to deleting the external source, immutable case copy, source preview,
backup, filesystem journal, swap, or another application's saved copy. Originals remain unchanged.
It does not promise forensic erasure, removal of facts inferable from surrounding visible content,
OCR certainty, legal validity, portal acceptance, preservation of accessibility structure, PDF/UA
conformance, or cryptographic signature validation. A modified signed source produces an
explicitly **unsigned derivative**. Rasterization may lose forms, vector quality, reading order,
tags, and other accessibility structure; PWS-0602 must explain those effects before application.

Unsupported input, ambiguous geometry, inspection limits, malformed structures, missing required
capabilities, or an unknown verification result must leave the candidate unverified and block its
final export commit. No fallback may silently revert to visual covering, retain source OCR, or
claim a failed method is unnecessary.

## Protected assets, actors, and trust boundaries

The foundation exclusions for administrator/kernel compromise, unrestricted same-account malware,
and platform-controlled retention continue to apply. An adversary may author every source byte,
object graph, text encoding, image, form, metadata value, layer, revision, and filename; influence
parser/tool results; race permitted local files; and inspect every byte of the exported file using
different readers, extractors, object tools, OCR, and save/resave. Ordinary mistakes, cancellation,
crashes, disk exhaustion, missing tools, and a mistaken reviewer are also modeled.

| Asset | Required property and owner |
|---|---|
| External source, immutable case copy, hashes | Unchanged before/after success and every failure; PWS-0603 and PWS-0607 |
| Reviewed region intent and page geometry | Versioned, bound to exact case/revision/page/transforms; ordinary shapes cannot authorize removal; PWS-0602 |
| Unsanitized source render and intermediate OCR | Sensitive, bounded, worker-private, never a preview/export/log artifact; PWS-0603 and PWS-0604 |
| Sanitized page pixels and regenerated OCR | Correspond exactly to reviewed masks; contain no covered source pixels/text; PWS-0603 and PWS-0604 |
| Fresh derivative and feature/object inventories | No original affected-page content or prohibited carriers, including unreachable objects and old revisions; PWS-0603 and PWS-0605 |
| Verification result and committed export | Exact bytes, page set, intent, profile, engines, and methods remain bound through commit; PWS-0606 and PWS-0607 |
| Reports, logs, recovery state | IDs, closed methods/status/codes, counts, and versions only; no removed values, OCR, pixels, source names, or paths; PWS-0605 through PWS-0607 |
| Corpus, canary inventory, negative controls | Deterministic synthetic provenance, complete carriers, positive recovery controls, no real user content; PWS-0601 |

| Boundary | Crossing requirements |
|---|---|
| User/UI to pure intent and application planning | Accessible region/list/numeric controls, explicit review, state/geometry validation, warning and signature handling; UI never parses source content |
| Broker to restricted worker and external tools | Existing versioned RPC, exact reduced handle grants, AppContainer/Job/egress denial, fixed admitted runtime and structured arguments; each new operation proves its own least-privilege profile |
| Source render to sanitized raster | Current page only, exact crop/rotation/scale mapping, proven pixel-boundary policy, mask before encoding; release unsanitized buffers before proceeding |
| Sanitized raster to OCR | Excluded covered regions, no source OCR/cache reuse, bounded local recognition, validated word geometry; failure leaves unverified state |
| New assembly to sanitizer and verifier | Fresh full save, closed generated-object policy, global feature scrub, reopened candidate; worker output and status remain untrusted |
| Verified staging to export destination | Broker authenticates exact candidate identity/digest and unchanged intent, confirms worker termination, then commits through existing atomic destination policy; no original or previous-good-output replacement on failure |
| Any processing state to logs/reports/recovery | Closed sanitized result model; content, canary values, coordinates carrying source content, raw exceptions, and temporary paths do not enter routine diagnostics |
| Maintainer evidence to release enablement | Exact revision/profile/corpus/tool inventory, complete automated and manual results, independent review, and explicit PWS-0608 approval; absence or stale evidence keeps feature disabled |

Workers receive no generalized case path or publication authority. Later M6 operations must extend
the existing handle-only boundary with evidence, rather than assuming another operation's grants
authorize redaction. The central versioned `ResourcePolicy` continues to bound pixel planes,
memory, output, private temporary storage, queues, descendants, CPU/time, and concurrency. Full-page
reconstruction is page-at-a-time; it never authorizes materializing a complete full-resolution packet.

## Adversarial scenarios and control ownership

The RTH identifiers extend foundation TM-017 and retain the related threats listed below. Every
row is release-blocking if its control or evidence is absent; a warning is not the mitigation.

| ID | Attack or failure | Foundation threats and decisions | Controls and planned evidence |
|---|---|---|---|
| RTH-001 | Cover leaves selectable text, alternate encodings, hidden OCR, or ActualText recoverable | TM-017, TM-025; PWS-D022, PWS-D023 | Whole-page reconstruction and no source-text reuse; PWS-0603, PWS-0604, PWS-0606; RT-002, RT-007, RT-012 |
| RTH-002 | Original image, mask, thumbnail, or alternate layer retains covered pixels | TM-017, TM-025; PWS-D022 | Encode only sanitized pixels, inspect all images/resources, reject optional-content recovery; PWS-0603, PWS-0606; RT-003, RT-004, RT-010 |
| RTH-003 | Unreachable object, nested resource, embedded file, or earlier revision retains content | TM-017, TM-025; PWS-D022, PWS-D029 | Fresh assembly plus decoded whole-file/object/revision and embedded-carrier checks; PWS-0603, PWS-0605, PWS-0606; RT-004 through RT-006 |
| RTH-004 | Annotation appearance, comment, form value, action/script, document info, or XMP leaks a value | TM-025, TM-026; PWS-D022, PWS-D027 | Global hardened sanitization and before/after feature inventory; PWS-0605, PWS-0606; RT-005, RT-006 |
| RTH-005 | Rotation, crop, inherited boxes, scale, thin edge, or stale intent masks the wrong pixels | TM-023; PWS-D010, PWS-D022 | Versioned coordinate/intent binding, boundary tests and non-redacted fidelity controls; PWS-0602, PWS-0603; RT-008, RT-009 |
| RTH-006 | OCR sees original pixels, joins neighboring fragments, restores cached text, or ignores geometry | TM-017, TM-022; PWS-D016, PWS-D022 | Only sanitized non-redacted input, recognition-output exclusion and source/derivative OCR controls; PWS-0604, PWS-0606; RT-007, RT-012 |
| RTH-007 | Viewer, layer switch, copy/search, save, or resave reveals retained content | TM-017, TM-025; PWS-D022, PWS-D029 | Multiple independent viewer/extractor paths and repeat all applicable checks after resave; PWS-0606, PWS-0608; RT-002 through RT-007, RT-010, RM-001 |
| RTH-008 | Parser recovery, unknown method, absent tool, or swallowed error creates a false pass | TM-001, TM-022; PWS-D012, PWS-D013, PWS-D022 | Malformed rejection, method completeness, positive/negative controls, closed statuses; PWS-0603, PWS-0606, PWS-0607; RT-001, RT-011 through RT-013 |
| RTH-009 | Oversize/cancel/timeout/disk-full/crash publishes partial output or leaves source-bearing artifacts | TM-002, TM-012; PWS-D013, PWS-D028 | Enforced constrained policy, incremental work, termination/cleanup/recovery and atomic commit; PWS-0603 through PWS-0607; RT-013, RT-014 |
| RTH-010 | Swapped candidate, wrong page/revision, forged report, retry, or alternate export bypasses checks | TM-008, TM-014, TM-015, TM-023; PWS-D029, PWS-D036 | Exact byte/intent/result binding, rechecks, all export paths gated, previous output preserved; PWS-0606, PWS-0607; RT-009, RT-013, RT-017 |
| RTH-011 | Removed content leaks through logs, exception strings, reports, caches, or diagnostics | TM-011, TM-012, TM-026; PWS-D027, PWS-D039 | Closed code-only reports and prohibited-token/pixel/path probes; PWS-0605 through PWS-0607; RT-014, RT-016 |
| RTH-012 | Ordinary shape, unapplied mark, failed candidate, or signed source is mislabeled | TM-016, TM-017, TM-024; PWS-D020, PWS-D022, PWS-D023, PWS-D030 | Distinct accessible states, unsigned-derivative warning, truthful outcomes, release gate; PWS-0602, PWS-0607, PWS-0608; RT-015, RT-017, RT-018, RM-002, RM-003 |

## Corpus and oracle contract

The committed source set contains thirteen PDFs and one PNG, with eighteen distinct canaries.
The [versioned canary inventory](../../tests/fixtures/redaction/v1/canaries.json) is generated by
the closed [redaction recipe](../../tools/redaction_corpus.py); each `fixture_path` is relative
to the canonical [synthetic corpus directory](../../tests/fixtures/synthetic/v1/index.json).
Source detectability checks live in
[the security source tests](../../tests/security/test_redaction_source_recovery.py), and inventory,
provenance, regeneration, and mutation checks in
[the corpus unit tests](../../tests/unit/test_redaction_corpus.py). These source checks do not
run a permanent-redaction engine. Algorithmic pixel-grid decoding establishes raster canary
recoverability; it does not claim native OCR recognition accuracy or complete RT-007 evidence.
The inventory's `ocr` recovery-method label is a **future planned method**, owned by PWS-0604 and
PWS-0606. Current source tests use deterministic glyph decoding; they do not execute Tesseract.

| Fixture ID | Canary IDs | Carrier and source recovery control |
|---|---|---|
| pdf-redaction-born-digital | rc-text | Selectable page text and decoded stream |
| pdf-redaction-scan | rc-scan | Image-only page, decoded grayscale pixels |
| pdf-redaction-embedded-image | rc-image | Reachable embedded image XObject, decoded grayscale pixels |
| png-redaction-scan | rc-png | PNG source with real bitmap text and no plaintext canary copy |
| pdf-redaction-annotation | rc-annotation | Reachable annotation value and appearance stream |
| pdf-redaction-form | rc-form | AcroForm field value/default, page widget and appearance |
| pdf-redaction-metadata | rc-info, rc-xmp | Separate document-info and XML metadata values |
| pdf-redaction-attachment | rc-attachment | Catalog name tree, Filespec and actual embedded-file stream |
| pdf-redaction-hidden-layer | rc-layer | Off optional-content group, decoded content and enabled-layer rendering |
| pdf-redaction-incremental | rc-revision | Real earlier content-stream revision linked by Prev; absent in current text |
| pdf-redaction-geometry | rc-geometry-zero, rc-geometry-one, rc-geometry-two, rc-geometry-three | Four cropped pages with nonzero box origins and all quarter turns |
| pdf-redaction-hidden-ocr | rc-ocr | Invisible text layer over bitmap text; both carriers require future removal |
| pdf-redaction-malformed | rc-malformed | Truncated structural source; strict parser rejection expected |
| pdf-redaction-visual-cover | rc-cover | Opaque cover over still-extractable text; explicitly unsafe control |

The dedicated corpus must cover born-digital text, scanned text, embedded images, annotations,
form values, metadata, attachments, hidden layers, incremental revisions, rotated/cropped pages,
existing OCR, and malformed PDFs. Mixed-page/duplicate occurrences, signed markers, and ordinary
opaque-cover controls supplement those required classes. Signed-marker fixtures exercise detection
and unsigned wording; they are never evidence of cryptographic signature validity.

Each inventoried canary has a distinct synthetic ID/value and an entry identifying
its fixture, carrier, applicable recovery methods, target geometry where relevant, and expected
source/derivative behavior. A value may occur in related carriers within one case, such as
field value/appearance or raster/hidden OCR; each listed carrier needs a positive recovery control.
Values are deliberately public synthetic fixture data, yet routine test
reports must print only IDs and codes. Visible non-redacted sentinel content is tracked separately
and must survive within the documented fidelity/OCR tolerance. Its presence prevents an empty,
blank, wrong-page, or wholly destroyed output from passing a canary-absence check.

Before testing absence in a derivative, each applicable method must positively recover the canary
from the unsafe source/control. Text controls require decoded text/encoding-aware extraction,
image controls require decoded pixels or independent OCR, and revision controls require recovery
from the earlier revision. Finding an ASCII token in a source file is insufficient to establish
an encoded/image carrier. A method that cannot recover its positive control is failed or unknown,
never a successful absence check. Unsupported malformed files have explicit rejection oracles;
they do not need to become exportable to pass.

The corpus must include unsafe derivatives for text-under-cover, original image retention,
stale OCR, prohibited metadata/attachments/forms/comments, original objects/revisions, and forged
or incomplete verifier results. Tests must prove those cases are blocked. These negative controls
may be test-built mutations of corpus sources; they are not production redaction implementations.
No test may assert that painting a rectangle alone removes content.

Validation in PWS-0601 proves fixture provenance, deterministic bytes, canary/carrier inventory,
positive source recovery, required classes, and coverage planning. It must report these results
separately from the future runtime RT/RM checks. No production redaction pass is possible in this
task. Generated artifacts remain synthetic and small; hostile expansion/resource variants are
generated in bounded temporary workspaces and removed after tests.

## Planned automated verification methods

Every RT identifier below is a **planned** test contract, not a claim that a test exists or passes.
Its owner must add concrete test-node references and exact executed evidence before PWS-0608.
Unit, integration, security, and accessibility suites use the repository's stable quality entry
points. Required native engines, OCR packs, and viewer slots cannot be replaced by capability
skips in gate evidence.

| Test ID | Owning tasks | Required scenario and pass/failure oracle |
|---|---|---|
| RT-001 | PWS-0601, PWS-0606 | Validate every corpus class, unique carrier canary, source recovery positive control, safe provenance, digest, and malformed rejection oracle. Missing/duplicate/undetectable carrier or incomplete inventory fails corpus validation. |
| RT-002 | PWS-0603, PWS-0606 | Select/copy/search and text extraction through independent supported paths, decoded strings, text encodings, ActualText, and hidden text. All removable canaries absent, retained sentinels present; opaque-cover controls must fail. RM-001 covers viewer actions. |
| RT-003 | PWS-0603, PWS-0606 | Extract/decode all image XObjects, inline images, masks, alternates, and thumbnails; inspect their pixels and run applicable independent OCR. No covered source pixels/canaries or original image carrier survive; source image and deliberately retained-image controls remain detectable. |
| RT-004 | PWS-0603, PWS-0606 | Inspect fresh builder provenance, all objects including unreferenced resources, decoded content streams, nested Forms, fonts/encodings, and object relationships. No original affected-page objects/content survive; object-number differences alone never prove this. Original-object controls fail. |
| RT-005 | PWS-0603, PWS-0605, PWS-0606 | Inspect file revisions, cross-reference chains, trailing bytes, prior trailers/Prev links, and recoverable earlier objects/streams. Fresh derivative carries no original revision; incremental-retention control must be recovered and rejected. |
| RT-006 | PWS-0605, PWS-0606 | Inspect entire derivative for embedded files/attachment streams, annotations/comments/appearances, AcroForm/XFA/field defaults and values, actions/scripts, document info/XMP and alternate metadata. Prohibited features/values absent under hardened policy; one planted carrier at a time fails. |
| RT-007 | PWS-0604, PWS-0606 | Prove OCR input/output exclusion, no source/cached layer reuse, and no text box crossing a removal region. Repeat extraction and independent OCR on sanitized image/page across supported languages, rotations, scans, and degraded samples. Covered canaries absent; non-redacted sentinels searchable within documented OCR limits. |
| RT-008 | PWS-0602, PWS-0603 | Test media/crop boxes, inherited rotations, 0/90/180/270 degrees, scaling, region edges, overlap, adjacent glyphs, and multiple disjoint regions. Every intended pixel is covered before encoding; supported outside-region fidelity and geometry remain within measured tolerances. Ambiguity fails closed. |
| RT-009 | PWS-0602, PWS-0603, PWS-0607 | Change manifest revision, page order/ID, crop/rotation, marks, source identity, operation/generation, or candidate after review/verification. Reject stale/cross-case/wrong-page results and reverify exact bytes/intent before commit. Original hashes remain equal. |
| RT-010 | PWS-0603, PWS-0606, PWS-0608 | Render all affected pages through multiple admitted independent paths; test optional-content/alternate appearances and outside-region sentinels. Reopen each RM-001 saved/resaved artifact and rerun RT-002 through RT-007 plus geometry/fidelity checks. Consistent appearance supplements extraction/object proof. |
| RT-011 | PWS-0603, PWS-0606 | Exercise truncated/malformed cross-reference/content/page trees, ambiguous/recovered parse, unsupported filters/geometry, and inspection-limit cases. Explicit unsupported/rejection code and no verified export; permissive viewer recovery is not proof. |
| RT-012 | PWS-0604, PWS-0606, PWS-0607 | Missing language/tool, OCR timeout/cancel/crash, empty/truncated/stale/malformed OCR, impossible coordinates, false text status, and verifier unavailable/error/unknown/skipped methods. Every required method must complete; otherwise block, preserve state, and expose sanitized remediation. |
| RT-013 | PWS-0606, PWS-0607 | Inject each failure taxonomy code, false/partial report, wrong digest/profile/method set, bypass attempt via every supported PDF/multiple-PDF/ZIP path, partial package, destination permission/disk failure, and failed retry. No final redacted commit/history success; previous-good output remains unchanged. |
| RT-014 | PWS-0603, PWS-0604, PWS-0605, PWS-0607 | Enforce central constrained ResourcePolicy just below/at/above bounds: one-page full-resolution materialization, memory, pixels, output/temp bytes, queues/concurrency, CPU/time, low-disk/disk-full and slow I/O. Cancel/crash/terminate at each stage; cleanup/reopen/retry preserve originals/last manifest, no source-bearing partial output escapes. |
| RT-015 | PWS-0602, PWS-0603, PWS-0607 | Signed-marker and unknown-signature input: warning/confirmation follows approved policy; any modified result has unsigned-derivative status and no claim of valid retained signatures. Reject forged signature/result states; preserve original hashes. |
| RT-016 | PWS-0605, PWS-0606, PWS-0607 | Scan success/failure/cancel/cleanup reports, logs, exceptions, accessibility announcements, and authorized diagnostics seams for every canary and prohibited text/pixel/path/name value. Reports contain only approved IDs/methods/status/codes/counts/versions; one planted disclosure fails. |
| RT-017 | PWS-0602, PWS-0607 | Verify unapplied/applied/unverified/verified/failed state transitions, ordinary-cover isolation, cancellation/retry, and no export success for incomplete or unknown checks. Schema/version/invalid-intent tests preserve old state. |
| RT-018 | PWS-0608 | Verify release enablement remains disabled before independent PWS-0608 approval, on absent/stale evidence, or with any incomplete prerequisite/required method. Exact approved source/profile/engine/corpus/method/viewer binding is required; tests cannot set release approval by passing themselves. |

Decoded stream scans and image OCR are complementary. Arbitrary raw-byte token absence cannot
prove the absence of encoded, compressed, encrypted, split, glyph-mapped, or raster content. A
verifier needs structural/provenance checks plus extraction and decoded-pixel evidence. Testing
must not consist solely of querying the same reconstruction code for its own success flag.

## Planned manual protocols

### PWS-0606 implemented verification and remaining evidence

The [private verifier contract](../architecture/redaction-verification.md) and
[viewer/resave procedure](../compatibility/pws-0606-redaction-viewer-resave-matrix.md)
now implement the automated verification seams. The original RT/RM tables below
remain the complete gate contract; implementing a seam does not complete every
scenario owned jointly with later tasks.

- [Structural regressions](../../tests/unit/test_redaction_verification_structure.py)
  cover decoded/escaped canaries, prohibited carriers, original payload hashes,
  indexed/unindexed objects, physical xrefs, revisions, malformed inputs and bounds
  for RT-002 through RT-006 and RT-011.
- [Visual regressions](../../tests/unit/test_redaction_verification_visual.py) cover
  native extraction/search, OCR character geometry, decoded image identities,
  nested Forms and all-page render preservation for RT-002, RT-003, RT-007 and RT-010.
- [The complete corpus](../../tests/security/test_redaction_verification_corpus.py)
  rejects all fourteen untreated sources and verifies thirteen acceptable
  reconstructed/sanitized cases, including PNG normalization, malformed rejection,
  all rotations and independent visible-preservation controls.
- [Native independent OCR and alternate rendering](../../tests/integration/test_redaction_verification_pipeline.py)
  supply positive unsafe-source controls and inspect candidate/resaved pixels.
  [The real OCR worker chain](../../tests/integration/test_redaction_verification_ocr_worker_windows.py)
  additionally compares genuine regenerated OCR and sanitized output to the exact
  trusted stage reference. These tests do not populate RM-001 human slots.
- [Typed evidence tests](../../tests/unit/test_redaction_verification_models.py),
  [orchestrator regressions](../../tests/unit/test_redaction_verification.py) and
  [helper integrity tests](../../tests/unit/test_pws0606_redaction_verification_harness.py)
  reject missing/forged/stale methods and bindings, unsafe recovery, resource
  failures, disclosures, changed resaves and incomplete human observations.

PWS-0606 remains **review** until RM-001 viewer/save/resave evidence is complete,
including the applicable independent OCR checks on each actual viewer-saved
artifact. Native automated OCR on a separate sample cannot fill those unknowns.
PWS-0607 still owns export integration and PWS-0608 independent approval; the
feature remains disabled.

### RM-001 — Multiple viewers and save/resave

Owner: PWS-0606, with final evidence reviewed by PWS-0608. Use the existing supported-reader
scope from [the compatibility strategy](../compatibility/pws-0506-portal-matrix.md) as the starting
matrix: Adobe Reader, native Edge, Chrome, Firefox, and one representative Android reader.
Record the actual viewer and version, operating environment, source/candidate/profile/corpus
revision and hashes, and method capabilities. Browser delegation to another viewer does not fill
that browser's slot. A required absent viewer or save capability remains NOT RUN until a documented
supported procedure supplies the evidence; do not quietly narrow the matrix.

PWS-0606 must provide one stable local helper entry point that prepares the exact synthetic
candidate and large labelled test targets, opens/identifies artifacts, records attempts, resumes
unfinished slots, and checks artifact hashes. That helper does not exist in PWS-0601. Do not ask
the reviewer to invent paths, calculate/transcribe hashes, edit JSON, or repeat accepted unrelated
M3/M5 compatibility evidence. Recovery must preserve prior attempts and report closed failure codes.

For each slot, reopen the candidate, inspect all marked targets and outside-region sentinels,
attempt selection/copy/search for each applicable canary, inspect available layers/comments/forms/
attachments, and use its ordinary save or save-a-copy operation. Reopen the saved artifact, repeat
those actions, save/resave once more using the matrix's supported procedure, and retain the exact
result for automated RT-010. Copy/search observations are recorded by canary ID without values.
Record unsupported viewer operations explicitly; the combined matrix must exercise every required
method. Every saved/resaved artifact must pass all applicable extraction, image, object, revision,
carrier, and OCR checks again. A screenshot, a hidden selection highlight, or an apparently black
region alone cannot pass. Any recovery or inconsistent/unknown required result fails the slot.

### RM-002 — Accessible review, failure, and signature wording

Owner: PWS-0602 and PWS-0607; PWS-0608 reviews the result. With keyboard-only controls and NVDA,
create/edit/remove regions through numeric/list alternatives, review page/region states, apply,
cancel, encounter a forced verification failure, recover focus, and retry. Check accessible
names/states, visible focus, logical tab order, progress/cancel/completion/error announcements,
200% scaling, non-color distinctions, and no timed review. Explain rasterization and accessibility
loss before application, and unsigned-derivative status for signed-marker sources. Ordinary shapes
and unapplied/failed candidates must never be announced as verified removal. Evidence uses large
synthetic targets and closed status observations; no removed values enter routine reports.

### RM-003 — Independent release review

Owner: PWS-0608. A reviewer independent of the implementation checks the exact source/candidate,
corpus, profile, engine/language versions, full RT results, RM-001/RM-002 attempts, unsupported
boundaries, and resolved findings. The reviewer reproduces representative successful derivatives
and unsafe controls, including text/image/object/revision/OCR recovery and export-bypass attempts.
No self-attestation, visual-only review, warning, missing capability, or incomplete prerequisite
can substitute. Record the reviewer identity/role, exact evidence binding, findings, and explicit
pass/fail approval in the gate report without document values. Approval is release-specific;
material boundary/engine/profile changes require current evidence and renewed review.

## Exhaustive ROADMAP M6 verification mapping

Each row below corresponds to the required implementation boundary or one exit criterion in
[ROADMAP M6](../../ROADMAP.md#m6--permanent-redaction). IDs refer to the planned methods above.
PWS-0601 completes planning/corpus evidence only; every runtime row stays pending until its owners
record real executed results. No row permits visual-only proof.

| M6 requirement | Automated method and required proof | Explicit manual protocol | Implementation/release owner |
|---|---|---|---|
| Whole affected page reconstructed from sanitized raster; burn before encode; original page objects discarded; OCR only outside regions | RT-003, RT-004, RT-007, RT-008, RT-009, RT-014: pixel provenance/masks, fresh object inventory, excluded OCR, geometry and bounds | RM-003 reviews implementation boundary | PWS-0602, PWS-0603, PWS-0604, PWS-0606, PWS-0608 |
| Corpus includes born-digital text, scanned text, images, annotations, form values, attachments, metadata, incremental revisions, and malformed PDFs | RT-001 validates all classes/positive controls; RT-011 verifies malformed rejection; PWS-0601 additionally requires hidden layers, rotated/cropped pages and OCR | RM-003 audits coverage | PWS-0601, PWS-0606, PWS-0608 |
| Redacted text cannot be selected, copied, searched, or extracted | RT-002 plus RT-007: independent decoded extraction and OCR checks, detectable unsafe cover control | RM-001 selection/copy/search in every required viewer | PWS-0603, PWS-0604, PWS-0606 |
| Image extraction cannot reveal redacted source pixels | RT-003: enumerate/decode all image carriers and masks; pixel/OCR recovery controls | RM-003 reproduces image recovery attempts | PWS-0603, PWS-0606 |
| Content-stream and incremental-revision inspection cannot recover original affected-page objects | RT-004, RT-005: complete decoded/unreachable object and prior-revision inspection, fresh construction provenance | RM-003 independent object/revision inspection | PWS-0603, PWS-0605, PWS-0606 |
| Attachments, comments, form values, actions, and metadata do not contain the redacted value | RT-006: global feature absence/approved inert metadata, planted carrier failures; RT-016: no report/log leakage | RM-001 viewer feature panels supplement structural proof | PWS-0605, PWS-0606 |
| OCR cannot recreate text beneath a redaction | RT-007, RT-012: sanitized input/output exclusion, independent source/derivative OCR, stale/failed OCR rejection | RM-003 reviews mask and OCR provenance | PWS-0604, PWS-0606 |
| Output renders consistently in multiple viewers and remains redacted after resave | RT-010 reruns RT-002 through RT-007 on exact saved/resaved artifacts, plus geometry/sentinels | RM-001 complete viewer/save/resave matrix | PWS-0606, PWS-0608 |
| Verification failure blocks export and emits a sanitized report | RT-009, RT-012, RT-013, RT-014, RT-016, RT-017: every failed/unknown/incomplete method, mutation/bypass/retry/low-disk path blocks commit, code-only report | RM-002 accessible failure/recovery; RM-003 bypass review | PWS-0606, PWS-0607 |
| Signed sources produce only explicitly unsigned derivatives | RT-015: signed-marker/unknown state and original hash controls | RM-002 warning and output status; RM-003 review | PWS-0602, PWS-0603, PWS-0607, PWS-0608 |
| Independent review approves PWS-0608 before release-build enablement | RT-018: disabled state and exact complete evidence binding | RM-003 explicit independent approval; RM-001/RM-002 complete | PWS-0608 |

## Release-blocking failure taxonomy

These are stable **planned** method/result codes for M6 implementation and gate evidence.
PWS-0606/PWS-0607 must register typed equivalents and contract tests before use; this document
does not add them to a runtime schema. Codes expose neither canary values nor raw library errors.
Any code below prevents redacted-export success and PWS-0608 approval. A recovered canary has
the most specific carrier code; an incomplete inspection uses the unknown/method code even when
no canary was observed. All applicable findings remain visible by method/ID.

| Code | Trigger | Plain-language recovery/status | Owner and evidence |
|---|---|---|---|
| PWS_REDACTION_CORPUS_INVALID | Missing class/carrier, invalid provenance/digest, duplicate canary, failed positive control | Test coverage is incomplete. Keep permanent redaction disabled. | PWS-0601, PWS-0606; RT-001 |
| PWS_REDACTION_TEXT_RECOVERED | Text/copy/search/extraction recovers removed content | Information may remain in the candidate. Review the marked pages and retry. | PWS-0606; RT-002 |
| PWS_REDACTION_PIXELS_RECOVERED | Image extraction or masks expose covered source pixels/content | Original image information remains. The candidate cannot be exported. | PWS-0603, PWS-0606; RT-003 |
| PWS_REDACTION_OBJECT_RETAINED | Original affected-page content/resource/object survives, including unreachable objects | Original page content remains. The candidate cannot be exported. | PWS-0603, PWS-0606; RT-004 |
| PWS_REDACTION_REVISION_RETAINED | Original incremental history or earlier objects remain recoverable | Earlier document information remains. The candidate cannot be exported. | PWS-0603, PWS-0605, PWS-0606; RT-005 |
| PWS_REDACTION_CARRIER_RETAINED | Attachment, annotation/comment, form, action/script, metadata, hidden/alternate carrier violates policy | Extra document information remains. The candidate cannot be exported. | PWS-0605, PWS-0606; RT-006 |
| PWS_REDACTION_OCR_UNSAFE | Covered pixels/text enter OCR, stale source layer reused, covered text recovered | Text recognition did not pass the removal checks. Review and retry. | PWS-0604, PWS-0606; RT-007, RT-012 |
| PWS_REDACTION_GEOMETRY_UNPROVEN | Mask alignment, rounding, page geometry, or outside-region fidelity is wrong/unknown | The marked area could not be applied reliably. Review the page and retry. | PWS-0602, PWS-0603; RT-008 |
| PWS_REDACTION_BINDING_INVALID | Source/intent/page/revision/candidate/digest/profile/report changes or mismatches | The document or removal plan changed. Review it before retrying. | PWS-0602, PWS-0606, PWS-0607; RT-009, RT-013 |
| PWS_REDACTION_VIEWER_RESAVE_FAILED | Required render/viewer/save/resave reveals content or has an incomplete required outcome | Viewer and saved-copy checks are incomplete or failed. Keep the candidate unverified. | PWS-0606, PWS-0608; RT-010, RM-001 |
| PWS_REDACTION_INPUT_UNSUPPORTED | Malformed, ambiguous/recovered, encrypted, oversized, or unsupported content cannot be safely processed | This document cannot be processed safely with the supported removal method. | PWS-0603, PWS-0606; RT-011 |
| PWS_REDACTION_METHOD_INCOMPLETE | Required method/tool/language unavailable, unknown, failed, timed out, skipped, or missing | A required check did not finish. Resolve the reported capability and retry. | PWS-0604, PWS-0606, PWS-0607; RT-012 |
| PWS_REDACTION_EXPORT_BLOCKED | Any unsafe/unknown outcome or bypass/partial commit attempt reaches export | Removal checks did not pass. No final redacted export was committed. | PWS-0607; RT-013, RT-017 |
| PWS_REDACTION_RESOURCE_FAILED | Budget/low-disk/disk-full/timeout/termination/cancellation or cleanup/recovery failure | Processing stopped safely or needs recovery. Resolve the reported resource issue and retry. | PWS-0603 through PWS-0607; RT-014 |
| PWS_REDACTION_SIGNATURE_STATE_INVALID | Signed/unknown source bypasses warning or modified output claims retained signature validity | Review the source signature warning. A modified output must be an unsigned derivative. | PWS-0602, PWS-0603, PWS-0607; RT-015 |
| PWS_REDACTION_REPORT_UNSAFE | Removed values, source text/pixels/names/paths, or raw exceptions reach reports/logs | The report failed privacy checks. Keep permanent redaction disabled pending correction. | PWS-0605 through PWS-0607; RT-016 |
| PWS_REDACTION_STATE_INVALID | Ordinary shape/unapplied/failed/unverified state is promoted or mislabelled | Removal has not been verified. Review its status before retrying. | PWS-0602, PWS-0607; RT-017, RM-002 |
| PWS_REDACTION_GATE_CLOSED | Independent approval absent/stale/failed or prerequisites/evidence incomplete | Permanent redaction is not enabled for this release. | PWS-0608; RT-018, RM-003 |

Cancellation itself need not be a product defect, but its candidate is never verified or committed.
A cleanup failure must remain actionable and cannot be converted into a success code. Reports
identify only internal page/operation IDs, method IDs, pass/fail/unknown, stable codes, numeric
counts, and approved engine/schema/profile versions. Content-bearing inventories stay in the
protected test/case context, never routine reports. Previous verified outputs and original hashes
must remain unchanged after every failed attempt.

## Evidence, change control, and standalone boundary

PWS-0608 must index exact corpus/source/profile/engine/language/candidate revisions and hashes,
concrete automated test nodes and results, manual attempt artifacts/results, independent review
and resolved findings, required capability inventory, and supported limitations. Gate records
must distinguish completed PWS-0601 fixture/coverage validation from pending runtime proof. M8
must re-evidence the accepted redaction boundary on the exact packaged candidate under PWS-D022
and PWS-D040. Material changes to rendering, mask rounding, object assembly, OCR, sanitization,
verifiers, or enabled profiles require the affected automated/manual checks and independent review
to remain current. No approval follows solely from updating a task status.

This work is useful entirely within PaperWorkShell. It introduces no sibling repository, service,
runtime schema, adapter, data access, or portfolio compatibility dependency. PaperWorkShell owns
all case, OCR, region, derivative, and gate data under PWS-D042. The release interface remains
human-readable and draft where unproved; PWS-0601 establishes no packaged-release or ThirdLife
compatibility claim and requires no release-document fact to be guessed.
