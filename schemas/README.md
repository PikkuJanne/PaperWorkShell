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

## Declarative template v1

- Contract: [`template-v1.schema.json`](template-v1.schema.json)
- Canonical synthetic example:
  [`examples/template-v1-minimal.json`](examples/template-v1-minimal.json)
- Data-only semantics, trust boundaries, and evolution policy:
  [`docs/schemas/template-v1.md`](../docs/schemas/template-v1.md)

The template schema is a closed, self-contained Draft 2020-12 contract for procedural packaging
guidance, unconditional checklist counts/order, output constraints, inert provenance, lifecycle
dates, warnings, and user-review confirmations. It provides no executable, condition, eligibility,
legal-decision, automatic-completion, path, or arbitrary filename-format surface. Embedded
provenance is explicitly untrusted; the future loader derives trust externally and never
dereferences a template URL.

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
