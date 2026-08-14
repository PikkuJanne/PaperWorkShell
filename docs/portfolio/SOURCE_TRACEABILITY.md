# Source traceability — PaperWorkShell ThirdLife steering bundle

| Field | Value |
|---|---|
| Supplied source title | *ThirdLife Software Portfolio — Two-Team Development Roadmap and Boundary Architecture* |
| Supplied source version/date | 2.0 / 2026-08-14 |
| Steering bundle | `PaperWorkShell-ThirdLife-steering-bundle` version 1.0.0 |
| Bundle-manifest SHA-256 | `d6bf62f3a382166546b446d1aa9a8d4ab81bfc24f1c4f42fd170d3fbe65f563a` |
| Purpose | Preserve supplied portfolio provenance and map it to adapted local documents |

## Authority and verification note

The named portfolio roadmap was not supplied as a repository file and is not stored here. The
section/page references below came from the maintainer-supplied steering bundle and were not
independently checked against that roadmap during ingestion. They are provenance, not permission
to override PaperWorkShell's local authority order.

All eight files listed by the supplied bundle manifest matched its byte size and SHA-256 on
2026-08-14. The download client renamed its `README.md` to `README(3).md`; its bytes still matched
the manifest. The steering manifest was used as a source-integrity record and did not replace the
repository's unrelated root `BUNDLE_MANIFEST.json` roadmap-bundle manifest.

| Supplied manifest path | Bytes | Verified SHA-256 |
|---|---:|---|
| `CODEX_THIRDLIFE_ALIGNMENT_PROMPT.md` | 27,241 | `bcb5ade8564c39bff1db3712c47599c06a0316533f89b1b686ef35fb2143b62d` |
| `README.md` (received as `README(3).md`) | 5,144 | `98a3a8a2df54f7c6161835c6050c711585ca413931880e80c746b3ca36b24ec3` |
| `RELEASE_EVIDENCE.md` | 10,707 | `baec9ec43f91ccfd89d070ee22eb7973776a1523f45d595d76c62a667060e0a3` |
| `RELEASE_INTERFACE.md` | 13,901 | `279062ca8baed9c28151cc7ccc98194cfcccd8fc88fdbce9a8cbe38c8e55678b` |
| `SOURCE_TRACEABILITY.md` | 7,597 | `bb227c275cde82c21516d7ae2b7513fb36458c01903e8d4a4f457395cc161d87` |
| `STANDALONE_RELEASE_CHECKLIST.md` | 9,417 | `7e4506b178727c50998aaecb858a0ba4879c3c1ae8a464d3512a98fe6e5dd0de` |
| `THIRDLIFE_ALIGNMENT.md` | 15,172 | `d91d23ecda388790dfb6833a4896c7bcf634db5726d0ff16369cc88bf299c996` |
| `THIRDLIFE_FUTURE_NOTES.md` | 9,460 | `bcb254ce4cc7bdc1450de61629e114e4e4b3f135e5bd2de8a59c21673eae422c` |

## Supplied portfolio requirement mapping

The “source anchor” column is retained exactly in substance as supplier-provided traceability.

