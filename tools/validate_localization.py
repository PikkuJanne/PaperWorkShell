"""Extract and validate the approved, offline application-text resource inventory."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from pathlib import Path

from paperworkshell.localization.catalog import (
    LocalizationError,
    load_catalog,
    placeholders,
    validate_catalog,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "paperworkshell"
CATALOG_ROOT = PACKAGE_ROOT / "localization" / "catalogs"
_MARKERS = frozenset({"tr", "mark"})
_DISPLAY_METHODS = frozenset({"safe_message", "claim", "status_message", "remediation"})
_SINKS = frozenset(
    {
        "setText",
        "setWindowTitle",
        "setAccessibleName",
        "setAccessibleDescription",
        "setToolTip",
        "setStatusTip",
        "setPlaceholderText",
        "setInformativeText",
        "setDetailedText",
        "addRow",
        "showMessage",
        "announce",
    }
)
_CONSTRUCTORS = frozenset({"QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QGroupBox"})
# These existing, closed presentation registries deliberately retain English
# backend values for validation. Their text is translated only when displayed.
_PRESENTATION_REGISTRY_MODULES = frozenset(
    {
        "app/core_overlay_tools.py",
        "app/checklist.py",
        "app/portal_constraints.py",
        "app/ocr_languages.py",
        "app/signature_images.py",
        "app/redaction_tools.py",
        "app/removal_export_review.py",
        "domain/checklist.py",
        "domain/quality_analysis.py",
        "domain/quality_ocr_review.py",
        "domain/redaction_export.py",
        "domain/target_size_search.py",
    }
)


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


_DISPLAY_ENUMS = frozenset(
    {
        "PackageSummaryOutputState",
        "PackageSummaryReviewKind",
        "RequirementCountUnit",
        "ChecklistStatus",
        "VerificationStatus",
    }
)


def _strings(node: ast.AST) -> Iterable[str]:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value


def _prose(value: str) -> bool:
    return (
        bool(value.strip())
        and any(character.isalpha() for character in value)
        and (" " in value or value[:1].isupper())
    )


def _marked_literals(expression: ast.expr) -> Iterable[str]:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        if expression.value:
            yield expression.value
    elif isinstance(expression, ast.IfExp):
        yield from _marked_literals(expression.body)
        yield from _marked_literals(expression.orelse)


def source_messages(package_root: Path = PACKAGE_ROOT) -> frozenset[str]:
    """Collect explicit markers plus closed display registries and template prose."""

    messages: set[str] = set()
    for path in sorted(package_root.rglob("*.py")):
        if "localization" in path.relative_to(package_root).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        if path.relative_to(package_root).as_posix() in _PRESENTATION_REGISTRY_MODULES:
            documentation = {
                id(node.value)
                for parent in ast.walk(tree)
                if isinstance(
                    parent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
                for node in parent.body[:1]
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            }
            messages.update(
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and id(node) not in documentation
                and isinstance(node.value, str)
                and _prose(node.value)
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and (
                node.name in _DISPLAY_ENUMS or node.name.endswith("Phase")
            ):
                for item in node.body:
                    if (
                        isinstance(item, ast.Assign)
                        and isinstance(item.value, ast.Constant)
                        and isinstance(item.value.value, str)
                    ):
                        messages.add(item.value.value)
                        messages.add(item.value.value.replace("_", " "))
            if isinstance(node, ast.Call) and _name(node.func) in _MARKERS and node.args:
                messages.update(_marked_literals(node.args[0]))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    isinstance(target, ast.Name) and "MESSAGES" in target.id for target in targets
                ):
                    if node.value is not None:
                        registry_values = (
                            node.value.values if isinstance(node.value, ast.Dict) else [node.value]
                        )
                        for value_node in registry_values:
                            if value_node is not None:
                                messages.update(
                                    value for value in _strings(value_node) if _prose(value)
                                )
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in _DISPLAY_METHODS
            ):
                for child in node.body:
                    if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                        continue  # Method documentation is not displayed.
                    messages.update(value for value in _strings(child) if _prose(value))
            if isinstance(node, ast.Call) and _name(node.func) in {
                "_finding_definition",
                "PreflightFindingDefinition",
            }:
                values: list[ast.expr] = []
                if _name(node.func) == "_finding_definition":
                    values.extend(node.args[1:])
                values.extend(
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg in {"message", "remediation"}
                )
                for value in values:
                    if (
                        isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                        and _prose(value.value)
                    ):
                        messages.add(value.value)
    if package_root == PACKAGE_ROOT:
        # A fixed content-free allow-list covers the finite generated editor
        # announcements. No source eval or document values are used.
        from paperworkshell.app.core_overlay_tools import _SAFE_EDITOR_ANNOUNCEMENTS

        messages.update(_SAFE_EDITOR_ANNOUNCEMENTS)
    for path in sorted((package_root / "templates" / "builtin").glob("*.json")):
        template = json.loads(path.read_text(encoding="utf-8"))
        messages.add(template["title"])
        for section, keys in (
            ("instructions", ("text",)),
            ("requirements", ("title", "guidance")),
            ("warnings", ("message",)),
            ("confirmations", ("statement",)),
        ):
            for item in template.get(section, []):
                messages.update(
                    item[key] for key in keys if isinstance(item.get(key), str) and item[key]
                )
    return frozenset(messages)


def unmarked_ui_strings(package_root: Path = PACKAGE_ROOT) -> tuple[tuple[str, int], ...]:
    """Reject literal display text at common Qt presentation entry points."""

    failures: list[tuple[str, int]] = []
    for path in sorted((package_root / "ui").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = _name(node.func)
            if name not in _SINKS | _CONSTRUCTORS:
                continue
            source = node.args[0]
            if (
                isinstance(source, ast.Constant)
                and isinstance(source.value, str)
                and _prose(source.value)
            ):
                failures.append((path.relative_to(package_root).as_posix(), node.lineno))
            elif isinstance(source, ast.JoinedStr) and any(
                _prose(value) for value in _strings(source)
            ):
                failures.append((path.relative_to(package_root).as_posix(), node.lineno))
    return tuple(failures)


def pseudo_translate(source: str, *, rtl: bool = False) -> str:
    """Expand known application literals without reversing data or placeholders."""

    substitutions = str.maketrans("AaEeIiOoUuCcNnSsYy", "ÁáÉéÍíÓóÚúÇçÑñŠšÝý")
    pieces = re.split(
        r"(\{[^{}]*\}|\{\{|\}\}|<[^<>]*>|\*\.[A-Za-z0-9]+|PaperWorkShell|"
        r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|"
        r"(?:Ctrl|Alt|Shift|Meta)(?:\+(?:Ctrl|Alt|Shift|Meta|[A-Za-z0-9]+))+)",
        source,
    )
    rendered = "".join(
        piece if index % 2 else piece.translate(substitutions) for index, piece in enumerate(pieces)
    )
    prefix = "اختبار " if rtl else ""
    lines = rendered.split("\n")
    return unicodedata.normalize(
        "NFC",
        "\n".join(
            f"[{prefix}{line}{' ~' * max(1, len(line) // 12)}]" if line else "" for line in lines
        ),
    )


def _payload(locale: str, sources: Sequence[str]) -> dict[str, object]:
    rtl = locale == "ar-XB"
    return {
        "schema_version": 1,
        "locale": locale,
        "direction": "rtl" if rtl else "ltr",
        "number": {
            "decimal_separator": "\u066b" if rtl else ".",
            "group_separator": "\u066c" if rtl else ",",
            "digits": "٠١٢٣٤٥٦٧٨٩" if rtl else "0123456789",
        },
        "date": {"order": "dmy" if rtl else "ymd", "separator": "/" if rtl else "-"},
        "messages": {
            source: source if locale == "en" else pseudo_translate(source, rtl=rtl)
            for source in sources
        },
    }


def refresh_catalogs(package_root: Path = PACKAGE_ROOT) -> int:
    """Refresh English and deterministic test locales only, preserving translations."""

    sources = sorted(source_messages(package_root))
    destination = package_root / "localization" / "catalogs"
    destination.mkdir(parents=True, exist_ok=True)
    for locale in ("en", "en-XA", "ar-XB"):
        payload = _payload(locale, sources)
        validate_catalog(payload)
        (destination / (locale + ".json")).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return len(sources)


def validate_resources() -> tuple[int, int]:
    sources = source_messages()
    paths = sorted(CATALOG_ROOT.glob("*.json"))
    if not {"en", "en-XA", "ar-XB"} <= {path.stem for path in paths} or len(paths) > 128:
        raise LocalizationError()
    for path in paths:
        catalog = load_catalog(path.stem)
        if set(catalog.messages) != sources:
            raise LocalizationError()
        if catalog.locale == "en" and any(key != value for key, value in catalog.messages.items()):
            raise LocalizationError()
        for source, translated in catalog.messages.items():
            if placeholders(source) != placeholders(translated):
                raise LocalizationError()
    return len(sources), len(paths)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="Refresh English and the two test catalogs."
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.refresh:
            count = refresh_catalogs()
            print(f"OK: refreshed {count} application messages in three built-in catalogs.")
            return 0
        count, catalogs = validate_resources()
        unmarked = unmarked_ui_strings()
        if unmarked:
            for path, line in unmarked:
                print(f"ERROR [LOCALIZATION_STRING_UNMARKED] {path}:{line}", file=sys.stderr)
            return 1
    except LocalizationError, OSError, ValueError, KeyError, TypeError:
        print(
            "ERROR [LOCALIZATION_RESOURCE_INVALID] Language resources do not match the application inventory.",
            file=sys.stderr,
        )
        return 1
    print(
        f"OK: {count} application messages, {catalogs} catalogs, placeholders and UI markers validated offline."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
