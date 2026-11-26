# ADR 0022 — Authenticated PaperWorkShell updates

- Status: Proposed update and recovery design. The first engineering increment verifies local bytes.
- Date: 2026-11-26
- Task: PWS-0803
- Decisions preserved: PWS-D005, PWS-D013, PWS-D027, PWS-D034, PWS-D042, PWS-D043

## Installed baseline

PaperWorkShell uses the per-user Inno Setup installation, application ID
`io.github.pikkujanne.paperworkshell`, and upgrade ID
`{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}`. The installer holds the lifecycle guard,
checks the fixed destination and payload paths, and allows same-candidate repair only when
the installed marker has the exact expected digest. `PrepareToInstall` refuses a different
candidate or an existing unmarked folder. Keep that refusal while authenticated replacement
has no qualified entry point. The current lifecycle guard is session-local. Replacement
also needs per-installation exclusion across sessions for the same Windows user.
[Installer identity and recovery policy](0018-windows-installer-and-release-identity.md).

The prepared TRACE B8 setup reads a receipt whose digest comes from a trusted delivery
source. It verifies the installer and installed payload, repairs the same candidate when
needed, and launches the verified installation. The disconnected Windows 11 x64 run proved
same-build repair, reuse, and an ordinary PDF workflow as a standard user. It used an unsigned
engineering installer and did not exercise an upgrade. A receipt binds bytes to an expected
hash. Origin authentication requires a separate trust authority.
[TRACE B8 setup evidence](../development/pws0814-automated-setup.md).

## Proposed update channel

Distribute complete PaperWorkShell packages through a product-owned channel. Begin with
explicit verification of a locally obtained package, which permits offline update and
leaves the ordinary offline workflow independent of distribution availability. A future
online check requires explicit user action and a channel-specific allowlist of request data.
Connection metadata may be visible to the distribution service. No case directory, document,
filename, path, identifier, digest, OCR text, template, diagnostic, device identifier, or
account field belongs in an update request. The updater must have no case access or worker
RPC route, and document workers retain their existing network denial.

The first increment is a verifier for a bounded, versioned JSON record and a separate
installer byte stream. Its inputs include the configured channel, a trusted UTC evaluation
time, and the release-sequence floor for that channel. A signature verifier supplied by the
caller checks the domain separator `PaperWorkShell update metadata v1` followed by one zero
byte and the exact received UTF-8 JSON bytes. Engineering tests use isolated Ed25519 keys
and detached 64-byte signatures. The verifier has no production key source or transport.
The current reviewed `cryptography` distribution is restricted to its existing worker use.
The test adapter uses the pinned dependency. Production use in another process needs its
own dependency and packaging-boundary review. [Ed25519 verification API](https://cryptography.io/en/50.0.0/hazmat/primitives/asymmetric/ed25519/).

The signed record contains `schema_version`, `application_id`, `platform`, `channel`, `version`,
`candidate_id`, `sequence`, `issued_at`, `expires_at`, `package_size`, and `package_sha256`.
The platform is `win-x64`. The sequence orders releases within a channel, including engineering
candidates that share a display version. The verifier requires a sequence above the supplied floor.
An expired or not-yet-valid record, a wrong product, channel or
platform, an unknown schema, a malformed record, a failed signature, or a package length or
digest mismatch fails before any installation is possible. The engineering limits are 4 KiB
of metadata and 1 GiB of package bytes. The verifier streams the package and hands back a
verification result. The verdict applies to the byte stream it read.

## Production trust and consent

Before an install path is enabled, establish release trust independently of offered metadata.
Bind metadata key authority to the exact product and channel. An offered package or key
cannot bootstrap its own authority. Establish a separate trusted recovery authority for
revoking or rotating compromised keys and recovering clients across missed rotations. Reject
expired, untrusted, or rolled-back trust records, including a replacement authorized only by
the suspect key. Define usable recovery instructions for clients that cannot advance their
trust securely. Review any network distribution and key hierarchy against established update
security practices before activation. Define a maximum offer lifetime and clock behavior, and
bind release sequences to the reviewed build and channel in the signing pipeline. A withheld
update or invalid clock can make it impossible to determine freshness. Leave the installed
application usable and report that update
verification is unavailable. [Emergency key recovery policy](../development/emergency-security-updates.md#5-handle-compromised-trust-material-explicitly).
[The Update Framework specification](https://theupdateframework.github.io/specification/v1.0.36/).

Production installation requires both the exact authenticated installer bytes and approval
under the release's Windows Authenticode publisher and timestamp policy. A valid signature
from another publisher cannot substitute for that approval. The existing release signing
tools supply build-time evidence. Runtime verification, signer rotation and offline
revocation behavior require their own tests before installation is enabled.
[Windows release signing procedure](0018-windows-installer-and-release-identity.md#signing-and-exact-output-identity).

Present the verified channel, current and offered versions, and the planned installation
action in an accessible review. Install only with explicit confirmation while retaining
required exact recipient-term consent. Support canceling verification and leaving the current
installation in place. An optional online check can discover an offer only. There is no
automatic install, worker or engine self-update, or release coupling to ThirdLife. Keep
application, engine and language versions attached to the exact installed release inventory.
Record bounded local audit events with version, channel, candidate identifier, outcome and
stable error code. Exclude user paths, raw metadata and private content from routine logs.

## Replacement and failure recovery

Keep the verified prior installation launchable until the replacement is ready. Stage verified
bytes under an installer-owned local location separate from cases, with storage and time
bounds. Check exact package identity at the execution boundary and protect against a path,
link, or content swap between verification and use. Require normal close of the application
and all lifecycle users before activation, use per-user privilege, and hold an exclusion
guard for normal launch, repair and removal while changing launch authority. Keep existing
installer refusal behavior for paths outside this controlled operation.

Choose and prove either a separately staged application directory with a recoverable switch
of stable launch and registration, or an equivalent installer transaction with exact prior
build recovery. An ordinary Inno exit or `[Run]` launch alone cannot establish that guarantee.
Before activation, reconcile the signed application, version, channel and candidate ID with the
exact staged build marker and inventory. Verify the complete runtime against that authenticated
inventory and run a health check that opens no user cases. An interrupted activation must
resolve to the previous known-good installation or a clearly recoverable state using the exact
previously established
package and registration. Restoration of that specific prior build under the recorded
recovery transaction grants no permission to install an arbitrary older update. Record the
committed release sequence durably and advance it only with successful activation. An older
version must never open cases changed by incompatible migration. Test compatibility or preserve
verified recoverable case state before enabling such an update. Application recovery never
calls the case-removal flow. [Installer recovery constraints](0018-windows-installer-and-release-identity.md#removal-reinstall-and-recovery).

## Alternatives and qualification

Transport-only trust and operator-supplied hashes alone cannot authenticate publisher origin.
Incremental component downloads add a second code activation and audit path, so the proposal
uses a whole installer and its inventory. An established update framework remains an option
if PaperWorkShell enables an online repository. That decision would review the framework and
runtime closure under PWS-D034. The scoped engineering verifier establishes a local trust
check independently of the final distribution transport.

The installed recovery transaction, production key and publisher policy, endpoint and
transport, persistence, consent UI and component audit trail require later engineering
evidence. The [security test plan](../security/security-test-plan.md#pws-0803-update-verification)
separates the local verifier from installed and release checks. Public claims require the
exact production-signed Windows 11 x64 matrix and release evidence. PaperWorkShell controls
its own update channel, versions, case ownership and migrations. A ThirdLife compatibility
claim requires the future B4 compatibility cut for the exact release or range.
