# PWS-0802 — unsigned installer implementation and qualification

**Historical R1/R2 report.** The later accepted Microsoft route, complete-runtime
R7 candidate and new observations are in the
[detailed R6/R7 record](pws0802-community-runtime-20261103.md). The
[current x64 continuation](pws0802-x64-continuation-20261104.md) applies PWS-D043:
Windows 11 x64 only. Earlier ARM64 proposals and unrun checks below are superseded
and outside scope, not passed or remaining requirements. Pending-agreement
statements below describe the earlier epoch; no repeat Microsoft acceptance is requested.

This resumes the maintainer's 2026-11-03 direction on the current local source.
The [earlier preflight](pws0802-result.md), completed milestones and accepted
PWS-0801 evidence remain historical facts. No published branch was restored.

**Status: blocked for public release; unsigned internal R2 built and audited.**
PWS-0804 and PWS-0805 are independently done after shared source-quality closure.

## Current handover — R2

`output/pws0802/resume-20261103/candidate-r2-final-source/` contains the final
unsigned engineering build. Earlier uncompiled staging attempts are not qualified
installers; R1 remains separate historical evidence below.

| Artifact | Exact value |
|---|---|
| Candidate | `pws0802-internal-20261103-r2` |
| Installer | `installer/PaperWorkShell-pws0802-internal-20261103-r2-win-x64-UNSIGNED.exe` |
| Size | 511,975,164 bytes |
| Installer SHA-256 | `c2e5bcfd9797c1f697fb278378567f31a0f99dc81ff323cb3faa0a7aedfdda6b` |
| Build receipt SHA-256 | `6c2cec3b78549aa67dd5ba36c9c811f05e5c504645cbec6e2d8af92b56893e63` |
| Inventory SHA-256 | `c2f575abb74e80dfe0bc974c226937d226754e7dba807fb802e3b53f089cc3d6` |
| Payload | 3,176 files; 648,092,693 expanded bytes |
| Installer source archive | 892 exact frozen members; SHA-256 `ab074b813ea7789bff42fa7d07ee2fcd8b305f223fb4dbf4d98fc5b17401afbb` |
| Independent audit receipt | `output/pws0802/actual-installer-r2-audit-v1.json`; SHA-256 `2b3586f9adf3c9578e98cdefaa268a80a8089bd0b27501ba637826865c53a768` |
| Release permission | **Unsigned/internal only; public distribution prohibited** |

The independent R2 audit passes both authentic offline CycloneDX 1.6/SPDX 2.3
schemas, exact SBOM/notice regeneration, every payload identity, all 892 frozen
source members and all 319 current application sources in wheel/sdist. All 956
inherited third-party records, 2,838 inherited file identities, 22 inherited
LicenseRefs and seven replacement notices remain unchanged. Across 2,428 runtime
paths, R1/R2 bytes differ only in the installed marker's candidate ID.

R2 corrects an installer-source privacy false positive: seven Win32
`Function@DLL` declarations are now expressed through a named DLL constant with
the same compiled imports. The actual source regression failed before and passes
afterward; no scanner exemption was added for project code. A minimal admitted
compiler proof and real R2 compilation both pass. The raw source-finding receipt
is retained. The separate exact UniPs upstream attribution addition is original
public licence text, with a whole-blob review and unchanged strict fixture/other
privacy checks.

Actual CLI compilation also exposed relative executable paths being interpreted
after a child working-directory change. Regression tests failed before correction;
the builder now anchors the compiler and wrapped executable before changing cwd.
All 27 installer units pass, and the documented relative-path CLI builds R2.
The same central fix covers production signing's compiler invocation without
changing its protocol, approvals or resource caps. The failing R1/R2 early compile
attempts remain recorded rather than attributed to installer/product behavior.

