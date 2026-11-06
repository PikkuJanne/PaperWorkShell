# PWS-0801 — exact dependency staging, notices and SBOM

**Status: done — exact internal dependency candidate, provenance, obligations and applicable QA are verified.** Resumed 2026-11-03 after
maintainer approval of the exact lxml omission proposal. Dependencies PWS-0005 and PWS-0707
are done. The unchanged [2026-11-02 audit](pws0801-audit-20261102.md) preserves the original
blocked result, evidence and alternatives. Its current-state conclusions are superseded here.

## Implemented

The [resolved inventory contract](../../schemas/resolved-dependency-inventory-v1.schema.json)
identifies one actual Windows x86-64 dependency-stage package. The policy baseline and Python
lock retain their original meanings. The reviewed records under `packaging/release-reviews/`
bind selected wheels, interpreter, engines, data, embedded libraries, build tools, notices,
corresponding sources and local recipes to exact artifacts. No dependency range was upgraded.

Candidate `pws0801-dependency-stage-20261103-r3` uses internal version `0.0.0`. Its actual application
source archive, separate release-tooling snapshot and consumed recipe/review hashes identify the
working source; Git HEAD alone does not. Each file has one owner, size, SHA-256 and SPDX-required
SHA-1. Consumed archive hashes remain separate from staged-file hashes. SHA-1 is not used for
input authenticity. Missing, changed, extra, aliased, unowned or unsafe files block verification.

The output procedure generates CycloneDX 1.6, SPDX 2.3, a readable notice bundle, exhaustive
resolved inventory, input-verification receipt, deterministic reopened ZIP and `SHA256SUMS` for
that same candidate. Sidecars sit outside the payload to avoid circular hashes. Custom
LicenseRefs preserve full applicable runtime terms; unshipped build-only supplier declarations
are explicitly limited to the authenticated declaration, without inventing an author waiver.
Raw notice bytes retain original encoding/newlines, including
authentic GNU form feeds. Official SBOM schemas are vendored for offline test verification.

This internal dependency stage is distinct from PWS-0802's stable application identity, product
licence/version/channel, protective distribution terms, installer/signing and clean-machine
prerequisite qualification. It is not a signed installer, supported portable installation or
public-release approval. Only the first-party root uses NOASSERTION; third-party rights cannot
use that deferral. Later packages must regenerate and reconcile their own exact evidence.
[ADR 0017](../adr/0017-release-dependency-staging.md) explains the staging boundary.

## Distribution findings resolved in this stage

- The [maintainer approval](../../packaging/release-reviews/lxml-exclusions-v1.json) authorizes
  omission of exactly two reviewed unlicensed resources. Wheel/source CRLF/LF representations
  are separately hashed and equivalence is proved. Remaining source bytes are unchanged. The
  unfiltered lxml source archive is never shipped. No rights waiver or future-version approval
  is inferred.
- The complete unused pi-heif decoder is omitted, with its locked input and exact omission
  record retained. Upstream OCRmyPDF's existing optional-import fallback supports this without
  a code patch. Synthetic PDF/JPEG/PNG OCR and malformed-input refusal were exercised without
  leaving a namespace-package remnant.
- Historical runtime v1 remains unchanged and rejected for this package: its TIFF DLL imports
  GPL-2.0 JBIG. The pinned libtiff 4.7.2 source build disables JBIG, preserves the other codecs,
  all 206 exports and Windows I/O mode, and passes all 18 upstream CTests. Two fixed-epoch builds
  produce the same 557,070-byte DLL, SHA-256
  `1f41506b92ab95df1511410122ee2eade01dacb49d2206242f1e460426faeae2`.
  Earlier POSIX-I/O and strip-timestamp failures remain recorded as failures.
- [Runtime v2](../../packaging/tesseract-runtime-closure-v2.json) contains 38 files, 28 signed
  vendor providers and one explicitly source-built provider. Application admission binds its
  separate fingerprint; the rebuilt DLL is not vendor-signed. Corresponding sources, recipe,
  GCC runtime exceptions, MinGW startup support and file-specific mixed notices are included.
