"""Recipe-only labeled corpus and sanitized metrics for PWS-0207.

The corpus creates opaque RGBA observations in memory.  It never writes a
document or image and its baseline contains only closed identifiers, counts,
rates, versions, and profile/corpus digests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Final, Self, cast
from uuid import UUID

from paperworkshell.domain.quality_analysis import (
    DEFAULT_QUALITY_ANALYSIS_PROFILE,
    OcrReviewFact,
    OcrReviewStatus,
    QualityAnalysisProfile,
    QualityFindingCode,
    QualityPageAnalysis,
    QualityPageKind,
    QualityPageSample,
    analyze_quality_page,
    suspected_duplicate_finding,
)
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    RESOURCE_POLICY_VERSION,
)

CORPUS_ID: Final = "pws0207-quality-findings-corpus-v1"
CORPUS_VERSION: Final = 1
BASELINE_ID: Final = "pws0207-quality-findings-baseline"
BASELINE_VERSION: Final = 1
RESOURCE_PROFILE: Final = CONSTRAINED_RESOURCE_POLICY_V1.profile.value
MAXIMUM_BASELINE_BYTES: Final = 64 * 1024
MAXIMUM_GIT_OUTPUT_BYTES: Final = 64 * 1024
MAXIMUM_IMPLEMENTATION_SURFACE_BYTES: Final = 4 * 1024 * 1024
MAXIMUM_IMPLEMENTATION_TOTAL_BYTES: Final = 16 * 1024 * 1024
PARTS_PER_MILLION: Final = 1_000_000
GIT_VALIDATION_TIMEOUT_SECONDS: Final = 10.0
PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
QUALITY_IMPLEMENTATION_SURFACES: Final = (
    "src/paperworkshell/__main__.py",
    "src/paperworkshell/app/organizer.py",
    "src/paperworkshell/app/quality_analysis.py",
    "src/paperworkshell/app/quality_ocr_review.py",
    "src/paperworkshell/domain/quality_analysis.py",
    "src/paperworkshell/domain/quality_ocr_review.py",
    "src/paperworkshell/domain/resource_policy.py",
    "src/paperworkshell/ocr/tesseract_runner.py",
    "src/paperworkshell/ui/backend.py",
    "src/paperworkshell/ui/models.py",
    "src/paperworkshell/ui/window.py",
    "src/paperworkshell/worker/_windows_broker.py",
    "src/paperworkshell/worker/_windows_runtime.py",
    "src/paperworkshell/worker/client/_windows.py",
    "src/paperworkshell/worker/desktop_composition.py",
    "src/paperworkshell/worker/host.py",
    "src/paperworkshell/worker/ocr_composition.py",
    "src/paperworkshell/worker/protocol/codec.py",
    "src/paperworkshell/worker/protocol/models.py",
    "src/paperworkshell/worker/protocol/operation_scope.py",
    "src/paperworkshell/worker/protocol/state.py",
    "src/paperworkshell/worker/quality_ocr_review_broker.py",
    "src/paperworkshell/worker/quality_ocr_review_buffers.py",
    "src/paperworkshell/worker/quality_ocr_review_composition.py",
    "src/paperworkshell/worker/quality_ocr_review_operation.py",
    "src/paperworkshell/worker/searchable_pdf_export_composition.py",
    "tools/pws0207_quality_evaluation.py",
)

LOW_RESOLUTION: Final = "low_resolution"
BLUR: Final = "blur"
CLIPPING: Final = "clipping"
BLANK: Final = "blank"
ORIENTATION: Final = "orientation"
DUPLICATE: Final = "duplicate"
OCR_FAILED: Final = "ocr_failed"
OCR_LOW_CONFIDENCE: Final = "ocr_low_confidence"
FINDING_KINDS: Final = (
    LOW_RESOLUTION,
    BLUR,
    CLIPPING,
    BLANK,
    ORIENTATION,
    DUPLICATE,
    OCR_FAILED,
    OCR_LOW_CONFIDENCE,
)
UNARY_FINDING_KINDS: Final = tuple(kind for kind in FINDING_KINDS if kind != DUPLICATE)

_WIDTH: Final = 256
_HEIGHT: Final = 256
_ROW_STRIDE: Final = _WIDTH * 4
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class CorpusOcrObservation:
    """Content-free OCR facts used to evaluate the review rule."""

    status: OcrReviewStatus | None
    recognized_word_count: int = 0
    mean_confidence_milli: int | None = None

    def __post_init__(self) -> None:
        if (
            self.status is not None
            and type(self.status) is not OcrReviewStatus
            or type(self.recognized_word_count) is not int
            or not 0 <= self.recognized_word_count <= 1_000_000
            or (
                self.mean_confidence_milli is not None
                and (
                    type(self.mean_confidence_milli) is not int
                    or not 0 <= self.mean_confidence_milli <= 100_000
                )
            )
        ):
            raise ValueError("invalid corpus OCR observation")
        if self.status is OcrReviewStatus.SUCCEEDED:
            if self.recognized_word_count < 1:
                raise ValueError("successful OCR must contain a recognized word")
        elif self.recognized_word_count != 0 or self.mean_confidence_milli is not None:
            raise ValueError("only successful OCR can contain counts or confidence")


@dataclass(frozen=True, slots=True, repr=False)
class QualityCorpusCase:
    """One deterministic opaque raster plus its complete unary truth state."""

    case_id: str
    recipe_id: str
    source_width_pixels: int
    source_height_pixels: int
    pixels: bytes = field(repr=False)
    applicable_kinds: tuple[str, ...]
    positive_kinds: tuple[str, ...]
    ocr: CorpusOcrObservation = field(default_factory=lambda: CorpusOcrObservation(None))
    width_pixels: int = _WIDTH
    height_pixels: int = _HEIGHT
    row_stride_bytes: int = _ROW_STRIDE

    def __post_init__(self) -> None:
        applicable = frozenset(self.applicable_kinds)
        positives = frozenset(self.positive_kinds)
        if (
            _TOKEN.fullmatch(self.case_id) is None
            or _TOKEN.fullmatch(self.recipe_id) is None
            or type(self.source_width_pixels) is not int
            or type(self.source_height_pixels) is not int
            or self.source_width_pixels <= 0
            or self.source_height_pixels <= 0
            or type(self.pixels) is not bytes
            or len(self.pixels) != _ROW_STRIDE * _HEIGHT
            or any(self.pixels[index] != 255 for index in range(3, len(self.pixels), 4))
            or tuple(sorted(applicable)) != self.applicable_kinds
            or tuple(sorted(positives)) != self.positive_kinds
            or not applicable.issubset(UNARY_FINDING_KINDS)
            or not positives.issubset(applicable)
            or type(self.ocr) is not CorpusOcrObservation
            or self.width_pixels != _WIDTH
            or self.height_pixels != _HEIGHT
            or self.row_stride_bytes != _ROW_STRIDE
        ):
            raise ValueError("invalid quality corpus case")

    def __repr__(self) -> str:
        return (
            "QualityCorpusCase("
            f"case_id={self.case_id!r}, recipe_id={self.recipe_id!r}, "
            f"applicable_kinds={self.applicable_kinds!r}, "
            f"positive_kinds={self.positive_kinds!r})"
        )


@dataclass(frozen=True, slots=True)
class DuplicateCorpusPair:
    """Truth for one bounded visual-duplicate comparison."""

    pair_id: str
    left_case_id: str
    right_case_id: str
    is_duplicate: bool

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not str or _TOKEN.fullmatch(value) is None
                for value in (self.pair_id, self.left_case_id, self.right_case_id)
            )
            or self.left_case_id >= self.right_case_id
            or type(self.is_duplicate) is not bool
        ):
            raise ValueError("invalid duplicate corpus pair")


@dataclass(frozen=True, slots=True)
class FindingMetrics:
    """One exact binary confusion matrix and derived integer rates."""

    kind: str
    applicable: int
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision_ppm: int | None = field(init=False)
    recall_ppm: int | None = field(init=False)
    false_positive_rate_ppm: int | None = field(init=False)
    false_negative_rate_ppm: int | None = field(init=False)

    def __post_init__(self) -> None:
        counts = (
            self.true_positive,
            self.false_positive,
            self.true_negative,
            self.false_negative,
        )
        if (
            self.kind not in FINDING_KINDS
            or type(self.applicable) is not int
            or self.applicable <= 0
            or any(type(value) is not int or value < 0 for value in counts)
            or sum(counts) != self.applicable
        ):
            raise ValueError("invalid finding metrics")
        object.__setattr__(
            self,
            "precision_ppm",
            _rate(self.true_positive, self.true_positive + self.false_positive),
        )
        object.__setattr__(
            self,
            "recall_ppm",
            _rate(self.true_positive, self.true_positive + self.false_negative),
        )
        object.__setattr__(
            self,
            "false_positive_rate_ppm",
            _rate(self.false_positive, self.false_positive + self.true_negative),
        )
        object.__setattr__(
            self,
            "false_negative_rate_ppm",
            _rate(self.false_negative, self.false_negative + self.true_positive),
        )

    def as_document(self) -> dict[str, str | int | None]:
        return {
            "kind": self.kind,
            "applicable": self.applicable,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "true_negative": self.true_negative,
            "false_negative": self.false_negative,
            "precision_ppm": self.precision_ppm,
            "recall_ppm": self.recall_ppm,
            "false_positive_rate_ppm": self.false_positive_rate_ppm,
            "false_negative_rate_ppm": self.false_negative_rate_ppm,
        }


@dataclass(frozen=True, slots=True)
class QualityBaseline:
    """Sanitized checked-in evaluation result for one exact profile and corpus."""

    revision: str
    profile_id: str
    profile_version: int
    profile_sha256: str
    corpus_sha256: str
    metrics: tuple[FindingMetrics, ...]
    report_id: str = BASELINE_ID
    report_version: int = BASELINE_VERSION
    corpus_id: str = CORPUS_ID
    corpus_version: int = CORPUS_VERSION
    resource_profile: str = RESOURCE_PROFILE
    resource_policy_version: int = RESOURCE_POLICY_VERSION

    def __post_init__(self) -> None:
        if (
            _REVISION.fullmatch(self.revision) is None
            or _TOKEN.fullmatch(self.profile_id) is None
            or type(self.profile_version) is not int
            or self.profile_version <= 0
            or _SHA256.fullmatch(self.profile_sha256) is None
            or _SHA256.fullmatch(self.corpus_sha256) is None
            or tuple(metric.kind for metric in self.metrics) != FINDING_KINDS
            or any(type(metric) is not FindingMetrics for metric in self.metrics)
            or self.report_id != BASELINE_ID
            or self.report_version != BASELINE_VERSION
            or self.corpus_id != CORPUS_ID
            or self.corpus_version != CORPUS_VERSION
            or self.resource_profile != RESOURCE_PROFILE
            or self.resource_policy_version != RESOURCE_POLICY_VERSION
        ):
            raise ValueError("invalid quality baseline")

    def as_document(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "report_version": self.report_version,
            "corpus_id": self.corpus_id,
            "corpus_version": self.corpus_version,
            "corpus_sha256": self.corpus_sha256,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "profile_sha256": self.profile_sha256,
            "resource_profile": self.resource_profile,
            "resource_policy_version": self.resource_policy_version,
            "revision": self.revision,
            "metrics": [metric.as_document() for metric in self.metrics],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> Self:
        expected = {
            "report_id",
            "report_version",
            "corpus_id",
            "corpus_version",
            "corpus_sha256",
            "profile_id",
            "profile_version",
            "profile_sha256",
            "resource_profile",
            "resource_policy_version",
            "revision",
            "metrics",
        }
        if set(document) != expected or type(document.get("metrics")) is not list:
            raise ValueError("invalid quality baseline fields")
        metrics = tuple(
            _metric_from_document(value) for value in cast(list[object], document["metrics"])
        )
        try:
            return cls(
                revision=cast(str, document["revision"]),
                profile_id=cast(str, document["profile_id"]),
                profile_version=cast(int, document["profile_version"]),
                profile_sha256=cast(str, document["profile_sha256"]),
                corpus_sha256=cast(str, document["corpus_sha256"]),
                metrics=metrics,
                report_id=cast(str, document["report_id"]),
                report_version=cast(int, document["report_version"]),
                corpus_id=cast(str, document["corpus_id"]),
                corpus_version=cast(int, document["corpus_version"]),
                resource_profile=cast(str, document["resource_profile"]),
                resource_policy_version=cast(int, document["resource_policy_version"]),
            )
        except TypeError, ValueError:
            raise ValueError("invalid quality baseline values") from None


@cache
def build_quality_corpus() -> tuple[QualityCorpusCase, ...]:
    """Build the complete deterministic in-memory corpus."""

    visual = tuple(sorted((LOW_RESOLUTION, BLUR, CLIPPING, BLANK, ORIENTATION)))
    cases = (
        _case("sharp-normal", "sharp-lines-v1", _line_page(), visual, ()),
        _case(
            "lowres-clear",
            "sharp-lines-v1-lowres",
            _line_page(),
            visual,
            (LOW_RESOLUTION,),
            source_size=(640, 900),
        ),
        _case(
            "lowres-boundary-minus-one",
            "sharp-lines-v1-short-edge-999",
            _line_page(),
            visual,
            (LOW_RESOLUTION,),
            source_size=(999, 1400),
        ),
        _case(
            "resolution-boundary",
            "sharp-lines-v1-short-edge-1000",
            _line_page(),
            visual,
            (),
            source_size=(1000, 1400),
        ),
        _case(
            "blur-box", "line-page-box-blur-v1", _box_blur(_line_page(), passes=3), visual, (BLUR,)
        ),
        _case(
            "blur-motion", "line-page-motion-blur-v1", _motion_blur(_line_page()), visual, (BLUR,)
        ),
        _case("sharp-low-contrast", "low-contrast-lines-v1", _line_page(ink=180), visual, ()),
        _case("blank-white", "white-page-v1", _solid(255), visual, (BLANK,)),
        _case("blank-faint-noise", "faint-noise-v1", _faint_noise(), visual, (BLANK,)),
        _case("blank-sparse-mark", "sparse-mark-v1", _sparse_mark(), visual, ()),
        _case("clip-left", "left-clipped-lines-v1", _line_page(left=0), visual, (CLIPPING,)),
        _case(
            "clip-bottom", "bottom-clipped-lines-v1", _bottom_clipped_page(), visual, (CLIPPING,)
        ),
        _case("clip-near-margin", "one-pixel-margin-v1", _line_page(left=2), visual, ()),
        _case("clip-full-bleed", "full-bleed-confounder-v1", _full_bleed(), visual, ()),
        _case("orientation-zero", "line-page-rotation-0-v1", _line_page(), visual, ()),
        _case(
            "orientation-90",
            "line-page-rotation-90-v1",
            _rotate(_line_page(), 90),
            visual,
            (ORIENTATION,),
        ),
        _case(
            "orientation-180",
            "line-page-rotation-180-v1",
            _rotate(_line_page(), 180),
            visual,
            (ORIENTATION,),
        ),
        _case(
            "orientation-270",
            "line-page-rotation-270-v1",
            _rotate(_line_page(), 270),
            visual,
            (ORIENTATION,),
        ),
        _ocr_case(
            "ocr-failed",
            "ocr-failed-observation-v1",
            CorpusOcrObservation(OcrReviewStatus.FAILED),
            (OCR_FAILED,),
        ),
        _ocr_case(
            "ocr-zero-nonblank",
            "ocr-zero-word-observation-v1",
            CorpusOcrObservation(OcrReviewStatus.NO_TEXT),
            (OCR_FAILED,),
        ),
        _ocr_case(
            "ocr-low",
            "ocr-low-confidence-observation-v1",
            CorpusOcrObservation(OcrReviewStatus.SUCCEEDED, 4, 55_500),
            (OCR_LOW_CONFIDENCE,),
        ),
        _ocr_case(
            "ocr-high",
            "ocr-high-confidence-observation-v1",
            CorpusOcrObservation(OcrReviewStatus.SUCCEEDED, 4, 93_000),
            (),
        ),
        _case(
            "ocr-zero-blank",
            "ocr-zero-word-blank-observation-v1",
            _solid(255),
            tuple(sorted((BLANK, OCR_FAILED, OCR_LOW_CONFIDENCE))),
            (BLANK,),
            ocr=CorpusOcrObservation(OcrReviewStatus.NO_TEXT),
        ),
        _case("duplicate-exact-a", "duplicate-base-v1", _line_page(), (), ()),
        _case("duplicate-exact-b", "duplicate-base-v1-copy", _line_page(), (), ()),
        _case(
            "duplicate-bright",
            "duplicate-brightness-shift-v1",
            _brightness(_line_page(), 6),
            (),
            (),
        ),
        _case("duplicate-similar", "duplicate-similar-distinct-v1", _line_page(variant=1), (), ()),
        _case("duplicate-unrelated", "duplicate-unrelated-v1", _full_bleed(), (), ()),
    )
    _require_unique(case.case_id for case in cases)
    return cases


@cache
def build_duplicate_pairs() -> tuple[DuplicateCorpusPair, ...]:
    """Return the closed pairwise duplicate truth matrix."""

    pairs = (
        DuplicateCorpusPair("duplicate-pair-exact", "duplicate-exact-a", "duplicate-exact-b", True),
        DuplicateCorpusPair("duplicate-pair-bright", "duplicate-bright", "duplicate-exact-a", True),
        DuplicateCorpusPair(
            "duplicate-pair-similar", "duplicate-exact-a", "duplicate-similar", False
        ),
        DuplicateCorpusPair(
            "duplicate-pair-unrelated", "duplicate-exact-a", "duplicate-unrelated", False
        ),
    )
    _require_unique(pair.pair_id for pair in pairs)
    return pairs


def quality_corpus_sha256(
    cases: Sequence[QualityCorpusCase] | None = None,
    pairs: Sequence[DuplicateCorpusPair] | None = None,
) -> str:
    """Hash exact recipes, truth, OCR facts, and generated pixels without exposing them."""

    selected_cases = build_quality_corpus() if cases is None else tuple(cases)
    selected_pairs = build_duplicate_pairs() if pairs is None else tuple(pairs)
    document = {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "cases": [
            {
                "case_id": case.case_id,
                "recipe_id": case.recipe_id,
                "source_width_pixels": case.source_width_pixels,
                "source_height_pixels": case.source_height_pixels,
                "pixel_sha256": hashlib.sha256(case.pixels).hexdigest(),
                "applicable_kinds": list(case.applicable_kinds),
                "positive_kinds": list(case.positive_kinds),
                "ocr_status": None if case.ocr.status is None else case.ocr.status.value,
                "ocr_word_count": case.ocr.recognized_word_count,
                "ocr_mean_confidence_milli": case.ocr.mean_confidence_milli,
            }
            for case in selected_cases
        ],
        "duplicate_pairs": [
            {
                "pair_id": pair.pair_id,
                "left_case_id": pair.left_case_id,
                "right_case_id": pair.right_case_id,
                "is_duplicate": pair.is_duplicate,
            }
            for pair in selected_pairs
        ],
    }
    return hashlib.sha256(_canonical_json(document)).hexdigest()


def evaluate_predictions(
    unary_predictions: Mapping[str, Set[str]],
    duplicate_predictions: Mapping[str, bool],
    *,
    cases: Sequence[QualityCorpusCase] | None = None,
    pairs: Sequence[DuplicateCorpusPair] | None = None,
) -> tuple[FindingMetrics, ...]:
    """Evaluate one exact prediction set against the complete truth matrix."""

    selected_cases = build_quality_corpus() if cases is None else tuple(cases)
    selected_pairs = build_duplicate_pairs() if pairs is None else tuple(pairs)
    expected_case_ids = {case.case_id for case in selected_cases}
    if set(unary_predictions) != expected_case_ids:
        raise ValueError("prediction case inventory does not match the corpus")
    expected_pair_ids = {pair.pair_id for pair in selected_pairs}
    if set(duplicate_predictions) != expected_pair_ids:
        raise ValueError("prediction pair inventory does not match the corpus")
    for prediction in unary_predictions.values():
        if not isinstance(prediction, Set) or not set(prediction).issubset(FINDING_KINDS):
            raise ValueError("prediction contains an unknown finding kind")
    if any(type(value) is not bool for value in duplicate_predictions.values()):
        raise ValueError("duplicate predictions must be booleans")

    results: list[FindingMetrics] = []
    for kind in UNARY_FINDING_KINDS:
        truth_and_prediction = [
            (kind in case.positive_kinds, kind in unary_predictions[case.case_id])
            for case in selected_cases
            if kind in case.applicable_kinds
        ]
        results.append(_metrics(kind, truth_and_prediction))
    pair_states = [
        (pair.is_duplicate, duplicate_predictions[pair.pair_id]) for pair in selected_pairs
    ]
    duplicate_metrics = _metrics(DUPLICATE, pair_states)
    by_kind = {metric.kind: metric for metric in (*results, duplicate_metrics)}
    return tuple(by_kind[kind] for kind in FINDING_KINDS)


def evaluate_current_profile(
    *,
    profile: QualityAnalysisProfile = DEFAULT_QUALITY_ANALYSIS_PROFILE,
) -> tuple[FindingMetrics, ...]:
    """Run the current pure-domain analyzer against every labeled recipe."""

    if type(profile) is not QualityAnalysisProfile:
        raise ValueError("invalid quality-analysis profile")
    cases = build_quality_corpus()
    _require_constrained_resource_fit(cases)
    by_case: dict[str, QualityPageAnalysis] = {}
    unary: dict[str, frozenset[str]] = {}
    for position, case in enumerate(cases, start=1):
        page_id = _case_uuid(position)
        analysis = analyze_quality_page(
            QualityPageSample(
                page_id=page_id,
                page_position=position,
                page_kind=QualityPageKind.IMAGE,
                width_pixels=case.width_pixels,
                height_pixels=case.height_pixels,
                row_stride_bytes=case.row_stride_bytes,
                pixels=case.pixels,
                source_width_pixels=case.source_width_pixels,
                source_height_pixels=case.source_height_pixels,
            ),
            ocr_fact=_domain_ocr_fact(case.ocr, page_id),
            profile=profile,
        )
        by_case[case.case_id] = analysis
        unary[case.case_id] = frozenset(
            _finding_kind(finding.code) for finding in analysis.findings
        )
    duplicates: dict[str, bool] = {}
    for pair in build_duplicate_pairs():
        left = by_case[pair.left_case_id]
        right = by_case[pair.right_case_id]
        duplicates[pair.pair_id] = (
            suspected_duplicate_finding(
                left.fingerprint,
                right.fingerprint,
                profile=profile,
            )
            is not None
        )
    return evaluate_predictions(unary, duplicates, cases=cases, pairs=build_duplicate_pairs())


def build_current_baseline(revision: str) -> QualityBaseline:
    """Build sanitized evidence for the exact current profile and labeled corpus."""

    profile = DEFAULT_QUALITY_ANALYSIS_PROFILE
    return QualityBaseline(
        revision=revision,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_sha256=profile.profile_sha256,
        corpus_sha256=quality_corpus_sha256(),
        metrics=evaluate_current_profile(profile=profile),
    )


def encode_quality_baseline(value: QualityBaseline) -> bytes:
    """Encode canonical bounded baseline JSON."""

    if type(value) is not QualityBaseline:
        raise ValueError("invalid quality baseline")
    payload = _canonical_json(value.as_document())
    if len(payload) > MAXIMUM_BASELINE_BYTES:
        raise ValueError("quality baseline exceeds its byte limit")
    return payload


def decode_quality_baseline(payload: bytes) -> QualityBaseline:
    """Decode a closed canonical baseline without accepting duplicate keys or floats."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAXIMUM_BASELINE_BYTES:
        raise ValueError("invalid quality baseline bytes")
    try:
        decoded = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_int=_bounded_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except UnicodeDecodeError, json.JSONDecodeError, ValueError:
        raise ValueError("invalid quality baseline JSON") from None
    if type(decoded) is not dict:
        raise ValueError("invalid quality baseline root")
    value = QualityBaseline.from_document(cast(dict[str, object], decoded))
    if encode_quality_baseline(value) != payload:
        raise ValueError("quality baseline is not canonical")
    return value


