# ADR 0016 — Local assisted-case consent, locking, and expiry

Status: Implemented task-local policy for PWS-0705; no frozen decision change.

## Context and choice

PWS-D028 and PWS-D031 require local, isolated assistance without organizational
accounts. PWS-0705 needs a specific local unlock workflow and explicit retention
semantics before import. This task chooses a case passphrase, five-minute idle
lock, and one/seven/thirty-day fixed UTC expiry. Seven days is the initial choice;
consent remains unchecked. Expiry quarantines the case for document work. Files
remain until explicit removal, as explained before consent. Automatic deletion is
not inferred from expiry. PWS-0706 separately owns verified-export deletion.

The versioned consent record lives in the authenticated case journal, independently
of the released document-manifest schema. It records closed policy values and a
randomly salted scrypt verifier, never a consent note or plaintext passphrase.
Its fixed scrypt parameters belong to record version 1. Unknown versions fail
before mutation. No dependency, executable, runtime RPC, or account is introduced.

## Access and interruption

The application gate runs before opening an assisted manifest and before new
document operations. Restart has no remembered grant. A content-free GUI curtain
hides the workflow, menus, and owned document dialogs immediately; a thread-safe
revocation gate does not wait for storage or passphrase work. Queued snapshots
cannot unlock it. Unlock requires a matching request and a still-current local
grant. Already queued edits may save while locked, and cleanup/shutdown remain
available. Existing cooperative cancellation and resource limits remain in force.

Idle uses a monotonic clock. UTC high-water checkpoints and sticky expiry prevent
an observed clock rollback from extending a deadline. An unobservable clock change
while the application is stopped cannot be proven by an offline application; the
local Windows clock remains the time authority. Clock review and cooldown errors
provide actionable recovery on the visible lock surface.

## Alternatives and limits

Windows workstation locking alone would affect other applications and would not
provide case-specific handover. Organization accounts would violate the task's
local scope. Encrypting all stored case data would require a separate key-management
and recovery design. The chosen lock limits ordinary in-app access; it does not
encrypt files, erase process memory, or defend against an administrator or another
person controlling the same Windows account. Users should sign out of Windows when
leaving a shared computer. A lost passphrase has no recovery bypass.

## Effects and evidence

Original documents and exports remain independent and unchanged. Atomic policy
publication uses existing private Windows handles and workspace quotas. This adds
no sibling dependency or portfolio interface; packaged location and uninstall facts
remain owned by M8. See the [architecture](../architecture/assisted-use.md) and
[task result](../development/pws0705-result.md) for verification and limits.
