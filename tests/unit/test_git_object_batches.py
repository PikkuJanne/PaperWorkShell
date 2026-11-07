"""Binary framing, admission and complete-snapshot transport regressions."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools import dev


def _oid(number: int, width: int = 40) -> str:
    return f"{number:0{width}x}"


def _frames(objects: list[tuple[str, str, int]], bodies: list[bytes]) -> bytes:
    return b"".join(
        f"{oid} {kind} {size}\n".encode("ascii") + body + b"\n"
        for (oid, kind, size), body in zip(objects, bodies, strict=True)
    )


def _capture_result(monkeypatch: pytest.MonkeyPatch, output: bytes, code: int = 0) -> list[Any]:
    calls = []

    def capture(arguments: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, code, output, b"")

    monkeypatch.setattr(dev, "_capture_bytes", capture)
    return calls


def test_batch_preserves_binary_empty_repeated_bodies_and_exact_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies = [b"first\n\x00binary\xff", b"", b"first\n\x00binary\xff"]
    objects = [
        (_oid(1), "blob", len(bodies[0])),
        (_oid(2, 64), "tag", 0),
        (_oid(1), "blob", len(bodies[2])),
    ]
    output = _frames(objects, bodies)
    calls = _capture_result(monkeypatch, output)
    budget = dev._SecretScanEnumerationBudget()
    assert (
        list(dev._read_git_objects("model-git", tmp_path, objects, "synthetic", budget)) == bodies
    )
    request = b"".join(oid.encode("ascii") + b"\n" for oid, _, _ in objects)
    framing = len(output) - sum(map(len, bodies))
    assert budget.work_bytes == sum(map(len, bodies)) + framing + len(request)
    assert budget.metadata_bytes == framing + len(request)
    assert budget.records == len(objects)
    assert calls[0][0] == ("model-git", "cat-file", "--batch")
    assert calls[0][1]["input_bytes"] == request
    assert calls[0][1]["stdout_limit"] == len(output)


@pytest.mark.parametrize(
    "mode",
    [
        "wrong_oid",
        "duplicate_oid",
        "reordered",
        "wrong_type",
        "wrong_size",
        "noncanonical_size",
        "crlf_header",
        "short_header",
        "short_body",
        "missing_delimiter",
        "wrong_delimiter",
        "extra_frame",
        "extra_byte",
        "nonzero_exit",
    ],
)
def test_malformed_chunk_never_exposes_even_first_valid_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    bodies = [b"valid first\n\x00", b"second"]
    objects = [(_oid(1), "blob", len(bodies[0])), (_oid(2), "commit", len(bodies[1]))]
    first = _frames(objects[:1], bodies[:1])
    second = _frames(objects[1:], bodies[1:])
    if mode == "wrong_oid":
        second = second.replace(_oid(2).encode(), _oid(3).encode(), 1)
    elif mode == "duplicate_oid":
        second = second.replace(_oid(2).encode(), _oid(1).encode(), 1)
    elif mode == "reordered":
        first, second = second, first
    elif mode == "wrong_type":
        second = second.replace(b" commit ", b" blob ", 1)
    elif mode == "wrong_size":
        second = second.replace(b" commit 6\n", b" commit 7\n", 1)
    elif mode == "noncanonical_size":
        second = second.replace(b" commit 6\n", b" commit 06\n", 1)
    elif mode == "crlf_header":
        second = second.replace(b" commit 6\n", b" commit 6\r\n", 1)
    elif mode == "short_header":
        second = second[:12]
    elif mode == "short_body":
        second = second[:-3]
    elif mode == "missing_delimiter":
        second = second[:-1]
    elif mode == "wrong_delimiter":
        second = second[:-1] + b"\x00"
    output = first + second
    if mode == "extra_frame":
        output += first
    elif mode == "extra_byte":
        output += b"\n"
    _capture_result(monkeypatch, output, 1 if mode == "nonzero_exit" else 0)
    iterator = dev._read_git_objects(
        "model-git", tmp_path, objects, "synthetic", dev._SecretScanEnumerationBudget()
    )
    with pytest.raises(dev.SecretScanError) as failure:
        next(iterator)
    assert failure.value.code == "SECRET_SCAN_GIT_FAILED"
    assert "valid first" not in failure.value.message and _oid(2) not in failure.value.message


@pytest.mark.parametrize(
    "mode",
    [
        "count",
        "body",
        "work",
        "metadata",
        "records",
        "deadline",
        "object_limit",
        "invalid_id",
        "invalid_type",
    ],
)
def test_chunk_and_shared_budgets_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    objects = [(_oid(1), "blob", 1)]
    budget = dev._SecretScanEnumerationBudget()
    calls = _capture_result(monkeypatch, _frames(objects, [b"x"]))
    if mode == "count":
        objects *= 65
    elif mode == "body":
        objects = [(_oid(1), "blob", dev.MAX_GIT_OBJECT_BATCH_BODY_BYTES + 1)]
    elif mode == "work":
        budget.work_bytes = dev.MAX_SECRET_SCAN_WORK_BYTES
    elif mode == "metadata":
        budget.metadata_bytes = dev.MAX_SECRET_SCAN_METADATA_BYTES
    elif mode == "records":
        budget.records = dev.MAX_SECRET_SCAN_METADATA_RECORDS
    elif mode == "deadline":
        budget.started_at -= dev.MAX_SECRET_SCAN_SECONDS
    elif mode == "object_limit":
        objects = [(_oid(1), "blob", dev.MAX_SECRET_SCAN_BYTES + 1)]
    elif mode == "invalid_id":
        objects = [("--unsafe-option", "blob", 1)]
    else:
        objects = [(_oid(1), "tree", 1)]
    with pytest.raises(dev.SecretScanError):
        if mode in {"count", "body"}:
            dev._read_git_object_batch("model-git", tmp_path, objects, budget)
        else:
            list(dev._read_git_objects("model-git", tmp_path, objects, "synthetic", budget))
    assert not calls


def test_transport_chunks_have_finite_bytes_count_and_keep_large_single_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies = [b"" for _ in range(130)] + [
        b"a" * 600_000,
        b"b" * 600_000,
        b"c" * (1024 * 1024 + 1),
        b"last",
    ]
    objects = [(_oid(index + 1), "blob", len(body)) for index, body in enumerate(bodies)]
    mapping = {
        oid: (spec, body) for spec, body in zip(objects, bodies, strict=True) for oid in [spec[0]]
    }
    calls = []

    def capture(arguments: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if arguments[2] == "--batch":
            selected = [mapping[oid.decode("ascii")] for oid in kwargs["input_bytes"].splitlines()]
            assert len(selected) <= 64 and sum(len(body) for _, body in selected) <= 1024 * 1024
            calls.append(("batch", len(selected)))
            output = _frames([spec for spec, _ in selected], [body for _, body in selected])
        else:
            calls.append(("single", 1))
            output = mapping[arguments[3]][1]
        return subprocess.CompletedProcess(arguments, 0, output, b"")

    monkeypatch.setattr(dev, "_capture_bytes", capture)
    budget = dev._SecretScanEnumerationBudget()
    actual = list(dev._read_git_objects("model-git", tmp_path, objects, "synthetic", budget))
    assert actual == bodies
    assert calls == [
        ("batch", 64),
        ("batch", 64),
        ("batch", 3),
        ("batch", 1),
        ("single", 1),
        ("batch", 1),
    ]
    assert budget.work_bytes == sum(map(len, bodies)) + dev._git_object_transport_work(objects)


def test_collector_batches_objects_and_preserves_every_callback_and_live_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    config = b"synthetic configuration\n"
    body = b"synthetic unchanged body\n"
    message = b"tree " + b"0" * 40 + b"\n\nsynthetic message\n"
    data = {_oid(1): ("blob", config), _oid(2): ("blob", body), _oid(3): ("commit", message)}
    entries = [
        dev._PathBlobEntry(Path(name), oid, len(value))
        for name, oid, value in (("config.txt", _oid(1), config), ("baseline.txt", _oid(2), body))
    ]
    for entry in entries:
        (root / entry.relative_path).write_bytes(data[entry.object_id][1])
    roots = dev._GitHistoryRoots(
        (root,), (b"HEAD",), (_oid(3),), (), (_oid(3),), (_oid(3),), (), ()
    )
    monkeypatch.setattr(dev, "_git_executable", lambda *_args: "model-git")
    for name in (
        "_require_git_worktree_root",
        "_require_local_ref_storage",
        "_require_complete_git_history",
    ):
        monkeypatch.setattr(dev, name, lambda *_args: None)
    monkeypatch.setattr(dev, "_history_roots", lambda *_args: roots)
    monkeypatch.setattr(dev, "_historical_path_entries", lambda *_args: entries)
    monkeypatch.setattr(dev, "_index_entries", lambda *_args: entries)
    monkeypatch.setattr(
        dev,
        "_historical_objects",
        lambda *_args: (
            [(entry.object_id, entry.object_size) for entry in entries],
            [(_oid(3), "commit", len(message))],
        ),
    )
    monkeypatch.setattr(dev, "_git_paths", lambda *_args: [])
    calls = []

    def capture(arguments: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments[2])
        if arguments[2] == "--batch":
            selected = [oid.decode("ascii") for oid in kwargs["input_bytes"].splitlines()]
            output = _frames(
                [(oid, data[oid][0], len(data[oid][1])) for oid in selected],
                [data[oid][1] for oid in selected],
            )
        else:
            output = data[arguments[3]][1]
        return subprocess.CompletedProcess(arguments, 0, output, b"")

    monkeypatch.setattr(dev, "_capture_bytes", capture)
    original_read = dev._read_scan_input
    live_reads = []

    def read(*args: Any, **kwargs: Any) -> bytes | None:
        live_reads.append(args[1])
        return original_read(*args, **kwargs)

    monkeypatch.setattr(dev, "_read_scan_input", read)
    inspected: list[bytes] = []
    summary = dev.inspect_repository_contents(root, inspected.append)
    assert summary.history_blob_count == 2 and summary.history_message_object_count == 1
    assert inspected.count(config) == inspected.count(body) == 2
    assert inspected.index(config) < inspected.index(body) < inspected.index(b"synthetic message\n")
    assert len(live_reads) == 6
    assert calls == ["--batch", "--batch"]
