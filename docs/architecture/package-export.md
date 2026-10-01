# Verified combined, multi-PDF, and ZIP package export

- Status: Binding PWS-0407 implementation contract
- Task: PWS-0407
- Decisions: PWS-D008, PWS-D029, PWS-D037, PWS-D041, PWS-D042
- Builds on: [deterministic verified PDF export](pdf-export.md),
  [verified searchable mixed-document output](searchable-mixed-output.md),
  [verified flattened visual-overlay export](flattened-overlay-export.md), and
  [portal constraints and safe output names](portal-constraints.md)

## Scope and limited claims

PWS-0407 turns one exact saved manifest revision, its exact validated checklist/template binding,
the saved case constraints, and one selected allowed output mode into either one combined PDF,
several named PDFs, or one ZIP containing named PDFs. It does not submit a package, decide that a
portal will accept it, generate the human-readable package summary owned by PWS-0408, or claim
legal sufficiency.

Package success means only that every planned PDF completed the existing closed PDF checks, every
active package constraint passed against actual bytes, and every top-level destination artifact was
committed without replacement and reopened with the expected identity, size, and SHA-256. A ZIP
success additionally means that its exact layout, metadata, member order, and member bytes were
reopened and verified. The report does not claim PDF/A, PDF/UA, OCR certainty, digital-signature
validity, permanent redaction, or universal viewer compatibility.

## Exact planning authority and order

`preflight.package_planner.build_package_export_plan` accepts a manifest-v2 `ManifestSnapshot`, a
`ChecklistDefinition`, an exact-bound `PortalConstraintProfile`, a selected `PortalOutputMode`, and
the actual destination-parent UTF-16 length budget. The immutable plan binds the case revision,
manifest digest and CAS token, exact template identity and digest, active saved constraints,
resolved names, and a versioned semantic fingerprint.

Planning fails before document work unless all of these conditions hold:

- the manifest, checklist definition, and constraint profile use the same exact template binding;
- the selected mode is allowed by the saved case constraints;
- every assignment is active and explicitly user-confirmed;
- required minimums and maximums are satisfied;
- every active page is present exactly once across confirmed assignments; and
- the assignment inventory contains no inactive, unknown, duplicate, or unassigned page.

The deterministic semantic page order is template requirement order, then manifest assignment
order within that requirement, then the ordered pages in each assignment. The manifest
`page_sequence` is the exact active-membership authority; it is not silently substituted for the
reviewed checklist grouping order.

The output organizations are fixed:

- **Combined PDF:** one PDF containing all non-empty requirement groups in semantic order.
- **Separate PDFs:** one PDF per non-empty requirement in template order. An empty optional
  requirement is omitted without creating a position gap.
- **ZIP:** the same requirement PDFs and order as separate mode, stored as members of one outer
  archive.

Requested names fail closed. Combined mode accepts no requested name or exactly one `.pdf` name;
separate mode accepts no requested names or exactly one `.pdf` name for every emitted PDF; ZIP mode
accepts no requested name or exactly one outer `.zip` name. ZIP member names always come from the
validated requirement-stem policy. Default names and collision counters are deterministic.

## One-output PDF production

`app.package_pdf_export.PackagePdfCandidateProducer` prepares one planned PDF at a time and retains
its authenticated sources and worker candidate until the destination stage has consumed the exact
verified bytes. It reuses existing worker capabilities rather than introducing a second parser or
PDF engine:

- the flattened pipeline is selected for compatible PDF pages, active crops, text/mark overlays,
  and authenticated visible signature-image rasters;
- the searchable pipeline is selected when JPEG/PNG, scan transforms, or OCR handling is required;
  and
- a group that simultaneously requires searchable image/scan processing and active overlay
  flattening fails with `PACKAGE_PDF_UNSUPPORTED_OVERLAY_TRANSFORM`.

That last case is intentionally unsupported in this version. The producer never drops overlay or
transform intent to force a result through one pipeline. Supporting the combination requires a
future explicitly designed two-stage verified pipeline with bounded intermediate bytes and visual
equivalence evidence.

The producer confirms the signed-source modification policy before source decoding, rasterizes
only signature assets visible on the selected page subset, uses the central `ResourcePolicy`, and
permits only one active heavy candidate. Its per-output report binds output key, package position,
exact ordered page IDs, selected pipeline, the existing pipeline-specific report, and the existing
base `PdfExportVerificationReport`.

Every base report proves the candidate reopened, passed the strict structural check, has the exact
page count, is unencrypted, rendered every page, and matches planned order and rotation. Flattened
or searchable reports retain their additional pipeline-specific evidence. A source race, malformed
report, wrong page subset, cancellation, timeout, or uncertain cleanup produces no trusted
candidate.

