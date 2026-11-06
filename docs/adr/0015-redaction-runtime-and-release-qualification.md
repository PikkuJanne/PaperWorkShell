# ADR 0015: Separate exact-file removal checks from release viewer qualification

- Status: Accepted implementation decision; final qualification bound to 7e2ea7ae1fd3eaf6a7882fc5f236f101e7424ab39ac57fd19dbca132eb9a44c9
- Date: 2026-11-01
- Decisions preserved: PWS-D009, PWS-D012, PWS-D013, PWS-D022, PWS-D023,
  PWS-D029, PWS-D030, PWS-D036, PWS-D037, PWS-D041, PWS-D042
- Task: PWS-0608, remediation of the first failed independent gate

## Problem

The private canonical verifier describes seven evidence methods for exact bytes.
Its all-seven `release_ready` property correctly requires a viewer matrix for
those bytes. The first export composition had no independent OCR runner or viewer
runner, so newly generated candidates could never pass. No desktop action invoked
that composition. The first independent gate therefore rejected release.

Human observations qualify the tested implementation and supported output
profile on the exact synthetic artifacts they inspected. They cannot become
invented observations of a different user document. Requiring users to reproduce
the five-reader human matrix for every ordinary export would also make the
offline desktop workflow unusable.

## Decision

Keep the existing canonical and viewer-resave profiles and their version-1
reports unchanged. In those profiles all seven methods still determine
`release_ready`; unknown methods remain unknown and cannot be relabeled.

Introduce the explicit `redaction-runtime-verification-v1` request profile for
the supported composed one-page image-only derivative. Its required exact-file
checks are objects/revisions/features, text extraction, image extraction,
rendering, fresh full resave, and actual independent local OCR. All six must
pass. The OCR check executes on the candidate and its internal full resave;
missing capability, failed recognition processing, unknown result, changed input
or failed cleanup blocks publication. Only the authenticated worker/broker chain
can supply this request and result.

The runtime report never supplies a viewer-matrix pass for a file that was not
opened in those readers. That method remains explicitly unknown. A failed viewer
result still blocks export. Runtime readiness is a separate property and grants
no release or publication authority.

Viewer compatibility remains a mandatory **release-profile qualification** under
PWS-D022. The exact supported reconstruction/sanitization implementation, engines,
profile, adversarial corpus, actual viewer saves/resaves and independent review
must have current evidence before the build gate can enable publication. The
application checks both the compiled feature gate and qualified runtime-profile
identity, in addition to the exact-file checks, current reviewed intent, immutable
source, broker-owned candidate and authenticated no-replace destination. No
environment setting, user report, manifest, UI checkbox or historical observation
can supply approval.

Version-2 user/audit export reports distinguish these scopes. They list the six
required candidate methods and explicitly state that the output was not opened
in the named viewer matrix. Version-1 export-report wire fields and meanings are
preserved; unsupported future versions fail closed. Reports remain data, not
capabilities to publish or promote saved marks.

## Evidence and approval

Retain the original failed gate and findings. Refresh inspection of the exact
accepted viewer artifacts with append-only receipts; preserve their original
observations and generation inputs. Composition-only added modules need a closed,
tested provenance exception proving that the original generator does not consume
them. That exception cannot permit changed or missing generation inputs.

Test both profiles, old/current/future report versions, every missing or failed
runtime method, invented viewer passes, stale/current intent, source/candidate
mutation, unsigned consent, capacity, cancellation, timeout, cleanup, retry and
previous-output preservation. Exercise actual native OCR and the full desktop
application path using the restricted worker and real destination port. Repeat
independent security review before enabling the feature or completing PWS-0608.

This decision does not approve the gate, waive viewer/OCR/resave coverage, or
claim that historical artifacts are new per-file observations. A final build
switch change, if approved, needs separately proved noninterference with the
retained artifact generation and fresh automated inspection.

## Scope and consequences

