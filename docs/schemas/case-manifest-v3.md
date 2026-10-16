# Case manifest JSON Schema v3

`PWS-0602` introduces the current case-state contract at
[`schemas/case-manifest-v3.schema.json`](../../schemas/case-manifest-v3.schema.json).
Frozen v1 and v2 schema bytes and meanings remain independently valid migration inputs.
The packaged v3 schema is byte-identical to the repository contract; runtime and repository
validators authenticate its reviewed semantic fingerprint and never retrieve schemas online.

Version 3 adds one required top-level `redaction_marks` collection. It retains every v2
source, page, transform, overlay, assignment, constraint, confirmation, and export-history
meaning. Each mark has exactly these members:

| Member | Meaning |
|---|---|
| `mark_id` | Canonical UUIDv4, unique across all case namespaces |
| `mark_version` | Integer `1` |
| `page_id` | An existing page identity, including an inactive page retained for undo |
| `box` | Positive full-page `normalized_page_millionths_v1` region |
| `state` | Exact string `unapplied` |

The collection contains at most 4096 marks. Boxes use the existing integer coordinate space
before manifest crop, clockwise rotation, and deskew; they must fit inside the full logical
page. Crop and rotation do not rewrite saved intent. Array order is stable and has no overlay
z-order meaning. Removed pages retain their marks so restoring a page also restores its intent.
Duplicate mark IDs, collisions with any source/page/operation/group/asset/overlay/assignment/
confirmation/export ID, unknown page references, and empty or out-of-page boxes fail validation.

Marks contain no copied text, reason, filename, path, executable payload, derivative identity,
or asserted verification evidence. Ordinary shapes remain visual overlays and cannot be decoded
as removal marks. No migration infers marks from black rectangles, highlights, or annotations.

## Review states and the security gate

`domain.redaction.RedactionMarkState` names `unapplied`, `applied`, `verified`, and `failed`
for clear review language. Only `unapplied` is a case-manifest state in this version. The other
labels explain future ephemeral pipeline results; they are not user-authored status flags.
`transition_redaction_state` rejects every transition involving a pipeline state while the
permanent-removal gate is closed. No shape, serialized boolean, or manually edited status can
produce a verified mark.

The current feature records review intent only. No information is removed. Export is blocked
when an active page has a mark, including alternative and package export routes, until the
future gated pipeline can create and verify a derivative. Marks on inactive pages do not block
an otherwise ordinary export. Future applied/verified results require their own evidence-bound
contract; this schema does not anticipate that contract by accepting unverified authored claims.

## Migration and persistence

Migration validates the complete frozen source, works on a defensive copy, adds an empty
collection, and validates the current result. The pure v1-to-v2 and v2-to-v3 functions each
increment their candidate revision. Repository load publishes the complete historical-to-current
upgrade as one journaled transaction with a single revision increment. Existing hashes, page
references, operation ordering, populated or empty overlays/assets, assignments, confirmations,
constraints, and export-history references survive unchanged. Unknown future versions fail before
mutation. Interrupted publication retains the existing journal recovery path.

This is private PaperWorkShell case state. It creates no sibling access, shared schema, portfolio
adapter, or release-interface capability. The product remains useful without ThirdLife.