## Safe ZIP profile

`export.safe_zip` accepts only already verified, seekable PDF streams with exact sizes and SHA-256
digests. It writes a narrow deterministic profile:

- PDF leaf names only, normalized to NFC and checked for traversal, separators, device names,
  alternate streams, control/bidi characters, unsafe punctuation, length, and case-fold collision;
- caller/planner member order preserved exactly;
- `ZIP_STORED`, no ZIP64, no directory entries, no encryption, no extras, no comments, fixed 1980
  timestamps, and fixed DOS attributes; and
- bounded streaming requests derived from the central resource policy, with cancellation and
  deadline probes throughout write and verification.

Before returning `SafeZipReport`, the writer verifies raw local and central-directory layout,
reopens through the standard ZIP reader, checks exact metadata and order, streams every member back
to its expected CRC, size, and SHA-256, and confirms that the archive hash remains stable. A failed
archive stage is untrusted and deleted by the destination transaction.

## Actual constraints and aggregate report

The planner's size facts are intentionally unknown before worker generation. After all PDFs are
verified and staged, PWS-0407 rebuilds the canonical output-plan facts with actual byte sizes. ZIP
mode first creates and verifies the outer archive, then supplies its actual byte size separately
from the member observations. The evaluator checks the exact saved constraints for:

- allowed mode and organization;
- safe extensions and collision-free names;
- top-level file count;
- total bytes and per-file bytes;
- pages per PDF; and
- required-unencrypted output.

ZIP member component length is independent of the external destination-parent length; only the
outer archive uses that complete external path budget. PDF member limits still apply to every
member, while ZIP total/top-level limits use the outer archive as defined by the portal-constraint
contract.

`PackageAggregateVerificationReport` contains the ordered per-output reports, the optional exact
ZIP report, the typed portal findings, and a pass/fail entry for every active constraint plus fixed
naming/organization invariants. Any finding returns a `constraints_blocked` outcome and deletes all
private stages before publication.

## Destination staging and atomicity

All PDFs are generated and verified before the first public rename. The Windows destination
transaction copies each PDF to a random destination-local stage, authenticates the retained file
handle, and rechecks exact size and digest. ZIP bytes are generated directly into another retained
destination-local stage, avoiding an unaccounted archive-sized intermediate copy. The sum of
retained member and archive stages is bounded by `ResourcePolicy.maximum_temporary_storage_bytes`.

PWS-0504 adds a reservation port: retained destination-stage bytes reduce the next PDF worker's
actual temporary-storage allowance. Source, raster, status/evidence and output-copy overlap
remain subject to admission before processing. A caller may also supply a review callback that
validates additional policy evidence while all outputs remain private, before any public rename.
The explicit [target-size search](target-size-export.md) uses these ports for its fixed sequence
of measured package attempts; ordinary desktop export keeps its existing lossless image policy.

Before publication, every proposed top-level name is checked for an existing destination. If any
exists, no new artifact is committed and the existing file is not replaced. Publication uses
no-replace renames and final-name identity/digest reopen checks.

Windows does not provide one atomic rename for several unrelated files. Therefore:

- combined PDF and ZIP each have one atomically committed top-level artifact;
- separate PDFs are atomic **per artifact**, in deterministic plan order;
- publication stops at the first non-commit or unknown result; and
- the returned destination result inventories every artifact as committed, not committed,
  destination exists, or unknown.

A partial or unknown inventory is never reported as package success. Cancellation and deadlines
are honored until the public-commit boundary. Once the first irreversible rename begins, the
transaction completes state resolution without relabelling a committed file as cancelled.

## History, recovery, and future decision

PWS-0407 does not reinterpret `export-history-v1`, whose released meaning records one PDF export.
Combined, separate, and ZIP package reports remain transient, and destination no-replace behavior
protects prior verified outputs. A future decision is required before durable package history or
restart-assisted multi-artifact recovery is promised. Extending history would require a new
versioned schema and migration plus exact package/member identity, partial-set, and ambiguous-commit
semantics; silently storing a ZIP or multi-file set as one v1 PDF record would corrupt that
contract.

## Privacy, accessibility, and standalone boundary

Routine representations and errors omit filenames, paths, case/page/assignment IDs, digests,
document content, OCR text, and raw exceptions. Progress is typed, numeric, cancellable, and
suitable for accessible announcements; PWS-0408 owns the final UI and human-readable package
summary.

The implementation is entirely local and offline. It creates ordinary user-visible PDF/ZIP files
and adds no portal account, network dependency, shared SDK, CLI, URI, RPC operation, file
association, sibling repository, or ThirdLife adapter. PaperWorkShell owns its case state and
outputs. The release interface records source-level capability while packaged-build status remains
`TBD` until release evidence exists.
