# Task result — PWS-0506: conservative portal profile and compatibility matrix

**Status:** implementation and verification in progress.

## Scope and plan

Selected PWS-0506 at `409c0ef` after confirming PWS-0501, PWS-0504 and PWS-0505
are done. Work is on `codex/pws-0506-portal-compatibility`. Unrelated untracked
maintainer files remain untouched.

Add a versioned portal profile to package export, enforce its policy in restricted
workers, preserve existing export-profile meanings, and prepare an exact-byte
synthetic reader and local upload-constraint matrix. Required manual reader evidence
includes Adobe Reader/free mode, Edge, Chrome, Firefox and a representative Android
reader. Older accepted PWS-0307 evidence stays bound to its original artifact.

## Implemented

Work is being verified against [ADR 0014](../adr/0014-conservative-portal-profile.md)
and the [feature policy](../architecture/portal-export-profile.md).

## Changed files

The final inventory will be recorded after implementation and verification stabilize.

## Verification plan

- Domain and worker transport: legacy/current/future profile versions, invalid
  combinations, report binding and history compatibility.
- Native PDF policy: success, forbidden nested features, malformed/unknown states,
  version/extension rejection, cancellation, bounded traversal and cleanup.
- Package and compression: profile survives every pipeline and final rewrite;
  actual target-size outcomes and existing preview/acknowledgement remain valid.
- Constrained real worker: successful policy proof and prohibited-resource failure
  preserve original hashes and leave no private candidate or case mutation.
- Synthetic outputs: reopen/qpdf/render, independent Poppler visual inspection,
  actual-byte boundary checks for combined, separate and ZIP organization.
- Required repository format, lint, type, unit, integration, security,
  accessibility, schema, task, documentation, portfolio and fixture checks.
- New exact-hash manual reader checks; remote uploads only where a synthetic test
  is explicitly permitted.

## Security/privacy/accessibility notes

Untrusted PDF parsing remains worker-owned; new policy checks use bounded traversal
and existing ResourcePolicy. Routine diagnostics contain no document values. No
dependency or licence change is planned. There is no new product UI control; the
guided developer procedure is plain text and has no review timeout.

## Portfolio boundary and release-interface notes

Standalone export usefulness and PaperWorkShell ownership of cases remain intact.
No sibling repository, service, shared schema or adapter is introduced. Release
documents retain their unproved `TBD` facts and make no new compatibility claim.

## Residual risks or deferrals

Manual reader observations and full verification are pending. An unavailable reader
cannot be relabeled as passed. Source-font substitution and recipient-specific rules
remain explicit limitations.

## Task graph

PWS-0506 is the selected task. PWS-0507 remains dependent on its completion.
