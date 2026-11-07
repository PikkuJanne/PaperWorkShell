# Case manifest JSON Schema v4

`PWS-0811` introduces the private case display name in
[`schemas/case-manifest-v4.schema.json`](../../schemas/case-manifest-v4.schema.json).
The packaged schema is byte-identical and both validators authenticate its reviewed
fingerprint offline. Frozen v1, v2 and v3 schema bytes and meanings remain unchanged.

Version 4 adds the optional top-level `case_name` string, with at most 200 characters.
An absent or empty name identifies an existing unnamed case. New named cases include
the normalized name in their initial manifest transaction. The UI requests a nonblank
name and accepts ordinary Unicode text without judging its meaning. Surrounding
whitespace is removed on entry; persisted values with surrounding whitespace, control,
format or surrogate characters fail validation. Names are display text, never paths.

Names remain inside the existing private case ACL and are omitted from model
representations and routine diagnostics. `CaseSummary.case_name` and
`OrganizerViewState.case_name` are explicitly private presentation fields.
Renaming commits through the existing atomic autosave path after draining prior edits.
Case UUIDs, source files, page IDs, history and cache identities do not change.
If saving fails after the name was queued, the desktop receives the unsaved name
and pending state before the error. Later page, mark or checklist edits preserve
that pending name until a successful retry; they cannot silently replace it with
the previous durable name or report the failed rename as saved.

The v3-to-v4 migration operates on a copy, adds an empty name and retains every other
value except schema dispatch and the single next persistence revision. The complete
v1/v2/v3 route commits in one authenticated journal transaction. Before publication,
the exact old record is retained create-only as `journal/migration-source-vN.json`.
R9/v3 records retain their byte-identical backup at `journal/migration-source-v3.json`.
Missing, changed, unsafe or stale evidence is never treated as a completed migration.
Unknown future versions fail before schema interpretation or mutation. Existing
recovery, low-disk, quota, cancellation and temporary-cleanup safeguards still apply.

Version 4 retains all v3 removal-intent semantics and security gates. It creates no
public workspace-transfer interface, sibling access or shared schema. PaperWorkShell
continues to own its private data and migrations independently.