R2's actual installed install, in-place same-candidate repair and reopening of
the prior R1 synthetic case have passed. The reopen preserves two pages/order,
90/0-degree rotations, revision 4, manifest/two-operation timeline hashes, both
source hashes, one typed export-history record and zero temporary files.
The full installed smoke and preserve-uninstall also passed: two previews, one
OCR page (27,504 bytes), seven export checks, invalid-input/existing-export
refusals, exact inventory, zero temporary files and unchanged old/new synthetic
profiles and external originals/exports. Product directory, registration and both
shortcuts are absent afterward. The existing production case-root count stayed
ten, without reading those cases. Final R2 session SHA-256 is
`fc9fea56329ca816fbb06cbf9b1e1ee21555c3c7980a5964778c85e414cbee47`.
Combined receipt: `output/pws0802/qualification/host-r1-20261103/repair-reopen-r2/r2-host-lifecycle-result.json`,
SHA-256 `5d7d4f38b966f5f172e44e29fee412d7eb1e17f915a4c3494080df31377bebd8`.
A CRLF-versus-LF harness hash mismatch stopped before child launch;
its receipt is retained and the corrected byte-exact continuation repeats no
completed install/repair work.

### Supplementary failure-path and GUI observations

The final additional receipt is
`output/pws0802/qualification/host-r1-20261103/interrupted-repair-r2-recovery/combined-supplementary-result.json`,
SHA-256 `1f1d4c899f5cd2123bbea8a07d498369a344e80c535b8ba3c1934d6df590bd92`.
It preserves the earlier regular R2 pass unchanged and distinguishes these outcomes:

- Actual quiet `/DIR=` override refusal passed with native exit 1; product,
  registration and wrong target remained absent, and the production case count stayed ten.
- The synthetic containment proof passed after the actual child was ready and
  pinned: controlled termination ended the controller and every observed job member.
  Earlier strict-JSON timestamp and duplicate asynchronous-termination helper failures
  remain retained; they occurred before fresh product damage and are not product passes.
- One actual repair attempt restored an authenticated 345-byte first-party notice,
  but its job snapshot was stale. The required live-installer cutpoint was
  **NOT OBSERVED**, so the interruption test is not passed or retroactively inferred.
- The controlled stop left two Inno stages: an exact old-uninstaller copy and a
  35,147-byte zero preallocation matching a source-notice size. Both exact files were
  copied to retained output, hash/identity/ancestor checked and removed through their
  pinned ordinary single-link handles. This was explicit operator recovery, with
  no wildcard or unknown-file cleanup; automatic interruption recovery is not claimed.
- Ordinary same-candidate repair then passed full inventory, registration and
  uninstaller-data binding. The prior case reopened with unchanged page order,
  rotations, source hashes, operation timeline and typed export history.
- A new isolated installed GUI called the normal identity setter before Qt.
  Actual AUMID `io.github.pikkujanne.paperworkshell` was verified before and after
  native window creation. The helper nevertheless returned the same identical-owner
  binding error twice with fresh window inventories. No screenshot or UI input
  occurred; packaged GUI/keyboard observation remains **NOT RUN**. Missing AUMID
  was only a hypothesis and was **not confirmed** as the earlier failure's cause.
- The held owned GUI controller and seven process identities ended and released
  the app mutex. Final preserve-uninstall passed: product directory, registration
  and both shortcuts are absent. All three synthetic profiles and external
  originals/exports remain unchanged; the ten production entries were count-only.
  Final controlled session SHA-256 is
  `7f3deafc9d51d9a60ac80effa50f97efe22abe65105f4e40b8f53aeb74dd8e22`.

These are host observations, not clean-machine, standard-account, normal-public-launch,
automatic rollback or power-loss qualification. No further experiment was performed.

## Built

- Per-user Inno Setup 7.1.1 installer, embedded CPython 3.14.6, current application,
  exact qpdf/Tesseract/OCR resources, original notices, corresponding sources and
  Qt/LGPL replacement instructions. No installed Python, uv, developer environment
  or repaired PATH is used by installed launch. The retained external Microsoft
  CRT prerequisite prevents a complete clean-machine/offline-installation claim.
- Product/publisher: PaperWorkShell / Janne Vuorela. Authorized project-owned
  material uses MIT; third-party terms and contributor notices remain separate.
  Official distribution is free, including commercial use. No product account,
  registration, email, activation, subscription or key; no document upload,
  telemetry or automatic crash-report submission.
- Stable application ID `io.github.pikkujanne.paperworkshell` and Inno upgrade
  ID `{DAC95C77-B1E7-501E-825D-4FAA0E3BCCCE}`. Engineering version `0.0.0`,
  channel `engineering`; Windows 11 minimum build 22000, initial native x64 only.
  The chosen ARM64 route is x64 emulation, pending actual qualification and
  disabled in this candidate. This is not a supported release matrix.
