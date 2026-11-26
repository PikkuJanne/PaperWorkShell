# PWS-0807 — TRACE B8 support ZIP investigation

## Targeted check

Can the exact installed TRACE B8 desktop publish the ZIP it has previewed, and if the save fails, which boundary rejects it? PWS-0806 recorded two failures after consent and a successful PDF export to the first ZIP's parent. The passing Windows source integration test uses a fixed synthetic preview and calls the real writer directly with an absolute pytest path. It bypasses the native save picker, the production session preview, and the dialog's Qt save thread. The corresponding source files and test have no substantive change between TRACE B8 and the documentation worktree.

Run one save through the installed dialog with a fresh synthetic path in the offline guest. Observe the actual picker-to-dialog path properties, production writer entry, and the first sanitized publication error or final state. Keep the installation untouched. A missing, changed, or invalid path would direct the fix to the picker handoff. A valid path followed by a writer failure would direct it to the first failing publication phase. If the check succeeds, retain the earlier failures and report the difference without assigning a cause.

## Evidence and result

- Starting checkout was clean on codex/pws-0806-documentation at
  0c48ba10a107c862d7fb2e094816de927b6f4deb. TRACE B8 was built from
  acc0b2621259975c6d418807488ce239b338ac05. The retained installer rehashed to
  6b2dce059dc2ef2f02e8fc5206e5aacf285af472dfd281d7785881c9c809d33e and
  Authenticode reported NotSigned.
