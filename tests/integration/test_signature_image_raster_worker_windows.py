"""Live Windows worker evidence for path-free signature-image rasterization."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self
from uuid import UUID

import pytest
from PIL import Image, PngImagePlugin

from paperworkshell.domain.signature_images import (
    SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES,
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImageRasterRequest,
)
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import (
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
    ResourceLimits,
)
from paperworkshell.worker.signature_image_raster_broker import (
    BrokeredSignatureImageRasterizer,
    SignatureImageRasterBrokerPolicy,
)
from tests.worker_resources import WorkerTestResources

if os.name == "nt":
    import msvcrt

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0305 uses the supported Windows AppContainer worker boundary",
)

_CASE_ID = UUID("83000000-0000-4000-8000-000000000001")
_ASSET_ID = UUID("83000000-0000-4000-8000-000000000002")
_PRIVATE_METADATA = "PRIVATE-LIVE-SIGNATURE-METADATA-CANARY"
_PRIVATE_ICC = b"PRIVATE-LIVE-SIGNATURE-ICC-CANARY"


class _AssetLease:
    def __init__(self, path: Path, payload: bytes) -> None:
        self.case_id = _CASE_ID
        self.asset_id = _ASSET_ID
        self.media_type = SignatureImageMediaType.PNG
        self.byte_size = len(payload)
        self.sha256 = hashlib.sha256(payload).hexdigest()
        self._path = path
        self._stream: BinaryIO | None = None

    @property
    def handle_value(self) -> int:
        assert self._stream is not None
        return msvcrt.get_osfhandle(self._stream.fileno())

    def __enter__(self) -> Self:
        self._stream = self._path.open("rb", buffering=0)
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        assert self._stream is not None
        self._stream.close()
        self._stream = None


class _UnusedCommittedStore:
    def borrow_committed_signature_image(self, *_args: object, **_kwargs: object) -> _AssetLease:
        raise AssertionError("prepared preview must use the caller-retained asset lease")


def _worker(resources: WorkerTestResources) -> WorkerClient:
    return WorkerClient(
        WindowsProcessLauncher(
            WindowsLaunchConfig(
                python_executable=resources.python_executable,
                bootstrap_script=(
                    resources.source_root / "paperworkshell" / "worker" / "host_bootstrap.py"
                ),
                runtime_roots=(resources.runtime_root,),
                code_roots=(resources.source_root,),
                working_directory=resources.source_root,
            )
        ),
        WorkerResourcePolicy(
            process_memory_bytes=256 * 1024 * 1024,
            job_memory_bytes=384 * 1024 * 1024,
            cpu_time_ms=30_000,
            maximum_processes=1,
        ),
        WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(SIGNATURE_IMAGE_RASTER_CAPABILITY,),
    )


def _policy() -> SignatureImageRasterBrokerPolicy:
    return SignatureImageRasterBrokerPolicy(
        ResourceLimits(
            wall_time_ms=20_000,
            cpu_time_ms=15_000,
            memory_bytes=64 * 1024 * 1024,
            temporary_storage_bytes=8,
            output_bytes=SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES + 8,
            page_count=1,
            pixel_count=16,
        ),
        preview_edge_pixels=2,
        pump_interval_seconds=0.01,
    )


def test_live_worker_decodes_asset_handle_and_strips_private_metadata(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    image = Image.new("RGBA", (2, 1))
    image.putdata(((91, 82, 73, 0), (12, 34, 56, 160)))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", _PRIVATE_METADATA)
    path = tmp_path / "PRIVATE-LIVE-SIGNATURE-NAME.png"
    image.save(path, format="PNG", pnginfo=metadata)
    image.close()
    payload = path.read_bytes()
    request = SignatureImageRasterRequest(
        _CASE_ID,
        _ASSET_ID,
        SignatureImageMediaType.PNG,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        2,
        1,
    )
    worker = _worker(worker_test_resources)
    rasterizer = BrokeredSignatureImageRasterizer(
        worker,
        _UnusedCommittedStore(),
        _policy(),
        heavy_operations=HeavyOperationCoordinator(1),
    )

    try:
        with _AssetLease(path, payload) as lease:
            preview = rasterizer.preview_prepared(
                request,
                lease,
                deadline=lambda: False,
                cancellation=lambda: False,
            )
    finally:
        worker.terminate()

    assert preview.pixels == bytes((0, 0, 0, 0, 12, 34, 56, 160))
    assert _PRIVATE_METADATA.encode() not in preview.pixels
    assert _PRIVATE_METADATA not in repr(preview)
    assert str(path) not in repr(preview)
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert worker.generation == 1


def test_live_worker_rejects_embedded_icc_with_sanitized_stable_error(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    image = Image.new("RGBA", (2, 1), (12, 34, 56, 160))
    path = tmp_path / "PRIVATE-LIVE-ICC-NAME.png"
    image.save(path, format="PNG", icc_profile=_PRIVATE_ICC)
    image.close()
    payload = path.read_bytes()
    request = SignatureImageRasterRequest(
        _CASE_ID,
        _ASSET_ID,
        SignatureImageMediaType.PNG,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        2,
        1,
    )
    worker = _worker(worker_test_resources)
    rasterizer = BrokeredSignatureImageRasterizer(
        worker,
        _UnusedCommittedStore(),
        _policy(),
        heavy_operations=HeavyOperationCoordinator(1),
    )

    try:
        with _AssetLease(path, payload) as lease:
            with pytest.raises(SignatureImageError) as caught:
                rasterizer.preview_prepared(
                    request,
                    lease,
                    deadline=lambda: False,
                    cancellation=lambda: False,
                )
    finally:
        worker.terminate()

    assert caught.value.code is SignatureImageErrorCode.COLOR_PROFILE_UNSUPPORTED
    assert caught.value.context == "signature.color_profile"
    assert _PRIVATE_ICC.decode() not in str(caught.value)
    assert str(path) not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert worker.generation == 1
