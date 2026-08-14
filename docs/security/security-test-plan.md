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
9. Numeric resource limits are accepted only with measured boundary evidence under the applicable
   constrained resource policy. An unevidenced limit or incomplete isolation/egress control leaves
   the owner/gate open.

## Planned suites

| Test ID | Threats | Earliest owning tasks | Layer and synthetic scenario | Required oracle |
|---|---|---|---|---|
| ST-001 | TM-001, TM-003, TM-022, TM-023 | PWS-0103, PWS-0105, PWS-0106, PWS-0107, PWS-0108, PWS-0201, PWS-0202, PWS-0204, PWS-0206 | Security/integration: malformed/truncated/deep PDF/image inputs; corrupted/truncated/oversized/wrong-case/misbound result, preview, OCR candidate, searchable-PDF, or page-status frames; hostile worker/tool results | GUI never invokes a general-purpose document decoder on worker bytes; worker terminates/restarts where required; exact correlations and closed result validate or stable error; case/original unchanged |
| ST-002 | TM-002 | PWS-0103, PWS-0105, PWS-0106, PWS-0107, PWS-0108, PWS-0201, PWS-0202, PWS-0204, PWS-0206, PWS-0208 | Security/performance: byte/page/object/graph/pixel/decompression/recursion/memory/CPU/temp/result/status/tool-output limits at boundaries | Work stops within budget; partial state is non-authoritative; retry remains possible |
| ST-003 | TM-004 | PWS-0107, PWS-0204, PWS-0803 | Security: real worker and descendant attempt IPv4/IPv6 TCP and UDP against controlled loopback listeners; reachable non-isolated control; OCR socket/DNS/HTTP attempts; updater privacy capture | AppContainer token has zero capability SIDs; no listener receives isolated traffic while control succeeds; descendants retain denial; updater request contains no case/document fields |
| ST-004 | TM-005, TM-023 | PWS-0103, PWS-0104, PWS-0108, PWS-0112, PWS-0201, PWS-0202, PWS-0204, PWS-0206 | Security/integration: one-handle source replacement/truncation; pending-copy corruption; post-import case-copy mutation before cache hit/parse/transform/OCR/export; volatile/cache/output/status swap between validation/digest/commit | Race/mutation detected before an authoritative result; successful source/staged/stored digests agree or no source record; exact verified volatile/cache/export bytes or no publication; external source/prior export hashes and last valid manifest unchanged |
| ST-005 | TM-006, TM-019, TM-023 | PWS-0101, PWS-0102, PWS-0104, PWS-0105, PWS-0107, PWS-0108, PWS-0201, PWS-0202, PWS-0204, PWS-0206, PWS-0407, PWS-0706 | Security: traversal, absolute/device/UNC/ADS/reserved names, symlink/reparse/hardlink/file-ID alias, case-insensitive/Unicode collision, cache/temp/runtime link swap, ZIP/deletion escape; restricted worker tries case roots, originals, manifest/journal/history, sibling case, external and inherited user-temp paths; deliberate exact prepared-source/result/source/bounded-output-section handles | Reject before mutation and again at opened handle; no path-based worker case access; only authenticated purpose-limited handles work; source/cache unauthorized rights fail; OCR/runtime closure admits no unlisted object; document workers have no persistent cache/candidate writer; broker alone publishes state; no file outside exact scope changes |
| ST-006 | TM-007 | PWS-0006, PWS-0104, PWS-0109, PWS-0804 | Unit/integration: old/current/future/invalid schemas, interrupted import-only append, partial atomic write, journal corruption, interrupted migration | Future/invalid fails before mutation; a precommit import exposes no source record; last valid state reopens; source hashes/intent preserved |
| ST-007 | TM-008 | PWS-0008, PWS-0107, PWS-0204, PWS-0206 | Unit/property/integration: wrong fixed bootstrap size/magic/version/identity/capability; zero/oversized/truncated/fragmented/coalesced frames; wrong session/worker/capability/case/operation/generation/direction/sequence/request; malformed, replayed, unknown, or out-of-order envelope; invalid OCR/searchable handle/profile/report/output/status binding | Bootstrap/frame/decoder/state rejects before work; grant from another launch fails; first protocol fault poisons generation and terminates when required; stale restart events do not mutate state; no raw values leak |
| ST-008 | TM-009 | PWS-0008, PWS-0103, PWS-0105, PWS-0107, PWS-0108, PWS-0201, PWS-0204 | Security: metacharacter/quote/newline input; qpdf argv separator; decoy executable/DLL/plugin/config/font/language resource in user PATH/PATHEXT/TEMP/cwd; proxy/cloud/command/Python environment canaries; inherited-handle inventory; child-spawn and excessive output | Fixed executable/resource identity, fixed argv/cwd, no shell or PATH discovery; only constructed allowlist with per-launch profile temp values; exactly RPC/bootstrap launch handles plus registered reduced operation handles; descendants inherit egress/limit/kill policy; stable sanitized result |
| ST-009 | TM-010 | PWS-0007, PWS-0401 | Security/unit: script/import/expression/path-bearing executable field, duplicate keys, tamper, expiry, future version; schema-permitted inert provenance URL under network/file spy | No execution or dereference/file/network access; inert provenance stays data; trust state is visible; case unchanged; invalid template code and plain remediation |
| ST-010 | TM-011, TM-018, TM-026 | PWS-0113, PWS-0201, PWS-0202, PWS-0204, PWS-0206, PWS-0305, PWS-0805 | Security: distinct prohibited values, including extracted/OCR text, at each typed diagnostic seam; PWS-0805 adds entry mutation/addition/removal after preview and before write | Baseline contains no prohibited value; later produced entries/digests exactly match the frozen approved snapshot, mutation fails closed, and cancel writes nothing |
| ST-011 | TM-012, TM-013, TM-023 | PWS-0101, PWS-0104, PWS-0105, PWS-0108, PWS-0113, PWS-0201, PWS-0202, PWS-0204, PWS-0206, PWS-0705 | Integration/security: cancel/crash/power-loss, pre-manifest source orphan, stale/raced/aliased prepared source or cache, OCR/searchable private-temp cleanup, result/output/status-authority survival, fixed-section oversized map and logical overflow, unrelated case, lock/expiry/restart | Partial data stays operation/case scoped and non-authoritative; one-shot worker terminates before source/result authority release; an unconfirmed document worker retains at most page-rounded bounded volatile output and no persistent writer; indeterminate persistent publication quarantines and blocks reuse; wrong-case/raced state is rejected; unrelated case is not visible |
| ST-012 | TM-014, TM-015 | PWS-0112, PWS-0206, PWS-0406, PWS-0407 | Integration/security: truncated/wrong-page/encrypted/unrenderable output, false text-presence status, collision/alias/UNC policy, ZIP names, destination-local temp digest mismatch, disk-full/permission/partial multi-file | Required verification failure blocks artifact/package success; each commit is atomic; partial set is inventoried for cleanup/retry; previous good output remains |
| ST-013 | TM-016 | PWS-0105, PWS-0204, PWS-0206, PWS-0208, PWS-0305 | Integration/accessibility: signed-marker source through fail-closed OCR gate, later modify/cancel/export and signature-image wording | PWS-0204 does not modify marker-present/unknown state; later warning/confirmation precedes change; original preserved; output says unsigned derivative; visual mark makes no crypto claim |
| ST-014 | TM-017 | PWS-0405, PWS-0601, PWS-0603, PWS-0606, PWS-0607, PWS-0608 | Security/golden/manual: visual-cover negative case and adversarial text/image/object/revision/metadata/OCR/viewer/resave canaries | Ordinary shape never passes; any recovered/unknown canary blocks export; independent gate remains mandatory |
| ST-015 | TM-020, TM-021 | PWS-0005, PWS-0204, PWS-0801, PWS-0802, PWS-0803, PWS-0808 | Supply-chain/release: OCR runtime-closure size/hash/layout/extras, inventory/hash/signature/SBOM mismatch, tampered/expired/wrong-channel/rollback/interrupted update | Reject changed OCR closure before launch; reject release/update mismatch before install/use; previous version recoverable; packaged inventory matches approved inputs |
| ST-016 | TM-024 | PWS-0206, PWS-0403, PWS-0404, PWS-0408 | Unit/accessibility/content: per-page OCR failure, confident heuristic, unconfirmed assignment, warning/error/summary wording corpus | Missing extractable text and automation cannot complete a requirement; “Needs review” and concrete claims persist; prohibited claims fail |
| ST-017 | TM-025 | PWS-0105, PWS-0401, PWS-0405, PWS-0501 | Security/contract: XFA, JavaScript/actions, forms, attachments, encryption, archive/incremental-revision fixtures | Typed present/unknown state; unsupported policy blocks or verified profile handles it; no active content silently retained |
| ST-018 | TM-026 | PWS-0006, PWS-0104, PWS-0305, PWS-0408, PWS-0501 | Unit/integration: Unicode display names, metadata, signature assets, summaries, random internal filenames | Storage/cache/temp names reveal no display value; only intended user output contains approved content |
| ST-019 | TM-027 | PWS-0503, PWS-0504, PWS-0505, PWS-0507 | Security/golden/manual: incremental re-encoding/grayscale on small text, stamps, signatures, mixed scans, and an unattainable target | Policy stops at the measured floor; affected pages/policy are reviewable; unsafe/unattainable result is explicit; no silent extra degradation |
| ST-020 | TM-028 | PWS-0003, PWS-0009, PWS-0113, PWS-0206, PWS-0805, PWS-0806 | Security/process: fixture provenance audit, known synthetic markers, prohibited sample/token in index/history/artifacts, sensitive bug-sample workflow review | Repository/CI rejects prohibited material without echoing it; every fixture has safe provenance; restricted samples never enter repository/routine CI |

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
forensic-erasure claim. PWS-0706 retains the later human inventory/retention workflow; PWS-0113
adds the separate startup-recovery and sanitized-diagnostics evidence described below.

