# ADR 0019: Limit PaperWorkShell application and release scope to Windows 11 x64

- Status: Accepted, version 1.0
- Proposed: 2026-11-04
- Accepted: 2026-11-04, by explicit maintainer direction
- Completion scope: The original signing prerequisite was superseded on 2026-11-26 by PWS-D044 and [ADR 0023](0023-unsigned-product-completion.md).
- Task: PWS-0802; applies to PWS-0803 and the M8 release boundary
- Decisions affected: PWS-D004 superseded for current platform scope; PWS-D043 added
- Decisions preserved: PWS-D005, PWS-D009, PWS-D013, PWS-D030, PWS-D034, PWS-D036, PWS-D037, PWS-D040, PWS-D041 and PWS-D042

## Context and authority

PWS-D004 originally established Windows-first delivery without selecting one processor architecture. ADR 0018 later selected x86-64 binaries and an unqualified future x64-emulation route for Windows 11 on ARM64. The initial installer admitted native x64 only; no ARM64 native or emulated application result was proved. Earlier reports therefore described ARM qualification as unavailable.

The maintainer now explicitly selects **Windows 11 x64 only**. Native ARM64 and x64 emulation on ARM64 are outside project scope and must not be PWS-0802, PWS-0803 or public-release prerequisites. This is an approved product-scope choice, not a test workaround or a claim about ARM compatibility. The ADR is proposed and accepted within the same explicitly authorized change, following the approved-amendment precedent in ADR 0007 and ADR 0008. No additional approval is pending for this scope decision.

The pre-change source/evidence baseline is retained under `output/pws0802/x64-scope-20261104/baseline-v1.json`, SHA-256 `9687373e51b82102a7fa7c0b4e1b1cd10d79930a4335dc46083dbd9d523f8ae9`. The prior 2,126-file source, task bytes, R7 artifact/audit/closure evidence and Git state belong to that immutable earlier epoch. This amendment does not rewrite them.

## Decision

Add PWS-D043 and retain PWS-D004's original text as **SUPERSEDED** history. Current application, package, update and required qualification scope is Windows 11 on x86-64 processors (x64). Other operating-system targets, native ARM64 and x64 emulation on ARM64 are excluded. Portable internal layers and existing development checks do not create additional supported application platforms.

No ARM build, native/emulated test environment, matrix cell or qualification result is required to complete PWS-0802, start dependency-ready PWS-0803 work, or satisfy an M8/public-release gate. Do not replace the removed prerequisite with a mandatory deferred ARM task. This decision creates no future ARM delivery commitment.

Exact Windows 11 editions/builds and supported packaged behavior remain evidence-based.
Authenticated update admission, worker isolation, privacy, accessibility, preservation, recovery,
licensing and resource-budget requirements stay in force. The required gates apply within this
platform boundary under the unsigned completion policy in PWS-D044.

The existing `x64os` installer admission and x64 runtime design already match the selected target. Historical R7 outer bytes, embedded product metadata, inventory, source archives, audits, failures, successful observations and signatures remain untouched. Current policy and validation must distinguish those authenticated historical records from a current support declaration. In particular, an old ARM-plan label is not current ARM support or permission to enable `x64compatible`.

## Alternatives considered

1. **Keep the wider Windows/ARM plan as a release prerequisite.** Rejected because it contradicts the maintainer's selected product scope. Missing ARM evidence must remain unproved, rather than blocking the x64-only project indefinitely.
2. **Remove ARM from the present gate but create a mandatory later ARM task.** Rejected because it would retain the excluded work as a project commitment under another name.
3. **Windows 11 x64 only, with historical evidence preserved.** Accepted. It gives recipients and future agents one explicit target while retaining truthful qualification and provenance.

## Effects and preservation

- **Users and packaging:** Only Windows 11 x64 is an application target. Each supported edition/build still needs its actual release evidence. Existing native-x64 rejection of other architectures is not relaxed. No installer is built or republished by this documentation change.
- **Security and privacy:** There is no change to worker filesystem/network restrictions, structured subprocesses, resource limits, private data ownership, logging or opt-in diagnostics.
- **Accessibility:** Keyboard/NVDA and scaling requirements remain. All actual R7 observations and earlier intermittent reader-context gaps are retained; this decision supplies no blanket accessibility or first-focus-reliability claim.
- **Licensing:** MIT scope, third-party rights, Microsoft recipient terms, Qt replacement rights and accepted component bytes remain unchanged. No dependency or provider is added.
- **Data and migration:** No case, template or RPC format changes. There is no data migration, source modification or change to update/repair identity. PWS-0804 and PWS-0805 work remains exact.
- **Standalone boundary:** PWS-D042 is unchanged. There is no ThirdLife/sibling source, runtime, service, data-access or release dependency.
- **Prior reader evidence:** Accepted observations of exported PDFs in external readers remain historical file-interoperability evidence. They do not establish another supported PaperWorkShell application platform or a new mandatory architecture task.

## Coordinated affected surfaces

| Surface | Required change in this authorized change |
|---|---|
| `DECISIONS.md` | Register version 0.4.0, retain superseded D004 and add frozen D043 |
| `ROADMAP.md` | Version 0.4.0; narrow platform, M8 matrix and current compatibility obligations to Windows 11 x64 |
| `CODEX_START_PROMPT.md`, `README.md` | State the current boundary and prevent revival of historical ARM prerequisites |
| ADR 0018 | Retain the old unqualified ARM-emulation choice as superseded history and link this replacement |
| `TASKS.yaml` | Add D043 references and remove ARM prerequisites/blockers from PWS-0802, PWS-0803 and affected M8 scope without inventing a pass or future mandatory task |
| Current product metadata, qualification plan and consistency validators | Declare x64-only current policy; keep exact historical R7 inputs distinguishable; reject current ARM support claims |
| Release/development documentation and `BUNDLE_MANIFEST.json` | Publish the narrowed current scope, preserve prior evidence and refresh current source checksums |

No released case/template/RPC schema transition is needed. Existing metadata consistency and governance validators must cover the current scope and refusal of contradictory ARM claims. There is no runtime implementation or golden-output change in this amendment.

## Verification and completion boundary

Use the repository's documentation, task-graph, schema, portfolio-boundary and affected metadata-validator checks after the coordinated edits settle. Compare changed paths and accepted evidence against the independent pre-change baseline. R7 artifacts and their qualification receipts must keep their original hashes; new source-policy receipts form a separate epoch.

This decision removes only the excluded architecture obligation. It does not mark PWS-0802 done, grant trusted signing, turn the engineering minimum build into a supported-build certificate, or complete the remaining finite Windows 11 x64 release matrix.

## Rollback or later scope change

Keep this ADR, the superseded D004 text, ADR 0018's historical architecture paragraph and the pre-change baseline. Any later expansion needs another explicit maintainer-approved decision and synchronized current governance, task, metadata and qualification changes. Do not overwrite this record, reinterpret historical unrun cells as passes, or silently restore an ARM prerequisite.
