"""Versioned, path-free TRACE recipes and effective case projections."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.page_operations import (
    MAX_PAGE_COUNT,
    NEUTRAL_CONTRAST_FACTOR_MILLIONTHS,
    OrganizerProjection,
    SequenceChangeCommand,
    SequenceChangeReason,
)
from paperworkshell.localization.catalog import mark, tr

TRACE_RECIPE_VERSION: Final = 1
MAX_TRACE_RECIPE_BYTES: Final = 1_048_576


class TraceOperationKind(StrEnum):
    INPUT = "input"
    SELECTION = "selection"
    ORDER = "order"
    CROP = "crop"
    ROTATION = "rotation"
    DESKEW = "deskew"
    CONTRAST = "contrast"
    BACKGROUND = "background_cleanup"
    OVERLAY = "overlay"
    REMOVAL = "permanent_removal"
    PACKAGE = "checklist_package"
    OCR = "ocr"
    EXPORT = "pdf_export"


TRACE_OPERATION_LABELS: Final = {
    TraceOperationKind.INPUT: mark("Imported sources"),
    TraceOperationKind.SELECTION: mark("Page selection"),
    TraceOperationKind.ORDER: mark("Assembled order"),
    TraceOperationKind.ROTATION: mark("Page rotation"),
    TraceOperationKind.CROP: mark("Crop"),
    TraceOperationKind.DESKEW: mark("Deskew"),
    TraceOperationKind.CONTRAST: mark("Contrast"),
    TraceOperationKind.BACKGROUND: mark("Background cleanup"),
    TraceOperationKind.OVERLAY: mark("Overlays"),
    TraceOperationKind.REMOVAL: mark("Permanent removal marks"),
    TraceOperationKind.PACKAGE: mark("Checklist and package constraints"),
    TraceOperationKind.OCR: mark("Searchable text"),
    TraceOperationKind.EXPORT: mark("PDF export"),
}

_AUTOMATION_OPERATIONS: Final = frozenset(
    {
        TraceOperationKind.INPUT,
        TraceOperationKind.SELECTION,
        TraceOperationKind.ORDER,
        TraceOperationKind.ROTATION,
        TraceOperationKind.EXPORT,
    }
)
_DEFAULT_CONSTRAINTS: Final = {
    "constraints_version": 1,
    "allowed_output_modes": ["combined_pdf"],
    "encryption_policy": "must_be_unencrypted",
}


class TraceRecipeErrorCode(StrEnum):
    INVALID = "TRACE_RECIPE_INVALID"
    VERSION_UNSUPPORTED = "TRACE_RECIPE_VERSION_UNSUPPORTED"
    UNSUPPORTED = "TRACE_RECIPE_OPERATION_UNSUPPORTED"
    NO_PAGES = "TRACE_RECIPE_NO_PAGES"
    INPUT_MISMATCH = "TRACE_RECIPE_INPUT_MISMATCH"
    TARGET_CHANGED = "TRACE_RECIPE_TARGET_CHANGED"
    IO_FAILED = "TRACE_RECIPE_IO_FAILED"


class TraceOutputStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    CHANGED = "changed"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"


_ERROR_MESSAGES: Final = {
    TraceRecipeErrorCode.INVALID: mark("The recipe file is invalid."),
    TraceRecipeErrorCode.VERSION_UNSUPPORTED: mark(
        "This version of PaperWorkShell cannot read that recipe version."
    ),
    TraceRecipeErrorCode.UNSUPPORTED: mark(
        "This recipe contains operations outside the supported reuse and automation scope."
    ),
    TraceRecipeErrorCode.NO_PAGES: mark("Add or select pages before saving or applying a recipe."),
    TraceRecipeErrorCode.INPUT_MISMATCH: mark(
        "Import the expected source formats and page counts in recipe input order."
    ),
    TraceRecipeErrorCode.TARGET_CHANGED: mark(
        "The case changed. Reopen the recipe action and retry."
    ),
    TraceRecipeErrorCode.IO_FAILED: mark("PaperWorkShell could not read or save the recipe file."),
}


class TraceRecipeError(ValueError):
    """Safe recipe failure with an optional, allowlisted operation summary."""

    def __init__(
        self,
        code: TraceRecipeErrorCode,
        unsupported_operations: tuple[TraceOperationKind, ...] = (),
    ) -> None:
        self.code = code
        self.unsupported_operations = unsupported_operations
        self.safe_message = tr(_ERROR_MESSAGES[code])
        if unsupported_operations:
            names = ", ".join(tr(TRACE_OPERATION_LABELS[kind]) for kind in unsupported_operations)
            self.safe_message = f"{self.safe_message} {names}."
        super().__init__(code.value, self.safe_message)


@dataclass(frozen=True, slots=True)
class TraceSourceBinding:
    binding: int
    media_type: DetectedMediaType
    page_count: int

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not int
            or not 1 <= self.binding <= MAX_PAGE_COUNT
            or type(self.media_type) is not DetectedMediaType
            or type(self.page_count) is not int
            or not 1 <= self.page_count <= MAX_PAGE_COUNT
            or (self.media_type is not DetectedMediaType.PDF and self.page_count != 1)
        ):
            raise TraceRecipeError(TraceRecipeErrorCode.INVALID)


@dataclass(frozen=True, slots=True)
class TraceRecipePage:
    source: int
    page: int
    rotation: int

    def __post_init__(self) -> None:
        if (
            type(self.source) is not int
            or not 1 <= self.source <= MAX_PAGE_COUNT
            or type(self.page) is not int
            or not 1 <= self.page <= MAX_PAGE_COUNT
            or type(self.rotation) is not int
            or self.rotation not in {0, 90, 180, 270}
        ):
            raise TraceRecipeError(TraceRecipeErrorCode.INVALID)


@dataclass(frozen=True, slots=True)
class TraceRecipe:
    """Portable structural PDF recipe. Bindings refer to explicitly selected inputs."""

    sources: tuple[TraceSourceBinding, ...]
    pages: tuple[TraceRecipePage, ...]
    unsupported_operations: tuple[TraceOperationKind, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.sources) is not tuple
            or len(self.sources) > MAX_PAGE_COUNT
            or any(type(source) is not TraceSourceBinding for source in self.sources)
            or type(self.pages) is not tuple
            or len(self.pages) > MAX_PAGE_COUNT
            or any(type(page) is not TraceRecipePage for page in self.pages)
            or type(self.unsupported_operations) is not tuple
            or any(
                type(kind) is not TraceOperationKind or kind in _AUTOMATION_OPERATIONS
                for kind in self.unsupported_operations
            )
            or len(set(self.unsupported_operations)) != len(self.unsupported_operations)
        ):
            raise TraceRecipeError(TraceRecipeErrorCode.INVALID)
        if (
            tuple(source.binding for source in self.sources)
            != tuple(range(1, len(self.sources) + 1))
            or sum(source.page_count for source in self.sources) > MAX_PAGE_COUNT
            or any(
                page.source > len(self.sources)
                or page.page > self.sources[page.source - 1].page_count
                for page in self.pages
            )
            or len({(page.source, page.page) for page in self.pages}) != len(self.pages)
        ):
            raise TraceRecipeError(TraceRecipeErrorCode.INVALID)

    def require_automation_supported(self) -> None:
        """Reject unresolved work before applying intent or writing destinations."""

        if self.unsupported_operations:
            raise TraceRecipeError(TraceRecipeErrorCode.UNSUPPORTED, self.unsupported_operations)
        if not self.pages:
            raise TraceRecipeError(TraceRecipeErrorCode.NO_PAGES)


def encode_trace_recipe(recipe: TraceRecipe) -> bytes:
    """Serialize only positional bindings and allowlisted operations."""

    return (
        json.dumps(
            {
                "recipe_version": TRACE_RECIPE_VERSION,
                "sources": [
                    {
                        "binding": source.binding,
                        "media_type": source.media_type.value,
                        "page_count": source.page_count,
                    }
                    for source in recipe.sources
                ],
                "pages": [
                    {"source": page.source, "page": page.page, "rotation": page.rotation}
                    for page in recipe.pages
                ],
                "output": "pdf",
                "unsupported_operations": [kind.value for kind in recipe.unsupported_operations],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise TraceRecipeError(TraceRecipeErrorCode.INVALID)
    return result


def decode_trace_recipe(payload: bytes) -> TraceRecipe:
    """Validate a bounded, declarative UTF-8 recipe before returning any intent."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_TRACE_RECIPE_BYTES:
        raise TraceRecipeError(TraceRecipeErrorCode.INVALID)
    try:
        raw: object = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_members)
        if type(raw) is not dict:
            raise TraceRecipeError(TraceRecipeErrorCode.INVALID)
        if (
            type(raw.get("recipe_version")) is not int
            or raw["recipe_version"] != TRACE_RECIPE_VERSION
        ):
            raise TraceRecipeError(TraceRecipeErrorCode.VERSION_UNSUPPORTED)
        if (
            set(raw) != {"recipe_version", "sources", "pages", "output", "unsupported_operations"}
            or raw["output"] != "pdf"
        ):
            raise TraceRecipeError(TraceRecipeErrorCode.INVALID)
        if (
            type(raw["sources"]) is not list
            or len(raw["sources"]) > MAX_PAGE_COUNT
            or type(raw["pages"]) is not list
            or len(raw["pages"]) > MAX_PAGE_COUNT
            or type(raw["unsupported_operations"]) is not list
            or len(raw["unsupported_operations"]) > len(TraceOperationKind)
        ):
            raise TraceRecipeError(TraceRecipeErrorCode.INVALID)
        sources: list[TraceSourceBinding] = []
        for source in raw["sources"]:
            if type(source) is not dict or set(source) != {"binding", "media_type", "page_count"}:
                raise TraceRecipeError(TraceRecipeErrorCode.INVALID)
            sources.append(
                TraceSourceBinding(
                    source["binding"], DetectedMediaType(source["media_type"]), source["page_count"]
                )
            )
        pages: list[TraceRecipePage] = []
        for page in raw["pages"]:
            if type(page) is not dict or set(page) != {"source", "page", "rotation"}:
                raise TraceRecipeError(TraceRecipeErrorCode.INVALID)
            pages.append(TraceRecipePage(page["source"], page["page"], page["rotation"]))
        return TraceRecipe(
            tuple(sources),
            tuple(pages),
            tuple(TraceOperationKind(kind) for kind in raw["unsupported_operations"]),
        )
    except TraceRecipeError:
        raise
    except ValueError, TypeError, RecursionError:
        raise TraceRecipeError(TraceRecipeErrorCode.INVALID) from None


