# PWS-0805 — previewable sanitized support bundle

**Status: done.** Implementation, focused verification, independent review and
shared source-quality closure are complete; packaged release qualification remains separate.
Dependencies PWS-0113 and PWS-0801 are done. Pending PWS-0802 owner agreements
and trusted signing do not gate this independent source work.

## Implemented and changed surfaces

- `diagnostics/bundle.py`: freeze only the accepted typed `DiagnosticSummary`,
  generate deterministic stored ZIP bytes with two fixed member names, and show
  complete safe contents plus exact sizes/hashes before creation. No case, log,
  content or environment-file collector exists. `diagnostics/errors.py` adds
  closed safe bundle/consent/cancellation/publication errors.
- `app/diagnostic_bundle.py`: one opt-in save use case through a writer protocol;
  preview freezes once, and save revalidates that exact snapshot without
  recollecting new state or echoing raw exceptions.
- `storage/diagnostic_bundle.py`: reuse authenticated Windows destination
  staging/readback/no-replace/final-reopen publication and central resource policy.
- `ui/diagnostic_bundle.py` and a narrow `ui/window.py` Help action: exact preview,
  unchecked consent, separate Save/location choice, one cancelable QThread,
  keyboard/focus/scaling/status announcements and shutdown ownership. The default
  desktop session records numeric application/Python versions only. A supplied
  existing typed logger can contribute additional approved recorded fields.
  No retrospective worker collection is implied.
- Dedicated unit, native integration, security and accessibility tests, fixed
  `tests/helpers/diagnostic_bundle.py`, and the cleared
  [published synthetic archive](../../tests/fixtures/diagnostics/v1/README.md).
  Its standalone allowlist and index bind both members and archive hashes.
- The three built-in localization catalogs receive additive extraction/refresh
  of approved UI messages. The [support contract](../architecture/support-bundle.md)
  documents fields, review, save, recovery, retention and exact scope.

The public synthetic archive is 1,626 bytes, SHA-256
`4f5bb7b36f1b58f36901f31c18b721a6875a44bc731fb8da3779e39b77662304`.
It reopens with exactly `summary.txt` and `allowlist.txt`; both full member
contents and hashes are independently checked. Fixed demonstration counts,
timestamp, UUID and codes are not real incident evidence. No accepted binary
fixture, corpus index or golden was refreshed.

## Verification receipts and development failures

Ignored `output/pws0805/` retains raw lane logs and the original window preimage.
The final focused canonical `test-unit` lane passed 202 tests with 7,411 deselected
in 9.51 seconds. Its selection includes existing diagnostic model/codec/privacy
and architecture-boundary tests plus the largest established summary-ID count
fitting the closed format bound. Earlier focused lanes passed 199 and 201 tests
before the final added coverage; their raw receipts remain separate.

Canonical focused security passed 19 with 1,222 deselected in 3.05 seconds,
including all prohibited-content categories, forged summary/bytes, collection and
raw destination errors. Canonical accessibility plus existing window keyboard
audits passed 13 with 333 deselected in 2.51 seconds. The first 200-percent-text
attempt failed because Qt's text-edit cursor rectangle could leave the inventory
below the outer viewport; the corrected focus logic reveals the control center
after layout, with one bounded timer. Both failed receipts remain retained.

The final canonical native `test-integration` publication lane passed eight tests
with 715 deselected in 2.60 seconds. It covers normal/constrained policies,
existing-destination refusal, cancellation, denied write, disk-full, low space,
seven bytes written followed by failure, and successive short writes that finish
with exact verified bytes. The strengthened short-write attempt first failed
because the test incorrectly expected successive short writes to fail; the
accepted destination writer correctly completed them. That failed receipt remains
`test-integration-final-short-write.txt`; no implementation was weakened.

Canonical lint, documentation and localization validation pass. Strict typing
passes all 908 source files at the recorded epoch. Canonical `format-check`
initially found the newly added format-bound test plus a concurrently edited
installer test. The support test was formatted; the unrelated installer file was
left to its owner. Earlier local lint/import-sort
and typing failures were corrected, including a redundant cast and two test-only
imports through non-exporting modules; no check was suppressed. Final shared
unit/security/privacy/bundle metadata reconciliation is coordinator-owned.
No full-suite or packaged-candidate pass is inferred from these focused results.

## Security, privacy, accessibility and resource boundaries

The archive has exactly two fixed safe names and no arbitrary-file attachment
path. It excludes OCR/form/document text, names, addresses, original filenames,
full paths, extracted metadata, thumbnails, signature images and document bytes.
Destination paths are local write authority only. Consent is false until the user
selects it. Opening review and canceling review/location create no file. Partial
or failed precommit writes preserve cases, existing files and logs and clean
only their stage; final success requires exact-name reopen/hash verification.
Cancellation after publication cannot undo an already committed file.

The default session log is bounded and memory-only. The closed format is capped
at 512 KiB using the established summary identifier limit; central normal and
constrained policies additionally enforce staging capacity/reserve and deadline.
There is one publication thread, no queue/executor/document worker or network
operation. No dependency, manifest schema, migration or product boundary changes.
Keyboard, accessible names, consent state, scroll reflow at doubled text size,
focus return, cancellation and window shutdown ownership have synthetic tests.
New packaged/NVDA observations remain release-gate work and do not invalidate or
repeat accepted earlier human evidence.

Support files remain at the selected local destination until the user deletes
them. Case cleanup/deletion and application removal preserve them; secure erasure
is not claimed. PaperWorkShell owns the evidence independently of ThirdLife.
No sibling source, private case, shared support schema, service, upload, contact
message, runtime CLI or adapter was added. Exact candidate release facts and
TASKS.yaml/BUNDLE updates remain with the coordinator.

## Coordinator closure — 2026-11-03

Full canonical unit testing passed 7,648 tests with two disclosed host-symlink
capability skips in 1,034.87 seconds in an exact current-source QA checkpoint,
`338013a97781991dab8f37c1db521f36f42589f8`. The original root Git/index/refs,
quality baseline and metrics remain unchanged; only the independent QA copy's
revision binding changed. All 319 application source files, including the support
workflow and catalogs, are byte-identical after that run. The isolated checkpoint
uses the current dirty source; it is not a reset to an older published branch.

The final installer-tool delta passed 250 selected unit cases, one disclosed
symlink skip and 122 subtests. Across the affected source features, 79 security
and 31 native integration cases pass. All 346 accessibility tests pass. Earlier
support-specific 202 unit, eight native, 19 privacy and 13 widget/window passes
remain scoped to their recorded lanes, with original failed attempts retained.

Canonical formatting, lint, strict typing (918 files), architecture/schema/task/
docs/portfolio/dependency/localization and resource/benchmark validators pass.
The final full repository privacy/fixture scan and all-ref secret scan pass;
neither has an unrun or stale result promoted to a pass. The published 1,626-byte
sample and exact allowlist remain unchanged with the hash above.

PWS-0805 and PWS-0804 are done independently of PWS-0802's signing/licensing and
clean-release qualification gaps. No further dependency-ready task exists;
PWS-0803 waits for PWS-0802. The local ZIP adds no upload, account, automatic
crash submission, document collector or portfolio dependency.
