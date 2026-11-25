# AGENTS.md — PaperWorkShell Codex Operating Contract

This file governs coding-agent work in the PaperWorkShell repository. It is intentionally strict because the application processes sensitive, untrusted documents and must preserve user originals.

## 1. Read order before every task

Read these files in order before making changes:

1. `DECISIONS.md`
2. `ROADMAP.md`
3. `TASKS.yaml`
4. This `AGENTS.md`
5. For portfolio-sensitive, release, packaging, installer, update, data-location, or
   interoperability work, `THIRDLIFE_ALIGNMENT.md`
6. The code, tests, schemas, and ADRs relevant to the selected task

Then identify one task ID and state it in the work log or completion report. Do not start from a broad milestone description when a task exists.

## 2. Authority and conflict handling

Repository authority is:

1. Current explicit maintainer instruction
2. Frozen decisions in `DECISIONS.md`
3. Binding requirements in `ROADMAP.md`
4. The selected task in `TASKS.yaml`
5. This operating contract
6. Accepted tests and public interfaces
7. Other documentation and comments

Never code around a conflict. Preserve the higher-level contract and report the conflict. Do not edit a frozen decision unless the maintainer explicitly requests the decision-change procedure.

### 2.1 Project vacuum and portfolio boundary

PaperWorkShell is a standalone Team A / A1 product in the ThirdLife portfolio. The binding local
rule is `PWS-D042`; `THIRDLIFE_ALIGNMENT.md` explains its portfolio provenance and release
consequences. PaperWorkShell must remain complete when ThirdLife and every sibling application are
absent.

Unless an explicit ThirdLife B4 task authorizes work in the portfolio repository:

- Stay inside the PaperWorkShell repository. Do not browse, clone, edit, test against, or depend on
  sibling repositories, active branches, private fixtures, databases, or services.
- Do not create a shared SDK, runtime schema, user-content database, workspace, service,
  background agent, plugin framework, universal job engine, cross-repository import, or synchronized
  release requirement.
- Do not implement a ThirdLife adapter in PaperWorkShell. Future catalogue entries, profiles,
  compatibility rules, adapters, and workarounds belong to ThirdLife Deployment and Suite Assembly
  B4 and must consume exact frozen public artifacts.
- Do not add a feature, CLI option, URI, RPC operation, file association, or machine-readable export
  solely for a future sibling. It needs independently demonstrated PaperWorkShell user, testing,
  support, accessibility, or administration value and the normal local decision process.
- Prefer ordinary files, normal interactive launch, user-visible opening, and manual export/import.
  Removing a future adapter must always leave a complete normal PaperWorkShell path.
- Keep PaperWorkShell versions and migrations independent. ThirdLife must not inspect, own, or
  migrate PaperWorkShell cases, documents, OCR text, overlays, signatures, redaction state, or other
  private product data.
- Record non-gating assembly observations in
  `docs/portfolio/THIRDLIFE_FUTURE_NOTES.md`; recording a note creates no task or dependency.
- Treat `docs/release/RELEASE_INTERFACE.md` as versioned human-readable documentation, not code, a
  shared API, or evidence that a compatibility cut exists. Leave unproved release facts `TBD`.
- Stop and use the frozen-decision procedure if a suite idea would change the product boundary,
  data ownership, or standalone architecture.

## 3. Task selection algorithm

Unless the maintainer names a task:

1. Parse `TASKS.yaml`.
2. Exclude tasks whose status is `done`, `deferred`, or `blocked`.
3. A task is ready only when every `depends_on` task is `done`.
4. Prefer the lowest incomplete milestone.
5. Within that milestone, prefer `P0`, then `P1`, then `P2`.
6. Prefer `critical_path: true`.
7. Break ties by ascending task ID.
8. Work on one task at a time.

Do not begin a later milestone because it appears easier. Gate tasks are evidence and integration tasks, not substitutes for incomplete implementation tasks.

### 3.1 Thread workflow

**PWS-0813 TRACE replacement (maintainer instruction, 2026-11-25).** Follow the
[TRACE continuation](docs/development/pws0813-trace.md). The approved design replaces
the normal native presentation. Continue across implementation, private build,
installed verification and delivery in the dedicated checkout. The internal stages
and old-interface human session are not prerequisites or handoff points. Keep earlier
PWS-0811 evidence and unresolved results under their own candidate and criteria.
Use the established authorized offline Windows VM and prepare only subjective review
that would change a product decision.