def load_quality_baseline(path: Path) -> QualityBaseline:
    """Load one caller-selected checked-in baseline with an exact byte cap."""

    if not isinstance(path, Path):
        raise ValueError("baseline path must be a Path")
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAXIMUM_BASELINE_BYTES + 1)
    except OSError:
        raise ValueError("quality baseline is unavailable") from None
    return decode_quality_baseline(payload)


def record_current_baseline(path: Path, revision: str) -> QualityBaseline:
    """Atomically create evidence for one verified implementation revision."""

    if not isinstance(path, Path):
        raise ValueError("baseline path must be a Path")
    validate_implementation_revision(revision)
    baseline = build_current_baseline(revision)
    payload = encode_quality_baseline(baseline)
    _write_baseline_no_replace(path, payload)
    return baseline


def validate_current_baseline(
    path: Path,
    *,
    expected_revision: str | None = None,
) -> QualityBaseline:
    """Require a baseline to match the exact current corpus, profile, and metrics."""

    if expected_revision is not None and _REVISION.fullmatch(expected_revision) is None:
        raise ValueError("expected revision is invalid")
    baseline = load_quality_baseline(path)
    if (
        expected_revision is not None
        and baseline.revision != expected_revision
        or baseline != build_current_baseline(baseline.revision)
    ):
        raise ValueError("quality baseline does not match the current evaluator")
    validate_implementation_revision(baseline.revision)
    return baseline