- Canonical Microsoft VC runtime DLLs are externally installed prerequisites. The isolated
  mixed-route proof identifies System32 loads and valid Microsoft signatures. Only pikepdf's
  exact hash-renamed runtime DLL remains under its explicit downstream notice and full terms.
  The failed all-CRT-removal probe is preserved. A signed reference installer is hash-bound
  review input, never executed or included in the payload. Final protective terms and clean
  prerequisite installation remain PWS-0802 requirements.
- Dynamic Qt Core/Gui/Widgets and Windows platform/style plugins have corresponding sources and
  replacement instructions. Addons, Qt PDF, QML, Quick, Network, Designer and unused plugins are
  excluded. Actual generated Qt features and PDFium compiled dependencies were inspected.
- Cargo records distinguish vendor-declared build closure from actual Windows embedding. Native
  tool notices preserve supplier declarations without inventing author public-domain waivers
  or redistributing an unlicensed generator script as a notice.

## Reproduction and exact evidence

The [packaging procedure](../../packaging/README.md) documents explicit caches, signature checks,
source snapshotting, assembly, validation and freezing. Build-time downloads are separate and
opt-in; normal validation/tests are offline. Downloads and generated candidate outputs remain
under ignored `output/`. The [emergency update policy](emergency-security-updates.md) covers
compromised/abandoned dependencies without bypassing provenance, licensing or worker review.

