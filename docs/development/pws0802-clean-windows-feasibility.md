# PWS-0802 — clean Windows qualification feasibility

**Current scope:** Windows 11 x64 only under PWS-D043 and
[ADR 0019](../adr/0019-windows-11-x64-only-scope.md). Earlier ARM64/emulation
research and unrun scenarios below are retained as superseded history, not passed
or remaining prerequisites. No ARM64 environment access is requested.

**Continuation, 2026-11-04:** the maintainer has installed Windows Sandbox.
Codex verified the enabled feature and official Microsoft package, created an
offline Windows 11 Enterprise 26100.9711 AMD64 guest, and established a genuine
standard-account interactive logon. R4's complete offline document smoke and
preserve removal pass; its earlier failed attempts remain recorded separately.
R6 is built and artifact-audited. In a new guest, its first launch, keyboard focus,
plain recovery message, full offline smoke, repair and preserve uninstall pass.
Reinstall/reopen and final preserve removal also pass through a separately bound
completion, with the original helper failure retained. Controlled worker and
installer interruption/recovery also pass. R7 changes only the removal entry
point and candidate marker in the installed payload. Its independent artifact
audit and full frozen-source privacy/secret scans pass. Install, repair, public
launch, cancellation and ordinary preserve removal pass in the existing
instrumented standard-account guest. This is separate from R6's original clean
proof. R7's Keep path has actual NVDA control/confirmation/completion observations.
A separate fresh-reader cancellation passes mechanically while retaining its
initial stale-reader-context gap. In the subsequent first-review diagnostic,
NVDA announced all relevant controls before any explicit UIA query, and keyboard
Cancel passed. Earlier intermittent context gaps and their unproved cause remain
recorded. Current qualification uses finite normal-use checks; it does not demand
universal first-focus reliability. No new clean-R7 or trusted-signature result is
inferred. The current observations and remaining x64/signing requirements are in
the [x64 continuation](pws0802-x64-continuation-20261104.md). No Sandbox
enablement, Windows agreement acceptance or general manual test is requested.
The feasibility investigation below is retained as historical evidence.

Observed on 2026-11-03. This is a read-only feasibility review, not a clean-guest
test result, OS entitlement receipt or authorization to acquire test tools. No
VM, partition, service, optional feature, host setting or installation changed;
no OS image or virtualization executable was acquired. All prospective tools
below belong only to ignored qualification output, outside the product payload.

## What this host actually exposes

The earlier read-only `output/pws0802/research/virtualization-preflight.json`
found no Sandbox, Hyper-V management namespace/cmdlets, VBox, VMware or QEMU
entry point and no repository VM image. Docker's configured Linux-engine pipe
was unavailable. This establishes no ready clean guest, not absence of every
virtualization API.

