"""Prepare synthetic portal outputs and record local reader observations.

This developer-only harness parses only its own generated synthetic material. It
does not connect to a portal, run a server, or accept user documents as input.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import re
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID

import pikepdf
from PIL import Image, ImageDraw, ImageFont

from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import (
    PORTAL_PDF_EXPORT_PROFILE,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageEligibility,
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageRequest,
)
from paperworkshell.domain.pdf_inspection import PdfEncryptionState
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.overlays.exporting import FlattenedPdfExportPage, FlattenedPdfExportPlan
from paperworkshell.overlays.models import (
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    ShapePrimitive,
    SignatureImageOverlayPayload,
)
from paperworkshell.overlays.rendering import OverlayRenderLimits
from paperworkshell.pdf.export_adapter import PdfExportAdapter, PdfExportAdapterControl
from paperworkshell.pdf.flattened_export_adapter import FlattenedPdfExportAdapter
from paperworkshell.pdf.image_stage_adapter import PdfImageStageAdapter, PdfImageStageAdapterLimits
from paperworkshell.preflight.facts import (
    OutputItemPreflightFact,
    OutputPlanAvailability,
    OutputPlanPreflightFacts,
)
from paperworkshell.preflight.models import PreflightError
from paperworkshell.preflight.portal_constraints import (
    PortalArchiveObservation,
    PortalConstraintEvaluation,
    PortalConstraints,
    PortalEncryptionPolicy,
    PortalOutputMode,
    PortalOutputObservation,
    evaluate_portal_constraints,
    normalize_output_filename,
)
from tools.pws0307_overlay_reader_corpus import (
    _adapter_limits,
    _configured_qpdf_checker,
    _mark_record,
    _shape_record,
    _signature_asset,
    _text_record,
)

ROOT: Final = Path(__file__).resolve().parents[1]
TASK_ID: Final = "PWS-0506"
VERSION: Final = 1
MAX_EVIDENCE_BYTES: Final = 128 * 1024
MAX_ARTIFACT_BYTES: Final = 32 * 1024 * 1024
MAX_ATTEMPTS: Final = 99
SLOTS: Final = {
    "W1": "Adobe desktop Reader/free/reduced mode",
    "W2": "Microsoft Edge built-in PDF viewer",
    "W3": "Google Chrome built-in PDF viewer",
    "W4": "Mozilla Firefox built-in PDF viewer",
    "A1": "Adobe Acrobat Reader for Android",
}
CHECKS: Final = (
    "correct_viewer",
    "all_pages_and_order",
    "all_visual_targets_and_zooms",
    "three_text_searches",
    "no_document_interactivity",
    "close_and_reopen",
)
PDF_NAMES: Final = (
    "portal-baseline.pdf",
    "portal-preserve.pdf",
    "portal-grayscale.pdf",
    "portal-part-1.pdf",
    "portal-part-2.pdf",
    "portal-part-3.pdf",
)
ARTIFACT_NAMES: Final = (*PDF_NAMES, "portal-parts.zip")
EXPECTED_ARTIFACT_HASHES: Final = {
    "portal-baseline.pdf": "47dfe39762bebdf7173160e032f1fa9400d8bd96231954652e6e275a978757f0",
    "portal-preserve.pdf": "8cf1b7523052345594e773b62b228e03db9cb35ac944122f1b1663b79c12ecd9",
    "portal-grayscale.pdf": "72597065cfd6cfc6485d87fba2014576f9e9e1f60e71d1596689f0830063f042",
    "portal-part-1.pdf": "291d22e15930712bd49b6707e5d97f641067a7e9b121c6fa93216bb2a11a00a6",
    "portal-part-2.pdf": "849be969231cfa295933e88cdc9e1c14302109d3027352d3152635a41b16a368",
    "portal-part-3.pdf": "00338a450499af5a8286d022f208e87d69a4a94f790d0b96885fbf8ef176c55e",
    "portal-parts.zip": "82781419dc2aa500c6ed94aaadc74e943046d604fe3b9b98ff989a81fa357d35",
}
_HEX: Final = re.compile(r"[0-9a-f]{64}\Z")
_VERSION: Final = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,8}){0,4}\Z")


class CompatibilityError(RuntimeError):
    """Closed diagnostic; no filesystem or reader-input values are echoed."""

    def __init__(self, code: str) -> None:
        self.code = f"PWS0506_{code}"
        super().__init__(self.code)


def _uuid(group: int, index: int = 1) -> UUID:
    return UUID(f"75060{group:03x}-0000-4000-8000-{index:012x}")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise CompatibilityError("FILE_INVALID")
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise CompatibilityError("FILE_LIMIT")
    return payload


def provenance() -> dict[str, str]:
    """Bind dirty implementation bytes as well as the base commit, without paths."""

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, check=True, timeout=10
    )
    revision = result.stdout.decode("ascii").strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise CompatibilityError("REVISION_INVALID")
    digest = hashlib.sha256()
    paths = sorted((ROOT / "src").rglob("*.py")) + [
        Path(__file__).resolve(),
        ROOT / "tools/pws0307_overlay_reader_corpus.py",
        ROOT / "uv.lock",
    ]
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(_read(path, MAX_ARTIFACT_BYTES))
    return {"base_revision": revision, "implementation_sha256": digest.hexdigest()}


def _synthetic_source() -> bytes:
    """Three full-size pages; all searchable source text is an embedded overlay."""

    with pikepdf.Pdf.new() as document:
        for index in range(3):
            page = document.add_blank_page(page_size=(612, 792))
            # Large reference border and centered axes remain obvious at Fit Page.
            commands = b"0.5 G 2 w 36 36 540 720 re S\n"
            if index == 1:
                page.obj.Rotate = 180
                commands += b"0.75 G 1 w 306 200 m 306 592 l S 150 396 m 462 396 l S\n"
            page.obj.Contents = document.make_stream(commands)
        page = document.pages[2]
        with Image.new("RGB", (1800, 2250), "white") as raster:
            draw = ImageDraw.Draw(raster)
            for y in range(0, 2250, 6):
                draw.line((0, y, 1800, y), fill=(245, 245, 245), width=1)
            for position, text, size in (
                ((80, 100), "SYNTHETIC SCAN", 110),
                ((80, 330), "Reference: DEMO-2048", 88),
                ((80, 520), "Amount: 1234.56 units", 88),
                ((80, 760), "Small print 0123456789", 48),
                ((80, 920), "No personal data", 72),
            ):
                draw.text(position, text, fill="black", font=ImageFont.load_default(size=size))
            draw.rectangle((80, 1150, 800, 1380), fill=(25, 70, 190))
            draw.rectangle((1000, 1150, 1720, 1380), fill=(20, 135, 70))
            for x, text in ((140, "OPTION A"), (1060, "OPTION B")):
                draw.text((x, 1220), text, fill="white", font=ImageFont.load_default(size=85))
            draw.line(
                (100, 1840, 330, 1590, 510, 1810, 690, 1640, 950, 1770),
                fill=(100, 20, 100),
                width=14,
            )
            draw.ellipse((1110, 1590, 1700, 1990), outline=(170, 20, 40), width=14)
            draw.text(
                (1210, 1730), "DEMO", fill=(170, 20, 40), font=ImageFont.load_default(size=100)
            )
            with io.BytesIO() as encoded:
                raster.save(encoded, format="JPEG", quality=98, subsampling=0)
                pixels = document.make_stream(encoded.getvalue())
        pixels.Type, pixels.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        pixels.Width, pixels.Height = 1800, 2250
        pixels.BitsPerComponent, pixels.ColorSpace = 8, pikepdf.Name.DeviceRGB
        pixels.Filter = pikepdf.Name.DCTDecode
        page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=pixels))
        page.obj.Contents = document.make_stream(
            b"q 432 0 0 540 90 126 cm /Scan Do Q\n0 0 1 RG 3 w 87 123 438 546 re S\n"
        )
        with io.BytesIO() as output:
            document.save(output, deterministic_id=True)
            return output.getvalue()


def _plan(payload: bytes, *, indexes: tuple[int, ...] = (0, 1, 2)) -> PdfExportPlan:
    return PdfExportPlan(
        case_id=_uuid(0),
        manifest_revision=1,
        manifest_sha256=_sha(b"pws0506-corpus-v1"),
        manifest_cas_token="pws0506-corpus-v1",
        sources=(
            PdfExportSource(
                _uuid(1),
                len(payload),
                3,
                PdfExportSourceSignatureState.NONE_DETECTED,
                _sha(payload),
            ),
        ),
        pages=tuple(
            PdfExportPage(i, _uuid(2, index + 1), _uuid(1), index, PdfExportRotation.DEGREES_0)
            for i, index in enumerate(indexes)
        ),
        profile=PORTAL_PDF_EXPORT_PROFILE,
        plan_version=PORTAL_PDF_EXPORT_PROFILE.profile_version,
    )


def build_corpus_plan(payload: bytes) -> FlattenedPdfExportPlan:
    base = _plan(payload)
    signature, _ = _signature_asset()
    pages: list[FlattenedPdfExportPage] = []
    titles = (
        "PORTAL CORPUS - TEXT AND MARKS",
        "ROTATED CROP - CENTERED TARGET",
        "SCAN - CHECK DETAILS AND COLORS",
    )
    for index, page in enumerate(base.pages):
        records = [
            _text_record(
                _uuid(3, index * 10 + 1),
                page.page_id,
                OverlayKind.TEXT,
                titles[index],
                NormalizedPageBox(80_000, 65_000, 840_000, 70_000),
                0,
                size=20,
            )
        ]
        if index == 2:
            # The supported image-stage grammar excludes executed Forms. Keep
            # the scan title in its pixels so page 3 remains an eligible image
            # page, while pages 1 and 2 exercise flattened overlay Forms.
            records = []
        if index == 0:
            for ordinal, kind, text, y in (
                (2, OverlayKind.TEXT, "Café - Ångström - naïve", 190_000),
                (3, OverlayKind.DATE, "2026-10-01", 300_000),
                (4, OverlayKind.INITIALS, "PWS", 410_000),
            ):
                records.append(
                    _text_record(
                        _uuid(3, ordinal),
                        page.page_id,
                        kind,
                        text,
                        NormalizedPageBox(120_000, y, 760_000, 70_000),
                        ordinal,
                        size=28,
                    )
                )
            records.extend(
                (
                    _mark_record(
                        _uuid(3, 5),
                        page.page_id,
                        OverlayKind.CHECKMARK,
                        NormalizedPageBox(160_000, 540_000, 180_000, 140_000),
                        5,
                        RgbaColor("#0060ffff"),
                    ),
                    _mark_record(
                        _uuid(3, 6),
                        page.page_id,
                        OverlayKind.CROSS,
                        NormalizedPageBox(640_000, 540_000, 180_000, 140_000),
                        6,
                        RgbaColor("#c02020ff"),
                    ),
                    OverlayRecord(
                        _uuid(3, 7),
                        page.page_id,
                        NormalizedPageBox(250_000, 760_000, 500_000, 100_000),
                        7,
                        SignatureImageOverlayPayload(signature.asset_id),
                    ),
                )
            )
        elif index == 1:
            records.append(
                _shape_record(
                    _uuid(3, 12),
                    page.page_id,
                    OverlayKind.SHAPE,
                    ShapePrimitive.ELLIPSE,
                    NormalizedPageBox(250_000, 350_000, 500_000, 300_000),
                    1,
                    RgbaColor("#d8e8ff80"),
                    RgbaColor("#204080ff"),
                )
            )
            records.append(
                _shape_record(
                    _uuid(3, 13),
                    page.page_id,
                    OverlayKind.HIGHLIGHT,
                    ShapePrimitive.RECTANGLE,
                    NormalizedPageBox(150_000, 750_000, 700_000, 80_000),
                    2,
                    RgbaColor("#ffe05070"),
                    RgbaColor("#806000ff"),
                )
            )
        pages.append(
            FlattenedPdfExportPage(
                page,
                NormalizedPageBox(50_000, 50_000, 900_000, 900_000)
                if index == 1
                else NormalizedPageBox.full_page(),
                tuple(records),
            )
        )
    return FlattenedPdfExportPlan(base_plan=base, pages=tuple(pages), assets=(signature,))


def _inventory(path: Path, pages: int) -> dict[str, object]:
    payload = _read(path, MAX_ARTIFACT_BYTES)
    return {"sha256": _sha(payload), "byte_size": len(payload), "page_count": pages}


def local_upload_checks(workspace: Path, inventory: dict[str, Any]) -> dict[str, object]:
    """Evaluate actual artifact bytes through the product's existing pure rules."""

    results: dict[str, object] = {}
    for mode in PortalOutputMode:
        names = (PDF_NAMES[0],) if mode is PortalOutputMode.COMBINED_PDF else PDF_NAMES[3:]
        facts = tuple(
            OutputItemPreflightFact(
                f"part-{i}",
                i,
                tuple(_uuid(4, i * 10 + p) for p in range(1, inventory[name]["page_count"] + 1)),
                inventory[name]["byte_size"],
            )
            for i, name in enumerate(names, 1)
        )
        outputs = tuple(
            PortalOutputObservation(
                item.output_key, item.position, name, PdfEncryptionState.UNENCRYPTED
            )
            for item, name in zip(facts, names, strict=True)
        )
        total = (
            inventory["portal-parts.zip"]["byte_size"]
            if mode is PortalOutputMode.ZIP
            else sum(inventory[name]["byte_size"] for name in names)
        )
        file_max = max(inventory[name]["byte_size"] for name in names)
        if mode is PortalOutputMode.ZIP:
            file_max = max(file_max, total)
        constraints = PortalConstraints(
            (mode,),
            PortalEncryptionPolicy.MUST_BE_UNENCRYPTED,
            1 if mode is PortalOutputMode.ZIP else len(names),
            total,
            file_max,
            max(inventory[n]["page_count"] for n in names),
        )
        evaluation = PortalConstraintEvaluation(
            constraints,
            OutputPlanPreflightFacts(
                _uuid(0), 1, OutputPlanAvailability.CURRENT, mode.value, facts
            ),
            outputs,
            PortalArchiveObservation("portal-parts.zip", total)
            if mode is PortalOutputMode.ZIP
            else None,
        )
        cases = {
            "exact": evaluation,
            "total_one_byte_lower": replace(
                evaluation, constraints=replace(constraints, maximum_total_bytes=total - 1)
            ),
            "file_one_byte_lower": replace(
                evaluation, constraints=replace(constraints, maximum_file_bytes=file_max - 1)
            ),
            "encrypted_observation": replace(
                evaluation,
                outputs=(
                    replace(outputs[0], encryption_state=PdfEncryptionState.ENCRYPTED),
                    *outputs[1:],
                ),
            ),
            "wrong_extension": replace(
                evaluation, outputs=(replace(outputs[0], filename="part.txt"), *outputs[1:])
            ),
            "disallowed_mode": replace(
                evaluation,
                constraints=replace(
                    constraints,
                    allowed_modes=(
                        PortalOutputMode.ZIP
                        if mode is not PortalOutputMode.ZIP
                        else PortalOutputMode.COMBINED_PDF,
                    ),
                ),
            ),
        }
        if len(names) > 1 and mode is not PortalOutputMode.ZIP:
            cases["count_one_lower"] = replace(
                evaluation, constraints=replace(constraints, maximum_file_count=len(names) - 1)
            )
        if constraints.maximum_pages_per_file and constraints.maximum_pages_per_file > 1:
            cases["pages_one_lower"] = replace(
                evaluation,
                constraints=replace(
                    constraints, maximum_pages_per_file=constraints.maximum_pages_per_file - 1
                ),
            )
        expected = {
            "total_one_byte_lower": "PORTAL_TOTAL_SIZE_EXCEEDED",
            "file_one_byte_lower": "PORTAL_FILE_SIZE_EXCEEDED",
            "encrypted_observation": "PORTAL_ENCRYPTION_DETECTED",
            "wrong_extension": "PORTAL_EXTENSION_INVALID",
            "disallowed_mode": "PORTAL_OUTPUT_MODE_NOT_ALLOWED",
            "count_one_lower": "PORTAL_FILE_COUNT_EXCEEDED",
            "pages_one_lower": "PORTAL_PAGE_COUNT_EXCEEDED",
        }
        for label, case in cases.items():
            codes = sorted({item.code for item in evaluate_portal_constraints(case)})
            passed = not codes if label == "exact" else expected[label] in codes
            if not passed:
                raise CompatibilityError("UPLOAD_CHECK_FAILED")
            results[f"{mode.value}.{label}"] = {"result": "pass", "finding_codes": codes}
    try:
        normalize_output_filename("../unsafe.pdf")
    except PreflightError:
        results["unsafe_filename"] = {"result": "pass"}
    else:
        raise CompatibilityError("UPLOAD_CHECK_FAILED")
    with zipfile.ZipFile(workspace / "portal-parts.zip") as archive:
        if archive.namelist() != list(PDF_NAMES[3:]):
            raise CompatibilityError("ZIP_CHECK_FAILED")
        for info in archive.infolist():
            if (
                info.compress_type != zipfile.ZIP_STORED
                or info.file_size != inventory[info.filename]["byte_size"]
                or info.file_size > MAX_ARTIFACT_BYTES
            ):
                raise CompatibilityError("ZIP_CHECK_FAILED")
        if archive.testzip() is not None:
            raise CompatibilityError("ZIP_CHECK_FAILED")
        for name in archive.namelist():
            if _sha(archive.read(name)) != inventory[name]["sha256"]:
                raise CompatibilityError("ZIP_CHECK_FAILED")
    results["zip_exact_members_crc"] = {"result": "pass"}
    return results


