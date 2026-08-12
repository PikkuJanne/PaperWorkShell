# Milestone 0 foundation gate — PWS-0010

- Review status: Complete; final task-state review
- Gate conclusion: PASS
- Review date: 2026-08-02
- Evidence revision: `ebe66c856aae1932d89d233606012e0d35c122af`
- Review-state closure revision: `f9e910db8c7e2a3b9d9c9705de735e0373d25b78`
- Review branch: `codex/pws-0010-foundation-gate`
- Frozen decisions: PWS-D003, PWS-D034, PWS-D035, PWS-D036

## Scope and decision rule

This gate reviews the M0 foundation as evidence, not as a feature-development shortcut. It passes
only when PWS-0001 through PWS-0009 are done, every M0 exit and PWS-0010 acceptance criterion has
current evidence, the final locked checkout passes the full local suite, and no unresolved M0
release-blocking defect remains. No criterion is waived.

A pass approves the repository to start PWS-0101. It does not approve document parsing, worker
isolation, a renderer, OCR, a UI, export, permanent redaction, an installer, a resolved release
inventory, or an SBOM. Those controls and claims remain with their named future tasks and gates.

Historical note (2026-08-12): PWS-D041 and
[ADR 0007](../adr/0007-low-resource-budgets.md) retired the physical reference-profile contract and
its commands. References below describe evidence accepted at the 2026-08-02 M0 review; they do not
create a current hardware gate or certify a two-core/4-GiB/integrated-GPU/HDD machine.

Authoritative sources for this review are [ROADMAP.md](../../ROADMAP.md),
[TASKS.yaml](../../TASKS.yaml), [DECISIONS.md](../../DECISIONS.md), the
[quality workflow](../development/quality-gates.md), the
[security model](../security/README.md), the
[dependency policy](../development/dependency-policy.md), the
[schema inventory](../../schemas/README.md), and the
[synthetic-fixture policy](../development/synthetic-fixture-policy.md).

## Reviewed environment

| Item | Review value | Interpretation |
|---|---|---|
| Operating system | Windows 11 build 26200, x86-64 | Local review workstation; no physical-hardware certification claim |
| Python | CPython 3.14.6, 64-bit | Supported pinned minor |
| uv | 0.11.32 | Exact project-manager pin |
| Git | 2.55.0.windows.3 | Meets the 2.45 minimum |
| Gitleaks | 8.30.1 | Exact scanner pin |
| GitHub Actions | `windows-2025` | Exact-revision push and pull-request runs passed |
| Product engines | None | No PDF, image, renderer, OCR, UI, or installer runtime is admitted in M0 |

The local machine exposed eight logical processors and about 16.5 GB RAM. The then-current
reference profile was record-only and contained no product target; it has since been superseded by
the constrained software-resource policy.

## Prerequisite task review

Every prerequisite status and evidence record was inspected in [TASKS.yaml](../../TASKS.yaml).
Every listed revision exists in the current ancestry.

| Task | Status | Evidence revision | Foundation evidence reviewed |
|---|---|---|---|
| PWS-0001 | Done | `f6bf518` | CPython/uv lock workflow, package import, bootstrap, task validation |
| PWS-0002 | Done | `3dd5f66` | Twelve package roots, dependency policy, import-boundary success and deliberate failure |
| PWS-0003 | Done | `a77e4f8` | Registry-derived local/CI suite, secret scanning, negative self-tests, reference profile |
| PWS-0004 | Done | `b4872e4` | Twenty-eight-threat catalogue, data flows, owner traceability, twenty planned security suites |
| PWS-0005 | Done | `cb29771` | Dependency/license policy baseline; later clean review evidence is recorded at `454bc9a` |
| PWS-0006 | Done | `e4aedbc` | Case-manifest v1 schema, examples, semantic checks, invalid/future-version cases |
| PWS-0007 | Done | `87baff8` | Data-only template v1 schema, inertness, lifecycle, and negative fixtures |
| PWS-0008 | Done | `8438448` | Worker-RPC v1 schema/models/state, bounded hostile decoding, sanitized errors |
| PWS-0009 | Done | `4903749` | Closed generator, exact fixture/index provenance, privacy scanning, on-demand policy |

