# ADR 0017: Freeze a resolved Windows dependency stage before installer assembly

- Status: Accepted implementation choice within PWS-0801
- Date: 2026-11-03
- Decisions preserved: PWS-D004, PWS-D005, PWS-D012, PWS-D013, PWS-D016, PWS-D034,
  PWS-D036, PWS-D041, PWS-D042

## Context

The reviewed dependency inventory v1 is a policy baseline. It covers development admission and
the Python resolution, not the precise DLLs, static libraries, source obligations or files in a
Windows package. Copying an installed development environment would also bring evaluated Qt
Addons into the product. A source-tree SBOM cannot prove what a later installer contains.

PWS-0801 precedes PWS-0802, which chooses the final application identity, product terms,
installer and signing process. Establish a real internal dependency staging candidate now and
make the same inventory/verification procedure reusable for later exact candidates. This does
not turn an internal `0.0.0` stage into a public release or a supported portable installation.

## Selected boundaries

- Keep policy inventory v1 and its historical evidence intact. Use a new, closed
  `resolved-dependency-inventory-v1` contract for exact artifacts, components, files, notices,
  source/replacement obligations and source/review provenance.
- Stage from authenticated explicit wheel/archive members and local source-build outputs. Do not
  copy a machine environment or use ambient sources as binary provenance. Every included file
  has one owner and SHA-256/SHA-1 identities; every missing, changed, extra or aliased file blocks
  verification. SHA-1 is used only for SPDX's specified package verification code.
- Keep document processing offline and in the restricted worker. Build-time acquisition is
  separate and explicit; normal validation and tests do not fetch dependencies.
- Use a dynamic QtCore/QtGui/QtWidgets subset with the Windows platform/style plugins and
  corresponding PySide/Qt sources and notices. Exclude Addons, Qt PDF reader, QML, Quick,
  Network and unrelated plugins. Keep replacement/relinking possible through supplied sources
  and a recipient-owned rebuild; no reverse-engineering restriction is added to LGPL components.
- Exclude only the two reviewed lxml resources from both executable staging and its required
  source subset. The maintainer approved the exact proposal on 2026-11-03. The source's LF and
  wheel's CRLF forms are separately hashed and their normalized equivalence is proved. This
  grants no rights to the omitted resources and approves no future version or broader exclusion.
- Exclude the complete unused HEIF decoder. PDF/JPEG/PNG remain the input boundary; upstream
  OCRmyPDF's existing optional-import fallback works without this decoder. The lock retains its
  resolution and the review records the omission. Supported image/PDF OCR and failure paths are
  tested without changing OCRmyPDF code. Do not leave a partially installed decoder.
- Replace the JBIG-dependent libtiff runtime with an independently built libtiff 4.7.2 that
  disables JBIG and preserves the other codecs and Windows I/O mode. Keep historical runtime
  manifest v1 unchanged. Production uses a separately fingerprinted v2 manifest with 38 files,
  28 signed vendor packages and one source-built provider. Do not describe the new DLL as
  vendor-signed. Include the exact compiler/startup support licences, source and reproducible
  recipe, not merely the upstream MIT package label.
- Supply canonical Microsoft VC runtime files through the Microsoft-installed external runtime,
  not through an inferred redistribution grant from the Qt/CPython wheels. The isolated stage
  proof identifies the exact installed reference and signed Microsoft installer without running
  that installer. Retain only pikepdf's hash-renamed C++ runtime object under its own explicit
  upstream downstream-distribution notice and the full applicable terms. Final protective
  distributor/end-user terms and clean prerequisite installation remain PWS-0802 qualifications.

## Evidence model

Separate consumed input-archive hashes from individual staged-file hashes. Embedded libraries
are separate components with immutable source revisions when no independent version exists.
Upstream all-target Cargo declarations are labelled as vendor-declared build closure, rather
than asserting that every declared crate is compiled into one Windows binary. Preserve exact
raw notice bytes and encoding; Git does not normalize those reviewed assets.

Publish CycloneDX 1.6 and SPDX 2.3 sidecars, a human-readable notice bundle, exhaustive resolved
inventory, input-verification receipt, deterministic reopened ZIP and matching hashes for the
same candidate. Sidecars sit outside the ZIP inventory, avoiding circular self-hashes. Source
snapshots and recipe/review hashes identify the dirty source exactly; HEAD alone does not.

The internal stage keeps product licence `NOASSERTION`; this limited first-party deferral never
grants unresolved third-party rights. Installer identity, signing, public release approval,
update trust, supported OS claims and final product terms remain with their existing M8 owners.
Later builds must regenerate and reconcile evidence against their actual contents. No measured
performance result, source revision, signature or candidate result is relabelled.

## Verification and consequences

Synthetic tests cover current/old-policy/future/invalid contracts, unsafe archive paths and
links, source/hash drift, missing notice/source/replacement material, forbidden components,
privacy, cancellation, disk-full cleanup and preservation of a previous good artifact. Native
qualification includes two equal fixed-epoch builds, all upstream libtiff tests, exports/imports,
actual OCR success/failure and runtime admission/tamper rejection. Existing resource policy and
worker egress limits remain enforced.

These are PaperWorkShell-local build and support artifacts. No sibling source, service,
database, fixture, adapter, shared runtime or ThirdLife compatibility result is an input. A later
ThirdLife cut can consume only the exact public frozen release through ordinary documented
behavior; this staging procedure introduces no application handoff interface.
