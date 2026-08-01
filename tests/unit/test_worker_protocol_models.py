from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch
from uuid import UUID

from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MESSAGE_VERSION,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    CaseObjectReference,
    Direction,
    ErrorCategory,
    ErrorParameter,
    ErrorPayload,
    MessageType,
    OperationOutcome,
    OperationResultPayload,
    ProtocolEnvelope,
    ReferenceArea,
    ResourceLimits,
    RetryAction,
    ScalarValueType,
    SessionCapability,
    SessionHelloPayload,
    TaggedScalar,
)


def _uuid(number: int) -> UUID:
    return UUID(f"10000000-0000-4000-8000-{number:012x}")


def _limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=1,
        cpu_time_ms=1,
        memory_bytes=1,
        temporary_storage_bytes=1,
        output_bytes=1,
        page_count=1,
        pixel_count=1,
    )


class WorkerProtocolModelTests(unittest.TestCase):
    def test_capability_is_redacted_unhashable_and_compared_in_constant_time(self) -> None:
        canary = "c" * 64
        capability = SessionCapability(canary)
        same = SessionCapability(canary)
        different = SessionCapability("d" * 64)

        with patch(
            "paperworkshell.worker.protocol.models.hmac.compare_digest",
            wraps=__import__("hmac").compare_digest,
        ) as compared:
            self.assertEqual(capability, same)
            self.assertNotEqual(capability, different)

        self.assertEqual(2, compared.call_count)
        self.assertNotIn(canary, repr(capability))
        self.assertNotIn(canary, str(capability))
        with self.assertRaises(TypeError):
            hash(capability)

    def test_models_are_frozen_slotted_and_hide_parameter_values_from_repr(self) -> None:
        parameter = TaggedScalar("operation_kind", ScalarValueType.TOKEN, "sensitive.token")

        self.assertFalse(hasattr(parameter, "__dict__"))
        self.assertNotIn("sensitive.token", repr(parameter))
        with self.assertRaises(FrozenInstanceError):
            parameter.name = "changed"  # type: ignore[misc]

    def test_boolean_is_not_accepted_as_an_integer(self) -> None:
        with self.assertRaises(ProtocolError) as raised:
            ResourceLimits(
                wall_time_ms=True,
                cpu_time_ms=1,
                memory_bytes=1,
                temporary_storage_bytes=1,
                output_bytes=1,
                page_count=1,
                pixel_count=1,
            )

        self.assertEqual("RPC_INVALID_INTEGER", raised.exception.code)

    def test_session_hello_must_offer_protocol_v1(self) -> None:
        with self.assertRaises(ProtocolError) as raised:
            SessionHelloPayload((2,), ())

        self.assertEqual("RPC_UNSUPPORTED_VERSION", raised.exception.code)

    def test_error_parameters_are_allowlisted_and_restart_flag_is_derived(self) -> None:
        with self.assertRaises(ProtocolError) as unknown:
            ErrorParameter("raw_message", ScalarValueType.TOKEN, "synthetic")
        with self.assertRaises(ProtocolError) as wrong_type:
            ErrorParameter("operation_id", ScalarValueType.TOKEN, "synthetic")
        with self.assertRaises(ProtocolError) as restart_mismatch:
            ErrorPayload(
                code="SYNTHETIC_FAILURE",
                category=ErrorCategory.INTERNAL,
                retry_action=RetryAction.RESTART_WORKER,
                restart_required=False,
                parameters=(),
            )

        self.assertEqual("RPC_INVALID_ERROR_PARAMETER", unknown.exception.code)
        self.assertEqual("RPC_INVALID_ERROR_PARAMETER", wrong_type.exception.code)
        self.assertEqual("RPC_INVALID_ERROR", restart_mismatch.exception.code)

    def test_result_outcomes_enforce_output_and_error_contracts(self) -> None:
        error = ErrorPayload(
            code="SYNTHETIC_FAILURE",
            category=ErrorCategory.OPERATION,
            retry_action=RetryAction.NEVER,
            restart_required=False,
            parameters=(),
        )
        for outcome, outputs, result_error in (
            (OperationOutcome.SUCCEEDED, (), error),
            (OperationOutcome.CANCELLED, (), error),
            (OperationOutcome.FAILED, (), None),
            (OperationOutcome.TIMED_OUT, (), None),
        ):
            with self.subTest(outcome=outcome), self.assertRaises(ProtocolError):
                OperationResultPayload(outcome, outputs, result_error)

    def test_result_outputs_must_be_verified_staging_references_for_envelope_operation(
        self,
    ) -> None:
        envelope_operation_id = _uuid(8)
        invalid_references = (
            CaseObjectReference(
                area=ReferenceArea.OPERATION_TEMP,
                object_id=_uuid(9),
                operation_id=envelope_operation_id,
                content_kind="application.pdf",
                expected_byte_size=1,
                expected_sha256="e" * 64,
            ),
            CaseObjectReference(
                area=ReferenceArea.OPERATION_STAGING,
                object_id=_uuid(10),
                operation_id=envelope_operation_id,
                content_kind="application.pdf",
                expected_byte_size=None,
                expected_sha256=None,
            ),
        )
        for reference in invalid_references:
            with self.subTest(area=reference.area), self.assertRaises(ProtocolError) as raised:
                ProtocolEnvelope(
                    protocol=PROTOCOL_NAME,
                    protocol_version=PROTOCOL_VERSION,
                    message_version=MESSAGE_VERSION,
                    message_type=MessageType.OPERATION_RESULT,
                    direction=Direction.WORKER_TO_CLIENT,
                    session_id=_uuid(1),
                    worker_instance_id=_uuid(2),
                    session_capability=SessionCapability("f" * 64),
                    case_id=_uuid(3),
                    message_id=_uuid(4),
                    sender_sequence=0,
                    request_id=_uuid(5),
                    operation_id=envelope_operation_id,
                    payload=OperationResultPayload(
                        OperationOutcome.SUCCEEDED,
                        (reference,),
                        None,
                    ),
                )

            self.assertEqual("RPC_REFERENCE_SCOPE", raised.exception.code)

    def test_limit_models_require_positive_integer_budgets(self) -> None:
        self.assertEqual(1, _limits().limits_version)
        with self.assertRaises(ProtocolError):
            ResourceLimits(
                wall_time_ms=0,
                cpu_time_ms=1,
                memory_bytes=1,
                temporary_storage_bytes=1,
                output_bytes=1,
                page_count=1,
                pixel_count=1,
            )


if __name__ == "__main__":
    unittest.main()