def generate_corpus(workspace: Path) -> dict[str, object]:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    deadline = time.monotonic() + policy.worker_cpu_time_ms / 1000
    checker = _configured_qpdf_checker(deadline)
    limits = _adapter_limits(policy, 3)
    control = PdfExportAdapterControl(deadline=deadline)
    payload = _synthetic_source()
    plan = build_corpus_plan(payload)
    signature, raster = _signature_asset()
    baseline = workspace / PDF_NAMES[0]
    with io.BytesIO(payload) as source:
        report = (
            FlattenedPdfExportAdapter(qpdf_checker=checker)
            .assemble_and_verify(
                plan,
                {_uuid(1): source},
                {signature.asset_id: raster},
                baseline,
                limits,
                OverlayRenderLimits.from_resource_policy(policy),
                control,
            )
            .base_report
        )
    inventory = {PDF_NAMES[0]: _inventory(baseline, 3)}
    stage_limits = PdfImageStageAdapterLimits(
        limits,
        policy.worker_process_memory_bytes // 2,
        policy.maximum_temporary_storage_bytes // 2,
        limits.inspection.maximum_output_bytes,
        4_050_000,
    )
    stages: dict[str, object] = {}
    adapter = PdfImageStageAdapter(qpdf_checker=checker)
    analysis = adapter.process(
        baseline,
        workspace / "analysis-unused.pdf",
        baseline_report=report,
        request=PdfImageStageRequest(),
        limits=stage_limits,
        control=control,
    )
    selected = tuple(
        asset.asset_id
        for asset in analysis.analysis.assets
        if asset.eligibility is PdfImageEligibility.ELIGIBLE and asset.page_indexes == (2,)
    )
    if not selected:
        raise CompatibilityError("SCAN_NOT_ELIGIBLE")
    for name, profile in (
        (PDF_NAMES[1], PdfImageStageProfile.PRESERVE_240_DPI_Q85),
        (PDF_NAMES[2], PdfImageStageProfile.GRAYSCALE_240_DPI_Q85),
    ):
        stage_path = workspace / f"stage-{name}"
        result = adapter.process(
            baseline,
            stage_path,
            baseline_report=report,
            request=PdfImageStageRequest(
                profile, selected, report.output_sha256, report.semantic_fingerprint
            ),
            limits=stage_limits,
            control=control,
        )
        if result.report.outcome is not PdfImageStageOutcome.REENCODED:
            raise CompatibilityError("COMPRESSION_NOT_EXERCISED")
        stage_payload = _read(stage_path, MAX_ARTIFACT_BYTES)
        PdfExportAdapter(qpdf_checker=checker).assemble_and_verify(
            _plan(stage_payload), {_uuid(1): stage_path}, workspace / name, limits, control
        )
        stage_path.unlink()
        inventory[name] = _inventory(workspace / name, 3)
        stages[name] = {
            "profile": profile.value,
            "outcome": result.report.outcome.value,
            "verified_page_count": result.report.verified_page_count,
            "changed_pages": [3],
            "other_pages_render_equal": True,
        }
    baseline_payload = _read(baseline, MAX_ARTIFACT_BYTES)
    for index, name in enumerate(PDF_NAMES[3:]):
        PdfExportAdapter(qpdf_checker=checker).assemble_and_verify(
            _plan(baseline_payload, indexes=(index,)),
            {_uuid(1): baseline},
            workspace / name,
            limits,
            control,
        )
        inventory[name] = _inventory(workspace / name, 1)
    with zipfile.ZipFile(
        workspace / "portal-parts.zip", "x", compression=zipfile.ZIP_STORED
    ) as archive:
        for name in PDF_NAMES[3:]:
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o600 << 16
            archive.writestr(info, _read(workspace / name, MAX_ARTIFACT_BYTES))
    inventory["portal-parts.zip"] = _inventory(workspace / "portal-parts.zip", 3)
    return {
        "artifacts": inventory,
        "compression": stages,
        "local_upload_checks": local_upload_checks(workspace, inventory),
    }


