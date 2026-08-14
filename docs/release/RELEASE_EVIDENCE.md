# PaperWorkShell release evidence index

| Field | Value |
|---|---|
| Document status | **DRAFT TEMPLATE — complete only for an exact packaged M8 candidate** |
| Product | PaperWorkShell |
| Release version | **TBD** |
| Release channel | **TBD** |
| Evidence revision | `0-draft` |
| Source revision/tag | **TBD** |
| Release interface revision | **TBD** |

## Purpose

This document indexes frozen black-box release evidence for maintainers, deployers, support
workers, and a possible future ThirdLife B4 compatibility cut. It does not expose user data,
require ThirdLife source access, or create a shared runtime schema.

An entry is release evidence only for the exact product revision, packaged artifacts, dependency
set, and environment named in it. Source-tree tests and policy documents may be listed as
development context, but they do not make a packaged-candidate row pass. See the
[release interface](RELEASE_INTERFACE.md), [standalone checklist](STANDALONE_RELEASE_CHECKLIST.md),
and [portfolio boundary](../../THIRDLIFE_ALIGNMENT.md).

## Current development evidence available for later reconciliation

The following evidence is real but is not a completed M8 release package:

| Area | Current development evidence | Release interpretation |
|---|---|---|
| Milestone gates | [M0 foundation gate](../gates/m0-foundation-gate.md) and [M1 vertical-slice gate](../gates/m1-vertical-slice-gate.md) passed | Earlier-gate evidence must be rerun or confirmed against the packaged candidate |
| Security | [Threat model](../security/threat-model.md), [data flows](../security/data-flows.md), and [test plan](../security/security-test-plan.md) | Packaged controls and the public reporting process remain M8 work |
| Worker isolation | PWS-0107 and the M1 gate record AppContainer, Job, egress-denial, cancellation, and recovery evidence | Package composition must preserve and retest the boundary |
| Original preservation and recovery | M1 gate plus implemented immutable ingest, atomic manifest persistence, and startup recovery | Migration/uninstall/preserved-case evidence remains M8 work |
| Resources | [Policy version 1 / constrained-v1](../performance/LOW_RESOURCE_POLICY.md) and completed M1/PWS-0208 constrained evidence | These are bounded software facts, not packaged or physical-hardware certification |
| Accessibility | [PWS-0111 human keyboard/NVDA record](../accessibility/pws-0111-nvda-smoke.md) and [PWS-0203 agent scan-review record](../accessibility/pws-0203-scan-review-smoke.md) | The complete packaged M7/M8 keyboard/NVDA matrix is still required |
| Synthetic corpus | [Public synthetic fixture index](../../tests/fixtures/synthetic/v1/index.json) and [fixture policy](../development/synthetic-fixture-policy.md) | M8 must freeze a representative release subset, expected outputs, behavior notes, and hashes |
| Dependencies | [Reviewed policy baseline](../../packaging/README.md) | It is not the final package inventory, licence bundle, or SBOM |
| Diagnostics | [Structured diagnostic baseline](../architecture/startup-recovery-and-diagnostics.md) | No user-authorized support bundle exists yet; PWS-0805 owns it |

At adoption, the repository is a pre-alpha source tree with project metadata `0.0.0`; no installer,
signed package, stable application ID, frozen release sample package, or M8 release candidate
exists. All artifact-specific rows below therefore remain pending.

## 1. Frozen artifact inventory

| Evidence | Location/reference | SHA-256 or verification | Status |
|---|---|---|---|
| Installer/package | **TBD** | **TBD** | Pending |
| Portable/offline package, if supported | **TBD** | **TBD** | Pending/not supported |
| Release notes | **TBD** | **TBD** | Pending |
| Known limitations | **TBD** | **TBD** | Pending |
| `RELEASE_INTERFACE.md` | [Draft template](RELEASE_INTERFACE.md) | **TBD for frozen revision** | Pending |
| Source revision/tag | **TBD** | Signed tag/hash **TBD** | Pending |
| Dependency lock | **TBD** | **TBD** | Pending |
| SBOM | **TBD** | **TBD** | Pending |
| Licence/notice bundle | **TBD** | **TBD** | Pending |

## 2. Application identity and versioning evidence

- Reverse-domain application ID: **TBD**
- Product version/channel: **TBD**
- Publisher and product licence: **TBD**
- Supported operating systems/architectures: **TBD**
- Installer identity and upgrade/package code: **TBD**
- Evidence that PaperWorkShell versions independently from ThirdLife: **TBD for exact release**
- Future B4 evidence that a newer unverified version is not automatically compatible: **TBD in
  ThirdLife, not a PaperWorkShell gate**

The development package name and console entry point are not substitutes for a stable release
identity.

## 3. Install, launch, update, repair, and uninstall evidence

