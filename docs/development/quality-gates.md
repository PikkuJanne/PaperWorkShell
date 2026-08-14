# Local quality gates and CI parity

PWS-0003 establishes `tools/dev.py` as the single executable source of truth for developer
commands. Run `py -3.14 tools/dev.py list` to see the current registry and which commands are
included in `check`. GitHub Actions bootstraps the pinned environment and calls the same
dispatcher; it does not duplicate individual lint, test, validation, or scanning commands.

## Prerequisites

- Standard CPython 3.14
- uv 0.11.32
- Git 2.45 or newer (`for-each-ref --include-root-refs` is required)
- Gitleaks 8.30.1

Install Gitleaks on Windows when needed:

```powershell
winget install --id Gitleaks.Gitleaks --version 8.30.1 -e
```

The dispatcher checks the exact Gitleaks version and fails closed when it is absent or
mismatched. Gitleaks is a standalone MIT-licensed development tool; it is not an application
runtime dependency.

## Normal workflow

```powershell
py -3.14 tools/dev.py bootstrap
py -3.14 tools/dev.py check
```

`check` runs every non-mutating command marked `[check]` by `list`, in registry order. It covers
formatting, linting, architecture boundaries, strict typing, unit/integration/security/
accessibility lanes, public synthetic fixture regeneration/provenance, repository schemas, the task
graph, the ThirdLife standalone-portfolio boundary, the constrained M1 evidence schema, the bound
PDF-renderer comparison workload, secret scanning, offline documentation
links/anchors/security references, and deliberate negative self-tests. Use
the individual registered command when narrowing a failure. `format` is intentionally mutating
and is not part of `check`.

PWS-0111 replaces the no-UI accessibility sentinel with widget-level keyboard, focus, label,
scaling, and announcement coverage for the first PySide6 organizer surface.

## Schema validation

```powershell
py -3.14 tools/dev.py validate-schemas
```

The registry validates the task graph, constrained M1 evidence, frozen case-manifest v1, current
case-manifest v2, declarative-template v1, worker-RPC v1, dependency-inventory v1, and synthetic-
fixture-index v1 contracts. The dependency validator is also available directly:

```powershell
py -3.14 tools/dev.py validate-dependencies
```

It validates the complete policy baseline, proves every rejected example fails for its reviewed
reason, and reconciles admitted Python components and Windows artifact hashes with
`pyproject.toml` and `uv.lock`, CI/tool pins, task and decision authorities, closed source/license
and admission identities, dependency relationships, evaluated and renderer evidence, worker
boundaries, license routes, and redistribution gates. It is offline, bounded, rejects duplicate
JSON/YAML keys, and never executes or fetches a listed component. The baseline is deliberately not
release/SBOM proof; see
[`dependency-policy.md`](dependency-policy.md) for the resolved-build requirements.

Case manifests use bounded strict UTF-8 JSON parsing, exact version dispatch, a
self-contained Draft
2020-12 schema, and semantic checks for IDs, references, source-page bounds, operation replay,
normalized boxes, confirmation coupling, storage names, and output-name collisions. Both canonical
synthetic examples must pass and every checked-in negative fixture must fail. Diagnostics use
stable codes and JSON locations without echoing document values or absolute paths.

Template validation uses bounded strict UTF-8 JSON, integer-only numbers, exact version dispatch,
and a fingerprint-bound, self-contained Draft 2020-12 schema. Semantic checks cover lifecycle
date ordering and explicitly supplied expiry dates, unique requirement/warning/confirmation IDs,
cross-references and ordering, counts, output modes, byte/file limits, filename stems, and inert
HTTPS provenance. Separate-PDF templates must reserve at least one allowed output file per
requirement. The fixture suite supplies a fixed evaluation date rather than reading the wall clock.
Script, command, expression, import, arbitrary trust assertion, and eligibility-decision
members are rejected structurally; shell-looking narrative and provenance URLs remain data and
are never executed or dereferenced. The checked-in positive and negative fixture inventories and
their expected failure codes are exact. See
[`docs/schemas/template-v1.md`](../schemas/template-v1.md) for the data contract.

## Synthetic fixture validation

