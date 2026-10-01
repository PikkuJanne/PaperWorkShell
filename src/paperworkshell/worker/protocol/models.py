"""Typed and self-validating models for worker RPC protocol version 1."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID

from paperworkshell.worker.protocol.errors import reject

PROTOCOL_NAME: Final = "paperworkshell.worker-rpc"
PROTOCOL_VERSION: Final = 1
MESSAGE_VERSION: Final = 1
MAX_MESSAGE_BYTES: Final = 65_536
MAX_JSON_DEPTH: Final = 32
MAX_JSON_NODES: Final = 4_096
MAX_JSON_STRING_CODEPOINTS: Final = 4_096
MAX_JSON_COLLECTION_ITEMS: Final = 256
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
MAX_PARAMETERS: Final = 64

_TOKEN_RE: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
_CAPABILITY_RE: Final = re.compile(r"[0-9a-f]{64}\Z")


class Direction(StrEnum):
    """Allowed directions across the client/worker trust boundary."""

    CLIENT_TO_WORKER = "client_to_worker"
    WORKER_TO_CLIENT = "worker_to_client"


class MessageType(StrEnum):
    """Closed set of protocol-v1 messages."""

    SESSION_HELLO = "session.hello"
    SESSION_READY = "session.ready"
    OPERATION_START = "operation.start"
    OPERATION_ACCEPTED = "operation.accepted"
    OPERATION_PROGRESS = "operation.progress"
    OPERATION_CANCEL = "operation.cancel"
    OPERATION_CANCEL_ACK = "operation.cancel_ack"
    OPERATION_RESULT = "operation.result"
    SESSION_SHUTDOWN = "session.shutdown"
    SESSION_CLOSED = "session.closed"
    PROTOCOL_ERROR = "protocol.error"
    WORKER_FAULT = "worker.fault"


class ReferenceArea(StrEnum):
    """Logical case-workspace areas; these are never filesystem paths."""

    SOURCE_READ = "source_read"
    ASSET_READ = "asset_read"
    OPERATION_CACHE = "operation_cache"
    OPERATION_TEMP = "operation_temp"
    OPERATION_STAGING = "operation_staging"


class ScalarValueType(StrEnum):
    """Allowlisted scalar parameter encodings."""

    BOOLEAN = "boolean"
    INTEGER = "integer"
    TOKEN = "token"
    UUID = "uuid"


class ProgressUnit(StrEnum):
    """Units allowed in progress events."""

    PAGES = "pages"
    ITEMS = "items"
    BYTES = "bytes"
    STEPS = "steps"


class ProgressStage(StrEnum):
    """Closed progress-stage registry for the protocol.noop v1 operation."""

    PROCESSING = "processing"


class CancelReason(StrEnum):
    """Why an operation cancellation was requested."""

    USER_REQUESTED = "user_requested"
    SHUTDOWN = "shutdown"
    SUPERSEDED = "superseded"


class CancelDisposition(StrEnum):
    """Worker disposition of a cancellation request."""

    REQUESTED = "requested"
    ALREADY_REQUESTED = "already_requested"
    ALREADY_TERMINAL = "already_terminal"


class OperationOutcome(StrEnum):
    """Terminal operation outcomes."""

    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ShutdownReason(StrEnum):
    """Why the client is closing the worker session."""

    USER_REQUESTED = "user_requested"
    APPLICATION_EXIT = "application_exit"
    PROTOCOL_FAULT = "protocol_fault"


class ClosedReason(StrEnum):
    """Why the worker reports the session closed."""

    SHUTDOWN_COMPLETE = "shutdown_complete"


class ErrorCategory(StrEnum):
    """Stable, non-sensitive error categories."""

    PROTOCOL = "protocol"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED = "unsupported"
    RESOURCE_LIMIT = "resource_limit"
    DEPENDENCY = "dependency"
    OPERATION = "operation"
    INTERNAL = "internal"


class ErrorCode(StrEnum):
    """Closed public error-code registry for protocol version 1."""

    RPC_MESSAGE_INVALID = "RPC_MESSAGE_INVALID"
    WORKER_OPERATION_FAILED = "WORKER_OPERATION_FAILED"
    WORKER_OPERATION_TIMED_OUT = "WORKER_OPERATION_TIMED_OUT"
    WORKER_INTERNAL_FAULT = "WORKER_INTERNAL_FAULT"


class RetryAction(StrEnum):
    """Closed set of safe recovery instructions."""

    NEVER = "never"
    RETRY = "retry"
    RESTART_WORKER = "restart_worker"
    USER_ACTION = "user_action"


def _require_uuid4(value: object, location: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject("RPC_INVALID_IDENTIFIER", location, "a canonical UUIDv4 is required")
    return value


def _require_int(
    value: object,
    location: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_SAFE_INTEGER,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        reject("RPC_INVALID_INTEGER", location, "an integer within the permitted range is required")
    return value


def _require_token(value: object, location: str, *, max_length: int = 128) -> str:
    if type(value) is not str or len(value) > max_length or _TOKEN_RE.fullmatch(value) is None:
        reject("RPC_INVALID_TOKEN", location, "a lowercase protocol token is required")
    return value


def _require_tuple(value: object, location: str, *, maximum: int) -> tuple[object, ...]:
    if type(value) is not tuple or len(value) > maximum:
        reject("RPC_INVALID_COLLECTION", location, "a bounded tuple is required")
    return cast(tuple[object, ...], value)


def _require_bool(value: object, location: str) -> bool:
    if type(value) is not bool:
        reject("RPC_INVALID_BOOLEAN", location, "a boolean is required")
    return value


def _require_unique(values: tuple[object, ...], location: str) -> None:
    try:
        unique_count = len(set(values))
    except TypeError:
        reject("RPC_INVALID_COLLECTION", location, "collection members must be hashable")
    if unique_count != len(values):
        reject("RPC_DUPLICATE_VALUE", location, "duplicate collection members are not permitted")


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class SessionCapability:
    """Per-launch capability secret, with redacted representations and constant-time comparison."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _CAPABILITY_RE.fullmatch(self.value) is None:
            reject(
                "RPC_INVALID_CAPABILITY",
                "session_capability",
                "a lowercase 256-bit hexadecimal capability is required",
            )

    def matches(self, other: object) -> bool:
        """Compare two launch capabilities without content-dependent early exit."""

        if type(other) is not SessionCapability:
            return False
        return hmac.compare_digest(self.value.encode("ascii"), other.value.encode("ascii"))

    def __eq__(self, other: object) -> bool:
        # Defining equality without a hash deliberately makes this secret unhashable.
        return self.matches(other)

    def __repr__(self) -> str:
        return "SessionCapability(<redacted>)"


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """A named, versioned worker capability."""

    capability_id: str
    capability_version: int

    def __post_init__(self) -> None:
        _require_token(self.capability_id, "payload.capability_id")
        _require_int(self.capability_version, "payload.capability_version", minimum=1)


NOOP_CAPABILITY: Final = CapabilityDescriptor("protocol.noop", 1)
PDF_INSPECT_CAPABILITY: Final = CapabilityDescriptor("pdf.inspect", 2)
PDF_THUMBNAIL_CAPABILITY: Final = CapabilityDescriptor("pdf.thumbnail", 1)
PDF_EXPORT_CAPABILITY: Final = CapabilityDescriptor("pdf.export", 1)
IMAGE_PDF_EXPORT_CAPABILITY: Final = CapabilityDescriptor("pdf.export", 2)
PORTAL_PDF_EXPORT_CAPABILITY: Final = CapabilityDescriptor("pdf.export", 3)
FLATTENED_PDF_EXPORT_CAPABILITY: Final = CapabilityDescriptor("pdf.export.flattened", 1)
IMAGE_NORMALIZE_CAPABILITY: Final = CapabilityDescriptor("image.normalize", 1)
SIGNATURE_IMAGE_RASTER_CAPABILITY: Final = CapabilityDescriptor("image.signature_raster", 1)
IMAGE_TRANSFORM_CAPABILITY: Final = CapabilityDescriptor("image.transform", 1)
OCR_PROCESS_CAPABILITY: Final = CapabilityDescriptor("ocr.process", 1)
QUALITY_OCR_REVIEW_CAPABILITY: Final = CapabilityDescriptor("ocr.review", 1)
SEARCHABLE_PDF_EXPORT_CAPABILITY: Final = CapabilityDescriptor("pdf.export.searchable", 1)
PDF_IMAGE_STAGE_CAPABILITY: Final = CapabilityDescriptor("pdf.image_stage", 1)

PDF_IMAGE_STAGE_PLAN_CONTENT_KIND: Final = "pdf-image-stage-plan-v1"
PDF_IMAGE_STAGE_OUTPUT_CONTENT_KIND: Final = "pdf-image-stage-pdf-v1"
PDF_IMAGE_STAGE_EVIDENCE_CONTENT_KIND: Final = "pdf-image-stage-evidence-v1"
MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES: Final = 2 * 1024 * 1024

IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER: Final = "source_handle"
IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER: Final = "output_handle"
IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER: Final = "output_object_id"
IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER: Final = "maximum_width_pixels"
IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER: Final = "maximum_height_pixels"
IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER: Final = "additional_rotation"
IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND: Final = "image-normalized-rgba-v1"

SIGNATURE_IMAGE_RASTER_SOURCE_HANDLE_PARAMETER: Final = "source_handle"
SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER: Final = "output_handle"
SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER: Final = "output_object_id"
SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER: Final = "maximum_width_pixels"
SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER: Final = "maximum_height_pixels"
SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND: Final = "signature-image-rgba-v1"

IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER: Final = "source_handle"
IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER: Final = "output_handle"
IMAGE_TRANSFORM_PAGE_ID_PARAMETER: Final = "page_id"
IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER: Final = "output_object_id"
IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER: Final = "maximum_width_pixels"
IMAGE_TRANSFORM_MAXIMUM_HEIGHT_PARAMETER: Final = "maximum_height_pixels"
IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER: Final = "quarter_turns_clockwise"
IMAGE_TRANSFORM_CROP_X_PARAMETER: Final = "crop_x_millionths"
IMAGE_TRANSFORM_CROP_Y_PARAMETER: Final = "crop_y_millionths"
IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER: Final = "crop_width_millionths"
IMAGE_TRANSFORM_CROP_HEIGHT_PARAMETER: Final = "crop_height_millionths"
IMAGE_TRANSFORM_DESKEW_PARAMETER: Final = "deskew_millidegrees_clockwise"
IMAGE_TRANSFORM_CONTRAST_PARAMETER: Final = "contrast_factor_millionths"
IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER: Final = "background_cleanup_strength_millionths"
IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND: Final = "image-transformed-rgba-v1"