def validate_implementation_revision(
    revision: str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> None:
    """Prove one commit exists and still matches the evaluated implementation surfaces."""

    if type(revision) is not str or _REVISION.fullmatch(revision) is None:
        raise ValueError("implementation revision is invalid")
    if not isinstance(project_root, Path):
        raise ValueError("implementation repository is invalid")
    try:
        resolved_root = project_root.resolve(strict=True)
    except OSError:
        raise ValueError("implementation repository is unavailable") from None
    git = shutil.which("git")
    if git is None:
        raise ValueError("implementation revision could not be verified")
    git_path = Path(git)
    if not git_path.is_absolute() or not git_path.is_file():
        raise ValueError("implementation revision could not be verified")

    commit_result, _ = _run_git(
        git_path,
        resolved_root,
        ("cat-file", "-e", f"{revision}^{{commit}}"),
    )
    if commit_result != 0:
        raise ValueError("implementation revision could not be verified")
    tree_result, tree_payload = _run_git(
        git_path,
        resolved_root,
        (
            "ls-tree",
            "-z",
            "--full-tree",
            revision,
            "--",
            *QUALITY_IMPLEMENTATION_SURFACES,
        ),
        capture_output=True,
    )
    committed_objects = _implementation_tree_objects(tree_payload)
    if tree_result != 0 or committed_objects is None:
        raise ValueError("implementation objects do not match the recorded revision")
    index_result, index_payload = _run_git(
        git_path,
        resolved_root,
        (
            "ls-files",
            "-z",
            "--stage",
            "--full-name",
            "--",
            *QUALITY_IMPLEMENTATION_SURFACES,
        ),
        capture_output=True,
    )
    if index_result != 0 or not _implementation_index_matches(
        index_payload,
        committed_objects,
    ):
        raise ValueError("implementation index does not match the recorded revision")
    if not _working_surfaces_are_bounded_ordinary_files(resolved_root, committed_objects):
        raise ValueError("implementation working surfaces are invalid")
    work_result, work_payload = _run_git(
        git_path,
        resolved_root,
        (
            "hash-object",
            "--no-filters",
            "--",
            *QUALITY_IMPLEMENTATION_SURFACES,
        ),
        capture_output=True,
    )
    if work_result != 0 or not _implementation_work_hashes_match(
        work_payload,
        committed_objects,
    ):
        raise ValueError("implementation surfaces do not match the recorded revision")


def _run_git(
    executable: Path,
    project_root: Path,
    arguments: tuple[str, ...],
    *,
    capture_output: bool = False,
) -> tuple[int, bytes]:
    """Run one fixed, read-only Git query with bounded time and captured output."""

    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
    }
    for name in ("COMSPEC", "PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"):
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    try:
        completed = subprocess.run(
            (
                str(executable),
                "-C",
                str(project_root),
                "--no-pager",
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                *arguments,
            ),
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=GIT_VALIDATION_TIMEOUT_SECONDS,
            env=environment,
        )
    except OSError, subprocess.SubprocessError:
        raise ValueError("implementation revision could not be verified") from None
    payload = completed.stdout if capture_output else b""
    if type(payload) is not bytes or len(payload) > MAXIMUM_GIT_OUTPUT_BYTES:
        raise ValueError("implementation revision could not be verified")
    return completed.returncode, payload