The PWS-0005 evidence revision identifies the implementation baseline. Its task record separately
names `454bc9a`, the following review-state commit on which the clean checks ran; that intervening
commit changes only task and bundle metadata. This is an explicit evidence lineage, not a claim
that `cb29771` and `454bc9a` are the same revision.

## Gate criteria

| Criterion | Result | Evidence |
|---|---|---|
| Every M0 prerequisite is done | Pass | PWS-0001 through PWS-0009 are `done`; task graph validation accepts every dependency and gate edge |
| Locked toolchain and clean bootstrap | Pass | Fresh SSH clone at the evidence revision installed all twenty-two locked packages with `uv sync --locked` |
| Complete local quality suite | Pass | The fresh clone passed the registry-derived `check`; exact-revision push and pull-request Actions passed the same suite |
| Architecture boundaries | Pass | Twelve package roots import without optional engines; boundary tests include deliberate prohibited imports |
| Threats map to planned mitigations | Pass as planning evidence | TM-001 through TM-028 map to frozen decisions, non-gate implementation owners, and ST-001 through ST-020 oracles; future controls are not claimed implemented |
| Dependency families have license treatment | Pass for policy baseline | Forty-one components and twenty-nine relationships have closed license, source, boundary, packaging, and owner treatment; release resolution remains PWS-0801 |
| Schemas pass positive and negative validation | Pass | Exact positive/negative inventories below; schema validator and focused suites pass at the evidence revision |
| Task graph passes positive and negative validation | Pass | Eighty tasks, 238 edges, nine milestones, forty decisions; deliberate invalid date and cycle are rejected |
| Public corpus is safe for the repository | Pass as synthetic construction | Seventeen exact deterministic files; eighteen committed, two on-demand, and four scheduled coverage classes; no external document is anonymized into the corpus |
| Open risks have owners and gates | Pass | The risk register below assigns every remaining implementation area to future tasks and a blocking gate |
| No unresolved M0 release-blocking defect | Pass | Clean clone, local and hosted suites, junction/ABA review, and three independent exact-commit audits found no blocker |

## Contract validation inventory

The registered schema command authenticates each canonical schema before evaluating its examples.
The focused validators require every checked-in positive example to pass and every intended-negative
example to fail for its reviewed stable code.

| Contract | Positive evidence | Intended-negative evidence |
|---|---|---|
| Task graph | Canonical eighty-task graph | Invalid schema date and dependency cycle in the five-case quality self-test; broader unit mutation coverage |
| Retired reference profile | Historical canonical Windows profile and deterministic workload | M0 schema evidence only; superseded by PWS-D041 |
| Case manifest v1 | Two canonical synthetic manifests | Eleven fixtures plus generated invalid reference, geometry, replay, version, and path cases |
| Declarative template v1 | One canonical synthetic template | Seven fixtures plus injection, lifecycle, reference, version, and inert side-effect cases |
| Worker RPC v1 | Fifteen canonical messages and full conversations | Twenty-seven fixtures plus malformed, oversized, state, correlation, path, version, and leakage cases |
| Dependency inventory v1 | One complete policy baseline | Four rejected examples, including a distribution-decision bypass |
| Synthetic fixture index v1 | One canonical index for seventeen fixtures and twenty-four coverage entries | Five rejected index examples for version, duplicate, provenance, and path failures |

This is contract and failure-path evidence. The RPC schema does not prove transport ACLs, worker
process containment, filesystem confinement, or egress denial; PWS-0107 owns those controls.

## Automated evidence

