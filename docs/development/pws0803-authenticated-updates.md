# PWS-0803 — authenticated updates

## 2026-11-26 engineering checkpoint

Work started from PWS-0814 handoff `224e51e400522486d0efb6340e2466a52eb9a5f8` in linked
worktree `output/pws0803/update-worktree`, branch `codex/pws-0803-authenticated-updates`.
PWS-0005 and PWS-0810 are complete. The installed reference is TRACE B8, source
`acc0b2621259975c6d418807488ce239b338ac05`. The frozen unsigned installer is
`PaperWorkShell-pws0813-trace-engineering-b8-win-x64-UNSIGNED.exe`, 512,966,979 bytes,
SHA-256 `6b2dce059dc2ef2f02e8fc5206e5aacf285af472dfd281d7785881c9c809d33e`.
The [PWS-0814 record](pws0814-automated-setup.md) contains the earlier standard-user repair,
reuse and offline workflow evidence for that exact build.

The [proposed ADR](../adr/0022-authenticated-application-updates.md) describes independently
trusted local update verification, user consent and a recoverable installer handoff. The
first increment verifies a detached test signature over a bounded versioned record, checks
the product, platform, channel, validity window and release sequence, and streams the exact
package through signed size and SHA-256 checks. The caller supplies the trusted key verifier,
UTC time and sequence floor. No production trust root is configured. The only use of the
pinned Ed25519 implementation here is in security tests, which generate ephemeral keys.
The original installer remains the reference for fixed identity and different-candidate refusal.

Verification in the linked worktree used the offline locked development environment. The 26
new security cases exercise valid and wrong keys, exact signed bytes, input bounds, malformed
records, wrong target, expiration, replay, short reads, tampered packages and interrupted
reads. Adjacent installer/signing suites cover the existing controls. The installed-runtime
suite skipped one existing synthetic symlink case because the host could not create one.
The combined focused security, unit and integration run passed 188 tests. The registered
format, lint, strict typing, import-boundary, schema, task-graph, documentation and
portfolio checks passed. See `tests/security/test_update_verification_security.py` for the
direct, offline reproduction.

The update mechanism still needs authenticated staging, an update transaction, rollback
verification, trusted sequence persistence, consent and a component audit trail. The
installed marker and static release documentation have no new update capability claim.
No case format, process privilege, worker network policy, or ThirdLife dependency changed.

## Next step

PWS-0803. Start from the verified local bundle and prototype the Windows handoff without
executing an installer. Use an isolated test directory and ephemeral signing keys to prove
that the proposed handoff keeps the authenticated bytes bound to the staged file and refuses
swaps, redirection and interrupted staging. Keep the existing different-candidate installer
guard and current installation available. Record the observed result before proceeding to
the installed replacement and recovery transaction.