def _implementation_tree_objects(payload: bytes) -> dict[bytes, tuple[bytes, bytes]] | None:
    """Return exact mode/object bindings for ordinary evaluated commit blobs."""

    if type(payload) is not bytes or not payload.endswith(b"\0"):
        return None
    expected = {path.encode("ascii") for path in QUALITY_IMPLEMENTATION_SURFACES}
    observed: dict[bytes, tuple[bytes, bytes]] = {}
    for record in payload[:-1].split(b"\0"):
        metadata, separator, path = record.partition(b"\t")
        fields = metadata.split(b" ")
        if (
            separator != b"\t"
            or path not in expected
            or path in observed
            or len(fields) != 3
            or fields[0] not in {b"100644", b"100755"}
            or fields[1] != b"blob"
            or len(fields[2]) not in {40, 64}
            or any(value not in b"0123456789abcdef" for value in fields[2])
        ):
            return None
        observed[path] = (fields[0], fields[2])
    return observed if set(observed) == expected else None


def _implementation_index_matches(
    payload: bytes,
    committed: Mapping[bytes, tuple[bytes, bytes]],
) -> bool:
    """Require stage-zero index entries to equal every committed surface object."""

    if type(payload) is not bytes or not payload.endswith(b"\0"):
        return False
    observed: dict[bytes, tuple[bytes, bytes]] = {}
    for record in payload[:-1].split(b"\0"):
        metadata, separator, path = record.partition(b"\t")
        fields = metadata.split(b" ")
        if (
            separator != b"\t"
            or path not in committed
            or path in observed
            or len(fields) != 3
            or fields[2] != b"0"
            or (fields[0], fields[1]) != committed[path]
        ):
            return False
        observed[path] = (fields[0], fields[1])
    return observed == committed


