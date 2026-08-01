# ADR 0001: CPython 3.14 and uv development toolchain

- Status: Accepted
- Date: 2026-08-01
- Task: PWS-0001
- Decisions: PWS-D001, PWS-D003, PWS-D004, PWS-D014, PWS-D034

## Context

PaperWorkShell needs a reproducible Python baseline before document-processing or UI dependencies are introduced. The supported development environment must be straightforward on Windows, preserve a portable package boundary, lock transitive dependencies, and expose the same local commands to people and automation.

The user-facing product name remains **PaperWorkShell**. Python distribution and import identifiers use `paperworkshell`.

## Decision

1. Standard GIL-enabled CPython 3.14 is the sole supported development minor. `requires-python` is `>=3.14,<3.15`, and `.python-version` contains `3.14`.
2. Compatible 3.14 patch updates are allowed so security and bug fixes do not require a toolchain ADR. The experimental free-threaded build is not supported at this stage.
3. `uv` 0.11.32 manages the environment and dependency lock. `pyproject.toml` enforces that exact CLI version.
4. `uv_build` 0.11.32 is the exact build backend. The application package uses the `src/paperworkshell` layout.
5. `uv.lock` is committed. `uv sync --locked` is the canonical clean-checkout bootstrap and fails when project metadata and the lock disagree.
6. PEP 735 dependency groups separate quality, test, and validation tools. The default `dev` group includes all three. Runtime dependencies remain empty in PWS-0001.
7. `tools/dev.py` is the stable developer command entry point. It invokes fixed argument arrays with `shell=False`; developer input is never interpolated into a command string.
8. PySide6, pikepdf/qpdf, OCRmyPDF/Tesseract, rendering, packaging, signing, and cloud dependencies remain deferred to their roadmap tasks and reviews.

## Evidence

- [Python's maintained-version schedule](https://devguide.python.org/versions/) lists Python 3.14 as supported through October 2030.
- [uv's Python support policy](https://docs.astral.sh/uv/reference/policies/python/) gives CPython 3.14 Tier 1 support.
- [uv project documentation](https://docs.astral.sh/uv/guides/projects/) defines the committed cross-platform lockfile and synchronized project environment.
- [uv dependency-group documentation](https://docs.astral.sh/uv/concepts/projects/dependencies/) supports the PEP 735 group layout used here.
- [uv build-backend documentation](https://docs.astral.sh/uv/configuration/build-backend/) documents `uv_build` for pure-Python packages.
- [Qt for Python release notes](https://doc.qt.io/qtforpython-6.8/release_notes/pyside6_release_notes.html) record CPython 3.14 support. This is compatibility evidence only; PySide6 is not added by this task.

## Windows implications

- The baseline is exercised on Windows x86-64 with CPython 3.14.6 and uv 0.11.32.
- Repository text is normalized to LF through `.gitattributes`; Git may use platform-appropriate checkout behavior for unclassified files.
- Developer commands are Python-based rather than tied to PowerShell or a POSIX shell.
- Installer technology, the release interpreter's provenance, code signing, and bundled native components remain Milestone 8 decisions.

## Dependency and licensing notes

PWS-0001 adds development/build tooling only:

| Component | Purpose | License family |
|---|---|---|
| CPython | Supported interpreter | PSF License |
| uv / uv_build | Environment, locking, build backend | MIT OR Apache-2.0 |
| Ruff | Formatting and linting | MIT |
| mypy | Static type checking | MIT |
| pytest | Unit-test runner | MIT |
| PyYAML | Safe YAML parsing for repository metadata | MIT |
| jsonschema | Local Draft 2020-12 validation | MIT |
| types-jsonschema | Static typing data for jsonschema | Apache-2.0 |
| types-PyYAML | Static typing data for PyYAML | Apache-2.0 |

The lockfile records exact direct and transitive versions. The formal dependency inventory, redistribution review, and later SBOM inputs belong to PWS-0005 and PWS-0801. PySide6 licensing and redistribution obligations must be documented before it is introduced.

## Security, privacy, and accessibility effects

- Runtime dependencies remain empty, and no untrusted document parser is introduced.
- Validation reads only repository planning files and performs no network access.
- Developer subprocesses use structured arguments and do not accept filenames, template values, document text, or other sensitive values.
- Ignore rules cover local cases, diagnostic exports, secrets, signing material, caches, and build output without globally excluding PDF/image extensions needed by later synthetic fixtures.
- This task adds no UI. Accessibility behavior is therefore unchanged.

## Alternatives considered

### `venv`, pip, and requirements files

Rejected because environment creation, direct dependency declaration, transitive locking, and command execution would be split across multiple mechanisms.

### Poetry, PDM, or Hatch as the primary manager

Rejected because each adds a second project-management layer when uv already provides the required Windows environment and lock workflow. This does not prohibit reassessment if a later packaging requirement provides concrete evidence.

### CPython 3.13

Rejected for the initial baseline because 3.14 is supported by the selected tooling and has a longer remaining upstream support window.

### Free-threaded CPython 3.14

Rejected because PaperWorkShell has no measured concurrency need that justifies the additional native-extension compatibility surface.

## Consequences

- Contributors need CPython 3.14 and exactly uv 0.11.32.
- An intentional dependency update changes both `pyproject.toml` and `uv.lock` and requires review.
- Application runtime dependencies will be admitted only by their dependency-ready roadmap tasks and licensing/security review.
- PWS-0002 can establish the remaining source layers without revisiting the package manager or Python minor.
