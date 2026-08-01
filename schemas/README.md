# Product schemas

Versioned PaperWorkShell case-manifest, template, and worker-RPC schemas belong in this directory.
Schema meaning is introduced and migrated by the corresponding roadmap task; `TASKS.schema.json`
remains at the repository root because it validates the repository task graph itself.

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