| Steering requirement | Supplied source anchor | Supplied interpretation |
|---|---|---|
| Independent product queues and late binding | Document control, pp. 2–4 | Standalone products are assembled from frozen releases, not live branches/databases |
| PaperWorkShell Team A/A1 role | Sections 1, 3, 6; pp. 4, 6, 11 | Establish safe file workflow, recovery, accessibility, and low-resource discipline |
| PaperWorkShell boundary | Section 9.1, p. 18 | Defines goal, owns/does-not-own, data, standalone release, and later suite treatment |
| Composite journeys are not dependencies | Section 3.3, p. 7 | Current products exchange ordinary files manually; later automation is separate |
| Standalone first | Section 4.1, p. 7 | Each product owns repository, architecture, installer, data, updater, threat model, and release |
| Minimal portfolio envelope | Section 4.2, pp. 7–8 | Identity, install/remove, data, inputs/exports, launch, support, and samples |
| Early prohibitions | Section 4.3, p. 8 | No shared database/SDK/platform/service/dependencies/gates/live integration/speculative features |
| ThirdLife-owned assembly | Section 4.4, p. 8 | ThirdLife owns catalogue/profile/adapter/association/compatibility work against frozen releases |
| Contact model | Section 4.5, pp. 8–9 | Preview/stable exchange uses public artifacts/interface sheets rather than joint development |
| Data ownership/deletion | Section 4.6, p. 9 | ThirdLife holds portfolio metadata, not product content; uninstall/support remain separate |
| Offline/resource/accessibility/security baselines | Section 5, pp. 9–11 | Core workflows remain local, bounded, accessible, unprivileged, private, and evidence-honest |
| PaperWorkShell Round 1 | Section 8.1, pp. 14–15 | Standalone package workflow, source preservation, recovery, ordinary outputs, and interface |
| Future suite actions | Section 10, p. 21 | Install/remove, launch, optional association/opening, and guidance; no private-data control |
| Default handoff behavior | Section 10.1, p. 22 | Prefer visible OS opening, user-selected export, manual fallback, and ordinary formats |
| Adapter approval | Section 10.2, p. 22 | Requires user value, safety, independence, maintenance, and data-ownership evidence |
| Independent versions | Section 11, pp. 22–23 | Products version independently; compatibility binds exact versions/ranges |
| Release evidence | Section 12.2, p. 24 | Frozen artifact/hash/SBOM/licence/interface/sample/support/resource/migration evidence |
| Black-box suite testing | Section 12.3, p. 24 | B4 tests frozen artifacts on clean machines using documented behavior only |
| No prerelease cross-team test dependency | Section 12.4, p. 25 | First cross-product test is B4 against released artifacts |
| Project bundle/interface | Section 13.2, pp. 25–26 | Product repositories remain self-sufficient and publish release interfaces |
| Codex project-vacuum rules | Section 13.3, p. 26 | Stay in active repo, avoid shared infrastructure, record non-gating future ideas |
| Cross-project dependencies | Section 13.4, p. 26 | Product task graph remains local; future B4 may depend on a frozen artifact |
| Immediate PaperWorkShell plan | Section 16.1, pp. 27–28 | Preserve local roadmap, add boundary/data/release evidence without speculative integration |
| Frozen portfolio decisions | Section 17, p. 29 | P-005–P-012 establish independence, minimal interface, late assembly, ordinary files first |
| Minimal interface fields | Appendix A, pp. 29–30 | Identity, artifact, install/remove, launch, data, I/O, resource, security, support, samples, limits |
| Adapter brief location | Appendix B, pp. 30–31 | Adapter brief belongs in future ThirdLife B4 work |
| Standalone checklist | Appendix C, p. 31 | Standalone, recovery, resources, accessibility, security, SBOM, lifecycle, and evidence |
| Final assembly rule | Appendix D/final rule, pp. 31–32 | The suite adapts to valuable standalone products rather than creating a monolith |

## Adapted repository destinations

| Supplied source | Local treatment |
|---|---|
| `THIRDLIFE_ALIGNMENT.md` | Adapted into the root [alignment contract](../../THIRDLIFE_ALIGNMENT.md), subordinate to local authority |
| `RELEASE_INTERFACE.md` | Adapted into the M8 [draft release interface](../release/RELEASE_INTERFACE.md) |
| `RELEASE_EVIDENCE.md` | Adapted into the M8 [draft evidence index](../release/RELEASE_EVIDENCE.md) |
| `STANDALONE_RELEASE_CHECKLIST.md` | Adapted and scoped to [packaged M8 candidates](../release/STANDALONE_RELEASE_CHECKLIST.md) |
| `THIRDLIFE_FUTURE_NOTES.md` | Adapted as these non-gating [future notes](THIRDLIFE_FUTURE_NOTES.md) |
| `SOURCE_TRACEABILITY.md` | Adapted into this provenance record with external-source disclaimer |
| Steering `README.md` | Merged selectively into the repository root README; not copied over it |
| Alignment prompt | Used as migration source; reusable operating rules belong in local canonical prompts/contracts |
| Steering bundle manifest | Recorded here as source integrity; not substituted for the roadmap-bundle manifest |

## Interpretation rule

Portfolio-local details such as application ID, publisher, product licence, installer technology,
exact release paths, CLI/association support, project-transfer format, support contact, and hardware
claims remain unresolved until PaperWorkShell's owning tasks decide and verify them. Source
traceability does not fill a release `TBD`.

