# PWS-0807 unsigned candidate continuation

## 2026-11-26 owner correction

[PWS-D044](../../DECISIONS.md#pws-d044--v10-product-completion-accepts-an-unsigned-candidate)
allows the project to complete on an exact unsigned Windows 11 x64 candidate.
The local continuation starts from `bd8e3e7d91900e1149e6a3af9e81ad50542b06de`,
which already contains the picker fix `e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec`,
PWS-0814 setup, authenticated offline update evidence and the installed R1
diagnostics save and overwrite-refusal observations. The primary checkout holds
unpublished work. The isolated `codex/pws-0807-unsigned-completion` worktree
preserves that checkout and the earlier worktrees.

## Affected installed follow-up

The [R1 diagnostics record](pws0807-diagnostics-investigation.md#packaged-verification-on-unsigned-r1)
covers preview, a successful save, privacy inspection, an occupied destination
and preservation. The remaining question is whether the exact shipped picker
dismisses cleanly on Cancel and returns to review safely after a genuine storage
refusal at a freshly selected path. The materially different conditions are a
native picker cancellation and a disposable synthetic destination held against
file creation. A pass closes those affected installed assertions. A failure
directs investigation to the picker cancellation or the publication error
handling and leaves the affected row open. Use the existing offline PWS-Test
standard-user desktop and R1 artifact. Keep caller-visible UI, candidate
identity and preservation checks in the same observation.

The lab currently has TRACE B8 installed at the retained
`PWS0807-before-packaged-diagnostics-r1-20261126` checkpoint. Use Keep cases
before starting prepared R1 setup. Verify whether the PWS-0814 setup entry
accepts the R1 receipt and exact payload after this actual replacement. Clean
machine installation and exact final-candidate qualification are separate
matrix conditions. Preserve the existing VM checkpoints and evidence.

## Prepared R1 replacement

The retained R1 installer is 513,022,419 bytes and Authenticode reports
`NotSigned`. Its SHA-256 is
`a0d809322ea2c29066d26134c649f9ee1a883f4268403ea9c4e2d9e92bf8aaf8`.
The installer receipt and resolved inventory rehashed to the recorded
`875894759adf79cd580c69a81018ca06c8a734e52f0fbd1754d2ec51bbd3220f` and
`8a9e3202d479c11e4e678adb0c25e31eab1cd1718f036187e500a7dc44566420`.
These are the existing bytes built from the picker-fix commit.

On the disconnected Windows 11 Pro x64 guest, build 26300.9711, the normal
uninstall review used **Continue application removal with case data
preserved**. The original B8 installation was removed, and all 353 baseline
case files matched before the successor was installed. The prepared
[setup launcher](../../packaging/README.md#automated-per-user-setup) from
PWS-0814 used the R1 receipt, inventory, and matching reviewed Microsoft terms.
The standard-user setup reported `operation=install`, native exit 0, and
3,219 verified payload files. It started the normal installed launcher and
identified its responsive desktop in session 5. The installed marker SHA-256
was `c01268636ea37745e065516c82f892fc40fae418fdafaeae22cf814599bf6736`.
The setup receipt has SHA-256
`8fa7866fdc92ea88344db5521dcf3c0c7f7668fd7e9a2ed20479ff3c0e4b5da8`.
This is an observed B8-to-R1 replacement with cases preserved on one x64
environment. Clean-machine and same-build repair evidence retain their own
starting states and scope.

Receipts are retained under `output/pws0807/unsigned-completion-proof` in the
primary repository. The install and diagnostics use the existing offline guest
and its unchanged protection settings.

Windows refused file creation in the denied scratch folder in the native
picker and offered to save elsewhere. Decline the redirect. To observe the
application publisher separately, submit a new filename of 181 ASCII
characters in the same writable scratch directory. Windows permits this
length, while the publisher allows up to 180 UTF-16 code units for the final
filename. An actionable app error with no file or stage, followed by a
successful save of the same preview at a supported name, proves recovery from
a rejected destination. An unexpected archive or unrecoverable error keeps
that assertion open.

## Observed diagnostics outcome

The exact R1 desktop opened the support preview with a one-page synthetic case
and required consent. Native Cancel returned to that preview. The named
canceled destination did not exist. The native picker showed a permission
error for the scratch folder that denied file creation, with no file
left there. After dismissing that refusal, the Windows picker accepted the
longer name and PaperWorkShell reported that the support bundle could not be
saved safely and offered a different location. Neither the unsupported name
nor a temporary publication stage remained.

The same preview saved at a supported name and the app reported a successful
local save. The reopened 1,567-byte ZIP matched the preview SHA-256
`f8599db42729a3541464f41496960c2b4af95fc96007f0f3d7f47fe07fa63e8e`.
CRC validation passed and the exact `summary.txt` and `allowlist.txt` members
matched the reviewed sizes and hashes. The summary omitted the checked
synthetic path, account, case and source-name tokens. The existing ZIP
sentinel and original PDF kept their SHA-256 values. The post-recovery
comparison retained all 353 prior case files and all five new case files
byte-for-byte from the cancellation observation. The earlier R1 overwrite
attempt remains in the original record. This follow-up adds cancellation,
native permission refusal, application destination refusal and exact-preview
retry evidence for R1.

The archive receipt SHA-256 is
`a3bba21029721b9c62828aa5daddcf01dd0af73f13069f8a609f057324f48ef0`.

## Same-build reuse and reopen

The next matrix question is whether the prepared launcher can reuse the
same installed R1 payload after a normal app exit and reopen the saved
synthetic case in a new installed process. This uses the new R1 installation
and case created during diagnostics. A pass adds exact R1 startup, reuse and
case-reopen evidence. A mismatch in the package or saved state keeps the
affected assertion open and determines whether to inspect setup or recovery.

After normal exit, the prepared launcher reverified the same 3,219 installed
files and returned `operation=reuse` without invoking the installer. It opened
a fresh exact R1 desktop. Keyboard navigation selected the existing synthetic
case, and **Open case** reopened it with one active page, all changes saved and
a completed preview update. All 358 case files matched the previous snapshot,
including the 353 pre-existing files. The reuse receipt SHA-256 is
`86892a4d01cf3bcf656401c8ffaf533dd15410d0b4d6e755028d409f17838666`. This
evidence covers reuse and reopening. The PWS-0814 same-build repair evidence
continues to describe B8.

## Guest state retained

PWS-Test remains offline with exact R1 installed. The active checkpoint parent
is `PWS0807-before-packaged-diagnostics-r1-20261126`. The application was closed
normally with zero owned processes or scheduled jobs remaining. The synthetic
case and setup inputs are available for related checks. The automation
runner's command policy rejected deletion of the empty permission-test folder.
Its temporary deny rule was removed and the folder remains empty. The retained
VM checkpoints and host artifacts still have their original identities.

## PWS-0802 clean per-user installation and launch

On 2026-11-27, the clean-install check passed for
`pws0807-diagnostics-engineering-r1` from
`e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec`. The exact 513,022,419-byte
installer, receipt, and inventory identified by
`output/pws0807/packaged-r1/qualified-build-handoff.json` matched their hashes
on the host and in the guest. Authenticode reported `NotSigned` in both.
The prepared setup launcher used the matching reviewed Microsoft runtime terms.

The live R1 marker and all 358 case files matched the previous evidence before
the VM was checkpointed. The restored
`Clean-offline-before-PWS-with-passwords` checkpoint
(`c3e64686-7b32-4247-b5bd-896c70f030d2`) ran Windows 11 Pro x64 build
26300.9711. The Hyper-V adapter remained disconnected and the guest had zero
up adapters. Smart App Control was Off. Defender real-time protection,
firewall, UAC, Secure Boot, and vTPM were enabled. The checked locations had
no PaperWorkShell install, registration, shortcuts, or app and case data.
The checked development commands, registrations, and paths were empty apart
from inert Python Store aliases. The limited interactive token in session 5
had no Administrators SID or linked token and used elevation type 1. The
first observer invocation exited 1 without a receipt. Running the same
observer as an inbox PowerShell command produced the baseline receipt before
setup, with Windows protection settings unchanged.

Setup reported `operation=install`, native installer exit 0, no restart
request, and 3,219 verified payload files. It installed under
`%LOCALAPPDATA%\Programs\PaperWorkShell` with the per-user uninstall
registration and Start menu shortcut. The checked machine-wide registration,
program locations, and common shortcut were absent. In session 5, both the
setup-started desktop and a new process opened through the installed
**PaperWorkShell** Start menu shortcut showed a responsive foreground window
under the verified standard-user token and closed normally with exit 0.
The check created no case files and ended with zero owned processes or tasks
on the clean test branch.

The host receipts are in `output/pws0807/clean-r1-install-proof`.
`clean-setup.json` has SHA-256
`3ab218d3f1d5c6cbfa361535fdda95b85041c0ca7af2522ceec2c5487064d88b`.
`clean-installed-final.json` binds the interactive baseline, setup, desktop
inspection, and shortcut receipts with SHA-256
`def5577c19b8c88339c982ccfa2e22161edee639ebb4e42362bb44eb69d54784`.
The tested install is retained as
`PWS0802-clean-diagnostics-R1-installed-20261127`
(`974e7fb6-1cd1-4bbf-abfe-130da4cacd72`). The live VM was returned to
`PWS0802-preserve-active-diagnostics-R1-20261127`
(`c1d7e179-9827-4bdf-9d57-6efa56f7034c`). The R1 marker and all 358
case files matched again, the VM remained offline, and all earlier checkpoints
were retained. The final state receipt SHA-256 is
`18cf8d4fd0e1b12f648143c18b9a1bb5222eaf08eab906756060fbb4e9730dff`.

This covers clean per-user installation and ordinary launch of exact R1 on
one Windows 11 Pro x64 guest with the observed execution policy. The earlier
B8 replacement and R1 reuse observations retain their own starting states.
Document workflows, same-build repair, uninstall, actual installer-target disk
exhaustion, broader Windows coverage, and product acceptance remain separate.

## PWS-0802 installer provenance and repair

The independent R1 audit verified the unsigned installer, 3,219 staged files
(650,881,153 bytes), 178 build inputs and 345 application source files. The
component report, recipient notices and CycloneDX and SPDX SBOMs reproduced
byte-for-byte. All 956 third-party component records and their 2,857 payload
files matched the audited R9 records. The Microsoft runtime check covered five
objects at nine destinations, and the compiler check verified the pinned
upstream signer. The audit receipt is
`output/pws0807/r1-installer-artifact-audit/result.json`, SHA-256
`17e31531b63d33ef0801e489d0babbe0e384b0be8c9f8af486e05728e2a87e6c`.
[R1 installer and component evidence](../release/RELEASE_EVIDENCE.md#diagnostics-r1-installer)

On the offline Pro x64 guest, the standard-user installer found and restored a
deliberately missing Microsoft recipient-terms file. Native repair exited 0,
verified all 3,219 files and relaunched the desktop. The installed Notices
shortcut opened the component and licence viewer. The installation root,
runtime, application data and case root had no write grants for the checked
Everyone, Authenticated Users or local Users groups. The engineering package
has no configured update anchor.

The first preserve/reinstall trial copied a synthetic case into the guest.
All five file hashes survived removal and reinstallation, but the application
reported `RECOVERY_SCAN_FAILED` when inspecting the copy. The file comparison
established preservation only. The next trial used **New case** and **Add
file** in the installed desktop to create a one-page case from the external
synthetic PDF. Same-build repair then exited 0 and reopened that case with one
active page and all changes saved. The host receipts for both trials are under
`output/pws0807/r1-installer-completion`.

The 328 focused installer, setup, removal, update and launcher tests passed.
The reviewed native toolchain in the candidate build worktree supplied the 17
native launcher tests that initially skipped in the continuation worktree.
The historical R6 controlled interruption and authenticated same-build repair
retain their original scope. Those observations and the R1 lifecycle-lease
tests are indexed in `r1-installer-artifact-audit/lifecycle-and-recovery.json`
under `output/pws0807` (SHA-256
`698a3e212a75bbed51940cb9cd9a10645fe6689ff35c4138af4e7be081a599ad`).

## PWS-0802 uninstall and reinstall

The preserve uninstall exited 0. The per-user installation directory,
registration, and all three Start menu shortcuts were absent. The five
managed files in the application-created case retained their paths, sizes,
and SHA-256 values. After reinstall verified all 3,219 payload files, the
standard user reopened the same saved page through **Open case**.

The interactive uninstall review then identified one available case and zero
unavailable cases. After a separate confirmation, the application reported one
fully deleted case, zero remaining, and zero failed or partial attempts.
Native uninstall exited 0. The managed case directory and its files were gone.
The external source PDF, exported PDF, and support ZIP retained their three
original hashes through both removals. The four observed file defaults for
PDF, JPG, JPEG, and PNG matched their starting values. The zero-byte profile
lifecycle lock remained. The install, repair, and removal operations requested
no restart in this guest.

Setup refused a one-byte change to a copy of the installer and a mismatched
inventory with `PWS_SETUP_ARTIFACT`. An alternate-stream path returned
`PWS_SETUP_ENVIRONMENT`. All three returned exit 1 before an install operation.
The native installer also rejected a destination override with exit 1 and
left the requested destination absent. The final observation confirmed that the
application remained removed and the external hashes and file defaults matched.

The host summary `output/pws0807/r1-installer-completion/lifecycle-result.json`
has SHA-256 `c39eda3f8562428e81121f07479e9c149247281879f8c3205f837861e3ea1d71`.
The VM was returned to the saved active diagnostics state, checkpoint
`999a5747-66e2-4565-baee-5e619dce8656`. Its marker and all 358 case files
matched. The offline test ended with zero owned guest tasks or application
processes. The results apply to R1 on the documented Pro x64 configuration.

## Next operation

The remaining PWS-0802 operation is real bounded exhaustion of the ordinary
installer target. The release-candidate matrix requires a separately authorized
disposable target. The prepared route applies an NTFS quota to PWSTest on
guest C: on a disposable branch of `PWS0802-R1-removed-product-case-20261127`
(`1621732b-33d6-4719-ab0f-d99fcc0e968c`). The starting state is the
verified preserve uninstall with the application-created case and three external
files. The helpers and negative host-identity control are in
`output/pws0807/r1-installer-exhaustion`. The proposed test covers refusal
before installation, exhaustion while writing the payload, recovery after the
quota is lifted, and data preservation. Host preparation and volume capability
are recorded. The guest quota operation awaits maintainer authorization.
