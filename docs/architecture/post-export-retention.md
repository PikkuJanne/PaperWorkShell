# Post-export retention and accessible companions

PWS-0706 adds explicit, local choices after a completely verified package export.
The existing selectable plain-text package summary remains the record of the
checks actually performed. No output asserts portal acceptance, legal validity,
OCR accuracy, or PDF/UA conformance.

## Optional text companion

The terminal package dialog offers **Save text companion** after a separate,
initially unchecked privacy acknowledgement. The companion is UTF-8 plain text,
in output and page order, with page separators. It contains the text available
in the exact exported derivative. It does not run recognition or consult original
documents or source OCR caches. Image-only pages can have no readable text;
recognition mistakes still need human review.

Only glyphs fully inside the final visible page box are included; text retained
outside a crop is omitted, including on rotated pages. Unsupported or invalid
geometry fails closed. Ordinary Unicode and script/emoji joiners are preserved;
unsafe control and directional-override characters are removed. Extracted text
can differ from the visual page and must be reviewed separately.

A dedicated version-1 worker operation authenticates the expected file size and
SHA-256 before extraction. ZIP members are selected from the verified package
plan and authenticated independently. The GUI does not parse a PDF or extract
archive document content. Extraction uses the existing restricted worker,
application heavy-operation gate, cancellation, timeout, and central resource
policy. Text is user content: it is excluded from routine logs, diagnostics,
exception messages and object representations.

The capability is explicitly negotiated by the desktop composition. It is
excluded from default capability requests, preserving existing clients; an
unrequested companion operation cannot start. The worker receives only pinned,
read-only handles to exact current-case export resources and a bounded writable
output section. No export-directory access or broader filesystem permission is
granted. The [RPC contract](../schemas/worker-rpc-v1.md#optional-text-companion-operation)
documents the additive version-1 payload and result checks.

The companion and optional summary use the existing no-replace destination
writer. They are separate files, outside application case storage; failure or
cancellation does not undo a successful PDF/ZIP export. These files can contain
personal information and survive case removal. Users must review and protect or
delete them separately.

Starting, refreshing or cancelling a package review, or beginning another export
or compression attempt, discards the prior companion receipt. A failed cleanup
cannot authorize text from an earlier package. Saving also checks the complete
expected case snapshot (including its compare-and-swap token).

## Deletion review

**Review case deletion** is offered after complete publication. A summary status
or UI boolean cannot authorize removal. The trusted exporter retains the exact
successful outcome and a current manifest snapshot. A fresh review binds a random
review ID to the case ID, manifest revision and hash, measured storage inventory,
and the exact retained exports. Unsaved or changed case content requires another
export. Cancellation leaves the case open and unchanged.

The inventory describes imported copies, pages, overlays, signature images,
metadata/history, OCR derivatives, caches and temporary files. It lists the saved
outputs that remain and explains that external source files remain unchanged.
**Keep case** is the default. A new acknowledgement and explicit **Delete case**
action are required. Locking an assisted session dismisses this review and clears
its consent; delayed replies cannot restore it.

Before removal, the service rechecks current assisted access, the manifest,
inventory and all retained-output hashes. It pins external output files and their
ancestors against writes/deletion for the destructive operation and rejects
outputs anywhere in application case storage. Hashing is bounded, cancellable and
timed. Only after these checks does it release the package review, flush/close the
case, and invoke the existing authenticated, no-follow case remover.

Consent is transient. Restart never resumes deletion automatically; a new
successful export and review are required. Removal makes no forensic-erasure
claim and does not affect independent backups or other copies.

## Failure and recovery limits

Changed, missing, locked or unsafe exports block deletion before case closure.
Choose an ordinary destination outside application case storage and export again
if the saved files cannot be authenticated there. Existing files are never
replaced; use a new filename or destination folder for a retry.
Close failure preserves the existing case recovery behavior. Actual removal is
not transactional: once it starts, cancellation cannot restore removed bytes.
The existing remover invalidates the manifest first. If some files cannot be
removed, the UI reports partial removal and the case is unavailable for ordinary
reopening, including after restart. Remaining application files may need support
or later explicit storage cleanup; there is no automatic destructive retry.
The independently verified exported package remains available.

These are standalone PaperWorkShell operations. No sibling process, service,
adapter, database or shared schema participates. Data ownership is unchanged.
Packaged locations and uninstall behavior remain M8 release-document facts;
this task proves no new packaged-release or ThirdLife compatibility claim.
