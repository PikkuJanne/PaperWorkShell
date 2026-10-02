# ADR 0014: Verify a conservative portal PDF profile

- Status: Implemented and automatically verified for PWS-0506; reader acceptance pending
- Date: 2026-10-01
- Decisions preserved: PWS-D008, PWS-D019, PWS-D024, PWS-D029, PWS-D037, PWS-D041, PWS-D042
- Builds on: [target-size search](0012-target-size-search.md) and
  [compression review](0013-compression-review-and-floor.md)

## Problem

Page copying can retain annotations, actions, attachments and metadata through page
resources even when the new document omits the original catalogue. Inspection warnings
alone do not establish that the derivative meets a conservative feature policy.
Existing export profiles also have accepted behavior that preserves source annotations.

## Decision

Introduce an explicit version 3 base export profile for package export. Keep the meanings
of versions 1 and 2. Bind the selected profile into plans, fingerprints and worker
validation. Verify the final candidate in the restricted document worker before it can
be published, including after safe optimization and image re-encoding.

The policy is defined in the [portal profile](../architecture/portal-export-profile.md).
Unsupported retained features block this export; a new header does not convert an
unsupported PDF. Do not introduce a general source-form or annotation flattener.
PaperWorkShell visual overlays continue through the existing verified flattening path.

PDF object streams require PDF 1.5 or later. Portal assembly writes at least PDF 1.7
so admitted source features such as page scaling retain an adequate version after
copying into a new document. Final verification accepts PDF 1.5 through 1.7 without
extension dictionaries.
The writer must not force a lower version to make an unsupported file appear compatible.
This follows the documented distinction between minimum and forced versions in
[qpdf](https://qpdf.readthedocs.io/en/stable/cli.html) and
[pikepdf](https://pikepdf.readthedocs.io/en/stable/api/main.html).

## Verification and evidence

New profile reports identify the performed policy check. Legacy reports and persisted
history retain their established meanings; a historical record without the new check
does not prove the portal policy. Domain and transport tests cover legacy, current,
future and malformed combinations. Native tests cover prohibited features, unknown
structures, version restrictions, resource exhaustion, cancellation and cleanup.

The new synthetic corpus has its own exact-byte reader matrix. Previously accepted
PWS-0307 artifacts and results remain unchanged. Automated parse/render results and
local upload-constraint tests are separate from observations in Adobe Reader, Edge,
Chrome, Firefox and an explicitly identified Android reader. No slot passes merely
because a related engine or older artifact passed.

## Effects and limitations

No dependency, licence, case migration, extra service or sibling interface is introduced.
PaperWorkShell owns its case data and remains usable on its own. ResourcePolicy limits
remain controlling. The output policy does not claim content sanitization, permanent
redaction, font fidelity for every source, PDF/A, PDF/UA, legal validity or portal
acceptance. Packaged-release and portfolio-compatibility facts remain unproved until
their own gates provide evidence.
