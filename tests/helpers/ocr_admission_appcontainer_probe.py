"""Report closed OCR admission stages from the restricted AppContainer host."""

from __future__ import annotations

import json
import os
import sys
import time
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Final

_SOURCE_ROOT = Path(__file__).parents[2] / "src"
_SITE_PACKAGES = Path(sys.executable).parent / "Lib" / "site-packages"
sys.path[:0] = [str(_SOURCE_ROOT), str(_SITE_PACKAGES)]

from paperworkshell.worker._windows_runtime import (  # noqa: E402
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.process_creation_policy import (  # noqa: E402
    WorkerProcessCreationAuditHook,
    WorkerProcessCreationDenied,
    configured_process_authorities,
    install_worker_process_creation_policy,
)
from paperworkshell.worker.protocol.bootstrap import decode_bootstrap_grant  # noqa: E402
from paperworkshell.worker.protocol.framing import write_frame  # noqa: E402

_ADMISSION_SECONDS: Final = 15.0
_OCRMY_PDF_VERSION: Final = "17.10.0"
_TESSERACT_VERSION: Final = "5.5.3"
_TESSERACT_EXECUTABLE_ENVIRONMENT: Final = "PWS_TESSERACT_EXECUTABLE"
_TESSDATA_ROOT_ENVIRONMENT: Final = "PWS_TESSDATA_ROOT"


class _AdmissionControl:
    __slots__ = ("_deadline",)

    def __init__(self) -> None:
        self._deadline = time.monotonic() + _ADMISSION_SECONDS

    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return time.monotonic() >= self._deadline


class _AuditObservation:
    __slots__ = ("allowed", "seen", "_validator")

    def __init__(self, validator: WorkerProcessCreationAuditHook) -> None:
        self.seen = False
        self.allowed = False
        self._validator = validator

    def __call__(self, event: str, arguments: tuple[object, ...]) -> None:
        if event != "subprocess.Popen":
            return
        self.seen = True
        try:
            self._validator(event, arguments)
        except WorkerProcessCreationDenied:
            self.allowed = False
        else:
            self.allowed = True


def _empty_result() -> dict[str, bool | str]:
    return {
        "ok": False,
        "stage": "bootstrap",
        "error_code": "PROBE_NOT_RUN",
        "app_container": False,
        "in_job": False,
        "minimal_environment": False,
        "environment_complete": False,
        "executable_absolute": False,
        "executable_file": False,
        "tessdata_absolute": False,
        "tessdata_directory": False,
        "temporary_absolute": False,
        "temporary_directory": False,
        "resources_available": False,
        "metadata_exact": False,
        "module_exact": False,
        "audit_event_seen": False,
        "audit_event_allowed": False,
        "version_exact": False,
        "english_available": False,
        "handler_available": False,
    }


def _run_probe() -> dict[str, bool | str]:
    result = _empty_result()
    observation: _AuditObservation | None = None
    try:
        result["stage"] = "bootstrap"
        decode_bootstrap_grant(read_bootstrap_from_standard_error())
        result["stage"] = "isolation"
        isolation = verify_runtime_isolation()
        result.update(
            {
                "app_container": isolation.app_container,
                "in_job": isolation.in_job,
                "minimal_environment": isolation.minimal_environment,
            }
        )
        result["stage"] = "process_policy"
        observation = _AuditObservation(
            WorkerProcessCreationAuditHook(configured_process_authorities())
        )
        sys.addaudithook(observation)
        install_worker_process_creation_policy()

        result["stage"] = "environment"
        executable_raw = os.environ.get(_TESSERACT_EXECUTABLE_ENVIRONMENT)
        tessdata_raw = os.environ.get(_TESSDATA_ROOT_ENVIRONMENT)
        temporary_raw = os.environ.get("TEMP")
        if not executable_raw or not tessdata_raw or not temporary_raw:
            raise ValueError
        result["environment_complete"] = True
        executable = Path(executable_raw)
        tessdata = Path(tessdata_raw)
        temporary = Path(temporary_raw)
        result.update(
            {
                "executable_absolute": executable.is_absolute(),
                "executable_file": executable.is_file(),
                "tessdata_absolute": tessdata.is_absolute(),
                "tessdata_directory": tessdata.is_dir(),
                "temporary_absolute": temporary.is_absolute(),
            }
        )
        if not (
            result["executable_absolute"]
            and result["tessdata_absolute"]
            and result["temporary_absolute"]
            and result["executable_file"]
            and result["tessdata_directory"]
        ):
            raise ValueError

        result["stage"] = "metadata"
        result["metadata_exact"] = distribution_version("ocrmypdf") == _OCRMY_PDF_VERSION
        if not result["metadata_exact"]:
            raise ValueError

        result["stage"] = "runner_import"
        runner_module = import_module("paperworkshell.ocr.tesseract_runner")
        runner_type = getattr(runner_module, "TesseractCommandRunner", None)
        if not callable(runner_type):
            raise ValueError

        result["stage"] = "adapter_import"
        adapter_module = import_module("paperworkshell.ocr.ocrmypdf_adapter")
        loaded_version = getattr(adapter_module, "loaded_ocrmypdf_version", None)
        if not callable(loaded_version):
            raise ValueError
        result["stage"] = "module"
        result["module_exact"] = loaded_version() == _OCRMY_PDF_VERSION
        if not result["module_exact"]:
            raise ValueError

        result["stage"] = "operation_import"
        operation_module = import_module("paperworkshell.worker.ocr_operation")
        materialize_temp = getattr(operation_module, "_materialize_private_temp", None)
        build_handler = getattr(operation_module, "build_ocr_process_operation_handler", None)
        if not callable(materialize_temp) or not callable(build_handler):
            raise ValueError
        result["stage"] = "temporary_materialize"
        admitted_temp = materialize_temp()
        result["temporary_directory"] = admitted_temp == temporary and admitted_temp.is_dir()
        result["resources_available"] = bool(result["temporary_directory"])
        if not result["resources_available"]:
            raise ValueError

        result["stage"] = "runner"
        runner = runner_type(
            executable=executable,
            tessdata_directory=tessdata,
            temporary_directory=temporary,
            control=_AdmissionControl(),
        )
        result["stage"] = "version"
        result["version_exact"] = runner.version() == _TESSERACT_VERSION
        if not result["version_exact"]:
            raise ValueError

        result["stage"] = "languages"
        result["english_available"] = "eng" in runner.languages()
        if not result["english_available"]:
            raise ValueError

        result["stage"] = "handler"
        result["handler_available"] = build_handler() is not None
        if not result["handler_available"]:
            raise ValueError
        result.update({"ok": True, "stage": "complete", "error_code": ""})
    except WorkerProcessCreationDenied:
        result["error_code"] = "PROCESS_POLICY_DENIED"
    except PackageNotFoundError:
        result["error_code"] = "PACKAGE_METADATA_UNAVAILABLE"
    except BaseException as error:
        code = "PROBE_STAGE_FAILED"
        if result["stage"] in {"version", "languages"}:
            error_code = getattr(error, "code", None)
            error_value = getattr(error_code, "value", None)
            if type(error_value) is str and error_value in {
                "configuration",
                "unavailable",
                "failed",
                "output_limit",
                "cancelled",
                "timed_out",
                "cleanup_failed",
            }:
                code = f"RUNNER_{error_value.upper()}"
        result["error_code"] = code
    finally:
        if observation is not None:
            result["audit_event_seen"] = observation.seen
            result["audit_event_allowed"] = observation.allowed
    return result


def main() -> int:
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    write_frame(
        writer,
        json.dumps(_run_probe(), sort_keys=True, separators=(",", ":")).encode("ascii"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
