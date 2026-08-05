# Bounded PDF-thumbnail service contract

- Status: PWS-0108 implementation contract
- Task: PWS-0108
- Decisions: PWS-D012, PWS-D013, PWS-D027, PWS-D028
- Renderer: [PDF page-rendering contract](pdf-page-rendering.md)
- Worker: [Restricted worker lifecycle contract](worker-lifecycle.md)
- RPC: [Worker RPC protocol v1](../schemas/worker-rpc-v1.md)
- Storage: [Secure case-workspace contract](case-workspace.md)
- Security flow: [DF-PREVIEW](../security/data-flows.md#df-preview--render-bounded-thumbnailspreviews)

## Scope and ownership

PWS-0108 turns the PWS-0106 worker-local page renderer into a bounded, case-scoped thumbnail
service. It owns the pure request, cache-key, artifact, snapshot, progress, result, and sanitized
error contracts; toolkit-free batch orchestration; one-page worker RPC operation; exact source and
cache-stage brokerage; authenticated cache lookup; atomic publication; invalidation; and stale-stage
cleanup.

The service produces review previews only. It does not edit a source, mutate a case manifest,
claim export fidelity, prove that dynamic form content ran, provide arbitrary image decoding, or
persist UI state. The authoritative page/source relationship remains in the current case manifest,
and the immutable PDF remains under the source-ingest boundary. Every cache entry is disposable
and reproducible from those authoritative inputs.

The package direction remains:

```text
UI -> app.pdf_thumbnails -> domain.pdf_thumbnails
                         -> structural one-page generator port

trusted composition -> storage committed-page/cache ports
                    -> worker client/broker

worker host -> pdf renderer adapter
```

`domain` and `app` import no Windows, storage, worker-host, pikepdf, or pypdfium2 implementation.
`storage` imports neither application orchestration nor a worker implementation. The renderer
adapter imports neither storage nor worker code. Trusted composition supplies the concrete ports.

## One page per worker operation

An application batch may request several distinct internal page IDs from one case, but generation
is deliberately serial and page-at-a-time:

1. Validate one non-empty tuple with a single case ID, unique page IDs, bounded dimensions,
   quarter-turn rotations, and a trusted monotonic batch deadline.
2. Resolve and authenticate one page against the current manifest and exact committed PDF.
3. Derive its complete cache key and attempt one authenticated cache lookup.
4. On a miss or removed corrupt entry, allocate one operation-scoped cache stage and launch a fresh
   restricted worker generation for exactly that page.
5. Confirm the complete worker tree ended before validating or publishing its bytes.
6. Return one fixed-layout snapshot, increment the completed-page count, then advance to the next
   request.

This design bounds each untrusted render independently and gives cancellation a clear boundary.
Successfully validated entries from earlier pages may remain reusable after a later page is
cancelled. Bytes from the active incomplete page never become a cache hit. Version 1 does not
coalesce concurrent callers or render pages concurrently; a matching valid no-replace publication
collision may be reused after full validation.

## Current-source authentication

A cache key is not source authority. Before either a hit or a render, the trusted storage broker
borrows a committed PDF page through its internal case and page UUIDs. While retaining authenticated
case and manifest authority, it:

- parses the current supported manifest and resolves the page to one PDF source and zero-based
  source-page index;
- opens the exact random internal original beneath the authenticated `originals/` directory;
- verifies its regular-file identity, containment, logical size, single-link/non-sparse state,
  private parent state, and absence of alternate streams;
- hashes exactly the recorded byte count, proves EOF and unchanged identity/size, and compares the
  SHA-256 with the manifest; and
- rewinds and retains that same read handle until the worker generation has ended.

Consequently, a cache entry cannot hide a missing, replaced, modified, remapped, or non-PDF source.
Source authentication happens before lookup even when the expected cache filename already exists.
The cache header repeats the source identity facts for correlation, but never replaces this fresh
manifest-and-source check.

## Cache-key and profile versioning

`PdfThumbnailCacheKey` has one fixed-width canonical version-1 encoding. Its digest covers:

- cache-key and artifact versions;
- internal case, source, and page UUIDs;
- zero-based source-page index;
- immutable source byte size and SHA-256;
- requested maximum width and height;
- requested additional rotation;
- fixed CropBox, RGBA8, opaque-white, and stored-annotation-appearance profile values; and
- an opaque renderer-profile SHA-256.

The renderer-profile digest covers every selected implementation choice that can alter pixels,
including pypdfium2 5.12.1, PDFium 152.0.7947.0, CropBox fitting, RGBA8 channel order, white
compositing, stored annotation appearances, and disabled form/XFA behavior. A renderer, engine,
artifact, key, or fidelity-profile change therefore selects another cache key; it does not silently
reinterpret old bytes. Unknown or future key/artifact versions fail closed.

The final storage component is exactly:

```text
<page-uuid>.<full-cache-key-sha256>.pwsthumb
```

Both values are random internal identifiers or their digest. Original/display filenames, paths,
document metadata, and page text are absent. A staging component is a distinct random
`<cache-object-uuid>.tmp`. Neither component is accepted from a user or worker, and neither is a
filesystem authorization token.

## Worker RPC and handle brokerage

The additive protocol-v1 capability is `pdf.thumbnail` version 1. The client must request that
exact capability, and the worker reports only capabilities requested by that peer. The operation
uses the existing v1 envelope, `processing` progress stage, resource limits, four public RPC error
codes, and terminal semantics unchanged.

One start contains exactly one `source_read` reference with content kind `pdf`, immutable source
UUID, expected byte size, and expected SHA-256. Its closed parameter set is:

| Parameter | Version-1 rule |
|---|---|
| `source_handle` | distinct positive worker-local integer for the exact read-only PDF handle |
| `cache_handle` | distinct positive worker-local integer for the exact write-only cache stage |
| `cache_object_id` | random UUIDv4 bound to that stage |
| `page_id` | current manifest page UUIDv4 |
| `source_page_index` | non-negative zero-based index |
| `maximum_width_pixels` | positive integer within policy |
| `maximum_height_pixels` | positive integer within policy |
| `page_box` | closed token `crop` for product thumbnails; `media` remains an explicit adapter/test profile |
| `additional_rotation` | integer 0, 90, 180, or 270 clockwise |

The source, page, cache-object, and operation UUIDs are pairwise distinct. The requested width by
height must fit the RPC pixel budget before dispatch. Missing, extra, mistyped, aliased, nonpositive,
or out-of-policy values fail before the handler runs.

After ready, the trusted Windows broker duplicates only these exact non-inheritable rights:

- source: file read-data/read-attributes plus synchronization; and
- cache stage: file write-data plus synchronization.

The worker receives no case, originals, cache-directory, manifest, journal, path, filename, delete,
rename, create-child, read-cache, or general directory authority. Handle integers are useful only
inside that target process and are never remotely closed by number.

## Worker-side generation

The host adopts and verifies both brokered handles and the complete typed operation binding. It
copies and hashes exactly the expected source bytes into bounded worker-private storage, proves EOF,
and closes the case-source handle before native parsing. The PWS-0106 adapter then renders the one
resolved page into canonical raw RGBA8 under the operation's page, pixel, memory, temporary-storage,
output, CPU, and wall-time limits.

The worker writes one fixed artifact header followed immediately by the renderer's raw rows through
the exact cache-stage handle. It flushes and closes that writer before reporting success. The
successful `operation.result` contains exactly one `operation_staging` reference with:

- the requested cache-object UUID;
- the envelope operation UUID;
- content kind `pdf-thumbnail-rgba`;
- the exact complete artifact byte count; and
- SHA-256 of the complete header-plus-payload artifact.

The RPC carries no raster bytes. Cancelled, failed, or timed-out results carry no output reference.
RPC success alone never makes the cache bytes trusted.

## Fixed raw RGBA artifact

The cache artifact is not PNG, JPEG, PDF, or a renderer-native bitmap. It is a versioned,
fixed-layout binary record whose header contains:

- magic, artifact version, exact header length, and zeroed reserved fields;
- operation and cache-object UUIDs;
- the complete canonical cache key and its SHA-256;
- width, height, exact `width * 4` stride, and payload length;
- intrinsic and requested quarter-turns;
- fixed CropBox, RGBA8, opaque-white, and stored-appearance codes;
- renderer-result version; and
- SHA-256 of the raw pixel payload.

The payload is exactly `height * width * 4` bytes, with left-to-right pixels and top-to-bottom rows.
Every pixel is red, green, blue, alpha and every alpha byte is 255. There is no padding, encoded
image stream, metadata segment, color-profile claim, filename, path, text, or variable extension
area. The complete artifact length must equal header length plus declared payload length and remain
within the requested/output/cache limits.

Before pixels can reach a UI-facing `PdfThumbnailSnapshot`, the trusted side validates the opened
file identity and stable size, whole-artifact RPC digest, magic/version/reserved fields, operation,
cache-object, case/source/page/key correlations, key digest, dimensions, stride, rotations, profile,
exact length, pixel digest, and opaque alpha. The snapshot contains only internal case/page IDs,
bounded layout metadata, rotations/profile, and the validated RGBA8 bytes. Pixel fields and digests
are excluded from ordinary object representations.

## Cache staging and atomic publication

Storage reserves the maximum possible header-plus-requested-pixels capacity against the case cache
quota before it creates a stage. It retains the authenticated case, thumbnail-directory handle,
exact pending-file handle, and publication coordination through validation and final disposition.
The stage is regular, single-link, non-sparse, bounded, randomly named, and opened without granting
the worker a directory capability.

After confirmed worker termination, the trusted broker seals the exact stage and reads it back
through retained authority. Publication requires all artifact checks plus agreement with the
terminal output reference. Storage then renames that exact opened stage to the derived final name
using no-replace semantics while holding its publication guard.

- A new successful rename publishes one complete entry atomically.
- If the final name exists, storage opens and fully validates that exact entry. A valid equivalent
  entry wins and the redundant trusted stage is discarded.
- A corrupt final entry is removed through its authenticated handle and publication is retried.
- An indeterminate rename outcome or identity mismatch is failure; the stage is quarantined rather
  than guessed into either published or disposable state.

The manifest is never part of this commit. A process interruption can lose a cache entry without
losing case intent, and a partial stage is never accepted under a final cache name.

## Cancellation, termination, and quarantine

Application cancellation and the shared monotonic deadline are checked before and after page
resolution, lookup, launch, worker pumping, rendering checkpoints, result handling, and publication.
The adapter's probes surround synchronous PDFium work, while the retained Job Object supplies the
hard boundary when native work does not cooperate.

Every generation used to produce a thumbnail is one-shot. On success, cancellation, timeout,
protocol failure, native crash, or broker failure, the broker requests or forces complete Job-tree
termination and requires positive termination confirmation before it trusts output or releases the
source authority. A returned result, exited primary process, closed pipe, or reusable process-local
handle number is not that confirmation.

If whole-tree termination cannot be confirmed, the broker must not parse, publish, delete, or reuse
the stage. It marks the random stage quarantined, preserves it as non-authoritative, and keeps the
worker client in its sticky cleanup-failure state so another generation cannot be mistaken for a
clean continuation. Later bounded recovery may remove such a stage only after no live worker or
broker authority can still write it. It never promotes an abandoned stage.

If termination is confirmed, a cancelled/failed/invalid partial stage is deleted only through its
still-authenticated exact identity. If deletion cannot be proven, the random stage remains an
unreferenced cache orphan; it is not a final entry and cannot corrupt the manifest.

## Progress and UI handoff

The application service exposes aggregate, content-free phases `lookup`, `rendering`, `validating`,
and `complete`. Each record contains a fresh internal batch operation UUID, phase, completed-page
count, and total-page count. A page increments the count only after a validated hit or generated
snapshot is available. The terminal `complete` phase requires completed pages to equal total pages.

The underlying RPC continues to use only protocol v1's `processing` stage; broker-side phases do
not extend the released wire schema. Progress callbacks are bounded by the same cancellation and
deadline policy as work. They contain no path, filename, page text, metadata, digest, raster, or
worker/library message. PWS-0111 owns accessible widget announcements, focus, retry, and cancellation
controls; this service supplies the typed state required for that integration.

## Limits and failure behavior

Limits are cumulative rather than interchangeable:

- a batch is non-empty, contains at most the product page limit, and has unique page IDs;
- width, height, their overflow-safe product, stride, payload, and complete artifact are each
  bounded before allocation or publication;
- current manifest page count, immutable source size, worker-private copy, render bitmap, memory,
  temporary storage, output bytes, CPU, and wall time have independent caps;
- cache staging reserves its worst-case byte count and one entry before creation; and
- lookup, page invalidation, and stale-stage cleanup use bounded inventories and authenticated
  exact-entry deletion.

Failures map to closed `PDF_THUMBNAIL_*` codes and trusted plain-language messages. Raw PDFium,
pypdfium2, pikepdf, Windows, filesystem, or worker exception text never crosses the boundary. A
malformed, encrypted, changed, out-of-range, oversized, corrupt, cancelled, timed-out, or crashed
operation yields no new trusted snapshot. There is no repair attempt, password guess, alternate
renderer retry, weaker-limit retry, or source/manifest mutation.

## Invalidation and cleanup

A cache entry becomes unreachable automatically when any canonical key input changes. Storage also
provides authenticated operations to:

- invalidate one exact key;
- remove every syntactically valid final thumbnail for one internal page UUID within a bounded
  inventory;
- delete an invalid exact cache entry discovered during lookup; and
- remove bounded random `.tmp` stages after the owning operation authority is known to be gone.

Deletion reopens each exact regular file, checks identity, root, single-link/non-sparse state,
stable size, and alternate streams, and reports only content-free counts and complete/partial state.
Unknown names and unsafe entries are not broadly deleted. Case removal and general disposable-cache
cleanup continue to operate at the authenticated workspace boundary. Version 1 does not perform an
implicit broad least-recently-used eviction; quota exhaustion reports a stable limit failure so the
owning lifecycle can choose an explicit cleanup policy.

## Privacy and diagnostics

Routine logs and metrics may contain internal case/source/page/operation/cache UUIDs, profile and
artifact versions, numeric dimensions/counts, cache hit/generated/rebuilt status, duration/resource
counters, and stable outcome/error codes. They must not contain:

- original or cache paths and filenames;
- source hashes, cache-key hashes, or pixel hashes;
- PDF/document text, metadata, form values, or rendered strings;
- pixel bytes, encoded thumbnails, screenshots, or sampled colors;
- raw RPC payloads, handle values, native error state, exceptions, or stack values; or
- user/account/machine identifiers.

Tests use only synthetic sources and scan errors, representations, progress, logs, and diagnostics
for prohibited canaries. A diagnostic feature may not include cached thumbnail bytes unless a later
task explicitly adds a previewed, opt-in inventory under PWS-D039.

## Verification contract

PWS-0108 evidence covers:

- multi-page serial service progress, cache hits, rotated pages, malformed/encrypted files,
  excessive geometry/pixels/storage, cancellation, timeout, and native/worker fault;
- fixed key/header/artifact round trips plus future/invalid versions, reserved fields, wrong IDs,
  aliasing, truncation, surplus data, stride/length/profile mismatch, digest mismatch, and
  non-opaque pixels;
- current-manifest page resolution and exact source identity/size/SHA checks on hit and miss;
- reduced-right source/stage handles, denied path/sibling/case access, one-shot termination, and
  unconfirmed-termination quarantine;
- no-replace publication, valid collision reuse, corrupt-entry removal/rebuild, indeterminate
  publication, exact-key/page invalidation, bounded stale-stage cleanup, quota failure, and partial
  cleanup reporting; and
- prohibited-token scans proving routine output contains no path, filename, text, metadata, digest,
  pixel, raw exception, or image content.

Synthetic render output is inspected under the PWS-0106 golden policy. Cache files are generated
test artifacts, not golden sources, and are cleaned from test case workspaces after each run.
