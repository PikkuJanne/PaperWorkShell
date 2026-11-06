# PWS-0802 signing and Microsoft runtime review

**2026-11-04 scope and authority update:** PWS-D043 selects Windows 11 x64 only.
Earlier ARM64 investigation below creates no project prerequisite or owner
environment request. The maintainer has authorized one factual, nonbinding
Foundation inquiry; the [current text and actual route status](pws0802-foundation-inquiry-20261104.md)
supersede the earlier authorization question. Provider enrollment, additional
agreements, identity documents, installer uploads, purchases and public
publication remain outside that authorization.

**Microsoft authority update:** the [Community runtime result](pws0802-community-runtime-result.md)
records later explicit acceptance, actual product acquisition and final scoped
terms. Earlier pending-Microsoft statements below are historical. Signing-provider
facts retain their separate scope and do not imply enrollment or approval.

Reviewed 2026-11-03. This is an engineering review, not agreement acceptance,
signing enrollment, a licence grant, or public release approval. The maintainer
has selected Janne Vuorela as publisher/maintainer, MIT for first-party work,
free use including commercial use, Windows 11, local processing without product
accounts or telemetry, and an unsigned internal qualification candidate.
Production signing remains a separate requirement. No private material was read.

## SignPath Foundation

The preferred zero-cost route remains conditional. Foundation requires maintained,
documented, released OSS with OSI licensing, allowing a System Libraries exception.
It controls admission. Its certificate identifies **SignPath Foundation**; Janne
Vuorela remains the product maintainer, a separate identity. MIT alone proves no
whole-package eligibility. Unsigned upstream OSS binaries may be included in a
signed installer, but must not be re-signed as the project's own code. Modified
upstream signing has additional fork/review conditions. MFA, named team roles,
manual release approval, verifiable builds, consistent metadata and a published
signing/privacy policy are required. Bundled Microsoft runtime and installer
components need eligibility assessment; their acceptance is unproved. No
application, upload, contact, purchase or enrollment occurred. The page labels
its code of conduct draft. These are the [current Foundation conditions](https://signpath.org/terms.html),
not an approval of PaperWorkShell.

The [Foundation overview](https://signpath.org/) describes repository-origin
verification, HSM-held keys, no personal identification requirement and no OSS
service fee. Its [application page](https://signpath.org/apply.html) is the
prospective entry point; this review submitted nothing.

For the prospective GitHub route, [SignPath's connector documentation](https://docs.signpath.io/trusted-build-systems/github)
requires linked project/build-system configuration; audit-log checks require
repository access through its GitHub App. It verifies workflow/artifact origin
and requires GitHub-hosted jobs leading to an OSS signing request. This does not
qualify the current local working tree. Any eventual integration must use
reviewed, pinned CI actions and protected secrets. Build-service accounts are
maintainer infrastructure, never product-user prerequisites. Do not add the
Foundation's sponsorship attribution until approval actually exists.

## Actual R3 runtime, not a complete installer

Inspected the actual `resolved-stage-r3` runtime and its frozen
`resolved-inventory.json`, SHA-256
`8fb9366e08dee502a69858d8070b97a98ba9d55af232bd87fcf2a50ccad5eb2b`.
The inventory targets Windows x86-64 / CPython 3.14.6. R3 contains exactly one
Microsoft CRT DLL: the pikepdf-renamed object below. Its bytes, version,
Authenticode status and AMD64 PE machine `0x8664` were checked afresh.

| Object | Observed identity |
|---|---|
| `runtime/Lib/site-packages/pikepdf.libs/msvcp140-0f885b509a685d2bbfa652fed26b5fb3.dll` | 557,728 bytes; version `14.44.35211.0`; SHA-256 `0f885b509a685d2bbfa652fed26b5fb31d88fbdab0a978c641d1c7b8aa460aa9`; Authenticode `Valid`, Microsoft Windows |
| Canonical CRT names | `msvcp140.dll`, `msvcp140_1.dll`, `msvcp140_2.dll`, `vcruntime140.dll`, `vcruntime140_1.dll` are absent from R3 |
| Existing reference installer | `VC_redist.x64-reference-20261103.exe`; 25,635,768 bytes; SHA-256 `cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b` |

All five cached copies of that reference installer match its recorded hash.
Its bootstrapper is x86 PE `0x14c`; this is distinct from its x64 runtime payload.
The existing reviewed receipt records product version `14.44.35211.0` and a
valid Microsoft Corporation signature. Its immutable [official download](https://download.visualstudio.microsoft.com/download/pr/990c720a-46ce-4dc7-87b9-c2eb615d34b9/CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe)
and exact source evidence remain in the
[native review](../../packaging/release-reviews/qt-pdfium-components-v1.json).
No redistributable installer was executed by this review.

R3's accepted native smoke used five external System32 objects of that version.
It therefore does **not** meet the new complete-runtime requirement by itself.
Do not copy system DLLs, restore arbitrary wheel CRT files, relabel that evidence
as clean-machine qualification, or generalize the exact pikepdf downstream grant.

## Selected prospective free redistribution route

Use **Visual Studio Community 2022, individual developer route**, subject to the
owner's explicit acceptance and legitimate acquisition. Its [official agreement](https://visualstudio.microsoft.com/license-terms/vs2022-ga-community/)
section 1(a) permits an individual's own free or paid applications. Section 4
permits listed object-code distribution, with substantial application
functionality and protective recipient terms, and excludes preview runtime
distribution, misleading Microsoft branding and imposing source obligations on
Microsoft code. Unlike the historical VS2019 Professional agreement retained
for pikepdf, this inspected Community agreement has no distributor indemnity
clause. No earlier contract is amended or silently replaced by this observation.

The [official Community agreement DOCX](https://visualstudio.microsoft.com/wp-content/uploads/2021/11/Visual-Studio-2022-Community-License-EN.docx)
was retrieved read-only: 62,859 bytes, SHA-256
`41a207b10c8ab91d0d2f10a854715f73dca54509581692d2fe179aa3ffcb8540`.
Retrieval is not acceptance. The [VS2022 REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)
requires a validly licensed copy of the named Visual Studio edition. It includes
unmodified release files under `VC\redist`, excluding debug directories, and
explicitly applies the licence restriction to runtime downloads obtained through
Microsoft's other channels. The end-user VC Runtime agreement alone supplies no
redistribution entitlement.

The [March 2024 Build Tools agreement](https://visualstudio.microsoft.com/license-terms/vs2022-ga-diagnosticbuildtools/)
has a narrow exception for building required third-party OSS dependencies without
a Visual Studio product licence. It does not supply the missing Community
product entitlement for PaperWorkShell's own redistribution. Its inspected DOCX
is 34,395 bytes, SHA-256
`2f66b86a00e8d9833789897ce23d05a4a2dbea370cf39c8c1098dbc17d0e7bdc`.

**One owner action:** review the exact Community 2022 agreement linked above and
explicitly accept it as the individual developer, authorizing Codex to acquire
the licensed Community product and use its section 4 route for the official
unmodified x64 runtime, and approve or correct the concrete
[Microsoft-only recipient-text proposal](pws0802-microsoft-recipient-terms-proposal.md). No owner installation, environment repair, transcription
or private credential is requested. Owner acceptance/acquisition has not been
inferred from the MIT choice or general engineering authorization.

The agreement does not expressly require an installed or running IDE before
section 4 applies. Microsoft separately documents [acquiring an offline layout](https://learn.microsoft.com/en-us/visualstudio/install/create-an-offline-installation-of-visual-studio?view=vs-2022)
and subsequently installing it. A complete authenticated Community product-copy
receipt is still needed; a statement of acceptance or the existing runtime
download alone must not be labelled completed entitlement. This distinction is
the proposed acquisition method, not a legal determination for an unseen copy.

## Acquisition and packaging plan; execution pending acceptance

1. Obtain Community **2022 / 17.14 Current** using the Community entry on
   [Microsoft's release-history page](https://learn.microsoft.com/en-us/visualstudio/releases/2022/release-history).
   Resolve its official URL, authenticate Microsoft signature and record exact
   bytes, SHA-256, product/version/channel and the applicable agreement. Do not
   use the current general Downloads button, which now offers Visual Studio 2026.
2. Acquire a bounded Community product layout, requesting only the required
   `Microsoft.VisualStudio.Component.VC.Redist.14.Latest` component and required
   product dependencies, with `--lang en-US`. This component is in the
   [official Community 2022 directory](https://learn.microsoft.com/en-us/visualstudio/install/workload-component-id-vs-community?view=vs-2022).
   Do not request all workloads, optional components or a global installation.
   Microsoft's documented layout command expects administrative execution;
   availability must be demonstrated without assuming this source host can do it.
3. Verify product identity, signatures, package manifests, hashes, selected
   agreement and exact REDIST membership. Preserve this acquisition receipt.
   If any acquired component has different terms, stop that inclusion for review.
4. Prefer an unmodified **app-local x64 release CRT** closure from that authorized
   route for the proposed standard-user installation. Keep Windows system/UCRT
   components in their OS role. Prove actual loads without preinstalled VC CRT.
   If versions differ from R3's tested reference, qualify the new exact closure;
   never reuse the older receipt as its result. Keep the pikepdf-renamed import
   satisfied under a documented matching entitlement and byte-preserving route.
5. Implement component-scoped recipient terms without changing first-party MIT
   or Qt/LGPL replacement/relinking/reverse-engineering rights. Regenerate the
   exact candidate inventory, notices, SBOMs and provenance. Unsigned internal
   qualification is authorized; public redistribution and production signing
   remain distinct, unsatisfied steps.

## Clean Windows 11 capability observations

The read-only receipt `output/pws0802/research/virtualization-preflight.json`
records an x64 host/process and a present hypervisor. Sandbox, Hyper-V command
entry points and CIM namespace, VBox, VMware and QEMU were unavailable; no VM
image was found inside the repository. Docker's CLI is present, but its configured
Linux-engine pipe is absent. The newer Sandbox CLI/Appx package is absent and
`vmcompute` is stopped. No service, optional feature, VM or host setting changed.

These facts establish no ready clean guest and do not prove provisioning
impossible. [Windows Sandbox](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/)
provides a disposable clean guest on eligible Windows editions and can disable
networking. It does not represent every claimed edition or architecture. Official
[x64/ARM64 installation media](https://www.microsoft.com/en-us/software-download/windows11)
and [Enterprise evaluation media](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise)
are prospective alternatives with their own licence/setup requirements. No
registration, OS agreement acceptance, ISO acquisition or OS installation was
performed. Guest provisioning and automated qualification remain agent work;
these observations are not a request that the maintainer repair the environment.

This review changes no existing licence asset, dependency inventory, runtime,
application, task status, accepted evidence, case or sibling product boundary.