- Both original PWS-0806 receipts show the safe-save message after consent and
  filename submission. The first submitted filename was in the parent of the
  successful PDF. The second was in that parent's Support subfolder. The
  independent enumeration recorded zero support archives and zero unexpected
  outputs. The identical UI result receipts, both SHA-256
  6c0454ebb85f6d053a47430101559295a3e850f03a13d5a4b71eaed8fa9263c5,
  provide the displayed error. They contain neither the path returned by Qt nor
  the underlying error. See [the PWS-0806 walkthrough](pws0806-documentation.md#installed-documentation-walkthroughs).
- The focused [Windows source integration tests](../../tests/integration/test_diagnostic_bundle_windows.py)
  passed all eight cases. They cover exact bytes under both resource profiles,
  refusal to overwrite, failure cleanup, and successive short writes. Their
  fixture preview and direct writer call leave the installed dialog handoff,
  session preview, and save thread outside that coverage.
- The single traced save ran in the normal installed desktop under a genuine
  standard user on the disconnected Windows 11 Pro x64 test guest, build
  26300.9711. The marker SHA-256 was
  c5499f269b524a169848cc27681c1cee019cbfb75f04f6fbe30979a07c1363a3.
  The foreground app was PID 564 in session 5 with elevation 0. The support
  dialog opened from the case chooser, with no case open. The fresh destination
  was a short scratch path under the user's profile. The original saves were
  made with the synthetic case still open and used the original walkthrough
  directory layout. The authenticated guest helper supplied the filename and
  Enter using the same UI Automation value-entry route as the earlier attempt.
- A temporary in-memory trace collected path properties and closed error or
  phase values while the installed code ran. The picker delivered the intended
  absolute .zip path. The save thread, service, and writer ran. Path validation,
  staging, write, flush, readback and rename completed, and the publisher
  returned committed after reopening the final file. The installed dialog
  announced that the bundle was saved locally. No stage remained. The archived
  trace, under output/pws0807/trace.json at the repository root, has SHA-256
  b84441c1ee9a6f7fd39ac39ce350969ad358a1b7cda9ed62819b1bea5d376604.
- The 1,567-byte ZIP matched the preview SHA-256
  7755bcd557a9106ca2a3e775b4eae21326844b2d13325b3418f6046a9e37a29d.
  It reopened and passed CRC checks. It contained exactly summary.txt and
  allowlist.txt. The summary hash matched the preview and the inspected
  summary omitted the test account token, destination marker, source filename,
  and Windows drive path token. The host retains the ZIP, archive verification
  and trace in output/pws0807. The guest support dialog was closed, the owned
  scratch directory was removed, and all 353 case-file hashes matched the
  restored PWS-0806 baseline. The installed marker and active checkpoint parent
  were preserved. The VM was not restored or reinstalled.

The chooser save passed. At that point the original error was still
undetermined. The original receipts showed only DIAGNOSTIC_EXPORT_FAILED,
which can represent failures at several boundaries.

## Reproduction in the original context

The maintainer continued PWS-0807 in this thread. The next check asks whether
the same installed save fails after the original open-case workflow and at
the original destination. Recreate the synthetic case, imported fixture,
occupied-name PDF refusal and successful PDF publication, then submit one
traced ZIP save in the same parent. This holds the original user context and
destination constant. A first sanitized error identifies the boundary to fix
and the specific before-fix test. An exact reviewed ZIP establishes that the
failure does not repeat in the recreated context and leaves the historical
cause unknown.

### Picker handoff comparison

The exact open-case reproduction returned an absolute ZIP path with nine
components. The requested path had five. The trace stopped before publication
because the returned path was outside the authorized destination. The next
finite comparison checks whether the native dialog retained its initial
filename in the installed working directory after the filename field's
accessibility value changed. The first arm observes the path and parent
write capability without creating a file. The second changes only the
initial picker argument to an existing home directory with an empty filename.
It submits the same accessibility interaction and publishes only if the
returned path matches the requested path. A matching path and exact ZIP on
the second arm support removing the prefilled filename. A different result
requires a different fix or leaves the cause unresolved.

## Cause and source fix

The reproduction opened a fresh synthetic case, imported the same PDF,
observed PDF_EXPORT_DESTINATION_EXISTS at the occupied name, then exported a
PDF matching the original verified 744-byte derivative. With that case open,
the native support save picker retained its prefilled
`PaperWorkShell-support.zip` in the application working directory, the installed
runtime folder. The requested path had been supplied through the filename
field's Windows UI Automation ValuePattern. A read-only directory open
requesting add-file access there returned Win32 error 32, a sharing violation.
The trace stopped before any attempt to publish into the runtime folder. The
two PWS-0806 errors remain historical failures. Their receipts contain no
underlying Windows error number.

With the picker starting in the user's home directory and the filename left
empty, the same accessibility interaction returned the intended path. The
save thread and Windows publisher committed the ZIP, the application announced
local save, and no stage or runtime ZIP remained. The 1,567-byte archive
reopened with valid CRCs and exactly `summary.txt` and `allowlist.txt`. The full
ZIP SHA-256 matched the unchanged preview,
`3c965ba9e2ba462cfa5105c47e50dbd9ad1cac2a3e04fc26c46d405edfa6db6a`.
The allowlist matched the checked-in fixture and the summary contained none
of the checked synthetic path, account, case or filename tokens.

The in-memory comparison used the exact installed B8 runtime and changed only
the initial argument of the picker on its second arm. The immutable build
remains the historical candidate. Under `output/pws0807/original-context`, the
before receipt SHA-256 is
`a3dc9258ec2927101f8dca3a4ee1640098f2e304951ade5f13fcfb9a4882ed13` and the after
receipt SHA-256 is
`eca59371a844fe01710f6e255b3feb3a937e972266bc217cd9e122e4d5d666fb`.

The [source change](../../src/paperworkshell/ui/diagnostic_bundle.py) gives the
native dialog the user's home directory as its initial location. The
[regression](../../tests/unit/test_diagnostic_bundle_dialog.py) verifies that
the argument is a directory, the picker still leaves overwrite decisions to
the publisher, and the consented save hands the selected path and exact
preview to the writer.
It failed on the old prefilled filename. After the change, all 37 focused
diagnostics dialog, archive, Windows publication, privacy and accessibility
tests passed, including the existing no-overwrite and cleanup cases.

## Next step

On the signed candidate built with this fix, exercise the same filename entry
through the native picker with a synthetic case open. Verify the matching ZIP,
no overwrite, cancellation and safe storage failure under the standard-user
account. The [release-candidate matrix](pws0807-rc-matrix.md) records the
remaining candidate and environment requirements.
