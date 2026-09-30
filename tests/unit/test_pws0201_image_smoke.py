"""The image smoke uses reproducible synthetic inputs and isolated local attempts."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageOps

from tools import pws0201_image_smoke as smoke
from tools.pws0201_image_smoke import SOURCE_NAMES, prepare_attempt


def test_fresh_attempts_and_resume_preserve_synthetic_orientation_and_alpha(tmp_path: Path) -> None:
    first = prepare_attempt(tmp_path)
    second = prepare_attempt(tmp_path)
    assert first != second
    for name in SOURCE_NAMES:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert prepare_attempt(tmp_path, first.name) == first
    assert list((first / "profile").iterdir()) == []
    assert list((first / "exports").iterdir()) == []
    with Image.open(first / SOURCE_NAMES[0]) as encoded:
        assert encoded.size == (800, 600)
        assert encoded.getexif()[274] == 6
        with ImageOps.exif_transpose(encoded) as upright:
            assert upright.size == (600, 800)
            red = upright.getpixel((70, 70))
            assert isinstance(red, tuple) and red[0] > 240 and red[1] < 15 and red[2] < 15
    with Image.open(first / SOURCE_NAMES[1]) as alpha:
        assert alpha.size == (600, 800)
        assert alpha.getpixel((0, 0)) == (0, 0, 255, 0)
        assert alpha.getpixel((70, 70)) == (255, 0, 0, 255)


def test_resume_rejects_changed_source_without_repair_or_overwrite(tmp_path: Path) -> None:
    root = prepare_attempt(tmp_path)
    source = root / SOURCE_NAMES[0]
    source.write_bytes(b"changed synthetic source")
    with pytest.raises(ValueError, match="smoke.source_changed"):
        prepare_attempt(tmp_path, root.name)
    assert source.read_bytes() == b"changed synthetic source"


@pytest.mark.parametrize("resume", ["../outside", "10000000-0000-1000-8000-000000000001"])
def test_resume_rejects_paths_and_non_random_identifiers(tmp_path: Path, resume: str) -> None:
    with pytest.raises(ValueError):
        prepare_attempt(tmp_path, resume)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("body_fails", (False, True))
def test_staged_runtime_restores_factory_and_cleans_only_owned_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body_fails: bool
) -> None:
    import paperworkshell.__main__ as desktop
    from tests import worker_resources
    from tests.integration import test_pdf_export_worker_windows as pdf_workers

    stage_root = tmp_path / "owned-stage"
    resources = worker_resources.WorkerTestResources(
        stage_root, stage_root / "runtime", stage_root / "repository"
    )
    events: list[str] = []
    original = desktop._worker_runtime_resources

    def stage(path: Path) -> worker_resources.WorkerTestResources:
        assert path == stage_root
        events.append("stage")
        return resources

    def engines(value: worker_resources.WorkerTestResources) -> None:
        assert value is resources
        events.append("engines")

    def cleanup(value: worker_resources.WorkerTestResources) -> None:
        assert value is resources
        assert desktop._worker_runtime_resources is original
        events.append("cleanup")

    monkeypatch.setattr(worker_resources, "create_worker_test_stage_root", lambda: stage_root)
    monkeypatch.setattr(worker_resources, "stage_worker_test_resources", stage)
    monkeypatch.setattr(pdf_workers, "_stage_pdf_engines", engines)
    monkeypatch.setattr(worker_resources, "cleanup_worker_test_resources", cleanup)

    class DesktopFailure(Exception):
        pass

    try:
        with smoke._worker_runtime(True):
            assert desktop._worker_runtime_resources() == (
                resources.python_executable,
                None,
                resources.source_root / "paperworkshell/worker/host_bootstrap.py",
                (resources.runtime_root,),
                (resources.source_root,),
                resources.source_root,
            )
            events.append("desktop")
            if body_fails:
                raise DesktopFailure
    except DesktopFailure:
        assert body_fails
    assert desktop._worker_runtime_resources is original
    assert events == ["stage", "engines", "desktop", "cleanup"]
    assert list(tmp_path.iterdir()) == []


def test_engine_staging_failure_cleans_stage_without_changing_desktop_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import paperworkshell.__main__ as desktop
    from tests import worker_resources
    from tests.integration import test_pdf_export_worker_windows as pdf_workers

    stage_root = tmp_path / "owned-stage"
    resources = worker_resources.WorkerTestResources(
        stage_root, stage_root / "runtime", stage_root / "repository"
    )
    original = desktop._worker_runtime_resources
    cleaned: list[worker_resources.WorkerTestResources] = []

    def fail_engines(_value: worker_resources.WorkerTestResources) -> None:
        raise RuntimeError("synthetic staging failure")

    monkeypatch.setattr(worker_resources, "create_worker_test_stage_root", lambda: stage_root)
    monkeypatch.setattr(worker_resources, "stage_worker_test_resources", lambda _path: resources)
    monkeypatch.setattr(pdf_workers, "_stage_pdf_engines", fail_engines)
    monkeypatch.setattr(worker_resources, "cleanup_worker_test_resources", cleaned.append)
    with (
        pytest.raises(RuntimeError, match="synthetic staging failure"),
        smoke._worker_runtime(True),
    ):
        pytest.fail("a failed stage must not enter the desktop loop")
    assert desktop._worker_runtime_resources is original
    assert cleaned == [resources]


def test_default_runtime_does_not_stage_or_replace_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    import paperworkshell.__main__ as desktop
    from tests import worker_resources

    original = desktop._worker_runtime_resources
    monkeypatch.setattr(
        worker_resources,
        "create_worker_test_stage_root",
        lambda: pytest.fail("default runtime must not stage resources"),
    )
    with smoke._worker_runtime(False):
        assert desktop._worker_runtime_resources is original


@pytest.mark.parametrize("prepare_only", (False, True))
def test_staged_option_is_explicit_in_card_resume_and_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prepare_only: bool,
) -> None:
    launched: list[tuple[Path, bool]] = []

    def launch(root: Path, *, staged_worker_runtime: bool = False) -> int:
        launched.append((root, staged_worker_runtime))
        return 0

    monkeypatch.setattr(smoke, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(smoke, "launch_attempt", launch)
    args = ["--staged-worker-runtime"]
    if prepare_only:
        args.append("--prepare-only")
    assert smoke.main(args) == 0
    card = capsys.readouterr().out
    assert "no pass recorded" in card
    assert "Worker runtime: staged copies" in card
    assert "Ordinary worker security checks apply" in card
    assert "does not test development-runtime startup" in card
    assert card.strip().endswith("--staged-worker-runtime")
    assert len(launched) == (0 if prepare_only else 1)
    if launched:
        assert launched[0][1]
        assert launched[0][0].is_relative_to(tmp_path)