## PWS-0104/PWS-0109 immutable-ingest and manifest evidence

The immutable-ingest and shared repository suites implement the PWS-0104/PWS-0109 portions of
ST-004 through ST-006, ST-011, and ST-018 to the extent listed here. Unit tests cover complete
schema and semantic validation, exact lexical tokens, canonical serialization, source/page
candidate construction with and without existing operation history, independent
streaming/readback digests, short-read and corrupted-readback oracles, cooperative cancellation,
bounded identifier collision handling, callback sanitization, autosave base-revision conflicts,
and path-free result/error models.

Windows end-to-end tests hold the real PWS-0103 source lease while importing generated JPEG carriers
immediately below, at, and above the copy-chunk boundary. They compare the external, committed-copy,
result, and manifest digests; retain an external content/size/modification-time/file-identity
snapshot; schema-check the committed manifest; and reopen the case through a fresh lifecycle
service. Other cases cover advisory duplicates with distinct copies, cancellation after the first
copy chunk, quota rejection, missing/size/digest integrity changes, a link-count source change,
deadline expiry while case coordination is contended, sequential stale commit rejection, and two
commits released concurrently from the same snapshot. Workspace races also prove the deadline
checkpoint uses 50-millisecond timed lock waits, never enters the protected section after expiry,
releases a just-acquired lock when the terminal checkpoint fails, and remains reentrant.
The per-case guard is also exercised across separate broker processes through the identity-derived
Windows mutex. The manifest primitive and store-level suites distinguish `committed`,
`not_committed`, and `unknown`; reject a different recoverable checkpoint before source
publication; recover before a subsequent import; and settle cancellation or process-control
interruption from the observed checkpoint/manifest state. A confirmed replacement wins over a
late cancellation or interruption, while uncertain publication retains the exact random recovery
artifacts. Unit fault injection also proves every lease cleanup target is attempted once, an
uncommitted primary error is preserved, and cleanup faults cannot mask a confirmed commit.

