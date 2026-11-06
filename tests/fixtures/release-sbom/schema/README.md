# Test-only SBOM schema references

PWS-0801 uses these unmodified upstream specification assets to validate generated synthetic
CycloneDX 1.6 and SPDX 2.3 documents offline. They are test inputs, not application dependencies,
release artifacts, or a PaperWorkShell runtime schema.

`PROVENANCE.json` records the exact tagged upstream URL, byte count, and SHA-256 for every asset.
Tests verify those identities and resolve schema references only from this local set. No schema
retrieval occurs during tests.

- CycloneDX specification contributors, specification tag `1.6`: the three JSON schemas are
  licensed under Apache-2.0. See `CycloneDX-LICENSE.txt`. The main schema includes its upstream
  licensing comment. No schema was modified.
- SPDX Workgroup / SPDX specification contributors, specification tag `v2.3`: the SPDX JSON
  schema is distributed under the repository's Creative Commons Attribution 3.0 Unported terms.
  See `SPDX-LICENSE.txt`. No schema was modified. Generated SPDX document metadata uses the
  standard-required `CC0-1.0` data licence; that is distinct from the schema asset's licence.

The schema fixture data contains no source document or personal information. Test inventories
are constructed in Python from clearly synthetic component names, notices, and byte strings.