- Fixed `<LocalAppData>\Programs\PaperWorkShell`; unelevated per-user setup,
  no associations, PATH mutation, services, scheduled tasks or automatic launch.
  Exact file inventory and ancestor/reparse/hardlink checks precede mutation.
  The launcher and installer share a lifecycle mutex; child processes use a
  kill-on-close Job. Same-candidate reinstall implements narrow file repair.
- Preserve-default uninstall. Interactive removal offers a separate unchecked
  managed-case deletion choice through authenticated per-case authority; unknown,
  corrupt, active or unsafe state is retained and reported. Silent removal
  preserves cases. External originals, exports and selected support bundles are
  outside removal authority. No wildcard app-data deletion is present.
- Production signing tools bind the approved public certificate and exact input
  bytes, validate SHA-256/RFC3161 timestamp/trust, sign launcher and embedded
  uninstaller before the outer installer, regenerate inventories/SBOMs and require
  final installed-uninstaller verification. Missing policy/signing inputs fail
  closed before mutation. No credential, enrollment or real product signature
  has been supplied, acquired or claimed.
- Native launch sets error mode `0x8003` before creating Python; worker creation
  preserves inheritance. Microsoft's [SetErrorMode contract](https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-seterrormode)
  states that `SEM_NOGPFAULTERRORBOX` prevents WER invocation and children inherit
  the mode. Native launcher tests observe the inherited flags. This is a process
  control, not a Windows-setting change or a new network-capture result.

## Historical R1 internal candidate

Candidate `pws0802-internal-20261103-r1` is under ignored
`output/pws0802/resume-20261103/candidate-r1-final/`:

| Artifact | Exact fact |
|---|---|
| Installer | `installer/PaperWorkShell-pws0802-internal-20261103-r1-win-x64-UNSIGNED.exe` |
| Size | 511,971,056 bytes |
| SHA-256 | `1b6ba9e0ce7eeb9f25b74730d70d196057dadc62463ae42c4592ea3f1432225a` |
| Build receipt SHA-256 | `638bdc88b1d0158b07a19cba75408f3b84f104d6ba5d12ef14a68c48ba4b1912` |
| Resolved inventory SHA-256 | `05fee629e6d02f5acf6572139a2ac58a33fb0df1fa748ffbcbb78682c430081f` |
| Payload | 3,176 files; 648,088,589 expanded bytes |
| Base dependency inventory | unchanged PWS-0801 R3 `8fb9366e08dee502a69858d8070b97a98ba9d55af232bd87fcf2a50ccad5eb2b` |
| Launcher SHA-256 | `1cea7b0ae1a498282587f2c45cfc7a3370553736268c596972f053cdabe6993e` |
| Public distribution/signature | **Not permitted / unsigned internal only** |

The sealed source archive reopens with all 890 included source/recipe files
matching the stage's exact epoch. Both SBOMs and full notices are adjacent to the
inventory; installed notices avoid a circular self-hash. Changes after this
sealed epoch are not silently represented as candidate contents. The compiler
review binds its 133 files, source and original licence assets. The launcher
receipt independently binds 17 source-toolchain archives and 4,575 extracted files.

## Historical R1 qualification scope

The actual available host is Windows 11 Professional x64 build 26300.9711,
unelevated, with development tools and preinstalled Microsoft CRT. It is **not a
clean machine**, and an unelevated token does not prove a standard-user account.
The host has ten existing case-root entries; their manifests/content are not
inspected or launched for testing. The qualifier instead uses installed embedded
Python and an isolated synthetic backend. Public normal first launch is therefore
not proved by that harness.

`python -m tools.qualify_windows_installer` anchors each operation to independent
installer/receipt hashes and the preceding immutable session receipt. It refuses
an unrelated existing installation. Fixed engineering quiet installation uses
`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-`; quiet removal uses the same
fixed options and preserves cases. Other
silent combinations are unqualified. Destination/INF, forced-close, restart,
component/task and destructive data overrides are rejected.

