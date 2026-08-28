# Generic portal application template 1.1.0

- Status: PWS-0402 built-in content contract
- Task: PWS-0402
- Decisions: PWS-D006, PWS-D025, PWS-D033, PWS-D037
- Schema: [Declarative template JSON Schema v1](../schemas/template-v1.md)
- Canonical artifact:
  [`templates/builtin/generic-portal-application-v1.1.0.json`](../../templates/builtin/generic-portal-application-v1.1.0.json)
- Packaged copy:
  [`paperworkshell` runtime resource](../../src/paperworkshell/templates/builtin/generic-portal-application-v1.1.0.json)

## Purpose and limits

Template `generic-portal-application` content version `1.1.0` is the first complete generic
PaperWorkShell packaging workflow. It provides an ordered application-form, identity-evidence,
address-evidence, and supporting-evidence checklist plus reviewable output-planning defaults. It is
useful without a named jurisdiction or receiving organization.

This template is not an application authority. Its categories, counts, byte limits, file limits,
page limits, output choices, and filenames are planning defaults for a generic demonstration
profile, not facts about a portal. A user must obtain and follow the current instructions from the
organization that will receive the packet.

PaperWorkShell does not use this template to decide eligibility, provide legal advice, verify an
identity, submit documents, or guarantee acceptance. Loading the template does not establish that
any document satisfies a substantive requirement.

## Schema version and content identity

The integer `schema_version: 1` identifies the declarative grammar. The string
`template_version: 1.1.0` identifies this content revision. They are independent version axes.

The accepted PWS-0007 minimal schema example already uses
`generic-portal-application` / `1.0.0`. That example remains byte-for-byte unchanged. PWS-0402
therefore publishes the complete workflow under the same stable template identity with content
version `1.1.0`; it does not reuse the `1.0.0` key for different bytes. A catalog selects the full
`(template_id, template_version)` key and binds it to the exact byte count and SHA-256. Once
published, changed `1.1.0` bytes require a new content version rather than an in-place replacement.

The canonical repository artifact and packaged copy must remain byte-identical. The packaged copy
does not create an executable resource or a second source of semantic truth.

## Plain-language workflow instructions

The template presents these instructions as inert plain text, in order:

1. Use this template to organize a generic application packet. It does not replace the current
   instructions from the organization you will send the packet to.
2. Before adding files, obtain the current application instructions and compare their document
   types, page order, filenames, number of files, and size limits with this template.
3. Assign each document to the matching category, then review the assignment and order yourself.
   PaperWorkShell does not decide whether a document satisfies a requirement.
4. Before submission, review every included page for readability and check that no page or side
   you intend to send is missing.
5. PaperWorkShell creates files on this device. It does not submit them.

These sentences describe user actions and product boundaries. They do not report a machine check
as complete.

## Ordered categories and counts

Counts use the template-v1 `documents` unit. They do not count the front and back of one physical
document as two documents merely because the sides are represented by separate pages.

| Order | Requirement ID | User-facing category | Inclusive document count | Procedural review |
|---:|---|---|---:|---|
| 1 | `application-form` | Application form | 1 | Include the current form intended for the packet and review every intended page. |
| 2 | `identity-evidence` | Identity evidence | 1–2 | Follow the current instructions and review every requested side. |
| 3 | `address-evidence` | Proof of address | 1–2 | Follow the current instructions and review intended pages for presence and readability. |
| 4 | `supporting-evidence` | Supporting evidence | 1–10 | Add the requested supporting documents and review their assignment and order. |

The identity guidance is deliberately jurisdiction-neutral:

> If an identity document has a front and back, check whether the current instructions ask for
> both sides. Include each side they ask for, and review that each side is readable.

This is not a universal rule that both sides are always required. It directs the user back to the
current application instructions and records no identity-verification result.

## Output-planning defaults

The template permits `combined_pdf`, `separate_pdfs`, and `zip`, with `combined_pdf` as the default.
It requests unencrypted output and flattened overlays. These are declarative planning choices, not
evidence that a receiving system accepts the result.

| Limit | Exact value | Human-readable value |
|---|---:|---:|
| Maximum output files | 4 | 4 files |
| Maximum aggregate size | 10,485,760 bytes | 10 MiB |
| Maximum size per file | 5,242,880 bytes | 5 MiB |
| Maximum pages per PDF | 100 | 100 pages |

The filename policy is the closed `safe_static_stem_with_counter_v1` policy. The package stem is
`generic-application-packet`; requirement-separated stems are `application-form`,
`identity-evidence`, `address-evidence`, and `supporting-evidence` in requirement order. Extensions
come from the selected output mode. A collision appends a deterministic positive counter.

PWS-0402 only declares these values. PWS-0406 owns runtime constraint evaluation, Windows-safe
filename behavior, manual overrides, collision handling, and boundary tests. PWS-0407 owns output
grouping, page selection, PDF/ZIP creation, atomic publication, and reopen verification.

