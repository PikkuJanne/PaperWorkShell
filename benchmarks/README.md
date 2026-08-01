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