Host install and constrained synthetic processing passed: installed module origin,
two previews, rotation, seven export checks including all-page rendering and
destination reopen, one OCR page (27,505 bytes; OCRmyPDF 17.10.0/Tesseract 5.5.3),
invalid-input refusal, existing-export refusal, close/reopen of two pages,
unchanged synthetic originals and zero temporary files. Smoke receipt SHA-256
`07c41f75dfac8693bad9646b4f162244287f3b0d95aba039c403a96b371161e4`
is under `output/pws0802/qualification/host-r1-20261103/`. Peak resources were
not measured; `constrained-v1` denotes the actual policy, not hardware certification.

The isolated GUI process exposed its native window, but the computer-use helper
rejected the selected binding twice, including a fresh inventory retry, with
identical expected/current app-owner text. No UI input occurred; GUI/setup
keyboard observation is **NOT_RUN**. The exact helper error is retained in
`gui-helper-blocked.json`. Only the held, verified synthetic GUI process was
terminated; no UI automation workaround or Windows setting change occurred.

The first quiet uninstall returned exit zero and removed payload/registration,
but the qualifier reported **QUALIFIER_UNINSTALL_RESIDUE** for the remaining
uninstaller. Reviewed Inno source establishes a two-phase handshake: first phase
returns before the second phase's 500-ms delay and original-executable deletion.
The qualifier's kill-on-close Job ended that cleanup child at parent exit.
This original failed observation is retained; bounded descendant-grace recovery
is qualified separately and is not silently substituted for it.

The corrected qualifier passed same-candidate reinstall and preserve-uninstall.
Product directory, registration and both shipped shortcuts are absent afterward.
All six synthetic profile files and eleven directories, the external PDF/PNG and
verified export remain identical. Existing production case entries stayed ten;
their contents were not inspected. Final lifecycle receipt SHA-256:
`cbed466b7beac8190bcbb01cd5f915ed04411ada903a386e796df9775dfc42d9`.
The original residual was preserved and only its authenticated single opened file
removed during an explicit qualification recovery; no recursive/app-data cleanup
or automatic recovery claim was introduced. A four-test native regression proves
successful child cleanup, unchanged default child termination, bounded timeout
and immediate failure containment. Signing limits remain 120 seconds/64 KiB;
only validated uninstaller execution receives at most 30 seconds inside its
original deadline. This qualifier epoch changes no sealed product bytes.

No clean Windows, standard-user, ARM64, signed
final-byte, power-loss rollback or network-capture pass is inferred from unit
tests or the source host. Failure and unrun results remain explicit.

Focused implementation verification already includes real native launcher
lifecycle/argument/environment tests, uninstall cancellation and concurrent-case
refusal, bounded signing subprocess/PE policies, installed resource discovery,
installer source/metadata/tamper rejection, migration recovery and diagnostic
publication/privacy/accessibility. A wrong metadata version/MIT omission failed
five regression cases before correction; all 24 installer unit cases then passed.
Two initial stage attempts were rejected for dropping the shared LGPL replacement
notice. The copy rule was corrected to preserve it before the successful stage;
the rejected inventories remain local evidence. No fixture/golden was refreshed.

## Public distribution and trusted signing remain pending

The [Microsoft/SignPath review](pws0802-signing-and-runtime-review.md) records the
official terms and sources. Data-only inspection now traces all five canonical
x64 CRT files to actual CAB/MSI members of the exact official redistributable,
version 14.44.35211.0, with valid Microsoft signatures. This establishes component
authenticity, not redistribution rights. The [gated overlay preparer](pws0802-microsoft-runtime-preparation.md)
requires separately pinned acceptance/acquisition/recipient-term attestations,
refuses missing authority before any output, and does not enable the complete-runtime
installer flag. Its synthetic tests and real-cache no-write refusal pass; no real
overlay or target load has occurred. The existing hash-renamed pikepdf DLL's
older conditional route is preserved; System32 files are not a redistribution source.