def _workspace_safe(workspace: Path, allowed_root: Path) -> None:
    absolute = workspace.absolute()
    root = allowed_root.resolve()
    if absolute.resolve() == root or not absolute.resolve().is_relative_to(root):
        raise CompatibilityError("WORKSPACE_INVALID")
    for path in (absolute, *absolute.parents):
        if path.is_symlink() or path.is_junction():
            raise CompatibilityError("WORKSPACE_INVALID")
        if path == root:
            break


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CompatibilityError("EVIDENCE_INVALID")
        result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read(path, MAX_EVIDENCE_BYTES), object_pairs_hook=_object_pairs)
    except ValueError, UnicodeError:
        raise CompatibilityError("EVIDENCE_INVALID") from None
    if type(value) is not dict:
        raise CompatibilityError("EVIDENCE_INVALID")
    return cast(dict[str, Any], value)


def verify(workspace: Path, *, allowed_root: Path = ROOT) -> dict[str, Any]:
    _workspace_safe(workspace, allowed_root)
    evidence = _load(workspace / "evidence.json")
    if (
        evidence.get("task_id") != TASK_ID
        or evidence.get("version") != VERSION
        or evidence.get("provenance", {}).get("implementation_sha256")
        != provenance()["implementation_sha256"]
        or type(evidence.get("artifacts")) is not dict
        or set(evidence["artifacts"]) != set(ARTIFACT_NAMES)
    ):
        raise CompatibilityError("EVIDENCE_MISMATCH")
    for name, item in evidence["artifacts"].items():
        expected_pages = 1 if name in PDF_NAMES[3:] else 3
        if (
            item != _inventory(workspace / name, expected_pages)
            or item["sha256"] != EXPECTED_ARTIFACT_HASHES[name]
        ):
            raise CompatibilityError("ARTIFACT_MISMATCH")
    if local_upload_checks(workspace, evidence["artifacts"]) != evidence.get("local_upload_checks"):
        raise CompatibilityError("EVIDENCE_MISMATCH")
    return evidence


