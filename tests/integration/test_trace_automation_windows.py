"""Exercise the generated script's real Windows PowerShell transport."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from base64 import b64decode
from pathlib import Path
from venv import EnvBuilder

import pytest

from paperworkshell.app.trace_automation import TraceAutomationError
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.trace_recipe import (
    TraceRecipe,
    TraceRecipePage,
    TraceSourceBinding,
    decode_trace_recipe,
)
from paperworkshell.ui.trace_script import export_trace_powershell

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell transport")


def _powershell_for_local_scripts() -> Path:
    windows_powershell = (
        Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    for executable in (shutil.which("pwsh.exe"), str(windows_powershell)):
        if executable is None:
            continue
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", "Get-ExecutionPolicy"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
        if result.stdout.strip() in {"RemoteSigned", "Unrestricted", "Bypass"}:
            return Path(executable)
    pytest.skip("No available PowerShell policy permits locally generated scripts.")


def test_exported_script_transmits_literal_paths_and_native_exit_code(tmp_path: Path) -> None:
    powershell = _powershell_for_local_scripts()
    recipe = TraceRecipe(
        (
            TraceSourceBinding(1, DetectedMediaType.PDF, 3),
            TraceSourceBinding(2, DetectedMediaType.JPEG, 1),
        ),
        (TraceRecipePage(2, 1, 0), TraceRecipePage(1, 3, 90)),
    )
    script = tmp_path / "TRACE recipe.ps1"
    export_trace_powershell(recipe, script)
    original_script = script.read_bytes()
    with pytest.raises(TraceAutomationError, match="filename is in use"):
        export_trace_powershell(recipe, script)
    assert script.read_bytes() == original_script

    # Only the consumer is a test double. The unchanged script invokes a real
    # interpreter in isolated mode from the available native PowerShell host.
    runtime = tmp_path / "Local runtime"
    EnvBuilder(with_pip=False).create(runtime)
    package = runtime / "Lib" / "site-packages" / "paperworkshell"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "__main__.py").write_text(
        "import json, sys\nprint(json.dumps(json.load(sys.stdin.buffer)))\nsys.exit(17)\n",
        encoding="utf-8",
    )

    source_pdf = tmp_path / "März; $doc [2].pdf"
    source_image = tmp_path / "São Paulo.jpg"
    output = tmp_path / "Übergabe [fertig].pdf"
    env = {
        **os.environ,
        "PWS_TEST_TRACE_SCRIPT": str(script),
        "PWS_TEST_TRACE_PDF": str(source_pdf),
        "PWS_TEST_TRACE_IMAGE": str(source_image),
        "PWS_TEST_TRACE_OUTPUT": str(output),
        "PWS_TEST_TRACE_RUNTIME": str(runtime / "Scripts"),
    }
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "& $env:PWS_TEST_TRACE_SCRIPT "
            "-InputPaths @($env:PWS_TEST_TRACE_PDF, $env:PWS_TEST_TRACE_IMAGE) "
            "-OutputPath $env:PWS_TEST_TRACE_OUTPUT -RuntimeRoot $env:PWS_TEST_TRACE_RUNTIME; "
            "exit $LASTEXITCODE",
        ],
        env=env,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 17, result.stderr.decode(errors="replace")
    received = json.loads(result.stdout)
    assert received["input_paths"] == [str(source_pdf), str(source_image)]
    assert received["output_path"] == str(output)
    assert decode_trace_recipe(b64decode(received["recipe_base64"])) == recipe
