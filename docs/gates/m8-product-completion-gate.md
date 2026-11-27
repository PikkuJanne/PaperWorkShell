# Milestone 8 product-completion gate — PWS-0808

- Review date: 2026-11-27
- Artifact admission: **PASS** for unsigned engineering candidate `pws0807-tab-focus-engineering-r3`
- Product-completion gate: **in progress**. Formal review and sign-off are pending.
- Candidate: PaperWorkShell `0.0.0`, channel `engineering`, Windows 11 x64
- Build source: `54e0b916ac069ce00a0ce200c43e07f3a76d1784`
- PWS-0807 qualification and PWS-0808 starting revision: `20c62ed104e8a230b56854e9a0d5238bce1fde10`

## Artifact admission

The candidate is admitted to the M8 review on its exact byte identity and observed
unsigned state under PWS-D044. The host review used the retained files under
`output/pws0807/` and the frozen Git revision. All 13 direct prerequisites and
83 transitive prerequisites are `done` in the task graph at the starting revision.

| Artifact | Bytes | Independently verified SHA-256 |
| --- | ---: | --- |
| Qualification index, `r3-completion-evidence/qualification-index.json` | 49,527 | `4cfe72f050a2abd654a2b35efa843e0125c29c8b314c3713cee89d0e8a3794bc` |
| Qualified build handoff | 2,904 | `71819c9b00b33be65ecdde772020850cde32517a6fb08084c244feab82640755` |
| `PaperWorkShell-pws0807-tab-focus-engineering-r3-win-x64-UNSIGNED.exe` | 513,023,918 | `7791cef6be0062319a9c9f39a4bf130ded8f3914485eb50ab4e07d182c992889` |
| Installer receipt | 1,679 | `057407a387e55bacff420deec7e1cc0689c3dffa09317b280f2b5f8f391b9b74` |
| Resolved inventory | 9,638,270 | `a436ca25db576c7cc60936428381ee4e747030b869a77d6c5342c7b15df202a4` |
| CycloneDX 1.6 SBOM, `bom.cdx.json` | 12,704,822 | `5f925fd49afc7c1e5cbc5b95868c6b8f24503d150f1d916af4420e1435371a42` |
| SPDX 2.3 SBOM, `bom.spdx.json` | 5,366,726 | `f73be051f5ffb59bfdaa0445c0b7cc8f3f736b0276c8bb8736bad8f21f506379` |
| Frozen engineering interface | 36,332 | `db9ad671e63c93bd51daa3324f7b302ca3a80da51a761e5861a897e82dc8a6e8` |
| `PaperWorkShell-R3-synthetic-sample.zip` | 4,173,788 | `027410d15b8a62874d535fca5fdc625e46e272667a07b3c8096920f217b4f468` |
| Sample manifest | 13,446 | `a51a4ccf34b47793e4495b00e9300479264ad0f2f33780caf4722aea887eee90` |

### Provenance, signing, and packaged inventory

- Recomputed size and SHA-256 for all 246 qualification-index references, including
  12 tracked documents verified against their exact Git blobs. The qualified handoff
  at `r3-build-preparation/pws0807-tab-focus-engineering-r3/qualified-build-handoff.json`
  and the index agree on the source, installer, receipt, inventory, and installed marker.
  The source is an ancestor of the qualification commit. The canonical build receipt
  (`build-result.json`, SHA-256 `041b3fc3b9f808c941425ce17a9ef54a136f2f3095dd0422c2d43d68d26ede42`),
  three build logs, and all five prepared setup bundle members matched their recorded identities.
- Windows `Get-AuthenticodeSignature` returned `NotSigned` with no signer or
  timestamper certificate. The x64 PE header had an empty certificate table.
  The installer receipt agrees with the observed SHA-256, size, inventory and internal
  distribution scope. Its `qualification: not-run` records the build-time state.
  Installed engineering observations reside in the later PWS-0807 qualification index.
  An in-memory comparison with a changed expected installer hash was rejected.
- The source-to-payload comparison verified all 345 bound application files across
  the exact Git revision, wheel, source distribution and staged inventory. The installed marker SHA-256 is
  `5e564b0fef1b5e0ebe30ab19bdfe1d8c65872420889ac35c82d7237c73ab5f1e`.
  The exact-source release tooling rehashed 3,219 staged files totaling
  650,884,602 bytes, including 729 notice files, plus 178 build inputs and 47 review
  inputs. It reproduced the stage audit and matched all 3,219 entries in the
  receipt-bound installer file and target-check lists. Retained clean, repaired
  and replacement inventory receipts report the same resolved inventory SHA-256,
  file count and byte count. An in-memory change to one staged-file digest was
  rejected with `RELEASE_STAGED_HASH`.
