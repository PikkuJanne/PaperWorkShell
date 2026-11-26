"""Determinism, metrics, and privacy tests for the PWS-0207 corpus evidence."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

import tools.pws0207_quality_evaluation as quality_evaluation
from paperworkshell.domain.quality_analysis import OcrReviewStatus
from tools.pws0207_quality_evaluation import (
    BASELINE_ID,
    BLANK,
    BLUR,
    CLIPPING,
    CORPUS_ID,
    DUPLICATE,
    FINDING_KINDS,
    LOW_RESOLUTION,
    OCR_FAILED,
    OCR_LOW_CONFIDENCE,
    ORIENTATION,
    CorpusOcrObservation,
    FindingMetrics,
    QualityBaseline,
    build_current_baseline,
    build_duplicate_pairs,
    build_quality_corpus,
    decode_quality_baseline,
    encode_quality_baseline,
    evaluate_current_profile,
    evaluate_predictions,
    load_quality_baseline,
    main,
    quality_corpus_sha256,
    validate_current_baseline,
    validate_implementation_revision,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = PROJECT_ROOT / "benchmarks" / "pws0207-quality-findings-d168db80-v1.json"
EXPECTED_CORPUS_SHA256 = "36fad4f6a94e06971ea9e8e3226df18f92050e8f6f14ba1a1dd5b5b4056bfd8e"
EXPECTED_PROFILE_SHA256 = "ac8a174ad72892f7a519090d0497d6382c4e67d34fb1f5b6638fb82f420901b8"
EXPECTED_COUNTS = {
    LOW_RESOLUTION: (18, 2, 0, 16, 0),
    BLUR: (18, 2, 1, 15, 0),
    CLIPPING: (18, 2, 1, 15, 0),
    BLANK: (19, 3, 0, 16, 0),
    ORIENTATION: (18, 2, 0, 15, 1),
    DUPLICATE: (4, 2, 1, 1, 0),
    OCR_FAILED: (5, 2, 0, 3, 0),
    OCR_LOW_CONFIDENCE: (5, 1, 0, 4, 0),
}


def _truth_predictions() -> tuple[dict[str, frozenset[str]], dict[str, bool]]:
    cases = build_quality_corpus()
    pairs = build_duplicate_pairs()
    return (
        {case.case_id: frozenset(case.positive_kinds) for case in cases},
        {pair.pair_id: pair.is_duplicate for pair in pairs},
    )


def _metric(metrics: tuple[FindingMetrics, ...], kind: str) -> FindingMetrics:
    return next(value for value in metrics if value.kind == kind)


def _implementation_tree_payload() -> bytes:
    object_id = b"a" * 40
    return b"".join(
        b"100644 blob " + object_id + b"\t" + path.encode("ascii") + b"\0"
        for path in quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES
    )


def _implementation_index_payload() -> bytes:
    object_id = b"a" * 40
    return b"".join(
        b"100644 " + object_id + b" 0\t" + path.encode("ascii") + b"\0"
        for path in quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES
    )


def _implementation_work_payload() -> bytes:
    return b"".join(
        b"a" * 40 + b"\n" for _path in quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES
    )


def test_revision_binding_inventory_is_exact_and_task_owned() -> None:
    expected = {
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
    }

    assert set(quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES) == expected
    assert len(quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES) == len(expected)


@pytest.mark.parametrize("mean_confidence_milli", [None, 0, 50_000])
def test_successful_corpus_ocr_requires_a_recognized_word(
    mean_confidence_milli: int | None,
) -> None:
    with pytest.raises(ValueError, match="successful OCR must contain a recognized word"):
        CorpusOcrObservation(
            OcrReviewStatus.SUCCEEDED,
            recognized_word_count=0,
            mean_confidence_milli=mean_confidence_milli,
        )

    assert CorpusOcrObservation(OcrReviewStatus.NO_TEXT) == CorpusOcrObservation(
        OcrReviewStatus.NO_TEXT,
        recognized_word_count=0,
        mean_confidence_milli=None,
    )


def test_recipe_only_corpus_is_exact_deterministic_and_opaque() -> None:
    first = build_quality_corpus()
    second = build_quality_corpus()

    assert len(first) == 28
    assert first == second
    assert quality_corpus_sha256(first, build_duplicate_pairs()) == EXPECTED_CORPUS_SHA256
    assert len({case.case_id for case in first}) == len(first)
    assert all(len(case.pixels) == 256 * 256 * 4 for case in first)
    assert all(case.pixels[3::4] == b"\xff" * (256 * 256) for case in first)
    assert all("pixels=" not in repr(case) for case in first)
    assert all("sha256" not in repr(case).lower() for case in first)


def test_recipe_evaluator_imports_no_network_random_clock_or_logging_surface() -> None:
    source = (PROJECT_ROOT / "tools" / "pws0207_quality_evaluation.py").read_text(encoding="utf-8")
    imported_roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.partition(".")[0])
    assert imported_roots.isdisjoint(
        {
            "http",
            "logging",
            "multiprocessing",
            "random",
            "requests",
            "socket",
            "time",
            "urllib",
        }
    )
    assert "shell=True" not in source
    assert "os.system" not in source


def test_recipe_evaluation_does_not_invoke_a_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_subprocess(*_arguments: object, **_keywords: object) -> object:
        pytest.fail("recipe evaluation attempted to invoke a subprocess")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)

    assert tuple(metric.kind for metric in evaluate_current_profile()) == FINDING_KINDS


def test_corpus_keeps_known_false_positive_and_false_negative_challenges() -> None:
    by_id = {case.case_id: case for case in build_quality_corpus()}
    assert ORIENTATION in by_id["orientation-180"].positive_kinds
    assert CLIPPING in by_id["clip-full-bleed"].applicable_kinds
    assert CLIPPING not in by_id["clip-full-bleed"].positive_kinds
    assert BLANK in by_id["blank-sparse-mark"].applicable_kinds
    assert BLANK not in by_id["blank-sparse-mark"].positive_kinds
    assert BLUR in by_id["blank-sparse-mark"].applicable_kinds
    assert BLUR not in by_id["blank-sparse-mark"].positive_kinds
    assert OCR_FAILED in by_id["ocr-zero-blank"].applicable_kinds
    assert OCR_FAILED not in by_id["ocr-zero-blank"].positive_kinds
    assert OCR_LOW_CONFIDENCE in by_id["ocr-zero-blank"].applicable_kinds
    assert OCR_LOW_CONFIDENCE not in by_id["ocr-zero-blank"].positive_kinds
    assert (
        next(
            pair for pair in build_duplicate_pairs() if pair.pair_id == "duplicate-pair-similar"
        ).is_duplicate
        is False
    )


def test_current_profile_metrics_and_digests_are_exact() -> None:
    metrics = evaluate_current_profile()
    assert {
        metric.kind: (
            metric.applicable,
            metric.true_positive,
            metric.false_positive,
            metric.true_negative,
            metric.false_negative,
        )
        for metric in metrics
    } == EXPECTED_COUNTS
    baseline = build_current_baseline("0" * 40)
    assert baseline.corpus_sha256 == EXPECTED_CORPUS_SHA256
    assert baseline.profile_sha256 == EXPECTED_PROFILE_SHA256


def test_confusion_matrices_report_false_positives_and_false_negatives_exactly() -> None:
    unary, duplicate = _truth_predictions()
    unary["orientation-180"] = frozenset(
        kind for kind in unary["orientation-180"] if kind != ORIENTATION
    )
    unary["clip-full-bleed"] = frozenset((*unary["clip-full-bleed"], CLIPPING))
    duplicate["duplicate-pair-similar"] = True

    metrics = evaluate_predictions(unary, duplicate)
    orientation = _metric(metrics, ORIENTATION)
    clipping = _metric(metrics, CLIPPING)
    duplicate_metric = _metric(metrics, DUPLICATE)

    assert orientation.true_positive == 2
    assert orientation.false_negative == 1
    assert orientation.recall_ppm == 666_667
    assert orientation.false_negative_rate_ppm == 333_333
    assert clipping.false_positive == 1
    assert clipping.false_positive_rate_ppm == 62_500
    assert duplicate_metric.false_positive == 1
    assert duplicate_metric.precision_ppm == 666_667


def test_prediction_inventory_and_values_fail_closed() -> None:
    unary, duplicate = _truth_predictions()
    unary.pop(next(iter(unary)))
    with pytest.raises(ValueError, match="inventory"):
        evaluate_predictions(unary, duplicate)

    unary, duplicate = _truth_predictions()
    unary[next(iter(unary))] = frozenset({"private-document-text"})
    with pytest.raises(ValueError, match="unknown"):
        evaluate_predictions(unary, duplicate)

    unary, duplicate = _truth_predictions()
    duplicate[next(iter(duplicate))] = 1  # type: ignore[assignment]
    with pytest.raises(ValueError, match="booleans"):
        evaluate_predictions(unary, duplicate)


def test_baseline_round_trip_is_canonical_closed_and_content_free() -> None:
    unary, duplicate = _truth_predictions()
    baseline = QualityBaseline(
        revision="0" * 40,
        profile_id="quality-review-v1",
        profile_version=1,
        profile_sha256="1" * 64,
        corpus_sha256=quality_corpus_sha256(),
        metrics=evaluate_predictions(unary, duplicate),
    )
    encoded = encode_quality_baseline(baseline)

    assert decode_quality_baseline(encoded) == baseline
    assert encoded.endswith(b"\n")
    assert encoded == encode_quality_baseline(decode_quality_baseline(encoded))
    assert BASELINE_ID.encode("ascii") in encoded
    assert CORPUS_ID.encode("ascii") in encoded
    assert b"pixel" not in encoded
    assert b"path" not in encoded
    assert b"text" not in encoded


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: b" " + value,
        lambda value: value + b"\n",
        lambda value: value.replace(b'"report_version":1', b'"report_version":1.0'),
        lambda value: value.replace(
            b'"report_version":1', b'"report_version":1,"report_version":1'
        ),
        lambda value: value.replace(b'"precision_ppm":1000000', b'"precision_ppm":999999', 1),
    ),
)
def test_baseline_rejects_noncanonical_duplicate_float_and_inconsistent_rate(
    mutation: object,
) -> None:
    unary, duplicate = _truth_predictions()
    baseline = QualityBaseline(
        revision="0" * 40,
        profile_id="quality-review-v1",
        profile_version=1,
        profile_sha256="1" * 64,
        corpus_sha256=quality_corpus_sha256(),
        metrics=evaluate_predictions(unary, duplicate),
    )
    payload = encode_quality_baseline(baseline)
    mutate = mutation
    assert callable(mutate)
    with pytest.raises(ValueError):
        decode_quality_baseline(mutate(payload))


def test_baseline_loader_is_bounded_and_sanitizes_io_failure(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unavailable") as missing:
        load_quality_baseline(tmp_path / "private-applicant-name.json")
    assert "private-applicant-name" not in str(missing.value)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(ValueError, match="bytes"):
        load_quality_baseline(oversized)


def test_checked_in_baseline_is_canonical_and_matches_current_corpus(
    pytestconfig: pytest.Config,
) -> None:
    selected = pytestconfig.getoption("--pws0207-baseline")
    baseline_path = BASELINE_PATH if selected is None else Path(selected).resolve(strict=True)
    assert baseline_path.is_file(), "the revision-bound PWS-0207 baseline is required"
    baseline = validate_current_baseline(baseline_path)
    assert baseline.corpus_sha256 == EXPECTED_CORPUS_SHA256
    assert baseline.profile_sha256 == EXPECTED_PROFILE_SHA256
    assert tuple(metric.kind for metric in baseline.metrics) == FINDING_KINDS
    document = json.loads(baseline_path.read_text(encoding="ascii"))
    assert document == baseline.as_document()


def test_cli_evaluate_prints_only_closed_content_free_metrics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["evaluate"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert f"corpus_sha256={EXPECTED_CORPUS_SHA256}" in captured.out
    assert f"profile_sha256={EXPECTED_PROFILE_SHA256}" in captured.out
    assert set(FINDING_KINDS).issubset(captured.out.split())
    assert "pixels" not in captured.out
    assert "path" not in captured.out
    assert "text" not in captured.out


def test_cli_records_once_and_validates_exact_current_baseline(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        quality_evaluation,
        "validate_implementation_revision",
        lambda _revision: None,
    )
    baseline_path = tmp_path / "baseline.json"
    arguments = ["record", "--revision", "0" * 40, "--output", str(baseline_path)]
    assert main(arguments) == 0
    assert "recorded" in capsys.readouterr().out
    original_payload = baseline_path.read_bytes()
    assert main(["validate", str(baseline_path), "--revision", "0" * 40]) == 0
    validated = capsys.readouterr()
    assert "valid" in validated.out
    assert "revision=" + "0" * 40 in validated.out
    assert main(["validate", str(baseline_path), "--revision", "1" * 40]) == 1
    assert "PWS0207_BASELINE_INVALID" in capsys.readouterr().err
    assert main(arguments) == 1
    failure = capsys.readouterr()
    assert "PWS0207_BASELINE_INVALID" in failure.err
    assert str(baseline_path) not in failure.err
    assert baseline_path.read_bytes() == original_payload


def test_revision_validation_uses_fixed_read_only_git_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = tmp_path / "git.exe"
    git.write_bytes(b"synthetic executable marker")
    monkeypatch.setattr(shutil, "which", lambda _name: str(git))
    monkeypatch.setattr(
        quality_evaluation,
        "_working_surfaces_are_bounded_ordinary_files",
        lambda _root, _committed: True,
    )
    calls: list[tuple[tuple[str, ...], bool]] = []

    def run_git(
        _executable: Path,
        project_root: Path,
        arguments: tuple[str, ...],
        *,
        capture_output: bool = False,
    ) -> tuple[int, bytes]:
        assert project_root == tmp_path.resolve()
        calls.append((arguments, capture_output))
        if arguments[0] == "ls-tree":
            return 0, _implementation_tree_payload()
        if arguments[0] == "ls-files":
            return 0, _implementation_index_payload()
        if arguments[0] == "hash-object":
            return 0, _implementation_work_payload()
        return 0, b""

    monkeypatch.setattr(quality_evaluation, "_run_git", run_git)
    revision = "a" * 40

    validate_implementation_revision(revision, project_root=tmp_path)

    assert calls == [
        (("cat-file", "-e", f"{revision}^{{commit}}"), False),
        (
            (
                "ls-tree",
                "-z",
                "--full-tree",
                revision,
                "--",
                *quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES,
            ),
            True,
        ),
        (
            (
                "ls-files",
                "-z",
                "--stage",
                "--full-name",
                "--",
                *quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES,
            ),
            True,
        ),
        (
            (
                "hash-object",
                "--no-filters",
                "--",
                *quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES,
            ),
            True,
        ),
    ]


def test_git_query_disables_prompts_lazy_fetch_and_repository_monitors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "git.exe"
    executable.write_bytes(b"synthetic executable marker")
    observed: dict[str, object] = {}

    def run(arguments: object, **keywords: object) -> subprocess.CompletedProcess[bytes]:
        observed["arguments"] = arguments
        observed.update(keywords)
        return subprocess.CompletedProcess((str(executable),), 0, stdout=b"")

    monkeypatch.setattr(subprocess, "run", run)

    assert quality_evaluation._run_git(
        executable,
        tmp_path,
        ("status", "--porcelain=v1"),
        capture_output=True,
    ) == (0, b"")

    assert observed["arguments"] == (
        str(executable),
        "-C",
        str(tmp_path),
        "--no-pager",
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "status",
        "--porcelain=v1",
    )
    assert observed["check"] is False
    assert observed["shell"] is False
    assert observed["stdin"] == subprocess.DEVNULL
    assert observed["stdout"] == subprocess.PIPE
    assert observed["stderr"] == subprocess.DEVNULL
    assert observed["timeout"] == quality_evaluation.GIT_VALIDATION_TIMEOUT_SECONDS
    environment = cast(dict[str, str], observed["env"])
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.parametrize(
    ("failure_call", "failure_result"),
    (
        (1, (1, b"")),
        (2, (0, b"120000 blob " + b"a" * 40 + b"\tprivate-source.py\0")),
        (3, (0, b"100644 " + b"a" * 40 + b" 0\tprivate-source.py\0")),
        (4, (0, b"b" * 40 + b"\n")),
    ),
)
def test_revision_validation_fails_closed_without_echoing_git_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
    failure_result: tuple[int, bytes],
) -> None:
    git = tmp_path / "git.exe"
    git.write_bytes(b"synthetic executable marker")
    monkeypatch.setattr(shutil, "which", lambda _name: str(git))
    monkeypatch.setattr(
        quality_evaluation,
        "_working_surfaces_are_bounded_ordinary_files",
        lambda _root, _committed: True,
    )
    call_count = 0

    def run_git(
        _executable: Path,
        _project_root: Path,
        arguments: tuple[str, ...],
        *,
        capture_output: bool = False,
    ) -> tuple[int, bytes]:
        nonlocal call_count
        del capture_output
        call_count += 1
        if call_count == failure_call:
            return failure_result
        if arguments[0] == "ls-tree":
            return 0, _implementation_tree_payload()
        if arguments[0] == "ls-files":
            return 0, _implementation_index_payload()
        if arguments[0] == "hash-object":
            return 0, _implementation_work_payload()
        return 0, b""

    monkeypatch.setattr(quality_evaluation, "_run_git", run_git)

    with pytest.raises(ValueError) as rejected:
        validate_implementation_revision("b" * 40, project_root=tmp_path)

    assert "private-source" not in str(rejected.value)


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        _implementation_tree_payload()[:-1],
        _implementation_tree_payload().replace(b"100644", b"120000", 1),
        _implementation_tree_payload()
        + _implementation_tree_payload().split(b"\0", maxsplit=1)[0]
        + b"\0",
        b"100644 blob " + b"a" * 40 + b"\tprivate-source.py\0",
    ),
)
def test_implementation_tree_inventory_rejects_missing_symlink_duplicate_and_unknown(
    payload: bytes,
) -> None:
    assert quality_evaluation._implementation_tree_objects(payload) is None


def test_implementation_tree_inventory_accepts_exact_ordinary_blobs() -> None:
    observed = quality_evaluation._implementation_tree_objects(_implementation_tree_payload())
    assert observed is not None
    assert set(observed) == {
        path.encode("ascii") for path in quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES
    }


def test_index_and_raw_worktree_hashes_must_match_exact_committed_objects() -> None:
    committed = quality_evaluation._implementation_tree_objects(_implementation_tree_payload())
    assert committed is not None
    assert quality_evaluation._implementation_index_matches(
        _implementation_index_payload(),
        committed,
    )
    assert quality_evaluation._implementation_work_hashes_match(
        _implementation_work_payload(),
        committed,
    )
    assert not quality_evaluation._implementation_index_matches(
        _implementation_index_payload().replace(b" 0\t", b" 1\t", 1),
        committed,
    )
    assert not quality_evaluation._implementation_work_hashes_match(
        _implementation_work_payload().replace(b"a", b"b", 1),
        committed,
    )


def test_worktree_surface_precheck_rejects_missing_and_oversized_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed = quality_evaluation._implementation_tree_objects(_implementation_tree_payload())
    assert committed is not None
    assert not quality_evaluation._working_surfaces_are_bounded_ordinary_files(
        tmp_path,
        committed,
    )
    for relative in quality_evaluation.QUALITY_IMPLEMENTATION_SURFACES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic source\n")
    assert quality_evaluation._working_surfaces_are_bounded_ordinary_files(tmp_path, committed)

    monkeypatch.setattr(quality_evaluation, "MAXIMUM_IMPLEMENTATION_SURFACE_BYTES", 1)
    assert not quality_evaluation._working_surfaces_are_bounded_ordinary_files(
        tmp_path,
        committed,
    )


@pytest.mark.parametrize("failure", ("timeout", "oversized"))
def test_git_query_is_time_and_output_bounded_with_sanitized_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    executable = tmp_path / "private-git.exe"
    executable.write_bytes(b"synthetic executable marker")

    def run(*_arguments: object, **_keywords: object) -> object:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                cmd=("private-applicant-path",),
                timeout=quality_evaluation.GIT_VALIDATION_TIMEOUT_SECONDS,
            )
        return subprocess.CompletedProcess(
            args=("private-applicant-path",),
            returncode=0,
            stdout=b"x" * (quality_evaluation.MAXIMUM_GIT_OUTPUT_BYTES + 1),
        )

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(ValueError) as rejected:
        quality_evaluation._run_git(
            executable,
            tmp_path,
            ("status",),
            capture_output=True,
        )

    assert "private-applicant" not in str(rejected.value)


def test_atomic_recorder_cleans_failed_temp_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        quality_evaluation,
        "validate_implementation_revision",
        lambda _revision: None,
    )
    baseline_path = tmp_path / "baseline.json"
    real_fsync = os.fsync

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated low disk")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(ValueError, match="could not be recorded"):
        quality_evaluation.record_current_baseline(baseline_path, "c" * 40)
    assert not baseline_path.exists()
    assert tuple(tmp_path.glob(".pws0207-quality-*.tmp")) == ()

    monkeypatch.setattr(os, "fsync", real_fsync)
    recorded = quality_evaluation.record_current_baseline(baseline_path, "c" * 40)
    assert baseline_path.read_bytes() == encode_quality_baseline(recorded)


def test_atomic_recorder_loses_no_replace_race_without_changing_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        quality_evaluation,
        "validate_implementation_revision",
        lambda _revision: None,
    )
    baseline_path = tmp_path / "baseline.json"
    winner = b"existing independently recorded baseline\n"

    def lose_race(_temporary: Path, destination: Path) -> None:
        destination.write_bytes(winner)
        raise FileExistsError

    monkeypatch.setattr(os, "link", lose_race)

    with pytest.raises(ValueError, match="already exists"):
        quality_evaluation.record_current_baseline(baseline_path, "d" * 40)

    assert baseline_path.read_bytes() == winner
    assert tuple(tmp_path.glob(".pws0207-quality-*.tmp")) == ()


def test_cli_validation_fails_closed_on_canonical_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = build_current_baseline("0" * 40)
    drifted = QualityBaseline(
        revision=baseline.revision,
        profile_id=baseline.profile_id,
        profile_version=baseline.profile_version,
        profile_sha256="0" * 64,
        corpus_sha256=baseline.corpus_sha256,
        metrics=baseline.metrics,
    )
    path = tmp_path / "drifted.json"
    path.write_bytes(encode_quality_baseline(drifted))
    assert main(["validate", str(path)]) == 1
    failure = capsys.readouterr()
    assert "PWS0207_BASELINE_INVALID" in failure.err
    assert str(path) not in failure.err
