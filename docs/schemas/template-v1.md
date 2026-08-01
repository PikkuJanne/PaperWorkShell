# Declarative template JSON Schema v1

`PWS-0007` defines the first PaperWorkShell template contract. The schema is
[`schemas/template-v1.schema.json`](../../schemas/template-v1.schema.json), and the canonical
synthetic example is
[`schemas/examples/template-v1-minimal.json`](../../schemas/examples/template-v1-minimal.json).
The contract is a self-contained JSON Schema Draft 2020-12 resource and is always evaluated
offline.

## Authority and trust

A template is data, not authority to execute work or decide whether a person qualifies for a
service. Schema validity establishes only that the supplied object has the bounded v1 structure.
It does not authenticate the publisher, prove that guidance is current, or guarantee that a portal
will accept an output.

Every embedded provenance object therefore carries both
`assertion_status: untrusted_embedded_claims` and
`trust_policy: external_verification_required`. `source_kind`, `publisher`, `source_reference`,
`source_url`, and `source_document_sha256` are inspectable claims made by the template. They cannot
declare the template trusted. The trusted loader introduced by `PWS-0401` must derive trust from the
exact loaded-byte snapshot, its store context, and external checksum or signature evidence. A case
manifest binds a selected template by ID, content version, and SHA-256 rather than by a path or URL.

`source_url` is either `null` or a bounded HTTPS URI. It remains inert presentation data: template
validation and loading never fetch it, open it, use it as a filesystem location, or pass it to a
worker or external command. A source-document digest describes the cited source document; it is not
a self-authenticating digest of the template.

## Top-level contract

Every top-level collection is present. All objects are closed with
`additionalProperties: false`, so an unknown command, condition, trust claim, or behavior cannot
hide in an extension bag.

| Member | v1 meaning |
|---|---|
| `schema_version` | Exact structural dispatch value; v1 accepts only integer `1` |
| `template_id` | Stable lowercase template identity token |
| `template_version` | Author-controlled content revision in strict three-part numeric form |
| `scope` | Exact value `document_packaging_only` |
| `title`, `locale` | Bounded plain title and canonical limited BCP-47 locale token |
| `provenance` | Untrusted, inspectable source claims plus the mandatory external-trust policy |
| `lifecycle` | Verification, review-due, and expiry calendar dates |
| `instructions` | Ordered, identified, inert plain-text packaging instructions |
| `requirements` | Unconditional document/page count requirements |
| `requirement_order` | Exact deterministic ordering of all requirement IDs |
| `output` | Closed output modes, limits, encryption/flattening choices, and filename policy |
| `warnings` | Informational or “Needs review” messages with typed package/requirement scope |
| `confirmations` | User-only review acknowledgements that cannot complete a requirement |

`schema_version` and `template_version` have different purposes. A new schema version changes the
contract grammar. A new template version changes the content for one `template_id`. Once published,
the same `(template_id, template_version)` must continue to identify the same externally digested
bytes.

## Instructions, requirements, and ordering

Instruction, title, guidance, warning, and confirmation strings are inert plain text. A consumer
must render them as plain text and must not interpret HTML, Markdown actions, URI schemes, shell
syntax, placeholders, or expression-like punctuation. Injection-looking characters in an allowed
text field remain text. In contrast, a member named `script`, `command`, `expression`, `import`,
`path`, `condition`, or similar behavior is unknown to the closed schema and fails validation.

Each requirement contains only:

- a stable `requirement_id`;
- a plain title and procedural guidance; and
- a fixed count with unit `documents` or `pages`, inclusive `minimum`, and inclusive `maximum`.

Requirements are unconditional. There is no rule language, predicate, formula, user attribute,
score, question, eligibility test, legal conclusion, portal call, or automatic completion state.
Semantic validation requires `minimum <= maximum`.

`requirement_order` must contain every requirement ID exactly once and no other ID. Array order is
the complete ordering rule. Warning and confirmation requirement scopes, and per-requirement
filename mappings, must reference existing requirement IDs. IDs are unique in their collections;
the repository validator also prevents ambiguous identifier reuse across template symbol
collections.

## Output and filename policy

