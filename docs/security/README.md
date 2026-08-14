# PaperWorkShell security model

- Status: Binding foundation model
- Task: PWS-0004
- Last reviewed: 2026-08-14

This directory defines the security and privacy assumptions that must be preserved before
PaperWorkShell parses untrusted documents. It is split into four artifacts so later tasks can
extend one concern without weakening the others:

- [Threat model](threat-model.md): assets, actors, attacker capabilities, trust boundaries,
  threats, misuse cases, and mandatory mitigation principles.
- [Data flows](data-flows.md): worker lifecycle, exact PDF inspection, import, preview, advisory
  quality review, OCR, export, diagnostics, startup recovery, deletion, template, and update flows
  with numbered boundary crossings and failure behavior.
- [Threat traceability](threat-traceability.md): one mapping from every threat to frozen
  decisions and implementation tasks or gates.
- [Initial security test plan](security-test-plan.md): deterministic success and failure evidence
  expected as the mapped tasks are implemented.
- [Synthetic fixture and restricted-sample policy](../development/synthetic-fixture-policy.md):
  public-only corpus provenance, deterministic regeneration, and the outside-repository handling
  rule for sensitive bug samples mapped by TM-028 and ST-020.

The frozen decisions in [DECISIONS.md](../../DECISIONS.md), the binding requirements in
[ROADMAP.md](../../ROADMAP.md), and the selected tasks in [TASKS.yaml](../../TASKS.yaml) remain
higher authority. This model does not approve a dependency, parser, isolation mechanism,
redaction claim, updater, or diagnostic export ahead of its task and gate.

Run the offline reference check after changing security or other repository documentation:

```powershell
py -3.14 tools/dev.py validate-docs
```

The validator checks local Markdown links and anchors, decision/task references, and the
threat-to-traceability/test-plan inventory. It does not retrieve network content.