def prepare(
    workspace: Path | None = None,
    *,
    allowed_root: Path = ROOT,
    generator: Callable[[Path], dict[str, object]] = generate_corpus,
) -> Path:
    source = provenance()
    destination = (
        workspace
        or ROOT / "output/pws0506-portal-compatibility" / source["implementation_sha256"][:12]
    )
    _workspace_safe(destination, allowed_root)
    if (destination / "evidence.json").exists():
        verify(destination, allowed_root=allowed_root)
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise CompatibilityError("WORKSPACE_INCOMPLETE")
    generated = generator(destination)
    evidence = {
        "task_id": TASK_ID,
        "version": VERSION,
        "provenance": source,
        "profile_version": PORTAL_PDF_EXPORT_PROFILE.profile_version,
        "manual_reader_matrix": {slot: "NOT RUN" for slot in SLOTS},
        "claim_boundaries": {"portal_acceptance": False, "android_app_support": False},
        **generated,
    }
    with (destination / "evidence.json").open("xb") as stream:
        stream.write(_json_bytes(evidence))
    verify(destination, allowed_root=allowed_root)
    return destination


def _attempt_valid(record: dict[str, Any], evidence_sha256: str, slot: str) -> None:
    keys = {
        "version",
        "slot",
        "viewer",
        "viewer_version",
        "os_version",
        "evidence_sha256",
        "tested_at_utc",
        "operator",
        "checks",
        "result",
    }
    if (
        set(record) != keys
        or record["version"] != VERSION
        or record["slot"] != slot
        or record["viewer"] != SLOTS[slot]
        or record["evidence_sha256"] != evidence_sha256
        or record["operator"] not in {"human", "codex_computer_use"}
        or type(record["viewer_version"]) is not str
        or not _VERSION.fullmatch(record["viewer_version"])
        or type(record["os_version"]) is not str
        or not _VERSION.fullmatch(record["os_version"])
        or type(record["checks"]) is not dict
        or set(record["checks"]) != set(CHECKS)
        or any(type(value) is not bool for value in record["checks"].values())
        or record["result"] != ("PASS" if all(record["checks"].values()) else "FAIL")
        or type(record["tested_at_utc"]) is not str
        or not re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["tested_at_utc"])
    ):
        raise CompatibilityError("READER_RECORD_INVALID")


