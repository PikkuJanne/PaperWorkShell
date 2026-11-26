# PWS-0814 — automated Windows setup

## Implementation

The [prepared-artifact setup route](../../packaging/README.md#automated-per-user-setup) uses
the existing per-user Inno Setup installer and its app-local runtime, including the local
OCR components and the reviewed Microsoft runtime terms. The x64 launcher uses the
Windows .NET Framework. The development compiler is used to prepare the bundle.
Recipients run the launcher and installer on their signed-in Windows 11 x64 desktop.

Setup verifies the published receipt digest and the installer and inventory it binds,
checks the actual per-user payload and registration, and runs the exact installed
application entry point. A healthy matching installation can be reused. A missing or
different payload member in an installation with the exact build marker calls for repair.
The native installer controls installation scope, consent, and protection of conflicting
installation folders. Different candidates still use the documented replacement route.

## Prepared artifact

The selected artifact is the existing internal TRACE B8 build, source
`acc0b2621259975c6d418807488ce239b338ac05`. The setup launcher is prepared separately
from its source, so the application's frozen bytes and commissioning evidence remain
intact.

| Item | Exact identity |
| --- | --- |
| Setup launcher SHA-256 | `2cedde20840b579cf03422026e67f88baf2293fbc8a02553ac0f1bbd769725e2` |
| Launcher source SHA-256 | `ec09406320b6476e9f98e847cb966ea2f8fe787436bf8fe0ae5f73a53bc32d94` |
| Installer | `PaperWorkShell-pws0813-trace-engineering-b8-win-x64-UNSIGNED.exe` |
| Installer size | 512,966,979 bytes |
| Installer SHA-256 | `6b2dce059dc2ef2f02e8fc5206e5aacf285af472dfd281d7785881c9c809d33e` |
| Receipt SHA-256 | `145d9efeb0ace1e24e43dd1543a48c0433dac2aad2f764fb4139238c4b4461bc` |
| Resolved inventory SHA-256 | `7a2ee4af857120d18f0bf625ba62cf4dcaaca7c08903b2cf7a00517301dba349` |
| Microsoft terms SHA-256 | `a5cdf1136b18611fc5235c657b7ad113a9ac338abe6c1af3c299205d9492c718` |

## Observed on 2026-11-26

The existing disconnected PWS-Test guest ran Windows 11 Pro x64 build 26300.9711.
The setup executable ran as the standard user in the interactive desktop. PowerShell
execution policy remained `Restricted`. The VM adapter and guest network interface
remained disconnected throughout repair, launch, document processing, and the repeat run.
The host's separate installation and the protected R16 and R18 VM checkpoints were preserved.

- **Repair and launch.** The guest already held exact TRACE B8. After normal application
  close, setup ran the original Inno installer in its supported per-user repair mode
  with the exact Microsoft terms digest. Inno exited 0. Setup verified all 3,210
  payload files, the per-user registration and Start menu shortcuts, then started the
  exact installed launcher and verified a responsive application window using Windows
  UI Automation. The installed marker SHA-256 was
  `c5499f269b524a169848cc27681c1cee019cbfb75f04f6fbe30979a07c1363a3`.
- **Offline document workflow.** On the launched application, UI Automation created a
  fresh synthetic case, imported one local PDF and exported a new ordinary PDF.
  The original and immutable case copy both matched the pre-import SHA-256
  `3f62fe484b8b389ec18afe115939d491f795e517195a7acf7fe5989075839c47`.
  The case's durable export history matched the exported 744-byte PDF with SHA-256
  `e255e4aa2fe57f955cb9096490e31b1801f3a97ac568870991f7a6ad0fb957cf`.
  Poppler independently reopened one page, extracted the expected source text, and
  rendered source and output identically. Strict pikepdf reopening returned no syntax
  findings. Poppler reported the same host font-alias diagnostics on both renders.
- **Repeat and preservation.** Setup rehashed all 3,210 installed files, confirmed
  registration and shortcuts, and verified the running instance. It reported
  `operation=reuse` without running Inno again. All 347 pre-existing case files,
  the new saved case revision, the external input and the export were unchanged.
- **Controlled failures.** An early launcher build refused a wrong receipt digest and
  refused to repair while the exact installed app was running. Neither attempt invoked
  Inno or changed the existing case files. Normal app close allowed the final native
  repair. Focused executable tests on the final source also check changed artifact bytes,
  unsafe inventory paths, terms matching, and refusal to overwrite a result file.

The local prepared bundle is under `output/pws0814/TRACE-B8-setup-bundle`. The
`output/pws0814/proof` directory contains artifact and launch receipts, case preservation
checks, UI Automation transcripts, and the reopened PDF evidence. The corrected GUI
automation used the observed **Export 1-page PDF** control after an initial probe
searched for the empty-case label. The import and repair remained in place for that
continuation.

The focused setup, installer, task-graph, portfolio and documentation test run passed
161 tests and 145 subtests. Registered format, lint, strict typing, schema, task-graph,
documentation, and portfolio boundary checks passed.

Public signing and public-release qualification retain their own criteria and evidence.
This run covers same-build repair and reuse, standard-user launch and an offline ordinary
PDF workflow for TRACE B8. The [task graph](../../TASKS.yaml) records the independent
prerequisites for broader release operations.

## Next step

Begin PWS-0803, authenticated updates. Codex should first inspect the existing install,
repair, and replacement paths and document how an update proves its origin and leaves a
working installation after a failure. Use isolated test signing keys to verify that the
mechanism rejects untrusted updates. The prerequisites for this engineering work are
complete. Production signing is needed for public release qualification.
