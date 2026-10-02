# Conservative portal export profile

- Task: PWS-0506
- Decision: [ADR 0014](../adr/0014-conservative-portal-profile.md)
- Evidence: [reader and upload matrix](../compatibility/pws-0506-portal-matrix.md)
- Status: Implemented; final verification and reader acceptance pending

Package export uses the versioned conservative portal profile. Ordinary legacy export
profiles keep their existing meanings. A recipient can impose additional requirements;
the profile does not guarantee that a portal will accept a file.

## Feature policy

| Feature | Portal policy |
|---|---|
| Encryption | Sources and final PDFs must be unencrypted. Password removal is outside this workflow. |
| Forms, XFA and scripts | Block unsupported retained form fields, widgets, actions and JavaScript. Do not run scripts or infer a form's intended calculated appearance. |
| Annotations and media | Block retained annotations and interactive features. Source comments, link annotations and their appearance streams are not silently discarded. |
| Attachments | Ordinary assembly omits catalogue attachments. Block any embedded-file reference retained through the copied output graph. |
| Metadata | Assembly omits catalogue metadata and removes established page metadata fields. Block prohibited metadata that survives through nested resources. This does not remove text or personal information from page content. |
| Source fonts | Preserve source font resources and verify rendering. Unembedded source fonts can be substituted by readers and need visual review. No general font conversion is claimed. |
| PaperWorkShell text | Use the existing embedded overlay font strategy and its verification. Unsupported text fails through that renderer's existing checks. |
| Visual overlays | Flatten PaperWorkShell marks into page content using the existing checked geometry. A placed signature image is a visual mark; a modified signed source is an unsigned derivative. |
| Compression | Safe object/stream optimization first. Image changes use the existing fixed stages, actual size checks, preview and explicit review; grayscale requires its own choice. No below-floor override. |
| PDF compatibility | Assembly writes PDF 1.7; final verification permits 1.5 through 1.7 without unsupported extensions. Source requirements are checked before copying. No forced version downgrade. |
| Verification | Strict reopen, structure, page count, encryption, feature policy, rendering and exact-byte destination checks. Verification failure prevents publication. |

Combined PDF, separate PDFs and ZIP members use the same PDF policy. ZIP adds the
existing safe naming, member order, size, content and reopen checks. File-count and size
limits use the actual exported files, including ZIP overhead where applicable.

## Failure and recovery

If the source or derivative is outside the supported policy, keep the original and
report a stable failure. The user can obtain a supported static source or follow the
recipient's instructions using the original in another application. Do not imply that
unsupported content was safely flattened or that a changed signature remains valid.

Private candidates stay under bounded worker and staging lifetimes. Cancellation,
timeout, insufficient space and policy failure must remove incomplete candidates and
leave sources and existing destination files unchanged. No document values enter
routine logs. All native PDF objects remain behind worker adapters.

## Compatibility scope

The matrix records viewer version, platform, exact PDF hash, observed checks and defects.
The new corpus uses large synthetic labels and clear alignment targets. Accepted older
reader evidence is retained as historical evidence for its original bytes.

Local representative upload tests exercise the production constraint evaluator with
synthetic outputs at and above limits. They do not contact a portal. A remote observation
may be added only for a test environment that permits synthetic uploads; it is evidence
about that destination and those files on the recorded date.

This feature is independently useful in PaperWorkShell. It introduces no portfolio
adapter or shared data access and changes no packaged-release compatibility claim.
