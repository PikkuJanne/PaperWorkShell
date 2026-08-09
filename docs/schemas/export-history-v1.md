# Export history v1

`export-history.json` is the case-internal, versioned record of destination-committed PDF exports.
It is separate from `manifest.json`: the manifest remains authoritative and stores only an internal
export ID, record version, and SHA-256 reference for each history record.

## Record boundary

Each v1 record contains exactly:

- an opaque UUIDv4 export ID and record version;
- a caller-supplied UTC recording instant;
- the exact manifest revision and canonical manifest digest used to plan the export;
- the deterministic semantic fingerprint of page selection, order, rotation, and profile;
- verified output SHA-256, byte size, and page count;
- the closed codes for checks actually completed; and
- whether the output is an unsigned derivative because signature markers were present.

There is no path, filename, display name, PDF text, OCR text, form value, metadata value, thumbnail,
or signature image field. Routine errors and representations omit record identifiers and digests.
The trusted caller supplies the UTC instant; persistence does not consult a hidden clock.

## Canonical digest and linking

Objects use UTF-8-compatible ASCII JSON with sorted keys, compact separators, and one trailing line
feed. The SHA-256 of one canonical record object is the `sha256` in the existing case-manifest v1
export-history reference. The complete `export-history.json` file has its own storage digest, but
that file digest is not placed in an individual manifest entry.

Publication order is deliberately asymmetric:

1. append and durably verify the record in `export-history.json`;
2. load the current manifest;
3. append the exact record reference and compare-and-swap the next manifest revision.

If step 3 fails, the destination output and path-free history record remain valid while the outcome
states that case-history linking is pending. Retrying the same export ID and record digest confirms
the existing record and retries only the manifest link; it never assembles or commits the PDF again.
The same export ID with any different canonical record fails closed.

## Storage and failure semantics

The Windows adapter operates under an authenticated case transaction. It writes and flushes a
bounded file in `temp/`, reopens and validates the staged bytes, then either publishes the first
history file without replacement or moves the exact authenticated prior identity to a private
retirement name before publishing the candidate without replacement through the retained case
directory. An unexpected destination identity is never overwritten. After publication, a
stable-name read handle denies delete sharing, refreshes and rechecks the final path and identity,
and verifies the exact canonical bytes before only the authenticated retired identity is removed.
An indeterminate identity is reported as commit-uncertain; exact staged and retired authorities are
preserved for PWS-0113 recovery rather than deleting through an untrusted name.

Startup validates stable history independently of the retry journal. Every manifest history
reference must resolve to the exact version and canonical record digest in stable history; valid
unlinked records may remain pending. If the stable name is absent, startup may restore only one
canonical UUIDv4 `.retired` file after authenticating its identity and proving that it contains all
manifest references. A generic `.tmp` file is never promoted because its name does not bind it to
the interrupted transaction. If stable history exists, one authenticated retirement is disposable
only when its complete record sequence is a strict prefix of stable history. Multiple, malformed,
corrupt, linked, streamed, divergent, or equal retirements remain unresolved and preserved. An
unresolved history state requires attention and stops all generic case-temp cleanup so recovery
evidence survives. A successful retirement restore marks the case `recovered`;
`repaired_export_history` continues to count only exact retry-receipt settlements.

Reparse points, sparse files, hard links, alternate data streams, noncanonical JSON, duplicate
keys, malformed records, and unsupported current or future versions fail before mutation. A failed
record write never creates a manifest reference; a failed manifest link never removes the already
committed output or its durable history record.

## Evolution

The v1 schema is closed. New record meaning requires a new schema/record version and migration
tests. Unknown future file or record versions fail before storage mutation. Migration must preserve
each canonical record digest so existing manifest references remain verifiable, or introduce an
explicit new reference version instead of silently changing meaning.
