# PWS-0803 — authenticated updates

## Implementation

Work started from the PWS-0814 handoff at
`224e51e400522486d0efb6340e2466a52eb9a5f8`. The local verifier checkpoint is
`51abc39c6c2d2babc220af168bc2a49a34299653`. The integrated product source is
`4cfbbdb295b271dae0dc70631a6e068f5e777ee9` on
`codex/pws-0803-authenticated-updates` in linked worktree
`output/pws0803/update-worktree`.

The installed **PaperWorkShell Updates** shortcut opens a separate accessible dialog.
The user selects a local folder, reviews the authenticated release information, and
explicitly confirms installation. The bootstrap verifies its provisioned offline root,
the channel authorization and the detached release signature with Windows CNG ECDSA
P-256. It enforces expiry, installation and trust sequence floors, and exact product,
platform, channel, package length and SHA-256. Root-signed trust records support
metadata-key rotation and revocation. Missing release trust prevents update admission.

The updater protects the verified archive during extraction. It verifies the complete
runtime and component inventory in an inactive slot and checks the runtime's actual
case schema before one atomic launch-state change. The installed launcher provides an
exclusive lifecycle file lease across sessions. The previous verified slot or bootstrap
can be restored without lowering the accepted release sequence. See the
[ADR](../adr/0022-authenticated-application-updates.md) and
[package preparation instructions](../../packaging/README.md#authenticated-offline-updates).

## Automated verification

- The focused update, Windows installer, signing, setup and accessibility selection passed
  200 tests. It includes valid, invalid, expired, wrong-channel and replayed offers,
  exact payload and inventory checks, user confirmation, interruption and cleanup,
  low-space injection, previous-runtime recovery, and privacy checks. The signed
  controller tests use ephemeral keys and prevent socket and DNS operations while
  checking case preservation.
- `pytest tests/integration/test_windows_launcher_native.py` passed 17 tests.
  Its new probe observed active runtime selection, bootstrap update launch, the live
  inherited lease, a competing launch refused while it was held, and a successful
  launch after release. The wider suite exposed an incorrect profile availability
  diagnostic. The repaired launcher preserves the prior failure code.
- `pytest tests/integration/test_worker_windows_containment.py` passed 132 tests with
  one host exception for unavailable synthetic directory links. The first run exposed
  the source-guard requirement for the separately launched updater's fixed case-free
  runtime health process. The final guard admits exactly that process boundary. The
  worker's egress and containment tests passed.
- Registered format, lint, strict typing, import boundaries, schemas, dependency,
  localization, task-graph, documentation and portfolio checks passed.

## Engineering artifacts

| Artifact | Identity |
| --- | --- |
| Bootstrap | `pws0803-updates-engineering-r1`, unsigned internal Inno Setup, 513,022,912 bytes, SHA-256 `d4dc66045e4ee89be881e5c32a93ed8c9ff279b2bd53ce6a03dae3a3db1b018c` |
| Bootstrap installed marker | SHA-256 `bda85a9271d9195b0f71fb767b1e9a2c1abbf5cc90e443205d7249d77d3db44b` |
| Successor | `pws0803-updates-engineering-r2`, isolated test-signed runtime ZIP, 515,298,153 bytes, SHA-256 `33bed87aaa04be6f382305d66420fd087ab14380977b20ac1464a7d038030960` |
| Successor installed marker | SHA-256 `99bfe36c8eac5d08b6511b6bf35b6091e9dd809fcc828ee59555b326e1d3331a` |
| Successor inventory | 3,220 files, SHA-256 `4062e092c15274f4cbdc66a2644091dedd45b43bb65eb06356e9c8a770a37772` |
| Build receipt | `output/pws0803/full-update-proof-v3/build-receipt.json` in the linked worktree, SHA-256 `f6fdde0960494692adbda6337967d99ee642554ed9257447e72643045371104d` |

## Installed offline verification

The disconnected PWS-Test guest ran Windows 11 Pro x64 build 26300.9711 in its
interactive standard-user desktop. PowerShell Direct verified the transferred
installer, trust records, signatures and runtime ZIP against their host digests.
Setup installed engineering R1 to the regular per-user location with the previously
accepted recipient-terms digest. The update window was opened by the installed
native launcher. UI Automation verified the foreground process, observed the install
button disabled before explicit confirmation, selected the local bundle and confirmed
installation. The update window reported success and the launch record selected slot A,
the exact R2 marker, and release sequence 2.

The normal launcher started the R2 desktop executable in slot A. The same installed
runtime completed one ordinary synthetic PDF export offline through TRACE, with no
failed checks. The 744-byte result had SHA-256
e255e4aa2fe57f955cb9096490e31b1801f3a97ac568870991f7a6ad0fb957cf.
Independent Poppler inspection reopened one page, extracted the same text as the
source, and rendered source and export identically. The host Poppler build reported
the same font-alias diagnostics for both renders. The synthetic input's digest matched
before and after. Offline processing and preservation evidence applies to this journey.

The recovery dialog asked for consent and restored the R1 bootstrap. The active
selector returned to 0 and an ordinary launch opened the exact R1 desktop
executable. Selecting the same signed R2 offer after recovery kept installation
disabled and left the complete launch record and sequence floor unchanged at 2.
The installed test build used its generic error text for that refusal.
Source `6d07c91730ec500e2869f7aecbbb8f7d7e3ad436` maps the stable rollback code
to a specific localized message. The additional widget regression reproduced
the generic wording before the fix and passed with the new message.

All 353 pre-existing synthetic case files matched their baseline through setup,
activation, offline export, restoration and replay. The aggregate inventory
SHA-256 was 77de2c5c14c64f1e8331f860d4ada3e3923943830fc2eea7b75a344ee1ba2208.
The host retained exact transfer, task, UI, launch, recovery and PDF receipts
under `output/pws0803/guest-proof-full-20261126`. After proof retention, the
temporary VM checkpoint restored the earlier running TRACE B8 installation, with
the same case inventory, disconnected adapter and preserved R16/R18 checkpoints.

The exact TRACE B8 [standard-user repair, reuse and offline workflow
evidence](pws0814-automated-setup.md) applies to its frozen installer with SHA-256
`6b2dce059dc2ef2f02e8fc5206e5aacf285af472dfd281d7785881c9c809d33e`. Legacy
fixed-layout candidates establish an update-capable bootstrap through the documented
preserve-cases replacement route.

Production trust provisioning, trusted installer signatures and exact public-candidate
qualification belong to PWS-0802, PWS-0807 and PWS-0808. PaperWorkShell owns its own case
data, release versions and update trust. ThirdLife compatibility requires a separate
future B4 verification against the exact released version.

## Next step

PWS-0806 is ready. Use the established local update and recovery behavior to
prepare the user and support documentation. Production update and signing facts
still require the exact release evidence owned by the public qualification tasks.