def record(
    workspace: Path,
    slot: str,
    version: str,
    os_version: str,
    checks: dict[str, bool],
    *,
    allowed_root: Path = ROOT,
    operator: str = "human",
) -> Path:
    verify(workspace, allowed_root=allowed_root)
    if slot not in SLOTS:
        raise CompatibilityError("READER_RECORD_INVALID")
    evidence_hash = _sha(_read(workspace / "evidence.json", MAX_EVIDENCE_BYTES))
    value = {
        "version": VERSION,
        "slot": slot,
        "viewer": SLOTS[slot],
        "viewer_version": version,
        "os_version": os_version,
        "evidence_sha256": evidence_hash,
        "tested_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "operator": operator,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
    }
    _attempt_valid(value, evidence_hash, slot)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        destination = workspace / f"{slot}-attempt-{attempt:02d}.json"
        try:
            with destination.open("xb") as stream:
                stream.write(_json_bytes(value))
            return destination
        except FileExistsError:
            continue
    raise CompatibilityError("ATTEMPT_LIMIT")


def status(workspace: Path, *, allowed_root: Path = ROOT) -> dict[str, str]:
    verify(workspace, allowed_root=allowed_root)
    evidence_hash = _sha(_read(workspace / "evidence.json", MAX_EVIDENCE_BYTES))
    results = dict.fromkeys(SLOTS, "NOT RUN")
    for slot in SLOTS:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            path = workspace / f"{slot}-attempt-{attempt:02d}.json"
            if path.exists():
                value = _load(path)
                _attempt_valid(value, evidence_hash, slot)
                results[slot] = value["result"]
    return results


