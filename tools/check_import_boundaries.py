"""Validate PaperWorkShell package layout and static import boundaries."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
import tokenize
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE_ROOT: Final = PROJECT_ROOT / "src" / "paperworkshell"
PROJECT_PACKAGE: Final = "paperworkshell"

REQUIRED_PACKAGES: Final = (
    "app",
    "domain",
    "ui",
    "worker",
    "pdf",
    "imaging",
    "ocr",
    "overlays",
    "preflight",
    "export",
    "templates",
    "storage",
    "diagnostics",
)
KNOWN_LAYERS: Final = frozenset(REQUIRED_PACKAGES)
DESKTOP_ENTRYPOINT: Final = f"{PROJECT_PACKAGE}.__main__"
DESKTOP_WORKER_COMPOSITION_MODULE: Final = f"{PROJECT_PACKAGE}.worker.desktop_composition"
ROOT_IMPORT_LAYERS: Final = frozenset((*KNOWN_LAYERS, "__main__"))
UI_FACING_LAYERS: Final = frozenset(("app", "ui", "desktop"))
DOCUMENT_ADAPTER_LAYERS: Final = frozenset(("pdf", "imaging", "ocr"))
APPROVED_EXTERNAL_IMPORT_PREFIXES: Final = {
    "pdf": frozenset({"pikepdf", "pypdfium2"}),
    "ui": frozenset(
        {
            "PySide6.QtCore",
            "PySide6.QtGui",
            "PySide6.QtWidgets",
        }
    ),
}
FORBIDDEN_UI_TOOLKIT_ROOTS: Final = frozenset(("_tkinter", "tkinter", "turtle"))
WORKER_PACKAGE: Final = f"{PROJECT_PACKAGE}.worker"
EXPORT_PACKAGE: Final = f"{PROJECT_PACKAGE}.export"
GUI_SAFE_WORKER_PREFIXES: Final = (
    f"{WORKER_PACKAGE}.client",
    f"{WORKER_PACKAGE}.protocol",
)


@dataclass(frozen=True, order=True)
class BoundaryViolation:
    """One stable, sortable architecture-policy violation."""

    path: str
    line: int
    code: str
    detail: str

    def render(self) -> str:
        """Render a deterministic developer-facing diagnostic."""

        return f"ERROR [{self.code}] {self.path}:{self.line}: {self.detail}"


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _module_context(path: Path, package_root: Path) -> tuple[str, str]:
    relative = path.relative_to(package_root.parent).with_suffix("")
    parts = list(relative.parts)
    is_initializer = parts[-1] == "__init__"
    if is_initializer:
        parts.pop()
    module = ".".join(parts)
    package = module if is_initializer else module.rpartition(".")[0]
    return module, package


def _layer(module: str) -> str | None:
    if module == DESKTOP_ENTRYPOINT:
        return "desktop"
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == PROJECT_PACKAGE:
        return parts[1]
    return None


def _is_within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _is_standard_library(module: str) -> bool:
    root = module.partition(".")[0]
    return root == "__future__" or root in sys.stdlib_module_names


def _from_import_targets(node: ast.ImportFrom, package: str) -> tuple[str, ...]:
    if node.level:
        relative_name = f"{'.' * node.level}{node.module or ''}"
        base = importlib.util.resolve_name(relative_name, package)
    else:
        base = node.module or ""

    if not base:
        raise ImportError("empty import target")

    targets = [base]
    if base == PROJECT_PACKAGE:
        targets.extend(
            f"{base}.{alias.name}"
            for alias in node.names
            if alias.name != "*" and alias.name in ROOT_IMPORT_LAYERS
        )
    elif base == WORKER_PACKAGE:
        targets.extend(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    elif base == EXPORT_PACKAGE:
        targets.extend(f"{base}.{alias.name}" for alias in node.names)
    return tuple(targets)


def _target_violation(
    *,
    importer: str,
    owner: str | None,
    target: str,
    path: str,
    line: int,
) -> BoundaryViolation | None:
    target_root = target.partition(".")[0]
    if target_root in FORBIDDEN_UI_TOOLKIT_ROOTS:
        return BoundaryViolation(
            path,
            line,
            "ARCH_UI_TOOLKIT",
            f"unapproved UI toolkit import {target!r} is not allowed in product code",
        )

    if _is_standard_library(target):
        return None

    approved_external_prefixes = (
        frozenset() if owner is None else APPROVED_EXTERNAL_IMPORT_PREFIXES.get(owner, frozenset())
    )
    if any(_is_within(target, prefix) for prefix in approved_external_prefixes):
        return None

    if _is_within(target, PROJECT_PACKAGE):
        target_layer = _layer(target)

        if (
            target_layer is not None
            and target_layer not in KNOWN_LAYERS
            and target != DESKTOP_ENTRYPOINT
        ):
            return BoundaryViolation(
                path,
                line,
                "ARCH_UNKNOWN_LAYER",
                f"import target {target!r} is outside the declared package layers",
            )

        if owner is None and target_layer is not None:
            return BoundaryViolation(
                path,
                line,
                "ARCH_ROOT_REEXPORT",
                f"the root package must not import layer {target!r}",
            )

        if owner == "domain" and target_layer not in (None, "domain"):
            return BoundaryViolation(
                path,
                line,
                "ARCH_DOMAIN_OUTBOUND",
                f"domain code must not import {target!r}",
            )

        if owner == "app" and target_layer in ("ui", "desktop"):
            return BoundaryViolation(
                path,
                line,
                "ARCH_UI_DIRECTION",
                f"application layer must not import higher UI tier {target!r}",
            )

        if owner == "ui" and target_layer == "desktop":
            return BoundaryViolation(
                path,
                line,
                "ARCH_UI_DIRECTION",
                f"UI layer must not import desktop composition entrypoint {target!r}",
            )

        is_gui_safe_worker_module = any(
            _is_within(importer, prefix) for prefix in GUI_SAFE_WORKER_PREFIXES
        )

        if (
            (owner in UI_FACING_LAYERS or is_gui_safe_worker_module)
            and _is_within(target, EXPORT_PACKAGE)
            and target != EXPORT_PACKAGE
        ):
            return BoundaryViolation(
                path,
                line,
                "ARCH_GUI_EXPORT_IMPLEMENTATION",
                f"GUI-safe module {importer!r} must not import export implementation {target!r}",
            )

        if owner in UI_FACING_LAYERS and target_layer in DOCUMENT_ADAPTER_LAYERS:
            return BoundaryViolation(
                path,
                line,
                "ARCH_UI_ADAPTER",
                f"UI-facing layer {owner!r} must not import adapter {target!r}",
            )

        is_desktop_worker_composition = (
            importer == DESKTOP_ENTRYPOINT and target == DESKTOP_WORKER_COMPOSITION_MODULE
        )

        if (
            owner in UI_FACING_LAYERS
            and _is_within(target, WORKER_PACKAGE)
            and target != WORKER_PACKAGE
            and not any(_is_within(target, prefix) for prefix in GUI_SAFE_WORKER_PREFIXES)
            and not is_desktop_worker_composition
        ):
            return BoundaryViolation(
                path,
                line,
                "ARCH_UI_WORKER_HOST",
                f"UI-facing layer {owner!r} must not import worker-host module {target!r}",
            )

        targets_worker_implementation = (
            _is_within(target, WORKER_PACKAGE)
            and target != WORKER_PACKAGE
            and not any(_is_within(target, prefix) for prefix in GUI_SAFE_WORKER_PREFIXES)
        )
        if is_gui_safe_worker_module and (
            target_layer in DOCUMENT_ADAPTER_LAYERS or targets_worker_implementation
        ):
            return BoundaryViolation(
                path,
                line,
                "ARCH_WORKER_CLIENT_BOUNDARY",
                f"GUI-safe worker module {importer!r} must not import {target!r}",
            )

        if owner is not None and owner not in UI_FACING_LAYERS and target_layer in UI_FACING_LAYERS:
            return BoundaryViolation(
                path,
                line,
                "ARCH_REVERSE_UI",
                f"layer {owner!r} must not import UI-facing layer {target!r}",
            )

        if owner in DOCUMENT_ADAPTER_LAYERS and target_layer == "worker":
            return BoundaryViolation(
                path,
                line,
                "ARCH_ADAPTER_WORKER",
                f"adapter layer {owner!r} must not import worker layer {target!r}",
            )

        return None

    code = "ARCH_DOMAIN_EXTERNAL" if owner == "domain" else "ARCH_EXTERNAL_IMPORT"
    layer_name = owner or "root"
    return BoundaryViolation(
        path,
        line,
        code,
        f"layer {layer_name!r} imports unapproved third-party package {target!r}",
    )


def _is_dynamic_import_function(
    function: ast.expr,
    *,
    importlib_aliases: set[str],
    import_module_aliases: set[str],
    builtins_aliases: set[str],
    builtin_import_aliases: set[str],
) -> bool:
    if isinstance(function, ast.Name):
        return function.id in (builtin_import_aliases | import_module_aliases)

    if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
        return (function.attr == "import_module" and function.value.id in importlib_aliases) or (
            function.attr == "__import__" and function.value.id in builtins_aliases
        )

    if (
        isinstance(function, ast.Call)
        and isinstance(function.func, ast.Name)
        and function.func.id == "getattr"
        and len(function.args) >= 2
        and isinstance(function.args[0], ast.Name)
        and isinstance(function.args[1], ast.Constant)
        and isinstance(function.args[1].value, str)
    ):
        owner_name = function.args[0].id
        attribute_name = function.args[1].value
        return (owner_name in importlib_aliases and attribute_name == "import_module") or (
            owner_name in builtins_aliases and attribute_name == "__import__"
        )

    return False


def inspect_source(
    source: str,
    *,
    module: str,
    package: str,
    path: str,
) -> tuple[BoundaryViolation, ...]:
    """Inspect one source string without importing or executing it."""

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        return (
            BoundaryViolation(
                path,
                error.lineno or 1,
                "ARCH_PARSE_ERROR",
                "source cannot be parsed",
            ),
        )

    owner = _layer(module)
    violations: list[BoundaryViolation] = []
    if module not in (PROJECT_PACKAGE, DESKTOP_ENTRYPOINT) and owner not in KNOWN_LAYERS:
        violations.append(
            BoundaryViolation(
                path,
                1,
                "ARCH_UNKNOWN_LAYER",
                f"source module {module!r} is outside the declared package layers",
            )
        )

    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()
    builtins_aliases: set[str] = set()
    builtin_import_aliases: set[str] = {"__import__"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = tuple(alias.name for alias in node.names)
            import_line = node.lineno
            for alias in node.names:
                imported_root = alias.name.partition(".")[0]
                bound_name = alias.asname or imported_root
                if imported_root == "importlib":
                    importlib_aliases.add(bound_name)
                if imported_root == "builtins":
                    builtins_aliases.add(bound_name)
        elif isinstance(node, ast.ImportFrom):
            import_line = node.lineno
            try:
                targets = _from_import_targets(node, package)
            except ImportError:
                violations.append(
                    BoundaryViolation(
                        path,
                        node.lineno,
                        "ARCH_RELATIVE_IMPORT",
                        "relative import escapes or lacks a package context",
                    )
                )
                continue

            if node.level == 0 and node.module == "importlib":
                import_module_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "import_module"
                )
            if node.level == 0 and node.module == "builtins":
                builtin_import_aliases.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "__import__"
                )
        else:
            continue

        for target in targets:
            if (
                module == package
                and module != PROJECT_PACKAGE
                and _is_within(target, PROJECT_PACKAGE)
            ):
                violations.append(
                    BoundaryViolation(
                        path,
                        import_line,
                        "ARCH_INIT_IMPORT",
                        f"package initializer {module!r} must remain a passive namespace",
                    )
                )
                continue

            violation = _target_violation(
                importer=module,
                owner=owner,
                target=target,
                path=path,
                line=import_line,
            )
            if violation is not None:
                violations.append(violation)

    for node in ast.walk(tree):
        if not isinstance(node, ast.expr):
            continue
        uses_dynamic_import = _is_dynamic_import_function(
            node,
            importlib_aliases=importlib_aliases,
            import_module_aliases=import_module_aliases,
            builtins_aliases=builtins_aliases,
            builtin_import_aliases=builtin_import_aliases,
        )
        if uses_dynamic_import:
            violations.append(
                BoundaryViolation(
                    path,
                    node.lineno,
                    "ARCH_DYNAMIC_IMPORT",
                    "dynamic imports bypass the static dependency policy",
                )
            )

    return tuple(sorted(set(violations)))


def inspect_file(path: Path, package_root: Path) -> tuple[BoundaryViolation, ...]:
    """Inspect one Python file under a PaperWorkShell package root."""

    display_path = _display_path(path)
    try:
        with tokenize.open(str(path)) as source_file:
            source = source_file.read()
    except OSError, SyntaxError, UnicodeError:
        return (
            BoundaryViolation(
                display_path,
                1,
                "ARCH_READ_ERROR",
                "source cannot be read using its declared encoding",
            ),
        )

    module, package = _module_context(path, package_root)
    return inspect_source(
        source,
        module=module,
        package=package,
        path=display_path,
    )


def inspect_package(package_root: Path) -> tuple[BoundaryViolation, ...]:
    """Inspect required layout and every Python source under a package root."""

    package_root = package_root.resolve()
    if not package_root.is_dir():
        return (
            BoundaryViolation(
                _display_path(package_root),
                1,
                "ARCH_PACKAGE_ROOT",
                "PaperWorkShell package root does not exist",
            ),
        )

    violations: list[BoundaryViolation] = []
    root_initializer = package_root / "__init__.py"
    if not root_initializer.is_file():
        violations.append(
            BoundaryViolation(
                _display_path(root_initializer),
                1,
                "ARCH_PACKAGE_LAYOUT",
                "root package is missing __init__.py",
            )
        )

    for package_name in REQUIRED_PACKAGES:
        initializer = package_root / package_name / "__init__.py"
        if not initializer.is_file():
            violations.append(
                BoundaryViolation(
                    _display_path(initializer),
                    1,
                    "ARCH_PACKAGE_LAYOUT",
                    f"required package {package_name!r} is missing __init__.py",
                )
            )

    for source_path in sorted(package_root.rglob("*.py"), key=lambda path: path.as_posix()):
        violations.extend(inspect_file(source_path, package_root))

    return tuple(sorted(set(violations)))


def main(argv: Sequence[str] | None = None) -> int:
    """Run package and import-boundary validation."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-root",
        type=Path,
        default=DEFAULT_PACKAGE_ROOT,
        help="package root to inspect (default: src/paperworkshell)",
    )
    arguments = parser.parse_args(argv)

    violations = inspect_package(arguments.package_root)
    if violations:
        for violation in violations:
            print(violation.render(), file=sys.stderr)
        return 1

    source_count = sum(1 for _ in arguments.package_root.rglob("*.py"))
    print(
        "OK: PaperWorkShell package layout and import boundaries are valid "
        f"({len(REQUIRED_PACKAGES)} layers, {source_count} source files)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