The [release evidence index](../release/RELEASE_EVIDENCE.md#pws-0801-resolved-internal-dependency-candidate)
records the exact candidate: **957 components, 3,153 files, 622,288,120 expanded bytes, 175
consumed inputs and 32 source/review identities**. The verified ZIP is 502,569,903 bytes,
SHA-256 `e14bd67b4b9e4f5bfb6f21eabbe3dcc7d2a03fe721fde33bbb11a4fb97e1e15d`.
Both generated SBOMs pass official schemas offline. The isolated constructor uses 180 files
from eight authenticated archives; before/after verification proves its files and recipe/input
identities unchanged. It loads only the six reviewed Python support distributions and verifies
all 14 GPG executable/DLL objects before execution. Its preparation receipt and manifest are
consumed input artifacts, anchored outside the builder payload.

All 13 final-runtime smoke steps pass: installed application import, exact omissions and v2
admission including German, native Qt Windows/style rendering, PNG/JPEG normalization and
malformed refusal, font shaping/PDF reopen/extraction/render, qpdf success/malformed refusal,
PNG/JPEG/PDF OCR, cancellation and exhaustive stage identity before/after. The generated OCR
page was inspected: the large synthetic phrase is clear and unclipped. All 2,414 runtime
files are byte-identical to the preceding native-engine integration stage; additions concern
licensing/build-source attribution. Neither source documents nor accepted goldens were changed.

### Changed files

- `tools/release_artifacts.py`, `release_staging.py`, `release_sbom.py`, `release_evidence.py`,
  `build_dependency_stage.py`, `prepare_dependency_builder.py`, `snapshot_release_tooling.py`,
  `build_libtiff_no_jbig.py`, `stage_lxml_source.py`, and the retained redistribution auditor:
  bounded source/artifact authentication, construction, source derivation and exact evidence.
- `schemas/resolved-dependency-inventory-v1.schema.json`, synthetic contract/official-schema
  fixtures, and corresponding unit/security/integration tests: closed versions, standards and
  failure-path evidence.
- `packaging/release-reviews/`, `packaging/licenses/release-*/`, the native recipe, and runtime
  manifest v2: original license bytes, selected routes, complete normalized input/component
  identities and explicit observed-versus-supplier-declared limits.
- `src/paperworkshell/worker/client/tesseract_runtime.py`, its packaged manifest v2, and admission
  tests: select the separately fingerprinted 38-file runtime and reject legacy/tampered inputs.
- `.gitattributes`, `tools/dev.py`, `tools/validate_schemas.py`, task/bundle metadata and affected
  development/packaging/release docs: preserve raw bytes, register checks and retain provenance.
- `tools/validate_synthetic_fixtures.py`, `tools/_reviewed_upstream_notices.py`, exact upstream
  attribution provenance and security regressions: recognize immutable public notice attribution
  only in repository source, retaining strict document-fixture and other privacy checks.

### Verification and evidence epochs

Broad QA below predates the final email-only repository-attribution tooling change. The later
focused checks qualify that delta; they are not a claim that every broad lane ran again on the
latest tooling snapshot. Receipts are under `output/pws0801/resume-20261103/`.

- Original-root canonical unit run: **7,352 passed, one failure**, 1,111.12 seconds. The failure
  was the historical scan-quality benchmark's Git-index/source-revision binding; deterministic
  metrics still matched. In an independent same-product QA clone, canonical `test-unit` then
  completed **7,396 passed, one existing synthetic-symlink capability skip**, 1,040.42 seconds.
  Its exact checkpoint is `25f43e01efcd659cccd6964a28059ecfe863ef34`. The QA baseline changed
  only `revision`; every metric, corpus/profile/version/resource field stayed identical. The
  original benchmark bytes and original Git objects, index, HEAD and refs were preserved.
  See `quality-source-proof-v2/prepared-proof.json`, `published-proof.json`, `unit-proof.json`
  and `test-unit.log`; no all-history secret-scan result for the QA clone is claimed.
- Selected canonical native integration run: **155 passed, one directory-link capability skip,
  one original-source failure, 536 deselected**, 1,817.52 seconds. The literal base-interpreter/
  consumed-venv-hint failure remains recorded. An observer captured only the sanitized
  `WORKER_WINDOWS_CONFIGURATION_INVALID` / `config.resource_identity` pair. The same exact node
  subsequently passed with verified owned-source import scope: **one passed, 692 deselected**,
  43.17 seconds. See `test-integration-native.txt`, `venv-hint-launch-observer.jsonl`,
  `venv-hint-qa-scope.txt` and `venv-hint-qa-scoped-retry.txt`. This retry does not turn the
  original whole integration invocation into an all-pass result or change original-source ACLs.
- Full canonical security: **1,158 passed, six existing link-capability skips**, 436.43 seconds.
  Full canonical accessibility: **340 passed**, 28.88 seconds. See `test-security-final.txt`
  and `test-accessibility-final.txt`. These results belong to their recorded broad QA epoch.
- Final attribution-only tooling delta: **31 security checks passed, two existing directory-link
  capability skips, 1,142 deselected**; **15 unit checks passed, 7,382 deselected**. The dedicated
  regression set had **10 failures and one pass before** the fix, then **11 passed afterward**.
  Exact public upstream attribution blobs are recognized only in the repository callback; core
  fixture defaults, changed/new-contact rejection, number/MRZ checks and document detection
  remain strict. See `attribution-security-final.txt`, `attribution-unit-final.txt` and
  `attribution-privacy-regression-{before,after}.txt`. No raw matched values were logged and
  no upstream notice bytes were changed.

- Current canonical `format-check`, `lint` and `typecheck`: pass on all 869 Python surfaces.
  Registered boundary, schema, release-inventory, dependency, Pillow, localization, benchmark,
  constrained-workload and quality self-checks pass. Completion-only metadata has separate final
  task, portfolio, documentation and source privacy receipts.
- Final R3 clean construction, freeze/reopen, both official SBOM schemas and **13 fresh native
  runtime smoke steps** pass. Before/after builder and exhaustive runtime checks pass; all 2,414
  runtime files also equal R2. See `assemble-stage-r3.txt`, `freeze-stage-r3.txt`,
  `actual-sbom-schemas-r3.json` and `runtime-smoke-r3/receipt.json`.
- Bootstrap: 63 passed; SBOM: 118 passed; libtiff: 18/18 upstream CTests and two equal fixed-epoch
  builds. The excluded renamed-archive regression had four failures before the fix and eight
  passing cases after it. Original lxml/TIFF/JBIG archives cannot be renamed into the payload.
- The initial complete secret/fixture lanes reached their unchanged 10,000-item budget at 10,466.
  Exact already-covered history/index pairs now reuse verified work: three new regressions and
  75 related security cases pass with two existing link-capability skips. Paths, raw data,
  aliases, final mutation checks and every resource limit remain checked.
- The final public-attribution regression had 10 failures/one pass before the fix and 11 passes
  after. All 78 reviewed files/60 digests and 234 altered versions were independently checked:
  core fixture defaults reject, repository attribution permits only immutable reviewed bytes,
  and number/MRZ/document checks remain enforced. The initial full fixture failure is retained;
  the corrected complete canonical lane passes 60 fixtures, 15 renderer/6 image-transform/3
  overlay references, 3,524 historical blobs, 1,012 index blobs and 2,036 working files.
  See `validate-fixtures-attribution-final.txt` and
  `upstream-notice-exception-independent-audit.json`.
- Independent preservation v3 passes: 1,212 of 1,226 pre-existing files remain exact bytes;
  the other 14 changes are authorized task files, with no missing files or unrelated drift.
  All seven frozen/control files, four benchmark files and 27 goldens match. Original HEAD,
  index and raw refs match before/after. Later completion metadata has a separate receipt.

### Final closure

Independent exhaustive R3 reconciliation passes: every packaged byte/hash, both SBOM graphs,
SPDX verification codes, 175 inputs, 32 current recipes/reviews, 822 licence references at 702
raw notice paths, 20 source paths, eight replacement guides, exact lxml omissions and current
application/tooling sources. See `final-candidate-audit-r3.json`, SHA-256
`3f07791d0c3b67531e1a5091cb7e1a4976de55380883ca4b95e64599c53885ec`.
The complete canonical source/history secret scan passes after the final tooling delta;
`scan-secrets-attribution-source-final.txt` is separate from the accepted earlier source scan.
Final completion metadata receipts use the `-completion-r3` suffix. They qualify task/bundle
publication and repository privacy, and do not relabel earlier broad QA epochs.

### Historical intermediate outcomes

The initial blocked audit is retained unchanged. Provisional POSIX-I/O/strip build failures,
partial CRT removal failures, source/wheel staging corrections, obsolete intermediate candidate
outputs and earlier raw review hashes remain separate from the accepted current candidate. Two
runtime-probe harness attempts used unsupported bitmap context management; the corrected probe
closes those objects explicitly and passes without a product-code change.

Clean-builder publication initially failed with WinError32 while the native lease was active;
an independent empty-directory publication probe failed under the same parent. No ACL/journal
or resource guard was relaxed. After normal native cleanup, preparation and both independent
before/after checks pass. Current code has bounded sharing-lock handling and sanitized BUSY
recovery. The source snapshot was regenerated after the mixed GCC compiler package expression
was corrected: compiler GPL-3.0-or-later parts do not inherit the runtime exception.

## Security/privacy/accessibility and portfolio boundary

No user cases, originals, document/OCR text, metadata or applicant fixtures are inputs. Errors
use closed codes; receipts use reviewed relative paths. Worker egress denial and resource policy
are unchanged. Bounded software evidence makes no physical-hardware certification claim. No UI
controls change and no accepted human observations are repeated.

PaperWorkShell remains complete without ThirdLife or siblings. No sibling repository, artifact,
service, adapter, shared runtime/database or private fixture is an input. Case ownership and
application interfaces are unchanged; the release interface remains draft. Installer, update,
release-gate and B4 facts remain with their existing owners. PWS-0802 is the next dependency-ready task. Final installer/product terms, signing and
clean-machine prerequisite qualification remain its work; no public-release approval is inferred.

## Task graph and intentional remaining scope

PWS-0801 is done. PWS-0802, PWS-0804 and PWS-0805 are newly ready; PWS-0802 wins the
normal priority/critical-path/task-ID ordering. No later task was implemented in this change.
The generated internal package is not a signed installer or supported portable installation.
PWS-0802 owns final product identity/licence/terms, signing and clean-machine prerequisite
installation; later packages must regenerate exact-candidate evidence. There are no remaining
PWS-0801 implementation deferrals or new sibling dependencies.
