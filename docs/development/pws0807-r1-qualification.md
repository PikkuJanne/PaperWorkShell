# PWS-0807 packaged R1 qualification

## Candidate and test boundaries

The selected engineering build is `pws0807-diagnostics-engineering-r1`, version
`0.0.0`, from `e0eaf53142aab4e6ae2e07db1a6c37e050ac24ec`. Its unsigned
installer is 513,022,419 bytes with SHA-256
`a0d809322ea2c29066d26134c649f9ee1a883f4268403ea9c4e2d9e92bf8aaf8`.
The qualified handoff pins the installer receipt, resolved inventory and
installed marker. The [installer record](pws0807-unsigned-completion.md)
describes the authenticated reference, observed signing state, baseline,
guest privileges, true installer disk exhaustion and recovery.

The interactive environment was the offline Windows 11 Pro x64 26300.9711
guest with Smart App Control Off. Staged component checks used the inventoried
R1 payload on Windows 11 Pro x64 26300.9974. Each result below identifies its
entry point. Private evidence is under `output/pws0807/` in the primary
checkout. Only cleared synthetic documents are eligible for the sample.

## Installed desktop and exported pages

The standard user imported the PDF, JPEG and PNG using **Add file**. All
three source and managed SHA-256 values matched before and after the ordinary
export. Independent host parsers reopened the three-page PDF. The first page
matched the PDF source in text and pixels, and the next two matched their
source images. [Mixed input result and hashes](pws0807-unsigned-completion.md#installed-mixed-input-pdf-export)

A second run changed the page order and rotated the JPEG. After a new
application process reopened the case, the saved export plan read PDF, PNG,
JPEG. The host reopened the new 22,413-byte PDF, checked each page position
and confirmed the clockwise quarter-turn against the original JPEG. The PDF
SHA-256 was `138dd9b9cdea25efb2d67cb2a506e9ca447e045f1b5dec4c492e7004bd6190bb`.
A malformed synthetic input produced `SOURCE_UNAVAILABLE` and left all three
pages available. An offline description-only support example records this
incident without including a case, attachment or private path.

The installed case chooser separately opened disposable schema v1, v2 and v3
cases. Each reached schema v4 with its previous manifest preserved exactly.
The order, rotation, managed originals and external source bytes matched
their seeded expectations. The three starting manifests, opening observations
and post-migration comparisons are retained in `r1-completion-ui/`.

## Installed checklist package and timeout

The keyboard-only journey created a new case, imported the four-page
synthetic packet and wrote an ordinary PDF. The keyboard reached the review
and native destination picker. The reopened pages matched the original
four pages in text and rendered pixels. The installed checklist blocked
package export with three confirmed categories and Supporting evidence
missing. After confirming the fourth category the operator approved the
review. R1 wrote a 2,020-byte combined package PDF, SHA-256
`9719fb9c9369b3cc318677302cd74d5d1ecb8a35b1a9076a5a10a62d96350eca`.
All four pages matched. The output size and order agreed with the review.

The first automation attempt to enter an absolute summary path in the
prefilled native picker returned “Summary saving was not confirmed.”
Navigating to the folder and replacing the filename with the keyboard
saved the 6,069-byte text, SHA-256
`8c08d9a76a35a18e1c5bf365ce6aa4d025a38ef38a23ab6f5243f7657c8febb8`.
The entire selectable summary matched its saved text.

**Separate PDFs failed on the installed R1 desktop.** The reviewed plan
had four one-page PDFs. Progress reached two of four during production
and verification. The app reported that the package had exceeded its
configured time limit and offered a fresh review. The observer saw
950-byte and 947-byte temporary files before rollback. The selected
folder was empty afterward, including hidden entries. The previous combined
PDF, summary and ordinary export retained their SHA-256 values.

The source grants 300 seconds to the entire package. To distinguish worker
setup from PDF production and verification, measure these phases on the
exact runtime. Compare the existing per-worker setup with the authenticated
runtime authority already used by ordinary export, retaining the original
limits, security checks and fresh worker processes. A faster instrumented
variant identifies a possible repair. It cannot close the installed failure.
A clean-installed run without a reader or repeated accessibility scans will
test whether the same documented input also times out in that environment.

The identical four-category ZIP review also exceeded the installed time limit
and published no archive. All temporary destination entries were removed.
The separate and ZIP trials share a worker setup path. A host replay of
the exact package reproduced the separate-PDF failure. The three worker
setups spent 173.20 seconds granting access, and retirement took 172.58
seconds. The first two workers returned the verified PDF bytes in 0.547
seconds combined. The entire failed attempt, including rollback, took
367.44 seconds. In a diagnostic comparison the established case-owned
runtime authority was passed to the same flattened-PDF worker factory.
All four production workers and their checks ran, and four correct pages
were independently reopened within a 23.42-second package attempt. The
diagnostic applied to the comparison process only. The R1 installation
and artifact retain their failed separate and ZIP results.

The joined installed result, `r1-completion-ui/installed-result.json`,
has SHA-256 `c9f325248b01d34a026df588c4f2edbaf0c0b0bbcc77b764734bcb509173f9cf`.
The host comparison, `r1-completion-package-perf/comparison.json`, has
SHA-256 `6bbb54d1a112f2153cfb8295eed2ec7bc9853f8d8d155151af6922b26118b88f`.

## Storage, cancellation and resource limits

The exact staged runtime passed 31 migration and storage checks. Four delayed
I/O widget scenarios exercised the visible recovery and responsiveness paths.
The host workload imported the three documents, generated three previews and
exported the same combined PDF with the normal and constrained profiles. The
sampled process-tree working-set maxima were 188,006,400 and 196,669,440
bytes respectively. Native job peak commit was 43,429,888 and 42,938,368
bytes. Both runs finished with zero temporary bytes. Sampled storage and
memory peaks describe the observed workloads. No physical machine minimum
was established.

When the accepted thumbnail worker was interrupted, the caller reported
`PDF_THUMBNAIL_WORKER_FAILED`. A fresh backend reopened the two saved pages
and rendered a 171 × 256 preview. The original and manifest retained their
hashes and the case/cache temporary areas were empty. The constrained
twelve-page mixed OCR workload completed, including signed-source review,
page-boundary cancellation, a correlated limit failure and a successful
fresh retry. Its first 128,179-byte searchable output reopened with twelve
pages. The child report records a 10 ms cancellation acknowledgement. Its
process-tree field is a placeholder. A second run included an independent outer sampler. The canonical validator accepted 1,407 complete samples with a peak of 343,543,808 bytes against the 1,342,177,280-byte limit. Its acknowledgement was 12 ms and its searchable output was 128,181 bytes.

The canonical native vertical workload used the inventory-bound worker bootstrap and qpdf 12.3.2. Its 2,158 actual Qt heartbeats had a maximum gap of 133.68 ms against the 250 ms limit. The two-page exports were identical and the restarted case reopened. Its merged runtime receipt is in r1-completion-runtime/runtime-qualification.json.

A restricted worker could not reach the local listener; positive controls
verified both the listener and permitted connection. An active constrained
worker acknowledged cancellation in 4 ms against a 2,000 ms budget. The
manifest and managed document matched their previous hashes. The next
preview was 171 × 256 and shutdown left zero temporary bytes.
[Profiles and limits](../release/RELEASE_INTERFACE.md#9-resource-behavior)

## Response-time observation plan

The question for this candidate is how soon its desktop visibly acknowledges
an import and displays the correctly oriented synthetic scan. Earlier runs
on other binaries cannot establish these timings on R1. Use one fresh
desktop process for the cold observation and three subsequent empty cases in
the same process for warm observations. The exact foreground native picker
receives one Enter key after the filename has been set. The quiet guest has
no active screen-reader observer during this series. Record guest QPC
timestamps and rectangle images of the status and preview. Compare the first
legible acknowledgement with 250 ms, and the first matching upright scan
with 5,000 ms cold and 2,000 ms warm. A qualifying upper bound can establish
a pass. An upper bound above the threshold alone cannot establish a miss.
Keep every attempt and its observer limitations.

## Reader compatibility and permanent removal

The exact runtime reproduced all seven accepted M5 PDFs and the ZIP
byte-for-byte. This binds the earlier named reader observations to the same
document bytes. Additional host rendering covered Edge 158.0.4370.45,
Chrome 158.0.8127.38 and Firefox 161.0. The newer Adobe build declined the
private-desktop run with Protected Mode enabled. Its earlier accepted
version and current untested version have distinct entries in the receipt.
The minimum-quality attempt refused an impossible one-byte target. R1
regenerated the independently checked one-page permanent-removal derivative,
matched the exact earlier output and verified ten reader copies. A retained
negative control failed verification. The removal profile covers the
qualified single affected PDF page. Its SHA-256 was
`33a6cc1dbfee6fca8d7fe940e5f0ced6e22090e9046fbd9a00481ac0355b8e8c`.
The joined compatibility receipt is
`r1-completion-compat/qualified-compatibility.json`, SHA-256
`993bb7a4c83a8858322987c6d9af2baf5ed0ce518df7d3caa04efc41e6fdcb6e`.

## Launch and support boundaries

For PDF, JPEG, PNG and a proposed workspace argument the native R1 launcher
displayed `LAUNCHER_ARGUMENT_INVALID` and directed the user to the visible
import controls. The staged-host observer read each native refusal and then
closed its test process. The guest separately verified normal Start-menu
launch, in-app import and preservation of the four original file defaults
across removal. The application has no portable project or file association
contract. The exported local PowerShell recipe is a separate documented operation.
The R1-generated script replayed the mixed-source plan and produced the
same 22,413-byte PDF as the installed edited export. A second invocation
refused the occupied name and the previous bytes survived. The local
package requires no sibling product or service.

The inventoried runtime passed 107 diagnostic privacy, restricted bundle
publication, signed-source policy and offline update trust checks. R1 has
no production update anchor. The installed opt-in bundle, cancel, occupied
destination, permission failure, retry and verified archive are recorded in
the [diagnostics continuation](pws0807-unsigned-completion.md#observed-diagnostics-outcome).
Internal reporting uses the maintainer channel that supplied the build.
Public delivery remains a separate owner decision.

The additional input-boundary run passed 275 tests and 217 subtests on the
inventoried runtime. It exercised media admission, image expansion, resource
limits, protected templates, signature-image handling and recipe refusals.
The test harness supplied a matching QtTest library for test-only imports.

## Candidate disposition and sample

**R1 is rejected for PWS-0807 completion.** Its two supported four-category
outputs timed out on the installed standard-user desktop. The independent
host reproduction and paired runtime measurement isolate the repeated
worker-access cost. A clean R1 replay was superseded by this controlled
reproduction and by verification of the successor.

The R1 runtime reproduced the planning limitation from the archived
mixed-image/active-overlay case. It refused the combined plan before
publication. The all-PDF replacement produced 27,060 bytes with the same
rendered pages and text as the historical successful export. The immutable
sources and older manifests kept their hashes.

The frozen R1 sample contains 23 entries and 4,141,874 bytes, SHA-256
`995d3d852d1cf68e2911d685d542eb80a4b1afafd6a83a9fe4fd890aa6776416`.
Its manifest, SHA-256
`95b06fffe394ca1f48840493b5e5017514e1bc351c9fd6447b5b00c8d7b0f676`,
labels the accepted combined desktop file, the two installed failures and
the three-part component references. Independent reopening checked archive
membership, CRCs, hashes, rendered output, rejected input and privacy tokens.
The R1 engineering interface snapshot has SHA-256
`48095295060703bb8da6af675bf1e9a71b1648d5a953d1e6a58e6fbeae239d99`.

The genuine NVDA 2026.1.1.55980 session exercised case/page selection and
rotation, scan and language controls, two overlay kinds, saved removal
intent and geometry, checklist readiness, error recovery, cancellation and
summary. The application said no quality results were applied after
cancellation. The keyboard-only case/import/PDF journey completed. The
exact R1 Qt accessibility suite passed 368 tests. Consent and retention
were inspected on a disposable assisted-case draft.

On the quiet installed desktop one cold import displayed the correct upright
scan by 3,098.94 ms against 5,000 ms. The first captured visible
acknowledgement was bounded at 505.9 ms against 250 ms. That upper bound
was inconclusive for the acknowledgement target. The first warm run lost
foreground provenance during the native picker handoff. Its observation
was retained as invalid. The remaining two warm trials were superseded
by final-candidate verification.
