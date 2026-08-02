# Initial security and privacy test plan

- Status: Foundation plan; implementation is owned by mapped tasks
- Task: PWS-0004
- Threat definitions: [foundation threat model](threat-model.md#threat-catalogue)
- Traceability: [threat-to-task mapping](threat-traceability.md)

## Test rules

1. Fixtures are deterministic and synthetic. They contain no real applicant, identity, address,
   financial, medical, employment, or signature data.
2. Unique synthetic canaries have context-specific oracles and are never printed in failure
   output. Log/diagnostic canaries never propagate there; hidden/metadata canaries are absent only
   under the named sanitization/export profile; intended document text may remain in ordinary OCR
   and exports; redacted-region canaries must be absent only from gated M6 derivatives.
3. Tests operate offline. Network tests attempt egress only to a controlled local/no-route target
   and prove denial; they never transmit fixture content to an external service.
4. Every failure oracle uses stable codes and sanitized parameters. Raw third-party exceptions,
   source values, filenames, and full paths are not acceptable evidence.
5. Security tests verify original hashes and last-valid manifest/export state before and after
   mutation, crash, cancellation, timeout, and disk-full cases.
6. Limits are tested immediately below, at, and above their boundary. Large hostile artifacts are
   generated on demand and cleaned after the test.
7. A skip is allowed only for an explicitly optional capability and must state the missing
   capability. Release/gate evidence cannot treat a required skipped control as passed.
8. UI warnings supplement controls; they do not replace isolation, validation, verification, or
   export blocking.
9. Numeric resource limits are accepted only with measured boundary evidence on the reference
   profile. An unevidenced limit or incomplete isolation/egress control leaves the owner/gate open.

## Planned suites

| Test ID | Threats | Earliest owning tasks | Layer and synthetic scenario | Required oracle |
|---|---|---|---|---|
| ST-001 | TM-001, TM-003, TM-022, TM-023 | PWS-0103, PWS-0105, PWS-0106, PWS-0107 | Security/integration: malformed/truncated/deep PDF/image inputs; corrupted/truncated/oversized/wrong-case/raced canonical preview buffers; hostile worker/tool results | GUI never invokes a general-purpose decoder on worker bytes; worker terminates/restarts where required; stable error; case/original unchanged |
| ST-002 | TM-002 | PWS-0103, PWS-0105, PWS-0106, PWS-0107, PWS-0201, PWS-0208 | Security/performance: byte/page/pixel/decompression/recursion/memory/CPU/temp limits at boundaries | Work stops within budget; partial state is non-authoritative; retry remains possible |
| ST-003 | TM-004 | PWS-0107, PWS-0204, PWS-0803 | Security: real worker and descendant attempt IPv4/IPv6 TCP and UDP against controlled loopback listeners; reachable non-isolated control; OCR socket/DNS/HTTP attempts; updater privacy capture | AppContainer token has zero capability SIDs; no listener receives isolated traffic while control succeeds; descendants retain denial; updater request contains no case/document fields |
| ST-004 | TM-005, TM-023 | PWS-0103, PWS-0104, PWS-0112 | Security/integration: one-handle source replacement/truncation; post-import case-copy mutation before parse/export; destination swap between verification/temp copy/digest/commit | Race/mutation detected before an authoritative result; exact verified bytes or no export commit; external source/prior export hashes and last valid manifest unchanged |
| ST-005 | TM-006, TM-019, TM-023 | PWS-0101, PWS-0102, PWS-0104, PWS-0107, PWS-0407, PWS-0706 | Security: traversal, absolute/device/UNC/ADS/reserved names, symlink/reparse/hardlink/file-ID alias, case-insensitive/Unicode collision, cache/temp link swap, ZIP/deletion escape; restricted worker tries case roots, originals, manifest/journal/history, sibling case, external and inherited user-temp paths; deliberate exact-handle control when available | Reject before mutation and again at opened handle; no path-based worker case access; only an authenticated least-privilege handle works; broker alone publishes state; no file outside exact scope changes |
| ST-006 | TM-007 | PWS-0006, PWS-0109, PWS-0804 | Unit/integration: old/current/future/invalid schemas, partial atomic write, journal corruption, interrupted migration | Future/invalid fails before mutation; last valid state reopens; source hashes/intent preserved |
| ST-007 | TM-008 | PWS-0008, PWS-0107 | Unit/property/integration: wrong fixed bootstrap size/magic/version/identity/capability; zero/oversized/truncated/fragmented/coalesced frames; wrong session/worker/capability/case/operation/generation/direction/sequence/request; malformed, replayed, unknown, or out-of-order envelope | Bootstrap/frame/decoder/state rejects before work; grant from another launch fails; first protocol fault poisons generation and terminates when required; stale restart events do not mutate state; no raw values leak |
| ST-008 | TM-009 | PWS-0008, PWS-0103, PWS-0105, PWS-0107, PWS-0204 | Security: metacharacter/quote/newline input; decoy executable/DLL/plugin/config/font/language resource in user PATH/PATHEXT/TEMP/cwd; proxy/cloud/command/Python environment canaries; inherited-handle inventory; child-spawn and excessive output | Fixed executable/resource identity, fixed argv/cwd, no shell; only constructed allowlist with per-launch profile temp values; exactly RPC/bootstrap handles; descendants inherit egress/limit/kill policy; stable sanitized result |
| ST-009 | TM-010 | PWS-0007, PWS-0401 | Security/unit: script/import/expression/path-bearing executable field, duplicate keys, tamper, expiry, future version; schema-permitted inert provenance URL under network/file spy | No execution or dereference/file/network access; inert provenance stays data; trust state is visible; case unchanged; invalid template code and plain remediation |
| ST-010 | TM-011, TM-018, TM-026 | PWS-0113, PWS-0204, PWS-0305, PWS-0805 | Security: distinct context-specific canaries plus diagnostic entry mutation/addition/removal after preview and before write | No prohibited canary; produced entries/digests exactly match frozen approved snapshot; mutation fails closed; cancel writes nothing |
| ST-011 | TM-012, TM-013, TM-023 | PWS-0101, PWS-0108, PWS-0113, PWS-0705 | Integration/security: cancel/crash/power-loss, stale/raced/aliased cache, unrelated case, lock/expiry/restart | Partial data stays operation/case scoped and is ignored/cleaned/recovered; wrong-case/raced cache is rejected; unrelated case is not visible |
| ST-012 | TM-014, TM-015 | PWS-0112, PWS-0406, PWS-0407 | Integration/security: truncated/wrong-page/encrypted/unrenderable output, collision/alias/UNC policy, ZIP names, destination-local temp digest mismatch, disk-full/permission/partial multi-file | Required verification failure blocks artifact/package success; each commit is atomic; partial set is inventoried for cleanup/retry; previous good output remains |
| ST-013 | TM-016 | PWS-0105, PWS-0206, PWS-0208, PWS-0305 | Integration/accessibility: signed-marker source through modify/cancel/export and signature-image wording | Warning/confirmation precedes change; original preserved; output says unsigned derivative; visual mark makes no crypto claim |
| ST-014 | TM-017 | PWS-0405, PWS-0601, PWS-0603, PWS-0606, PWS-0607, PWS-0608 | Security/golden/manual: visual-cover negative case and adversarial text/image/object/revision/metadata/OCR/viewer/resave canaries | Ordinary shape never passes; any recovered/unknown canary blocks export; independent gate remains mandatory |
| ST-015 | TM-020, TM-021 | PWS-0005, PWS-0801, PWS-0802, PWS-0803, PWS-0808 | Supply-chain/release: inventory/hash/signature/SBOM mismatch, tampered/expired/wrong-channel/rollback/interrupted update | Reject before install/use; previous version recoverable; packaged inventory matches approved inputs |
| ST-016 | TM-024 | PWS-0403, PWS-0404, PWS-0408 | Unit/accessibility/content: confident heuristic, unconfirmed assignment, warning/error/summary wording corpus | Automation cannot complete requirement; “Needs review” and concrete claims persist; prohibited claims fail |
| ST-017 | TM-025 | PWS-0105, PWS-0401, PWS-0405, PWS-0501 | Security/contract: XFA, JavaScript/actions, forms, attachments, encryption, archive/incremental-revision fixtures | Typed present/unknown state; unsupported policy blocks or verified profile handles it; no active content silently retained |
| ST-018 | TM-026 | PWS-0006, PWS-0104, PWS-0305, PWS-0408, PWS-0501 | Unit/integration: Unicode display names, metadata, signature assets, summaries, random internal filenames | Storage/cache/temp names reveal no display value; only intended user output contains approved content |
| ST-019 | TM-027 | PWS-0503, PWS-0504, PWS-0505, PWS-0507 | Security/golden/manual: incremental re-encoding/grayscale on small text, stamps, signatures, mixed scans, and an unattainable target | Policy stops at the measured floor; affected pages/policy are reviewable; unsafe/unattainable result is explicit; no silent extra degradation |
| ST-020 | TM-028 | PWS-0003, PWS-0009, PWS-0113, PWS-0805, PWS-0806 | Security/process: fixture provenance audit, known synthetic markers, prohibited sample/token in index/history/artifacts, sensitive bug-sample workflow review | Repository/CI rejects prohibited material without echoing it; every fixture has safe provenance; restricted samples never enter repository/routine CI |

Every TM-001 through TM-028 threat appears in at least one planned suite. The documentation
validator enforces that inventory while PWS-0004 is the current model.

## PWS-0102 implemented lifecycle evidence

The local lifecycle suites implement the PWS-0102 portion of ST-005 and TM-019. They prove an
atomic create/list/close/fresh-service reopen/delete round trip; incomplete publication is never a
valid case; malformed, legacy, future, duplicate-key, and mismatched manifests fail before
mutation; active leases and capacity reservations exclude deletion; and a complete no-follow
inventory precedes manifest-first removal. Dedicated lifecycle cases cover hard links, alternate
streams, sibling cases, external sentinels, malformed UUID-named candidates, and injected
mid-delete failures. A direct post-inventory leaf-identity swap proves that whole-case removal
stops after manifest invalidation without touching the replacement's outside target; the inventory
also reuses PWS-0101's live reparse and file-identity boundary. Together these prove scope and
honest partial reporting. Error/result checks prohibit paths, canaries, raw exceptions, and any
forensic-erasure claim. PWS-0706 retains the later human inventory/retention workflow, and PWS-0113
retains restart orphan cleanup and diagnostics.

## PWS-0107 required live evidence

PWS-0107 unit doubles prove deterministic protocol and lifecycle logic, but do not prove the
Windows security boundary. Its completion evidence must launch the real fixed Python host and
demonstrate all of the following on the supported Windows target:

1. The worker reports an AppContainer token with zero capability SIDs, Job membership, and the
   exact constructed environment before `session.ready`.
2. Process creation is suspended; Job assignment occurs before resume; the inherited handle list
   is exactly standard input/output plus the 92-byte bootstrap reader; no shell is used.
3. The environment omits user `PATH`, `PATHEXT`, `PYTHONPATH`, command processor, proxy, cloud
   credential, and user temp canaries. `LOCALAPPDATA`, `TEMP`, and `TMP` resolve only inside the
   per-launch AppContainer profile.
4. IPv4 and IPv6 TCP connect/listen and UDP send/receive probes run in both the worker and a
   descendant. A non-isolated control proves the local listener is reachable; the listener receives
   no isolated traffic.
5. Forced termination or retained Job handle close ends the worker and every descendant, with no
   breakaway or surviving pipe/profile authority.
6. The current no-op receives no document handle. Path opens for a case root, sibling case,
   originals, manifest, journal, export history, external path, user temp, and unrelated profile
   fail. Once an owning operation adds brokerage, a deliberately authenticated exact handle works
   and siblings/other access still fail.
7. Handshake, operation, cancellation-grace, and shutdown deadlines; result/deadline and
   result/cancel races; crash/partial frame; malformed message; clean close; forced stop; and fresh
   restart converge on exactly one stable terminal client state without raw values.
8. The exact job tree exits before runtime/code access grants are revoked and the per-launch
   profile is deleted; cleanup failure is reported rather than hidden.

A socket monkeypatch, proxy setting, firewall-rule inference, launcher Boolean, test-only
production RPC operation, path-only mock, or skipped required platform probe is not equivalent
evidence.

## Milestone evidence progression

### M0 foundation

- `validate-docs` checks local links/anchors and decision, task, and threat references offline.
- Schema/DAG, architecture, secret-scanning, and synthetic-fixture policies run in the aggregate
  quality suite.
- PWS-0010 reviews that each high-impact threat has a decision, owner, and planned oracle.

### M1 worker and immutable vertical slice

- Implement ST-001 through ST-008, the M1 portions of ST-010 through ST-012, and source hash/log
  audits.
- Force worker crash, timeout, cancellation, malformed message, disk-full, permission failure,
  destination failure, and recovery.
- For PWS-0107 specifically, retain the live AppContainer/egress/job/environment/handle/filesystem
  evidence above; the no-op lifecycle does not by itself prove case access confinement.
- PWS-0114 requires original hashes, verified export, worker recovery, egress evidence, and clean
  logs on the reference profile.

### M2–M5 document capabilities

- Extend resource, command, log, active-content, signature, OCR, template, archive, filename, and
  export matrices as each adapter is admitted.
- Test missing optional executables/language packs explicitly; required release capabilities may
  not pass on skips.
- Viewer/compatibility evidence uses only synthetic outputs and concrete checks.

### M6 permanent redaction

- Generate a separate adversarial corpus with unique canaries.
- Recoverability checks cover text/search/copy/extraction, embedded images, objects/content streams,
  incremental revisions, metadata, attachments, comments, form values, OCR, multiple viewers, and
  resave.
- Unknown or failed required verification blocks output. PWS-0608 requires independent review;
  visual inspection alone is never evidence.

### M7–M8 shared use and release

- Add keyboard/NVDA coverage to security warnings, consent, lock, retention, deletion,
  diagnostics inventory, update errors, and recovery.
- Run packaged-build dependency/SBOM/signature, install/update/migration/uninstall, low-disk/HDD,
  and residual-case matrices.
- Capture update traffic to prove no case/document fields leave the machine.

## Failure report requirements

A failing automated or manual scenario records only:

- test/operation/internal case/document/page IDs;
- engine, protocol, schema, template, and profile versions;
- generic input class, numeric size/count/resource observations, duration, and stable error code;
- which check failed and whether prior state/original hashes remained intact; and
- sanitized stack information with paths and values removed.

It does not attach real or synthetic document content to routine CI artifacts, print canary values,
or upload scan reports. A future user-authorized diagnostic export follows DF-DIAGNOSTICS and
PWS-0805 instead.