```powershell
py -3.14 tools/dev.py validate-fixtures
```

The non-mutating validator authenticates the closed index schema, proves the exact negative index
examples fail for their reviewed reasons, reconstructs all committed PDF/PNG bytes in memory, and
compares their canonical index and exact tree. It then reuses the secret scanner's hardened,
bounded Git/index/working-tree snapshot machinery to reject an unapproved document/image blob or a
high-confidence prohibited data-shaped pattern without printing the source path or value. The
separate Gitleaks lane retains responsibility for Git author/tag/commit bytes and secret patterns.
Neither automated pattern scan is anonymization; closed no-input generation and exact provenance
are the corpus admission rule. See
[`synthetic-fixture-policy.md`](synthetic-fixture-policy.md) for scheduled engine-owned classes,
on-demand hostile generation, limitations, and restricted bug-sample handling.

Regeneration is explicit and mutating, so it is not part of `check`:

```powershell
py -3.14 tools/dev.py generate-fixtures
```

Worker-RPC validation separately caps each message at 65,536 bytes, depth at 32, nodes at 4,096,
string code points at 4,096, and collection members at 256 before offline schema evaluation. It
checks the self-contained schema/reference policy, exact version and capability negotiation,
directions and correlations,
the exact operation registry, logical case-object reference scope, terminal results, progress,
restart semantics, and closed sanitized error codes without public context values. All canonical message examples must
pass and every malformed/incompatible/path-bearing negative fixture must fail. See
[`docs/schemas/worker-rpc-v1.md`](../schemas/worker-rpc-v1.md) for the protocol contract.

## Documentation validation

```powershell
py -3.14 tools/dev.py validate-docs
```

The offline validator scans root Markdown plus the `docs`, `benchmarks`, `packaging`, and `schemas`
documentation trees. It supports the repository's inline links/images and ATX headings, excludes
fenced/indented code and HTML-comment content from authority, and never fetches external HTTP(S)
or mail links. Unsupported raw HTML blocks, malformed fence openers, and ambiguous comment
delimiters inside inline Markdown fail closed. Local targets must remain under the repository
root, use exact Git-visible casing and forward slashes, and resolve to an existing file/heading.
Inputs must be UTF-8 and diagnostics contain only sorted relative paths and stable codes.

It also resolves every `PWS-D###` and `PWS-####` reference against the frozen decisions and task
graph. The PWS-0004 security catalogue is structural: threat IDs are unique, every high/critical
threat has a known decision and task/gate mapping, every threat has planned test coverage, and the
eleven threat categories named by the task remain explicit. This is reference/plan evidence; it
does not claim that future controls have been implemented.

## Portfolio-boundary validation

```powershell
py -3.14 tools/dev.py validate-portfolio-boundary
```

This offline validator preserves `PWS-D042` without consulting a sibling repository. It parses
project and lock metadata, the reviewed dependency inventory, production Python syntax, every
repository and packaged runtime schema, portfolio-document metadata, the M8 task owners, and
`BUNDLE_MANIFEST.json`.
It rejects ThirdLife runtime dependencies and production-only interface references; missing release
documents or metadata that does not match the current M8 gate stage; loss of project-local M8
ownership or gate coverage; and stale, unsafe, or mismatched roadmap-bundle records. Graph counts
and bundle summaries are reconciled with
the current structured task graph rather than frozen as permanent numeric constants. The validator
does not claim that a ThirdLife compatibility cut exists and does not turn future notes into work.
ThirdLife disclosure prose belongs in the canonical portfolio/release documentation, so production
Python literals and identifiers remain marker-free as well as dependency-free.

## Negative self-test

```powershell
py -3.14 tools/dev.py quality-self-test
```

The self-test creates only short-lived synthetic files under the operating system's temporary
directory. It proves that Ruff rejects an unformatted file, mypy rejects an invalid return type,
the task validator rejects an invalid schema date and dependency cycle, and Gitleaks rejects a
runtime-assembled synthetic canary staged inside a PDF-header byte stream. That case invokes
the production repository scanner after the staged bytes have been replaced in the working tree,
proving that the exact index blob and binary-safe path are covered. Child output is captured, the
secret is fully redacted, and the temporary directory is removed. Its Git setup ignores inherited
Git redirects and test knobs, system/global configuration, templates, hooks, signing, and prompts.

