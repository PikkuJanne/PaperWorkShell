# Private redaction derivative sanitization

- Task: PWS-0605
- Decisions: PWS-D009, PWS-D012, PWS-D013, PWS-D022, PWS-D023, PWS-D027, PWS-D029
- Status: Private candidate stage; permanent-removal export remains disabled

## Profile and authority

The versioned `redaction-sanitization-v1` profile consumes a bounded, authenticated
PDF derivative. It creates a fresh catalog and page tree and retains only the
supported page geometry, content streams and rendering resources. Attachments,
annotations and comments, actions and scripts, form fields and values, document
metadata and source signatures are excluded. No safe form-field exception is
approved. Embedded font identity uses only the fixed approved Noto attribution
and family values described below. The rewrite does not preserve the input trailer identifier
or incremental history. The profile applies to every page of its input.

Sanitization preserves supported visible page content and generated searchable OCR.
It does not remove text or pixels painted by a page content stream. Its intended
predecessors are the [masked reconstruction](redaction-reconstruction.md) and
[regenerated OCR](redaction-ocr.md). Sanitizing an arbitrary source is not proof
that marked information has been removed. Its only successful status is
`awaiting_verification`, always describing an **unsigned derivative**.

The private `pdf.redaction_sanitization_private` capability requires its own launch
scope. The request binds the case and source identities, input byte count, SHA-256,
page count, fixed profile and version. The returned evidence binds the request,
input digest, output digest and byte count, page count, and before/after inventories.
Inventories contain numeric counts of structural feature markers, never removed
values. Counts are conservative markers, not a count of unique semantic features.
The output inventory must be empty. Unknown versions, fields or correlations fail
closed. The stage has no publication, mark-promotion or export-history authority.

## Retained data and failures

A context-specific allowlist limits retained page and resource dictionaries.
Non-rendering private fields are excluded even when nested inside resources or
stream dictionaries. Unsupported or ambiguous rendering structures fail closed.
The adapter inventories the full input graph, including orphan objects, and writes
only the supported reachable rendering graph to a new PDF. A fresh full save
discards previous revisions and unreferenced source objects.

Page and Form content streams pass a streaming native token filter. It removes
lexical comments without changing literal text, then emits one validated drawing
or text instruction at a time with closed operand types and arity. Inline images,
marked-content operators and property dictionaries, unsupported operators, dangling
operands and invalid text-object boundaries fail closed. Text objects split across
separate streams are outside this initial profile. Generated raster and OCR content
uses supported image resources and complete text objects.

Successful candidates are reopened and checked for expected pages, absence of
prohibited features and revision history, and supported rendering structure.
Independent extraction, image/object inspection, rendering and resave verification
remain PWS-0606. Removing annotations or interactive fields can remove their visible
appearance; the intended reconstructed predecessor has already flattened the
supported appearance into pixels. Arbitrary interactive forms are not a supported
editable sanitization target.

The initial profile supports generated lossless 8-bit RGB/gray images and the
generated TrueType OCR font family. Raster streams must decode to exactly the
declared samples; extra compressed or decoded bytes are rejected. JPEG payloads,
ICC profiles, unsupported predictors and unproved embedded font variants fail
closed rather than carrying opaque image/profile metadata into the derivative.
The TrueType policy uses a closed table set, canonical non-sensitive Noto
attribution/family records, normalized timestamps/vendor identity and no custom
glyph names. CID system identity is fixed to Adobe/UCS/0. Unicode CMaps are
rebuilt from supported mappings, without source comments or arbitrary identity
strings. Ordinary approved unembedded Type1 fonts remain supported. These
restrictions are a deliberate initial profile, not general PDF compatibility.

## Resources and recovery

Limits derive from the central ResourcePolicy and the existing bounded operation
limits. Object traversal, collection size, recursion depth, stream materialization,
content tokens, pending operands, instruction output, input/output bytes and
temporary storage are bounded. Processing uses one heavy
operation and one restricted worker, with no OCR child or network authority.
Each image is decoded and checked against its exact sample count, then immediately
Flate-encoded before it enters the retained PDF. The next page is copied only after
the current decoded image has been released; final saving does not retain decoded
rasters for the whole packet. Encoding has bounded output and cancellation checks.
Only reduced input/request/output/result handles cross the process boundary.
One bounded candidate may be retained at a time and must close before reuse.

Cancellation, deadline expiry, output exhaustion, low storage, malformed input,
unsupported structures, changed input and forged results return no candidate.
Cleanup removes only exclusively created output and scratch; preexisting paths are
preserved. A retry starts from the unchanged input lease. Routine errors and reports
use closed codes, internal IDs, versions, digests and numeric counts, with no paths,
filenames, form values, metadata values or document/OCR text.

## Remaining gates and product boundary

PWS-0606 owns independent multi-method verification, PWS-0607 final publication and
reports, and PWS-0608 independent release approval. Ordinary and alternate export
blockers remain active. This stage does not establish universal redaction, OCR
accuracy, digital-signature validity, forensic erasure, PDF/UA conformance or portal
acceptance.

PaperWorkShell retains its standalone workflow and private data ownership. No
sibling repository, shared service, adapter or portfolio content access is added.
Packaged-release interface facts remain draft and unchanged.
