# Current verification-profile scopes

The explicit redaction-runtime-verification-v1 profile requires five built-in methods (objects/revisions/features, text extraction, image extraction, rendering and fresh full resave) plus the fixed private English (eng) OCR audit on the exact candidate and full resave. All six must pass. The output stays image-only, with no regenerated searchable text. Named-viewer testimony for this new file remains UNKNOWN; compatibility is mandatory release-profile qualification, not invented per-file observation. The original canonical/viewer version-1 profiles retain all-seven release_ready semantics.

Publication also requires the compiled feature switch and exact qualified runtime profile, current reviewed intent/source integrity, broker-owned single-use candidate authority and an authenticated no-replace destination. Review of saved marks and warnings is required for every export; an extra unsigned-derivative confirmation is required only for source signature markers with consent still pending. All outputs are unsigned. Reports, environment variables, manifests and UI inputs cannot grant approval. Saved marks remain unapplied; ordinary export paths retain active-mark blockers.

Approved implementation `7e2ea7ae1fd3eaf6a7882fc5f236f101e7424ab39ac57fd19dbca132eb9a44c9`; current profile evidence is recorded in the remediation report. The original PWS-0606 canonical/viewer method specification below remains the all-seven contract; its then-disabled integration status is historical, not a current source qualification claim.

Exactly one page may be in the active sequence and it must be the affected PDF page; even an additional unmarked active page is unsupported. Its source PDF may contain other pages, but must be complete and unencrypted. Active overlays, deskew/contrast/background cleanup and unsupported payload/filter combinations fail closed; only the proved crop and quarter-turn intent with bounded unfiltered/single-Flate payloads is supported. Every derivative is fresh, image-only and unsigned; searchable text, forms and accessibility structure are not promised. There is no universal hostile-PDF safety, forensic-erasure, OCR-certainty, hardware, portal-acceptance, legal-validity or PDF/UA claim.

## Historical canonical/viewer component specification

This accepted earlier specification remains the seven-method contract for those profiles, not the current runtime integration status.

# Private multi-method redaction verification

PWS-0606 implements a reusable worker-only verifier and a synthetic development
suite. The [redaction threat model](../security/redaction-threat-model.md) remains
the security contract. PWS-0607 adds the separate
[worker/export gate and sanitized result reports](redaction-export-gate.md).
Permanent-removal export remains disabled pending PWS-0608 independent release approval.

## Inputs and trust

The versioned `RedactionVerificationRequest` binds exact candidate and reference
byte counts and SHA-256 hashes, page count, affected page indices, reviewed intent
fingerprint, profile, and the digest of the ephemeral probe plan. The reference
must be the authenticated result of reconstruction and optional regenerated OCR,
before final sanitization. An arbitrary input, the candidate itself, or an
unauthenticated renderer output cannot serve as a removal oracle. The caller must
retain the upstream reconstruction/OCR/request bindings; this verifier cannot
establish those facts from two PDFs alone.

Affected pages require original affected-content/image stream fingerprints.
Generic raster placement instructions may match a newly generated page and do
not carry removed information; the corpus fingerprints their source image
samples instead. Other affected content and Form streams are fingerprinted.
Ephemeral synthetic canary probes supplement that inventory. Probe values never
appear in request records, report records, routine logs, representations, or
exception messages. The report binds the probe digest, not the values.

The adapter accepts already-open private streams and a worker-private scratch
directory. It preserves caller stream positions and ownership, checks input
hashes before and after all methods, writes one exclusive resave file, and removes
that file on success and failure. It does not mutate an original, manifest,
removal mark, or existing export. No GUI entry point imports it and no publication
capability or new RPC is added by this task.

## Required methods

| Method | Evidence and failure conditions |
|---|---|
| Objects, revisions and features | Independent physical classic-xref/object coverage, strict parsing, bounded raw/decoded stream and string inspection, source stream fingerprints, unreachable-object rejection, prohibited global carriers, trailing/revision data |
| Text selection, search and extraction | PDFium character extraction and native search, exact reference text and character geometry; any recovered canary fails |
| Image extraction | All page image objects, including nested Forms and off-canvas objects, must have the exact decoded pixel identities of the authenticated reference; structure inspection covers unused/unreachable resources |
| Rendering | Every page preserves geometry and exact 300 dpi PDFium pixels relative to the reference |
| Save/resave | Fresh nonincremental pikepdf save followed by all structural, text, image and rendering checks; supplied independent OCR/viewer methods repeat on the resaved bytes |
| Independent OCR | Required external evidence; the native test positively recognizes the unsafe source, then checks the reconstructed candidate and resave, including a retained visible control |
| Viewer matrix | Required external evidence; [the matrix](../compatibility/pws-0606-redaction-viewer-resave-matrix.md) describes independent renderers and human select/copy/search/resave slots |

The structural method additionally compares the complete page/resource graph to
the trusted reference, including unused images, fonts, nested Forms, font binary
tables, masks, CMaps and page content. Catalog and page-tree roles are closed;
inheritance flattening is disabled during inspection so an overridden parent
resource cannot disappear before comparison. Candidate-only resource metadata,
noncanonical font names, and incorrect font lengths/checksums are rejected.
Font normalization permits only authenticated SFNT head timestamps and derived
checksums; all other bytes, including table layout, names and unused glyph data,
remain bound. This deliberately fails closed if another font metadata rewrite
cannot be matched independently.

