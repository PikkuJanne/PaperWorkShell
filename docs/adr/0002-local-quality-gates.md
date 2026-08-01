# ADR 0002: Local quality gates, secret scanning, and CI parity

- Status: Accepted
- Date: 2026-08-01
- Task: PWS-0003
- Decisions: PWS-D003, PWS-D034, PWS-D035

## Context

PaperWorkShell needs one reproducible Windows-first quality workflow before document parsers,
fixture corpora, or UI dependencies expand the repository. The workflow must detect ordinary
quality failures and secrets while keeping repository and fixture content local to the developer
machine or ephemeral CI runner. The low-spec reference environment also needs a reproducible
description without inventing performance promises before feature workloads exist.

## Decision

1. `tools/dev.py` is the executable command registry. Command listing, CLI choices, and the
   aggregate `check` order are derived from that registry. CI invokes only `bootstrap` and
   `check` after installing prerequisites.
2. Formatting uses Ruff, linting uses Ruff, strict typing uses mypy, tests use separate pytest
   unit/integration/security/accessibility lanes, and local Python validators cover repository
   schemas, architecture boundaries, and the task DAG.
3. Standalone Gitleaks 8.30.1 performs local secret scanning. The configuration extends upstream
   defaults and adds one synthetic self-test rule. Bounded raw-byte scans cover every reachable
   blob, commit and annotated-tag payload, every reachable source path and ref name, the exact
   selected-worktree index, and its nonignored current working files without traversing ignored
   local state.
   Short-lived path-aware mirrors preserve filename-qualified rules for all historical path/blob
   pairs, the exact index, and current tracked/untracked bytes. Coverage includes renamed, removed,
   and multiply named historical blobs; ordinary refs; pseudo-refs from every registered linked
   worktree; refs that point directly to trees or blobs; Git's tree-valued `AUTO_MERGE`; nested
   annotated tags; commit messages; and every object recorded in multi-record
   `FETCH_HEAD`/`MERGE_HEAD` metadata. Captured ordinary and pseudo-ref object IDs, rather than a
   later live `--all` query, drive both history traversals. Ref, registered-worktree, index,
   path-set, and selected working-file content snapshots are rechecked after scanning; changes
   observed between those capture/check points fail closed. The scan is a bounded snapshot check,
   not an atomic lock against a writer that changes the repository after its terminal observation.
   Git replacement objects and commit-graph acceleration are disabled so physical object parent
   links are inspected; shallow repositories and nonempty legacy graft metadata are rejected as
   incomplete ancestry. Scanner subprocesses remove Git environment overrides that could redirect
   repository, index, object, namespace, reference backend, history state, or Git's test-only
   behavior; command-scope configuration also disables commit graphs and filesystem-monitor hooks.
   Bounded subprocesses run in a Windows kill-on-close job, enforce exact stdin delivery, cap
   captured output, and terminate the process tree at the deadline. Git/Gitleaks version probes
   use the same runner with a 4 KiB/10-second bound and never echo untrusted tool output.
   Canonical Git worktree identities must match every registered root. Ref storage is
   limited to the standard in-repository `files` or `reftable` backend without an external payload
   path.
   Controlled rule-preserving alias mirrors neutralize the pinned upstream global path allowlist
   for all five filename-qualified default rules, including overlaps with allowlisted directory
   and file names. A fixed scanner-input sentinel prevents binary MIME skipping; inputs over 50
   MiB and unreadable, unsafe, changing, or unavailable inputs fail closed. Repository names that
   would become a Win32 alias, reserved device, NTFS alternate data stream, or protected Git
   metadata path are rejected before mirroring. Git-object sizes and all selected working files
   are preflighted before Gitleaks starts. The tracked Gitleaks policy is copied into a bounded,
   scanner-owned immutable input and its digest is rechecked after scanning. Every scan uses a
   scanner-created empty ignore file, disregards inline allow annotations, and rejects
   source-controlled `.gitleaksignore` files. A shared streaming budget terminates Git enumeration
   at 16 MiB of metadata or 10,000 raw records across initial capture and final verification;
   duplicate history records consume the budget before parsing or deduplication. A second
   aggregate 10,000-item check bounds selected refs, historical path/blob pairs, Git objects,
   index entries, and current paths. A 512 MiB logical-work budget covers source/configuration and
   Git-object reads, scanner inputs, mirror writes, and repeated path associations; anticipated
   historical/index work, known ref/path inputs, and framing overhead are combined with completed
   preflight work and rejected before Gitleaks starts. One five-minute scan-wide deadline covers
   enumeration, filesystem work, tool probes, and scanning, and each subprocess receives only the
   remaining time before a terminal deadline check. Historical mirror entries use deterministic
   per-item namespaces so batch construction is linear.
4. The aggregate suite includes a negative self-test that creates temporary synthetic violations
   for formatting, typing, schema format, graph cycles, and secret detection. The secret case
   proves exact staged PDF-like bytes are scanned after working bytes change. Temporary Git setup
   strips redirect/test variables, ignores system/global configuration, and disables templates,
   hooks, prompts, and commit signing. The self-test captures child output and never commits a
   matching canary.