Repository integration tests exercise rapid autosave coalescing against the real Windows adapter,
a representative source/page/operation/asset/overlay/assignment/confirmation save-load graph,
partial staged bytes, an injected storage-write failure representing disk-full/write failure at
the adapter seam, configured quota exhaustion, malformed and future checkpoints, corrupt-current
recovery, and same-snapshot concurrency. Power-loss durability and a physical volume exhaustion
test are not claimed.

Security cases prove random internal naming and manifest-only display-name persistence, sanitized
results/errors for the exercised success and raw post-publication failure paths, stored-copy
hard-link mismatch, absence of shell/network imports in the ingest layers, and an exercised
post-publication cleanup path that leaves explicit sibling/outside sentinels and the external source
unchanged. Current tests do not claim end-to-end parent/original/temp/manifest reparse races,
physical-volume exhaustion, power-loss durability, or bounded restart orphan cleanup from ingest
behavior alone. Those claims remain with the broader ST-004/ST-005/ST-011 owners, including the
dedicated PWS-0103 and PWS-0113 suites. Required Windows
filesystem controls are evidence only when the real supported volume exercises them; a mocked or
skipped required race is not a pass.

## PWS-0105 PDF structural-inspection evidence

PWS-0105 implements its portions of ST-001, ST-002, ST-005, ST-008, ST-011, ST-013, and ST-017
through one closed adapter and one one-shot broker path.

The pikepdf contract matrix inspects the complete synthetic PDF corpus. Ordinary PDFs return
bounded page/object counts and no registered feature markers; malformed and password-required
fixtures return distinct closed states; signed-marker, attachment, annotation, action-only, and
orphan-script fixtures produce their specific conservative markers. Open calls use an empty
password, disable recovery, and suppress raw warning text. Tests enforce input/page/object,
object-graph depth/collection/node, cancellation, and monotonic deadline limits, discard syntax
warning values, and prove raw stream/library failures do not escape the adapter. Feature results
remain markers, not cryptographic signature validation or a general absence proof.

The storage/worker broker suites authenticate the complete sealed `PreparedSourceSummary`, obtain
an independent same-object read handle with no path lookup, reject stale/mismatched summaries, and
exercise a denied source write. Handle-transfer tests require reduced read-source and
write-result-pipe masks, non-inheritance, and no same-access duplication. The `pdf.inspect` codec
accepts exactly one source reference plus two distinct positive handle parameters. Result-frame
tests reject truncation, surplus, oversize, duplicate keys, future versions, and wrong operation or
source UUIDs without echoing bytes. Broker lifecycle tests assert that success, worker failure,
cancellation, deadline, and malformed or misbound results terminate the one-shot worker
before the source lease and pipe close; only protocol success permits an EOF read after
termination.

A supported-Windows integration test stages only the selected pinned PDF-engine packages into the
disposable worker runtime, launches the real zero-capability AppContainer/Job host, prepares a real
synthetic pending copy, transfers the two exact handles after `pdf.inspect` negotiation, and
receives a correlated typed result only after the one-shot generation is stopped. The test also
asserts that no pending case-temp artifact remains; the worker-private copy stays inside the
generation-scoped AppContainer profile cleanup boundary established by PWS-0107. Existing
PWS-0107 live tests retain path-based case/external/user-temp denial and network/descendant
containment; PWS-0105 does not infer those properties from a unit double.

