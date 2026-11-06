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

# Milestone 6 permanent-redaction release gate — PWS-0608

- Review date: 2026-11-01
- Gate conclusion: **FAIL — release approval withheld**
- Task status: **review**, not done; completion date remains unset
- Permanent removal: **disabled**; `PERMANENT_REDACTION_ENABLED` remains false
- Reviewed snapshot: `output/pws0608/qa-files.json`, SHA-256
  `6eb9ea7525d478e7699a47852bd165d72e0a3166b26e307809f7f54211b1a27d`
- Local parent: `2e70ec0e7174dab342f6df43d7b4d685f84a3259`

## Scope and authority

This gate evaluates the current inherited working-tree implementation, not just
the parent commit. Authority is [DECISIONS.md](../../DECISIONS.md), especially
PWS-D022, PWS-D023, PWS-D029 and PWS-D040; the
[M6 roadmap](../../ROADMAP.md#m6--permanent-redaction);
[PWS-0608](../../TASKS.yaml); and [AGENTS.md](../../AGENTS.md).

The independently reviewed source inventory contains 1,115 accepted files. It
excludes unrelated untracked documents, `tmp/`, local cases and prior test output.
Fresh tests use a new owned clone and a newly installed locked environment from
`uv sync --locked --offline --all-groups`. Existing dependency pins, the optional
renderer-evaluation development group and the admitted Tesseract closure are
reused. No new dependency or network service is introduced.

The runner authenticates every inventoried root and clone file before and after
each command. Logs and source-bound receipts are retained in `output/pws0608/`.
Gate documentation and final task/README/bundle metadata are a separate closure
phase; they do not change the tested implementation. Earlier M5/M6 changes and
the original index remain preserved.

## Prerequisites and acceptance

All seven prerequisites are recorded done. Their historical evidence is preserved:

| Task | Accepted result |
|---|---|
| PWS-0601 | [Threat model and adversarial corpus](../development/pws0601-result.md) |
| PWS-0602 | [Unapplied mark intent and accessible review](../architecture/redaction-intent-review.md) |
| PWS-0603 | [Whole-page masked reconstruction](../development/pws0603-result.md) |
| PWS-0604 | [Masked-only OCR regeneration](../development/pws0604-result.md) |
| PWS-0605 | [Fresh derivative sanitization](../development/pws0605-result.md) |
| PWS-0606 | [Multi-method verification and retained reader evidence](../development/pws0606-result.md) |
| PWS-0607 | [Private export coordination and sanitized reports](../development/pws0607-result.md) |

| PWS-0608 criterion | Gate assessment |
|---|---|
| Every prerequisite done | PASS in the current graph; this does not imply an end-to-end release path |
| No canary recoverable through every supported method | NOT ESTABLISHED for the actual composed release path: required OCR/viewer methods remain unknown, and retained viewer inspection is stale |
| Every intentionally unsafe fixture blocked | Fresh security/corpus results are recorded below; private successes do not approve release |
| Signed derivatives and accessibility warnings correct | Existing source policy, unsigned result/report and rasterization warnings retained; affected security/accessibility/native checks recorded below |
| Independent review approves release or gate remains failed | **REJECTED** by the separate source, verifier and evidence reviews; gate remains failed |

## Unresolved independent findings

The [independent review report](../security/pws0608-independent-review.md)
records exact code locations, scope, safeguards and resolution requirements.

1. **M6-R1:** Real composed candidates cannot supply trusted independent OCR or
   viewer passes. The adapter provides no external runners; the broker rejects
   invented passes; final coordination correctly blocks unknown methods. Turning
   on the build switch cannot make the actual candidate publishable.
2. **M6-R2:** The desktop does not invoke `RedactionExportService`. It saves
   unapplied intent and displays truthful blocked-export reports. Private native
   preparation and trusted-port publication tests do not establish a usable
   production removal-export journey.
3. **M6-R3:** Current PWS-0606 viewer status fails closed on changed provenance.
   Accepted human artifacts and exact-file OCR remain intact, but old inspection
   receipts do not establish a current pass.

No additional demonstrated content-recovery or publication-bypass exploit was
found by the read-only reviews. The findings concern missing release capability
and current evidence. They must not be described as successful removal, a proved
recovery exploit, or independent release approval.

## Verification

Fresh command results and retained evidence are tabulated in the
[completion record](../development/pws0608-result.md). The source-bound command
receipts carry command, selection, actual exit, duration, input-inventory digest,
before/after identity checks and log digest. Every redaction integration case is
selected with `PYTEST_ADDOPTS='-vv -k redaction'` through the stable
`python -m tools.dev test-integration` command. The full security suite is run
without a selection. Native suites run serially.

The failure paths include unsafe covering and untreated sources, hidden carriers,
revision/resource/metadata attacks, unknown or forged methods, immutable-source
checks, cancellation, timeout, termination, disk/output/temp limits, cleanup and
retry. Native resource assertions use the unchanged constrained policy. Results
are software-budget evidence, not physical-device certification.

The accepted PWS-0607 full unit result, 6,488 passes, remains historical evidence
on unchanged implementation/test bytes. Its 1,111-file implementation inventory
matched at audit. It is not represented as a fresh PWS-0608 full-unit execution.
The fresh focused unit run uses `-q -k "redaction or pws0606"`. Likewise,
the fresh integration selection does not claim a rerun of unrelated integration
tests or turn any earlier failed full run into a pass.

## Retained reader, resave and OCR evidence

The [five-reader matrix](../compatibility/pws-0606-redaction-viewer-resave-matrix.md)
retains the accepted 17 October human observations and both actual saved copies
per reader. No human session, file generation or OCR recognition was repeated.
Read-only audit receipt `output/pws0608/retained-evidence-audit-v2.json`, SHA-256
`a4374e4c697b4745c5fb7bd7c8f2dcff8a3278a306800b9e1292b5fbdaf472f1`,
confirms:

- The original evidence, packet, reference and unsafe-control identities match.
- All five observation, guidance and environment bindings validate, and all ten
  actual saved-file hashes match.
- The original OCR receipt, SHA-256
  `318bd196d643bdaac98be46b025c70d69ec511a6b39677f5aa70f60f0f7749d7`,
  still matches eleven implementation hashes, thirteen source fixtures, ten
  actual files, environment/evidence bindings and forty admitted Tesseract files.
- Current viewer status exits **1**; direct diagnosis is
  `PWS0606_GENERATION_CHANGED`. Three newly globbed export-composition modules and
  four changed inspection modules prevent a current inspection claim. There are
  no changed original non-inspection generation files.

The audit's PASS means these preservation and failure-diagnosis checks passed.
Its `gate_status` is explicitly FAIL and approval is REJECTED. The first audit
helper compared an identity dictionary with a digest string, producing ten false
mismatches. Its original FAIL receipt remains unchanged at SHA-256
`cd0c4452d27af4cb25f7660d3ea5f7a7e5fc35a245a8da93ccbbecb696db43be`.
The separate v2 helper compares the correct digest fields; it changes no product
source or accepted evidence. Both epochs remain available.

## Supported state, boundaries and next work

The private composition is limited to one affected PDF page, supported crop and
quarter-turn intent, and bounded admitted unfiltered/single-Flate payloads. It
rejects unsupported combinations, overlays and cleanup. It does not compose
regenerated OCR. Candidate/reference agreement proves chain preservation; the
separate mask/corpus/OCR checks remain necessary to establish removal. Saved marks
remain unapplied and ordinary export paths retain their blockers.

First resolve M6-R1 and M6-R2 through a reviewed supported production path and
exact-candidate evidence. Resolve M6-R3 with narrowly tested lineage handling and
append-only automated inspection of the unchanged accepted files. Preserve their
human observations and exact-file OCR proof. Do not copy their passes to new bytes.
Any necessary future human journey must be prepared and assessed under the
[human-testing policy](../development/human-testing.md).

Repeat independent review after resolution. Do not enable permanent removal or
advance PWS-0608 to done before approval. No prerequisite is retroactively
relabeled, no frozen decision is changed, and no finding is waived.

PaperWorkShell remains standalone and owns its private document/case data. There
is no sibling repository, adapter, service, shared content schema, migration,
data-location change or release-interface claim. Packaged facts remain draft/TBD.
The report supplies no portal, legal, OCR-certainty, forensic-erasure or PDF/UA
guarantee. No later task is started: **PWS-0608 remains the dependency-ready
unfinished task in review**, and there is no newly ready later task.
