# PaperWorkShell Codex prompts

Use these prompts with the current [operating policy](AGENTS.md). The repository's
[task graph](TASKS.yaml) and linked records track current work and evidence. Earlier
evidence and handoffs describe their recorded candidates and conditions.

## Continue current work

```text
Continue PaperWorkShell from the current repository state under AGENTS.md.
Read the owner-approved requirements, the relevant task entries and linked evidence,
and the affected code and tests. Preserve unrelated checkout work and accepted evidence.

Select a substantial useful increment. Sequence operations by their actual prerequisites
and adjust project-local tracking or checks when they encode obsolete procedure.
Complete implementation and proportionate verification, including the normal installed
path where the task calls for it. Record evidence against the exact source or artifact
and update the existing task records. If an operation is blocked, record its dependency
and next resolving action and continue useful independent work.
```

## Implement or repair a named task

```text
Implement or repair PaperWorkShell task <TASK_ID> under AGENTS.md.
Read its acceptance criteria, actual prerequisites, relevant decisions, and linked evidence.
Review the current diff if resuming interrupted work. Use synthetic inputs for reproduction.
Complete the relevant work, test the behavior and affected recovery paths, and preserve
the product's existing integrity, privacy, worker isolation and accessibility requirements.
Record the result and remaining affected operations in TASKS.yaml or its linked record.
```

## Review a change or verify a release gate

```text
Review <TASK_ID_OR_CHANGE> or verify release gate <GATE_TASK_ID> under AGENTS.md.
Inspect the relevant requirements, diff, tests, and evidence. For a gate, verify its
required behaviors and delivery obligations on the applicable revision and environment.
Where evidence is missing, perform the authorized checks that can proceed and record
what still depends on a missing prerequisite. Preserve failed or inconclusive results.
Report exact findings and evidence, and update the task or gate record as warranted.
```

For packaging, release, data-location or interoperability work, consult
[THIRDLIFE_ALIGNMENT.md](THIRDLIFE_ALIGNMENT.md). For TRACE, use its
[implementation and installed evidence](docs/development/pws0813-trace.md).
For security and resource changes, consult the affected threat model and
[resource policy](docs/performance/LOW_RESOURCE_POLICY.md).
