# ADR 0020: Avoid repeated implicit directory ACL propagation

- Status: Accepted implementation detail; installed-candidate timings remain separate evidence
- Date: 2026-11-06
- Task: PWS-0811
- Frozen decisions preserved: PWS-D012, PWS-D013, PWS-D027, PWS-D029, PWS-D041, PWS-D042, PWS-D043

## Evidence and decision

The reported small-document waits require measurement rather than weaker validation.
The installed R9 runtime contains 2,658 non-cache objects in its resource inventory.
A read-only profile measured 1.112 seconds to retain that inventory and 0.196 seconds
to close it. Exact bundled OCR admission took 0.182 seconds on the same host. These
measurements do not explain the full observed import wait and are not installed
end-to-end performance passes.

The worker grants and revokes access to individually authenticated, pinned objects.
Its former `SetSecurityInfo` call nevertheless reapplied the inheritance algorithm
below every directory. Microsoft documents this recursive behavior in
[SetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo)
and [automatic propagation](https://learn.microsoft.com/en-us/windows/win32/secauthz/automatic-propagation-of-inheritable-aces).
That implicit work is unnecessary when the broker already changes and verifies each
inventoried object independently.

Use the documented filesystem primitive
[SetFileSecurityW](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-setfilesecurityw)
only for a directory's non-inheritable worker grant or revocation. Its documented
behavior leaves children untouched. Microsoft marks this API obsolete; that is an
explicit maintenance tradeoff, not a claim that it is the preferred general ACL API.
Windows 11 x64 native tests verify the supported use here. Files and intentional
legacy inheritable operations continue using `SetSecurityInfo`.

The path comes from the retained directory handle. An additional read-only handle
authenticates that path against the retained volume/file identity before the call;
both identities are checked afterwards. Existing ancestor and resource share-read
handles prevent path replacement. Existing DACL control-state restoration remains.
No requested rights or sharing flags are widened. No runtime, source-content or
permission-validation result is cached.

The filesystem provider requires both `SE_DACL_AUTO_INHERITED` and
`SE_DACL_AUTO_INHERIT_REQ` when preserving automatic mode through this legacy API.
An independent Windows primitive matrix showed that supplying AI alone clears the
bit; AI+AR preserves it without changing an excluded child's ACL or control state.
The helper now requests the pair for True and clears both for False, preserving the
other applicable DACL controls and requiring exact readback. Inheritance restoration
uses that same pinned filesystem operation and the current DACL, not a stale whole
descriptor. The prior `SetKernelObjectSecurity` restoration returned success while
normalizing AI away; Microsoft explicitly advises against using it for filesystem
objects. It is no longer used for this restoration.

The first real R11-runtime source proof caught the AI-only optimization regression
before installer handover. Formal native regressions now independently establish
mixed parent/child inheritance and protected states, compare complete ACL bytes and
controls, keep excluded Python caches untouched, and exercise dead-owner recovery
after deliberately recreating the historical wrong flag. Ten checks passed on the
current Windows host; raw evidence is in `output/pws0811/development/acl-controls-native-v1.*`.

Cache only the four fixed OS DLL wrapper objects, with one bounded entry per
zero-argument loader. The measured inventory built 7,997 ctypes wrappers, spending
about 0.25 seconds constructing them. This change caches API wrappers rather than
security authority, OS access decisions, runtime bytes or document data.

## Installed desktop static runtime lease

A current-code/bundled-runtime diagnostic measured launch 6.162 seconds and
termination/cleanup 11.626 seconds of a successful 24.018-second original PNG
import. These phases are not an installed GUI timing pass. Image import normalizes
at 1 x 1 solely for inspection, then its first preview starts another worker. Dropping
one operation still pays the other complete runtime lease.

The validated installed desktop may retain one exact runtime/code lease for one
active case. Its ordinary plain/image, flattened and searchable exporters explicitly
borrow that same static owner. Default/development, package and qualified redaction
compositions retain the existing per-operation lifetime unless separately verified.
This extends ADR 0004's implementation;
frozen PWS-D012/D013 are unchanged. The lease owns only immutable runtime/code
handles, ancestor pins, direct SID ACEs and their exact recovery record. It owns no
case/workspace, source, output section, cache, destination, result pipe or document.
No resource inventory grows after admission. Retained identities, hardlink guards,
startup grants and bytecode policy are checked before every generation.

Borrowers must match the exact fixed interpreter/bootstrap/code/runtime/workdir and
recovery allowlist. Their actual admitted qpdf/Tesseract objects must already be in
the original retained inventory. No borrowing call extends that inventory or lazily
grants a missing engine/language path. Tool admission, per-launch environment, handler
scope, Job limits and all output verification remain independently enforced. Only
one borrowed generation may be active. Any consuming broker's failure retires the
shared owner; case close cannot race a still-running borrower.

Each operation still has a fresh parser, Job, capability, bootstrap, session and
pipes. The complete tree is stopped before reading/validating output. After transport
closure, close all profile-storage handles, delete the profile and require its exact
filesystem location absent. Keep only static runtime access. Recreate the same random
name only on S_OK for newly created storage; never adopt ERROR_ALREADY_EXISTS. Both
the returned and independently derived SID must equal the retained SID. Provision a
fresh protected empty PyCache. The original SID allocation belongs to the static
lease; temporary recreation allocations are freed separately.

[CreateAppContainerProfile](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-createappcontainerprofile)
documents creation of per-user filesystem and registry storage. Its new-datastore
success contract supplies production registry freshness; synthetic native registry
and file canaries exercise this behavior. There is no generic independent complete
registry-absence query. [DeleteAppContainerProfile](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-deleteappcontainerprofile)
qualifies storage cleanup when handles remain. S_OK alone therefore cannot satisfy
filesystem reset: a held storage handle or remaining folder fails reset and retains
exact cleanup authority. Every recreated SID must actually match, so no undocumented
cross-generation SID behavior is assumed.

Only protocol-successful terminal work can retain access. Cancel, timeout, fault,
launch failure or post-close artifact rejection retires it. Uncertain tree/cleanup
state is sticky and forbids reuse; known Job-zero cleanup can still retry without
clearing that fault. Case close settles autosave, releases this context, then releases
the case lease. Shutdown closes the same owner. Existing exact-owner/object journal
recovery also covers ACTIVE static access while no profile datastore exists. Separate
instances retain separate random profiles and preserve shared guard counts.

### Own live journal admission

The clean current-source/R11-input pipeline passed in 55.77 seconds. Its first
exact grant took 5.907 seconds; later launches still took 4.692–6.368 seconds,
although actual retained-resource checks took 0.302–0.377 seconds and fresh profile
creation took 0.027–0.040 seconds. The protected journal was repeatedly reopening
the same already pinned objects for its own live ACTIVE entry. The exact phase
receipt is `output/pws0811/development/ordinary-shared-runtime-v2.xml`.

Only that own ACTIVE entry may use an opaque in-memory witness instead of reopening
every object path. Every borrow still rereads and bounds the protected journal under
the existing mutex and requires exactly one byte-equivalent recorded entry, current
PID and process start, owner thread, derived profile SID, fixed roots, live inventory
reference and complete record/resource bijection. Missing or changed own records
reject reuse and poison the context. Publication eligibility is invalidated before
every journal write, including a write that commits and then fails readback.

This is not a cached security decision. Each borrow queries every retained resource
and ancestor handle for unchanged identity/type, reparse/delete state and file link
count, and reads every actual DACL. It requires the original inheritance control,
one explicit non-inheriting own-SID allow ACE with exactly read/execute rights, and
the recorded peer hardlink guard count. Own-SID denies, surplus, inherited or
inherit-only grants reject admission. Other principals' denies are not misclassified
as own-SID denies. Basic, callback and object access ACE SID positions follow the
documented Windows layouts ([basic deny](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-access_denied_ace),
[callback](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-access_allowed_callback_ace),
[optional object GUIDs](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-access_allowed_object_ace));
unsupported encodings fail closed.

Foreign records, including live same-PID records, and every default, dead, malformed
or non-ACTIVE record retain full existing path/identity/recovery checks. After any
foreign repair, revalidate the own witness again inside the mutex. An abandoned
mutex with only own ACTIVE access still fails. Final revocation uses the complete
existing recovery path and merges current foreign grants; it does not use a witness.

The first independent native witness/control matrix passed 26 checks in 5.30
seconds (`output/pws0811/development/active-witness-native-v1.xml`), including actual
own-ACE/control/guard tampering, record changes, missing records and foreign live
full-path validation. Installed timings and the broader final boundary matrix remain
separate requirements.

The identical source pipeline with the own-live witness passed in 43.12 seconds.
Its four warm launches took 1.016–1.251 seconds; the initial launch remained 6.180
seconds (5.752 seconds initial grant). Full final case-close recovery took 14.459
seconds and left no recovery record. The runtime inventory remained
`c84bb6a39a3007e10291f8930fc0480daa9172a65f9e68d57850383eebda44e9`;
the current owned source inventory was
`d3cc67f940a85a4b1a7d1b88bd5dd7eb1bb9e4cd56f981f8d13535696cc2c9cd`.
These are paired source-composition phase observations, not cold import/preview or
export target passes in an installed candidate. Raw phase data is in
`output/pws0811/development/ordinary-shared-runtime-v3.xml`.

The new unit/native lifecycle checks and final installed cold/three-warm timings are
required evidence; code alone does not prove this optimization. Pins may prevent
payload replacement until the case closes. An installer must not work around them.

## Verification and limits

`tests/security/test_worker_acl_propagation.py` verifies that grants remain exact:
an unrelated file stays inaccessible, a newly created child inherits no worker
grant, cleanup succeeds, and a noninventoried child's inheritance state is not
rewritten. The latter assertion fails with the actual former directory setter and
passes with the repair. The live ACL-recovery suite also covers interrupted and
overlapping grants and preservation of existing security state.

The final focused ACL invocation passed 32 tests in 11.49 seconds. A separate
native security invocation passed 23 tests in 76.11 seconds, including worker and
descendant network denial, unrelated-file denial, hardlink and ancestor-junction
protection, private bytecode isolation, and cross-composition recovery authority.
Their local XML receipts are under `output/pws0811/baseline/`; neither substitutes
for the installed candidate's user-visible timing and output checks.

The 776-object synthetic primitive comparison is reproducible with:

```text
.venv\Scripts\python.exe tools/pws0811_acl_timing.py --output output/pws0811/baseline/pinned-directory-cached-api-comparison.json
```

With cached API wrappers, the measured legacy grant/revoke totals were 1.465 and
1.426 seconds; repaired totals were 0.717 and 1.038 seconds. These are two pairs of
primitive measurements, not application cold/warm medians or hardware certification.
Final installed GUI and export timings must justify any remaining target deviation.
All source hashes, worker containment, network denial, timeout, cancellation,
temporary-storage budgets and final-output checks remain required.

## Rejected alternative and experiment correction

Opening directories with `MAXIMUM_ALLOWED` suppresses implicit propagation but
acquires write/delete rights. Live recovery reopens then conflicted with the original
share-read pinning. Widening sharing would weaken the boundary. This alternative was
reverted; its faster primitive benchmark did not establish a safe implementation.
`SetKernelObjectSecurity` was not selected for directory ACL writes: Microsoft's
documentation directs filesystem users to filesystem security functions.

The first new live experiment mistakenly used the normal recovery journal while R9
was active. Concurrent allowlist validation refused its cleanup, and pytest removed
the five disposable files. The exact abandoned synthetic profile
`162d2d9eadbeed1f77f766b2`, owner PID 19152, was subsequently removed through
`DeleteAppContainerProfile` and `AclRecoveryJournal.delete` only after verifying the
owner had exited, every recorded object was absent, every path belonged to that
exact pytest fixture, and the derived profile SID matched. The receipt is retained
locally at `output/pws0811/baseline/own-abandoned-test-cleanup.json`. No application
runtime or case ACL was changed. The test now uses its own recovery directory;
production allowlist and identity checks were not weakened.

The failed R11 source proof also exposed a test-evidence retention defect: its
private journal lived in pytest's numbered temporary tree. Later test sessions
pruned that tree while the code-resource guard correctly retained the exact code.
The original recorded inheritance baseline was therefore lost. It was not
reconstructed and that old recovery is not reported as passed. A read-only scan of
the 3,007 current own test objects confirmed no remaining allow mask for the exact
test profile SID; its original owner had exited and Windows reported profile
deletion. The affected R11 payload/code remain quarantined as own test artifacts,
with receipts under `output/pws0811/development/QUARANTINED-OWN-R11-TEST-ARTIFACTS.json`.
Actual user cases and originals were outside that scope. Further proof must derive
its runtime from pristine frozen inputs and match the complete file inventory.

Private native-test journals are now created outside pytest's numbered retention
tree from the start, beside the exact owned resources and outside every worker
grant. Bounded cleanup checks refuse any unreadable, redirected or nonempty journal
subtree and preserve both journal and objects together. Unknown records are never
parsed into invented authority, rewritten or deleted to make cleanup pass. Native
commands also use distinct output-owned `--basetemp` locations, never reused.

The production-composition source tests also exposed a pre-existing checkout
ownership limitation: an unchanged PDF adapter file was owned by the earlier
sandbox account; the executing user had Modify but not WRITE_DAC. The launcher
correctly refused ACL authority (`config.resource_identity`, Win32 error 5).
These tests now copy the complete current `src` tree to a test-user-owned staging
directory, verify every source/copy SHA-256 plus source stability, and retain the
full relative inventory in JUnit properties. Only the trusted code root, bootstrap
and working directory are injected; the actual production configuration builder,
interpreter/runtime roots, policy, launcher and all security checks remain. Original
checkout owners and ACLs are preserved. This fixture supplies source-integration
evidence and does not substitute for the real installed candidate.

The owned snapshot is created under the repository's ignored local test directory,
outside the per-user Windows Temp tree. The same bundled-runtime scan-review test
failed with code staged under Windows Temp and passed in 87.88 seconds with
workspace staging. This records the observed path distinction without claiming a
specific undocumented Windows virtualization mechanism. One immutable source
inventory is retained for the complete test session. Cleanup removes the snapshot
only when no per-test worker-recovery entry remains; uncertain cleanup retains
the exact objects for supported recovery and fails the test.

Setting the test-only `PWS_TEST_RUNTIME_ROOT` selects an already identified bundled
runtime while keeping the current staged worker code first in the bootstrap's
import path. The complete runtime-file inventory and its candidate marker are
recorded separately from the code snapshot in JUnit. An R9 runtime with current
source is diagnostic/source-integration evidence, not an R9 application result or
an installed-success claim for the next candidate.

## Exact installed structural PDF inventory

Three cold broad/exact comparisons on one staged V8 runtime and the same
cached mixed-source case verified and recorded identical PDFs. The exact
export grant admitted 757 files and 810 pinned objects. All three exact
cold backend times were 3.764–4.363 seconds. The median paired grant
reduction was 2.012 seconds. These results measure the backend on the host.
The installed application comparison measures native confirmation through
visible verified completion.

Use the reviewed fixed file inventory only for installed structural PDF
export with its bundled qpdf and case-scoped runtime authority. Require the
normal qpdf resource checks, startup checks, exact DACL verification and
Job lifecycle. A retained broad lease for the same case already covers this
composition and follows the existing revalidation path. Switching between
different exact inventories retires the earlier profile and verifies cleanup
before acquiring new access. The image-stage and removal compositions use
the existing base configuration. Updating the installed dependencies
requires reviewing the fixed inventory and rerunning native verification.

## Installed image normalization worker scope

Installed image inspection and page normalization use a worker that admits
the normalization adapter before reporting Ready. Image transforms retain the
image-page worker. Both launchers use the same exact image runtime inventory,
case authority and generation group. A normalization generation can hand off
the fixed grant to a transform generation under the existing revalidation
rules. Case close terminates all parser clients before retiring that grant.

Private V10 comparisons used the same 487 files and 513 pinned objects.
Normalization-only improved Ready by 68–376 ms in three untraced pairs.
Separate traced pairs isolated 86–148 ms in eager transform admission.
Each job denied access to the Qt canary, produced the same preview pixels
and removed its process tree and profile. The actual installed paint
comparison remains a separate acceptance check.

## Product and release effects

No dependency, document-processing algorithm, case/template/RPC schema, application
data location or third-party licence changes. No sibling product or service is
involved. Private data remains PaperWorkShell-owned and processing stays offline.
This implementation supplies no signing, public-release or compatibility approval.
The exact internal installer and its installed evidence must remain bound together.
