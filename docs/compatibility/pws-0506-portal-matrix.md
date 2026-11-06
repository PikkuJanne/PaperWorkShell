# PWS-0506 conservative portal compatibility matrix

## Scope and current result

**Manual matrix: PASS, accepted 2026-10-16.** All five human-operated slots passed
the complete procedure against the exact PWS-0506 outputs. Codex validated the
saved attempts and their common artifact binding after the maintainer confirmed
completion. The accepted [PWS-0307 matrix](pws-0307-overlay-reader-matrix.md)
remains valid for its own frozen artifact; it is not repeated or relabelled here.

The [conservative portal profile](../architecture/portal-export-profile.md) defines
the output feature policy. This matrix tests desktop/browser readers and one
representative Android reader. It does not claim universal reader compatibility,
portal acceptance, PDF/A, PDF/UA, reading order, legal validity, or digital signing.
Android is an output-reader observation, not support for running PaperWorkShell on
Android. No macOS reader or packaging claim is introduced.

## One stable entry point

In PowerShell, paste this complete single line from any directory:

```powershell
uv --directory 'C:\projects\PaperWorkShell' run --locked python tools/dev.py pws0506-compatibility-test
```

The helper creates and verifies its own ignored workspace. After
`READY [PWS0506_COMPATIBILITY_READY]`, use the one printed directory. Do not invent
paths, create folders, calculate hashes, edit JSON, or overwrite an existing corpus.
The menu offers W1, W2, W3, W4 and A1; `S` shows status and `Q` stops. Rerunning
preserves exact artifacts and previous attempts. A later failure remains visible
even if an earlier attempt passed.

Preparation uses the production native adapters on generated synthetic material,
with the central constrained resource policy, pinned qpdf, strict reopen and
all-page PDFium rendering. It neither takes user-document arguments nor launches a
network server. Ordinary product document parsing remains in restricted workers.
The developer harness is not a product import or export interface.

| Symptom | Recovery |
|---|---|
| PowerShell waits at `>>` | Press `Ctrl+C`, then paste the complete single line without a prompt, continuation characters, or Markdown fences. |
| `uv` is unavailable | Follow the pinned toolchain setup in the repository README and rerun the same command. |
| No `READY` appears, or an `ERROR` code appears | Stop; retain the workspace and report only the bracketed code. Codex inspects the failure. Do not delete or replace files to force a pass. |
| A browser downloads or delegates the file | Select its ordinary built-in PDF viewer and restart that slot. A delegated Adobe extension is not that browser's result. |
| Required viewer or Android device unavailable | Leave its slot `NOT RUN` and report the missing capability. Absence is not a pass. |
| Interrupted during a reader run | Rerun and select only the unfinished slot. Existing completed attempts remain. |

Maintainers may run `python -m tools.pws0506_portal_compatibility prepare` or
`status` in the locked environment for noninteractive preparation/status. The
default command provides the guided recording loop.

## Corpus and test card

The source recipe creates three comfortably sized Letter pages. Searchable text
is embedded through the approved overlay font strategy. The signature mark is
invented raster geometry, not a real signature. No personal documents, names,
addresses or account data are used.

| Page | Visible targets |
|---:|---|
| 1 | Large heading; Café/Ångström/naïve text; date; PWS initials; blue check; red cross; transparent synthetic signature-image mark. |
| 2 | Intrinsic 180-degree rotation, symmetric crop, a large centered translucent ellipse over crossed axes, and a highlight within the visible cropped page. |
| 3 | Large scan with a reference number, amount, smaller print, labelled colored boxes, invented ink and DEMO stamp, plus a blue vector border. |

The output directory contains:

- `portal-baseline.pdf`: flattened conservative-profile output.
- `portal-preserve.pdf`: explicit preserve-color 240 dpi/JPEG85 image stage, then
  conservative-profile re-verification.
- `portal-grayscale.pdf`: explicit grayscale 240 dpi/JPEG85 image stage, then
  conservative-profile re-verification. Raster color is removed; vector and
  overlay color remains. Both image policies exceed the existing 200 dpi/JPEG80 floor.
- `portal-part-1.pdf` through `portal-part-3.pdf`: verified separate page outputs.
- `portal-parts.zip`: the three exact separate PDFs with fixed archive metadata.
- `evidence.json`: exact byte counts/hashes, source provenance, compression facts,
  and local constraint-test results. Initial manual slots remain `NOT RUN`.

For each named reader:

1. Read the displayed test card. Confirm the reader configuration below. Enter
   only its numeric version from About; never enter account, device, or profile names.
