# Case manifest JSON Schema v1

`PWS-0006` defines the first authoritative case-state contract. The schema is
[`schemas/case-manifest-v1.schema.json`](../../schemas/case-manifest-v1.schema.json). It is a
self-contained JSON Schema Draft 2020-12 document; validation never retrieves a network resource.

## Authority and publication

A manifest is authoritative only after a trusted storage service has parsed it strictly, selected
the exact supported version, completed JSON Schema and semantic validation, and atomically
published the new revision. Validation does not apply defaults or modify the supplied object.

The `sources` collection contains only imports whose case copy is complete and whose byte size and
SHA-256 digest are known. Copying, rejected, and interrupted imports belong in staging or journal
state and cannot appear as authoritative source records. Each source uses a random UUIDv4 and a
derived opaque `<source-id>.bin` storage name. The resolver supplies the `originals` directory;
the manifest does not store an external path or an arbitrary relative path.

`display_name` is sensitive presentation data held only in the protected manifest. It is never a
storage name, RPC path, command argument, cache key, or log field. Its schema accepts bounded
Unicode and inert punctuation while rejecting controls and path separators.

## Top-level contract

Every collection is present even when empty. Objects are closed with `additionalProperties: false`
so unknown behavior cannot hide in an extension bag.

| Member | Meaning |
|---|---|
| `schema_version` | Exact version dispatch value; v1 accepts only integer `1` |
| `case_id`, `revision`, timestamps | Random case identity, monotonic persistence revision, and UTC lifecycle timestamps |
| `template_ref` | Optional inert template identity, version, and digest; never a path or dereferenced URL |
| `sources` | Completed immutable case copies with content-derived media type, size, digest, page count, and conservative inspection states |
| `pages` | Stable page IDs mapped to a source ID and zero-based source page index |
| `page_sequence_base` | Replay baseline for ordered page-sequence changes |
| `page_sequence` | Current authoritative active page projection after replay |
| `page_operations` | Ordered, versioned, typed non-destructive intent |
| `assets` | Immutable case-internal signature-image assets addressed by opaque IDs |
| `overlays` | Versioned visual intent with normalized geometry and closed payload variants |
| `assignments` | Requirement-to-page suggestions or user-confirmed assignments |
| `constraints` | Closed, versioned output modes and numeric packaging limits |
| `confirmations` | Versioned user decisions with stable codes and typed subjects, not free-text authority |
| `export_history` | Case-internal export-record IDs, versions, and digests; never destination paths |

Signature and encryption inspection use explicit states including `unknown`. A detected signature
state means only that markers were detected; it is not cryptographic validation. Image records use
`not_applicable`. A modified signed source can only produce an unsigned derivative under the
separate export policy.

## Page identity and replay

`pages` is the immutable registry of imported page identities. Removing a page from the active
packet removes its ID from `page_sequence`; it does not erase the registry entry or source identity.

Operation `sequence` values are contiguous and begin at one. Replay starts with
`page_sequence_base`. Each `change_sequence` operation must declare `before_page_ids` equal to the
current replay state; its `after_page_ids` becomes the next state. All referenced IDs must exist in
`pages`, and the final replay state must equal `page_sequence`. Crop, rotation, and grouping intent
does not independently replace the page sequence. This prevents a stored snapshot and its history
from both validating while contradicting one another.

The sequence-change reason is also checked against membership: `remove` strictly removes IDs while
preserving survivor order; `import` and `restore` strictly add IDs while preserving the prior order;
and `reorder`, `split`, and `combine` retain exactly the same page-ID membership.

## Normalized geometry

`normalized_page_millionths_v1` is an integer coordinate space over the full logical page before
manifest crop and rotation operations. The origin is the top-left, positive x moves right, and
positive y moves down. One million units is the full width or height. Adapters map this logical
space to PDF points or rendered pixels without changing stored intent.

Every box has positive integer `width` and `height`; semantic validation additionally requires
`x + width <= 1_000_000` and `y + height <= 1_000_000`. Integer millionths avoid floating-point
replay drift. A future change to origins, units, or transform order requires a new coordinate-space
and manifest version rather than changing v1 meaning.

## Inert overlays and confirmations

Operation and overlay payloads are discriminated closed objects. There is no command, expression,
module, environment, URI, arbitrary path, script, JSON Patch, or embedded executable field.
Text values remain inert data even when they contain shell-looking punctuation.

A signature-image overlay carries the fixed terminology
`visual_mark_not_digital_signature`. Highlights and shapes carry
`visual_overlay_only_not_permanent_removal`; they are ordinary visual marks and cannot represent
permanent redaction.

An automated assignment remains `suggested`. A `user_confirmed` assignment must reference a
confirmed, user-authored confirmation whose typed assignment scope points back to that assignment.
Schema validity never establishes eligibility or that a requirement is substantively satisfied.

## Validation layers

[`tools/validate_manifests.py`](../../tools/validate_manifests.py) enforces invariants JSON Schema
cannot express:

- strict bounded UTF-8 JSON with duplicate-key and non-finite-number rejection;
- exact version dispatch before schema validation or mutation;
- offline Draft 2020-12 validation with format checking;
- unique and case-wide non-colliding internal IDs;
- source/page index bounds and unique `(source_id, source_page_index)` pairs;
- storage-name/ID coupling and media-appropriate inspection states;
- page, operation, overlay, asset, assignment, confirmation, and export-reference integrity;
- page-sequence operation order and replay equivalence;
- normalized-box containment and timestamp ordering; and
- safe, collision-resistant requested output names.

Diagnostics contain stable codes, repository-relative fixture labels, and JSON locations. They do
not echo display names, overlay text, invalid values, absolute paths, or raw library exceptions.

The v1 parsing envelope is capped at 8 MiB, 64 nesting levels, 100,000 JSON nodes, and 4,096
members in each manifest collection. These are defensive contract limits that prevent a small
manifest from amplifying schema/semantic work; they are not portal, case-size, or document-engine
performance claims. Later measured document-processing limits remain owned by their roadmap
tasks. Requested output names must also fit the active file-count constraint and cannot contain
Unicode formatting controls that could disguise their visible order.

Canonical examples are
[`case-manifest-v1-minimal.json`](../../schemas/examples/case-manifest-v1-minimal.json) and
[`case-manifest-v1-representative.json`](../../schemas/examples/case-manifest-v1-representative.json).
Checked-in invalid fixtures cover old/future versions, duplicate keys and IDs, unsafe storage
names, invalid hashes and page references, out-of-range page indexes, malformed boxes, and
executable-looking fields.

## Evolution and migration

Version 1 meaning and its schema file are immutable once released. Additive fields are not added
silently because closed objects would otherwise change accepted input. A new contract receives a
new schema file, version number, dispatch entry, positive/negative fixtures, and migration tests.

A migration must:

1. strictly parse and validate the old manifest;
2. transform a copy rather than the authoritative file;
3. preserve source IDs, storage names, hashes, page references and order, operation intent/order,
   overlays, assignments, constraints, confirmations, and export-history references;
4. validate the complete new object under its target schema and semantic rules; and
5. atomically publish only the verified result while retaining recoverable prior state.

Missing, zero, malformed, and unknown future versions fail before mutation. No migration may infer
or silently default user intent.
