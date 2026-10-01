"""Explicit compression admits one bounded pipeline and closes it before review."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any, cast

import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.app.package_pdf_export import (
    PackagePdfCandidateError,
    PackagePdfCandidateErrorCode,
)
from paperworkshell.app.target_size_pdf_export import TargetSizePdfCandidateProducer
from paperworkshell.domain.pdf_export import IMAGE_PDF_EXPORT_PROFILE
from tests.unit.test_package_export_composition import _Rasterizer, _resources, _Sources


class _Processor:
    def __init__(self, resources: desktop.DesktopApplicationResourceContext) -> None:
        self.heavy_operations = resources.heavy_operations
        self.reservations: list[int] = []

    def process(self, *_args: object, **kwargs: Any) -> object:
        self.reservations.append(kwargs["reserved_temporary_bytes"])
        return self


class _ImageComposition:
    def __init__(self, resources: desktop.DesktopApplicationResourceContext) -> None:
        self.closed = False
        self.close_calls = 0
        self.image_stage_processor = _Processor(resources)

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def test_target_producer_factory_admits_no_worker_until_pdf_pipeline_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("preparation selected no pipeline yet")

    for factory in (
        "create_pdf_image_stage_worker_composition",
        "build_windows_flattened_pdf_export_worker_composition",
        "build_windows_searchable_pdf_export_worker_composition",
    ):
        monkeypatch.setattr(desktop, factory, forbidden)
    resources = _resources()
    with desktop._target_size_pdf_producer_context(
        cast(Any, _Sources()), cast(Any, _Rasterizer()), resources
    ) as producer:
        assert type(producer) is TargetSizePdfCandidateProducer
        assert producer._policy is resources.resource_policy


def test_image_pipeline_reuses_composition_for_search_and_closes_before_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _resources()
    composition = _ImageComposition(resources)
    configs: list[dict[str, object]] = []

    def config(**kwargs: object) -> object:
        configs.append(kwargs)
        return configs

    def create(value: object, *, heavy_operations: object) -> object:
        assert value is configs
        assert heavy_operations is resources.heavy_operations
        return composition

    monkeypatch.setattr(desktop, "build_windows_pdf_export_worker_config", config)
    monkeypatch.setattr(desktop, "create_pdf_image_stage_worker_composition", create)
    with ExitStack() as stack:
        processor = cast(Any, desktop._LazyPackageImageStageProcessor(resources, stack))
        for reserved in (0, 1024):
            assert (
                processor.process(
                    object(),
                    object(),
                    object(),
                    deadline=lambda: False,
                    cancellation=lambda: False,
                    progress=lambda _: None,
                    reserved_temporary_bytes=reserved,
                )
                is composition.image_stage_processor
            )
        assert composition.close_calls == 0
    assert composition.close_calls == 1
    assert composition.image_stage_processor.reservations == [0, 1024]
    assert configs == [
        {"resource_policy": resources.resource_policy, "profile": IMAGE_PDF_EXPORT_PROFILE}
    ]


def test_cancel_after_image_admission_closes_without_starting_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _resources()
    composition = _ImageComposition(resources)
    admitted = False

    def create(*_args: object, **_kwargs: object) -> object:
        nonlocal admitted
        admitted = True
        return composition

    monkeypatch.setattr(
        desktop, "build_windows_pdf_export_worker_config", lambda **_kwargs: object()
    )
    monkeypatch.setattr(desktop, "create_pdf_image_stage_worker_composition", create)
    with pytest.raises(PackagePdfCandidateError) as captured, ExitStack() as stack:
        cast(Any, desktop._LazyPackageImageStageProcessor(resources, stack)).process(
            object(),
            object(),
            object(),
            deadline=lambda: False,
            cancellation=lambda: admitted,
            progress=lambda _: None,
        )
    assert captured.value.code is PackagePdfCandidateErrorCode.CANCELLED
    assert composition.close_calls == 1
    assert composition.image_stage_processor.reservations == []