@dataclass(frozen=True, slots=True)
class TraceSourceIdentity:
    """Catalogue identity and private display text for the case's left pane."""

    source_id: UUID
    binding: TraceSourceBinding
    display_name: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class TraceMarkConfiguration:
    """Geometry and configuration of an active mark without its private content."""

    page_id: UUID
    kind: str
    box: NormalizedPageBox
    version: int
    layer: int | None = None


@dataclass(frozen=True, slots=True)
class TracePackageConfiguration:
    """Saved package policy without user-authored filenames or assignment labels."""

    version: int
    allowed_modes: tuple[str, ...]
    encryption_policy: str
    max_files: int | None
    max_total_bytes: int | None
    max_file_bytes: int | None
    max_pages_per_file: int | None
    assignment_count: int
    requested_name_count: int


@dataclass(frozen=True, slots=True)
class TraceOperation:
    """One projected operation with stable targets and effective parameters."""

    kind: TraceOperationKind
    page_ids: tuple[UUID, ...]
    depends_on: tuple[TraceOperationKind, ...]
    parameters: tuple[int, ...] = ()
    marks: tuple[TraceMarkConfiguration, ...] = field(default=(), repr=False)
    package: TracePackageConfiguration | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class TracePlan:
    """Effective intent derived from case state, independent of undo history."""

    recipe: TraceRecipe
    sources: tuple[TraceSourceIdentity, ...]
    operations: tuple[TraceOperation, ...]
    fingerprint: str = field(repr=False)


