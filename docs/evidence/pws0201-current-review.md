# PWS-0201 current implementation review

Status: implementation and local desktop verification complete; final acceptance
requires the successful exact-head CI record for the commit containing this report.

## Scope and baseline

The maintainer requested comparison of the current implementation against the supplied
PWS-0201 requirements, with fixes for confirmed gaps. The starting revision is
`0298c840421f106f55b1ac7c0c2077f171846600`. Work is isolated on
`codex/pws-0201-image-review`; unrelated files and PWS-0408 human acceptance remain
untouched.

The supplied archive reviewed `060cc36e12f37a57e9bf568adaee3160223b9659`. Its statement
that the imaging package has no decoder does not describe this checkout. PWS-0114,
PWS-0201, and PWS-0202 are already recorded done. The current portfolio decision is
PWS-D042 and ADR 0008; this graph has no entry for the archive's proposed alignment task.
Missing archive-proposed root
documents are not treated as frozen local requirements. No historical task is reset
or recreated to fit the older snapshot.

## Confirmed gaps and fixes

1. Record the missing Pillow/Qt decoder comparison and exact installed native closure.
   Keep the existing pinned decoder and distinguish development admission from shipment.
2. Reject 16-bit PNG and unsupported color/profile declarations before native decode,
   including PNG3 cICP, mDCV and cLLI.
   The old conversion clipped a synthetic 16-bit midtone to white, and silently ignored
   embedded profiles. Existing unprofiled 8-bit behavior stays within the admitted route.
3. Add a versioned ordinary mixed PDF/image export using existing worker, handle, quota,
   verification and publication primitives. Preserve the PDF-only version-1 contract.
4. Connect that export to a keyboard-accessible desktop action that needs no OCR or
   template. Verify the actual import, preview, close/reopen and export journey.

Generated image-page PDFs have a cumulative byte budget checked before append,
using the existing candidate capacity. The assembler retains compressed output
objects; full-resolution RGBA and per-page scratch are discarded sequentially.
The existing Job memory limit remains the hard native-memory boundary.

The work changes no immutable source or manifest schema, adds no runtime dependency,
and creates no sibling interface, service or content ownership. The shared heavy-work
limit and network-denied worker remain authoritative. Release-interface facts stay `TBD`.

## Automated verification

Use focused decoder, protocol, source, export, organizer, privacy and accessibility
regressions during implementation. Run repository format/lint/import/type checks and
the dependency, fixture, schema, docs, task, portfolio and secret validators. Exercise
failure and constrained-resource paths, then one complete quality CI run on the exact
final revision. Record desktop observations separately from automated tests. Review
performed by this Codex session is self-review/additional automated review.

The following focused checks passed during implementation:

- Decoder: twelve unsupported-depth/profile regressions failed before the fix;
  six additional PNG3 cases failed before their fix. The latest decoder/privacy/
  signature batch passed 104 tests.
- Export: 444 focused tests plus 172 subtests passed. After additional typed-profile
  and cumulative-byte regressions, the affected domain/adapter/image batch passed
  107 tests. All eight EXIF orientations compare actual RGB pixels.
- Live Windows integration: JPEG and PNG import, reopen and v2 export passed
  inside AppContainer with no OCR resources granted, seven verification checks,
  two pages, unchanged external/case hashes and empty operation temp state.
- Desktop adapters: 274 focused tests passed; the final changed-surface batch
  passed 167 tests. Keyboard shortcut activation, accessible controls, enlarged
  text, cancellation/retry focus and saved-output refresh failure are covered.
- Current-capability handshake fixtures were updated after a focused client
  lifecycle regression exposed stale capability lists. The repaired client,
  protocol, validator and framing/security batch passed 190 tests plus 212 subtests.
- Privacy/resource/recovery batch: 190 tests passed, covering immutable-source
  checks, prohibited canaries, cache integrity, disk-full cleanup, cancellation,
  timeout, worker cleanup and fresh retry.
- Strict typing passed across 597 source files. Schema, task graph, dependency,
  Pillow native-closure and documentation validators passed. Final formatting,
  lint and all 13 import boundaries passed.

