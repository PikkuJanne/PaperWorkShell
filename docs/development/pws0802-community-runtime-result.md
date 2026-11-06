# PWS-0802 — acquired Community 2022 runtime route

## Authority and actual acquisition

The maintainer explicitly accepted EULAID `VS_Comm_2022_ENU.1033` as an eligible
individual, authorized official release-component acquisition, and authorized
correction/implementation of Microsoft-only recipient terms preserving MIT and
every OSS grant. This is new authority, not verbatim approval of the old proposal.
The coordinator first froze the 2,112-file / 32,531,274-byte preservation baseline,
`output/pws0802/community-20261103/preservation-baseline.json`, SHA-256
`1cb632a705eba04910c87f9e19f41c48fb0184dd079af48b8b8e923bee15a759`.

The [official Current Community bootstrapper](https://aka.ms/vs/17/release/vs_community.exe)
was acquired over HTTPS: 4,474,744 bytes, SHA-256
`13a57d61cd0e5d0b7f55ee2d84173374ceb5590eecbb17cec5b663ffaf4fb968`,
version `17.14.37827.9`, Authenticode **Valid**, Microsoft Corporation signer
thumbprint `AB172913A2960A224809EE8A0C371CD47A079B72`.
Its structured, hidden, bounded local-layout operation acquired the selected
`en-US` Community/CoreEditor product dependencies and
`Microsoft.VisualStudio.Component.VC.Redist.14.Latest`: **155 actual product
files / 1,390,324,709 bytes**. Catalog `VisualStudio/17.14.44+37827.9` identifies
Community 2022, Current channel, `RTW`, not Preview. The official bootstrapper's
subsequent `--layout … --verify --quiet --wait --norestart` returned native **0**.
All product files are hash-bound in the acquisition receipt.

This is an actual complete selected product copy, beyond a bootstrapper download
or licence-acceptance assertion. Microsoft's [local layout documentation](https://learn.microsoft.com/en-us/visualstudio/install/create-an-offline-installation-of-visual-studio?view=vs-2022)
describes this acquisition method. Section 4 of the [exact accepted agreement](https://visualstudio.microsoft.com/wp-content/uploads/2021/11/Visual-Studio-2022-Community-License-EN.docx)
and the [VS2022 REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)
require a validly licensed product copy and distribution conditions; neither
states an installed-IDE, activation or sign-in prerequisite for this route.
No system install was requested/dispatched, and no claim is made that Visual
Studio compiled the application. A fresh agreement download matched the accepted
DOCX SHA-256 `41a207b10c8ab91d0d2f10a854715f73dca54509581692d2fe179aa3ffcb8540`.

The first ignored acquisition wrapper had an incorrect cleanup-helper name after
the bootstrapper exited and failed before saving its final code. Its original
script/log remain retained; process exit closed its noninherited job handle.
Completion is proved by the separate successful official verifier, not inferred
from that failed wrapper or the presence of files. No purchase, provider
enrollment, external submission or public publication occurred.

## Exact bytes, pikepdf and qpdf

The acquired catalog's x64 runtime is version `14.44.35211`, 25,635,768 bytes,
SHA-256 `cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b`:
exactly the earlier reference. It was freshly decompressed as data from the new
product copy, without running the redistributable installer, MSI actions or DLLs.
The pinned CAB/MSI and five members match the [precise member map](pws0802-microsoft-runtime-preparation.md).
Fresh member Authenticode checks are **Valid**, AMD64, version `14.44.35211.0`.

Nine copies total **2,338,008 bytes**, with five unique hashes: five beside
embedded Python, the existing pikepdf MSVCP loader filename, and exactly
`msvcp140.dll`, `vcruntime140.dll`, `vcruntime140_1.dll` beside qpdf. The latter
three match actual qpdf PE imports; Python-root copies alone would not establish
the child's local resolution. The final recipient asset lists every path/hash.

The pikepdf alias is newly supplied from the independent Microsoft acquisition,
not copied from the old wheel. Object-code bytes, notices and signature remain
identical. The REDIST permission has no filename-retention condition; treating
the loader alias as the same unmodified object is a documented, independently
reviewed interpretation, not express Microsoft filename approval. The new
candidate's component record replaces its old external-CRT/pikepdf-MSVC ownership
and rewires references only for these Microsoft copies. Original wheel/0801
evidence and all three historical Microsoft notice files remain byte-identical,
clearly historical. Their old grant is not amended or relicensed, and no old
VS2019 distributor indemnity is inferred accepted. All other components/grants
remain intact. The coordinator owns final inventory integration.

## Recipient assets and public builder gate

The final reviewed assets under
`packaging/licenses/release-native/microsoft-msvc-runtime/` are:

- `COMMUNITY-2022-RECIPIENT-TERMS.txt`, 5,466 bytes, SHA-256
  `a5cdf1136b18611fc5235c657b7ad113a9ac338abe6c1af3c299205d9492c718`.
- `COMMUNITY-2022-FULL-TERMS.txt`, the exact 19,035-byte cached plain-text extract,
  SHA-256 `cbfa92f37dc9cc11735952457c4c68df6f6aec0dfdb895e31c15c1adc0ed7963`.
- `COMMUNITY-2022-ROUTE.txt`, 2,499 bytes, SHA-256
  `9dc5d40efc8acc857711535c16a88a894c38a48cf3d7176dda14093346ff86ce`.

Independent primary-terms review found no concrete issue. Protections cover only
the nine Microsoft paths, preserve applicable-law/regional exceptions and every
separate OSS grant, including Qt/LGPL replacement/relinking/debugging. Normal
commercial use stays free with no product account/key/subscription/upload. The
terms require downstream protective agreement and significant application
functionality, unmodified Microsoft code/notices, lawful/export-compliant use,
and the relevant trademark, excluded-licence and warranty/liability protections.
They impose no new indemnity. The development agreement is provenance; end users
need no Visual Studio acquisition. The coordinator owns the local agreement page
and exact-hash quiet acceptance. Passive notices alone are not recipient assent.

`tools.prepare_windows_microsoft_runtime.verify_overlay(directory: Path,
expected_receipt_sha256: str) -> dict[str, Any]` is the read-only builder gate.
It accepts only full-route schema 2; rechecks original owner/copy/recipient-review
hashes, acquired product artifact bytes, source EXE/MSI/CAB/member/AMD64 pins and
all nine output files. Only fixed expected directories are enumerated; extra,
changed, stale or unsafe objects fail without repair/writes. Version-1 engineering
five-file behavior remains supported by the preparer but cannot satisfy this
builder gate. Coordinator approval remains a trust anchor for legal eligibility
and authenticity review, backed by actual acquisition evidence above.

The actual `output/pws0802/community-20261103/overlay-r1` receipt has SHA-256
`f6d04e0bf003343c2edfead916a4010e9f3ba26238cd90fcbf95d6cbcec3066f`.
Preparation and public verification passed. An initial invocation with an
incorrect recipient-review hash refused `MICROSOFT_RUNTIME_RECEIPT_CHANGED`
before writes; corrected exact inputs passed. `PWSCompleteRuntime` remains
**false** in this overlay: exact candidate integration and actual GUI/worker/qpdf
module-origin tests remain coordinator-owned gates.

The dedicated pure tests cover legacy/new/future/invalid reviews, exact source
and nine-member mapping, legacy-builder refusal, changed acquisition/overlay,
unsafe paths/extra objects and identity-only cleanup after nested write failure.
No real case or native document worker was used. Exact final focused results and
independent tool review are retained with the acquisition receipts. Initial
scoped static attempts (uninstalled pyright; source-resolution-only mypy error)
remain failures; strict repository-configured mypy with `MYPYPATH=src` passed.

This completes the selected Microsoft surface; no repeat agreement acceptance is
required. Later complete-runtime, clean Windows 11 x64 standard-account and
interruption/recovery results retain their original candidate bindings in the
[current continuation](pws0802-x64-continuation-20261104.md). PWS-0802 still
requires trusted public signing and its applicable finite x64 observations.
Earlier unrun ARM64 scenarios are superseded/outside scope under PWS-D043, not
passed or future prerequisites. No sibling repository, service or shared data was
used; PaperWorkShell remains standalone. PWS-0803 remains dependent on PWS-0802.