The v1 output policy permits the closed modes `combined_pdf`, `separate_pdfs`, and `zip` and names
one allowed mode as the default. It also records whether output must be unencrypted, whether
overlays must be flattened, and positive limits for file count, aggregate bytes, per-file bytes,
and pages per PDF. These are packaging constraints, not promises about portal acceptance. Semantic
validation requires the default mode to be allowed and the per-file byte limit not to exceed the
aggregate byte limit. When `separate_pdfs` is allowed, `maximum_file_count` must be large enough to
represent one output for every declared requirement; runtime assignment and byte-size feasibility
remain owned by `PWS-0406` and `PWS-0407`.

Filename generation deliberately has no format string, regular expression, variable expansion, or
expression language. The only v1 pattern is `safe_static_stem_with_counter_v1`:

1. use the bounded lowercase ASCII `package_stem` for a combined PDF or ZIP;
2. use the requirement's explicitly mapped stem for requirement-separated PDFs;
3. derive `.pdf` or `.zip` from the selected output mode; and
4. on collision, append a deterministic positive counter before the extension.

Stems are single leaf components. Separators, drive/UNC/device syntax, alternate data streams,
trailing dots/spaces, extensions supplied by a template, and Windows reserved device names are not
accepted. When separate PDFs are permitted, semantic validation requires exactly one stem mapping
for every requirement. Filename mappings and their requirement references must be unique. The
filesystem and ZIP containment checks owned by `PWS-0406` and `PWS-0407` remain mandatory even
after template validation.

## Warnings and confirmations

A warning has a stable ID, plain message, typed package/requirement scope, and severity
`information` or `needs_review`. It cannot report a machine check as complete.

A confirmation has fixed `actor: user` and `effect: records_review_only`. It records that the user
reviewed the stated package or requirement; it does not establish eligibility, legal sufficiency,
OCR correctness, signature validity, privacy, or portal acceptance. Automation can suggest an
assignment later, but only case state—not template data—records a user's confirmation.

## Lifecycle and expiry

All lifecycle values are exact `YYYY-MM-DD` calendar dates. Semantic validation requires:

```text
verified_on <= review_due_on <= expires_on
```

Date ordering is deterministic and independent of a machine clock. Operational expiry is evaluated
against an explicit trusted `as_of` date supplied by the loader. The template is valid through its
`expires_on` date and expired when `as_of` is later. Repository fixtures use a fixed reference date
so tests do not change with wall-clock time. `PWS-0401` owns user-visible review-due and expiry
behavior and must recheck the loaded snapshot before it can affect a case.

## Validation and security envelope

[`tools/validate_templates.py`](../../tools/validate_templates.py) adds deterministic checks that
JSON Schema cannot express:

- bounded, strict UTF-8 JSON decoding with duplicate-key, non-finite-number, and non-integer-number
  rejection;
- exact version dispatch before schema evaluation or case mutation;
- a self-contained, non-recursive local-reference policy and canonical-schema fingerprint;
- Unicode normalization and unsafe control/format-code rejection;
- identifier uniqueness, reference closure, requirement ordering, count ranges, and date ordering;
- output-mode, byte-limit, and filename-mapping consistency;
- HTTPS provenance syntax without dereference; and
- explicit-date expiry evaluation.

Diagnostics use stable `TEMPLATE_*` codes, repository-relative fixture labels, and structural JSON
locations. They never echo template titles, guidance, provenance values, hostile strings, absolute
paths, raw validator errors, or third-party exceptions. Validation neither applies defaults nor
modifies the supplied object. Failure leaves case state unchanged.

The canonical format is JSON. YAML tags, constructors, anchors, aliases, merge keys, implicit
non-JSON types, and multi-document behavior are outside v1. Any later YAML input boundary must
first produce the same strict JSON data model without those features and then run this complete
validator.

## Evolution

Version 1 meaning and its schema file are immutable once released. Because objects are closed, even
an additive member requires a new schema version, dispatch entry, fixtures, documentation, and
compatibility tests. Missing, malformed, old, and unknown future versions fail before dereference,
mutation, or interpretation. Implementations do not silently default missing intent.

A supported migration must validate the old object, transform a copy, preserve template identity
and content revision semantics, requirements and their order/counts, output constraints and names,
warnings, confirmations, provenance, and lifecycle intent, then validate the complete target object
before publication. Migration never silently changes the exact template digest already recorded in
a case manifest.
