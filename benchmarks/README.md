# Constrained-resource evidence

PaperWorkShell's low-resource contract is the central, versioned `ResourcePolicy` described in
[`docs/performance/LOW_RESOURCE_POLICY.md`](../docs/performance/LOW_RESOURCE_POLICY.md). The
constrained profile applies real limits to production composition and the canonical Milestone 1
vertical slice: worker memory and CPU, process count, workspace/cache/temporary storage, preview
demand and dimensions, queue/concurrency bounds, chunked I/O, responsiveness, cancellation, and
low-disk behavior.

Validate the closed evidence schema without running the workload:

```powershell
py -3.14 tools/dev.py validate-m1-benchmark-report
```

Run the synthetic, deterministic vertical slice under the constrained profile:

```powershell
py -3.14 tools/dev.py run-m1-vertical-slice
```

The sanitized report binds the repository revision, resource-policy version, synthetic workload,
engine identities, configured budgets, observed numeric values, deterministic result, recovery,
and explicit limitations. It omits usernames, hostnames, serial numbers, network identifiers,
document content, and full paths. Reports are not uploaded by the quality workflow.

Passing this workload proves that the tested revision stayed within the stated software budgets.
It does not emulate or certify a named CPU, RAM allocation, GPU, SSD, HDD, VM, or physical device.
Physical low-end-device testing has not been performed; any future observation is optional and
non-gating under PWS-D041.

## PDF renderer comparison

`pdf-renderer-windows-v1.json` binds the historical PWS-0106 comparison to a versioned Windows workload,
15 independently calculated renderer goldens, two semantic cases, three stable failure outcomes,
and exact pypdfium2/PDFium and Qt PDF identities. It is a record-only selection workload, not a
product latency target.

Validate every workload, fixture, golden, artifact, and outcome binding without loading
either native renderer:

```powershell
py -3.14 tools/dev.py validate-pdf-renderer-benchmark
```

Qt PDF is an evaluation-only candidate in a non-default dependency group. Prepare the exact locked
candidate closure before running the comparison:

```powershell
uv sync --locked --group renderer-evaluation
```

The Windows CI job also synchronizes this non-default group solely so the PWS-0106 comparison
tests can exercise both live adapters. Installing that group for tests does not grant Qt PDF a
source-import boundary, product runtime scope, or enabled shipment relationship.

The comparison requires a clean repository revision. Its accepted run remains historical renderer
selection evidence; current low-resource qualification comes from the central constrained profile,
not operator hardware attestations.

The harness copies only its pinned Python runtime subset, exact renderer files, and hash-bound
public synthetic corpus into a fresh authenticated fixed-drive stage. Tracked project code and
data come from bounded `git cat-file` reads of the exact reported revision rather than from the
mutable worktree. Source ancestors and staged objects remain held by authenticated Windows handles
while the closure is built. Every selected renderer file must match both the SHA-256 and size in
that installed distribution's exact `RECORD`; the stage manifest then cryptographically binds the
repository revision, every staged file, and every byte count. Git must report the same clean `HEAD`
before staging, after staging, and immediately before report creation.

Each renderer/mode runs in a fresh zero-capability AppContainer under the production kill-on-close
Job Object and bounded framed transport. The parent has already audited the retained stage before
launch and seals the root plus every staged directory with a retained, reversible DACL lease. The
current user keeps read/traverse access but is denied ordinary file creation, subdirectory
creation, deletion, and rename operations; the distinct AppContainer SID receives inherited
read/execute only. The seal is reverified before and after AppContainer access leasing. The
operating system must load the staged interpreter, fixed bootstrap, and minimal
isolation/protocol modules before the child can receive the parent-provided manifest digest; the
bootstrap verifies the complete manifest before it imports the benchmark harness or either native
renderer. The stage is read-only to the child and the child has no network capability or live
repository access.

Normal cleanup revalidates and deletes only exact identities retained by the creating process.
Directory policies remain sealed while their known children are removed; the parent restores and
byte-verifies each original DACL immediately before deleting that exact retained directory, with
the root restored last. If initial authority setup fails after the empty root is authenticated,
the parent deletes only that exact empty identity; a failure before authentication leaves the root
untouched and records cleanup failure rather than attempting unauthenticated recursive deletion. An
uncertain worker close, AppContainer launch cleanup, or post-revocation seal check permanently
withdraws cleanup authority for that stage. The process retains its handles and namespace-deny
DACLs and refuses every later cleanup or child-launch attempt; it does not restore directory
policies or retry deletion. This can intentionally leave the bounded public-only stage after the
benchmark process exits. An abrupt parent-process or machine termination can likewise leave at
most the 768 MiB bounded stage, which
contains only copied runtime/project code and public synthetic fixtures—not user documents. A later
process does not treat a filename or self-written token as deletion authority, so such residue is
preserved for explicit maintenance instead of risking deletion through an attacker-swapped path.

The sanitized JSON report distinguishes the workload's declared published-wheel hashes from the
observed installed `RECORD` and stage-closure hashes. It also records corpus/workload identities,
isolated-worker readiness, imports, document-open and page-render timings, nearest-rank warm
p50/p95/max values, and a parent-observed monotonic launch-through-exit envelope that bounds the
strictly positive child timing evidence,
Job-reported process/job memory peaks, sampled private temporary-storage high-water, cooperative
cancellation at the documented between-render checkpoint, idle-worker Job-tree termination, and
stable malformed/high-dimension outcomes. It contains no source path, filename, page pixels,
document content, user or machine identity, or native exception text. The benchmark does not claim
that cooperative cancellation interrupts an in-flight native render or certify its host as a
particular physical device.

### Accepted PWS-0106 selection run

The accepted record-only local-smoke run used clean revision
`74c1c7aae799195a87608a797d7136b88e80cc87`. Its authenticated 1,015-file stage had closure digest
`f78dffe61e4b86e6596f1080097a7a23aed90cc0ca6eca9023d67fb2521e3da8`. Both candidates matched all
15 analytical goldens and all three malformed/high-dimension outcomes. pypdfium2 also passed both
semantic cases; Qt PDF passed the missing-font case but its public API could not perform the
required distinct MediaBox mode.

The complete sanitized JSON result was reviewed locally and is intentionally not committed.
[ADR 0005](../docs/adr/0005-pdf-renderer-selection.md) records the exact warm, cold, memory,
cancellation, and Job-termination measurements and the functional, packaging, and license
rationale for selecting pypdfium2/PDFium. This local smoke remains selection evidence only; it is
not a product target or physical-device qualification.