The standalone qpdf security seam requires the reviewed absolute qpdf 12.3.2 executable. Tests use
clean, malformed, and metacharacter-bearing synthetic paths; assert the fixed `--check`,
`--suppress-recovery`, `--no-warn`, `--` argument vector with `shell=False`; exclude ambient
`PATH`, `PATHEXT`, proxy, and command variables; close anonymous-pipe stdin; count/discard bounded
stdout and stderr concurrently; map only documented return codes; and prove cancellation and
timeout termination escalation. Tool output and unexpected return details never enter errors or
logs. Separate dependency capability tests bind the admitted pikepdf/libqpdf native closure.

This evidence proves structural inspection of one prepared PDF, not rendering, export acceptance,
signature validity, active-content removal, or general case-object access. PWS-0106, PWS-0112,
PWS-0208, and PWS-0501 retain those later claims.

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
6. The no-op receives no document handle. Path opens for a case root, sibling case, originals,
   manifest, journal, export history, external path, user temp, and unrelated profile fail.
   PWS-0105 separately proves that its deliberately authenticated prepared-source/result handles
   work while source writes and sibling/other access remain denied.
7. Handshake, operation, cancellation-grace, and shutdown deadlines; result/deadline and
   result/cancel races; crash/partial frame; malformed message; clean close; forced stop; and fresh
   restart converge on exactly one stable terminal client state without raw values.
8. The exact job tree exits before runtime/code access grants are revoked and the per-launch
   profile is deleted; cleanup failure is reported rather than hidden.

A socket monkeypatch, proxy setting, firewall-rule inference, launcher Boolean, test-only
production RPC operation, path-only mock, or skipped required platform probe is not equivalent
evidence.

## PWS-0201 JPEG/PNG intake evidence

PWS-0201 implements its portions of ST-001, ST-002, ST-004, ST-005, ST-008, ST-010, and ST-011
through a worker-only Pillow adapter and a fixed image-normalization RPC/artifact contract.

Domain and adapter tests cover content/identity mismatch; malformed and truncated JPEG/PNG;
decompression, dimension, pixel, metadata, conservative peak-memory, temporary-storage, output,
cooperative cancellation, and wall bounds; all eight EXIF orientations; additive quarter-turns;
CMYK; palette/alpha/`tRNS`; multi-frame
rejection; dependency-version mismatch before source access; cancellation/timeout; and exact opaque
RGBA8 output. Protocol and broker tests reject extra/missing/mistyped parameters, aliased IDs and
handles, wrong content kind, wrong-case/page/source/output correlation, hostile length/digest/alpha
output, early worker exit, unconfirmed termination, and stale-generation state.

Windows integration proves the external original and random immutable case copy remain byte-identical;
only an authenticated read-only prepared/committed source and fixed output section enter fresh
one-shot generations; failure leaves the manifest and UI cache unchanged. A direct adapter probe
proves real Pillow normalization inside AppContainer/Job containment, while deterministic adapter
tests prove private-temp capacity refusal, partial-write cleanup, and a subsequent clean retry.
Generic WorkerClient/Job tests remain the authoritative CPU, deadline, forced-termination, and
descendant-containment evidence. Privacy tests use distinct path, filename, metadata, digest,
pixel, and raw-error canaries and scan typed representations, progress, tracebacks, and routine log
capture. The GUI receives only validated fixed-layout pixels and never invokes Pillow or decodes a
worker-produced JPEG/PNG stream.

## PWS-0202 required scan-transform evidence

PWS-0202 extends the PWS-0201 image boundary without weakening it. Its completion evidence must
cover the PWS-0202 portions of ST-001, ST-002, ST-004, ST-005, ST-010, and ST-011 through a distinct
closed transform request/artifact contract rather than widening `image.normalize` version 1.

Pure and fixed-seed generated-property tests must prove exact operation value bounds, canonical
serialization, deterministic replay, undo/redo, cache-key separation, and the fixed order: EXIF,
crop in full logical pre-manifest-transform coordinates, accumulated quarter-turn, deskew,
contrast, background cleanup, then bounded fit/serialization. Extreme/empty crop and fine-angle
deskew values fail before worker launch. The six-case approved public-synthetic golden set proves
rotation, crop, deskew, contrast, cleanup, transparency, and varied page geometry with exact RGBA8
comparison and recorded visual review; a current production result is never auto-promoted.

Adapter and worker tests must exercise exact and just-over byte/pixel/memory/temp/output boundaries,
capacity refusal before decode, disk full after a partial private write, identity-matched cleanup,
fresh retry, cancellation at every cooperative phase, native-call timeout termination, late-result
suppression, malformed operation/result data, and one-shot authority release. One constrained
Windows success path must preserve both the external and committed source SHA-256. Focused broker
and real-adapter tests separately prove that rejection, cancellation, and resource failure receive
no source-write authority, remove private intermediates, suppress late output, and permit a clean
retry. Together they demonstrate bounded software behavior only; they do not certify named hardware
or storage media.

