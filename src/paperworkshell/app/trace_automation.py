"""Prepare and execute the bounded TRACE PowerShell PDF workflow."""

from __future__ import annotations

from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from collections.abc import Callable
from dataclasses import dataclass, field
from json import loads
from pathlib import Path
from re import fullmatch
from typing import Final

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPagesPdfExportOutcome
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.trace_recipe import (
    MAX_TRACE_RECIPE_BYTES,
    TraceRecipe,
    decode_trace_recipe,
    encode_trace_recipe,
)

# JSON framing, independent of worker and recipe validation. Large requests fail
# before allocating a case or loading an engine. Paths do not travel on argv.
MAX_TRACE_AUTOMATION_REQUEST_BYTES: Final = 4 * 1024 * 1024
_MAX_PATH_LENGTH: Final = 32_767
_MAX_ENCODED_RECIPE_LENGTH: Final = ((MAX_TRACE_RECIPE_BYTES + 2) // 3) * 4
_REQUEST_FIELDS: Final = {"recipe_base64", "input_paths", "output_path"}
_POWERSHELL_BEFORE_RECIPE: Final = r"""#requires -Version 5.1
<#
.SYNOPSIS
Create an ordinary PDF using a PaperWorkShell TRACE recipe.
.DESCRIPTION
Bind one input per recipe source slot, in slot order. Supply a new PDF filename.
The script uses the installed PaperWorkShell Python runtime and PDF engines offline.
Use a PowerShell host whose existing policy permits locally generated scripts.
It prints a JSON result with the checks PaperWorkShell actually performed.
.EXAMPLE
& '.\Recipe.ps1' -InputPaths @('C:\Work\First input.pdf', 'C:\Work\Second.jpg') -OutputPath 'C:\Work\New output.pdf'
#>
param(
    [Parameter(Mandatory = $true)] [string[]] $InputPaths,
    [Parameter(Mandatory = $true)] [string] $OutputPath,
    [string] $RuntimeRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\PaperWorkShell\runtime')
)
$ErrorActionPreference = 'Stop'
$recipe = '
"""
_POWERSHELL_AFTER_RECIPE: Final = r"""

$pathResolver = $ExecutionContext.SessionState.Path
[string[]] $literalInputs = @(
    foreach ($item in $InputPaths) {
        $pathResolver.GetUnresolvedProviderPathFromPSPath($item)
    }
)
$literalOutput = $pathResolver.GetUnresolvedProviderPathFromPSPath($OutputPath)
$runtime = $pathResolver.GetUnresolvedProviderPathFromPSPath($RuntimeRoot)
if ($runtime -notmatch '^[A-Za-z]:\\') {
    throw '[TRACE_RUNTIME_UNAVAILABLE] Use a locally installed PaperWorkShell runtime.'
}
$python = Join-Path $runtime 'python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw '[TRACE_RUNTIME_UNAVAILABLE] Install PaperWorkShell or supply its runtime directory.'
}

$request = [ordered]@{
    recipe_base64 = $recipe
    input_paths = $literalInputs
    output_path = $literalOutput
} | ConvertTo-Json -Compress
$tracePreviousEncoding = $OutputEncoding
Push-Location -LiteralPath $runtime
try {
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $PSNativeCommandUseErrorActionPreference = $false
    $request | & $python -I -B -m paperworkshell --trace-batch
    $traceExit = $LASTEXITCODE
} finally {
    $OutputEncoding = $tracePreviousEncoding
    Pop-Location
}
exit $traceExit
"""


class TraceAutomationError(ValueError):
    """A path-free automation error suitable for the GUI and console."""

    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class TraceAutomationRequest:
    recipe: TraceRecipe = field(repr=False)
    inputs: tuple[Path, ...] = field(repr=False)
    destination: Path = field(repr=False)


def make_trace_powershell(recipe: TraceRecipe) -> bytes:
    """Embed the checked declarative recipe as data in a fixed script."""

    recipe.require_automation_supported()
    encoded = b64encode(encode_trace_recipe(recipe)).decode("ascii")
    prefix = _POWERSHELL_BEFORE_RECIPE.removesuffix("\n")
    return (prefix + encoded + "'" + _POWERSHELL_AFTER_RECIPE).encode("ascii")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _literal_path(value: object) -> Path:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_PATH_LENGTH or "\0" in value:
        raise ValueError
    path_length = len(value.encode("utf-16-le")) // 2
    result = Path(value)
    if (
        not result.is_absolute()
        or path_length > _MAX_PATH_LENGTH
        or fullmatch(r"[A-Za-z]:", result.drive) is None
        or ":" in value[2:]
        or any(part in {".", ".."} for part in value.replace("/", "\\")[3:].split("\\"))
    ):
        raise ValueError
    return result


def decode_trace_automation_request(payload: bytes) -> TraceAutomationRequest:
    """Admit the script request without evaluating any supplied value."""

    try:
        if not 0 < len(payload) <= MAX_TRACE_AUTOMATION_REQUEST_BYTES:
            raise ValueError
        request: object = loads(payload.decode("utf-8"), object_pairs_hook=_unique_json_object)
        if not isinstance(request, dict) or set(request) != _REQUEST_FIELDS:
            raise ValueError
        paths = request["input_paths"]
        encoded_recipe = request["recipe_base64"]
        if (
            not isinstance(paths, list)
            or not isinstance(encoded_recipe, str)
            or len(encoded_recipe) > _MAX_ENCODED_RECIPE_LENGTH
        ):
            raise ValueError
        recipe_payload = b64decode(encoded_recipe, validate=True)
        destination = _literal_path(request["output_path"])
        if destination.suffix.casefold() != ".pdf":
            raise ValueError
    except Base64Error, UnicodeError, ValueError, RecursionError:
        raise TraceAutomationError(
            "TRACE_REQUEST_INVALID",
            "Use the generated script with absolute local input paths and a new .pdf output path.",
        ) from None

    recipe = decode_trace_recipe(recipe_payload)
    recipe.require_automation_supported()
    if len(paths) != len(recipe.sources):
        raise TraceAutomationError(
            "TRACE_BINDINGS_INVALID",
            f"Supply {len(recipe.sources)} input paths in the order of the recipe source slots.",
        )
    try:
        inputs = tuple(_literal_path(item) for item in paths)
    except ValueError, UnicodeError:
        raise TraceAutomationError(
            "TRACE_INPUT_PATH_INVALID", "Supply one absolute local file path per input slot."
        ) from None
    return TraceAutomationRequest(recipe, inputs, destination)


def execute_trace_automation(
    backend: OrganizerBackend,
    request: TraceAutomationRequest,
    *,
    cancellation: Callable[[], bool],
) -> OrganizerPagesPdfExportOutcome:
    """Replay a recipe in the caller's transient case using normal document services."""

    request.recipe.require_automation_supported()
    for index, path in enumerate(request.inputs, start=1):
        result = backend.import_source(path, f"Input {index}", cancellation=cancellation)
        if result.refresh_required:
            backend.reload_case()
    backend.apply_trace_recipe(request.recipe)
    if cancellation():
        raise PdfExportError(PdfExportErrorCode.CANCELLED, "trace.automation")
    return backend.export_pages_pdf(
        request.destination, cancellation=cancellation, progress=lambda _value: None
    )