**PWS-0811 installed work (maintainer instruction, 2026-11-12).** Start from the
[current installed continuation](docs/development/pws0811-repairs-20261106.md#current-continuation--installed-application)
and continue through related installed checks, diagnosis, narrow repairs, focused tests, private
builds and affected installed verification while useful context remains. Normal standard-user
application interaction in the existing offline PWS-Test VM is authorized. Use the candidate,
session and foreground identity checks that apply to the action. Sampler qualification applies
to claims made from that sampler. Ordinary application interaction and correctness testing can
continue while a timing result remains inconclusive.

Preserve immutable artifacts, R16 and R18 checkpoints, and earlier evidence. Retain evidence
outside the VM before a restore. Restore for a concrete baseline or recovery need and record the
chosen active state. Record actual application results and remaining product work in the current
continuation. End at substantive completion, when context is insufficient for another coherent
unit, or when an external limitation prevents useful progress. Supply a complete next-thread
prompt when a handoff is needed. This PWS-0811 workflow governs the remaining work in place of
the bounded thread procedure below.

Before a diagnostic experiment, record the product question, the materially changed condition or
hypothesis, and which decision each result would change in the active continuation. Use existing
evidence when it answers the question. Count consecutive cycles on the same question across threads
and renamed experiments. New timestamps, seals, directories, builds, or observer variants alone do
not count as progress. A predeclared finite repeatability series counts as one experiment. After two
consecutive cycles that close no required check and materially narrow no product decision, record
“Investigation stalled; repeated approach stopped.” Continue other authorized work or pursue a
different evidence-supported repair. Verification after a relevant fix remains allowed. Keep failed
checks failing and keep investigation, fixes, focused tests, installation and verification together
while useful work remains.

For other tasks, use the bounded thread workflow:

Each Codex thread handles one bounded step, including its necessary checks. Work autonomously
within that step and keep parallel work inside it. Record the result and the exact next step
in the current continuation record, then stop. Janne opens a fresh thread for the next step.
Functional correctness and reliable processing take priority. Keep deferred UI polish visible in
the backlog. A completed step supplies evidence only for its own scope.

Every completed or partial step must end with a plain-language final reply that prints the complete
ready-to-paste next-thread prompt. Saving that prompt only in a file, or saying that the file
contains it, is insufficient. Include actual task IDs, paths, candidate identity, and branch/commit
state. Each prompt authorizes exactly one bounded step and ends with a STOP instruction.

If the step cannot be completed, save a compact partial checkpoint with the unfinished action,
reproduction, changed files, verification commands and results, and the next exact action.
The next thread resumes that step. Account for all edits and stop any owned background jobs
before handing over.

## 4. Required work loop

For each task:

1. **Orient**
   - Read the task, dependencies, decision references, acceptance criteria, and verification list.
   - Inspect the relevant code and recent changes.
   - Confirm that the repository is not carrying unrelated uncommitted work before modifying shared files.

2. **Plan**
   - Define the smallest coherent implementation that satisfies the task.
   - Identify security, privacy, accessibility, migration, and licensing effects.
   - For portfolio-sensitive work, state the standalone-boundary, data-ownership, and
     release-interface effects.
   - Name the tests that will prove completion.

3. **Implement**
   - Keep changes inside the selected task's scope.
   - Prefer pure domain logic and narrow adapters.
   - Add stable error codes with plain-language user messages.
   - Avoid speculative abstractions not required by the task or an imminent dependency.

4. **Verify**
   - Run formatting, static analysis, typing, unit tests, and relevant integration/security/accessibility tests.
   - Exercise at least one failure path.
   - Inspect generated outputs when the task changes document rendering or export.

5. **Document**
   - Update code documentation, schemas, ADRs, and user/developer docs affected by the task.
   - Update the task status and evidence in `TASKS.yaml` only after verification succeeds.
   - Mark newly unblocked tasks `ready` where the repository workflow uses explicit readiness.

6. **Report**
   - Provide the task ID and outcome.
   - List changed files.
   - List commands run and their results.
   - Identify residual risks or intentional deferrals.
   - Name the next dependency-ready task.

## 5. Task-state rules

Allowed task states are defined in `TASKS.yaml`.

- `planned`: not started; dependencies may be incomplete.
- `ready`: dependencies are complete and work may start.
- `in_progress`: actively being implemented.
- `review`: implementation is complete but required review or gate evidence remains.
- `done`: every acceptance criterion and applicable definition-of-done item is satisfied.
- `blocked`: an external or contractual blocker is recorded in `blocker`.
- `deferred`: explicitly removed from the active release path by maintainer decision.

State transitions:

```text
planned -> ready -> in_progress -> review -> done
                    |             |
                    +-> blocked <-+
planned/ready -> deferred   (maintainer-approved only)
```

Never mark a task `done` because code exists. Tests, documentation, and evidence must also be complete. Never change a gate task to `done` while any required milestone task is incomplete.

## 6. Architecture rules

### 6.1 Layering

- `domain` contains pure models and rules. It must not import PySide6, pikepdf, OCRmyPDF, renderer implementations, filesystem UI dialogs, or installer code.
- `app` coordinates use cases through interfaces.
- `worker` owns process lifecycle, RPC, cancellation, limits, and progress.
- `pdf`, `imaging`, and `ocr` are adapters around document libraries.
- `storage` owns case workspace and atomic persistence.
- `overlays` owns normalized overlay intent and export rendering.
- `preflight` produces typed findings; it does not display dialogs.
- `export` plans, writes, reopens, verifies, and commits derivatives.
- UI modules translate domain/application state into accessible widgets and user actions.

Add an import-boundary test when a new package is introduced.

### 6.2 Interfaces

- Keep third-party objects behind adapters.
- Use typed dataclasses, enums, protocols, or validated models for boundaries.
- Version manifest, template, and RPC payloads.
- Return stable internal error codes plus sanitized parameters; do not pass raw library exception text directly to the UI.
- Long operations expose progress, cancellation, timeout, and deterministic cleanup.

### 6.3 Determinism

Given the same immutable sources, manifest, template, engine versions, and export profile, page ordering and semantic output decisions must be reproducible. Byte-for-byte reproducibility is desirable but not required when libraries embed unavoidable nondeterministic metadata; such fields must be normalized where practical and documented.

## 7. Security and privacy rules

Treat every PDF and image as hostile.

### 7.1 Mandatory invariants

- Never modify an imported original.
- Never overwrite an external source path.
- Never trust a filename extension as the media type.
- Never parse untrusted full document content in the GUI process.
- Never allow the document worker network access.
- Never invoke external tools through a shell string.
- Never interpolate filenames, template values, OCR text, or user text into commands.
- Never place OCR text, document text, form values, names, addresses, original filenames, full paths, metadata values, thumbnails, or signature images in routine logs.
- Never label an overlay or black rectangle as permanent redaction.
- Never report an edited digitally signed PDF as still signed.
- Never permit executable template content.
- Never use real applicant documents as repository fixtures.

### 7.2 Input handling

Input adapters must enforce configured limits before expensive processing where possible:

- Byte size
- Page count
- Image width, height, and total pixels
- Decompression expansion
- Nesting or recursion
- CPU time
- Memory
- Temporary storage

Reject path traversal, device paths, unsafe archive names, and unsupported content with a stable error and no case corruption.

### 7.3 Worker process

- Use a minimal environment.
- Grant access only to the active case workspace and required read-only executables/resources.
- Deny network access using the strongest supported Windows mechanism available at the current milestone.
- Terminate the worker after severe parser faults or protocol violations.
- Validate every inbound and outbound RPC message.
- Treat worker output as untrusted until validated by the client.

### 7.4 Logs and diagnostics

Routine logs may contain:

- Internal case/document/page/operation IDs
- Engine and schema versions
- Operation start/end and duration
- Numeric counts and resource usage
- Generic error codes
- Sanitized stack traces with paths and values removed

Add tests that inspect logs for prohibited fixture tokens. Diagnostic exports remain opt-in and display an inventory before creation.

### 7.5 Redaction work

Only tasks in Milestone 6 may implement permanent redaction behavior. During earlier milestones, ordinary shapes and highlights must be visually and terminologically distinct.

A redaction implementation is incomplete until supported extraction and revision checks prove that original affected-page content is absent. Any verification failure blocks export.

## 8. Accessibility rules

Accessibility applies to every UI task, not only Milestone 7.

For every new control or workflow:

- Provide keyboard access and a logical tab order.
- Provide an accessible name, role, state, and description where needed.
- Do not rely on color, pointer hover, drag-and-drop, or animation alone.
- Preserve visible focus.
- Support display scaling and text enlargement.
- Announce progress, cancellation, completion, and errors.
- Avoid timeouts during review.
- Make long operations cancelable.
- Use plain-language messages and place focus on actionable recovery.

Tests should use the most direct level available: widget/unit checks, automated accessibility inspection, and manual NVDA scenarios at milestone gates.

## 9. Testing contract

### 9.1 Test placement

- Pure behavior: `tests/unit/`
- Cross-component and worker behavior: `tests/integration/`
- Hostile input, limits, egress, logging, and redaction: `tests/security/`
- Keyboard, labels, focus, scaling, and announcements: `tests/accessibility/`
- Synthetic source documents: `tests/fixtures/synthetic/`
- Approved visual references: `tests/golden/`

### 9.2 Required test behavior

- Tests must be deterministic and independent of the user's documents.
- All future human testing follows [the human-testing policy](docs/development/human-testing.md).
  Minimize setup, transcription, and repeated work. Reuse or automatically collect known environment
  metadata with its provenance. An explicit maintainer statement that the environment is unchanged
  authorizes reuse without another confirmation or numeric-version prompt; never invent missing facts
  or copy an earlier test outcome into a new observation.
- This policy also applies to historical human-test helpers when next used. Update or prefill their
  interaction before asking the person to use them. Existing accepted helpers need no bulk retrofit,
  and accepted M3/M5 evidence must not be rerun merely to retrofit ergonomics.
- Human tests cover new or affected behavior. Preserve accepted evidence and do not repeat it solely
  to improve test ergonomics. Required acceptance, security and release-gate checks still apply.
- Human testing must be straightforward and easy across the whole session. Excessive effort and
  repetition reduce observation quality. Splitting an arduous script into many short prompts does
  not make it human friendly. Minimize total human actions, repeated review, context switching,
  memory demands and transcription; consolidate logically related observations and automate
  mechanically checkable work, reserving human effort for necessary interaction and judgment.
- Before handing over a human test, walk through its complete journey across every required reader
  or device and record the estimated total human actions and active time. Include setup, save/reopen
  cycles, recording and recovery. If the journey remains arduous, redesign its method, fixtures or
  automation before asking the person to run it. Do not silently waive mandatory coverage or repeat
  completed valid observations to make that redesign convenient.
- Finish, test and freeze the helper before asking for human observations. Provide one stable entry
  point, one plain-language action at a time, its expected outcome, and explicit Copy/Open-folder
  actions for fixed queries and paths. Never ask the human to calculate hashes, edit JSON, or type
  paths. Make synthetic visual targets comfortably large and their intended alignment obvious unless
  size or offset is itself under test.
- Persist each completed human step immediately. Pause and resume without losing valid observations;
  retry invalid entries and recover missing-file steps without wiping earlier work. Keep human
  recording and status fast; run expensive automated verification separately and bind its receipts to
  the exact observations, artifacts, environment and implementation. Missing or stale evidence cannot
  become a pass. Clipboard helpers write only after an explicit Copy action and never read clipboard
  contents.
- Tests must clean up case and temporary directories.
- Network-dependent tests are prohibited for core processing.
- External executables and language packs should have capability-detection tests and clear skips only where the task permits optional availability.
- A bug fix includes a regression test that fails before the fix.
- Schema changes include old, current, future-version, and invalid fixtures.
- Export changes include reopen and render checks.
- Worker changes include cancellation, timeout, termination, and malformed-message tests where applicable.

### 9.3 Golden files

Do not refresh golden outputs merely to make tests pass. Explain why the output changed, inspect it, and record approval in the task evidence. Keep golden artifacts synthetic and as small as practical.

### 9.4 Resource-budget contract

Resource budgets are part of the definition of done for changes affecting import, rendering, thumbnails, OCR, compression, export, worker lifecycle, caching, queues, or temporary files.

- New unbounded caches, queues, executors, subprocess counts, concurrency, or whole-document/full-resolution materialization are prohibited.
- Use the central versioned `ResourcePolicy`; do not scatter replacement constants or make a profile change only a report label.
- Run the relevant constrained-profile tests for every resource-sensitive processing change.
- Exercise low-disk/disk-full, cancellation, cleanup, and recovery paths when storage or long operations change.
- Do not raise a budget merely to make a regression pass. Diagnose and optimize first, then document any justified policy amendment with evidence.
- Hardware-support claims must match actual evidence. Constrained-profile results prove bounded software behavior, not certification of a named CPU, RAM amount, GPU, SSD, HDD, VM, or physical device.
- Optional physical-device testing is useful but non-gating under `PWS-D041`.

## 10. Quality commands

`PWS-0001` and `PWS-0003` establish the exact commands. The repository should expose stable developer entry points for at least:

```text
format
lint
typecheck
test-unit
test-integration
test-security
test-accessibility
validate-schemas
validate-task-graph
validate-portfolio-boundary
```

Use repository-defined commands rather than inventing one-off invocations. Until those entry points exist, the bootstrap task may use direct Python module commands and must document them.

A task is not complete while a required command fails. Do not suppress a check globally to make an unrelated task pass.

## 11. Dependency rules

Before adding or upgrading a dependency:

1. Explain the required capability.
2. Check whether the standard library or an existing dependency is sufficient.
3. Record license, source, version constraint, maintenance signal, binary packaging impact, and security boundary.
4. Pin or lock it according to repository policy.
5. Add adapter and contract tests when it processes untrusted content.
6. Update the license inventory and SBOM inputs.

Do not introduce AGPL or otherwise distribution-altering components without an explicitly approved decision change.

## 12. Schema and migration rules

- Never edit an existing released schema meaning in place without a version transition.
- Unknown future schema versions fail before mutation.
- Migrations operate on copies or transactional state.
- Migration tests preserve source hashes, page references, operation order, overlay intent, checklist assignments, and constraints.
- Keep example manifests free of sensitive data.

## 13. UI and wording rules

Use exact product terminology:

- “Place signature image,” not “digitally sign,” for a visual mark.
- “Permanently remove information” only for the gated redaction feature.
- “Check complete” only for checks actually performed.
- “Needs review” for OCR, classification, blur, duplicate, clipping, and similar heuristics.
- “Unsigned derivative” when a signed source is modified.

Do not claim portal acceptance, legal validity, OCR certainty, secure erasure, or PDF/UA conformance unless a separately approved feature proves that claim.

## 14. Git and change hygiene

- Keep one task per branch/change by default.
- Do not combine broad refactoring with feature work.
- Do not delete or rewrite unrelated maintainer work.
- Do not commit caches, local cases, OCR language data, build outputs, secrets, certificates, or real documents.
- Use descriptive commits that include the task ID, for example: `PWS-0104 preserve immutable imports`.
- Keep generated lockfiles and schema outputs only when repository policy requires them.
- Do not force-push shared branches or rewrite published history without explicit instruction.

## 15. Blocker policy

Make a best effort to resolve ordinary implementation uncertainty through code inspection, tests, documentation, and a narrow ADR. Mark a task `blocked` only when:

- A frozen decision must change.
- A required license or redistribution right is unacceptable or unknown.
- A security property cannot be demonstrated with the current design.
- A required platform capability is unavailable and no compliant fallback exists.
- Needed maintainer-owned credentials, signing material, or external approval are absent.

A blocker record must include the task ID, evidence, attempted alternatives, affected decisions, and the smallest maintainer action needed.

## 16. Definition of done checklist

Before marking a task done, confirm:

- [ ] Dependencies are done.
- [ ] Acceptance criteria are met.
- [ ] Applicable frozen decisions are preserved.
- [ ] Tests cover success and failure paths.
- [ ] Quality commands pass.
- [ ] No prohibited data appears in code, fixtures, logs, screenshots, or diagnostics.
- [ ] Accessibility requirements are addressed for UI changes.
- [ ] Security/resource limits are addressed for document-processing changes.
- [ ] Documentation, schemas, and ADRs are updated.
- [ ] Portfolio-sensitive work preserves standalone operation, creates no live sibling dependency,
      and records its release-interface impact.
- [ ] `TASKS.yaml` status and evidence are updated.
- [ ] The completion report names the next ready task.

## 17. Completion report template

Use this structure after each task:

```markdown
## Task result — <TASK_ID>: <title>

**Status:** done | review | blocked

### Implemented
- ...

### Changed files
- `path`: reason

### Verification
- `<command>` — pass/fail and key result

### Security/privacy/accessibility notes
- ...

### Portfolio boundary and release-interface notes
- Standalone usefulness, data ownership, sibling dependencies, and release-document impact

### Residual risks or deferrals
- None, or a precise statement linked to a task/ADR

### Task graph
- Updated `<TASK_ID>` to ...
- Next ready task: `<NEXT_ID>`
```