| Command | Current result |
|---|---|
| `py -3.14 tools/dev.py validate-schemas` | Pass: all seven registered contracts |
| `py -3.14 tools/dev.py validate-dependencies` | Pass: one complete and four rejected examples |
| `py -3.14 tools/dev.py validate-fixtures` | Pass: seventeen deterministic fixtures, 50,489 bytes, 404 historical blobs, 181 index blobs, and 188 working files |
| `py -3.14 tools/dev.py validate-task-graph` | Pass: eighty tasks, 238 dependencies, nine milestones, forty decisions |
| `py -3.14 tools/dev.py validate-docs` | Pass: local links, references, twenty-eight threats, owner mappings, and planned tests |
| Retired `validate-reference-profile` command | Historical pass at the M0 evidence revision; command removed by PWS-0115 |
| `py -3.14 tools/dev.py quality-self-test` | Pass: all five deliberate format, type, schema, cycle, and staged-binary secret violations rejected and removed |
| Retired `benchmark-reference` command | Historical deterministic local-smoke record; command removed by PWS-0115 |
| `uv sync --locked` in a fresh SSH clone | Pass: twenty-two locked packages installed at the exact evidence revision |
| `py -3.14 tools/dev.py check` in that clone | Pass: 56-file format/lint, twelve-layer boundaries, strict typing, 256 unit, three integration, 103 security with two disclosed local privilege skips, two accessibility tests, all validators/scans, and five negative self-tests |
| GitHub Actions local-equivalent suite | Pass: evidence-baseline [push run 30728665451](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/30728665451) and [pull-request run 30728666356](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/30728666356), plus review-state [push run 30728984177](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/30728984177) and [pull-request run 30728985626](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/30728985626); hosted Windows passed all 105 security tests without skips |

The gate evidence used a quiescent fresh SSH clone. Repository scanning deliberately fails closed
if Git refs, the index, tracked paths, or selected bytes change during its snapshot; such a race
was not accepted as a corpus or secret-scan pass.

## Manual gate review

| Review | Result |
|---|---|
| Prerequisite status, evidence revisions, and ancestry | Pass for all nine prerequisites |
| Threat owners and oracles | Pass after aligning TM-002, TM-015, TM-016, and TM-023 with owners already assigned by TASKS/test-plan evidence |
| Dependency/license treatment | Pass for the M0 policy baseline: 29 approved, six planned, three candidates, and three decision-gated component records |
| Corpus provenance and category disposition | Pass: closed no-input construction; seventeen files; eighteen committed, two on-demand, four scheduled categories |
| Bundle file inventory | Pass: all seven v0.1.28 records matched byte counts and SHA-256 at the evidence revision |
| Windows linked-directory capability | Pass by explicit junction/ABA review; two Python symlink tests remain disclosed local WinError 1314 skips, while hosted Windows ran all 105 security tests |
| Accessibility scope | Pass for M0: two sentinel tests and no UI surface; real widget coverage is mandatory when PySide6 appears |
| Manual product demonstration | Not applicable in M0: no document engine or UI exists; the required manual activity is this foundation evidence review |

### Windows link and interruption evidence

Two security tests that call `Path.symlink_to(..., target_is_directory=True)` skip on this
workstation because WinError 1314 reports that symbolic-link privilege is unavailable:

- `test_regeneration_rejects_a_real_linked_directory_without_touching_target`
- `test_long_path_recipe_rejects_a_real_linked_child_without_touching_repository`

The review used real NTFS junctions, which do not require that privilege, to exercise both Windows
reparse-point paths. Committed-corpus generation rejected its junction with
`FIXTURE_OUTPUT_UNREADABLE` and left the outside sentinel byte-identical. Long-path generation
rejected its nested junction with `FIXTURE_OUTPUT_UNSAFE` and left the protected target empty. A
separate real-file identity-ABA scenario for both on-demand recipes showed that cleanup preserves
a replacement file at the same name.

The committed `test_on_demand_recipes_remove_partial_files_when_cancelled` test writes partial
bytes, raises `KeyboardInterrupt`, and proves identity-matched cleanup for both on-demand recipes.
That is an in-process interruption, not a forced OS process kill. A hard termination cannot run
`finally`; it can leave a scoped external partial whose exclusive name causes retry to fail with
`FIXTURE_OUTPUT_EXISTS` rather than overwrite it.

## Open-risk register

These are owned, future implementation risks and explicit limitations, not waived M0 defects.
Failure to prove the named control keeps its owning task or gate open.

