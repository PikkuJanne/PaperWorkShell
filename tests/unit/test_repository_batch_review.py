"""Independent byte-protocol and snapshot review; no Git or scanner subprocesses."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from tests.unit.test_repository_scan_batching import _snapshot
from tools import dev

ObjectSpec = tuple[str, str, int]


def _object(number: int, content: bytes, kind: str = "blob") -> ObjectSpec:
    return f"{number:040x}", kind, len(content)


def _frame(specification: ObjectSpec, content: bytes) -> bytes:
    oid, kind, size = specification
    return f"{oid} {kind} {size}\n".encode("ascii") + content + b"\n"


def _transport(
    monkeypatch: pytest.MonkeyPatch,
    values: Sequence[tuple[ObjectSpec, bytes]],
    *,
    change: Callable[[bytes], bytes] | None = None,
) -> list[tuple[str, ...]]:
    bodies = {spec[0]: (spec, body) for spec, body in values}
    requests: list[tuple[str, ...]] = []

    def capture(
        arguments: Sequence[str],
        *,
        cwd: Path,
        input_bytes: bytes | None = None,
        stdout_limit: int = dev.MAX_SCANNER_DIAGNOSTIC_BYTES,
        timeout_seconds: float = dev.SCANNER_SUBPROCESS_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess[bytes]:
        assert "cat-file" in arguments and "--batch" in arguments
        assert input_bytes is not None and input_bytes.endswith(b"\n")
        assert stdout_limit > 0 and timeout_seconds > 0
        ids = tuple(row.decode("ascii") for row in input_bytes.splitlines())
        requests.append(ids)
        payload = b"".join(_frame(*bodies[oid]) for oid in ids)
        return subprocess.CompletedProcess(
            arguments, 0, payload if change is None else change(payload)
        )

    monkeypatch.setattr(dev, "_capture_bytes", capture)
    return requests


def test_binary_bodies_duplicate_requests_and_empty_objects_retain_exact_callback_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"\x00\n" + b"1" * 40 + b" blob 99\ninside the body\n"
    first, empty = _object(1, content), _object(2, b"")
    requests = _transport(monkeypatch, [(first, content), (empty, b"")])
    budget = dev._SecretScanEnumerationBudget()
    observed = list(
        dev._read_git_objects("model-git", tmp_path, [first, empty, first], "review", budget)
    )
    assert observed == [content, b"", content]
    assert requests == [(first[0], empty[0], first[0])]
    assert budget.records == 3
    framed_size = len(_frame(first, content)) * 2 + len(_frame(empty, b""))
    assert budget.work_bytes >= framed_size


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_oid",
        "wrong_type",
        "wrong_size",
        "duplicate",
        "out_of_order",
        "missing",
        "truncated",
        "extra",
    ],
)
def test_no_body_escapes_a_chunk_with_a_later_invalid_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    first, second = _object(1, b"good"), _object(2, b"late")
    good_first, good_second = _frame(first, b"good"), _frame(second, b"late")

    def change(_payload: bytes) -> bytes:
        if fault == "wrong_oid":
            return good_first + good_second.replace(second[0].encode(), b"f" * 40, 1)
        if fault == "wrong_type":
            return good_first + good_second.replace(b" blob ", b" tag ", 1)
        if fault == "wrong_size":
            return good_first + good_second.replace(b" blob 4\n", b" blob 5\n", 1)
        if fault == "duplicate":
            return good_first + good_first
        if fault == "out_of_order":
            return good_second + good_first
        if fault == "missing":
            return good_first
        if fault == "truncated":
            return good_first + good_second[:-1]
        return good_first + good_second + b"unexpected trailing bytes"

    _transport(monkeypatch, [(first, b"good"), (second, b"late")], change=change)
    observed: list[bytes] = []
    with pytest.raises(dev.SecretScanError):
        for body in dev._read_git_objects(
            "model-git", tmp_path, [first, second], "review", dev._SecretScanEnumerationBudget()
        ):
            observed.append(body)
    assert observed == []


@pytest.mark.parametrize("mode", ["objects", "body_bytes"])
def test_transport_chunks_respect_both_independent_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    count, body = (65, b"x") if mode == "objects" else (3, b"x" * (400 * 1024))
    values = [(_object(index + 1, body), body) for index in range(count)]
    requests = _transport(monkeypatch, values)
    result = list(
        dev._read_git_objects(
            "model-git",
            tmp_path,
            [spec for spec, _body in values],
            "review",
            dev._SecretScanEnumerationBudget(),
        )
    )
    assert result == [body] * count
    assert len(requests) == 2
    assert all(len(chunk) <= 64 and len(chunk) * len(body) <= 1024 * 1024 for chunk in requests)


def test_oversized_singleton_uses_the_original_bounded_reader_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    large_body = b"x" * (1024 * 1024 + 1)
    first, large, last = _object(1, b"before"), _object(2, large_body), _object(3, b"after")
    requests = _transport(monkeypatch, [(first, b"before"), (last, b"after")])
    original_reads: list[ObjectSpec] = []

    def singleton(
        _git: str,
        _root: Path,
        oid: str,
        kind: str,
        size: int,
        _label: str,
        budget: dev._SecretScanEnumerationBudget,
    ) -> bytes:
        original_reads.append((oid, kind, size))
        budget.consume_work(size)
        return large_body

    monkeypatch.setattr(dev, "_read_git_object", singleton)
    result = list(
        dev._read_git_objects(
            "model-git",
            tmp_path,
            [first, large, last],
            "review",
            dev._SecretScanEnumerationBudget(),
        )
    )
    assert result == [b"before", large_body, b"after"]
    assert original_reads == [large]
    assert [oid for chunk in requests for oid in chunk] == [first[0], last[0]]


@pytest.mark.parametrize("cap", ["work", "metadata_bytes", "records"])
def test_shared_budget_exhaustion_prevents_every_callback_in_its_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap: str
) -> None:
    first, second = _object(1, b"body"), _object(2, b"body")
    _transport(monkeypatch, [(first, b"body"), (second, b"body")])
    if cap == "work":
        monkeypatch.setattr(dev, "MAX_SECRET_SCAN_WORK_BYTES", 1)
    elif cap == "metadata_bytes":
        monkeypatch.setattr(dev, "MAX_SECRET_SCAN_METADATA_BYTES", 1)
    else:
        monkeypatch.setattr(dev, "MAX_SECRET_SCAN_METADATA_RECORDS", 1)
    observed: list[bytes] = []
    with pytest.raises(dev.SecretScanError):
        for body in dev._read_git_objects(
            "model-git", tmp_path, [first, second], "review", dev._SecretScanEnumerationBudget()
        ):
            observed.append(body)
    assert observed == []


def test_known_path_blob_metadata_is_reused_but_unknown_objects_keep_sorted_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    oids = [f"{number:040x}" for number in range(1, 5)]
    entries = [
        dev._PathBlobEntry(Path("second.txt"), oids[1], 2),
        dev._PathBlobEntry(Path("last.txt"), oids[3], 4),
    ]
    roots = dev._GitHistoryRoots((), (), (), (), (), (), (), ())
    unknown_requests: list[list[str]] = []

    def capture(
        _budget: dev._SecretScanEnumerationBudget, arguments: Sequence[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        assert "rev-list" in arguments
        return subprocess.CompletedProcess(
            arguments, 0, b"".join(oid.encode() + b"\n" for oid in reversed(oids))
        )

    def metadata(
        _git: str, _root: Path, selected: Sequence[str], _budget: dev._SecretScanEnumerationBudget
    ) -> list[ObjectSpec]:
        unknown_requests.append(list(selected))
        assert oids[1] not in selected and oids[3] not in selected
        return [
            (oid, "commit" if oid == oids[2] else "blob", 3 if oid == oids[2] else 1)
            for oid in selected
        ]

    monkeypatch.setattr(dev, "_capture_git_metadata", capture)
    monkeypatch.setattr(dev, "_git_object_metadata", metadata)
    blobs, messages = dev._historical_objects(
        "model-git", tmp_path, roots, entries, dev._SecretScanEnumerationBudget()
    )
    assert unknown_requests == [[oids[0], oids[2]]]
    assert blobs == [(oids[0], 1), (oids[1], 2), (oids[3], 4)]
    assert messages == [(oids[2], "commit", 3)]


@pytest.mark.parametrize("omit_transport_negative_control", [False, True])
def test_known_batch_framing_is_forecast_before_starting_the_secret_scanner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, omit_transport_negative_control: bool
) -> None:
    root = tmp_path / "repository"
    ignore, roots, _payloads = _snapshot(root, monkeypatch)
    blobs, messages = dev._historical_objects(
        "model-git", root, roots, [], dev._SecretScanEnumerationBudget()
    )
    # Zero-byte objects still transport OID requests and response headers. Their
    # old raw-input forecast fits; known batch framing pushes this model over 8KiB.
    extras = [(f"{number:040x}", 0) for number in range(100, 164)]
    monkeypatch.setattr(dev, "_historical_objects", lambda *_args: ([*blobs, *extras], messages))
    monkeypatch.setattr(dev, "MAX_SECRET_SCAN_WORK_BYTES", 8 * 1024)

    class ScannerStarted(Exception):
        pass

    def start(_budget: dev._SecretScanEnumerationBudget) -> str:
        raise ScannerStarted("Pure model reached tool admission")

    monkeypatch.setattr(dev, "_gitleaks_executable", start)
    if omit_transport_negative_control:
        # Deliberately disable only the new transport forecast. This is a pure
        # negative control, not a retained historical source/test observation.
        monkeypatch.setattr(dev, "_git_object_transport_work", lambda _objects: 0)
        monkeypatch.setattr(dev, "_path_blob_transport_work", lambda _entries: 0)
        with pytest.raises(ScannerStarted):
            dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
    else:
        with pytest.raises(dev.SecretScanError) as failure:
            dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
        assert failure.value.code == "SECRET_SCAN_INPUT_TOO_LARGE"


def test_conflicting_cached_blob_metadata_is_rejected_before_unknown_metadata_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = f"{2:040x}"
    entries = [
        dev._PathBlobEntry(Path("one.txt"), oid, 1),
        dev._PathBlobEntry(Path("two.txt"), oid, 2),
    ]
    roots = dev._GitHistoryRoots((), (), (), (), (), (), (), ())
    monkeypatch.setattr(
        dev,
        "_capture_git_metadata",
        lambda *_args, **_kwargs: subprocess.CompletedProcess("model", 0, oid.encode() + b"\n"),
    )
    monkeypatch.setattr(
        dev, "_git_object_metadata", lambda *_args: pytest.fail("Conflicting immutable metadata")
    )
    with pytest.raises(dev.SecretScanError) as failure:
        dev._historical_objects(
            "model-git", tmp_path, roots, entries, dev._SecretScanEnumerationBudget()
        )
    assert failure.value.code == "SECRET_SCAN_GIT_FAILED"