TEST_CARD: Final = """Use only this prepared synthetic corpus. Keep files local.
Open portal-baseline.pdf in the named reader. Confirm 3 pages; navigate 1 > 2 > 3 > 2 > 1.
Inspect all pages at Fit Page and 200% (Android: fit and pinch enlargement sufficient
to read the small-print target).
Page 1: title, Café/Ångström/naïve, date, PWS, blue check, red cross, transparent visual mark.
Page 2: centered ellipse on crossed axes and highlight within the visible cropped page.
Page 3: readable reference/amount/small print, two colored labelled boxes, invented ink/DEMO.
Search baseline page 1 for Café, 2026-10-01, and PWS. All three must be found.
Tap/click the overlay regions: no field, script, action, attachment or editor should activate.
Close and reopen baseline in the same reader; revisit all 3 pages.
Open portal-preserve.pdf and portal-grayscale.pdf; confirm 3 pages and inspect changed page 3
at Fit Page and enlargement. Close/reopen each and revisit page 3. The native stage verifies
pages 1 and 2 unchanged; do not repeat their checks. Gray removes raster color while the
blue vector border and overlays retain their colors.
The separate PDFs and ZIP members are checked automatically against exact baseline pages.
W1 must be Adobe Reader/free/reduced; W2-W4 must use their own built-in PDF viewer.
A1 must be Adobe Acrobat Reader for Android. Transfer only these synthetic files locally;
keep their names and bytes unchanged. Do not use a cloud upload or send personal documents.
This records rendering observations, not successful portal submission or universal acceptance.
"""