2. Open the baseline PDF locally. Confirm three pages and navigate `1 > 2 > 3 > 2 > 1`.
3. Inspect every baseline page at Fit Page and 200%. On Android, use fit and pinch enlargement
   sufficient to read the smaller-print target. Confirm presence, placement, crop
   containment, orientation, transparency, signature aspect, and legibility of the
   labelled synthetic targets. Color loss in the grayscale raster is expected.
4. Search the baseline for `Café`, `2026-10-01`, and `PWS`; all three must
   be found. This is a limited font/text observation, not a reading-order claim.
5. Click or tap all overlay regions. No document field, script, external action,
   attachment or editor may activate. Viewer controls and optional reader tools
   are not document interactivity.
6. Close and reopen the baseline in the same reader and revisit all pages. Then open
   the preserve and grayscale PDFs, confirm three pages, and inspect their changed
   page 3 at Fit Page and enlargement. Close/reopen each and revisit page 3. Native
   stage verification proves semantic preservation outside selected image assets
   and exact rendered-pixel equality on unchanged pages 1 and 2; do not repeat their
   complete checks for the compressed variants.
7. The separate PDFs are checked automatically against the exact baseline pages;
   ZIP members are byte-identical to those parts. No duplicate human page review
   is required for them. The automated matrix covers all output organizations;
   the manual matrix covers the baseline and both changed scan variants.
8. Answer the six closed yes/no groups. Each answer covers all files described by
   that group. Any failed group derives `FAIL`; missing observations stay pending.

For Android, transfer only these synthetic files through an ordinary local method
such as USB. Keep names and bytes unchanged. Do not use personal documents or a
cloud share. The desktop inventory binds the transferred source files; if a transfer
changes bytes, recopy the originals before testing. Record the numeric Android
version when prompted; no device serial, account or local path belongs in evidence.

## Required matrix

| Slot | Confirmed configuration | Reader version | OS version | Tested at UTC | Result |
|---|---|---|---|---|---|
| W1 | Adobe desktop in Reader/free/reduced mode | 2026.002 | Windows 10.0.26300 | 2026-10-16 12:09:08Z | PASS |
| W2 | Microsoft Edge built-in PDF viewer | 155.0.4283.45 | Windows 10.0.26300 | 2026-10-16 12:10:33Z | PASS |
| W3 | Google Chrome built-in PDF viewer | 155.0.8059.80 | Windows 10.0.26300 | 2026-10-16 12:11:50Z | PASS |
| W4 | Mozilla Firefox built-in PDF viewer | 157.0.1 | Windows 10.0.26300 | 2026-10-16 12:17:43Z | PASS |
| A1 | Adobe Acrobat Reader for Android | 26.9.2.50367 | Android 16 | 2026-10-16 12:22:21Z | PASS |

Each slot has one saved version-1 attempt with operator `human`. Every attempt
records all six closed checks as true: correct viewer, all pages and order, all
visual targets and zooms, all three text searches, no document interactivity,
and close/reopen. These checks cover the baseline and both changed scan variants
according to the test card above; separate pages and ZIP members retain their
automated exact-byte comparisons.

All attempts bind evidence SHA-256
`b046984e5ddee6a30704932cf9220a884e8d1b2343ee599541b793b9ee0aef52`
and the seven pinned artifacts. The original `evidence.json` preparation
snapshot keeps its initial `NOT RUN` fields; the separate attempts supply the
observed results. Neither the snapshot nor the artifacts was rewritten.

| Saved attempt | SHA-256 |
|---|---|
| `W1-attempt-01.json` | `114b83241d83c0a5c836b2dfccd1631debb97dd3270cf8baba40e3b4bf024c9a` |
| `W2-attempt-01.json` | `81dd1e4e586dddc53fb8167d4935cb226262fa7af3e9fee33827852dd0785e79` |
| `W3-attempt-01.json` | `162f51048ef69e394e36e6aafb539a49f56bee7c9fd4d940cd6d259957bc4949` |
| `W4-attempt-01.json` | `4066e1481f750f789029b8559276132c9b915ae1a02c950b86773818b3510c06` |
| `A1-attempt-01.json` | `173c0986bbbcfd1f9c6da4cc4553b73453808dc402cb03b54313a8e77129ecab` |

`uv run --locked --no-sync python -m tools.pws0506_portal_compatibility status`
verified current implementation provenance, all seven pinned artifacts, all
22 local constraint checks, and the latest attempt for every slot on 2026-10-16.
It reported five `PASS` results and `MATRIX READY FOR REVIEW`; this tracked report
records acceptance of those human observations.