| Scenario | Exact environment | Public command/UI path | Result | Evidence reference |
|---|---|---|---|---|
| Clean install | **TBD** | **TBD** | Pending | **TBD** |
| Standard-user launch | **TBD** | **TBD** | Pending | **TBD** |
| Visible import of supported synthetic file | **TBD** | **TBD** | Pending | **TBD** |
| OS-level open of supported sample, if supported | **TBD** | Currently unsupported | Pending/not supported | **TBD** |
| Open portable workspace/project, if supported | **TBD** | Currently unsupported | Pending/not supported | **TBD** |
| Repair, if supported | **TBD** | **TBD** | Pending/not supported | **TBD** |
| Authenticated update | **TBD** | **TBD** | Pending | **TBD** |
| Interrupted/failed update recovery | **TBD** | **TBD** | Pending | **TBD** |
| Rollback, if supported | **TBD** | **TBD** | Pending/not supported | **TBD** |
| Uninstall while preserving cases | **TBD** | **TBD** | Pending | **TBD** |
| Explicit app-data removal | **TBD** | **TBD** | Pending | **TBD** |
| Reinstall and reopen preserved case | **TBD** | **TBD** | Pending | **TBD** |
| Remove optional associations, if supported | **TBD** | Currently unsupported | Pending/not supported | **TBD** |

Record prompts, privileges, restarts, and residue by category. Public evidence must use known-folder
notation rather than real usernames or private absolute paths.

## 4. Data-location and preservation evidence

- Configuration location and permissions: **TBD**
- Case/workspace location and permissions: implemented architectural convention; packaged result
  **TBD**
- Immutable-original location and hash verification: implemented case convention; candidate result
  **TBD**
- Cache/temp/log/support-bundle locations: case cache/temp known; remaining locations **TBD**
- Retention and cleanup policy: **TBD**
- Migration/rollback backup location and cleanup: **TBD**
- Original byte-for-byte preservation result: **TBD for candidate**
- Atomic save and crash-recovery result: **TBD for candidate**
- Uninstall data-preservation result: **TBD**
- Evidence that ThirdLife is absent and neither owns nor migrates PaperWorkShell data: **TBD for
  packaged black-box matrix**

## 5. Public input/output and black-box workflow evidence

| Workload | Input hashes | Expected output | Output hashes | Verification | Evidence |
|---|---|---|---|---|---|
| Canonical document-package workflow | **TBD** | PDF/ZIP/summary **TBD** | **TBD** | Pending | **TBD** |
| Mixed PDF/JPEG/PNG workflow | **TBD** | **TBD** | **TBD** | Pending | **TBD** |
| Interruption and recovery | **TBD** | Recovered case and verified export | **TBD** | Pending | **TBD** |
| Malformed/hostile input | **TBD** | Safe failure; no corrupt case | N/A | Pending | **TBD** |
| Low-space/slow-I/O workflow | **TBD** | Safe failure or verified completion | **TBD** | Pending | **TBD** |

Verification must include expected page count/order, parse/reopen, structural checks, rendering,
constraints, and unchanged source hashes.

## 6. Synthetic release sample package

| Artifact | Purpose | SHA-256 | Provenance | Status |
|---|---|---|---|---|
| Sample PDF input | Basic born-digital/scanned input | **TBD** | Synthetic/cleared **TBD** | Pending |
| Sample JPEG/PNG input | Image intake | **TBD** | Synthetic/cleared **TBD** | Pending |
| Sample portable workspace/project | Transfer fixture, only if product supports it | N/A | N/A | Not currently supported |
| Expected combined PDF | Output verification | **TBD** | Generated | Pending |
| Expected multi-file/ZIP output | Packaging verification | **TBD** | Generated | Pending |
| Expected package summary | Human-readable output | **TBD** | Generated | Pending |
| Sample sanitized support bundle | Support-boundary verification | **TBD** | Generated | Pending |
| Hash/expected-behavior manifest | Black-box fixture index | **TBD** | Project documentation | Pending |

No release sample may contain real identity, financial, medical, employment, signature,
beneficiary, or applicant data.

## 7. Support-bundle privacy evidence

- Support-bundle creation path: **TBD**
- User preview screenshot or accessible transcript: **TBD**
- Exact approved allow-list: **TBD**
- Prohibited-token test corpus: **TBD**
- Automated absence of document/OCR text, values, names, addresses, filenames, paths, metadata,
  thumbnails, signatures, credentials, and document bytes: **TBD**
- Cancellation/permission/disk-full behavior: **TBD**
- Sample sanitized bundle hash: **TBD**
- Evidence that PaperWorkShell and ThirdLife bundles remain separate: **TBD**

## 8. Offline and network evidence

