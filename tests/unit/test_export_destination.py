"""Pure contract tests for the PWS-0112 trusted export destination boundary."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest

import paperworkshell.storage.export_destination as destination_storage
from paperworkshell.storage._windows import FileIdentity, OpenedFileInfo, WindowsHandle
from paperworkshell.storage.export_destination import (
    ExportCommitState,
    ExportDestinationDependencies,
    ExportDestinationError,
    ExportDestinationErrorCode,
    ExportDestinationPhase,
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


def test_deadline_and_cancellation_are_separate_closed_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("paperworkshell.storage.export_destination.os.name", "nt")
    destination = Path(r"C:\synthetic-export\packet.pdf")

    with pytest.raises(ExportDestinationError) as timed_out:
        WindowsExportDestination().commit(
            destination,
            BytesIO(b"x"),
            expected_byte_size=1,
            expected_sha256=hashlib.sha256(b"x").hexdigest(),
            cancellation=_never_cancel,
            deadline=lambda: True,
        )
    assert timed_out.value.code is ExportDestinationErrorCode.TIMED_OUT

    with pytest.raises(ExportDestinationError) as cancelled:
        WindowsExportDestination().commit(
            destination,
            BytesIO(b"x"),
            expected_byte_size=1,
            expected_sha256=hashlib.sha256(b"x").hexdigest(),
            cancellation=lambda: True,
            deadline=lambda: True,
        )
    assert cancelled.value.code is ExportDestinationErrorCode.CANCELLED


@pytest.mark.parametrize("failure", (KeyboardInterrupt(), RuntimeError("private probe")))
def test_deadline_base_exception_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    monkeypatch.setattr("paperworkshell.storage.export_destination.os.name", "nt")

    def deadline() -> bool:
        raise failure

    with pytest.raises(ExportDestinationError) as raised:
        WindowsExportDestination().commit(
            Path(r"C:\synthetic-export\packet.pdf"),
            BytesIO(b"x"),
            expected_byte_size=1,
            expected_sha256=hashlib.sha256(b"x").hexdigest(),
            cancellation=_never_cancel,
            deadline=deadline,
        )

    assert raised.value.code is ExportDestinationErrorCode.INTERNAL_ERROR
    assert "private probe" not in str(raised.value)


class _ReadbackStage:
    def __init__(self, info: OpenedFileInfo) -> None:
        self.info = info
        self.reads = 0

    def refresh_info(self) -> OpenedFileInfo:
        return self.info

    def rewind(self) -> None:
        return

    def read_exact(self, _count: int) -> bytes:
        self.reads += 1
        raise AssertionError("deadline must be checked before the next readback chunk")


def test_deadline_is_checked_inside_stage_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path(r"C:\synthetic-export\.stage.pws-export.tmp")
    info = OpenedFileInfo(
        FileIdentity(7, b"synthetic-stage"),
        path,
        False,
        False,
        False,
        1,
        1,
    )
    stage = _ReadbackStage(info)
    monkeypatch.setattr(destination_storage, "require_no_alternate_streams", lambda _path: None)

    with pytest.raises(ExportDestinationError) as raised:
        destination_storage._verify_stage_readback(
            cast(WindowsHandle, stage),
            path,
            expected_byte_size=1,
            expected_sha256=hashlib.sha256(b"x").hexdigest(),
            cancellation=_never_cancel,
            deadline=lambda: True,
        )

    assert raised.value.code is ExportDestinationErrorCode.TIMED_OUT
    assert stage.reads == 0


def test_deadline_is_rechecked_after_before_rename_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired = False
    renamed = False
    destination = Path(r"C:\synthetic-export\packet.pdf")
    stage_path = destination.parent / ".stage.pws-export.tmp"
    info = OpenedFileInfo(
        FileIdentity(7, b"synthetic-stage"),
        stage_path,
        False,
        False,
        False,
        1,
        1,
    )
    stage = cast(WindowsHandle, _ReadbackStage(info))

    def checkpoint(phase: ExportDestinationPhase) -> None:
        nonlocal expired
        if phase is ExportDestinationPhase.BEFORE_RENAME:
            expired = True

    def rename_stage(
        _stage: WindowsHandle,
        _parent: WindowsHandle,
        _name: str,
    ) -> tuple[bool, int]:
        nonlocal renamed
        renamed = True
        return True, 0

    backend = WindowsExportDestination(
        ExportDestinationDependencies(checkpoint=checkpoint, rename_stage=rename_stage)
    )
    monkeypatch.setattr(
        WindowsExportDestination,
        "_create_unique_stage",
        lambda *_args, **_kwargs: (stage, stage_path),
    )
    monkeypatch.setattr(destination_storage, "_stream_and_verify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(destination_storage, "_authenticate_parent", lambda *_args: None)
    monkeypatch.setattr(destination_storage, "_finish_stage", lambda *_args, **_kwargs: None)

    with pytest.raises(ExportDestinationError) as raised:
        backend._stage_and_commit(
            cast(WindowsHandle, object()),
            destination,
            BytesIO(b"x"),
            expected_byte_size=1,
            expected_sha256=hashlib.sha256(b"x").hexdigest(),
            cancellation=_never_cancel,
            deadline=lambda: expired,
        )

    assert raised.value.code is ExportDestinationErrorCode.TIMED_OUT
    assert not renamed