def _working_surfaces_are_bounded_ordinary_files(
    project_root: Path,
    committed: Mapping[bytes, tuple[bytes, bytes]],
) -> bool:
    """Reject missing, symlinked, non-regular, oversized, or wrong-mode worktree surfaces."""

    expected = {path.encode("ascii") for path in QUALITY_IMPLEMENTATION_SURFACES}
    if set(committed) != expected:
        return False
    total_bytes = 0
    for path_bytes, (mode, _object_id) in committed.items():
        try:
            path = project_root / path_bytes.decode("ascii")
            status = path.lstat()
        except OSError, UnicodeDecodeError:
            return False
        if (
            not stat.S_ISREG(status.st_mode)
            or path.is_symlink()
            or not 0 <= status.st_size <= MAXIMUM_IMPLEMENTATION_SURFACE_BYTES
        ):
            return False
        if os.name != "nt" and (bool(status.st_mode & 0o111) != (mode == b"100755")):
            return False
        total_bytes += status.st_size
        if total_bytes > MAXIMUM_IMPLEMENTATION_TOTAL_BYTES:
            return False
    return len(committed) == len(QUALITY_IMPLEMENTATION_SURFACES)


def _implementation_work_hashes_match(
    payload: bytes,
    committed: Mapping[bytes, tuple[bytes, bytes]],
) -> bool:
    """Compare raw, filter-free worktree blob IDs in the fixed surface order."""

    if type(payload) is not bytes or not payload.endswith(b"\n"):
        return False
    expected_paths = [path.encode("ascii") for path in QUALITY_IMPLEMENTATION_SURFACES]
    if set(committed) != set(expected_paths):
        return False
    observed = payload[:-1].split(b"\n")
    expected = [committed[path][1] for path in expected_paths]
    return observed == expected


