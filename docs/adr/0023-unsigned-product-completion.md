# ADR 0023 — Unsigned PaperWorkShell product completion

- Status: Accepted under explicit owner direction
- Proposed and accepted: 2026-11-26
- Decision: PWS-D044, amending the PWS-D040 gate interpretation
- Tasks: PWS-0802, PWS-0803, PWS-0806, PWS-0807, PWS-0808

## Context

The owner established that PaperWorkShell publisher signing is unavailable before
product completion. The earlier definition required signing to finish. The
existing unsigned builder, exact-artifact qualification and per-user setup
already support useful engineering on Windows 11 x64. PWS-0814 established that
path for the exact B8 artifact, including same-build repair and reuse. PWS-0807
recorded later installed unsigned diagnostics observations on its own candidate.
Those records retain their original scope.

## Decision

PWS-0808 can accept an exact unsigned Windows 11 x64 candidate when the required
product evidence is complete. The approved document, privacy, security,
accessibility, resource, installation, migration, support and dependency criteria
still apply. Carry the source revision, version, candidate ID, inventory,
checksums, and observed signing status into the evidence record. Report the
actual outcomes of each operation and the tested Windows policy. Existing
internal candidate receipts keep their original distribution fields.

The [historical signing design in ADR 0018](0018-windows-installer-and-release-identity.md#historical-signing-design)
is superseded as a product requirement. A future signed distribution variant
requires an explicit owner decision after completion. Any publication is
separately authorized and discloses actual signing and platform compatibility.
Keep upstream authenticity and redistribution checks in place. Publisher signing
has no effect on PDF signature handling or signature-image placement.

The supported installation and maintenance path uses explicit local installation,
repair and case-preserving replacement. Verify different-build replacement
separately from same-build repair. PWS-0803 update admission still authenticates
metadata and content and refuses tampered or replayed offers. Only isolated test
installations may carry test anchors. An optional live production channel remains
disabled pending its own trust and distribution qualification. A candidate
checksum obtained against a selected reference establishes byte identity.

## Affected records and verification

PWS-D040 points to the amended gate. The roadmap and PWS-0808 define product
completion, and PWS-0802 continues to own packaging and installation. PWS-0803
tracks supported maintenance and update admission. PWS-0806 and PWS-0807 record
the actual unsigned state, applicable evidence and unresolved observations.
The task graph, validators, operating policy, prompts and release documents carry
these criteria. Accepted engineering evidence remains bound to the original
artifact, environment and result. Verify the completion dependency closure,
candidate identity and refusal checks, and changed installed behavior using the
repository's existing checks and synthetic data.

## Alternatives and effects

Keeping an internal-only exception would retain the circular completion condition
for everyone who needs an installable, verified product. A test publisher
certificate would leave the public trust question unresolved. The selected
decision uses the existing unsigned route and records real OS execution denials
against their observed environments. It introduces no app feature, network
requirement, new licence obligation, change to case ownership, or case, template
or RPC schema migration. Standalone operation and accessibility requirements
continue to apply. Hosted and third-party policy can still dictate what runs in
specific environments.
