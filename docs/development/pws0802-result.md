# PWS-0802 — Windows installer preflight and blocker

**Status: blocked — publisher/licensing decisions and approved signing identity/process are
not established. This is an implementation preflight, not a completed installer task.**

Audited 2026-11-03. Dependency PWS-0801 is done. Read the canonical contracts in their required
order and inspected the accepted internal dependency stage, release reviews, desktop/worker
composition, storage deletion operations, release documents and available host capabilities.

## Prepared

- [Proposed ADR 0018](../adr/0018-windows-installer-and-release-identity.md): concrete installer,
  identity, package-relative launch, prerequisite, signing, removal and recovery proposal, with
  alternatives and primary-source references. It grants no licence and freezes no publisher.
- [Qualification plan](../../packaging/windows/QUALIFICATION_PLAN.md): required clean-machine
  install/launch, offline workflow, signatures, preserve/reinstall/reopen, explicit data removal,
  interruption and external-file protection checks. Every result remains NOT RUN. Human work
  is designed around a small prepared session; no unfinished helper was handed to the user.
- Local read-only host receipt and a preservation baseline for all **2,036** pre-existing
  nonignored repository files, plus the original HEAD/index identities, under ignored
  `output/pws0802/`. The host receipt contains numeric OS/tool facts and booleans only, with no
  username, private absolute path, document content or certificate-store inventory.

The working tree already contains substantial accepted/unfinished work from earlier tasks.
No application code, lock, dependency inventory, licence asset, fixture, golden, original,
environment, Git index, HEAD or ref is changed by this audit. Task/bundle metadata changes
record this blocker only; the release interface and candidate evidence remain draft.

## Evidence and attempted alternatives

The reusable internal stage is `pws0801-dependency-stage-20261103-r3`; its source, 957 components,
3,153 files and historical hashes remain bound to PWS-0801. This audit did not rerun or relabel
its native smoke observations. See the [accepted result](pws0801-result.md) and
[release evidence](../release/RELEASE_EVIDENCE.md#pws-0801-resolved-internal-dependency-candidate).

The root has no first-party licence grant. Project metadata and release documents leave the
publisher, licence and signing facts unresolved. ADR 0017 deliberately permits first-party
`NOASSERTION` only for the internal stage. In addition, the exact retained pikepdf Microsoft DLL
has conditional distribution requirements recorded in the
[native review](../../packaging/release-reviews/native-components-v1.json), including protective
recipient terms and distributor obligations. Choosing a source licence or copying notices
does not resolve those separate conditions.

Considered reusing the already Microsoft-installed external runtime (the accepted route),
bundling Microsoft's runtime installer, replacing/removing every CRT object, using NSIS/MSI,
and making an unsigned internal installer. The external prerequisite avoids inventing new
redistribution rights but still needs clean-machine qualification. A bundled redistributable
requires its own demonstrated rights. PWS-0801 already records the failed all-CRT-removal
probe; it is not repeated or turned into a pass here. Installer technology or unsigned output
cannot supply the missing publisher's legal choices or approved production signatures.

Host preflight observed Windows `26300.9711` / `Professional`, 64-bit OS/process, unelevated.
Windows Installer and MakeCab commands are available; Inno/NSIS compilers, SignTool, Windows
Sandbox and Hyper-V's `Get-VM` are not discoverable through normal command entry points.
This is not proof that a test environment or signing service cannot be provisioned. It is not
a supported Windows edition/build matrix, clean-machine test or physical-device certification.

The current desktop defaults also require actual packaging changes: qpdf is a development
installation path and OCR uses explicit/environment paths. A normal installed launcher must
bind its own engines, preserve worker admission and verify actual standard-user worker launch.
The proposal and test plan identify this work; it has not been implemented in this audit.

## Smallest maintainer action

Provide the actual publisher/distributor name and chosen first-party product licence. Approve
the scoped Microsoft-component distribution-terms mechanism, including the distributor
obligation, or choose an alternative whose rights and runtime behavior can be demonstrated.
Name the approved signing service/process or public certificate identity; if none exists, say
so and keep the signed-release criterion unresolved. Keep all private keys, PFX files and
passwords outside chat, repository and agent command/log output.

The proposed application ID is `io.github.pikkujanne.paperworkshell`; the initial installer is
per-user Inno Setup 7.1.1 x64, no associations, with cases preserved by default. These choices
are reviewable in ADR 0018. No supported OS or public product version is invented.

## Verification

- `.venv\Scripts\python.exe tools/dev.py format-check` — PASS; 869 files already formatted.
- `.venv\Scripts\python.exe tools/dev.py lint` — PASS.
- `.venv\Scripts\python.exe tools/dev.py typecheck` — PASS; 869 source files.
- Canonical `test-unit` with process-local `PYTEST_ADDOPTS` selecting `validate_tasks`,
  `validate_docs` or `validate_portfolio_boundary` — PASS; 79 passed, 7,318 deselected,
  122 subtests passed in 19.61 seconds. This focused lane includes invalid-input/refusal
  cases; it is not a full-unit-suite result or installer coverage.
- Canonical `validate-docs` and initial `validate-task-graph` — PASS. Final metadata,
  schema, portfolio and preservation checks are recorded at closure.

No new executable behavior or schema was introduced, so document-processing, installer and
accessibility observations cannot be claimed from these source checks. No human test,
installer install/uninstall, signing or clean-machine launch was performed. Broad product
tests were not rerun for this documentation-only preflight, and no old result is relabelled.

## Security, privacy, accessibility and portfolio

No document parsing, logging, deletion or worker policy changes. The proposed removal design
uses the existing authenticated lifecycle deletion, retains unknown/busy/corrupt cases, and
never deletes external originals or exports. Its visible data choices require keyboard,
screen-reader and scaling verification when implemented. Existing accepted human evidence is
preserved and no human rerun is requested by this preflight.

PaperWorkShell stays standalone. No sibling repository, service, adapter, SDK, database,
content access or coordinated release is introduced. The release-interface impact is a link
to this unresolved local task; candidate facts remain TBD. PWS-0803 remains dependent on this
task. The next dependency-ready task under the selector, if the maintainer elects to proceed
while PWS-0802 is blocked, is **PWS-0804**; it was not started here.
