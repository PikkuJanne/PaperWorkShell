# Threat-to-decision and task traceability

- Status: Binding foundation mapping
- Task: PWS-0004
- Threat definitions: [foundation threat model](threat-model.md#threat-catalogue)
- Evidence plan: [initial security test plan](security-test-plan.md)

Every foundation threat has at least one frozen decision and one implementation task or explicit
future gate. A reference means the linked task owns implementation/evidence; it does not mean the
control exists before that task is done. High-impact threats may not be waived by prose or by a
user warning.

Every listed control is **planned** until its owning implementation task records passing evidence.
A milestone gate reviews accumulated evidence; the gate itself is never a mitigation.

| Threat | Frozen decisions | Implementation tasks and gates | Planned evidence focus |
|---|---|---|---|
| TM-001 | PWS-D012, PWS-D013 | PWS-0103, PWS-0105, PWS-0106, PWS-0107, PWS-0114 | Hostile parser corpus, exact prepared-source authority, one-shot worker crash/termination, containment |
| TM-002 | PWS-D013 | PWS-0103, PWS-0105, PWS-0106, PWS-0107, PWS-0201, PWS-0208, PWS-0209 | Byte/page/object/graph/pixel/decompression/time/memory/temp/result/tool-output limits |
| TM-003 | PWS-D012 | PWS-0002, PWS-0008, PWS-0107, PWS-0114 | Import-boundary and separate-process integration tests |
| TM-004 | PWS-D005, PWS-D013, PWS-D027 | PWS-0107, PWS-0204, PWS-0114, PWS-0209, PWS-0803 | Worker/OCR egress denial and updater privacy capture |
| TM-005 | PWS-D009, PWS-D029 | PWS-0104, PWS-0112, PWS-0114 | External/case source hashes and no-overwrite export tests |
| TM-006 | PWS-D028, PWS-D029 | PWS-0101, PWS-0102, PWS-0104, PWS-0407 | Traversal/device/reparse/permission and cleanup containment |
| TM-007 | PWS-D010, PWS-D036 | PWS-0006, PWS-0109, PWS-0804 | Schema versions, atomic failure, journal recovery, migrations |
| TM-008 | PWS-D012, PWS-D013, PWS-D036, PWS-D038 | PWS-0008, PWS-0107 | Malformed/oversized/versioned RPC, replay/state, termination |
| TM-009 | PWS-D038 | PWS-0008, PWS-0103, PWS-0105, PWS-0107, PWS-0204 | Injection strings, fixed qpdf argv and absolute identity, working directory, controlled search paths/environment, descendant limits |
| TM-010 | PWS-D025, PWS-D033, PWS-D036 | PWS-0007, PWS-0401, PWS-0402 | Script/expression/path/network/future-version template rejection |
| TM-011 | PWS-D027, PWS-D039 | PWS-0113, PWS-0204, PWS-0305, PWS-0805 | Closed diagnostic schema and prohibited canaries across logs, errors, crashes, and later bundles |
| TM-012 | PWS-D028, PWS-D039 | PWS-0101, PWS-0108, PWS-0113, PWS-0705, PWS-0706 | Ordered case-scoped crash cleanup, invalid-case isolation, cache isolation, persistent worker ACL-lease repair, retention inventory |
| TM-013 | PWS-D028, PWS-D031 | PWS-0705, PWS-0706, PWS-0707 | Consent, lock, expiry, case isolation, handover/deletion |
| TM-014 | PWS-D029, PWS-D037 | PWS-0112, PWS-0407, PWS-0114, PWS-0409 | Corrupt/partial/wrong-page output blocked before commit |
| TM-015 | PWS-D008, PWS-D028, PWS-D029 | PWS-0101, PWS-0112, PWS-0406, PWS-0407 | Filename/ZIP traversal, collision, race, no-overwrite tests |
| TM-016 | PWS-D020, PWS-D021, PWS-D037 | PWS-0105, PWS-0206, PWS-0208, PWS-0209, PWS-0305, PWS-0308 | Signed-source detection, confirmation, unsigned-derivative and signature-image wording |
| TM-017 | PWS-D022, PWS-D023 | PWS-0405, PWS-0601, PWS-0603, PWS-0606, PWS-0607, PWS-0608 | Visual-cover rejection, canary extraction, blocked failure, independent gate |
| TM-018 | PWS-D027, PWS-D039 | PWS-0113, PWS-0805 | Safe summary baseline; later exact inventory preview, opt-in, local-only bundle, and canary scan |
| TM-019 | PWS-D028, PWS-D031, PWS-D037 | PWS-0101, PWS-0102, PWS-0113, PWS-0705, PWS-0706 | Case-ID scoping, invalid-case isolation, partial failure, erasure wording, restart |
| TM-020 | PWS-D034 | PWS-0005, PWS-0801, PWS-0802, PWS-0803, PWS-0808 | Admission, pin/hash/signature, license inventory, SBOM, artifact match |
| TM-021 | PWS-D005, PWS-D013, PWS-D034 | PWS-0803, PWS-0807, PWS-0808 | Tamper/expiry/channel/rollback/offline/interruption/privacy tests |
| TM-022 | PWS-D012, PWS-D013, PWS-D027, PWS-D038 | PWS-0008, PWS-0105, PWS-0107, PWS-0113 | Closed typed result/frame validation, UUID correlation, hostile counts, discarded tool output, sanitized exceptions, fail-closed ACL recovery |
| TM-023 | PWS-D009, PWS-D029 | PWS-0103, PWS-0104, PWS-0105, PWS-0108, PWS-0112, PWS-0401, PWS-0407 | Source handle identity/digest/reopen, result correlation, cache/preview/destination/template alias, snapshot, race, and terminal recheck tests |
| TM-024 | PWS-D026, PWS-D032, PWS-D033, PWS-D037 | PWS-0207, PWS-0403, PWS-0404, PWS-0408, PWS-0409 | Deterministic review signals, confirmation ownership, and prohibited-claim/wording checks |
| TM-025 | PWS-D008, PWS-D019, PWS-D025 | PWS-0105, PWS-0401, PWS-0405, PWS-0501 | All-object XFA/script/action/form/attachment markers, unknown-state handling, archive/revision inventory |
| TM-026 | PWS-D027, PWS-D028 | PWS-0006, PWS-0104, PWS-0305, PWS-0408, PWS-0501, PWS-0805 | Random names, metadata/assets, intended-output summaries, prohibited-log values |
| TM-027 | PWS-D024, PWS-D026, PWS-D037 | PWS-0503, PWS-0504, PWS-0505, PWS-0507 | Staged compression, measured floor, affected-page preview, unsafe/unattainable target result |
| TM-028 | PWS-D027, PWS-D035, PWS-D039 | PWS-0003, PWS-0009, PWS-0113, PWS-0805, PWS-0806 | Synthetic provenance, repository/CI scanning, restricted sensitive-sample handling |

## Gate ownership

- PWS-0010 reviews this mapping before document-engine implementation begins.
- PWS-0114 proves the first worker/import/export/recovery boundary.
- PWS-0209 proves local OCR, signed-source, and incremental resource behavior.
- PWS-0409 confirms v0.1 keeps permanent redaction disabled and uses concrete claims.
- PWS-0507 reviews size reduction, legibility, determinism, and compatibility evidence.
- PWS-0608 is the independent permanent-redaction release gate; no earlier task may substitute for
  it.
- PWS-0707 reviews shared-computer, accessibility, retention, and deletion behavior.
- PWS-0808 reviews dependencies, installer/update, migrations, diagnostics, and packaged artifacts.

When a task changes threat ownership, update the threat model, this table, the test plan, and the
task graph together. Unknown or missing IDs fail `validate-docs`.