A subsequent call to the documented
[WHvGetCapability API](https://learn.microsoft.com/en-us/virtualization/api/hypervisor-platform/funcs/whvgetcapability)
loaded the known System32 `WinHvPlatform.dll` and queried capabilities only.
`HypervisorPresent` returned HRESULT zero and value one; `Features` returned
HRESULT zero and value 767. The exact ignored receipt is
`output/pws0802/research/whpx-capability.json`. No `WHvCreatePartition` or guest
execution occurred. Available API capabilities therefore warrant an actual
bounded WHPX probe later, without first enabling a host feature.

The host/process are x64. A later read-only CIM/volume observation found an Intel
Core i7-1185G7, four cores/eight logical processors, approximately 19.4 GiB visible
RAM and 485.7 GiB free on the repository volume. Free RAM was approximately
5.7 GiB while other work ran; this is transient, not a reservation or permission
to allocate all remaining memory. Recheck resources and serialize with native
qualification before launching a guest.

## Portable native QEMU has a TPM limitation

[QEMU's WHPX documentation](https://www.qemu.org/docs/master/system/whpx.html)
describes hardware acceleration on Windows x64 and ARM64. Its x64 backend is
tested from Windows 10 version 2004; the ARM64 backend needs Windows 11 24H2 with
the stated April/May 2025 updates. Retain the security-domain default: the
documented `ssd=off` optimization weakens a mitigation and is not an acceptable
qualification workaround.

However, upstream [QEMU v11.1.0 build configuration](https://gitlab.com/qemu-project/qemu/-/raw/v11.1.0/meson.build)
explicitly excludes TPM emulation on Windows. The native Windows executable
alone is therefore not a demonstrated compliant Windows 11 route. An external
TPM server does not establish that the excluded QEMU device/backend exists.

[Microsoft's Windows 11 VM requirements](https://learn.microsoft.com/en-us/windows/whats-new/windows-11-requirements)
include UEFI/Generation 2, 64 GB storage, 4 GB RAM, at least two virtual
processors, Secure Boot capability and virtual TPM. The host processor must meet
the applicable processor requirements. Do not use setup registry bypasses,
replace hardware checks, disable host security, or call an unproved virtual
configuration certified hardware.

## Agent-owned route worth attempting after acquisition authorization

Use portable Windows QEMU/WHPX for a small Linux tool guest, then run Windows
inside Linux QEMU with `swtpm` and enrolled Secure Boot firmware. This moves the
TPM backend onto its supported POSIX host without changing the Windows host.
Inner TCG avoids assuming nested virtualization is exposed; performance and
Windows eligibility remain to be measured. No successful guest is claimed.

The [upstream TPM documentation](https://www.qemu.org/docs/master/specs/tpm.html)
describes the separate `swtpm` process, TPM2 mode, firmware initialization and
QEMU socket/backend/device connection. It includes unprivileged POSIX examples;
they are not native Windows recipes. No host physical TPM is passed through.
For x64, use the distro's matched OVMF code/NVRAM pair with Microsoft keys,
not the `snakeoil` assets. [Ubuntu's package file list](https://packages.ubuntu.com/noble/all/ovmf/filelist)
contains `OVMF_CODE_4M.ms.fd`, `OVMF_VARS_4M.ms.fd` and secure-enrolled firmware
metadata. [libvirt's Secure Boot explanation](https://libvirt.org/kbase/secureboot.html)
distinguishes firmware support from active enforcement with enrolled keys.
Verify both in the guest; a firmware filename alone proves neither.

Prospective acquisition manifest; unresolved rows are deliberately not locks:

| Tool or image | Primary source and prospective selection | Required receipt before execution |
| --- | --- | --- |
| Windows QEMU tool guest host | [QEMU Windows distribution choices](https://www.qemu.org/download/) link MSYS2 and Stefan Weil; select the [MSYS2 UCRT64 QEMU package](https://packages.msys2.org/packages/mingw-w64-ucrt-x86_64-qemu), currently `11.1.3-1` | Package SHA-256 `72ba6064c81408a8f433859502405da7a93e012d2b9b9bcf7f66f07750882b34`, source/signature and bounded dependency-closure verification; local extraction, no global install |
| Linux tool guest | [Ubuntu Minimal 24.04 release directory](https://cloud-images.ubuntu.com/minimal/releases/noble/release/), observed image build `20261027`, amd64 QCow2 | Pin dated image, signed SHA256SUMS, exact image hash and package inventory; copied writable overlay; retain original bytes |
| Linux guest automation | [cloud-init NoCloud](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html) configuration using generated ephemeral credentials and a bounded seed | Hash the seed; bind any host control listeners to loopback; never expose the repository, user profile, clipboard or private cases |
| Linux QEMU, swtpm and firmware | Official Ubuntu repositories; packages `qemu-system-x86`, `swtpm`, `swtpm-tools`, `ovmf` with required dependencies | Authenticated package versions, licences, hashes and firmware pairing; enable neither a host service nor arbitrary device pass-through |
| Windows guest | Current [Enterprise evaluation ISO entry](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise), selected architecture and agreement not yet acquired | Registration eligibility, controlling terms, authorized acceptance, Microsoft hash evidence, ISO hash, setup/security and activation/expiry receipts |

QEMU is GPLv2 with file/component-specific notices; the MSYS2 package publishes
its combined licence identifiers. Preserve the actual acquired notices and
source provenance. These are internal test tools and do not become runtime,
installer, SBOM or user prerequisites.

Before Windows setup, inspect the exact acquired media's terms without installing
or accepting them. Stop if registration or displayed terms need an unprovided
owner identity, account, licence or agreement. An unattended answer file is a
supported [Windows Setup mechanism](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/automate-windows-setup),
not permission to accept terms. Keep agreement acceptance unset until authorized.
Build the complete setup/control/recovery journey as agent work, using only
synthetic inputs and explicit operation deadlines. No maintainer environment
construction, hash calculation, JSON editing or path transcription is required.

After setup, retain a clean baseline before adding only the candidate and
qualification helpers. Keep Python, uv, Visual Studio and preinstalled third-party
CRT absent from the Windows test baseline. Check Secure Boot enforcement, TPM2,
guest architecture, CPU/RAM/storage, OS build, exact runtime loads and test-user
privilege. Disable guest external networking for offline app tests after any
authorized OS activation/provisioning; do not weaken Windows security.

An ARM64 Windows guest on this x64 source host requires cross-ISA emulation,
not x64 WHPX acceleration. Linux QEMU's ARM `virt` machine, suitable ARM64 UEFI,
TPM2 and Secure Boot need separate validation; x64 OVMF is not ARM firmware.
[Windows 11 on ARM supports x86/x64 user-mode emulation](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation),
which was the earlier prospective application route and is now superseded by
PWS-D043. No ARM64 guest, emulation result,
processor eligibility, native ARM64 product build or physical-device support is
proved by that documentation or by the x64 host capability query.

## Media, expiry and remaining owner authority

The current Microsoft Enterprise ISO pages describe registration and a 90-day
evaluation for IT professionals evaluating on behalf of an organization. They
also describe Microsoft-account sign-in and expiry/activation behavior. They do
not expose a clearly identified standalone controlling current ISO agreement
that this review can bind to exact media. Cached page descriptions disagree on
25H2 versus 26H2; choose neither label as a frozen image fact before resolving
and hashing the actual artifact. A downloadable ISO or lack of a product-key
prompt is not a licence or independent product-testing grant.

The former [developer VM download entry](https://developer.microsoft.com/en-us/windows/downloads/virtual-machines/)
now redirects to Windows developer-tool documentation and supplies no prebuilt
VM link. Do not use historical WinDev archive links or assume their fixed expiry
remains valid. A current [Windows deployment lab kit](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-microsoft-365-lab-kit)
does exist, but requires Hyper-V/admin rights, 150 GB storage and 16 GB available
RAM, and includes domain/management infrastructure. Its VMs expire 90 days after
provisioning. It is neither a minimal clean installer baseline nor an established
individual software-testing entitlement.

No additional Windows licence, evaluation acceptance, registration or qualifying
subscription has been inferred from the source host or the separate Visual
Studio Community proposal. There is no exact Windows owner acceptance question
ready yet: first capture the selected artifact's controlling terms and determine
that they permit the intended test. If they do, the smallest owner action is
accepting that exact identified agreement and authorizing agent acquisition and
temporary VM use; technical setup remains agent work. If they do not, acceptance
cannot enlarge the grant and a different legitimate entitlement is needed.

At this historical investigation stage, clean x64 qualification was **NOT RUN**;
the accepted later R6/R7 results at the top of this document have their own exact
bindings. The earlier unrun ARM64 scenario is now superseded/outside scope under
PWS-D043, not passed or remaining work. Source-host observations retain host-only
scope. This historical investigation grants no permission to publish unsigned
artifacts and creates no new owner environment or agreement request.