W1 excludes paid Standard/Pro and mode-not-confirmed observations. W2-W4 exclude
external reader extensions, downloads without display, and delegation. A1 names
one representative Android reader; another app does not silently replace it.

The matrix is ready for review only when the latest attempt for every slot passes
and all attempts bind the same evidence SHA-256. The helper stores exclusive,
bounded, closed JSON attempts. Guided entries identify a human operator; observations
actually performed through computer use may identify a Codex computer-use operator.
Codex reviews them before updating this tracked
report. It never derives a reader pass from automated rendering.

## Representative upload constraints

The helper runs 22 local tests against exact generated artifact sizes through the
existing pure portal-constraint evaluator. These are **local constraint tests**;
no HTTP request or real portal submission occurs.

| Organization or rule | Required observation |
|---|---|
| Combined PDF | Actual byte/page equality passes; lowering total/per-file limits by one byte or page count by one page fails. |
| Separate PDFs | Actual summed total, per-file maximum and file count pass; one-byte lower total/per-file limits or one fewer permitted file fails. |
| ZIP | Actual outer archive bytes including overhead and member bounds pass; one-byte lower limits fail; CRC, member order and member hashes match the verified separate PDFs. |
| Every organization | Disallowed mode, wrong extension and an explicitly encrypted observation produce their stable blocking codes. |
| Names | Traversal is rejected by the same production filename validator. |

Negative encryption and extension rows intentionally change the supplied closed
observation, not the verified PDFs. Encryption and native feature rejection are
also covered by the profile's native tests. A clean result means only that the
configured checks found no violation; it does not mean a service accepted a file.

### Optional permitted remote observation

A real upload test may be added only for a destination that explicitly permits
synthetic test uploads. Use its test/sandbox route, follow its current instructions,
and use only this corpus. Record the public service/test-environment identifier,
date, advertised constraints, artifact SHA-256, browser/version and concrete
observed accept/reject/error state. Record no credentials, account identifiers,
private URL tokens or document content. Stop before any real application submission.
Remove the uploaded test material when the service's supported workflow permits.
No suitable permitted destination has been established here; remote upload status
is **NOT RUN**. Local results must not be relabelled as remote upload results.

## Automated artifact evidence

The native generator completed twice with the same seven artifact hashes. Each
PDF passed its production adapter's strict reopen, qpdf and PDFium checks. Both
explicit image policies re-encoded the scan; pages 1 and 2 passed the stage's
unchanged-page render comparison. Separate outputs were compared with baseline
pages, and the ZIP contains their exact bytes. All 22 local constraint checks
passed. No manual reader pass follows from these automated results.

| Three-page artifact | Bytes | SHA-256 |
|---|---:|---|
| Baseline | 1,102,218 | `47dfe39762bebdf7173160e032f1fa9400d8bd96231954652e6e275a978757f0` |
| Preserve 240 dpi | 362,313 | `8cf1b7523052345594e773b62b228e03db9cb35ac944122f1b1663b79c12ecd9` |
| Grayscale 240 dpi | 301,124 | `72597065cfd6cfc6485d87fba2014576f9e9e1f60e71d1596689f0830063f042` |

The generator pins all seven expected artifact hashes; a self-consistent changed
PDF/evidence pair is rejected. The exact implementation/evidence binding for human
checks was prepared on 2026-10-02 at source revision
`ce4868cf3bbc0f86d524420324c02fa3f5ecfc47`. The workspace is
`output/pws0506-portal-compatibility/a3741d06357b/`, with evidence SHA-256
`b046984e5ddee6a30704932cf9220a884e8d1b2343ee599541b793b9ee0aef52`.
All seven pinned hashes and 22 local checks matched. Source changes require fresh
preparation before observations are recorded. The guided command verifies the current
implementation binding before it records an attempt.

## Provenance, limitations and portfolio boundary

The helper binds a base Git revision plus a SHA-256 over actual product Python
sources, the corpus producer, its reused synthetic helpers, and the locked engine
inventory. This explicitly covers an uncommitted implementation; it does not call
the working tree a frozen release. A changed implementation uses a different
workspace and invalidates reuse of stale evidence. Existing files are preserved.

The matrix covers the named versions, outputs and observations only. It does not
certify all Android readers, portal limits, scripts, XFA, external tools, hardware,
PDF conformance or future releases. Smaller text and grayscale loss still require
human review. All output is local, product-owned and useful without ThirdLife.
No sibling repository, service, adapter, shared schema or release synchronization
is introduced. Unproved packaged-release facts remain `TBD` in release documents.
