# PaperWorkShell standalone and ThirdLife-readiness release checklist

| Field | Value |
|---|---|
| Applies to | Packaged M8 preview/stable candidates and the unsigned v1.0 product-completion gate |
| Does not apply as a new gate to | The Milestone 4 v0.1 pilot |
| Principle | PaperWorkShell is complete without ThirdLife; future suite work consumes frozen public artifacts |
| Completion rule | A checked item requires current evidence for the exact packaged candidate |

This checklist records the evidence required for M8 acceptance under PWS-D044. An
identified unsigned candidate is eligible. External publication requires separate
authorization. “Not supported” is an acceptable
release-interface answer when accompanied by a product reason and tests that prove safe visible
failure. The checklist must not force a portable project format, operating-system file-opening
contract, association, CLI, repair mode, rollback mode, or silent installer into PaperWorkShell
solely for future ThirdLife convenience.

Current application and release scope is **Windows 11 x64 only**, selected by
[PWS-D043 / ADR 0019](../adr/0019-windows-11-x64-only-scope.md). Native ARM64
and x64 emulation on ARM64 are outside project scope, with no release/update
prerequisite or mandatory future task. Use the
[support boundary](WINDOWS11_X64_SUPPORT.md),
[PWS-0802 qualification record](../development/pws0802-x64-continuation-20261104.md) and
[finite x64 qualification matrix](../development/pws0802-x64-qualification-20261104.md)
to distinguish selected scope, accepted engineering evidence and missing checks.
Scope selection does not check any scenario below or approve a public release.

The entries below apply to the unsigned engineering candidate
`pws0807-tab-focus-engineering-r3`, version 0.0.0. The Windows installation
evidence covers Pro x64 26300.9711 on the disconnected standard-user guest.
The [R3 qualification ledger](../development/pws0807-r3-qualification.md),
[evidence index](RELEASE_EVIDENCE.md) and [release interface](RELEASE_INTERFACE.md)
identify the tested artifact, sample and methods. Earlier candidates retain
their own results, including the selected-tab failure on R2 and package
timeouts on R1. PWS-0808 owns the product-completion decision. External
distribution requires separate authorization.

## 1. Product boundary and independence

- [x] The primary document-package journey works without ThirdLife, sibling applications, a mandatory account, or a project-controlled server.
- [x] PaperWorkShell's owns/does-not-own, primary-data, and standalone statements are consistent with [the alignment contract](../../THIRDLIFE_ALIGNMENT.md).
- [x] No feature or release criterion depends on another portfolio product, sibling branch, private cross-repository fixture, or development build.
- [x] No shared SDK, user-content database, workspace, runtime schema, universal job engine, plugin framework, or portfolio background service is required.
- [x] No command, URI, RPC, association, or schema field exists solely for hypothetical ThirdLife integration.
- [x] PaperWorkShell remains usable when any future adapter is absent, disabled, incompatible, or removed.
- [x] PaperWorkShell's version and release schedule remain independent.

## 2. User data and ownership

- [ ] Configuration, cases, immutable originals, derivatives, caches, temporary data, optional engines/models, logs, support bundles, migration backups, and exports are documented.
- [ ] Known-folder notation, permissions, and current-user ownership are verified on the package without publishing private usernames or paths.
- [x] Opening/importing states whether the product copies, references, converts, or exports a selected file.
- [x] Original inputs remain unchanged and are never overwritten by default.
- [ ] Case deletion and delete-after-export behavior are explicit and never claim forensic erasure.
- [ ] PaperWorkShell installs or delegates no ThirdLife-owned authority that can remove PaperWorkShell cases; its case storage and lifecycle remain independently owned.
- [ ] PaperWorkShell uninstall gives separate explicit choices for application removal and each supported user-data category.
- [ ] Reinstall can reopen intentionally preserved supported cases.

## 3. Install, update, repair, and removal

- [x] A clean supported Windows 11 x64 environment installs and launches PaperWorkShell without development tools, within the exact edition/build evidence boundary.
- [x] Package identity, version, source, size, hash, observed signing state, inventory and privileges are recorded. Size and hash match the retained build reference.
- [ ] Elevation occurs only for the selected install/update/repair/remove operation.
- [ ] Silent options are tested and documented or explicitly unsupported.
- [ ] Local preserve-cases replacement, interruption, failure recovery and offline behavior are tested on the exact candidate.
- [ ] Same-build repair and applicable recovery are tested. The live update channel is disabled, and the isolated authenticated verifier's rejection evidence is recorded.
- [ ] Uninstall removes application registrations and any supported associations while following the user's data choice.
- [ ] Remaining files/settings are documented by category.

## 4. Public inputs, outputs, and launch behavior

- [x] Supported ordinary inputs are documented and tested on the package.
- [ ] Supported outputs are documented and tested, including copy/convert/flatten/reconstruct/reference behavior.
- [x] Normal interactive launch is documented.
- [x] Visible file opening is tested when supported, or explicitly documented as unsupported with the in-app import fallback.
- [x] Portable workspace/project opening is tested when independently supported, or explicitly documented as unsupported.
- [ ] Optional file associations are previewed, user-controlled, removable, and tested, or explicitly unsupported.
- [ ] Independently useful CLI options, if any, have local justification, tests, and documentation.
- [x] Direct portal submission remains outside the product boundary.