## Secret-scanning boundary

```powershell
py -3.14 tools/dev.py scan-secrets
```

The local scan feeds raw reachable commit and annotated-tag payloads, every reachable blob,
source-path byte sequence, and ref name, every exact selected-worktree index blob, tracked working
files, and untracked files returned by `git ls-files --others --exclude-standard` through bounded
Gitleaks stdin batches. Short-lived mirrors of every reachable historical path/blob pair, the exact index,
and current tracked and untracked inputs preserve filename-qualified upstream rules (including
`.p12`/`.pfx`, Terraform, and Kubernetes rules) without traversing ignored state. Each mirror also
scans safe, controlled aliases that preserve the suffix or terminal name required by all five
filename-qualified rules in Gitleaks 8.30.1. Those aliases neutralize the pinned scanner's
built-in broad path allowlist (for example, `node_modules`, lockfile names, and unanchored
configuration-name exclusions). Renames, deleted historical versions, direct tree refs, root-ref
commit histories (such as `ORIG_HEAD`) from every registered linked worktree, Git's one-level
`AUTO_MERGE` tree, every entry in multi-record `FETCH_HEAD`/`MERGE_HEAD` metadata, direct root-ref
blobs, nested annotated tags, commit messages, and one blob used under multiple names are included.
Both history walks start from the object IDs in the initial ordinary/pseudo-ref snapshot instead
of querying live refs again. A fixed text sentinel is prepended only to the scanner input/mirror so
Gitleaks examines raw PDF/archive/application bytes
instead of skipping them based on file magic; repository bytes are never changed, and temporary
mirrors are removed after each bounded pass. Compressed or encrypted container payloads are not
unpacked. The working-tree pass does not traverse ignored directories such as
`.paperworkshell-local`, `local-cases`, diagnostic exports, signing material, caches, or the
virtual environment. Symlinks, unreadable or changing working inputs, missing Git objects, and
individual blobs/files larger than the explicit 50 MiB scanner limit fail closed. Historical or
index names that cannot be represented bijectively on Windows—including alternate-data-stream,
reserved-device, control-character, and trailing-dot/space names—also fail closed instead of
being mirrored under an alias. Protected `.git`/`git~1` components also fail closed. Before
Gitleaks starts, every selected working file is size/digest-preflighted and the tracked
`.gitleaks.toml` is copied to a bounded, scanner-owned input whose digest is rechecked after
scanning. Every scan forces a scanner-created empty ignore file and ignores inline
`gitleaks:allow` annotations; source-controlled `.gitleaksignore` files are rejected. Git metadata
is streamed through one scanner budget: the subprocess is terminated at 16 MiB, and each raw
record—including duplicate history records before deduplication and repeated integrity
snapshots—counts toward an independent 50,000-record limit shared by initial capture and final
verification. A separate aggregate check limits selected refs, historical path/blob pairs, Git
objects, index entries, and current paths to 10,000 logical items. Keeping the raw-record budget
independent prevents the scanner's own consistency passes from consuming the logical-source
allowance while both dimensions remain fail-closed and bounded.
One 512 MiB logical-work budget also covers source/configuration reads, Git-object reads, bounded
scanner batches, mirror writes, and repeated path associations; anticipated historical/index work
plus known ref/path inputs and framing overhead are combined with completed preflight work and
rejected before Gitleaks starts. One ten-minute scan-wide deadline covers enumeration, filesystem
work, tool probes, and scanning, with each subprocess receiving only the remaining time and a
terminal deadline check. The larger deadline accommodates the complete reviewed history without
changing any byte, item, metadata, logical-work, child-process, or cleanup limit.
Deterministic per-item mirror namespaces avoid quadratic path-conflict batching.
The local policy has one reviewed false-positive exception: the inherited `generic-api-key` rule
may ignore only the exact extracted value `PRIVATE-PARENT-PWS0104-CANARY`, a fixed public label
used by the PWS-0104 privacy test. It does not exclude a path, commit, baseline, stopword, or token
family. The security lane scans the exact value successfully and proves that a one-character
neighbor plus a runtime-assembled realistic access-key shape still fail.
Refs, registered worktrees, the selected index, tracked/untracked path sets, and scanned
working-file digests are snapshotted and rechecked; mutations observed between those checkpoints
fail closed. This is a bounded snapshot check, not an atomic repository lock: a writer must be
quiescent, and a change after the terminal observation is input to the next scan.
Replacement-object semantics and commit-graph acceleration are
disabled so physical object parent links are inspected. Shallow repositories and nonempty legacy
`info/grafts` metadata are rejected because they cannot prove complete physical ancestry. Lazy Git
object fetch and terminal prompting are disabled. Commands use fixed argument arrays,
`shell=False`, a sanitized Git environment that cannot redirect the repository, index, object
database, namespace, grafts, shallow state, implicit worktree, reference backend, or Git's
test-only override namespace. Command-scope configuration disables commit graphs and filesystem
monitor hooks. Bounded commands run in a Windows kill-on-close job, require complete stdin
delivery, cap captured output, and terminate the process tree at the earlier of their 120-second
limit or the remaining scan-wide deadline. Tool version probes use the same runner with a 4 KiB
and at-most-10-second bound; mismatch errors never include raw
tool output. Gitleaks also uses full finding redaction. The canonical Git worktree root must match the explicit scan root before and
after scanning; local `core.worktree` redirection fails closed. Other linked
worktree indexes and working files are outside the current-state boundary, but their pseudo-ref
histories are included because they share the object database. Ref storage must use the standard
in-repository `files` or `reftable` backend without an external payload path.