Methods have closed identities, `pass`, `fail` or `unknown` status, and closed
`RV_*` codes. A runner exception, wrong method identity, malformed/forged result,
omission, future record, or stale binding fails closed. Reports list all methods
in a fixed order. Missing methods are explicitly unknown. `automated_passed`
covers the five built-in automated methods; `release_ready` requires every method
to pass and does **not** replace independent PWS-0608 approval. The built-in
verifier does not manufacture human viewer evidence. These records are evidence
data, never trusted export capabilities.

## Bounds and supported profiles

The central constrained `ResourcePolicy` produces the existing
`RedactionReconstructionLimits`; no resource budget changes. Work is sequential,
with only the current decoded image or raster in memory. Probe counts/lengths,
object traversal/depth, stream expansion, PDF bytes, pages, pixels, text, scratch
output and resave writes are bounded. Cancellation and deadlines are checked
throughout Python work and resave progress/writes. Native calls additionally
require the worker's existing process memory, CPU, timeout, egress and filesystem
restrictions. Running the synthetic test process is not proof of a new worker
operation's isolation; that integration remains PWS-0607 work.

The default `redaction-verification-v1` profile deliberately accepts final, fresh,
non-linearized classic-xref sanitizer output with unfiltered or Flate streams. Object streams,
filter chains, predictors, unknown dictionaries, lexical comments in generated
content, hidden optional content and unsupported carriers fail closed. A binary
payload containing a revision marker can be conservatively rejected. Matching a
source payload hash is also conservatively rejected even if its reuse might be
harmless. Supporting more PDF profiles requires new evidence; unsupported methods
are never converted into a successful absence check.

The separate, explicit `redaction-viewer-resave-verification-v1` profile inspects
retained reader-generated copies. It changes the inspection envelope, not the
sanitizer's export contract. The request and report bind this profile identity;
it cannot silently substitute for the strict default. It accepts the default
classic envelope or a closed linearized envelope proved from the original bytes:
two classic xref sections must partition one complete physical object set, every
offset and object extent must match, and the forward `/Prev` must be that
linearization link. A real earlier revision, duplicate or unindexed object,
trailing content, object stream, or unsupported auxiliary remains rejected.
Linearization and hint objects are fully parsed bounded numeric structures,
including page/shared-object rows, padding bits and the optional Info hint. Their
sizes, references and offsets must agree with the physical file; hints are never
an opaque byte exception or evidence that protected content is absent.

Viewer metadata is permitted only as the exact observed inert Adobe serialization:
paired Info dates and catalog XMP, with valid cross-linked dates, two canonical
version-4 UUIDs and `application/pdf`. Namespace, element/attribute sets, toolkit,
prolog, layout and padding are closed. DTDs, entities, comments, extra properties
and arbitrary whitespace fail. Raw and decoded metadata and supported date/UUID
decodings are checked for probes and source fingerprints; UUID concatenations
are checked across the two fields. Only the exact validated auxiliary objects
are exempted from the ordinary closed page/resource grammar. Catalog metadata
does not authorize metadata on a page, resource, font or nested object.

Resource comparison normalizes only a redundant CropBox exactly equal to its own
MediaBox, integer Rotate zero, and the observed truncation of authenticated
MediaBox coordinates to 13 significant decimal digits. A changed coordinate must
be the exact truncation toward zero of a reference magnitude from 10 up to 1000
points, with an absolute difference below `1e-10` points; longer nonzero digit
tails and other perturbations fail. Content operators and every resource number
remain exact, as do unused binary resources, fonts and CMaps. Exact full-page
pixels and character geometry remain mandatory independent checks.

Every actual saved file is inspected before the verifier makes its own fresh
nonincremental resave and repeats all built-in methods. That internal viewer
resave preserves validated metadata bytes with `fix_metadata_version=False`;
it does not delete metadata or rewrite an unsupported original into a passing
input. Neither profile enables permanent-removal export.

## Verification entry point

Run `python tools/dev.py pws0606-redaction-test` for fast, noninteractive status of
the retained synthetic session. Run `python -m tools.pws0606_viewer_resave verify`
for its separate automated recheck. Normal `test-unit`, `test-security` and
`test-integration` commands include the new tests. The suite uses all fourteen
adversarial cases, including explicit malformed rejection and untreated source
controls, plus intentional retained text/image/object/carrier/revision mutations.
It verifies source detectability separately from removal and checks visible
nonremoved controls so blank output cannot pass.

The completed human helper and its preparation, query plan, observations and ten
actual saved files remain pinned and unchanged. The retained-session checker
accepts inspection-only implementation changes while requiring all generation
inputs, artifacts, observation digests and environment provenance to remain
bound. It rechecks the packet under the strict canonical profile, then records
append-only viewer-profile receipts under the existing workspace's
`viewer-verification/` directory. Historical canonical failures remain intact.
Fast status validates receipts and hashes without repeating document processing
or prompting the person. Human observations need no repetition for this
inspection compatibility fix. Future sessions follow the project
[human-testing contract](../development/human-testing.md), including a review of
total workload before handoff.

The existing independent OCR evidence covers the exact ten actual reader saves;
the accepted human observations cover the actual named-reader interactions.
Neither is copied onto internal qpdf-generated bytes. The retained-session
checker supplies no external method runners, so its built-in reports keep
independent OCR and viewer methods `unknown`, even when the separate actual-file
evidence is current. Its receipts establish built-in automated checks only;
PWS-0607 integration and PWS-0608 independent approval remain separate.

No dependency, released manifest/RPC meaning, golden reference, source fixture,
data location, product boundary, sibling dependency or proved packaged-release
fact changes. Results demonstrate bounded software behavior on synthetic inputs;
they do not establish hardware certification, universal redaction, OCR certainty,
signature validity, forensic erasure, PDF/UA conformance or portal acceptance.