def trace_source_identities(document: Mapping[str, object]) -> tuple[TraceSourceIdentity, ...]:
    return tuple(
        TraceSourceIdentity(
            UUID(cast(str, source["source_id"])),
            TraceSourceBinding(
                slot,
                DetectedMediaType(cast(str, source["detected_media_type"])),
                cast(int, source["page_count"]),
            ),
            cast(str, source["display_name"]),
        )
        for slot, source in enumerate(cast(list[dict[str, object]], document["sources"]), start=1)
    )


def _inspect_trace_mark(
    record: Mapping[str, object], operation: TraceOperationKind
) -> TraceMarkConfiguration:
    overlay = operation is TraceOperationKind.OVERLAY
    if overlay:
        payload = cast(Mapping[str, object], record["payload"])
        kind = cast(str, payload["kind"])
    else:
        kind = cast(str, record["state"])
    return TraceMarkConfiguration(
        page_id=UUID(cast(str, record["page_id"])),
        kind=kind,
        box=NormalizedPageBox.from_manifest(cast(Mapping[str, object], record["box"])),
        version=cast(int, record["overlay_version" if overlay else "mark_version"]),
        layer=cast(int, record["z_index"]) if overlay else None,
    )


def _inspect_trace_package(
    constraints: Mapping[str, object], assignment_count: int
) -> TracePackageConfiguration:
    return TracePackageConfiguration(
        version=cast(int, constraints["constraints_version"]),
        allowed_modes=tuple(cast(list[str], constraints["allowed_output_modes"])),
        encryption_policy=cast(str, constraints["encryption_policy"]),
        max_files=cast(int | None, constraints.get("maximum_file_count")),
        max_total_bytes=cast(int | None, constraints.get("maximum_total_bytes")),
        max_file_bytes=cast(int | None, constraints.get("maximum_file_bytes")),
        max_pages_per_file=cast(int | None, constraints.get("maximum_pages_per_file")),
        assignment_count=assignment_count,
        requested_name_count=len(cast(list[str], constraints.get("requested_output_names", []))),
    )


