# Safe declarative template-loading contract

- Status: Implemented PWS-0401 contract
- Task: PWS-0401
- Decisions: PWS-D025, PWS-D033, PWS-D036
- Depends on: PWS-0007 template schema v1; PWS-0308 Milestone 3 gate
- Schema: [Declarative template JSON Schema v1](../schemas/template-v1.md)

## Scope and layers

PWS-0401 turns the frozen template-v1 contract into a local runtime boundary. The
`templates` package owns path-free keys, exact-byte evidence, strict decoding, version dispatch,
schema and semantic validation, lifecycle state, immutable snapshots, and plain presentation
facts. `storage.template_repository` owns the Windows filesystem adapter. A caller selects only an
opaque `(template_id, template_version)` key; neither the loader nor template data receives a
filesystem path, directory capability, network client, worker, subprocess, or case writer.

The runtime uses only the Python standard library. The development-only `jsonschema` validator in
`tools/validate_templates.py` remains an independent repository oracle and is not imported by the
product loader. No dependency, worker capability, manifest schema, or migration was added.

## Exact-byte trust model

Trust comes from a catalog record outside the template. The record binds one opaque key to an
expected byte count, SHA-256 digest, catalog verification date, and closed external-signature
status. The repository returns those facts with one immutable byte snapshot. The loader then:

1. rejects a future catalog verification date;
2. compares the observed byte count with the catalog count;
3. hashes those exact bytes and performs a constant-time comparison with the catalog digest;
4. rejects explicitly invalid external signature evidence;
5. strictly decodes and validates the same byte object; and
6. checks that the decoded template ID and content version match the requested key.

Whitespace or any other byte change therefore requires a different externally reviewed digest,
even when decoded JSON meaning is otherwise equivalent. Embedded `publisher`, `source_reference`,
`source_url`, and `source_document_sha256` values remain labeled template claims. In particular,
`source_document_sha256` identifies a cited source document; it never authenticates the template.
An embedded template cannot promote its own checksum or signature status.

## Strict data-only validation

The loader admits at most 1 MiB of strict UTF-8 JSON. It rejects duplicate members, floats,
non-finite numbers, integers outside the interoperable safe range, non-object roots, and bounded
depth, node, string, and collection overflows. `schema_version` is dispatched before v1 meaning is
interpreted. Missing, malformed, legacy, and future versions fail closed; no default or migration
is attempted.

The packaged v1 schema is byte-identical to the canonical repository schema and is authenticated
against the reviewed semantic fingerprint before use. A bounded standard-library matcher applies
the exact keyword subset used by that schema. Runtime semantic checks then enforce identifier and
reference closure, requirement ordering and count ranges, output limits and modes, safe static
filename stems, lifecycle order, HTTPS-only inert source URLs, Unicode normalization, and the
mandatory untrusted-provenance policy.

Every object boundary is scanned for normalized executable, eligibility, and self-trust member
names. Commands, scripts, imports, expressions, paths, predicates, decisions, and trust overrides
are rejected even when nested or spelled with punctuation/case variants. Shell-, HTML-, Markdown-,
path-, and URL-looking characters inside an otherwise permitted narrative field remain ordinary
text. Validation never evaluates, imports, opens, resolves, or fetches them.

## Local repository boundary

The Windows adapter accepts one trusted absolute fixed-local root and a closed in-memory catalog.
Catalog filenames are lowercase single-component `.json` names. Traversal, separators, absolute,
UNC, device, alternate-stream, reserved-device, duplicate-key, and duplicate-filename forms are
rejected during catalog construction.

For a selected entry, storage opens every ancestor and the terminal file without following
reparse points, retains the authenticated handles, and permits only a regular single-link file on
the expected fixed local volume. Sparse, offline, recall, directory, delete-pending, reparse,
hardlink, and final-path-alias states fail closed. It reads the bounded file once through that
retained handle, then rechecks terminal identity and every pinned ancestor before returning the
snapshot. Missing/inaccessible files, replacement races, and close failures become stable,
content-free errors rather than raw Windows exceptions or paths.

## Lifecycle and user presentation

Evaluation uses an explicit trusted `as_of` date, never an implicit wall clock:

```text
as_of < catalog_verified_on                 -> reject external evidence
as_of < embedded verified_on                -> reject lifecycle
verified_on <= as_of < review_due_on        -> current
review_due_on <= as_of <= expires_on        -> review due (non-blocking warning)
as_of > expires_on                          -> reject expired template
```

A successful immutable snapshot includes a presentation model with a title, locale, non-color
freshness state, plain status message, checksum result, external-signature status, catalog
verification date, embedded verification/review/expiry dates, and clearly labeled provenance
claims. The notice states that PaperWorkShell verified the exact local bytes but does not guarantee
eligibility, legal correctness, or portal acceptance. This toolkit-free model is the user-visible
contract consumed by later M4 UI work; it provides explicit labels and text states rather than
requiring color, hover, or a digest-only signal.

Snapshot and presentation representations omit template text, URLs, paths, and full digests.
Stable `TEMPLATE_*` failures contain only a closed code, safe structural context, and actionable
plain-language message.

## Case safety and resource behavior

Loading is inspection only. The repository protocol has one `read` operation and the loader has no
case, manifest, autosave, export, filesystem-write, worker, network, subprocess, cache, queue, or
executor dependency. A successful snapshot must be passed to a separate future explicit case
action before its key and digest can become `template_ref`; every loader failure therefore leaves
the current case byte-identical. Returned document access is a fresh bounded defensive copy, so a
caller cannot mutate the validated internal snapshot.

The 1 MiB byte ceiling and versioned JSON structural limits are enforced without temporary files
or whole-repository enumeration. Loading one template holds only its bounded bytes, decoded object,
and bounded defensive copy. PWS-0401 adds no unbounded cache, concurrency, subprocess, network
operation, or persistent artifact.

## Verification and residual boundaries

Unit tests run the canonical and negative template corpora through the runtime loader, compare the
packaged and canonical schema, exercise exact lifecycle boundaries, and cover malformed JSON,
version, checksum, signature, identity, schema, semantic, Unicode, and resource failures. Security
tests place hostile strings behind evaluation, import, file, subprocess, socket, HTTP, and URL
spies and prove zero calls; they also prove sanitized representations and unchanged synthetic case
state. Windows integration covers fixed-local success, maximum size, traversal/device/ADS/reserved
names, hardlinks, reparse points where the account can create them, final-path/identity changes,
oversize files, missing files, and retained-handle cleanup.

Exact checksum matching detects a changed catalog artifact; it is not a general authenticity claim
against an attacker who can rewrite the trusted catalog and application together. External
signature verification is supplied by the trusted catalog boundary; this loader does not implement
a certificate or public-key infrastructure. Restrictive Windows handle checks do not defend
against administrators, SYSTEM, offline disk access, or unrestricted same-account malware.

The implementation is independently useful to PaperWorkShell and keeps all templates and case
bindings local to the product. It adds no ThirdLife dependency, adapter, shared schema, service,
data exchange, synchronized version, or release-interface change.
