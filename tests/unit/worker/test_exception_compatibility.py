"""Regression tests for interpreter-managed worker exception state."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientError,
    WorkerClientErrorCode,
)
from paperworkshell.worker.protocol.bootstrap import BootstrapError, BootstrapErrorCode
from paperworkshell.worker.protocol.errors import ProtocolError


@contextmanager
def _passthrough_context() -> Iterator[None]:
    yield


@pytest.mark.parametrize(
    ("error", "expected_text", "expected_repr", "expected_fields"),
    (
        (
            WorkerClientError(
                WorkerClientErrorCode.STATE_INVALID,
                WorkerClientContext.STATE,
            ),
            (
                "WORKER_CLIENT_STATE_INVALID at client.state: "
                "The worker action is unavailable in its current state."
            ),
            (
                "WorkerClientError("
                "code=<WorkerClientErrorCode.STATE_INVALID: "
                "'WORKER_CLIENT_STATE_INVALID'>, "
                "context=<WorkerClientContext.STATE: 'client.state'>)"
            ),
            (
                ("code", WorkerClientErrorCode.STATE_INVALID),
                ("context", WorkerClientContext.STATE),
                (
                    "safe_message",
                    "The worker action is unavailable in its current state.",
                ),
            ),
        ),
        (
            ProtocolError(
                "RPC_TEST_FAILURE",
                "payload.test",
                "The worker message is invalid.",
            ),
            "RPC_TEST_FAILURE at payload.test: The worker message is invalid.",
            (
                "ProtocolError(code='RPC_TEST_FAILURE', location='payload.test', "
                "safe_message='The worker message is invalid.')"
            ),
            (
                ("code", "RPC_TEST_FAILURE"),
                ("location", "payload.test"),
                ("safe_message", "The worker message is invalid."),
            ),
        ),
        (
            BootstrapError(
                BootstrapErrorCode.INVALID_SIZE,
                "The bootstrap grant is invalid.",
            ),
            "WORKER_BOOTSTRAP_INVALID_SIZE: The bootstrap grant is invalid.",
            (
                "BootstrapError("
                "code=<BootstrapErrorCode.INVALID_SIZE: "
                "'WORKER_BOOTSTRAP_INVALID_SIZE'>, "
                "safe_message='The bootstrap grant is invalid.')"
            ),
            (
                ("code", BootstrapErrorCode.INVALID_SIZE),
                ("safe_message", "The bootstrap grant is invalid."),
            ),
        ),
    ),
)
def test_worker_errors_survive_generator_context_manager_propagation(
    error: BaseException,
    expected_text: str,
    expected_repr: str,
    expected_fields: tuple[tuple[str, object], ...],
) -> None:
    with pytest.raises(type(error)) as raised:
        with _passthrough_context():
            raise error

    assert raised.value is error
    assert type(raised.value) is type(error)
    assert str(raised.value) == expected_text
    assert repr(raised.value) == expected_repr
    for field_name, expected_value in expected_fields:
        assert getattr(raised.value, field_name) == expected_value

    marker = RuntimeError()
    traceback = raised.value.__traceback__
    raised.value.__cause__ = marker
    raised.value.__context__ = marker
    raised.value.__traceback__ = traceback
    assert raised.value.__cause__ is marker
    assert raised.value.__context__ is marker
    assert raised.value.__traceback__ is traceback