- The release tooling reproduced both SBOMs and `THIRD_PARTY_NOTICES.txt` byte for
  byte. Both SBOMs passed the vendored official schema validators, describe 957
  components and agree on the SHA-256 and SHA-1 of all 3,219 payload files. The SBOMs
  record SHA-256 `bb52f260856bfb9f34f1af6e80a9e8fa61a71da127089aaa7d61d26ffbfbf4d3`
  for their normalized inventory input. The installer receipt hashes the raw resolved
  inventory shown in the table. The retained release-materials audit is bound at
  `r3-completion-compat/verified-release-materials.json`, SHA-256
  `b8b4875fe982baaa559dae42da2e72c423a211cab2290a2e007f26428e160530`.
- The external `THIRD_PARTY_NOTICES.txt` is generated from the full inventory and has
  SHA-256 `b09a78d7889d96bb12aaa977cee5ce004360c1e39a765e5539c4d816fc31c566`.
  The installed `notices/COMPONENTS.txt` is generated before adding its own entry and
  has SHA-256 `51a5727a149a3f990519b8b41e80e0610d54967c75f24c496e078b8265f334d1`.

### Engineering interface and synthetic sample

The frozen copy in `r3-completion-sample/` matches the [release interface](../release/RELEASE_INTERFACE.md)
at the qualification commit. Its engineering revision is `pws0807-r3-interface-1`.
All 74 relative file links resolved in the repository. The linked
[release evidence](../release/RELEASE_EVIDENCE.md) and sample proof identify the same
candidate, source, installer, and release materials.

The synthetic ZIP has 30 unique safe members. CRC checks passed, and all 29 manifest
rows matched the member sizes and SHA-256 values. The embedded manifest matched the
external copy byte for byte. Independent reopening covered 24 valid PDFs and three
nested ZIPs and refused the deliberately truncated PDF and ZIP. Twenty-eight members
match the R2 sample exactly. The README and manifest carry the R3 bindings.
The retained clean and replacement runs each bind seven input files to this ZIP and
match 11 workflow outputs to frozen references. Each separately generated support ZIP
matches its own receipt and the frozen allowlist.

## Discrepancies and limits

The admission checks found no artifact identity discrepancy. The frozen sample
manifest's expected-behavior note for `expected/compression/grayscale.pdf` describes
pages 1 and 2. The reopened PDF has three pages, and the retained compression receipt
shows re-encoding on page 3. Carry this missing detail into the final sample review
and preserve the current ZIP and manifest as evidence.

The R3 engineering sheet is frozen. The canonical release interface and evidence
index still carry draft release metadata. The formal M8 review must reconcile those fields,
the standalone checklist, the candidate matrix and remaining sign-offs. Current installed
R3 observations retain their Windows 11 Pro x64 `26300.9711` and Smart App Control
Off scope. Historical R1 and R2 failures and the R3 observation limits retain their
attributions. This admission used read-only host checks and retained installed receipts.
Publication remains subject to separate owner authorization.

This review used local PaperWorkShell artifacts and synthetic files. The engineering
interface documents standalone behavior and PaperWorkShell's ownership of local case
data. Future ThirdLife compatibility remains a separate B4 decision.

## Repository verification

- `py -3.14 tools/dev.py validate-docs`, `validate-task-graph`, `validate-portfolio-boundary`,
  `validate-release-inventory`, and `validate-schemas` passed.
- Focused `pytest` runs for `test_validate_docs.py`, `test_validate_tasks.py`,
  `test_validate_portfolio_boundary.py`, and `test_release_sbom.py` passed 209 tests
  and 149 subtests.

## Preserved checkpoint and continuation

The offline PWS-Test VM was left untouched. The retained final R1 receipt
`r3-completion-evidence/final-active-r1.json` matched SHA-256
`2c322594c5f9138d9bd495e9a945198e94f89a621312fd886b86a8a51182cfef` and
identifies the active R1 checkpoint `999a5747-66e2-4565-baee-5e619dce8656`.

**Next step — PWS-0808.** Reconcile the retained R3 clean install, same-build repair,
and R1-to-R3 replacement receipts against the corresponding M8 lifecycle criteria.
Record the evidence and any gaps in this report. The offline R1 checkpoint can stay
untouched during that review.