Privacy tests present distinct path, filename, metadata, operation, source/pixel digest, raster, and
raw-exception canaries at typed models, representations, progress, traceback, and routine-log seams.
The GUI receives only completely correlated fixed-layout opaque pixels. Neither a transform preview
nor a background-cleanup result is logged, described as permanent removal, or presented as proof
of legibility. PWS-0203 retains user confirmation and accessible before/after controls; PWS-0204
retains OCR subprocess, text, and language-pack evidence.

## PWS-0204 bounded local OCR evidence

PWS-0204 implements its portions of ST-001 through ST-005, ST-007, ST-008, ST-010, ST-011,
ST-013, and ST-015 through a separate OCR-only worker composition and a closed request/progress/
report/volatile-candidate contract. A candidate is not OCR text, a case artifact, a verified
searchable derivative, or an export; PWS-0206 retains those claims.

Domain, protocol, host, and broker tests cover path-free typed models; exact source/case/operation/
output/profile binding; signed-marker and unknown/encrypted PDF rejection before launch; malformed,
duplicate, noncanonical, oversized, wrong-version, wrong-engine-profile, wrong-page, wrong-digest,
and wrong-source results; capability absence; worker crash; cancellation; deadline; source mutation
after termination; terminal races during output authentication; and candidate/output cleanup
failure. Progress is phase/page-count only and remains monotonic. Every terminal path must terminate
the one-shot generation before source/report/output authority is released and must preserve the
immutable source and last-valid manifest.

Adapter and runner tests bind OCRmyPDF `17.10.0`, Tesseract `5.5.3`, `image_dpi=300`, standard PDF,
`mode="skip"`, pypdfium, fpdf2, `optimize=0`, and `jobs=1`; prove no-DPI image acceptance; and
reopen/hash/page-count the private candidate. They admit only the fixed OCRmyPDF built-ins plus the
PaperWorkShell plugin, never scan installed entry points, and exercise an exact Tesseract hOCR
argument vector. Runner tests require the configured absolute executable, fixed cwd, array
arguments, `shell=False`, no ambient executable search, `OMP_THREAD_LIMIT=1`, bounded/discarded
tool output, cooperative cancellation, and caller-timeout terminate-to-kill escalation for the
direct child. The narrow PDF-verifier tests require an empty password, `attempt_recovery=False`,
and content-free failure for an encrypted candidate.

Private-temp tests cover entry/byte limits, reparse/special entry rejection, a legitimate
create/delete sampling race, monitor failure precedence, strict recursive cleanup, and cleanup
failure overriding an earlier cancellation. Worker-operation tests reject prospective capacity
shortfall before staging or OCR, inject disk-full after a partial private source write, prove the
pinned operation tree is empty, and complete a fresh retry. Runtime-admission tests bind the
code-owned manifest fingerprint and exact 39-file executable/DLL/`eng` closure, rejecting a wrong
manifest, size/hash mutation, missing or extra entry, decoy layout, traversal, reparse state,
hardlink, and special entry before launch.
Process-policy tests reject shell/spawn or any child whose executable, argument zero, cwd, or
minimal environment differs from the configured authority.

The completion-level supported-Windows scenario must use the production launcher and real locked
engines, not a direct unrestricted adapter call. It authenticates the immutable source lease,
launches the zero-capability OCR Job with a two-process maximum, negotiates `ocr.process`, transfers
only source-read/volatile-section-write/report-write handles, completes OCR, confirms the complete
Job tree stopped, and independently validates the report and one-page PDF candidate. It records
unchanged external/committed source SHA-256 and unchanged authoritative manifest, private-temp and
volatile-output cleanup, structured progress, and no prohibited canary in routine logs. Existing
PWS-0107 live worker/descendant network probes remain the authoritative egress-denial control; the
OCR child must run under that same no-breakaway Job and AppContainer policy.

Current dependency inventory and the hash-addressed runtime manifest establish task-level engine
admission and redistribution review. PWS-0205 owns additional offline language packs. PWS-0208 adds
the explicit signed-source derivative warning/confirmation and incremental workload measurements.
PWS-0801 still owns the clean release-environment SBOM, final license/notice bundle, and comparison
of every packaged runtime/build object with release inputs.

## PWS-0206 searchable mixed-output evidence

PWS-0206 implements its portions of ST-001, ST-002, ST-004, ST-005, ST-007, ST-010 through ST-013,
ST-016, and ST-020 through a pure versioned plan/outcome contract and the additive
`pdf.export.searchable` version 1 worker boundary. It reuses the admitted OCR adapter/profile on one
private base page at a time, not the `ocr.process` RPC or its broker-owned volatile section. Neither
an OCR engine exit code nor a private page result is searchability evidence. The final output page
must pass the supported worker-side non-whitespace text-presence check, and every page must retain
its exact independent outcome.

The deterministic input matrix is fixed:

