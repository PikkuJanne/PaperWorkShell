# Reference environment and baseline workload

`reference-windows-v1.json` defines the PWS-0003 reference environment: Windows x86-64,
two allocated logical processors, 4096 MiB RAM, integrated graphics, limited storage, and a
rotational-HDD scenario. The profile separates declared requirements from observations.

The `limited-storage` scenario requires a dedicated volume no larger than 8 GiB with at least
4 MiB free before the run. Capacity and free space are checked and recorded at run time. The
`rotational-hdd` scenario requires a physical HDD or independently validated HDD emulation;
placing a virtual disk on an SSD is not sufficient evidence. Assigned memory, integrated
graphics, and storage media remain operator-attested because portable Windows APIs cannot prove
those topology properties consistently. CPU allocation comes from the native Windows process
affinity mask; interpreter CPU-count overrides are rejected.

The generated-I/O workload writes, flushes, reads, hashes, and removes two 1 MiB files made
from deterministic synthetic bytes. Its `sha256-seed-repeat-v1` generator and expected per-file
SHA-256 are pinned in the profile so an implementation change cannot silently redefine the v1
workload. These sizes make the harness reproducible; they are not product performance or storage
targets. Version 1 is explicitly record-only and rejects all numeric product targets.

Validate the profile without performing I/O:

```powershell
py -3.14 tools/dev.py validate-reference-profile
```

Exercise the entry point on the current development volume:

```powershell
py -3.14 tools/dev.py benchmark-reference
```

To validate the reference harness on a prepared environment, invoke the underlying tool and
provide each operator-attested property explicitly:

```powershell
uv run --locked --no-sync python -m tools.reference_profile benchmark --scenario limited-storage --work-directory D:\pws-benchmark --attest-memory-mib 4096 --attest-graphics integrated
uv run --locked --no-sync python -m tools.reference_profile benchmark --scenario rotational-hdd --work-directory E:\pws-benchmark --attest-memory-mib 4096 --attest-graphics integrated --attest-storage-media hdd-or-validated-emulation
```

The command fails before generated workload I/O unless observable Windows, x86-64,
two-allocated-processor, capacity, and free-space requirements plus every required attestation
match. The JSON report goes
to standard output and contains a structured match result, generic platform observations, exact
byte counts, timings, checksums, and free-space observations. It intentionally omits usernames,
hostnames, serial numbers, network identifiers, and full paths. Reports are not uploaded or
committed by the quality workflow. The 2 MiB generated workload validates measurement plumbing
only; it is not HDD or product-performance evidence. Later product workloads establish
performance evidence.

## PDF renderer comparison

`pdf-renderer-windows-v1.json` binds the PWS-0106 comparison to the same low-spec Windows envelope,
15 independently calculated renderer goldens, two semantic cases, three stable failure outcomes,
and exact pypdfium2/PDFium and Qt PDF identities. It is a record-only selection workload, not a
product latency target.

Validate every workload, fixture, golden, artifact, and reference-profile binding without loading
either native renderer:

```powershell
py -3.14 tools/dev.py validate-pdf-renderer-benchmark
```

Qt PDF is an evaluation-only candidate in a non-default dependency group. Prepare the exact locked
candidate closure before running the comparison:

```powershell
uv sync --locked --group renderer-evaluation
```

The comparison requires a clean repository revision and a Windows process constrained to the
profile's two logical processors. It also requires truthful 4096 MiB worker-envelope and integrated
graphics attestations. On a host with more processors, set the invoking PowerShell process affinity
for the duration of the command so every child inherits the same two-processor mask:

```powershell
$process = [System.Diagnostics.Process]::GetCurrentProcess()
$previousAffinity = $process.ProcessorAffinity
try {
  $process.ProcessorAffinity = [IntPtr]3
  uv run --locked --no-sync --group renderer-evaluation python -m tools.pdf_renderer_benchmark benchmark --scenario local-smoke --attest-memory-mib 4096 --attest-graphics integrated
} finally {
  $process.ProcessorAffinity = $previousAffinity
}
```

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
observed installed `RECORD` and stage-closure hashes. It also records corpus/profile identities,
isolated-worker readiness, imports, document-open and page-render timings, nearest-rank warm
p50/p95/max values, and a parent-observed monotonic launch-through-exit envelope that bounds the
strictly positive child timing evidence,
Job-reported process/job memory peaks, sampled private temporary-storage high-water, cooperative
cancellation at the documented between-render checkpoint, idle-worker Job-tree termination, and
stable malformed/high-dimension outcomes. It contains no source path, filename, page pixels,
document content, user or machine identity, or native exception text. The benchmark does not claim
that cooperative cancellation interrupts an in-flight native render.
