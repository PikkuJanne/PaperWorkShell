"""Privacy and closed-input checks for PWS-0307 human reader evidence."""

from __future__ import annotations

import json
import sys

import pytest

from tools.pws0307_reader_test import (
    APPROVED_READER_ARTIFACT,
    ReaderAnswers,
    ReaderSlot,
    ReaderTestError,
    _result_payload,
)


def _system_record() -> dict[str, str]:
    return {
        "platform": "Windows",
        "edition": "Professional",
        "release": "11",
        "version_build": "10.0.26200",
        "architecture": "AMD64",
    }


def _passing_answers() -> ReaderAnswers:
    return ReaderAnswers(
        configuration_confirmed=True,
        page_sequence_passed=True,
        visual_zoom_sweep_passed=True,
        search_matches=3,
        noninteractive_passed=True,
        close_reopen_passed=True,
    )


def test_reader_result_has_only_closed_content_free_evidence() -> None:
    payload = _result_payload(
        ReaderSlot.W3,
        "151.0.7922.174",
        "built_in_local_pdf_viewer",
        _passing_answers(),
        1,
        APPROVED_READER_ARTIFACT,
        tested_at_utc="2026-08-27T12:00:00Z",
        system_record=_system_record(),
    )
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)

    assert "Reader matrix text" not in encoded
    assert "2026-08-25" not in encoded
    assert "born-digital.pdf" not in encoded
    assert "render-geometry-matrix.pdf" not in encoded
    assert "synthetic-person" not in encoded
    assert "C:\\" not in encoded
    assert "path" not in encoded.casefold()
    assert "note" not in encoded.casefold()
    assert "account" not in encoded.casefold()
    assert payload["result"] == "pass"
    assert payload["defect_categories"] == []


@pytest.mark.parametrize(
    "unsafe_version",
    (
        r"151 C:\Users\synthetic-person\profile",
        "Alice Applicant",
        "151 Official build",
        "151:synthetic",
        "151/browser/profile",
        "151\nsynthetic",
        "x" * 121,
    ),
)
def test_path_profile_and_control_text_cannot_enter_viewer_version(
    unsafe_version: str,
) -> None:
    with pytest.raises(ReaderTestError) as raised:
        _result_payload(
            ReaderSlot.W4,
            unsafe_version,
            "built_in_local_pdf_viewer",
            _passing_answers(),
            1,
            APPROVED_READER_ARTIFACT,
            tested_at_utc="2026-08-27T12:00:00Z",
            system_record=_system_record(),
        )

    assert raised.value.code == "PWS0307_READER_INPUT_INVALID"
    assert unsafe_version not in str(raised.value)
    assert "synthetic-person" not in str(raised.value)


@pytest.mark.parametrize(
    "non_reader_mode",
    ("acrobat_standard", "acrobat_pro", "acrobat_mode_not_shown"),
)
def test_non_reader_adobe_modes_cannot_satisfy_w1(non_reader_mode: str) -> None:
    with pytest.raises(ReaderTestError) as raised:
        _result_payload(
            ReaderSlot.W1,
            "26.1.21789.0",
            non_reader_mode,
            _passing_answers(),
            1,
            APPROVED_READER_ARTIFACT,
            tested_at_utc="2026-08-27T12:00:00Z",
            system_record=_system_record(),
        )

    assert raised.value.code == "PWS0307_READER_INPUT_INVALID"


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    (
        ("edition", "Alice Applicant"),
        ("release", r"11\Users\synthetic-person"),
        ("version_build", "10.0.private"),
        ("architecture", "synthetic-account"),
    ),
)
def test_free_text_cannot_enter_closed_windows_record(field: str, unsafe_value: str) -> None:
    system_record = _system_record()
    system_record[field] = unsafe_value

    with pytest.raises(ReaderTestError) as raised:
        _result_payload(
            ReaderSlot.W4,
            "154.0",
            "built_in_local_pdf_viewer",
            _passing_answers(),
            1,
            APPROVED_READER_ARTIFACT,
            tested_at_utc="2026-08-27T12:00:00Z",
            system_record=system_record,
        )

    assert raised.value.code == "PWS0307_READER_INPUT_INVALID"
    assert unsafe_value not in str(raised.value)


def test_automatic_environment_capture_fails_closed_outside_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(ReaderTestError) as raised:
        _result_payload(
            ReaderSlot.W3,
            "151.0.7922.174",
            "built_in_local_pdf_viewer",
            _passing_answers(),
            1,
            APPROVED_READER_ARTIFACT,
            tested_at_utc="2026-08-27T12:00:00Z",
        )

    assert raised.value.code == "PWS0307_READER_WINDOWS_REQUIRED"