OCR_PROCESS_SOURCE_HANDLE_PARAMETER: Final = "source_handle"
OCR_PROCESS_OUTPUT_HANDLE_PARAMETER: Final = "output_handle"
OCR_PROCESS_RESULT_HANDLE_PARAMETER: Final = "result_handle"
OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER: Final = "output_object_id"
OCR_PROCESS_PAGE_COUNT_PARAMETER: Final = "page_count"
OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER: Final = "language_set_id"
OCR_PROCESS_PROFILE_ID_PARAMETER: Final = "profile_id"
OCR_PROCESS_OUTPUT_CONTENT_KIND: Final = "ocr-pdf-candidate-v1"
OCR_PROCESS_SOURCE_CONTENT_KINDS: Final = frozenset({"pdf", "jpeg", "png"})
MAX_OCR_PROCESS_PAGES: Final = 4_096

QUALITY_OCR_REVIEW_SAMPLE_HANDLE_PARAMETER: Final = "sample_handle"
QUALITY_OCR_REVIEW_RESULT_HANDLE_PARAMETER: Final = "result_handle"
QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER: Final = "page_id"
QUALITY_OCR_REVIEW_MANIFEST_REVISION_PARAMETER: Final = "manifest_revision"
QUALITY_OCR_REVIEW_WIDTH_PARAMETER: Final = "width_pixels"
QUALITY_OCR_REVIEW_HEIGHT_PARAMETER: Final = "height_pixels"
QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER: Final = "row_stride_bytes"
QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER: Final = "sample_byte_size"
QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER: Final = "sample_sha256"
QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER: Final = "presentation_sha256"
QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX: Final = "sha256-"
QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER: Final = "language_set_id"
QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER: Final = "profile_id"
QUALITY_OCR_REVIEW_PROFILE_ID_VALUE: Final = "quality-ocr-review-v1"
QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_VALUES: Final = frozenset(
    {
        "latin-default-v1",
        "german-fast-v1",
        "english-german-fast-v1",
    }
)
MAX_QUALITY_OCR_REVIEW_EDGE_PIXELS: Final = 256
MAX_QUALITY_OCR_REVIEW_PIXELS: Final = 256 * 256
MAX_QUALITY_OCR_REVIEW_ROW_PADDING_BYTES: Final = 4_096
MAX_QUALITY_OCR_REVIEW_TSV_BYTES: Final = 1024 * 1024

PDF_INSPECT_SOURCE_HANDLE_PARAMETER: Final = "source_handle"
PDF_INSPECT_RESULT_HANDLE_PARAMETER: Final = "result_handle"

PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER: Final = "source_handle"
PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER: Final = "cache_handle"
PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER: Final = "cache_object_id"
PDF_THUMBNAIL_PAGE_ID_PARAMETER: Final = "page_id"
PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER: Final = "source_page_index"
PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER: Final = "maximum_width_pixels"
PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER: Final = "maximum_height_pixels"
PDF_THUMBNAIL_PAGE_BOX_PARAMETER: Final = "page_box"
PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER: Final = "additional_rotation"
PDF_THUMBNAIL_CONTENT_KIND: Final = "pdf-thumbnail-rgba"

PDF_EXPORT_PLAN_HANDLE_PARAMETER: Final = "plan_handle"
PDF_EXPORT_OUTPUT_HANDLE_PARAMETER: Final = "output_handle"
PDF_EXPORT_RESULT_HANDLE_PARAMETER: Final = "result_handle"
PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER: Final = "output_object_id"
PDF_EXPORT_PLAN_CONTENT_KIND: Final = "pdf-export-plan-v1"
IMAGE_PDF_EXPORT_PLAN_CONTENT_KIND: Final = "pdf-export-plan-v2"
PORTAL_PDF_EXPORT_PLAN_CONTENT_KIND: Final = "pdf-export-plan-v3"
PDF_EXPORT_OUTPUT_CONTENT_KIND: Final = "pdf-export-pdf"
MAX_PDF_EXPORT_PLAN_BYTES: Final = 2 * 1024 * 1024
MAX_PDF_EXPORT_PAGES: Final = 4_096
MAX_PDF_EXPORT_SOURCES: Final = MAX_JSON_COLLECTION_ITEMS - 1

FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER: Final = "plan_handle"
FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER: Final = "output_handle"
FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER: Final = "result_handle"
FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER: Final = "output_object_id"
FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND: Final = "flattened-pdf-export-plan-v1"
FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND: Final = "overlay-rgba8-v1"
FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND: Final = "flattened-pdf-v1"
MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES: Final = MAX_JSON_COLLECTION_ITEMS
MAX_FLATTENED_PDF_EXPORT_RASTER_ASSETS: Final = MAX_JSON_COLLECTION_ITEMS - 2

SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER: Final = "plan_handle"
SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER: Final = "output_handle"
SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER: Final = "status_handle"
SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER: Final = "result_handle"
SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER: Final = "output_object_id"
SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER: Final = "status_object_id"
SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER: Final = "status_capacity_bytes"
SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND: Final = "searchable-pdf-export-plan-v1"
SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND: Final = "searchable-pdf-v1"
SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND: Final = "searchable-pdf-page-status-v1"
MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES: Final = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TaggedScalar:
    """A closed, explicitly tagged scalar used for parameters and safe error context."""

    name: str
    value_type: ScalarValueType
    value: bool | int | str | UUID = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.name, "payload.parameters.name", max_length=64)
        if type(self.value_type) is not ScalarValueType:
            reject(
                "RPC_INVALID_ENUM",
                "payload.parameters.value_type",
                "an allowed value type is required",
            )
        if self.value_type is ScalarValueType.BOOLEAN:
            if type(self.value) is not bool:
                reject(
                    "RPC_INVALID_SCALAR", "payload.parameters.value", "a boolean value is required"
                )
        elif self.value_type is ScalarValueType.INTEGER:
            _require_int(
                self.value,
                "payload.parameters.value",
                minimum=-MAX_SAFE_INTEGER,
            )
        elif self.value_type is ScalarValueType.TOKEN:
            _require_token(self.value, "payload.parameters.value")
        elif self.value_type is ScalarValueType.UUID:
            _require_uuid4(self.value, "payload.parameters.value")


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Integer limits applied to one worker operation."""

    wall_time_ms: int
    cpu_time_ms: int
    memory_bytes: int
    temporary_storage_bytes: int
    output_bytes: int
    page_count: int
    pixel_count: int
    limits_version: int = 1

    def __post_init__(self) -> None:
        if self.limits_version != 1 or type(self.limits_version) is not int:
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.limits_version",
                "resource limits version is unsupported",
            )
        for field_name in (
            "wall_time_ms",
            "cpu_time_ms",
            "memory_bytes",
            "temporary_storage_bytes",
            "output_bytes",
            "page_count",
            "pixel_count",
        ):
            _require_int(getattr(self, field_name), f"payload.{field_name}", minimum=1)


@dataclass(frozen=True, slots=True)
class CaseObjectReference:
    """A logical case object resolved by the trusted workspace broker."""

    area: ReferenceArea
    object_id: UUID
    operation_id: UUID | None
    content_kind: str
    expected_byte_size: int | None
    expected_sha256: str | None = field(repr=False)
    reference_version: int = 1

    def __post_init__(self) -> None:
        if self.reference_version != 1 or type(self.reference_version) is not int:
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.reference_version",
                "reference version is unsupported",
            )
        if type(self.area) is not ReferenceArea:
            reject("RPC_INVALID_ENUM", "payload.area", "an allowed case-object area is required")
        _require_uuid4(self.object_id, "payload.object_id")
        _require_token(self.content_kind, "payload.content_kind")
        if self.expected_byte_size is not None:
            _require_int(self.expected_byte_size, "payload.expected_byte_size")
        if self.expected_sha256 is not None and (
            type(self.expected_sha256) is not str
            or _SHA256_RE.fullmatch(self.expected_sha256) is None
        ):
            reject(
                "RPC_INVALID_DIGEST",
                "payload.expected_sha256",
                "a lowercase SHA-256 digest is required",
            )
        if self.area in {ReferenceArea.SOURCE_READ, ReferenceArea.ASSET_READ}:
            if self.operation_id is not None:
                reject(
                    "RPC_REFERENCE_SCOPE",
                    "payload.operation_id",
                    "read-only source references cannot be operation-scoped",
                )
        else:
            _require_uuid4(self.operation_id, "payload.operation_id")


@dataclass(frozen=True, slots=True)
class ErrorPayload:
    """Sanitized public error data with no worker-controlled context values."""

    code: ErrorCode
    category: ErrorCategory
    retry_action: RetryAction
    restart_required: bool
    parameters: tuple[()]
    error_version: int = 1

    def __post_init__(self) -> None:
        if self.error_version != 1 or type(self.error_version) is not int:
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.error_version",
                "error payload version is unsupported",
            )
        if type(self.code) is not ErrorCode:
            reject(
                "RPC_INVALID_ERROR", "payload.code", "a registered public error code is required"
            )
        if type(self.category) is not ErrorCategory:
            reject("RPC_INVALID_ENUM", "payload.category", "an allowed error category is required")
        if type(self.retry_action) is not RetryAction:
            reject(
                "RPC_INVALID_ENUM", "payload.retry_action", "an allowed retry action is required"
            )
        restart_required = _require_bool(self.restart_required, "payload.restart_required")
        if restart_required is not (self.retry_action is RetryAction.RESTART_WORKER):
            reject(
                "RPC_INVALID_ERROR",
                "payload.restart_required",
                "restart requirement must match the retry action",
            )
        parameters = _require_tuple(self.parameters, "payload.parameters", maximum=MAX_PARAMETERS)
        if parameters:
            reject(
                "RPC_INVALID_ERROR_PARAMETER",
                "payload.parameters",
                "public protocol-v1 errors cannot carry context values",
            )


@dataclass(frozen=True, slots=True)
class SessionHelloPayload:
    supported_protocol_versions: tuple[int, ...]
    required_capabilities: tuple[CapabilityDescriptor, ...]

    def __post_init__(self) -> None:
        versions = _require_tuple(
            self.supported_protocol_versions,
            "payload.supported_protocol_versions",
            maximum=MAX_JSON_COLLECTION_ITEMS,
        )
        if not versions:
            reject(
                "RPC_INVALID_COLLECTION",
                "payload.supported_protocol_versions",
                "at least one version is required",
            )
        for version in versions:
            _require_int(version, "payload.supported_protocol_versions", minimum=1)
        _require_unique(versions, "payload.supported_protocol_versions")
        if PROTOCOL_VERSION not in versions:
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.supported_protocol_versions",
                "the current protocol version must be supported",
            )
        capabilities = _require_tuple(
            self.required_capabilities,
            "payload.required_capabilities",
            maximum=MAX_JSON_COLLECTION_ITEMS,
        )
        if any(type(capability) is not CapabilityDescriptor for capability in capabilities):
            reject(
                "RPC_INVALID_COLLECTION",
                "payload.required_capabilities",
                "capability descriptors are required",
            )
        _require_unique(capabilities, "payload.required_capabilities")


@dataclass(frozen=True, slots=True)
class SessionReadyPayload:
    selected_protocol_version: int
    capabilities: tuple[CapabilityDescriptor, ...]
    max_message_bytes: int
    max_in_flight_operations: int = 1

    def __post_init__(self) -> None:
        if (
            self.selected_protocol_version != PROTOCOL_VERSION
            or type(self.selected_protocol_version) is not int
        ):
            reject(
                "RPC_UNSUPPORTED_VERSION",
                "payload.selected_protocol_version",
                "selected protocol version is unsupported",
            )
        capabilities = _require_tuple(
            self.capabilities,
            "payload.capabilities",
            maximum=MAX_JSON_COLLECTION_ITEMS,
        )
        if any(type(capability) is not CapabilityDescriptor for capability in capabilities):
            reject(
                "RPC_INVALID_COLLECTION",
                "payload.capabilities",
                "capability descriptors are required",
            )
        _require_unique(capabilities, "payload.capabilities")
        _require_int(
            self.max_message_bytes,
            "payload.max_message_bytes",
            minimum=1,
            maximum=MAX_MESSAGE_BYTES,
        )
        if self.max_in_flight_operations != 1 or type(self.max_in_flight_operations) is not int:
            reject(
                "RPC_UNSUPPORTED_LIMIT",
                "payload.max_in_flight_operations",
                "only one in-flight operation is supported",
            )


@dataclass(frozen=True, slots=True)
class OperationStartPayload:
    operation_kind: str
    operation_version: int
    inputs: tuple[CaseObjectReference, ...]
    parameters: tuple[TaggedScalar, ...]
    requested_limits: ResourceLimits

    def __post_init__(self) -> None:
        _require_token(self.operation_kind, "payload.operation_kind")
        _require_int(self.operation_version, "payload.operation_version", minimum=1)
        inputs = _require_tuple(self.inputs, "payload.inputs", maximum=MAX_JSON_COLLECTION_ITEMS)
        if any(type(reference) is not CaseObjectReference for reference in inputs):
            reject(
                "RPC_INVALID_COLLECTION", "payload.inputs", "case-object references are required"
            )
        parameters = _require_tuple(self.parameters, "payload.parameters", maximum=MAX_PARAMETERS)
        if any(type(parameter) is not TaggedScalar for parameter in parameters):
            reject(
                "RPC_INVALID_COLLECTION",
                "payload.parameters",
                "tagged scalar parameters are required",
            )
        _require_unique(
            tuple(parameter.name for parameter in self.parameters), "payload.parameters"
        )
        if type(self.requested_limits) is not ResourceLimits:
            reject("RPC_INVALID_OBJECT", "payload.requested_limits", "resource limits are required")


def validate_ocr_process_start(payload: OperationStartPayload) -> None:
    """Validate the exact, path-free ``ocr.process`` version-1 contract."""

    if len(payload.inputs) != 1:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "ocr.process version 1 requires exactly one source reference",
        )
    source = payload.inputs[0]
    if (
        source.area is not ReferenceArea.SOURCE_READ
        or source.operation_id is not None
        or source.content_kind not in OCR_PROCESS_SOURCE_CONTENT_KINDS
        or source.expected_byte_size is None
        or source.expected_byte_size < 1
        or source.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "ocr.process requires one authenticated PDF, JPEG, or PNG source reference",
        )

    expected_names = {
        OCR_PROCESS_SOURCE_HANDLE_PARAMETER,
        OCR_PROCESS_OUTPUT_HANDLE_PARAMETER,
        OCR_PROCESS_RESULT_HANDLE_PARAMETER,
        OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER,
        OCR_PROCESS_PAGE_COUNT_PARAMETER,
        OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER,
        OCR_PROCESS_PROFILE_ID_PARAMETER,
    }
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.process version 1 requires its exact closed parameter set",
        )

    handles = tuple(
        parameters[name]
        for name in (
            OCR_PROCESS_SOURCE_HANDLE_PARAMETER,
            OCR_PROCESS_OUTPUT_HANDLE_PARAMETER,
            OCR_PROCESS_RESULT_HANDLE_PARAMETER,
        )
    )
    if any(
        parameter.value_type is not ScalarValueType.INTEGER
        or type(parameter.value) is not int
        or parameter.value <= 0
        for parameter in handles
    ) or len({parameter.value for parameter in handles}) != len(handles):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.process brokered handles must be distinct positive integers",
        )

    output_object = parameters[OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER]
    if (
        output_object.value_type is not ScalarValueType.UUID
        or type(output_object.value) is not UUID
        or output_object.value == source.object_id
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.process output identity must be a distinct UUID",
        )

    page_count = parameters[OCR_PROCESS_PAGE_COUNT_PARAMETER]
    if (
        page_count.value_type is not ScalarValueType.INTEGER
        or type(page_count.value) is not int
        or not 1 <= page_count.value <= MAX_OCR_PROCESS_PAGES
        or page_count.value != payload.requested_limits.page_count
        or (source.content_kind in {"jpeg", "png"} and page_count.value != 1)
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.process page count must exactly match its source and requested limit",
        )

    for name in (OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER, OCR_PROCESS_PROFILE_ID_PARAMETER):
        parameter = parameters[name]
        if parameter.value_type is not ScalarValueType.TOKEN or type(parameter.value) is not str:
            reject(
                "RPC_INVALID_OPERATION",
                "payload.parameters",
                "ocr.process configuration identifiers must be opaque protocol tokens",
            )

    source_size = source.expected_byte_size
    limits = payload.requested_limits
    if (
        limits.page_count > MAX_OCR_PROCESS_PAGES
        or source_size > limits.temporary_storage_bytes
        or limits.output_bytes > limits.temporary_storage_bytes - source_size
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "ocr.process temporary storage cannot hold its source and output bounds",
        )


def ocr_process_bound_object_ids(payload: OperationStartPayload) -> tuple[UUID, UUID]:
    """Return source and output UUIDs from an already validated OCR start."""

    validate_ocr_process_start(payload)
    for parameter in payload.parameters:
        if parameter.name == OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER:
            if parameter.value_type is ScalarValueType.UUID and type(parameter.value) is UUID:
                return payload.inputs[0].object_id, parameter.value
            break
    reject(
        "RPC_INVALID_OPERATION",
        "payload.parameters",
        "ocr.process output object identifier is unavailable",
    )


def ocr_process_output_object_id(payload: OperationStartPayload) -> UUID:
    """Return the staging output UUID from an already validated OCR start."""

    return ocr_process_bound_object_ids(payload)[1]


def validate_ocr_process_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    maximum_output_bytes: int,
) -> None:
    """Bind a successful OCR result to its exact bounded opaque PDF candidate."""

    _require_uuid4(operation_id, "operation_id")
    _require_uuid4(output_object_id, "payload.outputs.object_id")
    _require_int(maximum_output_bytes, "payload.requested_limits.output_bytes", minimum=1)
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "unsuccessful ocr.process results cannot carry outputs",
            )
        return
    if len(payload.outputs) != 1:
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "successful ocr.process results require exactly one staging output",
        )
    output = payload.outputs[0]
    if (
        output_object_id == operation_id
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != OCR_PROCESS_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "ocr.process output is not bound to its requested staging object and limit",
        )


def validate_quality_ocr_review_start(payload: OperationStartPayload) -> None:
    """Validate the exact handle-only ``ocr.review`` version-1 contract."""

    if payload.inputs:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "ocr.review version 1 accepts only one bounded private sample section",
        )
    expected_names = {
        QUALITY_OCR_REVIEW_SAMPLE_HANDLE_PARAMETER,
        QUALITY_OCR_REVIEW_RESULT_HANDLE_PARAMETER,
        QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER,
        QUALITY_OCR_REVIEW_MANIFEST_REVISION_PARAMETER,
        QUALITY_OCR_REVIEW_WIDTH_PARAMETER,
        QUALITY_OCR_REVIEW_HEIGHT_PARAMETER,
        QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER,
        QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER,
        QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER,
        QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER,
        QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER,
        QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER,
    }
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.review version 1 requires its exact closed parameter set",
        )
    handles = tuple(
        parameters[name]
        for name in (
            QUALITY_OCR_REVIEW_SAMPLE_HANDLE_PARAMETER,
            QUALITY_OCR_REVIEW_RESULT_HANDLE_PARAMETER,
        )
    )
    if (
        any(
            parameter.value_type is not ScalarValueType.INTEGER
            or type(parameter.value) is not int
            or parameter.value <= 0
            for parameter in handles
        )
        or handles[0].value == handles[1].value
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.review brokered handles must be distinct positive integers",
        )
    page = parameters[QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER]
    if page.value_type is not ScalarValueType.UUID or type(page.value) is not UUID:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.review requires one UUID page identity",
        )
    revision = parameters[QUALITY_OCR_REVIEW_MANIFEST_REVISION_PARAMETER]
    integer_names = (
        QUALITY_OCR_REVIEW_WIDTH_PARAMETER,
        QUALITY_OCR_REVIEW_HEIGHT_PARAMETER,
        QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER,
        QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER,
    )
    if (
        revision.value_type is not ScalarValueType.INTEGER
        or type(revision.value) is not int
        or not 0 <= revision.value <= MAX_SAFE_INTEGER
        or any(
            parameters[name].value_type is not ScalarValueType.INTEGER
            or type(parameters[name].value) is not int
            for name in integer_names
        )
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.review requires bounded integer sample metadata",
        )
    width = cast(int, parameters[QUALITY_OCR_REVIEW_WIDTH_PARAMETER].value)
    height = cast(int, parameters[QUALITY_OCR_REVIEW_HEIGHT_PARAMETER].value)
    row_stride = cast(int, parameters[QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER].value)
    sample_bytes = cast(int, parameters[QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER].value)
    if (
        not 1 <= width <= MAX_QUALITY_OCR_REVIEW_EDGE_PIXELS
        or not 1 <= height <= MAX_QUALITY_OCR_REVIEW_EDGE_PIXELS
        or width * height > MAX_QUALITY_OCR_REVIEW_PIXELS
        or not width * 4 <= row_stride <= width * 4 + MAX_QUALITY_OCR_REVIEW_ROW_PADDING_BYTES
        or sample_bytes != row_stride * height
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.review sample geometry and byte count must agree",
        )
    for name in (
        QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER,
        QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER,
    ):
        parameter = parameters[name]
        if (
            parameter.value_type is not ScalarValueType.TOKEN
            or type(parameter.value) is not str
            or re.fullmatch(r"sha256-[0-9a-f]{64}", parameter.value) is None
        ):
            reject(
                "RPC_INVALID_OPERATION",
                "payload.parameters",
                "ocr.review digests must be lowercase SHA-256 values",
            )
    language = parameters[QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER]
    profile = parameters[QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER]
    if (
        language.value_type is not ScalarValueType.TOKEN
        or type(language.value) is not str
        or language.value not in QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_VALUES
        or profile.value_type is not ScalarValueType.TOKEN
        or profile.value != QUALITY_OCR_REVIEW_PROFILE_ID_VALUE
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "ocr.review configuration identifiers must be closed version-1 tokens",
        )
    limits = payload.requested_limits
    ppm_bytes = 32 + width * height * 3
    if (
        limits.page_count != 1
        or limits.pixel_count < width * height
        or not 1 <= limits.output_bytes <= MAX_QUALITY_OCR_REVIEW_TSV_BYTES
        or sample_bytes > limits.memory_bytes
        or limits.temporary_storage_bytes < sample_bytes + ppm_bytes + limits.output_bytes
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "ocr.review limits cannot admit the exact sample and bounded OCR output",
        )


def quality_ocr_review_page_id(payload: OperationStartPayload) -> UUID:
    """Return the page UUID from an already validated OCR-review start."""

    validate_quality_ocr_review_start(payload)
    for parameter in payload.parameters:
        if parameter.name == QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER:
            if parameter.value_type is ScalarValueType.UUID and type(parameter.value) is UUID:
                return parameter.value
            break
    reject(
        "RPC_INVALID_OPERATION",
        "payload.parameters",
        "ocr.review page identity is unavailable",
    )


def validate_quality_ocr_review_result(payload: OperationResultPayload) -> None:
    """Require OCR-review results to remain out-of-band and object-free."""

    if payload.outputs:
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "ocr.review cannot publish document objects",
        )


def validate_pdf_inspect_start(payload: OperationStartPayload) -> None:
    """Validate the exact, path-free ``pdf.inspect`` version-2 input contract."""

    if len(payload.inputs) != 1:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.inspect version 2 requires exactly one source reference",
        )
    source = payload.inputs[0]
    if (
        source.area is not ReferenceArea.SOURCE_READ
        or source.content_kind != "pdf"
        or source.expected_byte_size is None
        or source.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.inspect version 2 requires one authenticated PDF source reference",
        )

    expected_names = {
        PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
        PDF_INSPECT_RESULT_HANDLE_PARAMETER,
    }
    if (
        len(payload.parameters) != 2
        or {parameter.name for parameter in payload.parameters} != expected_names
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.inspect version 2 requires its two brokered handles",
        )
    handle_values = tuple(parameter.value for parameter in payload.parameters)
    if (
        any(
            parameter.value_type is not ScalarValueType.INTEGER
            or type(parameter.value) is not int
            or parameter.value <= 0
            for parameter in payload.parameters
        )
        or len(set(handle_values)) != 2
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.inspect brokered handles must be distinct positive integers",
        )


def validate_image_normalize_start(payload: OperationStartPayload) -> None:
    """Validate the exact, path-free ``image.normalize`` version-1 contract."""

    if len(payload.inputs) != 1:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "image.normalize version 1 requires exactly one source reference",
        )
    source = payload.inputs[0]
    if (
        source.area is not ReferenceArea.SOURCE_READ
        or source.content_kind not in {"jpeg", "png"}
        or source.expected_byte_size is None
        or source.expected_byte_size < 1
        or source.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "image.normalize version 1 requires one authenticated JPEG or PNG source reference",
        )

    expected_names = {
        IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER,
        IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER,
        IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
        IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER,
        IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER,
        IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER,
    }
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.normalize version 1 requires its exact closed parameter set",
        )

    handles = tuple(
        parameters[name]
        for name in (
            IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER,
            IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER,
        )
    )
    if (
        any(
            parameter.value_type is not ScalarValueType.INTEGER
            or type(parameter.value) is not int
            or parameter.value <= 0
            for parameter in handles
        )
        or handles[0].value == handles[1].value
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.normalize brokered handles must be distinct positive integers",
        )

    output_object = parameters[IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER]
    if (
        output_object.value_type is not ScalarValueType.UUID
        or type(output_object.value) is not UUID
        or output_object.value == source.object_id
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.normalize output identity must be a distinct UUID",
        )

    for name in (
        IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER,
        IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER,
    ):
        parameter = parameters[name]
        if (
            parameter.value_type is not ScalarValueType.INTEGER
            or type(parameter.value) is not int
            or parameter.value <= 0
        ):
            reject(
                "RPC_INVALID_OPERATION",
                "payload.parameters",
                "image.normalize dimensions must be positive integers",
            )
    maximum_width = cast(int, parameters[IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER].value)
    maximum_height = cast(int, parameters[IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER].value)
    if maximum_width > payload.requested_limits.pixel_count // maximum_height:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.normalize dimensions exceed the requested pixel limit",
        )

    rotation = parameters[IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER]
    if (
        rotation.value_type is not ScalarValueType.INTEGER
        or type(rotation.value) is not int
        or rotation.value not in {0, 90, 180, 270}
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.normalize rotation must be a clockwise quarter-turn",
        )


def validate_signature_image_raster_start(payload: OperationStartPayload) -> None:
    """Validate the exact, path-free ``image.signature_raster`` version-1 contract."""

    if len(payload.inputs) != 1:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "image.signature_raster version 1 requires exactly one asset reference",
        )
    source = payload.inputs[0]
    if (
        source.area is not ReferenceArea.ASSET_READ
        or source.operation_id is not None
        or source.content_kind not in {"jpeg", "png"}
        or source.expected_byte_size is None
        or source.expected_byte_size < 1
        or source.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "image.signature_raster requires one authenticated JPEG or PNG asset reference",
        )
    expected_names = {
        SIGNATURE_IMAGE_RASTER_SOURCE_HANDLE_PARAMETER,
        SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER,
        SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER,
        SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER,
        SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER,
    }
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.signature_raster version 1 requires its exact closed parameter set",
        )
    handles = tuple(
        parameters[name]
        for name in (
            SIGNATURE_IMAGE_RASTER_SOURCE_HANDLE_PARAMETER,
            SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER,
        )
    )
    if (
        any(
            parameter.value_type is not ScalarValueType.INTEGER
            or type(parameter.value) is not int
            or parameter.value <= 0
            for parameter in handles
        )
        or handles[0].value == handles[1].value
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.signature_raster brokered handles must be distinct positive integers",
        )
    output_object = parameters[SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER]
    if (
        output_object.value_type is not ScalarValueType.UUID
        or type(output_object.value) is not UUID
        or output_object.value == source.object_id
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.signature_raster output identity must be a distinct UUID",
        )
    for name in (
        SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER,
        SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER,
    ):
        parameter = parameters[name]
        if (
            parameter.value_type is not ScalarValueType.INTEGER
            or type(parameter.value) is not int
            or parameter.value <= 0
        ):
            reject(
                "RPC_INVALID_OPERATION",
                "payload.parameters",
                "image.signature_raster dimensions must be positive integers",
            )
    width = cast(int, parameters[SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER].value)
    height = cast(int, parameters[SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER].value)
    if width > payload.requested_limits.pixel_count // height:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.signature_raster dimensions exceed the requested pixel limit",
        )


def signature_image_raster_bound_object_ids(
    payload: OperationStartPayload,
) -> tuple[UUID, UUID]:
    """Return asset and output UUIDs from an already validated raster start."""

    validate_signature_image_raster_start(payload)
    for parameter in payload.parameters:
        if parameter.name == SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER:
            if parameter.value_type is ScalarValueType.UUID and type(parameter.value) is UUID:
                return payload.inputs[0].object_id, parameter.value
            break
    reject(
        "RPC_INVALID_OPERATION",
        "payload.parameters",
        "image.signature_raster output object identifier is unavailable",
    )


def validate_signature_image_raster_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    maximum_output_bytes: int,
) -> None:
    """Bind a signature-raster terminal result to its bounded staging output."""

    _require_uuid4(operation_id, "operation_id")
    _require_uuid4(output_object_id, "payload.outputs.object_id")
    _require_int(maximum_output_bytes, "payload.requested_limits.output_bytes", minimum=1)
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "unsuccessful image.signature_raster results cannot carry outputs",
            )
        return
    if len(payload.outputs) != 1:
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "successful image.signature_raster results require one staging output",
        )
    output = payload.outputs[0]
    if (
        output_object_id == operation_id
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "image.signature_raster output is not bound to its requested staging object",
        )


def image_normalize_bound_object_ids(payload: OperationStartPayload) -> tuple[UUID, UUID]:
    """Return source and output UUIDs from an already validated normalize start."""

    validate_image_normalize_start(payload)
    for parameter in payload.parameters:
        if parameter.name == IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER:
            if parameter.value_type is ScalarValueType.UUID and type(parameter.value) is UUID:
                return payload.inputs[0].object_id, parameter.value
            break
    reject(
        "RPC_INVALID_OPERATION",
        "payload.parameters",
        "image.normalize output object identifier is unavailable",
    )


def image_normalize_output_object_id(payload: OperationStartPayload) -> UUID:
    """Return the staging output UUID from an already validated normalize start."""

    return image_normalize_bound_object_ids(payload)[1]


def validate_image_normalize_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    maximum_output_bytes: int,
) -> None:
    """Bind a normalize terminal result to its exact bounded staging output."""

    _require_uuid4(operation_id, "operation_id")
    _require_uuid4(output_object_id, "payload.outputs.object_id")
    _require_int(maximum_output_bytes, "payload.requested_limits.output_bytes", minimum=1)
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "unsuccessful image.normalize results cannot carry outputs",
            )
        return
    if len(payload.outputs) != 1:
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "successful image.normalize results require exactly one staging output",
        )
    output = payload.outputs[0]
    if (
        output_object_id == operation_id
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "image.normalize output is not bound to its requested staging object and limit",
        )


def validate_image_transform_start(payload: OperationStartPayload) -> None:
    """Validate the exact, path-free ``image.transform`` version-1 contract."""

    if len(payload.inputs) != 1:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "image.transform version 1 requires exactly one source reference",
        )
    source = payload.inputs[0]
    if (
        source.area is not ReferenceArea.SOURCE_READ
        or source.content_kind not in {"jpeg", "png"}
        or source.expected_byte_size is None
        or source.expected_byte_size < 1
        or source.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "image.transform requires one authenticated JPEG or PNG source reference",
        )
    expected_names = {
        IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER,
        IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER,
        IMAGE_TRANSFORM_PAGE_ID_PARAMETER,
        IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER,
        IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER,
        IMAGE_TRANSFORM_MAXIMUM_HEIGHT_PARAMETER,
        IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER,
        IMAGE_TRANSFORM_CROP_X_PARAMETER,
        IMAGE_TRANSFORM_CROP_Y_PARAMETER,
        IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER,
        IMAGE_TRANSFORM_CROP_HEIGHT_PARAMETER,
        IMAGE_TRANSFORM_DESKEW_PARAMETER,
        IMAGE_TRANSFORM_CONTRAST_PARAMETER,
        IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER,
    }
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.transform version 1 requires its exact closed parameter set",
        )
    handles = tuple(
        parameters[name]
        for name in (
            IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER,
            IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER,
        )
    )
    if (
        any(
            parameter.value_type is not ScalarValueType.INTEGER
            or type(parameter.value) is not int
            or parameter.value <= 0
            for parameter in handles
        )
        or handles[0].value == handles[1].value
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.transform brokered handles must be distinct positive integers",
        )
    page_object = parameters[IMAGE_TRANSFORM_PAGE_ID_PARAMETER]
    output_object = parameters[IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER]
    if (
        page_object.value_type is not ScalarValueType.UUID
        or type(page_object.value) is not UUID
        or output_object.value_type is not ScalarValueType.UUID
        or type(output_object.value) is not UUID
        or len({source.object_id, page_object.value, output_object.value}) != 3
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.transform page and output identities must be distinct UUIDs",
        )
    integer_values: dict[str, int] = {}
    for name in expected_names - {
        IMAGE_TRANSFORM_PAGE_ID_PARAMETER,
        IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER,
    }:
        parameter = parameters[name]
        if parameter.value_type is not ScalarValueType.INTEGER or type(parameter.value) is not int:
            reject(
                "RPC_INVALID_OPERATION",
                "payload.parameters",
                "image.transform numeric parameters must be exact integers",
            )
        integer_values[name] = parameter.value
    width = integer_values[IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER]
    height = integer_values[IMAGE_TRANSFORM_MAXIMUM_HEIGHT_PARAMETER]
    if width <= 0 or height <= 0 or width > payload.requested_limits.pixel_count // height:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.transform dimensions exceed the requested pixel limit",
        )
    if integer_values[IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER] not in {0, 1, 2, 3}:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.transform rotation must be a clockwise quarter-turn count",
        )
    crop_x = integer_values[IMAGE_TRANSFORM_CROP_X_PARAMETER]
    crop_y = integer_values[IMAGE_TRANSFORM_CROP_Y_PARAMETER]
    crop_width = integer_values[IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER]
    crop_height = integer_values[IMAGE_TRANSFORM_CROP_HEIGHT_PARAMETER]
    if (
        not 0 <= crop_x <= 1_000_000
        or not 0 <= crop_y <= 1_000_000
        or not 1 <= crop_width <= 1_000_000
        or not 1 <= crop_height <= 1_000_000
        or crop_x + crop_width > 1_000_000
        or crop_y + crop_height > 1_000_000
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.transform crop must fit the normalized logical page",
        )
    if not -15_000 <= integer_values[IMAGE_TRANSFORM_DESKEW_PARAMETER] <= 15_000:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.transform deskew is outside the admitted range",
        )
    if not 0 <= integer_values[IMAGE_TRANSFORM_CONTRAST_PARAMETER] <= 4_000_000:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.transform contrast is outside the admitted range",
        )
    if not 0 <= integer_values[IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER] <= 1_000_000:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "image.transform background cleanup is outside the admitted range",
        )


def image_transform_bound_object_ids(payload: OperationStartPayload) -> tuple[UUID, UUID, UUID]:
    """Return source, page, and output UUIDs from a validated transform start."""

    validate_image_transform_start(payload)
    values = {parameter.name: parameter for parameter in payload.parameters}
    page = values[IMAGE_TRANSFORM_PAGE_ID_PARAMETER]
    output = values[IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER]
    if (
        page.value_type is ScalarValueType.UUID
        and type(page.value) is UUID
        and output.value_type is ScalarValueType.UUID
        and type(output.value) is UUID
    ):
        return payload.inputs[0].object_id, page.value, output.value
    reject(
        "RPC_INVALID_OPERATION",
        "payload.parameters",
        "image.transform output object identifier is unavailable",
    )


def validate_image_transform_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    maximum_output_bytes: int,
) -> None:
    """Bind a transform terminal result to its exact bounded staging output."""

    _require_uuid4(operation_id, "operation_id")
    _require_uuid4(output_object_id, "payload.outputs.object_id")
    _require_int(maximum_output_bytes, "payload.requested_limits.output_bytes", minimum=1)
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "unsuccessful image.transform results cannot carry outputs",
            )
        return
    if len(payload.outputs) != 1:
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "successful image.transform results require exactly one staging output",
        )
    output = payload.outputs[0]
    if (
        output_object_id == operation_id
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "image.transform output is not bound to its requested staging object and limit",
        )


def validate_pdf_thumbnail_start(payload: OperationStartPayload) -> None:
    """Validate the exact, path-free ``pdf.thumbnail`` version-1 input contract."""

    if len(payload.inputs) != 1:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.thumbnail version 1 requires exactly one source reference",
        )
    source = payload.inputs[0]
    if (
        source.area is not ReferenceArea.SOURCE_READ
        or source.content_kind != "pdf"
        or source.expected_byte_size is None
        or source.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.thumbnail version 1 requires one authenticated PDF source reference",
        )

    expected_names = {
        PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER,
        PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER,
        PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
        PDF_THUMBNAIL_PAGE_ID_PARAMETER,
        PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER,
        PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
        PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER,
        PDF_THUMBNAIL_PAGE_BOX_PARAMETER,
        PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER,
    }
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.thumbnail version 1 requires its exact closed parameter set",
        )

    source_handle = parameters[PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER]
    cache_handle = parameters[PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER]
    if (
        source_handle.value_type is not ScalarValueType.INTEGER
        or type(source_handle.value) is not int
        or source_handle.value <= 0
        or cache_handle.value_type is not ScalarValueType.INTEGER
        or type(cache_handle.value) is not int
        or cache_handle.value <= 0
        or source_handle.value == cache_handle.value
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.thumbnail brokered handles must be distinct positive integers",
        )

    for name in (
        PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
        PDF_THUMBNAIL_PAGE_ID_PARAMETER,
    ):
        parameter = parameters[name]
        if parameter.value_type is not ScalarValueType.UUID or type(parameter.value) is not UUID:
            reject(
                "RPC_INVALID_OPERATION",
                "payload.parameters",
                "pdf.thumbnail object identifiers must be UUID values",
            )

    bound_object_ids = (
        source.object_id,
        cast(UUID, parameters[PDF_THUMBNAIL_PAGE_ID_PARAMETER].value),
        cast(UUID, parameters[PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER].value),
    )
    if len(set(bound_object_ids)) != len(bound_object_ids):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.thumbnail source, page, and cache identifiers must be distinct",
        )

    page_index = parameters[PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER]
    if (
        page_index.value_type is not ScalarValueType.INTEGER
        or type(page_index.value) is not int
        or page_index.value < 0
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.thumbnail page index must be non-negative",
        )

    for name in (
        PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
        PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER,
    ):
        parameter = parameters[name]
        if (
            parameter.value_type is not ScalarValueType.INTEGER
            or type(parameter.value) is not int
            or parameter.value <= 0
        ):
            reject(
                "RPC_INVALID_OPERATION",
                "payload.parameters",
                "pdf.thumbnail dimensions must be positive integers",
            )
    maximum_width = cast(int, parameters[PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER].value)
    maximum_height = cast(int, parameters[PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER].value)
    if maximum_width > payload.requested_limits.pixel_count // maximum_height:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.thumbnail dimensions exceed the requested pixel limit",
        )

    page_box = parameters[PDF_THUMBNAIL_PAGE_BOX_PARAMETER]
    if (
        page_box.value_type is not ScalarValueType.TOKEN
        or type(page_box.value) is not str
        or page_box.value not in {"crop", "media"}
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.thumbnail page box must be crop or media",
        )

    rotation = parameters[PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER]
    if (
        rotation.value_type is not ScalarValueType.INTEGER
        or type(rotation.value) is not int
        or rotation.value not in {0, 90, 180, 270}
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.thumbnail rotation must be a clockwise quarter-turn",
        )


def pdf_thumbnail_cache_object_id(payload: OperationStartPayload) -> UUID:
    """Return the cache object UUID from an already validated thumbnail start."""

    for parameter in payload.parameters:
        if parameter.name == PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER:
            value = parameter.value
            if parameter.value_type is ScalarValueType.UUID and type(value) is UUID:
                return value
            break
    reject(
        "RPC_INVALID_OPERATION",
        "payload.parameters",
        "pdf.thumbnail cache object identifier is unavailable",
    )


def pdf_thumbnail_bound_object_ids(payload: OperationStartPayload) -> tuple[UUID, UUID, UUID]:
    """Return source, page, and cache UUIDs from an already validated start."""

    source_object_id = payload.inputs[0].object_id
    page_object_id: UUID | None = None
    cache_object_id: UUID | None = None
    for parameter in payload.parameters:
        if parameter.value_type is not ScalarValueType.UUID or type(parameter.value) is not UUID:
            continue
        if parameter.name == PDF_THUMBNAIL_PAGE_ID_PARAMETER:
            page_object_id = parameter.value
        elif parameter.name == PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER:
            cache_object_id = parameter.value
    if page_object_id is not None and cache_object_id is not None:
        return source_object_id, page_object_id, cache_object_id
    reject(
        "RPC_INVALID_OPERATION",
        "payload.parameters",
        "pdf.thumbnail bound object identifiers are unavailable",
    )


def validate_pdf_thumbnail_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    cache_object_id: UUID,
    maximum_output_bytes: int,
) -> None:
    """Bind a thumbnail terminal result to its exact staging object and budget."""

    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "unsuccessful pdf.thumbnail results cannot carry outputs",
            )
        return
    if len(payload.outputs) != 1:
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "successful pdf.thumbnail results require exactly one staging output",
        )
    output = payload.outputs[0]
    if (
        cache_object_id == operation_id
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != cache_object_id
        or output.operation_id != operation_id
        or output.content_kind != PDF_THUMBNAIL_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "pdf.thumbnail output is not bound to its requested staging object and limit",
        )


def validate_pdf_export_start(payload: OperationStartPayload) -> None:
    """Validate exact PDF-copy, image-page, or portal-profile authority."""

    if payload.operation_version not in {1, 2, 3}:
        reject("RPC_INVALID_OPERATION", "payload.operation_version", "unsupported export version")
    image_profile = payload.operation_version in {2, 3}
    plan_kind = {
        1: PDF_EXPORT_PLAN_CONTENT_KIND,
        2: IMAGE_PDF_EXPORT_PLAN_CONTENT_KIND,
        3: PORTAL_PDF_EXPORT_PLAN_CONTENT_KIND,
    }[payload.operation_version]
    source_kinds = {"pdf", "jpeg", "png"} if image_profile else {"pdf"}

    if not 2 <= len(payload.inputs) <= MAX_PDF_EXPORT_SOURCES + 1:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.export requires one plan and at least one supported source",
        )
    plan = payload.inputs[0]
    sources = payload.inputs[1:]
    if (
        plan.area is not ReferenceArea.OPERATION_TEMP
        or plan.operation_id is None
        or plan.content_kind != plan_kind
        or plan.expected_byte_size is None
        or not 1 <= plan.expected_byte_size <= MAX_PDF_EXPORT_PLAN_BYTES
        or plan.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.export requires one bounded operation-scoped plan of its exact version",
        )
    if any(
        source.area is not ReferenceArea.SOURCE_READ
        or source.operation_id is not None
        or source.content_kind not in source_kinds
        or source.expected_byte_size is None
        or source.expected_byte_size < 1
        or source.expected_sha256 is None
        for source in sources
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.export requires authenticated source references for its exact version",
        )
    input_object_ids = tuple(reference.object_id for reference in payload.inputs)
    if len(set(input_object_ids)) != len(input_object_ids):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "pdf.export input object identifiers must be distinct",
        )

    expected_names = {
        PDF_EXPORT_PLAN_HANDLE_PARAMETER,
        PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
        PDF_EXPORT_RESULT_HANDLE_PARAMETER,
        PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    }
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.export version 1 requires its exact closed parameter set",
        )
    handles = tuple(
        parameters[name]
        for name in (
            PDF_EXPORT_PLAN_HANDLE_PARAMETER,
            PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
            PDF_EXPORT_RESULT_HANDLE_PARAMETER,
        )
    )
    if any(
        parameter.value_type is not ScalarValueType.INTEGER
        or type(parameter.value) is not int
        or parameter.value <= 0
        for parameter in handles
    ) or len({parameter.value for parameter in handles}) != len(handles):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.export brokered handles must be distinct positive integers",
        )
    output_object = parameters[PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER]
    if (
        output_object.value_type is not ScalarValueType.UUID
        or type(output_object.value) is not UUID
        or output_object.value in set(input_object_ids)
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.export output identity must be a distinct UUID",
        )

    limits = payload.requested_limits
    if limits.page_count > MAX_PDF_EXPORT_PAGES:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "pdf.export page limit exceeds the supported profile",
        )
    source_bytes = 0
    for source in sources:
        size = source.expected_byte_size
        if size is None or source_bytes > MAX_SAFE_INTEGER - size:
            reject(
                "RPC_INVALID_OPERATION",
                "payload.requested_limits",
                "pdf.export source size total is invalid",
            )
        source_bytes += size
    if (
        source_bytes > limits.temporary_storage_bytes
        or limits.output_bytes > limits.temporary_storage_bytes - source_bytes
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "pdf.export temporary storage cannot hold its sources and output",
        )


def pdf_export_bound_object_ids(payload: OperationStartPayload) -> tuple[UUID, ...]:
    """Return plan, source, and output IDs from an already validated export start."""

    validate_pdf_export_start(payload)
    output_object_id: UUID | None = None
    for parameter in payload.parameters:
        if parameter.name == PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER:
            if parameter.value_type is ScalarValueType.UUID and type(parameter.value) is UUID:
                output_object_id = parameter.value
            break
    if output_object_id is None:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "pdf.export output object identifier is unavailable",
        )
    return (*tuple(reference.object_id for reference in payload.inputs), output_object_id)


def pdf_export_output_object_id(payload: OperationStartPayload) -> UUID:
    """Return the staging output UUID from an already validated export start."""

    return pdf_export_bound_object_ids(payload)[-1]


def validate_pdf_export_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    maximum_output_bytes: int,
) -> None:
    """Bind a successful export result to its exact bounded staging output."""

    _require_uuid4(operation_id, "operation_id")
    _require_uuid4(output_object_id, "payload.outputs.object_id")
    _require_int(maximum_output_bytes, "payload.requested_limits.output_bytes", minimum=1)
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "unsuccessful pdf.export results cannot carry outputs",
            )
        return
    if len(payload.outputs) != 1:
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "successful pdf.export results require exactly one staging output",
        )
    output = payload.outputs[0]
    if (
        output_object_id == operation_id
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != PDF_EXPORT_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "pdf.export output is not bound to its requested staging object and limit",
        )


def validate_flattened_pdf_export_start(payload: OperationStartPayload) -> None:
    """Validate additive ``pdf.export.flattened`` version-1 authority."""

    if not 2 <= len(payload.inputs) <= MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "flattened PDF export requires one plan and at least one PDF source",
        )
    plan = payload.inputs[0]
    if (
        plan.area is not ReferenceArea.OPERATION_TEMP
        or plan.operation_id is None
        or plan.content_kind != FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND
        or plan.expected_byte_size is None
        or not 1 <= plan.expected_byte_size <= MAX_PDF_EXPORT_PLAN_BYTES
        or plan.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "flattened PDF export requires one bounded operation-scoped plan",
        )

    source_count = 0
    raster_count = 0
    raster_started = False
    raster_bytes = 0
    total_input_bytes = plan.expected_byte_size
    for reference in payload.inputs[1:]:
        size = reference.expected_byte_size
        if reference.area is ReferenceArea.SOURCE_READ:
            if (
                raster_started
                or reference.operation_id is not None
                or reference.content_kind != "pdf"
                or size is None
                or size < 1
                or reference.expected_sha256 is None
            ):
                reject(
                    "RPC_INVALID_OPERATION",
                    "payload.inputs",
                    "flattened PDF sources must precede raster sections and be authenticated PDFs",
                )
            source_count += 1
        elif reference.area is ReferenceArea.OPERATION_TEMP:
            raster_started = True
            if (
                reference.operation_id is None
                or reference.content_kind != FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND
                or size is None
                or size < 4
                or size % 4 != 0
                or reference.expected_sha256 is None
            ):
                reject(
                    "RPC_INVALID_OPERATION",
                    "payload.inputs",
                    "flattened PDF raster inputs must be exact operation-scoped RGBA8 sections",
                )
            raster_count += 1
            if raster_bytes > MAX_SAFE_INTEGER - size:
                reject(
                    "RPC_INVALID_OPERATION",
                    "payload.requested_limits",
                    "flattened PDF raster size total is invalid",
                )
            raster_bytes += size
        else:
            reject(
                "RPC_INVALID_OPERATION",
                "payload.inputs",
                "flattened PDF inputs use an unsupported reference area",
            )
        if size is None or total_input_bytes > MAX_SAFE_INTEGER - size:
            reject(
                "RPC_INVALID_OPERATION",
                "payload.requested_limits",
                "flattened PDF input size total is invalid",
            )
        total_input_bytes += size
    if source_count < 1 or raster_count > MAX_FLATTENED_PDF_EXPORT_RASTER_ASSETS:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "flattened PDF input reference counts exceed the supported profile",
        )

    input_object_ids = tuple(reference.object_id for reference in payload.inputs)
    if len(set(input_object_ids)) != len(input_object_ids):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "flattened PDF input object identifiers must be distinct",
        )

    expected_names = {
        FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
        FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
        FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
        FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    }
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "flattened PDF export requires its exact closed parameter set",
        )
    handles = tuple(
        parameters[name]
        for name in (
            FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
            FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
            FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
        )
    )
    if any(
        parameter.value_type is not ScalarValueType.INTEGER
        or type(parameter.value) is not int
        or parameter.value <= 0
        for parameter in handles
    ) or len({parameter.value for parameter in handles}) != len(handles):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "flattened PDF handles must be distinct positive integers",
        )
    output_object = parameters[FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER]
    if (
        output_object.value_type is not ScalarValueType.UUID
        or type(output_object.value) is not UUID
        or output_object.value in set(input_object_ids)
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "flattened PDF output identity must be a distinct UUID",
        )

    limits = payload.requested_limits
    if limits.page_count > MAX_PDF_EXPORT_PAGES:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "flattened PDF page limit exceeds the supported profile",
        )
    if (
        raster_bytes > limits.memory_bytes
        or raster_bytes // 4 > limits.pixel_count
        or total_input_bytes > limits.temporary_storage_bytes
        or limits.output_bytes > limits.temporary_storage_bytes - total_input_bytes
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "flattened PDF inputs and output exceed their resource budgets",
        )


def flattened_pdf_export_bound_object_ids(payload: OperationStartPayload) -> tuple[UUID, ...]:
    """Return plan, source, raster, and output IDs from a validated start."""

    validate_flattened_pdf_export_start(payload)
    output_object_id: UUID | None = None
    for parameter in payload.parameters:
        if parameter.name == FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER:
            if parameter.value_type is ScalarValueType.UUID and type(parameter.value) is UUID:
                output_object_id = parameter.value
            break
    if output_object_id is None:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "flattened PDF output object identifier is unavailable",
        )
    return (*tuple(reference.object_id for reference in payload.inputs), output_object_id)


def flattened_pdf_export_output_object_id(payload: OperationStartPayload) -> UUID:
    """Return the flattened-PDF staging UUID from a validated start."""

    return flattened_pdf_export_bound_object_ids(payload)[-1]


def validate_flattened_pdf_export_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    maximum_output_bytes: int,
) -> None:
    """Bind success to one exact bounded flattened-PDF staging section."""

    _require_uuid4(operation_id, "operation_id")
    _require_uuid4(output_object_id, "payload.outputs.object_id")
    _require_int(maximum_output_bytes, "payload.requested_limits.output_bytes", minimum=1)
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "unsuccessful flattened PDF results cannot carry outputs",
            )
        return
    if len(payload.outputs) != 1:
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "successful flattened PDF results require exactly one staging output",
        )
    output = payload.outputs[0]
    if (
        output_object_id == operation_id
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "flattened PDF output is not bound to its requested staging object and limit",
        )


def validate_searchable_pdf_export_start(payload: OperationStartPayload) -> None:
    """Validate the additive ``pdf.export.searchable`` version-1 contract."""

    if not 2 <= len(payload.inputs) <= MAX_PDF_EXPORT_SOURCES + 1:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "searchable PDF export requires one plan and at least one source",
        )
    plan = payload.inputs[0]
    sources = payload.inputs[1:]
    if (
        plan.area is not ReferenceArea.SOURCE_READ
        or plan.operation_id is not None
        or plan.content_kind != SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND
        or plan.expected_byte_size is None
        or not 1 <= plan.expected_byte_size <= MAX_PDF_EXPORT_PLAN_BYTES
        or plan.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "searchable PDF export requires one authenticated immutable plan",
        )
    if any(
        source.area is not ReferenceArea.SOURCE_READ
        or source.operation_id is not None
        or source.content_kind not in OCR_PROCESS_SOURCE_CONTENT_KINDS
        or source.expected_byte_size is None
        or source.expected_byte_size < 1
        or source.expected_sha256 is None
        for source in sources
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "searchable PDF export requires authenticated PDF, JPEG, or PNG sources",
        )
    input_object_ids = tuple(reference.object_id for reference in payload.inputs)
    if len(set(input_object_ids)) != len(input_object_ids):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.inputs",
            "searchable PDF export input identities must be distinct",
        )

    expected_names = {
        SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
        SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
        SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
        SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
        SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
        SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
        SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
    }
    parameters = {parameter.name: parameter for parameter in payload.parameters}
    if len(payload.parameters) != len(expected_names) or set(parameters) != expected_names:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "searchable PDF export requires its exact closed parameter set",
        )
    handle_parameters = tuple(
        parameters[name]
        for name in (
            SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
            SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
            SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
            SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
        )
    )
    if any(
        parameter.value_type is not ScalarValueType.INTEGER
        or type(parameter.value) is not int
        or parameter.value <= 0
        for parameter in handle_parameters
    ) or len({parameter.value for parameter in handle_parameters}) != len(handle_parameters):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "searchable PDF export handles must be distinct positive integers",
        )
    output_parameter = parameters[SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER]
    status_parameter = parameters[SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER]
    if (
        output_parameter.value_type is not ScalarValueType.UUID
        or status_parameter.value_type is not ScalarValueType.UUID
        or type(output_parameter.value) is not UUID
        or type(status_parameter.value) is not UUID
        or output_parameter.value == status_parameter.value
        or output_parameter.value in set(input_object_ids)
        or status_parameter.value in set(input_object_ids)
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "searchable PDF output identities must be distinct UUIDs",
        )
    status_capacity = parameters[SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER]
    if (
        status_capacity.value_type is not ScalarValueType.INTEGER
        or type(status_capacity.value) is not int
        or not 1 <= status_capacity.value <= MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "searchable PDF page-status capacity is invalid",
        )

    limits = payload.requested_limits
    if limits.page_count > MAX_PDF_EXPORT_PAGES:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "searchable PDF page limit exceeds the supported profile",
        )
    source_bytes = 0
    for source in sources:
        size = source.expected_byte_size
        if size is None or source_bytes > MAX_SAFE_INTEGER - size:
            reject(
                "RPC_INVALID_OPERATION",
                "payload.requested_limits",
                "searchable PDF source size total is invalid",
            )
        source_bytes += size
    if (
        source_bytes > limits.temporary_storage_bytes
        or limits.output_bytes > limits.temporary_storage_bytes - source_bytes
        or status_capacity.value
        > limits.temporary_storage_bytes - source_bytes - limits.output_bytes
    ):
        reject(
            "RPC_INVALID_OPERATION",
            "payload.requested_limits",
            "searchable PDF temporary storage cannot hold sources and outputs",
        )


def searchable_pdf_export_bound_object_ids(payload: OperationStartPayload) -> tuple[UUID, ...]:
    """Return plan, source, PDF-output, and status-output IDs."""

    validate_searchable_pdf_export_start(payload)
    values: dict[str, UUID] = {}
    for parameter in payload.parameters:
        if (
            parameter.name
            in {
                SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
                SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
            }
            and parameter.value_type is ScalarValueType.UUID
            and type(parameter.value) is UUID
        ):
            values[parameter.name] = parameter.value
    if set(values) != {
        SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
        SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
    }:
        reject(
            "RPC_INVALID_OPERATION",
            "payload.parameters",
            "searchable PDF output identities are unavailable",
        )
    return (
        *tuple(reference.object_id for reference in payload.inputs),
        values[SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER],
        values[SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER],
    )


def searchable_pdf_export_output_object_ids(
    payload: OperationStartPayload,
) -> tuple[UUID, UUID]:
    """Return the PDF and page-status staging identities."""

    identifiers = searchable_pdf_export_bound_object_ids(payload)
    return identifiers[-2], identifiers[-1]


def searchable_pdf_export_status_capacity_bytes(payload: OperationStartPayload) -> int:
    """Return the exact bounded page-status staging capacity."""

    validate_searchable_pdf_export_start(payload)
    for parameter in payload.parameters:
        if parameter.name == SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER:
            if parameter.value_type is ScalarValueType.INTEGER and type(parameter.value) is int:
                return parameter.value
            break
    reject(
        "RPC_INVALID_OPERATION",
        "payload.parameters",
        "searchable PDF page-status capacity is unavailable",
    )


def validate_searchable_pdf_export_result(
    payload: OperationResultPayload,
    *,
    operation_id: UUID,
    output_object_id: UUID,
    status_object_id: UUID,
    maximum_output_bytes: int,
    maximum_status_bytes: int,
) -> None:
    """Bind success to both exact staging sections and their capacities."""

    _require_uuid4(operation_id, "operation_id")
    _require_uuid4(output_object_id, "payload.outputs.object_id")
    _require_uuid4(status_object_id, "payload.outputs.object_id")
    _require_int(maximum_output_bytes, "payload.requested_limits.output_bytes", minimum=1)
    _require_int(maximum_status_bytes, "payload.parameters.status_capacity_bytes", minimum=1)
    if payload.outcome is not OperationOutcome.SUCCEEDED:
        if payload.outputs:
            reject(
                "RPC_INVALID_RESULT",
                "payload.outputs",
                "unsuccessful searchable PDF results cannot carry outputs",
            )
        return
    if len(payload.outputs) != 2:
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "successful searchable PDF results require PDF and page-status outputs",
        )
    if tuple(output.content_kind for output in payload.outputs) != (
        SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
        SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    ):
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "searchable PDF result outputs must use PDF-then-status order",
        )
    output, status = payload.outputs
    if (
        len({operation_id, output_object_id, status_object_id}) != 3
        or output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
        or status.area is not ReferenceArea.OPERATION_STAGING
        or status.object_id != status_object_id
        or status.operation_id != operation_id
        or status.expected_byte_size is None
        or not 1 <= status.expected_byte_size <= maximum_status_bytes
        or status.expected_sha256 is None
    ):
        reject(
            "RPC_INVALID_RESULT",
            "payload.outputs",
            "searchable PDF outputs are not bound to requested objects and limits",
        )


@dataclass(frozen=True, slots=True)
class OperationAcceptedPayload:
    operation_kind: str
    operation_version: int
    effective_limits: ResourceLimits

    def __post_init__(self) -> None:
        _require_token(self.operation_kind, "payload.operation_kind")
        _require_int(self.operation_version, "payload.operation_version", minimum=1)
        if type(self.effective_limits) is not ResourceLimits:
            reject("RPC_INVALID_OBJECT", "payload.effective_limits", "resource limits are required")


@dataclass(frozen=True, slots=True)
class OperationProgressPayload:
    stage_code: ProgressStage = field(repr=False)
    completed_units: int
    total_units: int | None
    unit: ProgressUnit

    def __post_init__(self) -> None:
        if type(self.stage_code) is not ProgressStage:
            reject(
                "RPC_INVALID_ENUM",
                "payload.stage_code",
                "a registered progress stage is required",
            )
        completed = _require_int(self.completed_units, "payload.completed_units")
        if self.total_units is not None:
            total = _require_int(self.total_units, "payload.total_units")
            if completed > total:
                reject(
                    "RPC_INVALID_PROGRESS",
                    "payload.completed_units",
                    "completed units cannot exceed total units",
                )
        if type(self.unit) is not ProgressUnit:
            reject("RPC_INVALID_ENUM", "payload.unit", "an allowed progress unit is required")


@dataclass(frozen=True, slots=True)
class OperationCancelPayload:
    reason: CancelReason

    def __post_init__(self) -> None:
        if type(self.reason) is not CancelReason:
            reject(
                "RPC_INVALID_ENUM", "payload.reason", "an allowed cancellation reason is required"
            )


@dataclass(frozen=True, slots=True)
class OperationCancelAckPayload:
    disposition: CancelDisposition

    def __post_init__(self) -> None:
        if type(self.disposition) is not CancelDisposition:
            reject(
                "RPC_INVALID_ENUM",
                "payload.disposition",
                "an allowed cancellation disposition is required",
            )


@dataclass(frozen=True, slots=True)
class OperationResultPayload:
    outcome: OperationOutcome
    outputs: tuple[CaseObjectReference, ...]
    error: ErrorPayload | None

    def __post_init__(self) -> None:
        if type(self.outcome) is not OperationOutcome:
            reject(
                "RPC_INVALID_ENUM", "payload.outcome", "an allowed operation outcome is required"
            )
        outputs = _require_tuple(self.outputs, "payload.outputs", maximum=MAX_JSON_COLLECTION_ITEMS)
        if any(type(reference) is not CaseObjectReference for reference in outputs):
            reject(
                "RPC_INVALID_COLLECTION", "payload.outputs", "case-object references are required"
            )
        if self.outcome is OperationOutcome.SUCCEEDED:
            if self.error is not None:
                reject(
                    "RPC_INVALID_RESULT",
                    "payload.error",
                    "successful results cannot carry an error",
                )
        elif self.outcome is OperationOutcome.CANCELLED:
            if outputs or self.error is not None:
                reject(
                    "RPC_INVALID_RESULT",
                    "payload",
                    "cancelled results cannot carry outputs or an error",
                )
        elif outputs or type(self.error) is not ErrorPayload:
            reject(
                "RPC_INVALID_RESULT",
                "payload",
                "failed and timed-out results require one error and no outputs",
            )
        elif self.outcome is OperationOutcome.FAILED:
            if self.error.code is not ErrorCode.WORKER_OPERATION_FAILED:
                reject(
                    "RPC_INVALID_ERROR",
                    "payload.error.code",
                    "failed results require the registered failure code",
                )
        elif self.error.code is not ErrorCode.WORKER_OPERATION_TIMED_OUT:
            reject(
                "RPC_INVALID_ERROR",
                "payload.error.code",
                "timed-out results require the registered timeout code",
            )


@dataclass(frozen=True, slots=True)
class SessionShutdownPayload:
    reason: ShutdownReason

    def __post_init__(self) -> None:
        if type(self.reason) is not ShutdownReason:
            reject("RPC_INVALID_ENUM", "payload.reason", "an allowed shutdown reason is required")


@dataclass(frozen=True, slots=True)
class SessionClosedPayload:
    reason: ClosedReason

    def __post_init__(self) -> None:
        if type(self.reason) is not ClosedReason:
            reject("RPC_INVALID_ENUM", "payload.reason", "an allowed close reason is required")


@dataclass(frozen=True, slots=True)
class ProtocolErrorPayload:
    error: ErrorPayload

    def __post_init__(self) -> None:
        if (
            type(self.error) is not ErrorPayload
            or self.error.code is not ErrorCode.RPC_MESSAGE_INVALID
            or self.error.category is not ErrorCategory.PROTOCOL
            or not self.error.restart_required
        ):
            reject(
                "RPC_INVALID_ERROR",
                "payload.error",
                "a restart-required protocol-category error is required",
            )


@dataclass(frozen=True, slots=True)
class WorkerFaultPayload:
    error: ErrorPayload
    invalidated_operation_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if (
            type(self.error) is not ErrorPayload
            or self.error.code is not ErrorCode.WORKER_INTERNAL_FAULT
            or self.error.category is not ErrorCategory.INTERNAL
            or not self.error.restart_required
        ):
            reject(
                "RPC_INVALID_ERROR", "payload.error", "a restart-required worker error is required"
            )
        operation_ids = _require_tuple(
            self.invalidated_operation_ids,
            "payload.invalidated_operation_ids",
            maximum=MAX_JSON_COLLECTION_ITEMS,
        )
        for operation_id in operation_ids:
            _require_uuid4(operation_id, "payload.invalidated_operation_ids")
        _require_unique(operation_ids, "payload.invalidated_operation_ids")


type MessagePayload = (
    SessionHelloPayload
    | SessionReadyPayload
    | OperationStartPayload
    | OperationAcceptedPayload
    | OperationProgressPayload
    | OperationCancelPayload
    | OperationCancelAckPayload
    | OperationResultPayload
    | SessionShutdownPayload
    | SessionClosedPayload
    | ProtocolErrorPayload
    | WorkerFaultPayload
)

_PAYLOAD_TYPES: Final = MappingProxyType(
    {
        MessageType.SESSION_HELLO: SessionHelloPayload,
        MessageType.SESSION_READY: SessionReadyPayload,
        MessageType.OPERATION_START: OperationStartPayload,
        MessageType.OPERATION_ACCEPTED: OperationAcceptedPayload,
        MessageType.OPERATION_PROGRESS: OperationProgressPayload,
        MessageType.OPERATION_CANCEL: OperationCancelPayload,
        MessageType.OPERATION_CANCEL_ACK: OperationCancelAckPayload,
        MessageType.OPERATION_RESULT: OperationResultPayload,
        MessageType.SESSION_SHUTDOWN: SessionShutdownPayload,
        MessageType.SESSION_CLOSED: SessionClosedPayload,
        MessageType.PROTOCOL_ERROR: ProtocolErrorPayload,
        MessageType.WORKER_FAULT: WorkerFaultPayload,
    }
)

_FIXED_DIRECTIONS: Final = MappingProxyType(
    {
        MessageType.SESSION_HELLO: Direction.CLIENT_TO_WORKER,
        MessageType.SESSION_READY: Direction.WORKER_TO_CLIENT,
        MessageType.OPERATION_START: Direction.CLIENT_TO_WORKER,
        MessageType.OPERATION_ACCEPTED: Direction.WORKER_TO_CLIENT,
        MessageType.OPERATION_PROGRESS: Direction.WORKER_TO_CLIENT,
        MessageType.OPERATION_CANCEL: Direction.CLIENT_TO_WORKER,
        MessageType.OPERATION_CANCEL_ACK: Direction.WORKER_TO_CLIENT,
        MessageType.OPERATION_RESULT: Direction.WORKER_TO_CLIENT,
        MessageType.SESSION_SHUTDOWN: Direction.CLIENT_TO_WORKER,
        MessageType.SESSION_CLOSED: Direction.WORKER_TO_CLIENT,
        MessageType.WORKER_FAULT: Direction.WORKER_TO_CLIENT,
    }
)

_SESSION_MESSAGES: Final = frozenset(
    {
        MessageType.SESSION_HELLO,
        MessageType.SESSION_READY,
        MessageType.SESSION_SHUTDOWN,
        MessageType.SESSION_CLOSED,
    }
)
_OPERATION_MESSAGES: Final = frozenset(
    {
        MessageType.OPERATION_START,
        MessageType.OPERATION_ACCEPTED,
        MessageType.OPERATION_PROGRESS,
        MessageType.OPERATION_CANCEL,
        MessageType.OPERATION_CANCEL_ACK,
        MessageType.OPERATION_RESULT,
    }
)


@dataclass(frozen=True, slots=True)
class ProtocolEnvelope:
    """Closed protocol-v1 envelope bound to one launch, case, request, and sequence."""

    protocol: str
    protocol_version: int
    message_version: int
    message_type: MessageType
    direction: Direction
    session_id: UUID
    worker_instance_id: UUID
    session_capability: SessionCapability
    case_id: UUID
    message_id: UUID
    sender_sequence: int
    request_id: UUID | None
    operation_id: UUID | None
    payload: MessagePayload

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL_NAME or type(self.protocol) is not str:
            reject("RPC_UNSUPPORTED_PROTOCOL", "protocol", "protocol identifier is unsupported")
        if self.protocol_version != PROTOCOL_VERSION or type(self.protocol_version) is not int:
            reject("RPC_UNSUPPORTED_VERSION", "protocol_version", "protocol version is unsupported")
        if self.message_version != MESSAGE_VERSION or type(self.message_version) is not int:
            reject("RPC_UNSUPPORTED_VERSION", "message_version", "message version is unsupported")
        if type(self.message_type) is not MessageType:
            reject("RPC_UNKNOWN_MESSAGE", "message_type", "message type is unsupported")
        if type(self.direction) is not Direction:
            reject("RPC_INVALID_ENUM", "direction", "message direction is unsupported")
        for field_name in ("session_id", "worker_instance_id", "case_id", "message_id"):
            _require_uuid4(getattr(self, field_name), field_name)
        if type(self.session_capability) is not SessionCapability:
            reject(
                "RPC_INVALID_CAPABILITY", "session_capability", "a session capability is required"
            )
        _require_int(self.sender_sequence, "sender_sequence")

        expected_payload = _PAYLOAD_TYPES[self.message_type]
        if type(self.payload) is not expected_payload:
            reject("RPC_PAYLOAD_MISMATCH", "payload", "payload does not match the message type")
        expected_direction = _FIXED_DIRECTIONS.get(self.message_type)
        if expected_direction is not None and self.direction is not expected_direction:
            reject(
                "RPC_DIRECTION_MISMATCH", "direction", "message direction is invalid for its type"
            )

        if self.message_type in _SESSION_MESSAGES:
            _require_uuid4(self.request_id, "request_id")
            if self.operation_id is not None:
                reject(
                    "RPC_INVALID_IDENTIFIER",
                    "operation_id",
                    "session messages cannot name an operation",
                )
        elif self.message_type in _OPERATION_MESSAGES:
            _require_uuid4(self.request_id, "request_id")
            operation_id = _require_uuid4(self.operation_id, "operation_id")
            self._validate_reference_scope(operation_id)
        elif self.message_type is MessageType.WORKER_FAULT:
            if self.request_id is not None:
                _require_uuid4(self.request_id, "request_id")
            if self.operation_id is not None:
                reject(
                    "RPC_INVALID_IDENTIFIER",
                    "operation_id",
                    "worker faults cannot name one operation",
                )
        else:
            if self.request_id is not None:
                _require_uuid4(self.request_id, "request_id")
            if self.operation_id is not None:
                _require_uuid4(self.operation_id, "operation_id")

    def _validate_reference_scope(self, operation_id: UUID) -> None:
        if type(self.payload) is OperationStartPayload:
            for reference in self.payload.inputs:
                if reference.area in {ReferenceArea.SOURCE_READ, ReferenceArea.ASSET_READ}:
                    continue
                if (
                    self.payload.operation_kind
                    in {
                        PDF_EXPORT_CAPABILITY.capability_id,
                        FLATTENED_PDF_EXPORT_CAPABILITY.capability_id,
                    }
                    and reference.area is ReferenceArea.OPERATION_TEMP
                    and reference.operation_id == operation_id
                ):
                    continue
                reject(
                    "RPC_REFERENCE_SCOPE",
                    "payload.inputs",
                    "operation inputs are outside their registered reference scope",
                )
        elif type(self.payload) is OperationResultPayload:
            for reference in self.payload.outputs:
                if (
                    reference.area is not ReferenceArea.OPERATION_STAGING
                    or reference.operation_id != operation_id
                    or reference.expected_byte_size is None
                    or reference.expected_sha256 is None
                ):
                    reject(
                        "RPC_REFERENCE_SCOPE",
                        "payload.outputs",
                        "operation outputs must be verified staging references for this operation",
                    )


type ProtocolMessage = ProtocolEnvelope