The proposed legitimate free entitlement is Visual Studio Community 2022's
individual-developer route and section 4 REDIST provisions. Owner acceptance of
the exact agreement, legitimate Community product acquisition and scoped
protective recipient terms remain necessary before app-local redistribution.
The maintainer has not accepted that agreement by selecting MIT or authorizing
engineering. No unresolved canonical CRT is included in this candidate.
The exact [Microsoft-only recipient-text proposal](pws0802-microsoft-recipient-terms-proposal.md)
is available for review; it is not accepted, shipped or a grant covering the
separately retained pikepdf object. Legitimate acquisition, that object's route
reconciliation and final load/notice/inventory qualification remain engineering
work before a complete public candidate.

SignPath Foundation is the preferred zero-cost candidate, with OSI eligibility,
trusted source/CI/onboarding requirements and explicit approval needed for bundled
Microsoft System Libraries. Its certificate publisher is **SignPath Foundation**,
distinct from AppPublisher Janne Vuorela. No acceptance is assumed and no provider
has been contacted, enrolled or supplied files. Unsigned or self-signed bytes do
not meet the public trusted-signing criterion. Final signed/repackaged artifacts
must receive new hashes, inventory/SBOM and all affected qualification checks.

The [clean Windows feasibility report](pws0802-clean-windows-feasibility.md)
records available hypervisor capability without claiming a prepared guest.
There is no ready Sandbox/Hyper-V/other VM environment or accepted Windows guest
media entitlement. A legitimate guest provisioning route and x64/ARM64 execution
remain agent work. No user environment construction or technical transcription
is requested, and no Windows security control is disabled.

## Independent tasks and boundary

As expressly directed, PWS-0804 migration/recovery and PWS-0805 opt-in local
support bundles continue independently of signing/entitlement. Their
[migration](pws0804-result.md) and [diagnostic](pws0805-result.md) reports retain
focused and broad evidence, including original failures and source epochs.

All work stays within PaperWorkShell. No sibling input, shared SDK/service/data,
portfolio adapter, synchronized version or ThirdLife compatibility claim exists.
Private cases/migrations remain PaperWorkShell-owned. The human-readable release
interface records selected identity and source features while unsupported or
unproved release facts remain visible. PWS-0802 is not complete; PWS-0803 remains
dependent on its actual completion.

## Task result — PWS-0802: Build Windows installer and signed release artifacts

**Status: blocked.** The unsigned internal build and available host qualification
are concrete; public release acceptance is incomplete. PWS-0804 and PWS-0805 are done.

### Implemented

The R2 table and separate actual-host receipts above identify what was built and
tested. Runtime packaging, native launch, safe removal, signing integration and
the gated Microsoft preparation route are implemented. No production signing,
provider enrollment, public upload, purchase or Microsoft agreement acceptance occurred.

### Changed files

- `LICENSE`, `LICENSING.md`, `pyproject.toml`, `README.md`: selected publisher,
  scoped MIT/free distribution and explicit account/privacy policy.
- `packaging/windows/product-v1.json`, `paperworkshell.iss`, `launcher.c`,
  `launcher.rc`, compiler/attribution review JSON and two original upstream licence
  assets: stable identities, native launch, per-user install/removal and admitted inputs.
- `tools/build_windows_launcher.py`, `build_windows_installer.py`,
  `windows_signing.py`, `sign_windows_installer.py`, `qualify_windows_installer.py`,
  `prepare_windows_microsoft_runtime.py`: bounded build, signing, provenance and qualification.
- `src/paperworkshell/app/installed_runtime.py`, `__main__.py`, and
  the four `uninstall_review.py` modules under `src/paperworkshell/domain/`,
  `app/`, `storage/` and `ui/`: installed resource discovery,
  native recovery and authenticated preserve-default data review.
- PWS-0804's manifest registry/repository/backup adapter, compatibility fixture,
  tests and architecture/result documents: exact bounded backups and transactional recovery.
- PWS-0805's diagnostic domain/application/storage/UI modules, window Help action,
  three additive catalogs, exact synthetic sample/allowlist, tests and documentation.
- Dedicated unit/integration/security/accessibility tests, `tools/_reviewed_upstream_notices.py`,
  `.gitattributes`, ADR 0018, release/alignment documentation, `TASKS.yaml` and
  `BUNDLE_MANIFEST.json`: failure/privacy evidence and truthful task/release state.

The complete path and preservation inventory is the local ignored receipt
`output/pws0802/resume-20261103/final-preservation.json`; it distinguishes the
authorized existing-file changes from new files and untouched accepted work.

