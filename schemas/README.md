# Product schemas

Versioned PaperWorkShell case-manifest, template, worker-RPC, and synthetic-fixture schemas belong in this directory.
Schema meaning is introduced and migrated by the corresponding roadmap task; `TASKS.schema.json`
remains at the repository root because it validates the repository task graph itself.

## Synthetic fixture index v1

- Contract: [`synthetic-fixture-index-v1.schema.json`](synthetic-fixture-index-v1.schema.json)
- Canonical generated index:
  [`tests/fixtures/synthetic/v1/index.json`](../tests/fixtures/synthetic/v1/index.json)
- Corpus and restricted-sample policy:
  [`docs/development/synthetic-fixture-policy.md`](../docs/development/synthetic-fixture-policy.md)

The closed, self-contained Draft 2020-12 contract records exact generated fixture bytes,
construction assertions, public-only provenance, complete roadmap-category coverage, and bounded
on-demand recipes. Repository validation authenticates the schema, proves rejected version and
structure examples fail, regenerates committed artifacts byte-for-byte, and enforces the historical,
index, tracked, and nonignored-untracked document policy without opening documents in a feature
engine.

## Case manifest v1

- Contract: [`case-manifest-v1.schema.json`](case-manifest-v1.schema.json)
- Canonical minimal example:
  [`examples/case-manifest-v1-minimal.json`](examples/case-manifest-v1-minimal.json)
- Representative synthetic example:
  [`examples/case-manifest-v1-representative.json`](examples/case-manifest-v1-representative.json)
- Semantics and evolution policy:
  [`docs/schemas/case-manifest-v1.md`](../docs/schemas/case-manifest-v1.md)

The schema is self-contained Draft 2020-12. Repository validation combines structural validation
with deterministic semantic checks for IDs, references, page bounds, operation replay, normalized
boxes, storage names, confirmations, and export-history references.

Version 1 is frozen migration input. Its schema and packaged copy are not changed when a successor
is released.

## Case manifest v2

- Contract: [`case-manifest-v2.schema.json`](case-manifest-v2.schema.json)
- Canonical minimal example:
  [`examples/case-manifest-v2-minimal.json`](examples/case-manifest-v2-minimal.json)
- Representative synthetic example:
  [`examples/case-manifest-v2-representative.json`](examples/case-manifest-v2-representative.json)
- Semantics and migration:
  [`docs/schemas/case-manifest-v2.md`](../docs/schemas/case-manifest-v2.md)
- Released overlay-v1 runtime binding:
  [`docs/architecture/overlay-domain-model.md`](../docs/architecture/overlay-domain-model.md)

Version 2 is the current manifest contract. It retains the closed v1 top-level graph and adds
version-coupled deskew, contrast, and background-cleanup intent. Repository load migrates a fully
validated v1 copy through the existing transactional recovery journal before returning state.
PWS-0302 binds the overlay-v1 wire already shared by v1 and v2: per-page z-order is semantic,
visibility is derived rather than stored, and the signature-image and ordinary-shape terminology
metadata cannot claim cryptographic signing or permanent removal. No schema bytes or reviewed v1/v2
fingerprints changed for that runtime binding, and migration fresh-copies both populated and empty
asset/overlay collections without inserting defaults.

## Export history v1

- Contract: [`export-history-v1.schema.json`](export-history-v1.schema.json)
- Record semantics and persistence boundary:
  [`docs/schemas/export-history-v1.md`](../docs/schemas/export-history-v1.md)

The closed record stores only an internal export ID, trusted UTC record time, exact source-manifest
revision and digest, semantic fingerprint, verified output digest/size/page count, performed check
codes, and the unsigned-derivative fact. It has no destination path or filename field. The digest
in each case-manifest `export_history` entry is the SHA-256 of that individual canonical record,
not the digest of the complete append-only history file.

## Declarative template v1

- Contract: [`template-v1.schema.json`](template-v1.schema.json)
- Canonical minimal synthetic example (`generic-portal-application` / `1.0.0`):
  [`examples/template-v1-minimal.json`](examples/template-v1-minimal.json)
- Complete built-in workflow (`generic-portal-application` / `1.1.0`):
  [`templates/builtin/generic-portal-application-v1.1.0.json`](../templates/builtin/generic-portal-application-v1.1.0.json)
- Complete workflow semantics and structural scenarios:
  [`docs/templates/generic-portal-application-v1.md`](../docs/templates/generic-portal-application-v1.md)
- Data-only semantics, trust boundaries, and evolution policy:
  [`docs/schemas/template-v1.md`](../docs/schemas/template-v1.md)

The template schema is a closed, self-contained Draft 2020-12 contract for procedural packaging
guidance, unconditional checklist counts/order, output constraints, inert provenance, lifecycle
dates, warnings, and user-review confirmations. It provides no executable, condition, eligibility,
legal-decision, automatic-completion, path, or arbitrary filename-format surface. Embedded
provenance is explicitly untrusted; the runtime loader derives trust externally and never
dereferences a template URL. The implemented PWS-0401 runtime keeps a byte-identical packaged copy
of this schema, authenticates its canonical semantic fingerprint, binds the exact loaded bytes to
external catalog evidence, and exposes labeled provenance and lifecycle state through the
[`safe template-loading contract`](../docs/architecture/template-loading.md).

The 1.0.0 minimal example remains immutable under its accepted identity. PWS-0402 uses content
version 1.1.0 for the complete workflow while retaining schema version 1. The complete template's
counts, order, output modes, numeric limits, and filenames are generic planning defaults, not facts
about a portal. Its confirmations record user review only; PWS-0403, PWS-0406, and PWS-0407 own
assignment, constraint/filename evaluation, and verified output planning respectively.

## Worker RPC v1

- Contract: [`worker-rpc-v1.schema.json`](worker-rpc-v1.schema.json)
- Canonical synthetic messages under `examples/worker-rpc/v1`, starting with:
  [`session-hello.json`](examples/worker-rpc/v1/session-hello.json)
- Semantics, state, limits, and evolution policy:
  [`docs/schemas/worker-rpc-v1.md`](../docs/schemas/worker-rpc-v1.md)

The RPC schema is self-contained Draft 2020-12 and closes the common envelope and every message
payload. Repository validation adds strict bounded JSON decoding and semantic checks for negotiation,
correlation, operation registration, logical case-object references, progress,
results, and closed sanitized error codes without public context values. Messages never carry filesystem paths or raw exception
text.

## Dependency inventory v1

- Contract: [`dependency-inventory-v1.schema.json`](dependency-inventory-v1.schema.json)
- Complete policy baseline:
  [`packaging/dependency-inventory-v1.json`](../packaging/dependency-inventory-v1.json)
- Admission, license, redistribution, and release-evidence policy:
  [`docs/development/dependency-policy.md`](../docs/development/dependency-policy.md)

The schema describes a non-release policy baseline. Its offline validator adds strict bounded JSON
decoding, schema fingerprinting, dependency-graph closure, frozen-decision and task references,
root-lock/declaration/workflow reconciliation, compatible Windows wheel hashes, closed upstream and
admission profiles, worker-boundary checks, exact evaluated-artifact evidence, renderer evidence,
selected license routes, and the distribution-changing decision gate. A resolved Windows build and
final SBOM require a later versioned release-evidence contract and packaged-file inventory.