| Source case | Closed fixture | Required eligibility/outcome oracle |
|---|---|---|
| Born-digital PDF | page 1 of `pdf-ocr-readable-mixed` (`DIGITAL PAGE 206`) | `preserve_extractable_text` → `existing_text_preserved`; source/output PDFium pixels match and output extraction finds the digital token without OCR/rasterization |
| Image-only PDF | `pdf-ocr-raster-text` (`SCAN PAGE 206` exists only in pixels) | `ocr_required_image_only_pdf` → `ocr_text_verified`; the real local output has non-whitespace extractable text, without claiming that OCR reproduced the pixel token accurately |
| Image source | `png-ocr-raster-text` (same packed pixels) | `ocr_required_image_source` → `ocr_text_verified`; the real AppContainer output has non-whitespace extractable text, without an OCR-accuracy or exact-token claim |
| Mixed PDF | both pages of `pdf-ocr-readable-mixed` in order | page 1 preserves existing text, page 2 gains verified OCR text, both pages render, and the aggregate all-pages fact is true only after both page facts pass |
| No-text result | existing closed checkerboard `pdf-image-only` or an injected private page result with no output text | `ocr_no_text_retained` with `not_verified`/`no_extractable_text`; visual page remains and aggregate searchability is false |
| Page-local recognition failure | inject the exact admitted Tesseract-recognition failure condition only for the raster page in `pdf-ocr-readable-mixed` | page 1 remains `existing_text_preserved`; page 2 is `ocr_failed_retained` with `ocr_engine_failed`; no whole-document searchable claim |
| Signed/unknown PDF | `pdf-signed-marker` plus an injected unknown structural state | OCR/pixel modification is blocked, original page is retained, report says `ocr_blocked_signed_source_retained`, derivative says **unsigned derivative**, and original/case-source hashes do not change |

Fixture-generator tests additionally prove exact one-bit PNG structure, absence of accidental text
operators in the image-only PDF, born-digital/raster page order, and byte identity of decompressed
PNG/PDF raster streams. Fixture validation regenerates the closed tree and canonical index in a
temporary directory; exact SHA-256/provenance drift fails. No existing fixture bytes are refreshed.

Pure domain tests bind manifest revision/digest/CAS authority, source/order/transform/profile
fingerprinting, closed eligibility/outcome combinations, per-page failure isolation, conservative
signature-marker/unknown behavior, aggregate limited wording, version rejection, and sanitized
representations. Protocol tests must reject missing/extra/aliased handles; oversized plan, PDF, or
status sections; non-PDF/JPEG/PNG sources; invalid source totals; wrong object IDs/content kinds;
malformed status; wrong page order/count; and success without both PDF and status outputs.

Worker/application/security evidence must exercise strict pikepdf reopen, fixed qpdf 12.3.2 check,
every-page PDFium render comparison, every-page PDFium text presence, post-termination source
reauthentication, destination readback/final-name reopen, manifest/source hash preservation,
distinct prohibited document/OCR-text canaries at the typed seams plus no routine logging surface,
low-disk before work, injected disk-full after a partial disposable write, cancellation, deadline,
one page OCR failure, private-temp/section/
reservation cleanup, and a clean retry. The constrained profile must demonstrate no more than one
full-resolution raster and one private page-local OCR result live at once; page-status and plan tuples
remain bounded by the admitted page/source limits. PWS-0208 retains measured large-workload process-
tree evidence rather than PWS-0206 claiming a particular RAM/CPU/storage device.

Completion-level text and render claims require the locked real local closure: OCRmyPDF 17.10.0,
Tesseract 5.5.3 with admitted `eng`, pikepdf/libqpdf, qpdf 12.3.2, and pypdfium2/PDFium. Unit doubles
may prove failure isolation but cannot prove actual token extraction or viewer rendering. The
automated PDFium reopen/render is the minimum viewer smoke. If the release evidence names another
desktop viewer, its installed version and a synthetic-only open/page-order/text-search result must
be recorded; an absent or skipped named required viewer is not a pass.

## PWS-0208 signed-source confirmation and resource evidence

PWS-0208 extends ST-002, ST-004, ST-005, ST-010 through ST-013, ST-016, and ST-020. Its UI evidence
uses the synthetic `pdf-signed-marker` fixture only as a structural marker-present input. That
fixture contains an unsigned `/FT /Sig` field and no `/ByteRange`; neither its filename nor its
structure is evidence that a cryptographic signature exists or is valid. The unknown state remains
an injected closed structural result rather than a fixture mislabeled as signed.

Focused unit and accessibility tests must prove both reason presentations, the visible
**Unsigned derivative** label, the exact **Create unsigned derivative** action, and plain wording
that modification can invalidate an existing signature while originals remain unchanged. Cancel
is the initial/default action; Escape rejects; keyboard-only focus reaches both actions; every
control and warning has an accessible name/description; and enlarged text keeps the actions visible
while warning details remain scrollable. Accepting the presentation is not worker authority: the
trusted application must still validate the source-scoped confirmation state, while Cancel or
Escape must start no worker and create no destination. Confirmation must not unlock OCR or pixel
transforms for marker-present or unknown-signature pages.