### Verification

- `uv build --offline --wheel --sdist` — pass; all 319 wheel/sdist application
  sources match the installed application epoch and scoped MIT metadata.
- Native launcher build/verify and six actual launcher lifecycle tests — pass;
  independent receipt binds the full admitted toolchain and source.
- `python -m tools.build_windows_installer stage ...` and `compile ...` — pass
  for the exact R2 table, including the documented relative compiler path.
- Actual R2 official SBOM/schema, regeneration, source, byte and notice audit — pass.
- `python -m tools.qualify_windows_installer` plus bound reviewed operator/continuation
  — R2 install, in-place repair, old-case reopen, full smoke and preserve-uninstall pass;
  scope **HOST_NOT_CLEAN**, with the original preparation failure retained.
- `tools/dev.py test-unit` — 7,648 pass/two disclosed symlink skips in isolated
  current-source checkpoint `338013a97781991dab8f37c1db521f36f42589f8`.
  Original root/index/metrics preserved. Final tool delta: 250 pass/one disclosed
  symlink skip/122 subtests; all 319 application source files unchanged.
- `tools/dev.py test-security` / `test-integration` with recorded affected
  selections — 79 / 31 pass; these are selections, not full-suite claims.
  `test-accessibility` — all 346 pass.
- `format-check`, `lint`, `typecheck`, `check-import-boundaries`, schema/task/docs/
  portfolio/dependency/localization/release-inventory/Pillow validators and all
  registered benchmark/resource validators — pass at their recorded source epochs;
  final strict typing covers 918 Python files. All five quality self-tests pass.
- `validate-fixtures` and `scan-secrets` — frozen final source passes, including
  3,524 historical blobs, unchanged 1,012 index blobs and 2,112 working files.
  Metadata-only closure is checked separately without promoting stale results.

The full-unit source-map proof, final tool-source delta, failed-before/passed-after
regressions, original scan/preparation failures and raw command lanes remain under
ignored `output/pws0802/`; the PWS-0804/0805 reports preserve their own earlier failures.
Completion-date schema validation caught unquoted YAML dates and passed after
quoting them as the required strings; no check or schema was weakened.
Final metadata validation also caught a colon interpreted as a YAML mapping;
the note punctuation and two merged list entries were corrected before closure.

### Security/privacy/accessibility notes

Synthetic documents only; originals/exports remain unchanged; no normal launch
against the existing private case root occurred. Its ten entries were count-only.
Worker isolation, network denial, central resource limits and export verification
are preserved. Peak-resource measurement and packet capture were not performed.
New widget/keyboard/focus/scaling checks pass; actual packaged GUI/setup/data-choice
interaction remains unrun after the recorded computer-use helper binding failure.

### Portfolio boundary and release-interface notes

Standalone usefulness and private data ownership remain complete without siblings.
No adapter, shared schema/service/runtime, sibling access, synchronized version or
compatibility claim was added. Release documents now record selected identity and
source features while keeping unproved public-release fields draft/TBD.

### Residual risks or deferrals

Public trusted signing/provider approval; Microsoft Community acceptance and
legitimate acquisition; approved component-scoped recipient terms and retained
pikepdf reconciliation; complete app-local CRT delivery; clean Windows/build and
ARM64 qualification; actual standard-account/normal-first-launch and packaged
interactive accessibility/data-deletion observations remain incomplete.
Installer low-disk, restart, different-version upgrade and broader interruption/
rollback coverage are unrun; authenticated updates remain PWS-0803. No power-loss,
hardware, portal, legal-validity, secure-erasure or PDF/UA claim is made.

**Precise owner action:** accept the linked exact Community 2022 agreement as an
eligible individual and approve or correct the linked Microsoft-only recipient
text. This authorizes agent acquisition/preparation under the selected free REDIST
route; it is needed because engineering authorization does not accept Microsoft's
agreement or establish protective downstream terms. It requests no owner installation,
environment repair, technical transcription, private signing material or provider enrollment.

### Task graph

- PWS-0802 remains blocked on unmet release requirements; it is not marked done.
- PWS-0804 and PWS-0805 are done independently after their applicable checks.
- Next ready task: **none**. PWS-0803 remains planned until PWS-0802 is actually done.
