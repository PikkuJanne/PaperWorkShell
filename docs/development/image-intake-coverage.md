# PWS-0201 synthetic coverage index

All inputs below are generated from fixed geometry and synthetic values. No applicant
document or downloaded photograph is used. The
[fixture policy](synthetic-fixture-policy.md) and existing golden promotion process
remain controlling. This review adds no committed binary or refreshed golden.

| Cases | Recipe or test owner |
|---|---|
| Baseline/progressive JPEG, orientations 1–8, grayscale and CMYK | `tests/unit/test_pillow_image_decoder.py` |
| RGB, grayscale, indexed, alpha and transparency PNG | `tests/unit/test_pillow_image_decoder.py` |
| Bounded EXIF/text metadata, unsupported ICC/gamma/chromaticity/PNG3 HDR, 16-bit ramp | `tests/unit/test_pillow_image_decoder.py` |
| Truncation, malformed chunks/markers, APNG and multi-frame, bomb/dimensions/pixels/decoded bytes | `tests/unit/test_pillow_image_decoder.py`, `tests/unit/test_image_pages.py` |
| Immutable source mutation, filename/content mismatch, identity and path/handle misuse | `tests/security/test_source_ingest_security.py`, `tests/integration/test_source_ingest_windows.py` |
| Closed exceptions, source/metadata/pixel/log canaries | `tests/security/test_image_page_privacy.py` |
| Reduced-handle worker, deadline/cancel/cleanup/capacity and malformed protocol | `tests/unit/worker/test_image_normalize_operation.py`, `tests/unit/worker/test_image_page_broker.py`, `tests/integration/test_image_normalize_worker_windows.py` |
| Small approved orientation/transform references | Existing `tests/golden/image-transforms-v1/index.json`; unchanged |
| V1 compatibility, v2 mixed-page export, all eight orientation pixel results, alpha, cancellation, render mismatch and aggregate output limits | `tests/unit/test_image_pdf_export.py` |
| Network-denied JPEG/PNG import, reopen and export without OCR resources, seven verification checks and unchanged hashes | `tests/integration/test_image_pdf_export_worker_windows.py` |
| Lazy desktop composition, saved-output recovery, keyboard export, accessible controls, enlarged-text reachability and retry focus | `tests/unit/test_pages_pdf_export_desktop.py`, `tests/unit/test_pdf_export_entrypoint.py`, `tests/accessibility/test_pages_pdf_export_accessibility.py` |
| Quota/disk-full cleanup, source-copy cancellation, timeout and export retry recovery | `tests/unit/worker/test_image_normalize_operation.py`, `tests/unit/test_source_ingest_storage.py`, `tests/unit/worker/test_pdf_export_broker.py`, `tests/unit/test_pdf_export_retry_recovery.py` |
| Real desktop JPEG/PNG import, orientation, save/reopen and plain PDF export | `tools/pws0201_image_smoke.py`; [review evidence](../evidence/pws0201-current-review.md) |

## Desktop recipe

Run `uv run --locked --no-sync python -m tools.pws0201_image_smoke --staged-worker-runtime`.
The explicit staging option uses the existing trusted-copy integration runtime and
all normal worker security checks. Omitting it uses the full development runtime,
whose access setup may delay startup and cancellation. The helper makes
two comfortably large 600 by 800 synthetic targets in an isolated ignored attempt
directory and launches the ordinary application with the constrained policy. It uses
one JPEG stored sideways with EXIF orientation 6 and one PNG with transparent blue
background pixels. Both should display an upward arrow, a red square at top left,
and a white page background. The JPEG filename includes Unicode.

The printed test card gives source and export paths plus an exact resume command.
Preparation is not a pass. Import through the normal file dialog, close/reopen the
case, export through **Export pages as PDF**, and inspect the result. Exercise the
keyboard import/cancel/retry path and effective 200% scaling. Record only the revision,
numeric counts, pass/fail facts, and stable failure codes; keep local paths, filenames,
image bytes, and document-derived values out of committed evidence.
