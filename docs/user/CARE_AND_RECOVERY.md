# Care, recovery, and support

Draft guidance for the Windows 11 x64 PaperWorkShell workflow. Use the instructions
for the build you received. Public release support, exact Windows editions and
builds, and release-specific removal evidence are recorded in the draft
[release interface](../release/RELEASE_INTERFACE.md).

PaperWorkShell works offline under your Windows account and versions independently.
You control your local source files, cases, and exports. ThirdLife and other
applications have no role in case access, migration, or support. No ThirdLife
app-to-app integration or compatibility cut has been established.

## Help someone with their documents

1. From the case chooser, select **Start assisted case**. Before importing anything,
   ask the person you are helping to agree to local storage, expiry, and locking.
   Explain that PaperWorkShell copies the source files into private case storage.
   The source files stay unchanged. Any exported files stay in the chosen folders
   after closing or deleting the case.
2. Use a neutral case name because it appears in the chooser before unlocking.
   Set **Case expires after** to one, seven, or thirty days from consent. Seven
   days is the initial choice. Enter and repeat a local passphrase of 12 to 128
   characters. Keep it with the person who will need access.
3. Select the consent checkbox and **Start assisted case**. To leave without
   creating a case, cancel. Import only what the person agreed to share locally.
4. Choose **Lock assisted case** when stepping away. The case also locks when the
   app loses focus, after five minutes without activity, and on reopening. Choose
   **Unlock assisted case**, enter the passphrase, and choose **Unlock** to return.
   Cancel leaves the case locked. On a shared computer, sign out of Windows before
   leaving. The app lock hides content on screen. File protection depends on the
   Windows account and its access controls.
5. If the lock screen shows **Assisted case expired**, the case stays locked and
   its files remain until explicit removal. A forgotten passphrase has no recovery
   path. If the case needs clock review, correct the Windows clock and try to unlock
   before expiry. **End assistance** closes PaperWorkShell without opening other
   cases or deleting files. Allow queued changes to finish before signing out.

For a planned handover, choose **Close case** while the case is open. The handover
summary shows the local case reference and expiry. Keep the passphrase private and
hand over only the exports the person wants to keep. If you intend to delete only
this case after export, complete the separate deletion review before expiry.

Use **Tab** and **Shift+Tab** to move between controls, **Space** to toggle a
checkbox, arrow keys to review a focused list or read-only text, and **Enter** to
activate a focused button. Press **F1** or open **Settings → Keyboard guide**.
For adding files and completing the packet, see the [user guide](USER_GUIDE.md).
For the exact lock and handover behavior, see the [assisted-use contract](../architecture/assisted-use.md).

## Keep or delete a case after export

Save and open the exported files before giving up the case. Save the optional
summary or text companion separately if you need it. Review these files before
sharing them. They can contain personal information.

1. After a verified checklist package export, choose **Review case deletion** when
   offered. Read the inventory and the list of exports that will remain.
2. Choose **Keep case** to leave the case available for later use. To remove it,
   acknowledge the consequences and choose **Delete case**.
3. Wait for the result. If the export changed or is missing, deletion is blocked.
   Export again to a fresh destination outside managed case storage and repeat
   the review. If removal is partial, keep the verified external package and follow
   the reported recovery guidance. The app reports incomplete removal and does not
   automatically retry partial deletion.

Case removal leaves separately saved exports, summaries, text companions,
support bundles, and source files in their original locations. Other copies and
backups may remain. Case deletion cannot be undone through PaperWorkShell.
[Post-export retention and failure behavior](../architecture/post-export-retention.md).

## Recover after an interruption

- If changes have not saved, choose **Retry saving changes** when the option appears.
  Use **Close case** and reopen the case if the saved state needs reconciliation.
  Wait for pending saves to finish before closing the app.
- After a crash or interruption, launch PaperWorkShell normally and read its startup
  recovery message. Use **Open case** to check the last available saved order and
  edits. When a case needs attention or is unavailable, leave its files in place
  and record the message. The app preserves uncertain work files for review.
- If you are unsure whether an interrupted export was saved, check the destination
  you originally selected. Open any file found there and check what it contains.
  Startup recovery repairs internal records where it has sufficient evidence.
  For a fresh export, pick a new filename or destination and keep any existing file
  until its contents have been checked.