- Core workflow with network unavailable: **TBD for candidate**
- Worker egress-denial result: **TBD for candidate**
- Optional update/language-pack disclosure: **TBD**
- Interrupted optional-download recovery: **TBD**
- Offline installation or verified media/cache procedure: **TBD**
- Packet-capture/privacy review: **TBD**
- No mandatory ThirdLife process, sibling product, account, or project server: **TBD for black-box
  candidate**

## 9. Low-resource evidence

- Resource policy/profile version: **TBD for candidate**
- Workload IDs and input hashes: **TBD**
- Enforced concurrency/cache/temp limits: **TBD**
- Peak complete process-tree memory and worker Job facts: **TBD**
- CPU and elapsed time: **TBD**
- Peak cache and temporary storage: **TBD**
- Output size: **TBD**
- Slow-I/O and low-space/disk-full behavior: **TBD**
- Cancellation acknowledgement and cleanup: **TBD**
- No-discrete-GPU/CPU-path evidence: **TBD**
- Hardware wording and limitations: **TBD**

No named physical configuration may be described as verified unless it was actually tested.

## 10. Accessibility evidence

- Keyboard-only primary packaged workflow: **TBD**
- No drag-only operation: **TBD**
- Focus order and visible focus: **TBD**
- Names, roles, states, relationships, and progress announcements: **TBD**
- NVDA environment and result: **TBD**
- 200-percent scaling/text and clipping result: **TBD**
- Contrast/high-contrast/reduced-motion result: **TBD**
- Plain-language error review: **TBD**
- Accessible package-summary result: **TBD**
- Known output-accessibility limitations: **TBD**

Earlier task-level accessibility records are linked above and must not be relabeled as the final
packaged matrix.

## 11. Security and privacy evidence

- Threat-model revision: **TBD for release freeze**
- Packaged worker isolation and network denial: **TBD**
- Path traversal, command injection, and unsafe-overwrite tests: **TBD**
- Hostile PDF/image/template tests: **TBD**
- Timeout, memory, page, pixel, and temporary-storage limits: **TBD**
- Original preservation and signed-source handling: **TBD**
- Logging/support prohibited-content audit: **TBD**
- Update provenance/signature tests: **TBD**
- Independent M6 permanent-redaction gate and exact-candidate result: **TBD; required for PWS-0808/v1.0. A preview that omits redaction cannot satisfy that gate**
- No-telemetry default: **TBD for candidate**
- Security reporting process: **TBD**

## 12. Migration and recovery evidence

- Supported prior case versions: **TBD**
- Forward migration and idempotent reopen: **TBD**
- Interrupted/disk-full migration: **TBD**
- Future-version rejection: **TBD**
- Worker crash/restart recovery: **TBD for candidate**
- Update failure and application rollback/recovery: **TBD**
- Migration-backup cleanup: **TBD**
- Recovery documentation walkthrough: **TBD**
- Confirmation that only PaperWorkShell performs case migrations: **TBD**

Portable workspace/project import/export remains unsupported unless later independently approved;
case-schema migration does not imply a portable handoff format.

## 13. Licence, provenance, and maintenance evidence

- Final packaged dependency inventory and SBOM: **TBD**
- Bundled engine/model/template/font licences and notices: **TBD**
- Release input hashes and provenance: **TBD**
- Reproducible or documented-build status: **TBD**
- Vulnerability reporting route: **TBD**
- Supported versions and security-update policy: **TBD**
- Upstream abandonment/replacement strategy: **TBD**
- Maintenance status and capacity limits: **TBD**

## 14. Known limitations

Exact release limitations document: **TBD**.

At minimum, record unsupported formats, forms/signatures, redaction status, OS/file opening and
project-transfer status, rollback constraints, accessibility and resource limitations, and every
untested environment.

## 15. Portfolio handoff status

| Requirement | Status | Evidence |
|---|---|---|
| Frozen installer/package and hashes | Pending | **TBD** |
| Complete release interface | Pending | **TBD** |
| Non-sensitive samples and hashes | Pending | **TBD** |
| Sanitized support-bundle sample | Pending | **TBD** |
| Low-resource candidate profile | Pending | **TBD** |
| Migration/update/uninstall results | Pending | **TBD** |
| Security/privacy acceptance | Pending | **TBD** |
| Accessibility acceptance | Pending | **TBD** |
| Offline acceptance | Pending | **TBD** |
| Standalone integrity without ThirdLife | Pending | **TBD** |
| Ready for future B4 review | **No — draft template** | Complete only after the standalone release gate |

## 16. Approval

| Role | Name/identifier | Date | Result |
|---|---|---|---|
| PaperWorkShell release owner | **TBD** | **TBD** | Pending |
| Security review | **TBD** | **TBD** | Pending |
| Accessibility review | **TBD** | **TBD** | Pending |
| Release gate | **TBD** | **TBD** | Pending |

A future ThirdLife B4 review is separate. Completing this index does not approve PaperWorkShell's
inclusion in any ThirdLife release.