def _version_input(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value.casefold() == "cancel":
            raise KeyboardInterrupt
        if _VERSION.fullmatch(value):
            return value
        print("Enter only the numeric version, or cancel.")


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?", choices=("prepare", "status", "record"), default="record"
    )
    args = parser.parse_args(argv)
    try:
        workspace = prepare()
        print("READY [PWS0506_COMPATIBILITY_READY]")
        print(f"Synthetic files: {workspace}")
        print(f"Evidence SHA-256: {_sha(_read(workspace / 'evidence.json', MAX_EVIDENCE_BYTES))}")
        print(TEST_CARD)
        while True:
            result = status(workspace)
            for slot, state in result.items():
                print(f"{slot} {SLOTS[slot]}: {state}")
            print(
                "MATRIX READY FOR REVIEW"
                if all(value == "PASS" for value in result.values())
                else "MATRIX INCOMPLETE: pending or failed reader evidence remains."
            )
            if args.command != "record":
                return 0
            slot = input("Choose W1/W2/W3/W4/A1, S for status, or Q to stop: ").strip().upper()
            if slot == "Q":
                return 0
            if slot not in SLOTS:
                continue
            print(SLOTS[slot])
            version = _version_input("Reader About numeric version (or cancel): ")
            os_version = (
                _version_input("Android numeric version (or cancel): ")
                if slot == "A1"
                else platform.version()
            )
            answers: dict[str, bool] = {}
            for check in CHECKS:
                while True:
                    answer = (
                        input(f"{check.replace('_', ' ')} for ALL required files? y/n: ")
                        .strip()
                        .lower()
                    )
                    if answer in {"y", "n"}:
                        answers[check] = answer == "y"
                        break
                    print("Enter y or n; Ctrl+C preserves previous results.")
            record(workspace, slot, version, os_version, answers)
            print("Result recorded; existing attempts were preserved.")
    except KeyboardInterrupt:
        print("Stopped. Existing files and reader attempts were preserved.")
        return 0
    except Exception as error:
        code = error.code if isinstance(error, CompatibilityError) else "PWS0506_PREPARATION_FAILED"
        print(f"ERROR [{code}] Preserve the workspace and report only this code.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
