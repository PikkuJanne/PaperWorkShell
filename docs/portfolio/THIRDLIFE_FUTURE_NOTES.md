# PaperWorkShell future ThirdLife assembly notes

| Field | Value |
|---|---|
| Status | **Non-binding, non-gating, and no implementation authorization** |
| PaperWorkShell role | Record observations only |
| Future implementation owner | ThirdLife Deployment and Suite Assembly, Team B / B4 |
| Earliest product-specific work | After a frozen PaperWorkShell stable release and completed release interface |

## Purpose

This file records possible future ThirdLife catalogue, profile, launch, or adapter ideas discovered
while developing PaperWorkShell. Nothing here blocks a PaperWorkShell task or release, authorizes a
runtime integration, requires a CLI/URI/schema/association, or changes the product boundary in
[the alignment contract](../../THIRDLIFE_ALIGNMENT.md).

Accepted observations may later be copied into a ThirdLife-owned assembly backlog. An adapter brief
belongs in the future B4 repository, not here. Do not add a `TASKS.yaml` dependency merely because
a note exists.

## Admission rule for a future custom adapter

A custom adapter is considered only when all five questions have evidence-based positive answers:

1. **User value:** Does it remove several error-prone steps from a frequent, high-value journey for
   a defined target group?
2. **Safety:** Are exact files, settings, privileges, and failure recovery visible before action?
3. **Independence:** Do both products remain useful when the adapter is absent or disabled?
4. **Maintenance:** Can ThirdLife bind supported versions, test frozen artifacts, and offer a manual
   fallback?
5. **Data ownership:** Does it avoid shared content storage and disclose copying, conversion,
   reference, and deletion?

If normal installation, launch, visible import/export, or human guidance is adequate, no custom
adapter is justified.

## Candidate notes

### TFA-PWS-001 — Catalogue and profile inclusion

| Field | Note |
|---|---|
| Status | Candidate generic B4 action; no PaperWorkShell code required |
| Journey | A refurbisher, library, charity, or support worker selects a profile that includes PaperWorkShell |
| Permitted action | Display purpose, exact supported version, package size, privileges, licence, limits, and install/remove choices |
| Public artifacts | Installer/package, hash/signature, [release interface](../release/RELEASE_INTERFACE.md), [evidence](../release/RELEASE_EVIDENCE.md), notices, limitations |
| Data touched | ThirdLife catalogue/profile metadata only |
| Manual fallback | Install PaperWorkShell through its normal documentation |
| No-go | ThirdLife identity inside PaperWorkShell, case discovery, content indexing, or shared workspace |
| Earliest stage | B4 compatibility cut after PaperWorkShell stable |

### TFA-PWS-002 — Normal launch shortcut

| Field | Note |
|---|---|
| Status | Candidate shallow action |
| Journey | A profile or handover screen helps the user start PaperWorkShell |
| Permitted action | Invoke the documented interactive launch path for an exact supported version |
| Public artifacts | Stable application identity and launch behavior in the release interface |
| Data touched | Normal operating-system process-launch metadata only |
| Manual fallback | Start PaperWorkShell from its ordinary application entry |
| No-go | Hidden work, automatic case opening, or background processing |
| Earliest stage | B4 after a frozen release |

### TFA-PWS-003 — Open a user-selected supported document

| Field | Note |
|---|---|
| Status | Candidate only if PaperWorkShell independently supports documented OS opening |
| Journey | The user explicitly selects a supported document and chooses PaperWorkShell |
| Permitted action | Pass that one explicit path through visible documented behavior |
| Public artifacts | Inputs, opening behavior, copy/reference semantics, consent, and failure behavior |
| Data touched | Only the user-selected path; PaperWorkShell owns its import workflow |
| Manual fallback | Start PaperWorkShell and use Add/Import |
| No-go | Folder scanning, automatic import, bypassing consent, or reading the case afterward |
| Current state | Unsupported; no OS-open contract or integration CLI is frozen |

### TFA-PWS-004 — Open a documented workspace or project export

| Field | Note |
|---|---|
| Status | Candidate only after an independently useful portable format is approved and stable |
| Journey | A user resumes a user-selected portable PaperWorkShell project, if supported |
| Permitted action | Pass the selected path to PaperWorkShell without parsing private contents |
| Public artifacts | Versioned import/export docs, migration behavior, synthetic sample, and limitations |
| Data touched | User-selected project path only |
| Manual fallback | Use PaperWorkShell's normal Open/Resume action, if such a portable action exists |
| No-go | ThirdLife schema interpretation, database migration, content search, or case repair |
| Current state | Unsupported; local case reopen/migration does not imply a portable project format |

### TFA-PWS-005 — Optional file association or “Open with” guidance

| Field | Note |
|---|---|
| Status | Candidate generic deployment action; independent product-value and user-control review required |
| Permitted action | Offer an opt-in association for a release-supported input and preview exact changes |
| Public artifacts | Stable app ID, inputs, installer association behavior, and uninstall cleanup |
| Data touched | Operating-system association settings only |
| Manual fallback | Use Open with manually when supported, or import inside PaperWorkShell |
| No-go | Taking over the default PDF viewer, unrelated associations, or stale uninstall handlers |
| Current state | Unsupported |

### TFA-PWS-006 — Handover and support guidance

| Field | Note |
|---|---|
| Status | Candidate shallow guidance action |
| Journey | A volunteer or recipient needs safe launch, storage, and support guidance |
| Permitted action | Link public user/recovery docs and explain PaperWorkShell's own support-bundle workflow |
| Public artifacts | User/data/uninstall/support guides, limits, and reporting route |
| Data touched | ThirdLife handover metadata only |
| Manual fallback | Use PaperWorkShell documentation directly |
| No-go | Automatically collecting or merging diagnostics, documents, logs, or case content |
| Earliest stage | B4 after stable documentation and PWS-0805 |

## Rejected or deferred patterns

| Idea | Disposition | Reason |
|---|---|---|
| Shared PaperWorkShell/ThirdLife manifest | Rejected before evidence from released products | Creates schema coupling and a hidden integration platform |
| Universal portfolio handoff package | Rejected by default | Ordinary PDFs, ZIPs, summaries, and any independently supported project format come first |
| Shared user-content database/workspace | Prohibited | Violates ownership and uninstall independence |
| ThirdLife reads PaperWorkShell cases/manifests for discovery | Prohibited | Exposes private content and depends on internals |
| ThirdLife indexes documents, OCR text, or metadata | Prohibited | Violates the data boundary |
| Hidden control of redaction, compression, OCR, export, migration, or deletion | Prohibited | Domain workflow must remain visible inside PaperWorkShell |
| PaperWorkShell embeds ThirdLife source, SDK, or service | Prohibited | Creates live dependency and synchronized maintenance |
| ThirdLife-only command line | Rejected without independent PaperWorkShell value | Permanent speculative interface cost |
| Synchronized product/suite releases | Rejected | Versions and schedules remain independent |
| Automatic adoption of latest PaperWorkShell | Prohibited | B4 uses explicit compatibility cuts and “unverified” state |
| PaperWorkShell release blocked on ThirdLife fixtures/tests | Prohibited | Cross-product tests occur only in B4 against frozen artifacts |

## Recording a new note

Include the target user/journey, why ordinary behavior is insufficient, public artifacts consumed,
data and paths touched, privileges, failure/manual fallback, version bounds, owner/retirement plan,
and why the idea does not change PaperWorkShell's product boundary. Keep the note non-gating.

