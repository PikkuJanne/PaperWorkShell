# Case migration and release-to-release recovery

PWS-0804 completes the current migration boundary without inventing a released
project-transfer format. PaperWorkShell alone interprets private case records.
No sibling application, shared service, remote schema or network request is used.

## Supported records and preservation

The closed [migration registry](../../src/paperworkshell/domain/manifest_migrations.py)
contains only the accepted v1-to-v2 and v2-to-v3 transitions. The existing
[Windows repository](../../src/paperworkshell/storage/manifest_repository.py)
validates the complete frozen source and current result and commits the route
through its existing authenticated compare-and-swap/checkpoint transaction.
One open consumes one persistence revision, including a two-step v1 upgrade.
Current v3 opens consume no revision and create no backup.

Only schema dispatch, the next revision and the empty v3 permanent-removal
collection change. Sources/hashes, page identity/order, transforms and operation
order, populated or empty assets/overlays, groups, assignments, constraints,
confirmations, template reference and export-history references preserve all
values. Ordinary shapes never become permanent-removal marks. Document bytes
are neither parsed nor rewritten by migration.

The [versioned synthetic compatibility index](../../tests/fixtures/manifests/release-compatibility-v1.json)
binds six accepted minimal/representative v1/v2/v3 examples by SHA-256. These are
historical development schema cases, not fabricated public-release artifacts.
Native tests create only synthetic immutable source copies and compare their
bytes across open, failure, interruption and repeated open.

## Exact pre-migration backup

Before publishing the first migration checkpoint, the
[backup adapter](../../src/paperworkshell/storage/manifest_migration_backup.py)
retains the exact validated source-manifest bytes at one fixed case-relative name:

- `journal/migration-source-v1.json` for a settled v1 source;
- `journal/migration-source-v2.json` for a settled v2 source.

At most those two backups exist per case. Each is bounded by the existing 8 MiB
manifest limit. It contains the protected manifest, not copies of original
documents. It remains inside the same private case ACL boundary, counts against
the existing complete-case quota and is removed only with explicit authenticated
whole-case deletion. Disposable cache/temp cleanup leaves it intact. Do not
publish it in ordinary logs, diagnostics, public samples or support bundles.

Creation uses a reserved random temporary file, flush and independent readback,
an authenticated journal-directory handle and a create-only handle-relative
rename. The writer closes before final independent reopen. Folder ACL, path,
identity, stream, sparse-file and hardlink guards follow the existing storage
boundary. An exact existing backup is idempotent; a different, corrupt, linked
or wrongly typed object blocks migration and is never replaced. No fallback
overwrites unknown data or expands a quota.

## Failure and rollback boundary

A future-version current manifest or authoritative checkpoint is rejected before
backup/recovery/migration mutation. Invalid old records also fail before a new
backup or checkpoint. Low-space/quota refusal or backup write failure leaves
the current record unchanged. A failure after backup publication may retain that
exact backup; retry reuses it without replacing its identity.

Before a migration checkpoint commits, the old manifest remains authoritative.
After checkpoint publication, an interrupted replacement retains the old
manifest and original bytes plus the exact validated next-revision checkpoint.
The next normal open rolls forward that checkpoint through existing recovery.
The backup remains unchanged. Atomic rename/flush evidence covers tested process
interruption, not arbitrary controller failure or power-loss durability.

Automatic downgrade/backup restoration is **unsupported**: replacing a current
record with an old record could erase later edits or race a pending checkpoint.
No hidden restore API or manual JSON-edit instruction is introduced. The backup
preserves pre-migration evidence; it is not ordinary version history or a portable
project. A record that requires unsupported repair is retained for recovery
instead of silently downgraded. Current reopening and normal case recovery remain
the supported path.

## Public opening and transfer

Users reopen managed cases through the existing visible PaperWorkShell case
workflow. An arbitrary external workspace/project path, portable project import
or export, project file association and ThirdLife handoff are **unsupported**.
The protected directory is private product storage, not a public transfer
format. Supported document import and ordinary PDF/ZIP/summary export remain
independent paths. Passing an explicitly selected path to another product never
authorizes that product to inspect, repair, index or migrate a case.

## Evidence and bounded behavior

Dedicated unit, native integration and security tests cover all three supported
schema versions, exact intent equality, source preservation, current idempotence,
constrained/normal policies, cancellation before migration, low disk, disk-full
injection, interruption after backup rename and after checkpoint publication,
corrupt/future/unsafe backup refusal, cleanup and absence of content-bearing logs.
The original accepted v1/v2 migration and repository tests remain unchanged.
No document worker is launched by these tests. No UI control or new user
interaction is introduced; existing error messages remain plain-language and
sanitized. Packaged-release matrix evidence remains owned by PWS-0807.
