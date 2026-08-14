# PaperWorkShell Codex Prompts

These prompts are designed for a Codex session operating in the PaperWorkShell repository. Paste one prompt at a time. The repository files remain authoritative; the prompt does not grant permission to override frozen decisions.

---

## 1. First repository session

Use this once after placing the roadmap bundle at the repository root.

```text
You are starting the PaperWorkShell repository.

Read, in this order:
1. DECISIONS.md
2. ROADMAP.md
3. TASKS.yaml
4. AGENTS.md
5. README.md

Treat DECISIONS.md and ROADMAP.md as binding. Parse TASKS.yaml as a dependency graph. Do not implement document-processing features yet.

Execute only task PWS-0001, “Bootstrap repository and pin the development toolchain.”

Before editing:
- Inspect the existing repository and preserve any unrelated maintainer work.
- Confirm the current task dependencies and decision references.
- State the exact files you plan to add or change.
- Select and document a supported CPython minor and dependency workflow suitable for a Windows-first PySide6 application; record the evidence and choice in an ADR.

Implementation expectations:
- Establish project metadata and the internal Python package identifier `paperworkshell` without changing the user-facing name PaperWorkShell.
- Add the minimal development bootstrap, ignore rules, and reproducible environment/lock mechanism required by the selected workflow.
- Define stable local entry points for at least bootstrap, format, lint, typecheck, unit test, schema validation, and task-graph validation, even when some are initially no-op scaffolds that fail clearly until PWS-0003 completes them.
- Do not add PDF, OCR, renderer, installer, or cloud dependencies in this task.
- Do not add real document fixtures.
- Do not edit frozen decisions.

Verification:
- Run the documented bootstrap from a clean environment as far as the current machine permits.
- Confirm the package can be imported.
- Validate TASKS.yaml and its dependency graph.
- Run every quality command that exists after this task and report exact results.

On success:
- Update PWS-0001 to done in TASKS.yaml and record concise evidence.
- Mark dependency-ready tasks ready according to AGENTS.md.
- Provide the AGENTS.md completion report, including the next ready task.

On a genuine blocker:
- Do not silently choose a conflicting architecture.
- Record the blocker with evidence and the smallest maintainer action needed.
```

---

## 2. Start the next ready task

```text
Continue PaperWorkShell development from the repository state.

Read DECISIONS.md, ROADMAP.md, TASKS.yaml, and AGENTS.md. Parse the task graph and select the next task using the AGENTS.md task-selection algorithm. Work on exactly one task.

Before editing, report:
- selected task ID and title
- why it is dependency-ready
- acceptance criteria
- relevant frozen decisions
- planned files and verification commands

Implement the smallest coherent change that satisfies the task. Preserve immutable-source, no-content-logging, worker-isolation, accessibility, and non-destructive-editing rules. Add success and failure-path tests. Run all applicable repository quality commands.

Only after verification passes, update TASKS.yaml status/evidence and mark newly unblocked tasks ready. End with the AGENTS.md completion report and name the next ready task.
```

---

## 3. Implement a named task

Replace `<TASK_ID>`.

```text
Implement PaperWorkShell task <TASK_ID> and no unrelated task.

First read DECISIONS.md, ROADMAP.md, TASKS.yaml, AGENTS.md, and all code/tests/ADRs touched by the task. Confirm that every dependency of <TASK_ID> is done. When a dependency is not done, do not bypass it; report the graph conflict.

Summarize the task's objective, deliverables, acceptance criteria, decision references, security/privacy/accessibility impact, and verification plan before editing.

Then implement the task with narrow, reviewable changes. Keep third-party document libraries behind adapters, keep untrusted parsing out of the GUI, use structured subprocess arguments, preserve originals, and avoid document content in logs. Add regression and failure-path coverage appropriate to the task.

Run all applicable quality and task-specific verification commands. Inspect generated document output when relevant. Update documentation, schemas, ADRs, and TASKS.yaml in the same change. Do not mark the task done unless every acceptance criterion is evidenced.

Finish with the AGENTS.md completion report.
```

---

## 4. Resume an interrupted task

Replace `<TASK_ID>`.

```text
Resume PaperWorkShell task <TASK_ID> from the current working tree.

Read DECISIONS.md, ROADMAP.md, TASKS.yaml, AGENTS.md, the task entry, and the current diff. Do not assume the partial implementation is correct.

First reconstruct the state:
- summarize completed and incomplete acceptance criteria
- identify unverified code
- identify unrelated changes that must be preserved but excluded
- identify failed or missing tests
- check whether the task state in TASKS.yaml matches reality

Continue only the selected task. Prefer finishing or safely reverting incomplete pieces over layering new work on an uncertain foundation. Run all applicable verification, update task evidence, and produce the AGENTS.md completion report.
```