| Risk | Threats | Owner tasks and gate | M0 disposition |
|---|---|---|---|
| Parser, worker, RPC, egress, resource, and hostile-result containment | TM-001–TM-004, TM-008, TM-009, TM-022, TM-023 | PWS-0103, PWS-0105, PWS-0106, PWS-0107; PWS-0114 | Planned; no document parser is enabled; incomplete isolation or egress evidence blocks M1 |
| Case paths, immutable ingest, atomic persistence/export, cleanup, and sanitized logs | TM-005–TM-007, TM-011, TM-012, TM-014, TM-015, TM-018, TM-019, TM-026 | PWS-0101, PWS-0102, PWS-0104, PWS-0108, PWS-0109, PWS-0112, PWS-0113; PWS-0114 | Planned; baseline failure blocks M1 |
| Renderer and document dependency admission | TM-001, TM-002, TM-020 | PWS-0105, PWS-0106; PWS-0114 | No runtime engine admitted; exact artifact, worker boundary, license, fidelity, performance, and packaging evidence remain required |
| OCR, external tools, signed sources, and signature-image terminology | TM-002, TM-004, TM-009, TM-016 | PWS-0201, PWS-0204, PWS-0206, PWS-0208, PWS-0305; PWS-0209 and PWS-0308 | Features absent; missing evidence blocks their gates |
| Template trust, active content, preflight, review ownership, and claim limits | TM-010, TM-024, TM-025 | PWS-0401 through PWS-0408; PWS-0409 | Planned; blocks v0.1 if incomplete |
| Compression, compatibility, and measured legibility | TM-025, TM-027 | PWS-0501, PWS-0503 through PWS-0506; PWS-0507 | Feature absent; no performance or compatibility claim exists |
| Permanent redaction | TM-017 | PWS-0405, PWS-0601 through PWS-0607; PWS-0608 | Explicitly disabled; any earlier removal claim or enablement is blocking |
| Shared-computer consent, lock, retention, and deletion | TM-013, TM-019 | PWS-0705, PWS-0706; PWS-0707 | Accepted pre-M7 limitation only while no safe assisted-mode claim is made |
| Release inventory/SBOM, installer, updates, migrations, and diagnostic bundles | TM-007, TM-018, TM-020, TM-021 | PWS-0801 through PWS-0805; PWS-0808 | Development-stage exposure; hard v1 gate work |
| Scheduled corpus capabilities | TM-001, TM-002, TM-025, TM-028 | PWS-0105, PWS-0201, PWS-0303, PWS-0704 | Protected, CMYK, rendered multilingual/complex-script, and RTL behavior stay scheduled until their required engines/UI exist |
| Real UI accessibility and measured product performance | TM-002, TM-024, TM-027 | PWS-0111, PWS-0208, PWS-0701 through PWS-0703; milestone gates | M0 has sentinels and record-only measurements, not conformance or product thresholds |

Accepted residual limitations remain those already stated in the threat model: unrestricted
same-account or administrator compromise is not an application boundary; OS-managed artifacts are
not subject to a forensic-erasure claim; a future compromised parser may see only its granted
active-case scope; hashes do not authenticate against malware able to rewrite data and manifests;
schema validity does not prove template authenticity or suitability; signed-marker detection is
not cryptographic validation; and unsupported network locations fail closed until their owning
tasks prove safe semantics.

## Defect review

| Finding | Severity | Disposition |
|---|---|---|
| TM-015 omitted PWS-0112, TM-016 omitted PWS-0305/PWS-0308, TM-023 omitted PWS-0108, and TM-002 omitted PWS-0105 from the complete ownership map | P2 documentation precision | Resolved in PWS-0010 by aligning traceability and the planned test-owner rows |
| PWS-0009 evidence called its interruption coverage an “abnormal-exit” probe | P2 evidence precision | Resolved in PWS-0010: committed coverage is KeyboardInterrupt cleanup; hard termination leaves a non-overwritable scoped partial |
| Two directory-symlink tests require an unavailable Windows privilege | Capability limitation | Not a product-control skip; final gate records real junction and ABA review and retains the two explicit automated skips |
| Other unresolved M0 defect | — | None found in the clean clone, hosted runs, or three independent exact-commit audits |

## Gate conclusion

**PASS.** All nine prerequisite tasks are done. The exact evidence revision passed a locked fresh
bootstrap, the full local suite, both hosted Windows suites, focused positive and negative
contract checks, bundle verification, platform link/identity review, and three independent audits.
No unresolved M0 release-blocking defect remains.

This conclusion approves only the documented foundation and its owned future-risk plan. It does
not claim that any future runtime control or product feature exists. PWS-0010 is `done`, and
PWS-0101 is the next ready task. A later regression in a required check reopens the owning task or
gate; it is not converted into a warning.
