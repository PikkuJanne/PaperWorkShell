# Local previewable support bundle

PWS-0805 implements PWS-D027, PWS-D039 and PWS-D042. In the TRACE workbench, choose
**Create support bundle…** from **Settings** or **Help**. Opening the dialog creates no file. It displays
the exact two member names, sizes, SHA-256 hashes, archive size/hash, categories
and complete safe file contents. After review, select the unchecked consent
control and **Choose location and save**. Canceling review or the location picker
writes nothing. Saving requires a new path on a supported fixed local Windows
volume; existing files are never replaced.

The archive contains only:

| Member | Included fields |
| --- | --- |
| `summary.txt` | Existing typed diagnostic summary version and UTC generation time; record/dropped/sink-failure counts; numeric application, engine and schema versions when recorded; internal operation UUIDs; closed count/failure/resource kinds and numeric measurements |
| `allowlist.txt` | Fixed field list, exclusions, local-sharing and retention explanation |

The default desktop session records numeric application and Python versions.
The dialog does not retrospectively collect worker activity or other missing
measurements. A service supplied with an existing `SanitizedDiagnosticLogger`
may summarize more already-recorded approved fields. No collector reads cases,
originals, OCR, overlays, private manifests, log files, arbitrary file paths,
environment variables, machine/user identity or installed programs. Nothing is
uploaded, emailed, submitted or automatically transmitted. This feature does not
establish any support contact or cloud reporting route.

The closed field boundary excludes document/OCR/form text, names, addresses,
original filenames, full paths, extracted metadata values, thumbnails, signature
images, document bytes and raw exceptions. It uses the established validated
`DiagnosticSummary` contract. Every preview/save revalidates it and compares
the exact fixed member contents and canonical ZIP bytes. Arbitrary text or files
cannot be added by specifying a path. A snapshot is frozen once: later logger
events do not silently change what Save publishes. A zero-record summary is
valid and does not imply other measurements were collected.

The ZIP format has two stored members with fixed timestamps and no directory
entries. Its 512 KiB format cap derives from the existing 4,096-operation-ID
summary bound; normal/constrained `ResourcePolicy` also bounds temporary output,
free-space reserve and publication deadline. There is one publication QThread,
no executor/queue/document worker, no case-storage staging and no parser. UI
review has no timeout. The existing authenticated Windows destination adapter
creates a same-folder opaque stage, flushes and reopens it, verifies exact bytes,
and atomically publishes without replacement. Failed or canceled precommit
writes clean only their stage. Success is reported only after exact final-name
reopen/hash verification; cancellation requested after successful commit cannot
undo publication. Uncertain/failed outcomes never claim success.

The destination path exists only as local save authority and never enters the
bundle or routine diagnostic messages. The support bundle remains in the user's
chosen folder, outside managed cases, until the user deletes it. Case cleanup,
case deletion and application removal preserve it. Deletion is not a promise of
forensic erasure. The user can inspect or share this local file separately.

Review and save are keyboard accessible, expose names/roles/states, preserve
focus and announce saving/cancellation/results. A scrollable reflowing surface
supports enlarged text. Closing during publication requests cancellation and
retains ownership until safe cleanup completes; desktop shutdown waits as well.
Synthetic widget and accessibility tests prove these mechanics. Exact packaged
behavior and new human NVDA observations remain with the release gate; earlier
accepted human evidence is neither repeated nor relabelled.

The [public synthetic sample](../../tests/fixtures/diagnostics/v1/README.md)
includes the actual ZIP, standalone allowlist, expected behavior, fixed no-input
recipe and exact archive/member hashes. The compatibility and privacy tests
reopen the sample and inspect every member without extracting arbitrary paths.
It proves this support format, not a real incident, hardware qualification or
packaged-release test.

PaperWorkShell owns this evidence and remains useful without any sibling.
No ThirdLife support metadata, content access, adapter, service or shared schema
is created. Candidate release-interface and uninstall location/residue statements
must be reconciled by their release owners against the exact next package; this
source feature does not change historical candidate facts.
