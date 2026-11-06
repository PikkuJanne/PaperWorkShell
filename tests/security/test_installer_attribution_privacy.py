"""The admitted installer notice keeps exact public attribution, never user data."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from tools import validate_synthetic_fixtures as validator

ROOT = Path(__file__).resolve().parents[2]
NOTICE = "packaging/licenses/release-installer/inno-setup-7.1.1/UniPs-LICENSE.md"
DIGEST = "a08799bb2385a3af99a9bbb7c746d0ad9ac7dd07be0fb1473f2f4d1dc3e11bd1"


def test_admitted_installer_notice_preserves_exact_public_attribution() -> None:
    content = (ROOT / NOTICE).read_bytes()
    assert hashlib.sha256(content).hexdigest() == DIGEST
    assert validator._contains_prohibited_pattern(content)
    validator._repository_inspector(frozenset())(content)


@pytest.mark.parametrize("suffix", [b"\n", b"\nUnreviewed additional contact\n"])
def test_changed_installer_notice_remains_unapproved(suffix: bytes) -> None:
    content = (ROOT / NOTICE).read_bytes() + suffix
    assert validator._contains_prohibited_pattern(content, reviewed_source_notice=True)
    with pytest.raises(validator.RepositoryPrivacyFinding):
        validator._repository_inspector(frozenset())(content)


def test_installer_notice_review_binds_existing_upstream_source() -> None:
    review = json.loads(
        (ROOT / "packaging/windows/inno-setup-7.1.1-review.json").read_text(encoding="utf-8")
    )
    record = next(item for item in review["license_files"] if item["path"] == NOTICE)
    assert record["sha256"] == DIGEST
    assert record["size_bytes"] == (ROOT / NOTICE).stat().st_size
    # The source archive is an ignored build input; the pinned checked-in review
    # remains available to offline source tests without requiring downloaded data.
    archive = ROOT / review["source_local_path"]
    if archive.is_file():
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == review["source_sha256"]
        with zipfile.ZipFile(archive) as source:
            names = [name for name in source.namelist() if name.endswith("/UniPs/LICENSE.md")]
            assert len(names) == 1
            assert source.read(names[0]) == (ROOT / NOTICE).read_bytes()