The signed-source output evidence reuses the existing synthetic fixture and does not refresh a
golden merely because the confirmation surface was added. On a clean completion revision, record
external-source and immutable-case-copy SHA-256 before and after the confirmed export, the exact
source-scoped confirmation state used by the application gate, the derivative SHA-256 and page
count, and the content-free unsigned-derivative result label. Strictly reopen the derivative with
pikepdf, run the admitted qpdf check, and render every source/derivative page through the pinned
PDFium profile. Independently render every page with Poppler `pdftoppm`, then visually inspect the
synthetic output for expected page order, legibility, geometry, blank pages, clipping, and the
visible unsigned-derivative presentation where the owning output surface requires it. Record the
renderer/tool versions and inspection decision. A required skip, mismatch, or uninspected output
leaves the evidence open. A new golden is warranted only if an approved visual output contract
changes; it must remain synthetic, minimal, hash-bound, and review-approved rather than silently
refreshed.

Resource evidence uses workload `pws0208-searchable-mixed-incremental` version 1 and report family
`pws0208-resource-evidence` version 1. The constrained synthetic run must separately record sampled
complete-process-tree RSS, worker-process and Windows Job commit peaks, sampled worker-private-temp
peak/completeness/interval/count, active full-resolution page buffers/page results, repository and
policy revisions, cleanup, and retry results. `peak_worker_job_commit_bytes` is Job commit
accounting, not RSS. A cancellation scenario records bounded `cancellation_acknowledgement_ms`,
confirmed complete-tree termination, no authoritative partial output, authenticated disposable
cleanup, and a successful fresh retry. Reports and routine logs are scanned for prohibited fixture
tokens and path/content fields. Constrained-profile evidence proves bounded software behavior only;
it does not certify a named processor, RAM size, GPU, disk, VM, or physical device.
The exact recipe is
[`pws0208-searchable-mixed-workload-v1.json`](../../benchmarks/pws0208-searchable-mixed-workload-v1.json).
A focused supported-Windows integration harness produces the local report; the separate
`python -m tools.pws0208_resource_evidence <report>` command validates its canonical model and
budgets but is not a live runner.
Process, Job, and private-temp measurements come from the supported-Windows AppContainer run. The
active-buffer/result peaks come from a separate focused production-adapter scheduler observation
over the exact same workload and are explicitly labeled
`focused-production-adapter-instrumentation`; no searchable RPC-v1 field is added or inferred.

## PWS-0113 startup-recovery and diagnostic evidence

PWS-0113 implements its M1 portions of ST-004, ST-005, ST-010, ST-011, and ST-020 through separate
case-recovery, export-receipt, and diagnostics boundaries.

Pure recovery tests require deterministic internal-UUID ordering; valid `current`, `recovered`, and
`unavailable` combinations; bounded numeric cleanup/receipt counts; closed attention codes; and
sanitized error behavior under raw adapter failure. Application tests prove startup emits only
typed aggregate counts/resources and that an ordinary diagnostic sink failure cannot replace the
recovery result; process-control exceptions remain process control.

Windows startup integration first exercises PWS-0109 manifest recovery, independently validates or
restores stable export history from one authenticated retirement, then settles durable PDF export
receipts, and only afterward performs source-orphan, thumbnail-stage, and generic temp cleanup.
The desktop-process cut point uses an isolated profile and real offscreen PySide organizer: after
one edit reaches the durable autosave checkpoint but before manifest replacement, the parent
forcibly terminates the UI process and launches a fresh one. The restart must report one recovered
case, reopen the exact checkpoint revision, preserve both external and case-copy source bytes, keep
an outside sentinel unchanged, and expose no profile, filename, or display-name value in its closed
result.
It proves a valid checkpoint is authoritative before deletion, a corrupt case is reported without
artifact mutation, an exact UUID-named unreferenced source can be removed, a concurrent manifest
advance forces a reload before cleanup, and repeated stale-authority checks retain the candidate
with an attention result. Injected ordinary cleanup and export-recovery failures receive their own
closed attention codes rather than fabricated case-change or invalid-receipt facts. The
security suite uses sibling/outside sentinels and hard-linked orphan candidates to prove cleanup
cannot escape the selected authenticated case root.

Receipt tests cover strict canonical version/phase binding; future, noncanonical, misbound, and
forged payload rejection; conflicting phase rejection; invalid owned-entry isolation; ID
reservation; and private exact-marker disposal. A
fresh startup may replay `COMMITTED` through history append/manifest link only and clears it after
exact success; no worker or destination port is present. A `PREPARED` receipt without exact
downstream case proof remains inert and commit-ambiguous, while the same receipt with an exact
history record or manifest reference may settle idempotently. Invalid entries never replay, their
parseable IDs remain reserved, and their presence blocks a new export; an independently valid
committed sibling may still settle. History or cleanup failure remains pending for the next
startup. Missing or mismatched history with manifest references, multiple/malformed retirements,
and linked retirement owners all require attention and preserve every temp artifact. An absent
stable name may restore only one canonical, authenticated retirement; a generic `.tmp` file is
never treated as transaction authority. A stable history permits generic cleanup of one retirement
only when it is a strict record prefix. Generic temp cleanup does not run before history validation
or receipt classification, and a retry inventory/service failure also preserves temp evidence.