def _write_baseline_no_replace(path: Path, payload: bytes) -> None:
    """Publish one fsynced baseline through a same-directory no-replace hard link."""

    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".pws0207-quality-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            written = stream.write(payload)
            if written != len(payload):
                raise OSError("short baseline write")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, path)
    except FileExistsError:
        raise ValueError("quality baseline already exists") from None
    except OSError:
        raise ValueError("quality baseline could not be recorded") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _case(
    case_id: str,
    recipe_id: str,
    pixels: bytes,
    applicable: Iterable[str],
    positives: Iterable[str],
    *,
    source_size: tuple[int, int] = (1600, 2200),
    ocr: CorpusOcrObservation | None = None,
) -> QualityCorpusCase:
    return QualityCorpusCase(
        case_id=case_id,
        recipe_id=recipe_id,
        source_width_pixels=source_size[0],
        source_height_pixels=source_size[1],
        pixels=pixels,
        applicable_kinds=tuple(sorted(applicable)),
        positive_kinds=tuple(sorted(positives)),
        ocr=CorpusOcrObservation(None) if ocr is None else ocr,
    )


def _ocr_case(
    case_id: str,
    recipe_id: str,
    observation: CorpusOcrObservation,
    positive_kinds: Iterable[str],
) -> QualityCorpusCase:
    return _case(
        case_id,
        recipe_id,
        _line_page(),
        (OCR_FAILED, OCR_LOW_CONFIDENCE),
        positive_kinds,
        ocr=observation,
    )