The shared local Git repository's fixture scan correctly rejects a 4,079-byte
unapproved PNG reachable only from an unrelated Codex capture ref. It is absent
from this worktree, HEAD, branches and remote branches. That ref and its content
were left untouched. Branch validation uses a separate, full-history, single-branch
clone with the exact task changes and the unchanged scanners. This is separate
from the final exact-head GitHub quality run; neither scanner has an exemption.

### Final-gate corrections

The first full CI attempt, [run 36721148531](https://github.com/PikkuJanne/PaperWorkShell/actions/runs/36721148531),
tested `a55c5b7a3866fac5c54640e5de06eaee9fe86339`. Its unit lane passed 4,720
tests and failed three checks. It is not a successful final-gate record.

- The scan-quality baseline binds 27 implementation files, including nine desktop,
  worker and protocol files changed here. The production evaluator and no-replace
  exact-revision recorder were rerun against that commit. The validated candidate
  differs only in its revision: all eight metric rows, corpus/profile digests and
  resource-policy fields are unchanged. The evaluator and validation rules are
  unchanged. The baseline, domain and service regression batch passed 106 tests.
- Two overlay-reader corpus checks assumed the developer machine's default qpdf
  location, while CI deliberately installs the pinned checker elsewhere. The
  developer helper now honors `PWS_QPDF_EXECUTABLE`, verifies its pinned version
  and uses the existing bounded checker. Regressions first reproduced ignored
  configuration and silent fallback; they now require the configured executable
  and fail without output when it is unavailable. Approved artifact hashes and
  product overlay behavior remain unchanged. The corpus and reader-helper batch
  passed all 32 tests, including actual execution from a copied nondefault path.

These corrections affect test tooling and evidence, with no further product runtime
changes after the desktop journey. A complete CI run is still required for the
follow-up commit containing them.

## Desktop observations

The original development-runtime attempt exercised keyboard import and Escape
cancellation at effective 200% Qt scaling. Cancellation returned to an empty case
with Import focused and no source committed. Closing during a second import also
completed cleanly. Runtime admission was slow: the ordinary development environment
contains about 12,800 non-cache entries. Existing synchronous ACL inventory/grant
work occurs before worker creation and delays cancellation during this phase.

The resumable helper offers an explicit staged-worker-runtime option using the
existing synthetic integration harness. This stages trusted copies and substitutes
only runtime locations; ordinary desktop controllers, dialogs, source ingestion,
AppContainer, ACL validation, Job limits, RPC and publication remain active.
This does not demonstrate that the full development environment's launch delay
is fixed. No real document, content screenshot or local source path is committed.

The staged-runtime desktop journey passed on 2026-09-30 with effective 200% Qt
scaling and the constrained resource policy:

1. Reopened the empty case after cancellation and imported the synthetic Unicode
   JPEG through Ctrl+I. Its intrinsic orientation produced the intended upright
   preview without an organizer rotation.
2. Imported the transparent PNG through the same dialog. Both previews showed
   the expected upright target, top-left marker and white background.
3. Closed the autosaved case by keyboard and reopened it through Ctrl+O. Both
   pages and their order were restored; both previews became ready again.
4. Used Ctrl+Shift+E and the ordinary save dialog. The app announced that the
   verified PDF was saved and recorded in case history. No OCR or template action
   was used.
5. Closed the app cleanly. Strict pikepdf reopen found two unencrypted pages and
   zero syntax findings. PDFium rendered both pages; visual inspection confirmed
   orientation, framing, markers and alpha composition. Each page is 144 by 192
   points with a 600 by 800 image, consistent with the documented 300-DPI policy.
   The derivative is 27,793 bytes and has no font resources.
6. Rehashed both external sources and both immutable case sources after export:
   all four match their original expected hashes.

This is automated interaction with the real desktop, not a first-time-user or
screen-reader acceptance claim. The staged runtime does not certify an installer.

## Reproduction and final gate

Use `python -m tools.dev` with `format-check`, `lint`,
`check-import-boundaries`, `typecheck`, `validate-pillow-runtime`,
`validate-dependencies`, `validate-fixtures`, `validate-schemas`,
`validate-docs`, `validate-task-graph`, `validate-portfolio-boundary` and
`scan-secrets`. The isolated clone is required locally when unrelated capture
refs contain unapproved binaries; the final GitHub checkout has the branch history.
The synthetic coverage index lists the focused pytest modules and desktop recipe.

The final gate is the existing **Local-equivalent quality gates / Windows quality
suite** workflow on the exact commit containing this report. Its immutable run URL,
commit SHA and result belong in the completion report. No future CI outcome is
asserted by this source document.

## Review and limits

This session's self-review and additional automated review found and fixed the
PNG3 color-declaration and cumulative generated-image-output gaps. They are not
independent human approval. No golden output was replaced.

See [known limitations](../../KNOWN_LIMITATIONS.md), [enforced limits](../../LOW_SPEC.md)
and [decoder ADR](../adr/0010-image-decoder-selection.md). No physical-device,
NVDA, release, portal-acceptance, calibrated CMYK or color-management claim is added.
Shipment-specific license/SBOM closure remains PWS-0801. PWS-0408 human acceptance
and PWS-0409 remain unchanged. PWS-0202 was already done before this review;
no work on that task was started.

## Changed files

- `BUNDLE_MANIFEST.json`
- `KNOWN_LIMITATIONS.md`
- `LOW_SPEC.md`
- `README.md`
- `TASKS.yaml`
- `benchmarks/pws0207-quality-findings-v1.json`
- `docs/adr/0010-image-decoder-selection.md`
- `docs/architecture/image-intake.md`
- `docs/architecture/page-organizer-ui.md`
- `docs/architecture/pdf-export.md`
- `docs/architecture/worker-lifecycle.md`
- `docs/development/dependency-policy.md`
- `docs/development/image-intake-coverage.md`
- `docs/development/scan-quality-evaluation.md`
- `docs/development/synthetic-fixture-policy.md`
- `docs/evidence/pws0201-current-review.md`
- `docs/schemas/worker-rpc-v1.md`
- `docs/security/security-test-plan.md`
- `docs/security/threat-traceability.md`
- `packaging/dependency-inventory-v1.json`
- `packaging/pillow-runtime-closure-v1.json`
- `src/paperworkshell/__main__.py`
- `src/paperworkshell/app/organizer.py`
- `src/paperworkshell/app/pdf_export.py`
- `src/paperworkshell/domain/pdf_export.py`
- `src/paperworkshell/imaging/pillow_decoder.py`
- `src/paperworkshell/pdf/export_adapter.py`
- `src/paperworkshell/storage/source_ingest.py`
- `src/paperworkshell/ui/backend.py`
- `src/paperworkshell/ui/window.py`
- `src/paperworkshell/worker/desktop_composition.py`
- `src/paperworkshell/worker/host.py`
- `src/paperworkshell/worker/operations.py`
- `src/paperworkshell/worker/pdf_export_broker.py`
- `src/paperworkshell/worker/pdf_export_operation.py`
- `src/paperworkshell/worker/pdf_export_plan.py`
- `src/paperworkshell/worker/protocol/codec.py`
- `src/paperworkshell/worker/protocol/models.py`
- `src/paperworkshell/worker/protocol/state.py`
- `tests/accessibility/test_pages_pdf_export_accessibility.py`
- `tests/integration/test_image_pdf_export_worker_windows.py`
- `tests/unit/test_image_pdf_export.py`
- `tests/unit/test_pages_pdf_export_desktop.py`
- `tests/unit/test_pdf_export_entrypoint.py`
- `tests/unit/test_pillow_image_decoder.py`
- `tests/unit/test_pws0201_image_smoke.py`
- `tests/unit/test_pws0307_overlay_reader_corpus.py`
- `tests/unit/test_validate_pillow_runtime.py`
- `tests/unit/test_validate_worker_rpc.py`
- `tests/unit/worker/test_client_lifecycle.py`
- `tests/unit/worker/test_pdf_export_composition.py`
- `tests/unit/worker/test_pdf_inspect_protocol.py`
- `tests/unit/worker/test_pdf_thumbnail_protocol.py`
- `tools/dev.py`
- `tools/pws0201_image_smoke.py`
- `tools/pws0307_overlay_reader_corpus.py`
- `tools/validate_pillow_runtime.py`
- `tools/validate_worker_rpc.py`