- Opening a supported older case upgrades its private case records and retains the
  exact old manifest in the same case. Its imported source files and saved editing
  intent carry forward. If the app refuses an unsupported or damaged case, leave
  the records intact. Use a compatible PaperWorkShell build or report the problem.
  An older manifest is private recovery evidence and automatic downgrade is unsupported.

See the [startup recovery](../architecture/startup-recovery-and-diagnostics.md) and
[case migration](../architecture/case-migration.md) contracts.

## Create a support bundle

1. In the main window, open **Settings → Create support bundle…**. This opens a preview.
2. Read the two file names and their complete contents. `summary.txt` includes
   approved status codes, counts and software versions when available.
   `allowlist.txt` explains the included and excluded fields. The preview also
   shows sizes and hashes.
3. If you choose to save a copy, tick **I reviewed these exact files and choose to
   save them locally** and choose **Choose location and save**. Pick a new ZIP
   filename on a supported local drive. **Close without saving** leaves no ZIP.
   The app saves the reviewed ZIP to your selected folder and sends nothing.

When asking for help, start with the build, the action, the visible result, and
steps using made-up example files. Keep real documents, screenshots with content,
case folders, migration records, ordinary logs, and passphrases private. Share
only the reviewed support ZIP if appropriate through an agreed private route.
Keep or delete your local copy when you are finished.
[Support bundle contents and behavior](../architecture/support-bundle.md).
[Security reporting](../../SECURITY.md).

## Install, repair, update, or remove the application

