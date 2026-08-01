# Windows packaging and dependency evidence

Windows packaging configuration and reviewed release inputs belong in this directory. Installer
selection, signing, authenticated updates, and generated release artifacts remain deferred to
their Milestone 8 tasks.

## Dependency inventory

- [`dependency-inventory-v1.json`](dependency-inventory-v1.json) is the complete checked-in
  PWS-0005 policy baseline.
- [`dependency-inventory-v1.schema.json`](../schemas/dependency-inventory-v1.schema.json) is its
  versioned Draft 2020-12 contract.
- [`dependency-policy.md`](../docs/development/dependency-policy.md) defines admission, update,
  license, integrity, redistribution, renderer, OCR, and SBOM rules.
- [`distribution-decision-bypass.json`](../tests/fixtures/dependencies/v1/invalid/distribution-decision-bypass.json)
  is the canonical policy-rejected example.

The baseline records currently admitted development/build/CI dependencies as well as planned,
deferred, candidate, evaluated, and decision-gated application families. `evaluated` is reserved
for a concrete pinned artifact under measurement, with exact URL, filename, target, size, and
SHA-256 evidence; it does not activate or approve that artifact. Locked Python records duplicate
the reviewed source and compatible Windows x64/universal wheel hashes from `uv.lock` so source,
artifact, and root-project metadata drift fails closed.
The baseline is not a snapshot of an installed machine and is not a release SBOM. In particular, a
minor-line interpreter, a minimum external-tool version, an environment probe, or a representative
source hash must not be presented as the exact identity of a packaged Windows artifact.

## Maintaining the baseline

For every admitted addition or upgrade:

1. Follow the complete review in the dependency policy.
2. Update the direct declaration and `uv.lock` where applicable.
3. Review every transitive, platform-marker, source, license, executable, and native-binary change.
4. Update components and relationships, then increment `inventory_revision` and `reviewed_on`.
   New component IDs or upstream/license identity changes also require an explicit update to the
   validator's closed reviewed identity policy.
5. Keep non-approved components inactive. A disabled candidate edge documents risk; it does not
   permit production installation, bundling, discovery, or invocation. A concrete benchmark
   artifact remains `evaluated` with candidate packaging state until explicitly approved.
6. Run the registered dependency, schema, documentation, and full quality checks.

Do not commit downloaded installers, native archives, OCR language data, build directories,
generated SBOMs, license keys, certificates, signing material, or local machine inventories as
ordinary baseline files. A later task may define reviewed generated release artifacts and their
retention policy.

## Release reconciliation

`PWS-0801` must create a resolved inventory for the exact Windows release candidate. It reconciles
three independent views:

```text
reviewed baseline + uv.lock + upstream embedded-component records
                              |
                              v
                 final packaged-file inventory
                              |
                              v
           CycloneDX JSON + SPDX JSON + notice bundle
```

The resolved evidence uses exact interpreter/package/tool/data revisions and hashes the artifacts
actually consumed or shipped. It maps every staged DLL, executable, plugin, model, license, and
notice to a reviewed component. It also records the selected license route and defines every SPDX
`LicenseRef`.

Release validation fails when an enabled dependency is absent from the SBOM, a packaged file has
no owner, a source or hash does not match, a selected license route is unresolved, required
notices/source/relinking material is missing, or a distribution-changing component lacks an
explicit frozen-decision change.

## Family-specific packaging gates

- Prefer the smallest reviewed PySide6 wheel set needed by the UI. Do not admit the convenience
  meta-package or Addons without inspecting whether QtPdf/PDFium and other native payloads enter
  the package. Qt PDF remains a renderer candidate until `PWS-0106` selects it.
- Inspect the selected pikepdf wheel for bundled qpdf and runtime libraries; inventory that native
  closure exactly once.
- Keep OCRmyPDF, Tesseract, and all document-processing helpers in the network-denied worker with
  fixed executable paths. Ghostscript remains prohibited unless the required decision or approved
  commercial route exists.
- Treat every Tesseract `.traineddata` file as a separate, versioned, hashed, licensed data
  component. The worker must not download language data.
- Admit a renderer only after concrete security, license, fidelity, performance, and Windows
  packaging evidence is accepted and its exact artifacts are added to the inventory.

## Validation

```powershell
py -3.14 tools/dev.py validate-dependencies
py -3.14 tools/dev.py validate-schemas
py -3.14 tools/dev.py validate-docs
```

All validation is offline. It checks the committed evidence and references without downloading
packages, binaries, notices, or vulnerability data.
