# Emergency dependency security updates

- Task: PWS-0801
- Policy revision: 1
- Effective: 2026-11-02
- Scope: PaperWorkShell runtime, build tools, native libraries, engines, and data

An urgent security fix follows the same admission, licensing, provenance, isolation, and release
checks as an ordinary dependency change. Urgency may change the work order; it does not waive a
release gate. This policy defines maintainer work. It does not establish a response-time promise,
a supported-version list, or a public reporting contact.

At adoption, authenticated updates and signed application distribution remain PWS-0802/PWS-0803
work. This policy does not claim that a signed release or functioning updater already exists.
Published contacts, support commitments, and vulnerability-handling documentation belong to
PWS-0806. The [dependency policy](dependency-policy.md), [frozen decisions](../../DECISIONS.md),
and [security model](../security/README.md) continue to apply.

## 1. Establish the affected inputs and exposure

The maintainer records the upstream advisory or report, its observation date, affected component
and exact versions or distribution revisions, affected PaperWorkShell release/channel, and the
reason the shipped use is exposed. A family name or an installed development version is not a
release identity. Consult the released SBOM, packaged-file inventory, lock, and embedded-component
records; a vulnerable library may be inside a wheel or native archive with a different name.

Use public upstream evidence and synthetic reproductions. Do not ask a reporter for an applicant
document or copy document text, OCR output, names, user paths, case contents, or signature images
into routine logs or public issues. Report handling follows the
[restricted-sample policy](synthetic-fixture-policy.md); an eventual opt-in sanitized support
bundle does not authorize collection of document content.

Record separately what is proved, what remains uncertain, and any temporary user guidance. Do
not infer that network denial alone prevents a malicious-document exploit. Preserve the original
report and the exact affected release's signed metadata, hashes, SBOMs, notices, source, and test
receipts as historical evidence. A new fix must not rewrite a past result or replace a released
artifact under its old identity.

## 2. Choose the smallest compliant remedy

Prefer a reviewed upstream fixed artifact or a narrowly patched source build. Compare direct,
transitive, embedded, executable, language-data, and platform-specific changes. Assess the actual
capability used, license route, binary interfaces, worker boundary, resource behavior, migration
effects, and packaging obligations. If no compliant fix is available, record the limitation and
block the affected release; disabling an affected capability is possible only through the normal
local product/task decision process with accurate user guidance.

The [dependency admission procedure](dependency-policy.md#adding-a-dependency) applies to an
emergency replacement as well as an upgrade. No previously evaluated candidate, ambient system
tool, unreviewed fallback, or distribution-altering component becomes approved through urgency.
Use the [decision change procedure](../../DECISIONS.md#change-procedure) for changes to an
owner-approved product requirement. Uncertain redistribution rights block the affected
distribution until resolved. Record the actual rights in addition to the SBOM and license label.

## 3. Freeze the replacement and its complete evidence

Identify the exact source revision, patches, build recipe/toolchain, target, release inputs, and
consumed artifacts. Expected artifact hashes must come from the reviewed provenance channel;
hashing an otherwise untrusted download is not authentication. Verify applicable upstream
signatures before extraction or execution. Acquire inputs in the maintainer build process, never
through the document worker.

Update together:

- Direct constraints, `uv.lock`, admitted component/relationship records, and affected native or
  data closure manifests, including their byte sizes and hashes.
- The selected license route, notices, license texts, corresponding source, replacement or
  relinking instructions, and any source offer required for the exact shipped files.
- A resolved Windows staging inventory assigning every file to its reviewed component, both
  CycloneDX JSON and SPDX JSON, and the human-readable notice bundle for the same build.
- The source snapshot/revision, exact build-input hashes, artifact hashes, known limitations, and
  current-to-fixed component comparison in the candidate evidence.

Use a clean release environment and compare the actual package contents with the resolved
inventory. Do not copy a development environment wholesale or activate excluded renderer/tool
candidates. Missing, extra, unowned, tampered, or unexplained files prevent approval. Keep secrets,
certificates, private keys, purchase records, and signing credentials outside the repository and
ordinary tool output.

## 4. Verify the fix and the release boundary

Add a deterministic synthetic regression for the reported exposure when feasible and document
its before/after result. Run the repository's format, lint, type, schema, task, dependency,
portfolio, privacy, and secret checks plus affected unit/integration/security/accessibility tests.
Run the full required candidate checks; a focused regression alone does not close release gates.

For document engines or their native closure, exercise the affected hostile inputs, import,
preview, OCR, export/reopen/render, cancellation, timeout, termination, cleanup, recovery,
source-preservation, and egress controls as applicable. Enforce the existing constrained resource
policy and storage failure checks; do not raise limits just to pass an emergency upgrade. A
redaction-affecting change retains its independent gate and must fail export when verification
does not succeed.

PWS-0803 must establish update tests for valid, unsigned, tampered, expired, wrong-channel,
rollback-forbidden, offline, and interrupted updates, including privacy capture and recovery of
the previous working version. The emergency candidate must run those tests against its exact
artifacts once that mechanism exists. Until then, these are required future implementation
checks, not accepted evidence or a reason to invent an update path. PWS-0802 owns signing and
installer behavior; PWS-0807/PWS-0808 own packaged-candidate verification and final approval.

## 5. Handle compromised trust material explicitly

If a signing key, upstream trust channel, or update metadata may be compromised, stop approval or
publication through that trust path and preserve the affected metadata and artifacts for review.
Identify the affected key identities, versions, channels, and trusted metadata without disclosing
secret material. The release owner must establish an independent trusted recovery route before
accepting replacement keys or artifacts; metadata authenticated only by the suspect key cannot
serve as its own recovery authority.

Record the revocation/rotation decision, new public trust identity and its provenance, supported
client recovery steps, expiry and rollback policy, and tests of valid and rejected recovery paths.
PWS-0803's approved trust design must implement and test this behavior. If deployed clients
cannot recover securely through that design, publish maintainer-approved manual recovery
instructions using an independently verified signed package. Do not disable authentication,
accept expired metadata, permit an otherwise forbidden downgrade, or grant the worker network
access as a workaround. No current key-rotation or revocation capability is claimed by this
policy.

## 6. Approve, communicate, and preserve the record

The release owner reviews the exact candidate, observed regression/security results, licensing
closure, migration/recovery effects, remaining limitations, and applicable gate approvals. Freeze
its [release interface](../release/RELEASE_INTERFACE.md),
[evidence index](../release/RELEASE_EVIDENCE.md),
[standalone checklist](../release/STANDALONE_RELEASE_CHECKLIST.md), SBOMs, notices, source/build
provenance, signatures, and checksums together. Unproved candidate facts stay draft rather than
being copied from an earlier release.

User guidance states affected and fixed versions, the concrete risk and scope of available
evidence, available mitigations, remaining limitations, and the verified installation/recovery
method. Publish through the product's established reporting and distribution routes after those
routes are defined; do not invent a contact, support commitment, or guaranteed fix date. Keep
historical artifacts and records immutable, while marking superseded or withdrawn releases
clearly. Record any follow-up upstream issue or replacement work without claiming its outcome.

Document processing remains fully usable offline, optional update checks remain separate from
it, and an update does not upload or grant access to case/document content. Do not silently
replace a worker component outside the approved authenticated installation flow. PaperWorkShell
owns its migration and rollback decisions, releases independently, and requires no ThirdLife or
sibling source, service, account, data, schedule, adapter, or compatibility result for the fix.
