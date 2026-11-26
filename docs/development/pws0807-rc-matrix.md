# PWS-0807 release-candidate matrix

PWS-0807 qualifies the exact public Windows 11 x64 package and its standalone
release evidence. This plan records the final runs, available engineering
evidence, and prerequisites. Public candidate identity, signer, release version
and final artifact hashes remain TBD. [Task graph](../../TASKS.yaml) ·
[Release evidence](../release/RELEASE_EVIDENCE.md) ·
[Standalone checklist](../release/STANDALONE_RELEASE_CHECKLIST.md)

## Candidate and environment inventory

| Environment or artifact | Available evidence | Use for PWS-0807 |
| --- | --- | --- |
| Disconnected Windows 11 Pro 26300.9711 x64 Hyper-V guest, genuine standard-user desktop, existing installation | Unsigned TRACE B8, source acc0b2621259975c6d418807488ce239b338ac05. Installer SHA-256 6b2dce059dc2ef2f02e8fc5206e5aacf285af472dfd281d7785881c9c809d33e, installed marker c5499f269b524a169848cc27681c1cee019cbfb75f04f6fbe30979a07c1363a3. Same-build repair, reuse, launch and ordinary offline PDF export have scoped receipts. | Engineering workflow rehearsal and defect isolation. Qualify clean installation in a clean environment. [PWS-0814](pws0814-automated-setup.md) |
| Windows 11 Enterprise 26100.9711 x64 Sandbox, genuine standard-account sessions | Historical unsigned R6/R7/R9 results. R9 SHA-256 d29f7f6d081c0c43295ba8a3d5bb215344cefa6253a401be34ef0f74941bd9d4. The owned R9 guest was retired after verified backup and preserve-case removal. Its volume lacked NTFS quota support. | Evidence for the recorded artifacts and operations. Prepare a clean x64 environment for any exact edition/build claim. [x64 qualification ledger](pws0802-x64-qualification-20261104.md) |
| Separate Windows 11 Pro 26300.9711 x64 update rehearsal, standard user and offline | Unsigned R1 bootstrap SHA-256 d4dc66045e4ee89be881e5c32a93ed8c9ff279b2bd53ce6a03dae3a3db1b018c. Isolated test-signed R2 runtime ZIP SHA-256 33bed87aaa04be6f382305d66420fd087ab14380977b20ac1464a7d038030960. | Authenticated-update mechanism rehearsal within the isolated test trust configuration. [PWS-0803](pws0803-authenticated-updates.md) |
| Clean, contained Windows 11 x64 environments for the proposed support claims | Required exact editions and builds are TBD. Capture actual editions, builds, architecture, target scope, privileges, NVDA and PDF-reader versions, network state, and installed product baseline before each run. | Start from fresh user and machine state for clean installation. Use separately seeded states for update, migration, removal, and recovery. [Windows support boundary](../release/WINDOWS11_X64_SUPPORT.md) |
| Contained Windows 11 x64 environment with bounded exhaustion of the ordinary fixed install volume | The Pro Hyper-V guest reports NTFS quota capability and one system partition record. This read-only check establishes a platform capability. A separately authorized, configured disposable target is still needed. Preserve the earlier unavailable Windows Sandbox result. | Exercise real pre-install refusal, exhaustion during installation or repair, recovery, and preserved case/external hashes. Restore only the owned test environment after retaining its receipts. [Installer low-disk record](pws0802-x64-qualification-20261104.md#exact-remaining-x64-observations-and-external-gates) |

The release owner supplies the approved signing route, intended support and
channel claims, and reporting and maintenance commitments. The qualification
operator records the final installer and app signatures, approved signer and
trust policy, signed update inputs, source/tag, lock, SBOM, component licenses,
exact packaged inventory, and SHA-256 values before testing. Pin matching
release-interface and sample revisions in each receipt. Any rebuilt or re-signed
artifact has a new identity. Reassess the affected cells against its final
bytes. [Windows qualification plan](../../packaging/windows/QUALIFICATION_PLAN.md) ·
[Release interface](../release/RELEASE_INTERFACE.md)

## Verification cells

Every final-candidate cell is pending. Prior observations retain their original
verdicts. Record pass, fail, not run, or unsupported per actual scenario. An
unsupported result requires a product reason, the visible behavior where
applicable, and the documented in-app route. Record failed attempts and
preservation or cleanup results separately.

Bind each receipt to the exact candidate and installed marker, interface and
sample revisions, environment and scenario starting state. Record the entry
point, expected behavior, actual result, output hashes, preservation checks and
receipt digest. Use known-folder notation and synthetic filenames in published
evidence. A not-run cell names the missing prerequisite. A failed cell records
the actual defect or observation and the evidence for any later retest.

| Area and owner | Prior evidence | Required candidate outcome |
| --- | --- | --- |
| **Artifact admission and offline deployment** · packaging operator | PWS-0801 supplies a reviewed inventory and provenance workflow. R6/R7/R9 installation records and B8 setup show scoped standard-user behavior. [Dependency inventory results](pws0801-result.md) · [B8 setup](pws0814-automated-setup.md) | Authenticate signatures, approved signer, timestamp and packaged files from the final distribution. Test wrong-signer and changed-bytes refusals. On a clean supported x64 environment without development tools, install locally, confirm privileges and visible prompts, launch from the ordinary shortcut and recheck repair/reinstall, restart conditions, inventory and stable application identity. |
| **Primary workflow and standalone operation** · application operator | The M4 source gate covers the interactive workflow and real-worker export modes. B8 exported a one-page PDF offline, refused an occupied name and left the source and immutable copy intact. [M4 gate](../gates/m4-pilot-gate.md) · [B8 walkthrough](pws0806-documentation.md#installed-documentation-walkthroughs) | Run without ThirdLife, siblings, accounts or a project server. Open supported PDF/JPEG/PNG through documented in-app actions. Confirm source copying and immutable hashes, page order, readiness, checklist review, combined/separate/ZIP outputs, package summary and reopen after restart. Check exported files for page count/order, structural validity, rendering and packaging constraints. Check the documented behavior for associations, OS file opening and portable projects. |
| **Authenticated update, repair and recovery** · packaging operator | R1/R2 engineering rehearsal recorded consent, local activation, offline export, restoration and replay refusal with 353 pre-existing synthetic case files matching. [Update rehearsal](pws0803-authenticated-updates.md) | Bind the installed supported starting version to the candidate update. Verify the approved production trust chain, exact signed artifacts, update interruption before and after activation, wrong/tampered/replayed offers, recovery or documented rollback behavior, successful relaunch and preserved cases. |
| **Case migration and application recovery** · storage and application operator | PWS-0804 source/native-storage tests cover supported v1/v2 to v3 paths, exact previous-manifest backups and failure cases. [Migration result](pws0804-result.md) · [Compatibility fixture index](../../tests/fixtures/manifests/release-compatibility-v1.json) | Open disposable supported older cases through the packaged app and confirm migrated intent and immutable source hashes. Check exact backup, restart/reopen, processing interruption, cancellation and recovery. Future-version, invalid, interrupted or storage-limited cases must fail safely or recover under the documented contract. |
| **Uninstall, preservation, reinstall and explicit data removal** · packaging operator | R9 preserve and partial-deletion observations recorded actual outcomes in separate receipts. The record gives the scope of unknown data, partial failures and reader activation. [Installer and removal ledger](pws0802-x64-qualification-20261104.md) | Test Cancel and default preserve with synthetic cases and external sentinels. Confirm that reinstall reopens preserved cases. On a separate disposable synthetic state, verify explicit deletion with a successful case and safe handling of an unavailable or partial case. Measure installed files, registry/associations, shortcuts, case/cache/temp data, worker artifacts, configuration, logs, optional engines, migration backups, external exports and support bundles by category. |
| **Resources, failure cleanup and offline worker boundary** · resource/security operator | Source budget suites and constrained pilot results establish software behavior for their tested profiles. V12 warm PNG timing exceeded 2,000 ms and its measurement limitations remain. The PWS-0811 preparation completed no timed B8 import. Earlier keyboard and NVDA evidence retains separate composition failures. [PWS-0811](pws0811-repairs-20261106.md) · [Keyboard results](pws0701-result.md) · [NVDA results](pws0702-result.md) | Record central ResourcePolicy version and actual enforced normal/constrained settings, profile, workload hashes, full process-tree memory, worker Job limits, CPU/time, caches/temp, concurrency and output size. Exercise slow I/O, simulated low space, cancellation and cleanup, malformed input, CPU-only operation and packaged egress denial. Keep the actual ordinary-target disk-full installer result distinct from simulations. Recheck reported timing and affected compositions using the exact candidate and a qualified observer. |
| **Diagnostics and privacy** · support operator | PWS-0805 verifies source behavior and the generated support sample. PWS-0806 recorded exact B8 preview/consent, two failed saves and a description-only fallback. PWS-0807 records its additional B8 attempts with their tested conditions. [Source diagnostics](pws0805-result.md) · [B8 diagnostics investigation](pws0807-diagnostics-investigation.md) | On the genuine standard-user desktop, check exact inventory before consent. Save through the public picker with a synthetic case open, then verify the reopened archive's digest and exact members. Scan with synthetic prohibited tokens. Check cancellation, existing-name refusal, rejected destination or storage failure and cleanup, and the offline description-only fallback. |
| **Accessibility and assisted use** · accessibility operator | M7 source gate includes native keyboard, NVDA, scaling, contrast and assisted-use evidence. B8 proved scoped configuration-pane readability and focus at 96/192 DPI. [M7 gate](../gates/m7-accessibility-assisted-gate.md) · [B8 installed accessibility](pws0813-trace.md#installed-b8-verification) | Run the packaged keyboard-only journey and named NVDA scenarios. Capture visible focus, names/states, announcements, recoverable errors, consent/retention, accessible summary, 200% scaling and high contrast. Record agent-observed interaction and human judgments separately. Use one resumable helper for any human observations and estimate the full journey before handoff. [Human-testing policy](human-testing.md) |
| **Reader compatibility, compression and redaction** · compatibility and security operators | M3/M5 reader observations apply to their exact PDFs. PWS-0608 source approval describes the qualified single active affected PDF page and runtime verifier. Earlier viewer-resave observations retain their limits. [Overlay readers](../compatibility/pws-0307-overlay-reader-matrix.md) · [M5 gate](../gates/m5-compression-compatibility-gate.md) · [M6 gate](../gates/m6-permanent-redaction-gate.md) | Verify candidate-generated compression/overlay files in supported Windows Adobe Reader, Edge, Chrome and Firefox configurations and record exact versions. Confirm quality-floor refusal and ordinary export reopening. Exercise the independent redaction release profile, candidate rendering and extraction checks, required reader/resave behavior and export blocking on verification failure. |
| **Evidence freeze and release blockers** · release owner | The interface, index and checklist remain drafts. [Release index](../release/RELEASE_EVIDENCE.md) · [Standalone checklist](../release/STANDALONE_RELEASE_CHECKLIST.md) | Audit every public file for prohibited personal data, private paths, unapproved fixture content and misleading claims. Freeze the tested artifacts, sample package, exact interface/evidence revisions and limitation inventory together. Identify unresolved release-blocking results for sign-off. |

## Engineering rehearsal results

- The standard-user B8 comparison isolated the support ZIP picker handoff.
  Starting with an empty filename selected the intended path and produced a
  reopened ZIP matching the reviewed digest and exact file inventory. The
  source fix and regression passed. [Diagnostics investigation](pws0807-diagnostics-investigation.md)
- Unsigned R1, built from commit e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec,
  passed the installed standard-user support ZIP scenario with a synthetic
  case open. The native picker honored the submitted filename, the reopened
  archive matched the preview and the occupied ZIP stayed byte-for-byte intact.
  Signed-candidate diagnostics remain pending. [Packaged R1 verification](pws0807-diagnostics-investigation.md#packaged-verification-on-unsigned-r1)
- The existing canonical installer smoke ran the exact B8 installed runtime on
  the disconnected Pro guest under constrained-v1. The separate launch receipt
  qualifies the standard-user token and session. The component smoke imported
  PDF and PNG, generated two previews, reopened a verified two-page PDF after
  rotation, and processed one English OCR page. It refused unavailable input
  and an occupied export name. The external originals and the existing case
  inventory were unchanged, with zero temporary files remaining. Final case
  and cache sizes were 318,825 and 290,512 bytes. Peak resource measurements,
  slow I/O and disk-full were outside this run.
- The smoke report at
  `output/pws0807/constrained-smoke-prep/receipts-b8v1/smoke-result.json`
  has SHA-256
  `eeb8e1e0978ec938bd71b93583fd531b850ff80557e4d9ca89269d2ebe8bad03`.
  The separately retained synthetic PDF has SHA-256
  `a40627abae5a8725bbcba247b2af0478aadd63925dc097f3ee05e5b434c70af7`.
- The environment probe found Edge 157.0.4341.42 in a conventional installation
  path. Chrome, Firefox, Adobe Reader and NVDA did not resolve at the
  conventional paths checked. Record actual qualified copies and versions in
  the final reader and NVDA scenarios.
- After the diagnostic and constrained-smoke receipts were retained outside the
  guest, the owned pre-test checkpoint was restored. The active checkpoint is
  `PWS0807-before-original-diagnostics-reproduction-20261126`. The exact B8
  marker and all 353 baseline case files matched the pre-test receipt.

## Standalone sample readiness

The local engineering rehearsal archive is
output/pws0807/rc-sample-preparation/paperworkshell-pws0807-rehearsal.zip in the
primary checkout. It contains 12 members, including the manifest, instructions,
mixed PDF/JPEG/PNG and malformed inputs, a four-category packet, and sample
outputs and summary. The archive is 79,466 bytes with SHA-256
3f99132e414566b664dc1ccbaaed926525da549daae7c33db41d01f95bdd77f0. Its separate
verification receipt records repeat generation, archive reopening and hashes,
source/reference PDF text and render matching, and privacy checks. The sample
use instructions identify the earlier outputs as engineering references.

The checked-in [synthetic corpus index](../../tests/fixtures/synthetic/v1/index.json)
records input provenance, and the [M4 pilot walkthrough](pws0408-pilot.md)
defines the four-category package workflow, expected assignment and summary
checks. The one-page born-digital PDF has SHA-256
3f62fe484b8b389ec18afe115939d491f795e517195a7acf7fe5989075839c47. The PNG OCR
fixture has SHA-256 f2976a85d04b992bd690d8a2b70faf8444197d87ce58a7a5687a778c97f3fd82.
The [existing generated support sample](../../tests/fixtures/diagnostics/v1/README.md)
has SHA-256 4f5bb7b36f1b58f36901f31c18b721a6875a44bc731fb8da3779e39b77662304.
Use its fixed two-member inventory as a documentation example. Compare a live
bundle against its own preview digest.

Reopen candidate-generated combined, separate and ZIP results, and save and
compare the accessible summary. Bind each generated file to its workflow, input
digests, candidate, expected behavior and individual SHA-256. Keep the internal
migration fixture distinct from a portable case/project deliverable. Current
public project transfer is unsupported. Publish the mechanical fixture and
expected-output entries through the existing release documents and keep
engineering sample files identified as engineering evidence. [Release sample
inventory](../release/RELEASE_EVIDENCE.md#6-synthetic-release-sample-package)

## Completion dependencies

1. The release owner supplies signed release artifacts and the approved signer
   and trust policy. PWS-0802 signature qualification applies to those exact
   bytes. Independently prepared sample inputs, diagnostics and engineering
   rehearsals can proceed under their own identities while this is pending.
2. The qualification operator establishes an authorized contained x64
   environment with demonstrable bounded disk exhaustion at the actual install
   target. The earlier Sandbox capability result and the current Hyper-V read-only probe leave real exhaustion unrun.
3. The qualification operator records all applicable results against the exact
   public candidate and isolates any failures. The release owner reviews the
   supported editions/builds, versions, reporting route and remaining defects
   and resolves the release documents before PWS-0808 sign-off.

The work uses PaperWorkShell's existing standalone interfaces and synthetic
data. Each release remains independently testable with ThirdLife absent.