The initial runtime composition remains restricted to one affected PDF page and
its documented crop/quarter-turn and payload limits. It exports a fresh image-only
unsigned derivative and does not promise regenerated searchable text. Existing
ordinary-export blockers and unapplied saved intent remain intact. Unsupported
whole-case combinations fail before worker publication.

There is no new dependency, network capability, budget increase, manifest
migration, sibling application, shared data store or release-interface fact.
Candidate OCR is local and private; recognized text never enters routine reports
or logs. Existing resource policies bound the additional child process, time,
memory and private temporary files. Actual viewer, OCR and resource observations
retain their exact scope; there is no universal safety, hardware certification,
forensic-erasure, OCR-certainty, legal-validity, portal-acceptance or PDF/UA claim.


## PWS-0608 qualification evidence

Current source-tree qualification: **PASS**, source `7e2ea7ae1fd3eaf6a7882fc5f236f101e7424ab39ac57fd19dbca132eb9a44c9`, checkpoint `9a4a74c08efd0bc30c9439288f511ef37dba755c`. Independent approval: `output/pws0608-repair/independent-final-source-approval-v1.json`. The supported M6 source-tree removal export is qualified for exactly one active affected PDF page, with documented crop/quarter-turn and bounded payload limits. This is not a packaged/public v1.0 release; M7 and M8 remain pending under PWS-D040.

The explicit redaction-runtime-verification-v1 profile requires five built-in methods (objects/revisions/features, text extraction, image extraction, rendering and fresh full resave) plus the fixed private English (eng) OCR audit on the exact candidate and full resave. All six must pass. The output stays image-only, with no regenerated searchable text. Named-viewer testimony for this new file remains UNKNOWN; compatibility is mandatory release-profile qualification, not invented per-file observation. The original canonical/viewer version-1 profiles retain all-seven release_ready semantics.

Publication also requires the compiled feature switch and exact qualified runtime profile, current reviewed intent/source integrity, broker-owned single-use candidate authority and an authenticated no-replace destination. Review of saved marks and warnings is required for every export; an extra unsigned-derivative confirmation is required only for source signature markers with consent still pending. All outputs are unsigned. Reports, environment variables, manifests and UI inputs cannot grant approval. Saved marks remain unapplied; ordinary export paths retain active-mark blockers.

Destination uncertainty is latched before context unwind and remains UNCERTAIN with no automatic retry across closer failures or exception suppression. A confirmed committed derivative stays committed; it is not relabelled as unpublished when later cleanup fails. Private worker cleanup before publication still blocks publication on failure. These distinct states are covered by current source-bound service regressions; no new error codes or schema meanings are introduced.

Evidence: approved native `output/pws0608-repair/verified-native-desktop-plain.json`; current viewer status/verify `output/pws0608-repair/verified-viewer-status-v3.json` / `output/pws0608-repair/verified-viewer-inspection.json`; OCR noninterference `output/pws0608-repair/ocr-noninterference-attestation-v1-01.json`; UI `output/pws0608-repair/native-success-ui-nvda-observations-v2.json`. Exact commands, source epochs and limitations are in `docs/development/pws0608-remediation.md`.

Final metadata checks are recorded externally in output/pws0608-repair/final-files.json and output/pws0608-repair/final-closure-validation.json. This report does not claim outcomes for checks not yet run and does not embed their downstream digests: the inventory and receipt hash this report. Publication requires successful schema, task-graph, documentation, portfolio, full secret-scan and Git whitespace checks with unchanged code and Git identities.

The factory uses O=min(base output allowance,floor(base temporary allowance/4),floor(central policy temporary budget/4)); O<1 or fewer than two permitted processes is refused. The broker requires source bytes + encoded plan bytes +3*O<=temporary allowance before allocation/launch; equality is allowed. The quarter-budget cap is conservative headroom, not a promise that every source/plan fits. Capacity still fails closed and no central budget was raised.

This preserves PWS-D022 and the original all-seven profile; no frozen decision is replaced.