## 5. Core quality and recovery

- [ ] The complete primary workflow passes on a clean install and after a supported upgrade.
- [x] Interrupted processing, worker termination, application restart, and case recovery are tested.
- [ ] Partial or corrupt output is never presented as final.
- [ ] Every exported PDF is reopened, structurally checked, rendered, and checked for expected page count/order and constraints.
- [ ] Low-space, disk-full, slow-I/O, constrained-resource, CPU-path, large-input, cancellation, and malformed-input behavior are tested.
- [x] Resource budgets, workloads, versions, hashes, and limitations are recorded without claiming untested hardware certification.
- [x] Core processing works offline.

## 6. Accessibility and understandable operation

- [x] The packaged primary workflow is completable with keyboard only.
- [ ] No action depends solely on drag-and-drop or color.
- [ ] Focus, names, roles, states, relationships, errors, and progress announcements pass.
- [ ] NVDA testing passes for the required primary workflow; only limitations outside the supported workflow or output scope may remain when explicit and release-approved.
- [ ] 200-percent scaling/text, high contrast, plain-language errors, and confirmation dialogs pass.
- [ ] Long operations expose progress, cancellation, and actionable recovery.
- [x] Package summaries are accessible to the supported extent and output limitations are honest.

## 7. Security, privacy, and support

- [ ] Documents, images, templates, project files, and optional engine packages are treated as untrusted where applicable.
- [ ] Worker isolation, filesystem scoping, network denial, limits, path validation, and shell-free subprocess launch pass on the package.
- [ ] Routine logs and evidence contain no document/OCR text, values, names, addresses, original or user filenames, full or private local paths, extracted metadata, thumbnails, signatures, credentials, or document bytes; public artifact names, known-folder notation, and repository-relative evidence links remain allowed.
- [x] The support bundle is local, opt-in, previewable, allow-listed, and prohibited-token tested.
- [x] PaperWorkShell and ThirdLife support artifacts remain separate.
- [x] Update and dependency provenance are verified.
- [ ] Threat model, hostile-input tests, no-telemetry default, and vulnerability-reporting process are current.
- [ ] The PWS-0808/v1.0 candidate includes permanent redaction and passes its independent export-blocking gate against that exact candidate; a preview that omits redaction cannot satisfy PWS-0808.
- [x] Signature-image terminology never implies cryptographic or qualified signing.

## 8. Dependencies, licences, and maintainability

- [x] Dependency lock, exact packaged inventory, source hashes, machine-readable SBOM, and notices match.
- [x] Bundled engines, language data, templates, fonts, and assets have reviewed redistribution terms.
- [ ] Major external engines remain behind product-local adapters with replacement/abandonment strategies.
- [x] Release inputs do not silently fetch an unverified “latest” component.
- [ ] Supported versions, security-update policy, reporting route, and maintenance status are documented.

## 9. Release interface and evidence package

- [ ] [The release interface](RELEASE_INTERFACE.md) is complete for the exact candidate with no unexplained `TBD`.
- [ ] Stable identity, publisher, licence, Windows 11 x64-only scope, evidenced editions/builds, and release channel are recorded.
- [ ] Installer, launch, inputs/outputs, data, offline/resource behavior, privilege/security, support, samples, limits, and contacts are complete.
- [ ] [The evidence index](RELEASE_EVIDENCE.md) points to current packaged results and hashes.
- [x] Public synthetic sample inputs, expected outputs, behavior notes, and hashes are frozen.
- [x] A workspace/project sample is included only if PaperWorkShell supports a portable project format.
- [ ] A sample sanitized support bundle and exclusion proof are published.
- [ ] Migration, update, rollback/non-rollback, uninstall, and data-preservation results are indexed.
- [ ] Security, accessibility, offline, and resource acceptance results are indexed.
- [ ] Artifacts, interface, evidence, limitations, and samples are frozen together.

## 10. Future ThirdLife boundary

- [x] Future actions remain limited to documented installation/update/removal, launch, optional user-controlled opening/association, and guidance unless B4 separately approves an adapter.
- [x] ThirdLife never needs to read PaperWorkShell cases, manifests for content inspection, documents, OCR text, overlays, signatures, redaction state, or logs.
- [x] A complete normal/manual path exists for every future shallow action.
- [x] A newer unverified PaperWorkShell release is not automatically considered compatible.
- [x] No unfinished adapter or cross-project feature is required to declare PaperWorkShell useful or stable.
- [x] [Future notes](../portfolio/THIRDLIFE_FUTURE_NOTES.md) remain observations and create no project dependency.

## 11. Sign-off

- [ ] All release-blocking defects are closed or the candidate is rejected.
- [ ] Known limitations avoid portal-acceptance, legal-advice, OCR-certainty, PDF/UA, forensic-erasure, hardware-certification, and unsupported security claims.
- [ ] The tested packaged candidate matches the frozen hashes and SBOM.
- [ ] The PaperWorkShell release owner approves the standalone release.
- [x] ThirdLife inclusion remains a separate future B4 compatibility decision.
