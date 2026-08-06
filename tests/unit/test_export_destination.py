"""Pure contract tests for the PWS-0112 trusted export destination boundary."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from paperworkshell.storage.export_destination import (
    ExportCommitState,
    ExportDestinationDependencies,
    ExportDestinationError,
    ExportDestinationErrorCode,
    ExportDestinationResult,
    WindowsExportDestination,
)


def _never_cancel() -> bool:
    return False


@pytest.mark.parametrize(
    "destination",
    [
        Path("relative.pdf"),
        Path("C:drive-relative.pdf"),
        Path(r"\\server\share\packet.pdf"),
        Path(r"\\?\C:\exports\packet.pdf"),
        Path(r"\\.\C:\exports\packet.pdf"),
        Path(r"\??\C:\exports\packet.pdf"),
        Path(r"C:\exports\..\packet.pdf"),
        Path(r"C:\exports\packet.pdf:private"),
    ],
)
def test_nonlocal_or_traversal_destination_fails_before_source_read(
    destination: Path,
) -> None:
    source = BytesIO(b"verified")

    with pytest.raises(ExportDestinationError) as raised:
        WindowsExportDestination().commit(
            destination,
            source,
            expected_byte_size=8,
            expected_sha256=hashlib.sha256(b"verified").hexdigest(),
            cancellation=_never_cancel,
        )

    assert raised.value.code in {
        ExportDestinationErrorCode.PATH_INVALID,
        ExportDestinationErrorCode.LOCATION_UNSUPPORTED,
    }
    assert source.tell() == 0


@pytest.mark.parametrize(
    "name",
    [
        "CON.pdf",
        "nul",
        "LPT9.txt",
        "packet.pdf.",
        "packet.pdf ",
        "bad?.pdf",
        "line\nbreak.pdf",
        "zero\u200bwidth.pdf",
        "e\u0301.pdf",
        f"{'a' * 181}.pdf",
    ],
)
def test_unsafe_or_reserved_final_name_is_rejected_without_disclosure(name: str) -> None:
    destination = Path("C:/synthetic-export") / name

    with pytest.raises(ExportDestinationError) as raised:
        WindowsExportDestination().commit(
            destination,
            BytesIO(b"x"),
            expected_byte_size=1,
            expected_sha256=hashlib.sha256(b"x").hexdigest(),
            cancellation=_never_cancel,
        )

    rendered = f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert raised.value.code is ExportDestinationErrorCode.NAME_INVALID
    assert name not in rendered
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_committed_result_hides_digest_and_rejects_incoherent_models() -> None:
    digest = hashlib.sha256(b"verified").hexdigest()
    result = ExportDestinationResult(ExportCommitState.COMMITTED, 8, digest)

    assert result.committed
    assert result.byte_size == 8
    assert result.sha256 == digest
    assert digest not in repr(result)

    with pytest.raises(ExportDestinationError) as missing_artifact:
        ExportDestinationResult(ExportCommitState.COMMITTED)
    assert missing_artifact.value.code is ExportDestinationErrorCode.MODEL_INVALID

    with pytest.raises(ExportDestinationError) as unexpected_artifact:
        ExportDestinationResult(ExportCommitState.NOT_COMMITTED, 8, digest)
    assert unexpected_artifact.value.code is ExportDestinationErrorCode.MODEL_INVALID


def test_dependency_and_source_contracts_are_closed_and_sanitized() -> None:
    with pytest.raises(ExportDestinationError) as dependency:
        ExportDestinationDependencies(checkpoint="not-callable")  # type: ignore[arg-type]
    assert dependency.value.code is ExportDestinationErrorCode.MODEL_INVALID

    with pytest.raises(ExportDestinationError) as source:
        WindowsExportDestination().commit(
            Path(r"C:\synthetic-export\packet.pdf"),
            object(),  # type: ignore[arg-type]
            expected_byte_size=0,
            expected_sha256="private-digest-canary",
            cancellation=_never_cancel,
        )
    rendered = f"{source.value!s}\n{source.value!r}\n{source.value.args!r}"
    assert source.value.code is ExportDestinationErrorCode.MODEL_INVALID
    assert "private-digest-canary" not in rendered
