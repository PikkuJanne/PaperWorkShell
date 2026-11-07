# ADR 0021: Bounded serial repository scan groups

- Status: Accepted policy amendment; complete-root verification remains required
- Date: 2026-11-06
- Task: PWS-0811
- Decisions preserved: PWS-D003, PWS-D034, PWS-D035, PWS-D041, PWS-D042

## Evidence and scope

The complete repair history reached 11,673 selected logical scan items: 112 ref
names, 4,725 historical path/blob associations, 4,323 historical blobs, 336
commit/tag objects and 2,177 current paths. The old aggregate 10,000-item rule
correctly rejected it. The initial failure and bounded metadata diagnosis are
preserved under `output/pws0811/development/r12-fixture-enumeration-readonly.json`.
This is legitimate retained source history, including intermediate repair snapshots;
no ref, historical input or current file is removed to make a gate pass.

The metadata-only original-root forecast also showed that changing item admission
alone was insufficient: planned Git/raw/mirror work plus the three mandatory current
reads exceeded the unchanged 512 MiB work budget. The final corrected forecast,
`output/pws0811/development/repository-scan-byte-forecast-v2.json`, identified
32,179,975 bytes of duplicate alias payload writes (32,175,335 body bytes and 160
sentinels). Removing those actual writes leaves about 19.1 MB (18.2 MiB) forecast headroom
before small link-path metadata charges. This is not a completed scan or guaranteed
pass. Exact current-file digest/path reuse and full-root execution remain required.
Original and detached indexes have different raw metadata costs and require their
own evidence; neither is called the other's capacity proof.

## Amended admission policy

The former **global 10,000 selected logical items** limit becomes a maximum of
**two logical groups of 10,000, 20,000 total**, in both fixture-content inspection
and secret scanning. This is an explicit policy change, not a claim that the old
aggregate limit remained unchanged.

Each category, the combined historical/uncovered-index path coverage, resident raw
payload, and optional digest/coverage cache remain bounded at 10,000 items. Raw
payloads and physical mirror preparation retain their existing byte chunking.
Logical groups are admission units; they need not equal the number of physical
tool invocations. Mirror item counts refer to scanned input files, not all generated
directory inodes. Existing validated-path and metadata bounds still govern directory
complexity.

Every group shares the same 50 MiB per-input/payload ceiling, 512 MiB cumulative
logical-work budget, 16 MiB metadata transport cap, 50,000 raw-record cap and
ten-minute invocation deadline. No counter or deadline restarts between groups.
Raw digest epochs flush successfully before forgetting prior dedup proofs. An epoch
may rescan repeated bytes, which remain charged. Bodies and optional dedup entries
never grow without their existing finite bounds. More than two logical groups or
an excessive category/path/cache remains a failure. Descriptor metadata may grow
inside the same finite transport limits; identical total heap use is not claimed.

## Private alias payload reuse

Every original path and controlled filename-rule alias still exists and is scanned
by its separate original/shadow Gitleaks invocation. The alias is now a hardlink to
the just-written **scanner-created private mirror**, inside the same temporary
batch. Imported originals, repository sources, runtimes and case files are never
linked by this operation. There is no global exception allowing untrusted input
hardlinks.

Creation retains the fresh source file's device, inode, size and modification time
from its write handle. Before linking, source and target ancestors must be ordinary,
contained private directories, the source must remain that exact regular object
with link count one, and the destination must be absent. Afterwards both names must
have the exact recorded device/inode/size/time, be regular non-reparse files and have
link count two. Unsupported filesystems, redirection, replacement, additional links,
pre-existing targets or uncertain identity fail closed. Temporary ownership covers
both names through success and failure cleanup.

The work budget charges each original payload write and the link's encoded source
and destination path metadata. Only the unwritten second payload is removed from
the work forecast. Alias scanner CPU, remaining deadline, metadata and all alias
coverage remain charged/enforced. The forecast uses the same path metadata formula
as actual preparation. Separate scanner processes preserve alias coverage even if a
scanner internally deduplicates inodes during one traversal.

## Completed current-file proof reuse

Both tracked and untracked current files may reuse an already completed historical
mirror only for the same case-sensitive original path and freshly verified SHA-256.
The same bounded completed-coverage set is used. Status as tracked or untracked does
not change the isolated current rules under which the original/alias was scanned.

All three live reads remain: initial state, processing read and final state. Raw path
and body submission occurs before the reuse check. Unsafe/missing untracked files,
changed bytes, new names, staged-only content, direct refs and incomplete original or
alias scans retain their full checks. Final classification, index, source bytes,
config and ref/worktree guards remain mandatory. Nothing is accepted using only a
size, mtime or stale hash.

## Coherent index and tracked-path metadata

The official [Git ls-files documentation](https://git-scm.com/docs/git-ls-files)
defines the cached index selection and the stage/mode/object/path fields.
Validated `git ls-files --stage -z` rows already contain every cached path, in its
captured order. Initial and final tracked membership now derive from those same
index rows instead of another `ls-files --cached` query. This removes two actual
duplicate metadata transports and their record/byte costs; it does not stop
charging records that were transported. Final complete index equality, explicit
derived path equality, untracked membership, config/ref/worktree and all live-file
guards remain. Unmerged/unsafe/non-blob rejection and tracked missing/sparse-file
handling retain the same parser and policy.

The result is a coherent initial/final same-index snapshot. The incidental separately
timed cached-path observations are removed, so an ABA visible only at those former
extra points is not claimed as observed. The scan was and remains a bounded snapshot
check rather than an atomic lock or a guarantee against unobserved transient writes.

## Verification required

Five model regressions failed before the changes: affordable two-group admission,
untracked exact mirror reuse, private alias work capacity, and second-group secret
and unapproved-document detection. Initial after-fix models pass, with additional
guard/cleanup/state tests and strict typing. Their raw before/after receipts remain
under `output/pws0811/development/repository-scan-model-*`.

Acceptance additionally requires the pinned real Gitleaks filename-only canaries,
actual temporary Git history/staged/direct-ref/mutation cases, capacity/cache/work/
metadata/deadline negatives, cleanup checks, and complete registered full-root
`validate-fixtures` and `scan-secrets` commands. A narrower replica, failed command,
metadata forecast or model success cannot substitute for them. Results must name
their exact source and actual root/ref/index scope.

## Product and release effects

This changes development quality tooling only. The document-worker ResourcePolicy,
processing architecture, document limits, application data ownership and runtime
dependencies are unchanged. No sibling application/service, dependency, public
interface, signing authority or publication scope is introduced.