## Review warnings and confirmations

The template has two informational warnings and two `needs_review` warnings. They tell the user
that the profile is generic, call out identity sides and page readability, and state the
packaging-only product boundary. A warning is not evidence that a document is acceptable or
unacceptable.

Every declared confirmation fixes `actor: user` and `effect: records_review_only`:

| Confirmation ID | Scope | Statement |
|---|---|---|
| `current-instructions-reviewed` | Package | I compared the included document types and counts with the current application instructions. |
| `identity-sides-reviewed` | Identity evidence | I compared the identity evidence with the current application instructions and reviewed whether each requested side is present and readable. |
| `pages-reviewed` | Package | I reviewed every included page for readability and checked that no page or side I intend to send is missing. |
| `package-arrangement-reviewed` | Package | I reviewed the document assignments, page order, and filenames shown for this package. |

The statements record review actions only. They do not establish eligibility, legal sufficiency,
identity, document authenticity, OCR correctness, signature validity, privacy, completeness, or
acceptance. Template loading never creates or infers a confirmation. PWS-0403 owns accessible
assignment actions, user-confirmed checklist state, manifest persistence, and suggestion behavior;
automation cannot turn these statements into completed requirements.

## Provenance, review, and expiry

The embedded provenance describes the artifact as built in and names `PaperWorkShell project` as
its publisher claim. `source_reference` is `pws.generic.portal.application`; `source_url` and
`source_document_sha256` are `null` because this generic profile does not cite one external portal
or source document. As required by schema v1, embedded provenance remains
`untrusted_embedded_claims` under `external_verification_required`.

| Lifecycle field | Date | Meaning |
|---|---|---|
| `verified_on` | 2026-08-28 | The project reviewed this generic content against its schema and frozen product boundaries. It did not verify a portal's rules. |
| `review_due_on` | 2027-02-28 | The loader reports a non-blocking review-due state from this date. Current application instructions still take precedence before this date. |
| `expires_on` | 2027-08-28 | The template is usable through this date and is rejected when the trusted evaluation date is later. |

Trust comes from the catalog evidence for the exact local bytes, not from these embedded claims.
Validation and loading use an explicit trusted `as_of` date, perform no network request, and do not
open the inert source reference. Review due does not mean approved, and an unexpired template does
not mean current for a particular application.

## Structural-only synthetic packet mapping

The canonical
[`generic-portal-packets.json`](../../tests/fixtures/templates/v1/generic-portal-packets.json)
mapping binds the exact template byte count and SHA-256, then maps opaque synthetic document IDs
and existing public-corpus fixture IDs to requirement IDs. It contains no real or simulated
identity details, address, application answers, document text, image pixels, filenames, paths, or
portal claims.

| Scenario | Application form | Identity evidence | Proof of address | Supporting evidence | Structural expectation |
|---|---:|---:|---:|---:|---|
| `complete` | 1 | 1 | 1 | 1 | Every declared category count is within its inclusive range; no structural finding is expected. |
| `missing` | 1 | 1 | 0 | 1 | `GENERIC_PACKET_REQUIREMENT_MISSING` identifies `address-evidence` below its minimum of 1. |
| `excessive` | 1 | 1 | 1 | 11 | `GENERIC_PACKET_REQUIREMENT_EXCESSIVE` identifies `supporting-evidence` above its maximum of 10. |

The identity entry references an ordinary public synthetic geometry fixture. It does not portray an
identity document or claim that the fixture has front and back identity content. Front/back review
remains jurisdiction-neutral user guidance rather than a structural fixture assertion.

“Complete” means only that these synthetic assignments are within the declared count ranges. It
does not mean the packet is complete, correct, legible, legally sufficient, eligible, verified, or
accepted. The mapping's expected records are structural test-oracle results, not product preflight
findings or claims about the mapped document content.

PWS-0403 owns the domain behavior that maps real case documents/pages to requirements and retains
explicit user confirmation. PWS-0406 owns evaluation of file/page/byte limits and safe names.
PWS-0407 owns deterministic output planning and verified derivative creation. The structural
mappings are bounded test oracles for those later tasks, not substitute implementations.

## Validation and updates

The repository validator must accept the canonical `1.1.0` artifact at an explicit reference date
on or after `verified_on` and no later than `expires_on`. Focused evidence covers schema and semantic
validation plus the complete, missing, and excessive mappings. Repository
documentation validation checks the links and task/decision references offline.

Changing narrative guidance, counts, order, output values, filenames, warnings, confirmations,
provenance, or lifecycle dates changes template content. After publication, make such a change
under a new `template_version`, update the external catalog digest, preserve older bytes, and rerun
the positive, missing, excessive, expiry, and prohibited-claim reviews.
