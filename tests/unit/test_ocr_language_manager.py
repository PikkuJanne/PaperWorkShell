"""PWS-0205 toolkit-free OCR language manager tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest

from paperworkshell.app.ocr_languages import (
    OcrLanguageDiscoveryResult,
    OcrLanguageManager,
    OcrLanguageOption,
    OcrLanguageRuntimeStatus,
    OcrLanguageSnapshot,
)
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.ocr_languages import (
    DEFAULT_OCR_LANGUAGE_SET_ID,
    ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID,
    GERMAN_OCR_LANGUAGE_SET_ID,
)


class _Discovery:
    def __init__(
        self,
        results: Sequence[OcrLanguageDiscoveryResult | BaseException | object],
    ) -> None:
        self._results = iter(results)
        self.calls = 0

    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        self.calls += 1
        value = next(self._results)
        if isinstance(value, BaseException):
            raise value
        return cast(OcrLanguageDiscoveryResult, value)


def _result(
    status: OcrLanguageRuntimeStatus,
    *codes: str,
) -> OcrLanguageDiscoveryResult:
    return OcrLanguageDiscoveryResult(status, tuple(codes))


def test_manager_is_lazy_and_refreshes_default_selection() -> None:
    discovery = _Discovery([_result(OcrLanguageRuntimeStatus.AVAILABLE, "eng")])
    manager = OcrLanguageManager(discovery)

    assert discovery.calls == 0
    assert manager.snapshot.runtime_status is OcrLanguageRuntimeStatus.UNCHECKED
    assert manager.snapshot.selected_language_set_id == DEFAULT_OCR_LANGUAGE_SET_ID
    assert not manager.snapshot.selected_available

    snapshot = manager.refresh()

    assert discovery.calls == 1
    assert snapshot.runtime_status is OcrLanguageRuntimeStatus.AVAILABLE
    assert snapshot.installed_language_codes == frozenset({"eng"})
    assert snapshot.selected_available


def test_review_reuses_snapshot_but_export_preflight_detects_removed_runtime() -> None:
    discovery = _Discovery(
        [
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng"),
            _result(OcrLanguageRuntimeStatus.INVALID),
        ]
    )
    manager = OcrLanguageManager(discovery)
    initial = manager.review_snapshot()
    assert initial.selected_available
    for _ in range(3):
        assert manager.review_snapshot() is initial
    assert discovery.calls == 1
    with pytest.raises(OcrError) as raised:
        manager.require_selected_available()
    assert raised.value.code is OcrErrorCode.LANGUAGE_UNAVAILABLE
    assert discovery.calls == 2
    assert not manager.snapshot.selected_available


def test_multiple_language_selection_is_canonical_and_available() -> None:
    discovery = _Discovery(
        [
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
        ]
    )
    manager = OcrLanguageManager(discovery)
    manager.refresh()

    snapshot = manager.select_language_codes(("deu", "eng"))

    assert snapshot.selected_language_set_id == ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID
    assert snapshot.selected_language_codes == ("eng", "deu")
    assert snapshot.selected_available


def test_installed_german_only_selection_is_available() -> None:
    manager = OcrLanguageManager(
        _Discovery(
            [
                _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
                _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
            ]
        )
    )
    manager.refresh()

    snapshot = manager.select_language_codes(("deu",))

    assert snapshot.selected_language_set_id == GERMAN_OCR_LANGUAGE_SET_ID
    assert snapshot.selected_language_codes == ("deu",)
    assert snapshot.selected_available


def test_direct_missing_selection_fails_without_changing_selection() -> None:
    manager = OcrLanguageManager(
        _Discovery(
            [
                _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng"),
                _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng"),
            ]
        )
    )
    before = manager.refresh()

    with pytest.raises(OcrError) as captured:
        manager.select_language_codes(("deu",))

    assert captured.value.code is OcrErrorCode.LANGUAGE_UNAVAILABLE
    assert captured.value.context == "ocr.language_selection"
    assert manager.snapshot.selected_language_set_id == before.selected_language_set_id


def test_pack_removed_between_refresh_and_save_cannot_commit_selection() -> None:
    manager = OcrLanguageManager(
        _Discovery(
            [
                _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
                _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng"),
            ]
        )
    )
    before = manager.refresh()

    with pytest.raises(OcrError) as captured:
        manager.select_language_codes(("eng", "deu"))

    assert captured.value.code is OcrErrorCode.LANGUAGE_UNAVAILABLE
    assert manager.snapshot.selected_language_set_id == before.selected_language_set_id
    assert manager.snapshot.installed_language_codes == frozenset({"eng"})


@pytest.mark.parametrize(
    ("codes", "expected_code"),
    [
        ((), OcrErrorCode.MODEL_INVALID),
        (("fra",), OcrErrorCode.LANGUAGE_UNAVAILABLE),
        (("eng", "eng"), OcrErrorCode.MODEL_INVALID),
        (("eng;private-path",), OcrErrorCode.MODEL_INVALID),
    ],
)
def test_unsupported_or_malformed_selection_is_sanitized(
    codes: tuple[str, ...],
    expected_code: OcrErrorCode,
) -> None:
    manager = OcrLanguageManager(
        _Discovery(
            [
                _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
                _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
            ]
        )
    )
    manager.refresh()

    with pytest.raises(OcrError) as captured:
        manager.select_language_codes(codes)

    assert captured.value.code is expected_code
    assert "private-path" not in str(captured.value)


def test_refresh_retains_a_selection_that_later_becomes_missing_without_fallback() -> None:
    discovery = _Discovery(
        [
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng"),
        ]
    )
    manager = OcrLanguageManager(discovery)
    manager.refresh()
    manager.select_language_codes(("eng", "deu"))

    snapshot = manager.refresh()

    assert snapshot.selected_language_set_id == ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID
    assert snapshot.selected_language_codes == ("eng", "deu")
    assert snapshot.missing_selected_language_codes == ("deu",)
    assert not snapshot.selected_available


@pytest.mark.parametrize(
    "status",
    (OcrLanguageRuntimeStatus.UNAVAILABLE, OcrLanguageRuntimeStatus.INVALID),
)
def test_unavailable_and_invalid_refreshes_are_distinct_and_path_free(
    status: OcrLanguageRuntimeStatus,
) -> None:
    manager = OcrLanguageManager(_Discovery([_result(status)]))

    snapshot = manager.refresh()

    assert snapshot.runtime_status is status
    assert not snapshot.selected_available
    assert snapshot.installed_language_codes == frozenset()
    assert "path" not in repr(snapshot).lower()
    assert "eng" not in repr(snapshot)


def test_require_available_refreshes_lazily_and_fails_closed() -> None:
    discovery = _Discovery(
        [
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng"),
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng"),
        ]
    )
    manager = OcrLanguageManager(discovery)

    manager.require_available(DEFAULT_OCR_LANGUAGE_SET_ID)
    assert discovery.calls == 1

    with pytest.raises(OcrError) as captured:
        manager.require_available(ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID)

    assert captured.value.code is OcrErrorCode.LANGUAGE_UNAVAILABLE
    assert captured.value.context == "ocr.language_preflight"


def test_selected_preflight_is_atomic_and_rechecks_pack_presence() -> None:
    discovery = _Discovery(
        [
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng", "deu"),
            _result(OcrLanguageRuntimeStatus.AVAILABLE, "eng"),
        ]
    )
    manager = OcrLanguageManager(discovery)
    manager.refresh()
    manager.select_language_codes(("eng", "deu"))

    with pytest.raises(OcrError) as captured:
        manager.require_selected_available()

    assert captured.value.code is OcrErrorCode.LANGUAGE_UNAVAILABLE
    assert manager.snapshot.selected_language_set_id == ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID
    assert manager.snapshot.missing_selected_language_codes == ("deu",)


def test_invalid_adapter_result_and_unexpected_failure_are_sanitized() -> None:
    invalid = OcrLanguageManager(_Discovery([object()]))
    private = OcrLanguageManager(_Discovery([RuntimeError(r"C:\\private\\case.pdf")]))

    with pytest.raises(OcrError) as invalid_error:
        invalid.refresh()
    with pytest.raises(OcrError) as private_error:
        private.refresh()

    assert invalid_error.value.code is OcrErrorCode.RESULT_INVALID
    assert private_error.value.code is OcrErrorCode.INTERNAL_ERROR
    assert "private" not in repr(private_error.value).lower()


@pytest.mark.parametrize("codes", ((), ("deu",)))
def test_available_discovery_requires_authenticated_builtin_english(
    codes: tuple[str, ...],
) -> None:
    with pytest.raises(OcrError) as captured:
        OcrLanguageDiscoveryResult(OcrLanguageRuntimeStatus.AVAILABLE, codes)

    assert captured.value.code is OcrErrorCode.MODEL_INVALID
    assert captured.value.context == "ocr.language_discovery"


def test_snapshot_rejects_non_option_objects_with_a_typed_error() -> None:
    manager = OcrLanguageManager(_Discovery([_result(OcrLanguageRuntimeStatus.AVAILABLE, "eng")]))
    snapshot = manager.refresh()

    with pytest.raises(OcrError) as captured:
        OcrLanguageSnapshot(
            snapshot.runtime_status,
            cast(tuple[OcrLanguageOption, ...], (object(), object())),
            snapshot.selected_language_set_id,
            snapshot.selected_language_codes,
            snapshot.selected_available,
        )

    assert captured.value.code is OcrErrorCode.MODEL_INVALID


def test_snapshot_cannot_claim_available_without_builtin_english() -> None:
    with pytest.raises(OcrError) as captured:
        OcrLanguageSnapshot(
            OcrLanguageRuntimeStatus.AVAILABLE,
            (
                OcrLanguageOption("eng", "English", False),
                OcrLanguageOption("deu", "German", True),
            ),
            DEFAULT_OCR_LANGUAGE_SET_ID,
            ("eng",),
            False,
        )

    assert captured.value.code is OcrErrorCode.MODEL_INVALID
