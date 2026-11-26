# ADR 0022 — Authenticated PaperWorkShell updates

- Status: Implemented for offline update engineering. Production channel activation is optional under PWS-D044.
- Date: 2026-11-26
- Task: PWS-0803
- Decisions preserved: PWS-D005, PWS-D013, PWS-D027, PWS-D034, PWS-D042, PWS-D043

## Installed baseline

PaperWorkShell uses per-user Inno Setup, application ID `io.github.pikkujanne.paperworkshell`,
and upgrade ID `{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}`. The installer checks its own fixed
layout and continues to refuse a different candidate or an existing unmarked installation
folder. The original installed launcher, baseline runtime, Start menu entries, and uninstall
registration stay at their fixed locations when applying a runtime update. An older build
that lacks the update-capable launcher requires regular installation through the documented
case-preserving replacement procedure. [Installer identity and recovery policy](0018-windows-installer-and-release-identity.md).

TRACE B8's prepared setup proved same-build repair, reuse, and an offline ordinary PDF
workflow as a standard user against exact unsigned engineering bytes. That work supplied
neither an upgrade result nor release trust. [TRACE B8 setup evidence](../development/pws0814-automated-setup.md).

## Offline package and trust authority

The **PaperWorkShell Updates** Start menu entry opens a separate window from the baseline
runtime. The user selects a local folder holding `trust.json`, `trust.sig`, `release.json`,
`release.sig`, and `runtime.zip`. The window displays the signed offer and current installed
version before explicit review and installation. The update uses a fixed local Windows 11 x64
folder and requires no network. It has no case or document processing route, rejects package
folders inside private PaperWorkShell storage, and never launches a document worker. The
worker network-denial policy for ordinary document processing stays in effect.

A reviewed regular installer can provision an independent, channel-specific public root in
the baseline runtime. The build verifies the supplied SHA-256 and records the exact public
input and its installation destination in the resolved inventory. The versioned anchor
contains the channel, P-256 public coordinates, a minimum trust epoch, and the baseline's
release sequence floor. An installation needs a provisioned anchor to accept an offer. A
runtime package cannot change the installed root. Root replacement uses a separately
approved and verified regular installer. That approval concerns admission of the update trust
root. Under PWS-D044, the supported local installer path and product completion can use an
unsigned PaperWorkShell installer. A selected installer checksum identifies known bytes.
An installed update anchor is a distinct mechanism that authenticates offered update metadata.
[Windows installer identity](0018-windows-installer-and-release-identity.md#signing-and-exact-output-identity).

The update verifier uses Windows CNG ECDSA P-256 with SHA-256. Public points and detached
signatures use fixed 64-byte big-endian coordinate and scalar pairs. The root authorizes a
separate release-metadata key or a channel revocation in a versioned trust record. The root
signs the `PaperWorkShell update trust v1` domain separator, one zero byte, and the exact UTF-8
record. The delegated key signs the `PaperWorkShell update metadata v1` domain separator,
one zero byte, and the exact UTF-8 release record. The delegated key cannot authorize its own
successor. The Windows verifier requires no additional cryptographic distribution.
[Windows CNG](https://learn.microsoft.com/en-us/windows/win32/seccng/cng-features).

The verified trust epoch, record digest and release floor are retained locally even when a
verified revocation or an offered update stops further processing. A higher authenticated
trust epoch can rotate the delegated key. An old epoch or conflicting record at the same
epoch is refused. Release metadata binds the PaperWorkShell application ID, `win-x64` target,
channel, version, candidate ID, monotonically increasing sequence, UTC validity window, and
exact ZIP size and SHA-256. The offered sequence must exceed the recorded baseline, trust and
installed floors. Metadata before its issue time or at or after expiry is rejected. A machine
that cannot verify freshness can continue ordinary document work. Withholding a bundle can
prevent clients from learning about new releases or revocations. [Emergency key recovery policy](../development/emergency-security-updates.md#5-handle-compromised-trust-material-explicitly).

## Activation and recovery

The native launcher and update-capable installer hold an exclusive local file lease for this
user across Windows sessions. The launcher passes the lease to the update window under its
controlled process start. Normal launch, update, installer and removal operations use the same
lease. Close PaperWorkShell before opening the updates window. The lease is under
`%LOCALAPPDATA%`, while update state and payloads are owned by the fixed application root under
`%LOCALAPPDATA%\Programs\PaperWorkShell`.

The controller copies the offered ZIP into private fixed-volume staging and verifies its exact
signed size and SHA-256. It extracts into an unused application slot among A, B and C, preserving
the active and retained previous builds. The versioned payload manifest binds every relative
file path, size, SHA-256 and component owner to the original resolved inventory digest and
records component versions. The archive carries the complete runtime and its packaged notices,
sources and evidence. Linked, redirected, duplicate, missing, extra, oversized, or mismatched
members fail admission. The signed identity must match the payload manifest and staged runtime
marker. [Windows packaging](../../packaging/README.md#authenticated-offline-updates).

Only updates declaring the current case-manifest schema and carrying exactly the same Microsoft
runtime recipient terms as the baseline can activate through this route. Use the regular installer
to review different terms. Case-schema transitions need their own migration and recovery
qualification. Before committing an update the controller checks the staged runtime through a
bounded installed-startup probe that opens no cases, then rechecks the trust and offer validity.
It flushes and replaces one activation record containing the selected slot, previous slot,
sequence floor and a bounded history of accepted releases with manifest and inventory digests.
The stable launcher selects only the baseline or a fixed slot. Windows restarts and arbitrary
power-loss durability require separate qualification.

Failure before activation leaves the prior selection in place. If the active build later cannot
launch, **PaperWorkShell Updates** remains available from the baseline. **Restore previous
version** verifies and selects the retained build or the original installation, while retaining
the accepted release sequence floor. **Remove installed updates** returns to the original
installation and removes the update slots, while keeping the floor. Uninstall requires removal
of the update slots before the ordinary explicit case-data choice applies. These update
recovery actions leave case data separate. An interrupted or unselected slot requires a
successful activation record to become a launch target.

## Qualification and scope

The [security test plan](../security/security-test-plan.md#pws-0803-update-verification)
identifies local admission, installed update, recovery and accessibility checks. Engineering
trust established by isolated test keys applies only to the recorded installed test artifacts.
Production channels require separately qualified trust and distribution. Keep them disabled
without that qualification. The former requirement for trusted bootstrap publisher signing
was superseded on 2026-11-26 by PWS-D044. The supported local per-user installer route
covers repair and case-preserving cross-build replacement with their own evidence.

The update protocol adds no automatic check, background task, remote endpoint, separate
component self-updater or ThirdLife dependency. PaperWorkShell owns its channel, versions,
case data and migrations. A ThirdLife compatibility claim requires a future B4 cut for
the exact release or range.