def _case_uuid(position: int) -> UUID:
    if type(position) is not int or not 1 <= position <= 999_999_999_999:
        raise ValueError("invalid corpus page position")
    return UUID(f"92070000-0000-4000-8000-{position:012d}")


def _domain_ocr_fact(observation: CorpusOcrObservation, page_id: UUID) -> OcrReviewFact | None:
    if observation.status is None:
        return None
    return OcrReviewFact(
        page_id,
        observation.status,
        observation.recognized_word_count,
        observation.mean_confidence_milli,
    )


def _finding_kind(code: QualityFindingCode) -> str:
    mapping = {
        QualityFindingCode.LIKELY_LOW_RESOLUTION: LOW_RESOLUTION,
        QualityFindingCode.LIKELY_BLUR: BLUR,
        QualityFindingCode.SUSPECTED_CLIPPING: CLIPPING,
        QualityFindingCode.LIKELY_BLANK: BLANK,
        QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION: ORIENTATION,
        QualityFindingCode.SUSPECTED_DUPLICATE: DUPLICATE,
        QualityFindingCode.OCR_FAILED: OCR_FAILED,
        QualityFindingCode.OCR_LOW_CONFIDENCE: OCR_LOW_CONFIDENCE,
    }
    try:
        return mapping[code]
    except KeyError, TypeError:
        raise ValueError("unknown domain finding code") from None


def _line_page(*, ink: int = 24, left: int = 24, variant: int = 0) -> bytes:
    plane = bytearray([255]) * (_WIDTH * _HEIGHT)
    for line_index, top in enumerate(range(34, 220, 24)):
        first_end = 94 + (7 if variant and line_index == 3 else 0)
        _rectangle(plane, left, top, first_end, top + 8, ink)
        _rectangle(plane, 104, top, 222, top + 8, ink)
    return _rgba(plane)


def _bottom_clipped_page() -> bytes:
    plane = bytearray(_luma(_line_page()))
    _rectangle(plane, 24, 250, 222, 260, 24)
    return _rgba(plane)


def _sparse_mark() -> bytes:
    plane = bytearray([255]) * (_WIDTH * _HEIGHT)
    _rectangle(plane, 110, 110, 145, 145, 24)
    return _rgba(plane)


