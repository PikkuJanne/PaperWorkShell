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
accessibility lanes, repository schemas, the task graph, the reference profile, secret scanning,
offline documentation links/anchors/security references, and deliberate negative self-tests. Use
the individual registered command when narrowing a failure. `format` is intentionally mutating
and is not part of `check`.

The accessibility lane currently contains a foundation sentinel: it proves that no PySide6 UI
surface exists without widget-level tests. The first UI task must replace that sentinel with real
keyboard, focus, label, scaling, and announcement coverage.

## Schema validation

```powershell
py -3.14 tools/dev.py validate-schemas
```

The registry validates the task graph, reference profile, case-manifest v1, and worker-RPC v1
contracts. Case manifests use bounded strict UTF-8 JSON parsing, exact version dispatch, a
self-contained Draft
2020-12 schema, and semantic checks for IDs, references, source-page bounds, operation replay,
normalized boxes, confirmation coupling, storage names, and output-name collisions. Both canonical
synthetic examples must pass and every checked-in negative fixture must fail. Diagnostics use
stable codes and JSON locations without echoing document values or absolute paths.

Worker-RPC validation separately caps each message at 65,536 bytes, depth at 32, nodes at 4,096,
string code points at 4,096, and collection members at 256 before offline schema evaluation. It
checks the self-contained schema/reference policy, exact version and capability negotiation,
directions and correlations,
the exact operation registry, logical case-object reference scope, terminal results, progress,
restart semantics, and allowlisted sanitized error parameters. All canonical message examples must
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
record—including duplicate history records before deduplication—counts toward a 10,000-record
limit shared by initial capture and final verification. A second aggregate check limits selected
refs, historical path/blob pairs, Git objects, index entries, and current paths to 10,000 items.
One 512 MiB logical-work budget also covers source/configuration reads, Git-object reads, bounded
scanner batches, mirror writes, and repeated path associations; anticipated historical/index work
plus known ref/path inputs and framing overhead are combined with completed preflight work and
rejected before Gitleaks starts. One five-minute scan-wide deadline covers enumeration, filesystem
work, tool probes, and scanning, with each subprocess receiving only the remaining time and a
terminal deadline check.
Deterministic per-item mirror namespaces avoid quadratic path-conflict batching.
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

## Reference profile and measurements

`benchmarks/reference-windows-v1.json` and
`schemas/reference-profile.schema.json` define the Windows low-spec environment and generated-I/O
workload. The ordinary suite validates the profile but does not treat mutable hosted CI hardware
as the reference machine. See `benchmarks/README.md` for local smoke and prepared
limited-storage/HDD runs. Reference runs compare observable OS, architecture, native process
affinity, an at-most-8-GiB volume, and at least 4 MiB free; interpreter CPU-count overrides fail.
Memory, graphics, and (for HDD) storage media remain explicit attestations. A mismatch exits
nonzero and prints a sanitized structured evaluation showing each
matched, mismatched, or unknown requirement. The generated-byte algorithm and expected checksum
are pinned so the version-1 workload cannot drift silently. Measurements are record-only;
version 1 contains no guessed product thresholds.

## CI supply-chain controls

`.github/workflows/quality.yml` uses the `windows-2025` hosted image, read-only repository
permissions, full commit history, disabled persisted checkout credentials, disabled uv cache
restore/save, and actions pinned to full commit hashes. It downloads the official Gitleaks 8.30.1
Windows archive and verifies SHA-256 before extraction. No artifact, cache, SARIF, PR-comment, or
external reporting action receives repository content.