---

## 5. Review a completed task or pull request

Replace `<TASK_ID>` and optionally `<BASE_REF>`.

```text
Review the implementation of PaperWorkShell task <TASK_ID> against DECISIONS.md, ROADMAP.md, TASKS.yaml, and AGENTS.md. Use <BASE_REF> as the comparison base when provided.

Do not begin by rewriting the code. Inspect the diff, task acceptance criteria, tests, schemas, ADRs, logging behavior, worker boundaries, accessibility behavior, and failure handling.

Report findings in severity order:
- release/security blocker
- correctness defect
- privacy or logging defect
- accessibility defect
- missing acceptance evidence
- maintainability or test gap

For every finding, cite the exact file and line or symbol, explain the violated requirement, and describe the smallest compliant fix. Verify whether tests would catch the problem. Explicitly state when no material findings remain.

Do not approve a gate task while required milestone tasks, threat checks, or manual evidence are missing.
```

---

## 6. Fix a defect without expanding scope

Replace `<BUG_DESCRIPTION>` and optionally `<RELATED_TASK_ID>`.

```text
Fix this PaperWorkShell defect: <BUG_DESCRIPTION>
Related roadmap task: <RELATED_TASK_ID>

Read the binding documents and inspect the smallest relevant code path. Reproduce the defect with a synthetic fixture or deterministic test before changing implementation. Confirm whether the defect exposes source mutation, document-content logging, worker escape, output corruption, signature confusion, redaction risk, or accessibility regression; treat those as release blockers.

Implement the narrowest root-cause fix, add a regression test that fails before the change, and avoid unrelated refactoring. Run all applicable quality and integration/security checks. Update task evidence or create a narrowly scoped follow-up task entry when the defect is not covered by an existing task.

Finish with changed files, reproduction, verification, residual risk, and task-graph effect.
```

---

## 7. Security-sensitive implementation prompt

Use for worker isolation, import, export, diagnostics, compression, redaction, updates, or signing-related work.

```text
Perform the selected PaperWorkShell security-sensitive task under an adversarial-input model.

Read DECISIONS.md, ROADMAP.md security invariants, TASKS.yaml, AGENTS.md, the threat model, and relevant ADRs. Enumerate trust boundaries, attacker-controlled values, resource-exhaustion paths, persistence paths, subprocess boundaries, and log/diagnostic outputs before editing.

The implementation must:
- preserve immutable originals
- validate content and structured messages
- avoid shell interpolation
- keep document processing out of the GUI
- deny worker network access according to the current platform design
- enforce time, memory, page, pixel, and temporary-storage limits where applicable
- emit stable sanitized errors
- clean up safely after cancellation, timeout, crash, and disk-full conditions
- add negative tests and prohibited-log-token tests

For redaction work, do not treat visual appearance as proof. Exercise text extraction, search, image extraction, content/revision inspection, metadata, attachments, annotations, form values, OCR exclusion, multiple viewers, and resave behavior. Block export on any verification failure.

Run the full relevant security suite and provide evidence for every acceptance criterion. Do not downgrade a failed security check to a warning merely to complete the task.
```

---

## 8. Run a milestone gate

Replace `<GATE_TASK_ID>`.

```text
Run PaperWorkShell milestone gate <GATE_TASK_ID> as an evidence review, not as a feature-development shortcut.

Read the gate task, milestone section in ROADMAP.md, all prerequisite tasks in TASKS.yaml, decision references, ADRs, test reports, and current repository state.

First verify that every required milestone task is done. Then execute the milestone's full automated test set and the documented manual demonstration. Check security, privacy, accessibility, packaging, performance, failure recovery, and documentation evidence applicable to the milestone.

Create or update a concise gate report under docs/gates/ containing:
- commit/revision tested
- environment and engine versions
- prerequisite task status
- commands and results
- manual scenarios and results
- known limitations that are within frozen scope
- unresolved defects and severity
- explicit pass or fail conclusion

A gate fails when any required task or release-blocking criterion is incomplete. Do not waive criteria without an approved decision change. Update TASKS.yaml only after the report supports the state.
```

---

## 9. Propose a frozen-decision change

