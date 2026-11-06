# Assisted-use consent, locking, and expiry

Local assistance needs no accounts/server roles; ordinary cases remain independent. [ADR 0016](../adr/0016-assisted-use-local-policy.md) owns policy; the [result](../development/pws0705-result.md) and [audit](../accessibility/pws-0705-assisted-use-audit.md) own verification and limits.

Domain validates records/snapshots/errors; the application coordinates guarded actions. Windows storage owns protected handles/atomic writes; the backend serializes disk/scrypt. Qt uses safe snapshots; the worker parses/renders documents.

Unchecked consent before import explains immutable copies, independent exports, expiry and locking. Retention is one/seven/thirty days, initially seven. Matching masked fields accept 12–128 characters; wrapping labels/buddies and keyboard order support enlarged text. Private `journal/assisted-use-v1.json` stores closed versioned policy, timestamps, salt and bounded scrypt verifier, without plaintext secret/free-text consent. Quota applies. The document manifest owns sources, page intent and export history. Invalid/future records reject document work.

Explicit inspection selects an isolated locked context, not an access grant. Checkpoint failures stay locked; unrelated cases cannot replace the context. Successful release clears selection. Checks validate IDs and inspect only current context; stale requests after release cannot reselect or revoke another case.

Foreground loss, restart/reopen and five minutes of monotonic inactivity hide workflow, menus and owned content dialogs. Curtain/recovery remain available; Escape/shortcuts cannot reveal content. All seven assisted choosers use Qt ownership with overwrite/directory options. No picker is exempt from inactivity locking.

The privacy epoch revokes access without waiting for storage/scrypt. One cancellable unlock request requires correlated completion. Cancel, relock, stale reply, expiry or clock rollback cannot grant access. Completion speaks existing opened-page count/recovery text only if accepted state, current grant, epoch and deadline agree. Stale typed checklist lock refusals clear their request without misleading unlocked recovery; other failures remain sanitized and visible.

Only `assisted_check_finished` releases the single scheduler slot; its backend `finally` covers invalid/failure paths. Generic snapshots/unlock replies cannot acknowledge checks. Close holds pending state until `case_close_finished`: timers stop, active work cancels cooperatively, and preview/autosave/retry/document commands are guarded. Manual Lock remains keyboard-accessible. Completion restores chooser/error recovery without stealing handover-modal focus; retention-close failure publishes locked state. Internal completion signals add no RPC or executor.

UTC high-water observations prevent observed rollback extending retention; expiry is sticky quarantine, never deletion. Windows is offline time authority; unobserved stopped-app clock changes cannot be proved. PWS-0706 owns deletion after verified export. Handover exposes only internal reference, fixed expiry and local instructions; exports remain independent.

Forgotten secrets have no bypass. This lock cannot encrypt files, erase memory, promise forensic erasure or defeat control of the same Windows account. Sign out of Windows. Case data stays PaperWorkShell-owned: no sibling/ThirdLife service, adapter or shared schema. Release docs own packaged locations/uninstall facts.
