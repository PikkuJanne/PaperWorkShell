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