Diagnostic unit and security tests enumerate every closed event/field combination, reject invalid
types, duplicates, event-inconsistent optional fields, unsafe integer/capacity values, and mutated
objects, and compare the exact canonical JSON key set. Logger tests cover bounded displacement,
copy-before-retain, concurrent emitters, short/failed sinks, and exception sanitization. Summary
tests bind deterministic labels/order, typed-record aggregation, atomic logger snapshots, and the
exact version, operation-ID, count, generic-code, and numeric-resource inventory. Distinct prohibited objects are presented at every public typed
seam; failures, `repr`, canonical lines, and rendered summaries may neither stringify nor retain
their values. Static checks reject diagnostics dependencies on paths, networking, processes,
storage, workers, document adapters, or generic logging.

This is the sanitized data baseline, not the PWS-0805 support-bundle gate. PWS-0805 still owns
freezing an exact bundle snapshot/digests, accessible preview and opt-in confirmation, safe local
bundle writing, and cancel/permission/partial-write/prohibited-token verification on the produced
bundle.

PWS-0113's persistent worker runtime/code ACL repair is separate from the case scan and does not
replay a worker operation. Codec/journal tests must prove canonical bounded phase records, atomic
update, exact-stage cleanup, corruption retention, path-private representations, strict legacy-v1
decoding, and new v2 fields for the current-user peer SID plus each object's Windows volume
serial/file ID, inheritance state, and nullable per-file guard baseline.

A Windows helper process publishes v2 `PREPARED`, partial-grant, `ACTIVE`, and `REVOKING` cutpoints
while retaining a test-isolated protected mutex and is then forcibly killed. Recovery in the
separate parent process must observe `WAIT_ABANDONED`, authenticate the dead owner and
profile/AppContainer/peer-SID binding, reopen every exact object by the recorded identity, revoke
and verify the AppContainer SID per object, and restore the guard and inheritance baselines before
deleting the ledger and permitting a replacement grant. Overlap tests must prove two authenticated
live v2 leases share one guard, interrupted cleanup retains it while another `ACTIVE` ledger covers
the object, and final cleanup removes exactly one PaperWorkShell guard without removing any matching
ACE represented by the recorded baseline. Legacy v1 recovery must retain its original bounded
inherited-root behavior rather than being reinterpreted as an exact-object grant.

Separate tests must prove that an insecure directory, hardlinked resource, corrupt or unknown
record, peer-SID/allowlist mismatch, conflicting guard baseline, or object-identity mismatch blocks
new ACL work without mutating unproven state. New inventories must omit `__pycache__`, `.pyc`, and
`.pyo` objects and reject those objects as explicit roots. A cache-path identity mismatch in an old
v2 journal must retain the exact evidence and perform no unproved mutation, just like every other
identity mismatch. A valid conflicting cache is made explicitly readable to the live restricted
profile; both the initial worker and its descendant must still import the authenticated source,
leave the source cache byte-identical, and keep the fresh profile-private prefix empty under `-B`.
The complete live worker/descendant containment suite remains required on the
production pre-launch path. This does not claim general ACL repair, recovery from an unrecorded
mutation, or automatic cold deletion of abandoned AppContainer profiles/local temporary trees.

## Milestone evidence progression

### M0 foundation

- `validate-docs` checks local links/anchors and decision, task, and threat references offline.
- Schema/DAG, architecture, secret-scanning, and synthetic-fixture policies run in the aggregate
  quality suite.
- PWS-0010 reviews that each high-impact threat has a decision, owner, and planned oracle.

### M1 worker and immutable vertical slice

- Implement ST-001 through ST-008, the M1 portions of ST-010 through ST-012, and source hash/log
  audits.
- Retain PWS-0104/PWS-0109 source/pending/committed hash equality, unchanged external-original,
  full validated manifest+journal transaction, operation-aware import append, cancellation and
  interruption settlement, recovery, orphan, duplicate, autosave, and later-integrity evidence.
- Force worker crash, timeout, cancellation, malformed message, disk-full, permission failure,
  destination failure, and recovery. Retain PWS-0113 receipt-phase, invalid-case-isolation,
  exact-scope cleanup, and prohibited-diagnostic-token evidence.
- For PWS-0107 specifically, retain the live AppContainer/egress/job/environment/handle/filesystem
  evidence above; combine it with PWS-0105's real one-shot prepared-source/result-handle path rather
  than treating the no-op lifecycle by itself as case-access evidence.
- PWS-0114 requires original hashes, verified export, worker recovery, egress evidence, clean
  logs, and numeric budget evidence under the constrained resource profile.

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
- Run packaged-build dependency/SBOM/signature, install/update/migration/uninstall, constrained
  resource/low-disk, and residual-case matrices. Optional device observations do not replace the
  automated budget gate and do not certify an HDD or other physical configuration.
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
