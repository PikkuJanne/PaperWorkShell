# PaperWorkShell security and vulnerability reporting

PaperWorkShell is in engineering development. No public release is currently designated for
support. Each internal build has the support scope in its handover. [Release status and known limits](KNOWN_LIMITATIONS.md#release-and-acceptance-status).

## Report privately

If you received an internal build, contact the maintainer who supplied it through the private
channel you already use for that build. Keep suspected vulnerabilities out of public issues,
discussions and shared folders while the maintainer investigates. The private route for public
releases is **TBD**. [Recovery and ordinary support](docs/user/CARE_AND_RECOVERY.md).

Start with a short description of what went wrong and why it matters. Include the build or
version from your handover or installer filename, the Windows 11 edition and build if known, which
action you took, and any sanitized error code. Describe the expected result and what you observed.
If you can reproduce the issue safely using a synthetic document, describe those steps. Leave out
any personal information, usernames, file paths, case files, real documents, screenshots showing
documents, OCR text, signature images, routine logs, passwords and keys.

You can send the description on its own. If more detail is needed, **Settings → Create support
bundle…** lets you preview the exact files and save a local ZIP. Review it and share it only
through the private route if you choose. Creating it sends nothing. [Support bundle guide](docs/user/CARE_AND_RECOVERY.md).

## How reports are handled

1. The maintainer checks which build and component are affected, assesses the impact, and asks for
   sanitized clarification if needed. Synthetic files are used for reproduction and regression tests.
2. The maintainer determines whether an available mitigation or an update is appropriate, verifies
   the fix against the affected workflow, and checks how it affects installation and recovery.
3. The maintainer coordinates disclosure with the reporter as appropriate. Release guidance identifies
   the affected and fixed versions, verification method, available mitigations, and any remaining limits.
   Response and fix times depend on the issue and the available evidence.

Dependency incidents also follow the [emergency security update procedure](docs/development/emergency-security-updates.md).
The [release interface](docs/release/RELEASE_INTERFACE.md) and [evidence index](docs/release/RELEASE_EVIDENCE.md)
will carry the support and update details for the exact qualified release.

## Local protection and updates

PaperWorkShell processes documents locally in a restricted worker, preserves imported originals,
and verifies new outputs before reporting export success. A user can run authenticated offline
updates independently of document processing where the installation has provisioned trust. The
engineering update proof used isolated test signing. Availability, production trust, and supported
versions require exact release evidence. [Authenticated update record](docs/development/pws0803-authenticated-updates.md).

PaperWorkShell owns its update process and migrations and versions independently. The user
controls their local cases and files. PaperWorkShell has no current ThirdLife app-to-app
integration or compatibility claim. [Portfolio boundary](THIRDLIFE_ALIGNMENT.md).
