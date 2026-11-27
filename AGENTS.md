# AGENTS.md — PaperWorkShell operating policy

## Engineering authority

Preserve the owner's approved PaperWorkShell product requirements in [DECISIONS.md](DECISIONS.md),
[ROADMAP.md](ROADMAP.md), and current explicit owner direction. Codex owns ordinary engineering
choices, task decomposition, sequencing, and proportionate verification within those requirements
and its actual permissions.

Codex may revise project-local process rules, task dependency edges, and procedural checks that
obstruct this authority. Ordinary engineering decisions need no separate owner exception.
Preserve checks that establish required behavior, integrity, privacy, security, or a genuine
delivery obligation.

A blocker applies to operations whose real prerequisites it prevents. Record the evidence, affected
operation and dependants, missing prerequisite, useful work continuing, and next resolving action
in the existing [task graph](TASKS.yaml) or its linked evidence. Continue useful independent work.
Milestone labels, task numbers, and historical sequence provide planning context.

Complete authorized work in substantial useful increments. Ask only for a material product
decision, genuinely missing authorization, or an external action Codex cannot perform. Prepare
the relevant work first. Codex owns setup and mechanical verification. Reserve human effort for
necessary interaction or judgment and follow the [human-testing policy](docs/development/human-testing.md).

The maintainer has authorized the PWS-0802 disposable guest quota test and all future
PaperWorkShell guest quota tests. Apply quota settings inside the intended guest and
retain the evidence before restoring its test state.

Verify changed behavior with sufficient relevant evidence. Repeat an unsuccessful operation only
when a changed condition or concrete diagnostic question can produce useful information. Keep
historical evidence and verdicts with their original identity and scope. Use this policy for
future work and carry it through active instructions, templates, handoffs, and validators.

Real platform permissions, OS access controls, and owner requirements remain in effect. Consult
the applicable decisions, roadmap, task entries, code, tests, schemas, and ADRs. Use the
[decision change procedure](DECISIONS.md#change-procedure) for changes to owner-approved product
requirements. Use a coherent task ID or set of related IDs in the work log and delivery report.

## Unsigned product completion

Apply [PWS-D044](DECISIONS.md#pws-d044--v10-product-completion-accepts-an-unsigned-candidate)
to build, packaging, setup, testing, acceptance, and handoffs. Complete the required
PWS-0808 product evidence on an exact unsigned candidate. Keep publisher-signing
credentials, receipts, signed reruns and provider actions out of the prerequisite
chain. Bind evidence to candidate identity, checksums and observed signing status.
Follow actual Windows execution policy and preserve genuine security and integrity
checks. Publication needs separate authorization. Consider a signed variant only
after product completion and a separate owner decision. Use the task-graph
`publisher-code-signing` risk tag for any such future work and keep it outside
the PWS-0808 prerequisite closure.

## Standalone boundary

Apply PWS-D042 and [THIRDLIFE_ALIGNMENT.md](THIRDLIFE_ALIGNMENT.md) to portfolio, installer, update,
release, data-location, and interoperability work. Keep work inside PaperWorkShell and preserve
independent development, runtime, testing, installation, versions, migrations, and release. A
separate explicit ThirdLife B4 assignment governs any work in the portfolio repository.
PaperWorkShell remains complete without siblings and keeps control of its private product data.
Use ordinary files and normal interactive paths first. Each public surface needs independent
PaperWorkShell value. Keep future assembly observations in
[THIRDLIFE_FUTURE_NOTES.md](docs/portfolio/THIRDLIFE_FUTURE_NOTES.md). Treat the
[release interface](docs/release/RELEASE_INTERFACE.md) as human-readable documentation and leave
unproved release facts `TBD`.

## Implementation safeguards

- Preserve unrelated work, immutable artifacts, and accepted evidence. Keep changes reviewable.
  Keep secrets, build outputs, local cases, OCR language data, and real documents out of Git.
  Use descriptive commits with task IDs. Preserve published history.
- Keep pure domain logic separate from UI, document adapters, installer code, and the filesystem
  UI. Use typed, versioned boundaries and validate worker messages in both directions. Keep
  parser and renderer objects behind adapters. Check import boundaries when they change.
- Treat PDFs and images as hostile. Validate media content and enforce applicable input,
  decompression, resource, path, and temporary-storage limits. Keep untrusted parsing outside
  the GUI. Run document workers without network access, with bounded work, minimal environment,
  case-scoped access, deterministic cancellation and cleanup, and termination on severe faults.
  Pass structured subprocess arguments with no shell interpolation of document-controlled data.
- Preserve external inputs and imported originals. Write derivatives to new paths, then reopen,
  check, and render as required before reporting success. Keep exports deterministic in page
  order and semantic decisions. Keep migration transactional, support existing versions, and
  reject unknown future schemas before mutation.
- Use stable error codes and sanitized, actionable messages. Exclude document content, user names,
  original filenames, full paths, and signatures from routine logs. Preview the inventory of
  opt-in diagnostic exports.
- Keep permanent redaction subject to PWS-D022 and its independent security gate. Fail closed
  on verification failure. Keep ordinary marks distinct. Use “Place signature image,”
  “Needs review,” and “Unsigned derivative” where applicable. Reserve “Check complete” for
  performed checks and claims of safety, accessibility, or compatibility for proven scope.
- Provide keyboard access, accessible names and states, visible focus, text scaling, non-color
  feedback, live announcements, and cancelable long work. Keep review free of timeouts.
- Pin and inventory dependencies and applicable licences. Review source, maintenance, platform
  and packaging impacts, including redistribution rights and the untrusted-input boundary.
  Preserve PWS-D034 and applicable release/SBOM obligations. Record material changes in the
  relevant ADRs and dependency inventory.

## Definition of done

- Verify the actual task acceptance criteria, behavior and failure paths. Use relevant repository
  entry points from [quality-gates.md](docs/development/quality-gates.md), focused regression tests,
  and wider checks when they address a real risk or gate. Keep deterministic synthetic fixtures
  and clean up owned test state. Inspect rendered/exported output where it changes. Preserve
  the basis for any golden-output change.
- Check resource-sensitive behavior with the central `ResourcePolicy` and relevant constrained
  profiles. Exercise cancellation, cleanup, recovery, and low-disk behavior where affected.
  Budget evidence describes the tested software and workload. Hardware claims require actual
  hardware evidence.
- Keep affected docs, schemas, and task evidence current. Close tasks after their actual
  prerequisites and acceptance are evidenced. Release gates require their specified evidence,
  with unresolved failures and unverified observations recorded accurately.
- In every handover, identify the task IDs, changed locations, verification results, remaining
  limitations and affected operations, and the commit or review artifact. End with a
  **Next step** in plain English. Name the task ID when applicable and the first concrete
  action Codex should take. If progress requires an external action, name the smallest
  action needed and who can take it. If the work has no follow-up, say so explicitly.