def _faint_noise() -> bytes:
    plane = bytearray(_WIDTH * _HEIGHT)
    for index in range(len(plane)):
        plane[index] = 250 + ((index * 17 + index // _WIDTH * 13) % 6)
    return _rgba(plane)


def _full_bleed() -> bytes:
    plane = bytearray(_WIDTH * _HEIGHT)
    for y in range(_HEIGHT):
        for x in range(_WIDTH):
            plane[y * _WIDTH + x] = 52 + ((x // 8 + y // 8) % 2) * 136
    return _rgba(plane)


def _solid(value: int) -> bytes:
    return _rgba(bytes([value]) * (_WIDTH * _HEIGHT))


def _brightness(pixels: bytes, delta: int) -> bytes:
    return _rgba(bytes(min(255, value + delta) for value in _luma(pixels)))


def _box_blur(pixels: bytes, *, passes: int) -> bytes:
    plane = bytearray(_luma(pixels))
    for _ in range(passes):
        source = bytes(plane)
        for y in range(1, _HEIGHT - 1):
            for x in range(1, _WIDTH - 1):
                total = sum(
                    source[(y + dy) * _WIDTH + x + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                )
                plane[y * _WIDTH + x] = (total + 4) // 9
    return _rgba(plane)


def _motion_blur(pixels: bytes) -> bytes:
    source = _luma(pixels)
    plane = bytearray(source)
    for y in range(_HEIGHT):
        first_row = max(0, y - 5)
        last_row = min(_HEIGHT, y + 6)
        count = last_row - first_row
        for x in range(_WIDTH):
            total = sum(source[row * _WIDTH + x] for row in range(first_row, last_row))
            plane[y * _WIDTH + x] = (total + count // 2) // count
    return _rgba(plane)


def _rotate(pixels: bytes, degrees: int) -> bytes:
    source = _luma(pixels)
    plane = bytearray(len(source))
    for y in range(_HEIGHT):
        for x in range(_WIDTH):
            if degrees == 90:
                target_x, target_y = _WIDTH - 1 - y, x
            elif degrees == 180:
                target_x, target_y = _WIDTH - 1 - x, _HEIGHT - 1 - y
            elif degrees == 270:
                target_x, target_y = y, _HEIGHT - 1 - x
            else:
                raise ValueError("unsupported corpus rotation")
            plane[target_y * _WIDTH + target_x] = source[y * _WIDTH + x]
    return _rgba(plane)


def _rectangle(
    plane: bytearray,
    left: int,
    top: int,
    right: int,
    bottom: int,
    value: int,
) -> None:
    for y in range(max(0, top), min(_HEIGHT, bottom + 1)):
        start = y * _WIDTH + max(0, left)
        end = y * _WIDTH + min(_WIDTH, right + 1)
        plane[start:end] = bytes([value]) * max(0, end - start)


def _rgba(luma: bytes | bytearray) -> bytes:
    if len(luma) != _WIDTH * _HEIGHT:
        raise ValueError("invalid corpus luma plane")
    result = bytearray(len(luma) * 4)
    for index, value in enumerate(luma):
        offset = index * 4
        result[offset : offset + 4] = bytes((value, value, value, 255))
    return bytes(result)


def _luma(pixels: bytes) -> bytes:
    if len(pixels) != _ROW_STRIDE * _HEIGHT:
        raise ValueError("invalid corpus RGBA plane")
    return bytes(pixels[index] for index in range(0, len(pixels), 4))


def _metrics(kind: str, states: Sequence[tuple[bool, bool]]) -> FindingMetrics:
    if not states:
        raise ValueError("a finding kind has no applicable corpus state")
    return FindingMetrics(
        kind=kind,
        applicable=len(states),
        true_positive=sum(truth and prediction for truth, prediction in states),
        false_positive=sum(not truth and prediction for truth, prediction in states),
        true_negative=sum(not truth and not prediction for truth, prediction in states),
        false_negative=sum(truth and not prediction for truth, prediction in states),
    )


def _rate(numerator: int, denominator: int) -> int | None:
    if denominator == 0:
        return None
    return (numerator * PARTS_PER_MILLION + denominator // 2) // denominator


def _metric_from_document(value: object) -> FindingMetrics:
    expected = {
        "kind",
        "applicable",
        "true_positive",
        "false_positive",
        "true_negative",
        "false_negative",
        "precision_ppm",
        "recall_ppm",
        "false_positive_rate_ppm",
        "false_negative_rate_ppm",
    }
    if type(value) is not dict or set(value) != expected:
        raise ValueError("invalid quality metric fields")
    document = cast(dict[str, object], value)
    try:
        metric = FindingMetrics(
            kind=cast(str, document["kind"]),
            applicable=cast(int, document["applicable"]),
            true_positive=cast(int, document["true_positive"]),
            false_positive=cast(int, document["false_positive"]),
            true_negative=cast(int, document["true_negative"]),
            false_negative=cast(int, document["false_negative"]),
        )
    except TypeError, ValueError:
        raise ValueError("invalid quality metric values") from None
    if metric.as_document() != document:
        raise ValueError("quality metric rates do not match their counts")
    return metric


def _canonical_json(document: object) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _bounded_integer(value: str) -> int:
    result = int(value)
    if not -(2**53 - 1) <= result <= (2**53 - 1):
        raise ValueError("JSON integer exceeds the safe range")
    return result


def _reject_number(_value: str) -> int:
    raise ValueError("floating-point JSON values are prohibited")


def _require_unique(values: Iterable[str]) -> None:
    collected = tuple(values)
    if len(set(collected)) != len(collected):
        raise ValueError("duplicate corpus identifier")


def _require_constrained_resource_fit(cases: Sequence[QualityCorpusCase]) -> None:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    if len(cases) > policy.maximum_page_count or any(
        case.width_pixels * case.height_pixels > policy.maximum_preview_pixels_per_page
        for case in cases
    ):
        raise ValueError("quality corpus exceeds the constrained resource policy")


def _print_current_evaluation() -> None:
    profile = DEFAULT_QUALITY_ANALYSIS_PROFILE
    print(f"corpus_sha256={quality_corpus_sha256()}")
    print(f"profile_id={profile.profile_id}")
    print(f"profile_version={profile.profile_version}")
    print(f"profile_sha256={profile.profile_sha256}")
    for metric in evaluate_current_profile(profile=profile):
        print(
            f"{metric.kind} applicable={metric.applicable} "
            f"tp={metric.true_positive} fp={metric.false_positive} "
            f"tn={metric.true_negative} fn={metric.false_negative} "
            f"precision_ppm={metric.precision_ppm} recall_ppm={metric.recall_ppm} "
            f"false_positive_rate_ppm={metric.false_positive_rate_ppm} "
            f"false_negative_rate_ppm={metric.false_negative_rate_ppm}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate, record, or validate the closed PWS-0207 quality corpus."""

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("evaluate", help="print current content-free metrics")
    record = commands.add_parser("record", help="record a new revision-bound baseline")
    record.add_argument("--revision", required=True)
    record.add_argument("--output", required=True, type=Path)
    validate = commands.add_parser("validate", help="validate a recorded baseline")
    validate.add_argument("baseline", type=Path)
    validate.add_argument("--revision")
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "evaluate":
            _print_current_evaluation()
        elif arguments.command == "record":
            record_current_baseline(arguments.output, arguments.revision)
            print("PWS-0207 quality baseline: recorded")
        else:
            baseline = validate_current_baseline(
                arguments.baseline,
                expected_revision=arguments.revision,
            )
            print(f"revision={baseline.revision}")
            _print_current_evaluation()
            print("PWS-0207 quality baseline: valid")
    except ValueError:
        print("ERROR [PWS0207_BASELINE_INVALID] quality evidence is invalid", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
