# Human testing

This policy applies to all future PaperWorkShell human tests. It implements the
testing contract in [AGENTS.md](../../AGENTS.md#92-required-test-behavior). It
changes how evidence is gathered, without waiving acceptance criteria or changing
the frozen decisions. Required security, accessibility and release-gate checks
remain required.

Historical helpers follow this policy when next used: update or prefill their
interaction before asking the person to use them. No bulk rewrite is required,
and accepted M3/M5 observations must not be repeated merely to retrofit ergonomics.

## Keep the whole session easy

Human tests must be straightforward and easy to complete. Excessive effort,
repetition and context switching make people miss problems and reduce the quality
of evidence. Assess the complete session, including every reader or device, rather
than judging each prompt in isolation. Breaking a long procedure into short
prompts, adding a progress counter or offering pause/resume does not by itself
make the total work manageable.

On **17 October 2026**, after completing PWS-0606 human testing, the maintainer
reported that close to 40 steps was still difficult and explicitly rejected it as
human friendly. The lesson applies to all future human tests: reduce the actual
work and mental effort, rather than presenting the same burden more neatly.
Preserve those completed observations; do not repeat them to improve ergonomics.

Automate mechanically checkable work and reserve the person's effort for necessary
interaction and human judgment. Reuse valid accepted evidence. Consolidate related
observations into a clear, coherent activity where that reduces repetition without
creating a memory-heavy checklist. Minimize total actions, repeated page reviews,
application/device switches, manual recording, transcription and things the person
must remember. Prepare files, collect metadata and verify results automatically
where the test contract permits it.

Before handoff, walk through the complete human journey and record its estimated
total human actions and active time alongside the test plan. Include setup,
all readers/devices, save/reopen cycles, result recording and recovery. Explain
which remaining activities require a person and how repeated work was removed.
These estimates assess burden; they are not a timeout or a demand that the person
work at that pace. If the session remains arduous, redesign the method, test pack
or automation before asking the person to run it. Preserve mandatory acceptance,
security, accessibility and release-gate coverage; a difficult test is a design
problem to solve, not permission to silently omit its requirements.

## Reuse facts; collect new observations

Before asking a person to do anything, inspect the accepted local evidence and
automatically collect suitable environment facts. Keep the provenance: the source
task and record, its digest, what was observed, and any later maintainer statement.
Use a known value directly when the maintainer explicitly says it is unchanged.
Do not ask for the same confirmation or make the person type numeric versions
again. Ask only for missing or changed facts that cannot be established from
available evidence. Do not substitute an executable version for a viewer's About
version when the test requires the latter.

Reusing an environment fact does not reuse a test result. A previous pass applies
to its original task, artifact and procedure. New or affected behavior needs new
observations. Preserve accepted records and artifacts. Do not repeat an accepted
test solely because its helper or wording is being improved; retain valid partial
progress when the tested behavior and bindings remain unchanged.

A later guidance-only improvement may upgrade the recorder without repeating
accepted observations. Preserve the old PDFs, evidence, checkpoints and helper
provenance, and record the new helper provenance. Prove that the upgrade leaves
the tested behavior, artifacts, generation and probe plan unchanged. A production
or generation change cannot be hidden as a guidance upgrade; identify and rerun
only the checks it actually affects.

The current [environment record](human-test-environment-v1.json) contains only
reader/OS versions and their provenance. All five versions come from the accepted
PWS-0506 tests of **16 October 2026**, recorded in
[the accepted portal matrix](../compatibility/pws-0506-portal-matrix.md). On
**17 October 2026**, the maintainer explicitly confirmed that the same versions
still apply. Those dates use the maintainer's Europe/Berlin calendar. No repeat
numeric-version prompts or reconfirmation are needed for that unchanged setup.

The version-1 JSON has six top-level fields: `schema_version`, `source_task_id`,
`source_evidence_sha256`, `confirmed_at`, `basis`, and `slots`. Each slot records
`reader_version`, `os_version`, `source_attempt_sha256`, and a project-relative
`source_attempt` path. The basis is
`maintainer_confirmed_unchanged_from_last_accepted_test`. It contains no new
observation, copied pass result, account name, username, device serial, or private
absolute path. A helper must validate this bounded record and bind its digest and
applicable source provenance to new observations. Missing provenance remains
missing evidence; it must never be invented.

## Prepare the helper before the human session

Finish and test the helper, its recovery paths and its evidence bindings before
asking for human observations. Freeze the tested artifacts and relevant helper
behavior for that session. Give one stable entry point that works from a known
location and identifies the exact synthetic files. Preparation belongs to the
agent; the person should not need to create directories or assemble a test pack.

Show one plain-language action at a time, followed by the expected outcome. Use
familiar control labels and numbered choices, with keyboard alternatives. Avoid
implementation details in the human steps. Provide explicit **Copy** actions for
fixed search queries and save paths and an **Open folder** action for the fixed
test folder. Do not ask the person to transcribe paths, calculate or compare
hashes, edit JSON, or retain a long sequence of instructions in memory.

Clipboard helpers write only when the person explicitly chooses Copy. Never read
clipboard contents. Where copy/select behavior itself is under test, the person
can inspect synthetic copied text in an unsaved local text view; the recorder
stores the closed observation, without collecting the clipboard or text.

Make visual targets comfortably large, labelled and clearly aligned unless size
or offset is the behavior being tested. Consolidate queries and other repeated
actions when a checked invariant proves equivalent coverage. For the current
redaction corpus, one `PWSRED` search with **whole-word matching off** covers all
canaries only after the helper confirms that every canary has that prefix.
Retain an unsafe positive control and a `PWSKEEP` preservation/search control.
For negative selection/copy checks, search the removal prefix first, then find the
preservation-control matches. Keep that search step free of duplicate copying.
Copy an actual full visible preservation control from the document once,
immediately before each negative-copy step, including when that step is resumed.
This proves positive copying and primes a benign clipboard value. A failed copy
can retain the previous value; priming prevents an old query or unsafe-control
value from causing a false report. Do not rely on earlier clipboard state or
repeat accepted page review merely to restore the benign clipboard.
Simpler queries do not remove required page views, carrier checks, actual viewer
saves, reopen checks, or independent verification of the resulting files.

## Preserve progress and recover locally

Checkpoint each completed human step immediately, with the artifact and
environment bindings. A person may pause between any two steps and resume at the
first unfinished step. Invalid menu input should explain the valid choices and
retry the same step. A missing saved file should offer the save/open-folder step
again and retain earlier observations. Never wipe work, create a false pass, or
restart an accepted reader run to recover a later missing file.

Keep an explicit problem path and a leave-for-later path. Failed observations
remain visible. If artifacts, implementation or environment have materially
changed, preserve the earlier records and identify the affected checks before
requesting further work. Do not silently apply old observations to changed
artifacts. Absent or stale required evidence is unknown or failed, as the task's
contract requires.

## Separate human recording from automated verification

Recording a human step and displaying status must be fast. They should not
repeatedly parse PDFs, render pages, run OCR, or execute the whole automated suite.
Run expensive checks in a separate automated mode after the necessary files
exist. Bind automated proofs to the exact source/candidate files, relevant
processing and probe plan, and engine versions. An unchanged-content proof may be
reused while those bindings remain valid. Validate the current human observation
and environment separately, and bind them to that proof before deriving combined
status. A new metadata record alone need not trigger another PDF run; it cannot
validate an old human observation for changed behavior. Recheck all applicable
bindings before using the result. A fast status may report that automated evidence
is pending; it must not claim a current pass from stale receipts.

The agent performs automated preparation and verification and records the exact
results. The person reports only the observations that require human judgment.
An unrun human step, missing file, failed method or incomplete release gate must
remain explicit. The current
[redaction matrix](../compatibility/pws-0606-redaction-viewer-resave-matrix.md)
still requires both actual saved copies from every named reader and independent
OCR of all applicable saved artifacts. Completing its menu does not enable
permanent redaction or independently approve its release gate.