def build_trace_plan(document: Mapping[str, object], projection: OrganizerProjection) -> TracePlan:
    """Project only validated manifest metadata and follow current editable history."""

    sources = trace_source_identities(document)
    source_slots = {source.source_id: source.binding.binding for source in sources}
    page_states = {page.page_id: page for page in projection.pages}
    active = [page_states[page_id] for page_id in projection.page_sequence]
    active_ids = set(projection.page_sequence)
    recipe_pages = tuple(
        TraceRecipePage(
            source_slots[page.reference.source_id],
            page.reference.source_page_index + 1,
            page.quarter_turns_clockwise * 90,
        )
        for page in active
    )
    imported_order = tuple(page.page_id for page in projection.pages if page.active)
    steps: list[TraceOperation] = []
    if sources:
        steps.append(TraceOperation(TraceOperationKind.INPUT, imported_order, (), (len(sources),)))
    if len(active) != len(projection.pages):
        steps.append(
            TraceOperation(
                TraceOperationKind.SELECTION,
                imported_order,
                (TraceOperationKind.INPUT,),
                (len(active), len(projection.pages)),
            )
        )
    if imported_order != projection.page_sequence:
        steps.append(
            TraceOperation(TraceOperationKind.ORDER, projection.page_sequence, (steps[-1].kind,))
        )
    base = () if not steps else (steps[-1].kind,)
    groups: dict[tuple[TraceOperationKind, tuple[int, ...]], list[UUID]] = {}
    for page in active:
        if page.crop is not None:
            crop = page.crop
            groups.setdefault(
                (TraceOperationKind.CROP, (crop.x, crop.y, crop.width, crop.height)), []
            ).append(page.page_id)
        if page.quarter_turns_clockwise:
            groups.setdefault(
                (TraceOperationKind.ROTATION, (page.quarter_turns_clockwise * 90,)), []
            ).append(page.page_id)
        if page.deskew_angle_millidegrees_clockwise:
            groups.setdefault(
                (TraceOperationKind.DESKEW, (page.deskew_angle_millidegrees_clockwise,)), []
            ).append(page.page_id)
        if page.contrast_factor_millionths != NEUTRAL_CONTRAST_FACTOR_MILLIONTHS:
            groups.setdefault(
                (TraceOperationKind.CONTRAST, (page.contrast_factor_millionths,)), []
            ).append(page.page_id)
        if page.background_cleanup_strength_millionths:
            groups.setdefault(
                (TraceOperationKind.BACKGROUND, (page.background_cleanup_strength_millionths,)), []
            ).append(page.page_id)
    pipeline = tuple(TraceOperationKind)
    page_dependencies: dict[UUID, set[TraceOperationKind]] = {}
    for (kind, parameters), page_ids in sorted(
        groups.items(), key=lambda entry: (pipeline.index(entry[0][0]), entry[0][1])
    ):
        prior_kinds = {
            prior for page_id in page_ids for prior in page_dependencies.get(page_id, ())
        }
        dependencies = base + tuple(prior for prior in pipeline if prior in prior_kinds)
        steps.append(TraceOperation(kind, tuple(page_ids), dependencies, parameters))
        for page_id in page_ids:
            page_dependencies.setdefault(page_id, set()).add(kind)

    def add_marks(kind: TraceOperationKind, key: str) -> list[dict[str, object]]:
        selected = [
            item
            for item in cast(list[dict[str, object]], document.get(key, []))
            if UUID(cast(str, item["page_id"])) in active_ids
        ]
        if selected:
            targets = {UUID(cast(str, item["page_id"])) for item in selected}
            dependencies = tuple(dict.fromkeys(step.kind for step in steps))
            steps.append(
                TraceOperation(
                    kind,
                    tuple(page_id for page_id in projection.page_sequence if page_id in targets),
                    dependencies,
                    (len(selected),),
                    marks=tuple(_inspect_trace_mark(item, kind) for item in selected),
                )
            )
        return selected

    overlays = add_marks(TraceOperationKind.OVERLAY, "overlays")
    removal = add_marks(TraceOperationKind.REMOVAL, "redaction_marks")
    constraints = cast(Mapping[str, object], document.get("constraints", _DEFAULT_CONSTRAINTS))
    assignments = cast(list[object], document.get("assignments", []))
    package_intent = (
        document.get("template_ref") is not None
        or bool(assignments)
        or constraints != _DEFAULT_CONSTRAINTS
    )
    if package_intent:
        steps.append(
            TraceOperation(
                TraceOperationKind.PACKAGE,
                projection.page_sequence,
                base,
                package=_inspect_trace_package(constraints, len(assignments)),
            )
        )
    if active:
        dependencies = tuple(dict.fromkeys(step.kind for step in steps))
        steps.append(
            TraceOperation(TraceOperationKind.EXPORT, projection.page_sequence, dependencies)
        )
    recipe = TraceRecipe(
        tuple(source.binding for source in sources),
        recipe_pages,
        tuple(
            dict.fromkeys(step.kind for step in steps if step.kind not in _AUTOMATION_OPERATIONS)
        ),
    )
    # This hashes bounded manifest metadata, never the underlying documents. Ignore history,
    # revision, names and view preferences. Include active input digests and hidden edit payloads
    # without exposing them to the recipe file or to the presentation process.
    active_source_ids = {page.reference.source_id for page in active}
    transform_parameters = [
        (step.kind.value, tuple(map(str, step.page_ids)), step.parameters)
        for step in steps
        if step.kind
        in {
            TraceOperationKind.CROP,
            TraceOperationKind.DESKEW,
            TraceOperationKind.CONTRAST,
            TraceOperationKind.BACKGROUND,
        }
    ]
    source_facts = [
        (
            source["source_id"],
            source.get("sha256"),
            source.get("signature_state"),
            source.get("encryption_state"),
        )
        for source in cast(list[dict[str, object]], document["sources"])
        if UUID(cast(str, source["source_id"])) in active_source_ids
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                source_facts,
                [
                    (
                        str(page.page_id),
                        str(page.reference.source_id),
                        page.reference.source_page_index,
                        page.quarter_turns_clockwise,
                    )
                    for page in active
                ],
                transform_parameters,
                overlays,
                removal,
                document.get("template_ref"),
                document.get("assignments"),
                constraints,
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return TracePlan(recipe, sources, tuple(steps), fingerprint)


def trace_recipe_sequence_commands(
    current: tuple[UUID, ...], target: tuple[UUID, ...]
) -> tuple[SequenceChangeCommand, ...]:
    """Translate selection, restoration and order into existing validated commands."""

    target_set = set(target)
    retained = tuple(page_id for page_id in current if page_id in target_set)
    retained_set = set(retained)
    restored = retained + tuple(page_id for page_id in target if page_id not in retained_set)
    commands: list[SequenceChangeCommand] = []
    if retained != current:
        commands.append(SequenceChangeCommand(SequenceChangeReason.REMOVE, retained))
    if restored != retained:
        commands.append(SequenceChangeCommand(SequenceChangeReason.RESTORE, restored))
    if target != restored:
        commands.append(SequenceChangeCommand(SequenceChangeReason.REORDER, target))
    return tuple(commands)