Use the installer or prepared setup bundle for the build you received. Run setup
under the Windows account that will use the app. Review the Microsoft runtime
terms when prompted. Open **PaperWorkShell** from Start. If setup asks you to
close the running application, close it normally and wait for pending saves before
retrying. For a matching installation, use the documented same-build repair or
reuse route. For a different fixed-layout build, follow its replacement
instructions and keep cases during removal if you need them afterward.
[Prepared setup and repair](../../packaging/README.md#automated-per-user-setup).

### Apply or recover an authenticated offline update

Use this path on an update-capable installation with a configured update channel.
The engineering proof used an internal installer and isolated test-signed ZIP.
Public update packages and trust provisioning remain subject to public
qualification. [Engineering update verification](../development/pws0803-authenticated-updates.md).

1. Close PaperWorkShell and open **PaperWorkShell Updates** from Start. Select
   **Choose update folder…** and select the local folder supplied for the update.
   Keep its trust records, release records, signatures, and runtime ZIP together.
2. Check the offered version, build, and channel. When the app verifies the
   update information, tick **I reviewed this update** and choose
   **Install update**. During cancellable work, **Cancel operation** requests
   cancellation. Wait for the status before proceeding.
3. When the operation finishes, close the update window and open **PaperWorkShell**
   normally. If the app refuses an update, review the current installation in the
   updates window. Use a newly supplied, authorized update if the original offer
   expired, has already been used, or was superseded.
4. If the updated app cannot launch, open **PaperWorkShell Updates** again. Choose
   **Restore previous version**, confirm, and wait for the result. This returns
   to the retained working version while keeping the accepted update sequence.
   **Remove installed updates** returns to the original installation and removes
   the local update slots. Both paths keep your cases.

### Remove PaperWorkShell

Close the app. If the installation has update slots, use **Remove installed updates**
before uninstalling. From Windows **Settings → Apps → Installed apps**, use
**Uninstall** for PaperWorkShell under the same Windows account. Read the
**Remove PaperWorkShell** review before continuing.

- To preserve cases, choose **Keep cases and continue application removal**.
  A compatible reinstall under the same Windows account can reopen preserved
  cases through **Open case**.
- To request managed-case deletion, select the review checkbox, choose
  **Review case deletion**, and read the inventory. Only after checking what will
  remain, acknowledge the separate deletion confirmation and choose
  **Delete reviewed cases**.
- **Cancel application removal** can stop before the next case if deletion has
  started. Completed deletions cannot be undone. If any case is unavailable,
  unsafe, or partly removed, note the result. **Continue application removal;
  keep remaining data** is available after a deletion attempt. A remaining case
  may require support. Leave unknown files and worker recovery records in place.

Source files, external exports, and external support ZIPs remain. The signed
public candidate needs its own removal and residue verification.
[Installer removal and residue plan](../../packaging/windows/QUALIFICATION_PLAN.md#residue-inventory).

## Where data stays

The paths below use Windows known-folder notation. `<case>` means
`%USERPROFILE%\AppData\Local\PaperWorkShell\Cases\<case-id>\`. `<install>` means
`%LOCALAPPDATA%\Programs\PaperWorkShell\` for the per-user installer.
The table records implemented location classes and intended installer behavior.
Exact public-candidate verification is pending.

| Category | Location and retention | Deletion or uninstall disposition |
|---|---|---|
| External originals | The folders you selected during import. PaperWorkShell keeps these files unchanged. | The person who owns the files manages them independently. Case deletion and uninstall leave them in place. |
| Imported copies and signature image assets | `<case>originals\`, using random internal filenames. Referenced copies are kept with the case. | Keep-cases uninstall preserves them. Confirmed whole-case deletion includes them. Authenticated, unreferenced leftovers may be cleaned during recovery. |
| Case records, editing intent, review, and export history | `<case>manifest.json`, `<case>journal\`, and `<case>export-history.json`. These include page order, transforms, overlays, checklist/constraints, recovery, and assisted-use consent and expiry. Assisted expiry keeps the stored files. | Keep-cases uninstall preserves them. Confirmed whole-case deletion includes them. Some records may remain after partial removal. |
| Migration backups | Version-specific `migration-source-v1.json`, `migration-source-v2.json`, or `migration-source-v3.json` under `<case>journal\`. A backup is retained after a migration and may already exist after an interrupted upgrade. | These follow the case disposition. Temporary-file and cache cleanup preserve the backups. |
| Preview and processing derivatives and caches | `<case>cache\thumbnails\`, `<case>cache\previews\`, and bounded session previews. The app can regenerate previews from case data. Processing intermediates, including OCR where applicable, use the authorized temporary locations below. | Cache or session cleanup can discard disposable files. Files still inside the case follow the case disposition. |
| Case and worker temporary data | `<case>temp\` and Windows AppContainer worker-private temporary storage. Worker access recovery uses `%LOCALAPPDATA%\PaperWorkShell.WorkerAclRecovery.v1`. Normal lifecycle cleanup removes eligible work. Uncertain recovery evidence may be retained. | Case temporary files follow the case disposition if still present. The worker's own shutdown and recovery handle its private storage and access records. Exact public-candidate worker residue remains TBD. |
| Configuration and local install records | Case settings live in the private case records. Packaged runtime configuration is installed with the application. Per-user installation registration and shortcuts are Windows-managed. A separate persistent user-preference store has no established release location. | Case settings follow the case disposition. Removal targets installer-owned runtime, registration, and shortcuts. Confirm any extra configuration or residue against the exact release. |
| Engines, notices, and OCR languages | Bundled engines and notices are below the installed runtime. The English OCR data is under `runtime\engines\tesseract\share\tessdata\` in TRACE B8. Supported separately supplied language data must match the pack for that build. | Installer-owned engine and language files follow their installed inventory. Keep separately supplied files unless their owner chooses removal. Exact release language-pack inclusion and residue remain TBD. |
| Update state, rollback runtime, and supplied bundles | Update state `update-state-v1` and runtime slots `update-slot-A`, `update-slot-B`, and `update-slot-C` belong under `<install>`. The per-user lifecycle lock is `%LOCALAPPDATA%\PaperWorkShell.lifecycle-v1.lock`. The offered folder remains where you placed it. | **Remove installed updates** discards local update slots but retains the installed sequence floor. The ordinary uninstaller targets its managed activation records. Independently supplied update folders remain. Exact public-candidate residue, including the lock file, remains TBD. |
| Routine diagnostics | The desktop support summary uses a bounded in-session record. A default persistent desktop-log location or retention period is unproved. | Session records do not require cleanup by the user. Any separately configured persistent log store needs candidate-specific verification. |
| Recipes and scripts | Saved recipe JSON and exported PowerShell scripts remain at their user-selected locations. A recipe can describe page selection, order, and rotation. | Review and handle these files separately. The application only removes managed data during case deletion or uninstall. |
| Explicit exports and support bundles | PDFs, ZIP packages, summaries, text companions, and support ZIPs stay in their selected external locations. | You choose when to remove these files. Closing or deleting a case and uninstalling the app leave them in place. |

See the [case storage contract](../architecture/case-workspace.md), the
[engineering installer choices](../adr/0018-windows-installer-and-release-identity.md),
and the [release evidence index](../release/RELEASE_EVIDENCE.md).