5. GitHub Actions runs the same suite on `windows-2025` with read-only permissions. Checkout,
   setup-python, and setup-uv use full commit hashes. uv caching and all artifact/report uploads
   are disabled.
6. The reference environment profile fixes two logical processors, 4096 MiB RAM, integrated
   graphics, an at-most-8-GiB volume with at least 4 MiB free, and an HDD scenario. Its generated-I/O
   harness records observations only. The versioned generator and expected per-file checksum pin
   the workload across implementations. Reference runs compare Windows, architecture, native
   process-affinity CPU allocation, capacity, and free-space observations; interpreter CPU-count
   overrides are rejected. Memory allocation, integrated graphics, and HDD media remain explicit
   attestations rather than inferences from mutable hosted CI.

## Dependency and supply-chain record

| Component | Version/source | License | Maintenance signal checked 2026-08-01 | Verification and security/runtime boundary |
|---|---|---|---|---|
| Git | 2.45 or newer, `git-scm.com`; Git for Windows 2.55.0 verified locally | GPL-2.0-only | Installed 2.55.0 supports the required root-ref/worktree commands | Developer/CI VCS executable only; version is checked at scan time, hosted-runner variability fails closed, and Git is not packaged with the application |
| Gitleaks | 8.30.1, `github.com/gitleaks/gitleaks` | MIT | Release published 2026-03-21 | Development/CI executable only; Windows x64 ZIP SHA-256 `d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e`; processes repository bytes only inside the local/ephemeral quality boundary |
| actions/checkout | 7.0.1 | MIT | Release published 2026-07-20 | Pinned commit `3d3c42e5aac5ba805825da76410c181273ba90b1`; CI only; credentials are not persisted |
| actions/setup-python | 7.0.0 | MIT | Release published 2026-07-20 | Pinned commit `5fda3b95a4ea91299a34e894583c3862153e4b97`; CI toolchain setup only |
| astral-sh/setup-uv | 9.0.0 | MIT | Release published 2026-07-21 | Pinned commit `c771a70e6277c0a99b617c7a806ffedaca235ff9`; CI bootstrap only; cache disabled |
| uv Windows x64 archive | 0.11.32 | MIT OR Apache-2.0 | Release published 2026-07-23 | SHA-256 `acfde570451cfdb8689fa159a138ee805ba4e241c466432750302c86254b0984`; development dependency synchronization only |

This is the task-local admission record required by PWS-D034. PWS-0005 remains responsible for
the formal repository-wide dependency inventory and license policy, and PWS-0801 remains
responsible for release SBOM evidence. None of these tools becomes an application runtime
dependency.

## Security, privacy, and accessibility effects

- Gitleaks runs locally with full redaction and no report upload. Ignored case data, diagnostic
  exports, signing material, caches, and virtual environments are not traversed as current
  working state. Reachable Git objects, source-path bytes, and exact index bytes are scanned even
  when binary. Raw-byte and controlled path-rule alias passes compensate for broad path exclusions
  built into the pinned upstream configuration.
- The negative tests contain only generated text/bytes and no applicant, identity, financial,
  medical, employment, address, or signature data.
- CI has read-only repository permissions, does not persist checkout credentials, and uploads no
  cache, scan report, benchmark result, fixture, or artifact.
- The test lanes establish a stable accessibility command before UI work. The current sentinel
  fails when PySide6 first appears, forcing replacement with widget accessibility tests.

## Alternatives considered

### Gitleaks GitHub Action

Rejected in favor of the standalone MIT CLI. Direct invocation is cross-environment, avoids an
additional action/license boundary, and prevents default comments or report/artifact behavior.

### Directory-wide secret scanning

Rejected because scanning the repository directory directly can traverse ignored local case or
development state. Git-aware enumeration covers source inputs while respecting `.gitignore`.

### Separate CI command definitions

Rejected because duplicated command lists drift. The workflow calls the same dispatcher used
locally.

### Numeric performance thresholds in M0

Rejected because no representative product workload has established them. The version-1 schema
requires an empty product-target list and records measurements for later gate decisions.

## Consequences and residual risks

- Developers need Git 2.45 or newer and exactly Gitleaks 8.30.1 in addition to the PWS-0001
  toolchain.
- Hosted `windows-2025` images remain mutable and do not reproduce the two-core/4-GB/HDD profile.
  Action commits and tool archives are pinned, while reference measurements remain separate.
- Gitleaks pattern matching can produce false positives. Any future PaperWorkShell allowlist must
  be narrow, reviewed, and tested; no baseline or broad fixture exclusion is accepted by this
  ADR. Gitleaks 8.30.1's inherited broad path allowlist is not trusted as coverage: raw bytes and
  controlled rule-preserving aliases independently cover its excluded paths.
- Secret scanning examines raw container bytes but does not unpack compressed or encrypted
  payloads. Synthetic archives added later require explicit content-generation and inspection
  policy under PWS-0009/PWS-0005; broad archive exclusions are not permitted.
