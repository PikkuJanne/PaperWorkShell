# PWS-0802 — gated Microsoft runtime overlay preparation

**Historical pre-acceptance engineering review.** The later explicit authority,
actual Community product acquisition, nine-path overlay and final recipient terms
are recorded in [the current result](pws0802-community-runtime-result.md).
The earlier conditional facts below retain their original scope.

This is engineering preparation for a later candidate, not a redistribution
approval. The compiled/sealed installer R1 retains its external CRT prerequisite.
No existing candidate, application, inventory, installer flag, signature, licence
asset, task metadata or release evidence is modified by this work.

## Exact data provenance

The prior `external-crt-probe-v1` and `v3` receipts observed five canonical DLLs
loaded from System32. They did not contain extracted package members or establish
clean-Windows operation. The v2 probe removed the renamed pikepdf MSVCP object
and failed; v3 retained that required object and passed host-only smoke. Those
historical outcomes remain unchanged.

The existing official reference file was inspected as data only:

- `VC_redist.x64-reference-20261103.exe`, 25,635,768 bytes, SHA-256
  `cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b`;
  [exact official source](https://download.visualstudio.microsoft.com/download/pr/990c720a-46ce-4dc7-87b9-c2eb615d34b9/CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe).
- Fresh Authenticode status: Valid, Microsoft Corporation, signer thumbprint
  `8F985BE8FD256085C90A95D3C74580511A1DB975`. The file remained byte-identical.
- Attached cabinet at byte offset 686,152: 24,939,223 bytes, SHA-256
  `468f1264d50b9e3b9d309ab9e13c02ff379fd95a2837383bf65c9616583013d6`.
- Outer member `a9`,
  `packages/vcRuntimeMinimum_amd64/vc_runtimeMinimum_x64.msi`: 212,992 bytes,
  SHA-256 `fe5af8f2efc9bbdd2c23f22f2d3daf480a4047acf3b1edc6ecda81147c1ec14f`.
  Product code `{43B0D101-A022-48F4-9D04-BA404CEB1D53}`, product version
  `14.44.35211`, ProductCPU `amd64`.
- Outer member `a12`, `packages/vcRuntimeMinimum_amd64/cab1.cab`: 987,836 bytes,
  SHA-256 `640aa6c516c72444523b8fbe034db46ff4e118ed02705340e3ccb62d426ff040`.
  Its members match the read-only MSI File/Media tables. Bundle-manifest hashes
  for the attached cabinet and extracted payloads also match.

The five unmodified objects total 1,048,216 bytes. All have file/product version
`14.44.35211.0`, PE machine `0x8664` (AMD64), and fresh Authenticode Valid status.
Each cabinet member is its canonical name followed by `_amd64`:

| Canonical name | Bytes | SHA-256 | MSI sequence |
| --- | ---: | --- | ---: |
| `msvcp140.dll` | 557,728 | `0f885b509a685d2bbfa652fed26b5fb31d88fbdab0a978c641d1c7b8aa460aa9` | 2 |
| `msvcp140_1.dll` | 35,952 | `bfad5aef4c63a669e3c140655cdfdf395b6c979b400a447bd5dcb65ed8826c3d` | 3 |
| `msvcp140_2.dll` | 280,200 | `3ea06f0ee098b4823cb79599df3780e7f23cce52c19aac31d2a0d47efe33a5e9` | 4 |
| `vcruntime140.dll` | 124,544 | `d5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066` | 10 |
| `vcruntime140_1.dll` | 49,792 | `1f2d41c4aa5db0bc33ebf7b66d72943a817d7ce6cbe880502a9403823633093f` | 11 |

The first, fourth and fifth objects use Microsoft Windows signer thumbprint
`667BBA1E1F478221AA65DE4F75187A751B03A453`; the second and third use Microsoft
Windows Software Compatibility Publisher thumbprints
`81915C173D7FFCBF49EAA8CF7594696B29A035E1` and
`EC5F0D7EE2327688384B4FDF5D7633553A0D055F`, respectively. Matching immutable hashes
bind these inspected signature/version facts; the preparer does not load them.

Read-only records and neutral `.bin` research objects remain under ignored
`output/pws0802/research/redist-data-inspection/`. `provenance-review.json` has
SHA-256 `0f5e8abdfa41d96eaeb3c54394cfcbd9cf453ea38e99c2f6db10aaa93b64c664`.
Container decompression used [SetupIterateCabinetW](https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupiteratecabinetw)
with fixed neutral target names. MSI access used
[MsiOpenDatabaseW](https://learn.microsoft.com/en-us/windows/win32/api/msiquery/nf-msiquery-msiopendatabasew)
with `MSIDBOPEN_READONLY`, with source hashes checked afterward. No installer,
MSI installation session/custom action, downloaded component or target DLL was
executed. These objects were not staged or publicly redistributed.

## Licence authority stays explicit

The source inspection establishes bytes and provenance, not entitlement. The
selected prospective free route remains the individual developer licence for
[Visual Studio Community 2022](https://visualstudio.microsoft.com/license-terms/vs2022-ga-community/),
whose exact [agreement DOCX](https://visualstudio.microsoft.com/wp-content/uploads/2021/11/Visual-Studio-2022-Community-License-EN.docx)
has SHA-256
`41a207b10c8ab91d0d2f10a854715f73dca54509581692d2fe179aa3ffcb8540`.
Its section 4 conditions and Microsoft's [VS2022 REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)
require the appropriate licensed product copy and scoped protective recipient
terms. The end-user runtime agreement or possession of a runtime/bootstrapper
download alone does not establish that route. The earlier pikepdf-specific
VS2019 downstream grant remains distinct, including its original conditions;
this overlay does not silently replace it or alter its renamed DLL.

Owner acceptance, eligibility, legitimate Community acquisition and approved
recipient terms remain unprovided at this source freeze. No receipt saying they
occurred has been generated. First-party MIT and Qt/LGPL rights remain unchanged.
The tool neither asks the maintainer to install developer tools nor executes
Community acquisition. Acquisition/authenticity review, if authorized after the
owner's actual reply, is separate agent work.

## Preparer input contract

[`tools/prepare_windows_microsoft_runtime.py`](../../tools/prepare_windows_microsoft_runtime.py)
accepts three separately SHA-256-pinned approved receipts. They are trusted
coordinator attestations supported by actual acceptance and acquisition evidence;
their hashes bind exactly what was reviewed. The program cannot authenticate a
human's legal acceptance or manufacture entitlement from a boolean. It verifies
the closed receipt fields and actual receipted artifact bytes, and rejects missing,
changed or ineligible input before creating any output.

1. **Owner acceptance:** schema version 1, kind `owner-acceptance`, owner Janne
   Vuorela, `accepted_as=individual-developer`, exact agreement URL/hash,
   `accepted=true`, `agent_acquisition_authorized=true` and a bounded opaque
   `evidence_reference` to the actual authorization. This tool creates no such
   record and no CLI switch substitutes for it.
2. **Community product copy:** kind `community-product-copy`, completed acquisition,
   product `Microsoft.VisualStudio.Product.Community`, reviewed 17.14 product
   version/channel, selected `Microsoft.VisualStudio.Component.VC.Redist.14.Latest`,
   matching owner/agreement hashes and
   `licensed_copy_review=approved-valid-community-copy`. Every listed relative
   artifact is checked by size/hash beneath the selected owned copy root. The
   required roles are bootstrapper, product catalog, layout manifest, product
   package and redistributable package. A bootstrapper alone is rejected. Actual
   Microsoft signatures/catalog/package authenticity and acquisition sufficiency
   must have been established in the approved acquisition review; this copier
   does not infer those unseen facts from filenames or parse an unreviewed layout
   as an automatically valid licence. Limits are 512 artifacts and 2 GiB total;
   hashing streams bounded chunks and refuses growth.
3. **Recipient review:** kind `recipient-terms-review`, approved status, exact
   agreement/acquisition receipt hash, first-party MIT, Microsoft component-only
   scope, preserved Qt/LGPL rights, exact version/AMD64/five member hashes and
   the SHA-256 of the approved terms asset. The existing pikepdf alias/downstream
   route is explicitly unchanged. The tool does not draft, accept or modify terms.

All paths remain inside the repository. Inspection inputs include the exact
outer reference, neutral inspected MSI/cabinet and five pinned member objects.
Link/reparse/hardlink/stream-like/traversal/device names and changed files fail
closed. Output must be a new directory beneath ignored `output/`, with an
existing safe parent. Windows directory traversal pins protect publication;
file creation is exclusive with flush and exact readback. Failure cleanup removes
only identified files/directories created by this attempt, never recursively
deletes unknown content or alters source inputs.

After every gate passes, output consists only of `runtime/` with the five fixed
canonical DLL names and `overlay-receipt.json`. The receipt always records
`PWSCompleteRuntime=false`: integration, complete component ownership/inventory,
notices/SBOM, exact GUI and restricted-worker module-load proof and clean-Windows
qualification remain separate work. No existing stage/configuration is rewritten.
The alias already required by pikepdf remains in its original base stage and is
not duplicated, replaced or relabelled by this tool. No Windows-owned UCRT/MSU,
ARM64, MFC, debug or other unreviewed payload is copied.

## Verification scope

Focused tests use only generated synthetic PE-shaped data and explicit isolated
monkeypatches of the reviewed pins and native-directory boundary. They cannot
serve as actual owner acceptance, authentic Community acquisition, real Microsoft
runtime staging, packaged qualification or licence evidence. Production exposes
no skip-licensing, test-mode or unchecked complete-runtime CLI switch.

Verification covers missing/false/ineligible/stale receipts, bootstrapper-only
acquisition refusal, exact owned overlay contents, input preservation, hash/path/
hardlink refusal, existing-output preservation, partial-write cleanup and unknown
content preservation. All real-package work in this task remains data-only;
the preparer has not been run with fabricated receipts against Microsoft bytes.
Canonical focused test receipts and source hashes are retained under ignored
`output/pws0802/microsoft-runtime-tool-qa/`. Final shared QA and release metadata
remain coordinator-owned, with no sibling dependency or support-data access.

The final focused canonical lanes pass 19 unit tests (7,624 deselected,
8.72 seconds) and 11 security tests (1,249 deselected, 4.37 seconds). Canonical
lint, format check (916 files), strict typing (916 source files) and documentation
validation pass. An early helper-only typing failure involving synthetic
bytearrays and dynamic dataclass replacement was corrected; its raw receipt is
retained, with no rule suppression. Native-directory behavior is deliberately
not exercised during concurrent candidate qualification.

The actual CLI was invoked against the inspected cache without owner/acquisition/
recipient receipts. It returned `MICROSOFT_RUNTIME_RIGHTS_REQUIRED`, exit 2, and
the requested output directory remained absent. That is the only real-cache
preparer invocation. It is a refusal result, not successful runtime staging or a
rights determination; no synthetic approval was supplied for Microsoft bytes.