Neither the local suite nor CI uploads source, fixtures, scan reports, benchmark reports, caches,
or artifacts. CI network use is limited to checkout and installation/bootstrap of pinned tools
and dependencies before the suite runs.

## Constrained-resource policy and measurements

PWS-D041 replaces the mandatory physical reference-machine gate with a central, versioned
`ResourcePolicy`, bounded production composition, deterministic synthetic workloads, and numeric
automated evidence. The constrained M1 run enforces worker, workspace, cache, temporary-storage,
preview, concurrency, responsiveness, cancellation, and low-disk budgets. Schema validation checks
the closed report contract; the live runner proves that the policy is operational rather than a
report label. See [`LOW_RESOURCE_POLICY.md`](../performance/LOW_RESOURCE_POLICY.md) and
[`benchmarks/README.md`](../../benchmarks/README.md).

Passing constrained-profile evidence proves bounded software behavior for that exact revision and
workload. It does not emulate or certify a CPU, RAM allocation, GPU, storage medium, VM, or physical
machine. Physical low-end-device testing has not been performed and is optional/non-gating.

PWS-0106 adds `benchmarks/pdf-renderer-windows-v1.json`. The ordinary non-mutating command validates
its exact profile, corpus, golden, artifact, and outcome bindings; it does not itself synchronize
the non-default Qt group or execute the complete benchmark command/report.
`benchmark-pdf-renderers` is an explicit Windows-only, clean-revision operation using the
`renderer-evaluation` dependency group. Its isolation,
affinity, staging, and invocation contract is documented in `benchmarks/README.md`.

The Windows CI workflow separately synchronizes the non-default `renderer-evaluation` group so the
PWS-0106 unit/integration comparison tests can exercise both live renderer adapters. Essentials and
shiboken6 are ordinary PWS-0111 runtime dependencies, but the group-only Qt PDF/Addons surface
retains no product runtime scope, source-import authorization, or enabled shipment relationship. CI
does not run or upload the mutable-host benchmark report.

## CI supply-chain controls

`.github/workflows/quality.yml` uses the `windows-2025` hosted image, read-only repository
permissions, full commit history, disabled persisted checkout credentials, disabled uv cache
restore/save, and actions pinned to full commit hashes. It downloads the official Gitleaks 8.30.1
Windows archive and verifies SHA-256 before extraction. No artifact, cache, SARIF, PR-comment, or
external reporting action receives repository content.