Use only when the maintainer asks for a scope or architecture change.

```text
Prepare a PaperWorkShell decision-change proposal; do not implement it yet.

Read DECISIONS.md, ROADMAP.md, TASKS.yaml, AGENTS.md, relevant code, ADRs, and test evidence. Identify the exact frozen decision IDs and roadmap sections affected.

Create a proposed ADR that contains:
- current decision and observed problem
- concrete evidence
- user impact
- security, privacy, accessibility, licensing, packaging, and migration impact
- at least two viable alternatives
- recommended replacement and rationale
- exact edits required in DECISIONS.md, ROADMAP.md, TASKS.yaml, schemas, prompts, and tests
- rollback strategy

Leave the existing frozen decision unchanged until explicit maintainer approval. Do not make code changes that presuppose approval.
```

---

## 10. Prepare a concise project status report

```text
Produce a PaperWorkShell project status report from the repository, not from memory.

Read TASKS.yaml and validate its graph. Read milestone gates, recent ADRs, and current test results. Report:
- current roadmap and task-graph version
- latest completed gate
- tasks done/in progress/blocked by milestone
- next five dependency-ready tasks
- critical-path risks
- failed or missing quality/security/accessibility checks
- frozen decisions under active pressure, without proposing silent changes
- recommended next task according to AGENTS.md

Keep claims evidence-based and link each blocker or risk to a task, test, ADR, or gate report.
```

---

## 11. Resource-sensitive implementation or regression review

```text
Review or implement this PaperWorkShell resource-sensitive change: <CHANGE>
Related task: <TASK_ID>

Read DECISIONS.md, ROADMAP.md, TASKS.yaml, AGENTS.md, docs/performance/LOW_RESOURCE_POLICY.md, and the central ResourcePolicy before editing. Identify every affected memory, cache, queue, concurrency, process, pixel, I/O-chunk, temporary-space, heartbeat, cancellation, and cleanup budget.

Prove that the selected normal and constrained profiles apply real enforced values rather than labels. Reject new whole-file reads of untrusted large inputs, eager all-page rendering, full-packet raster retention, unbounded queues/executors, per-page processes/threads, caches without byte limits, and temporary files outside approved scopes.

Add the lowest-level deterministic regression first, including at least one budget/failure boundary. Run the relevant constrained-profile, low-disk/disk-full, slow-I/O, cancellation, recovery, worker-isolation, report-privacy, and architecture tests. Do not raise a budget merely to make a regression pass; diagnose retained memory/work, optimize, or reject oversized input first.

Report measured software budgets honestly. Never describe the result as emulation or certification of a named CPU count, RAM size, GPU, SSD, HDD, VM, or physical computer unless that exact environment was actually tested. Physical-device smoke testing is optional and non-gating.
```

---

## 12. Review a portfolio-sensitive task

Use this before work involving releases, installers, updates, launch/open behavior, data locations,
support artifacts, interoperability, or a proposed suite-facing surface.

```text
Review this PaperWorkShell task for ThirdLife portfolio-boundary impact: <TASK_OR_CHANGE>

Read DECISIONS.md, ROADMAP.md, TASKS.yaml, AGENTS.md, THIRDLIFE_ALIGNMENT.md, and the active task.
Remain inside the PaperWorkShell repository; do not inspect a sibling repository or active branch.

Before editing, confirm that the work:
- is independently useful to PaperWorkShell users, testing, support, accessibility, or administration
- preserves complete standalone operation with ThirdLife absent
- creates no live sibling source, runtime, database, service, build, test, or release dependency
- uses ordinary files, public packaged behavior, normal launch/opening, and manual export/import first
- does not add a speculative shared SDK, schema, service, CLI, URI, RPC, plugin framework, or handoff format
- leaves every future ThirdLife adapter optional, removable, version-bounded, and owned by B4
- keeps data storage/lifecycle, versions, migrations, and deletion implementation product-local while users retain control of their content

Identify security/privacy, accessibility, low-resource, packaging, migration, maintenance,
data-ownership, and release-interface impact. Put non-gating future assembly observations in
docs/portfolio/THIRDLIFE_FUTURE_NOTES.md rather than TASKS.yaml. Keep unproved release facts in
docs/release/RELEASE_INTERFACE.md and docs/release/RELEASE_EVIDENCE.md as TBD.

Stop for a frozen-decision proposal if the change would redesign the product boundary or create a
permanent integration lane. In the completion report, state the boundary and release-interface
impact even when both are "none".
```
