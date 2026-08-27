"""Guide and record the remaining PWS-0307 Windows reader checks.

The deterministic corpus generator deliberately requires two explicit, new
destinations.  This human-test wrapper keeps that lower-level contract intact
while owning a fixed ignored workspace, pinning the exact artifact already
accepted by the W2 Edge run, and recording only closed, content-free answers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, TextIO, cast

from paperworkshell.overlays.exporting import FlattenedPdfExportError
from tools.pws0307_overlay_reader_corpus import (
    EVIDENCE_VERSION,
    GENERATOR_VERSION,
    TASK_ID,
    ReaderCorpusError,
    generate_reader_corpus,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
READER_TEST_PROTOCOL_VERSION: Final = 2
READER_RESULT_SCHEMA_VERSION: Final = 1
MAXIMUM_READER_RESULT_BYTES: Final = 16 * 1024
MAXIMUM_RESULT_ATTEMPTS_PER_SLOT: Final = 99
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_GIT_REVISION: Final = re.compile(r"[0-9a-f]{40}\Z")
_VIEWER_VERSION: Final = re.compile(r"[0-9]{1,4}(?:\.[0-9]{1,8}){1,4}\Z")
_UTC_TIMESTAMP: Final = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_WINDOWS_BUILD: Final = re.compile(r"10\.0\.[0-9]{4,6}\Z")
_WINDOWS_RELEASE: Final = re.compile(r"(?:10|11)\Z")
_WINDOWS_EDITIONS: Final = frozenset(
    {
        "Core",
        "CoreCountrySpecific",
        "CoreN",
        "CoreSingleLanguage",
        "Education",
        "EducationN",
        "Enterprise",
        "EnterpriseEval",
        "EnterpriseG",
        "EnterpriseGN",
        "EnterpriseN",
        "EnterpriseNEval",
        "IoTEnterprise",
        "IoTEnterpriseS",
        "Professional",
        "ProfessionalEducation",
        "ProfessionalEducationN",
        "ProfessionalN",
        "ProfessionalSingleLanguage",
        "SE",
    }
)
_WINDOWS_ARCHITECTURES: Final = frozenset({"AMD64", "ARM64", "x86", "x86_64"})
_RESULT_KEYS: Final = frozenset(
    {
        "schema_version",
        "protocol_version",
        "task_id",
        "attempt",
        "tested_at_utc",
        "operator_role",
        "viewer",
        "windows",
        "artifact",
        "checks",
        "result",
        "defect_categories",
        "affected_pages",
    }
)
_VIEWER_KEYS: Final = frozenset({"slot", "name", "version_build", "mode"})
_ARTIFACT_KEYS: Final = frozenset(
    {
        "implementation_revision",
        "generator_version",
        "pdf_sha256",
        "pdf_byte_size",
        "page_count",
        "evidence_json_sha256",
    }
)
_WINDOWS_KEYS: Final = frozenset(
    {"platform", "edition", "release", "version_build", "architecture"}
)


@dataclass(frozen=True, slots=True)
class ReaderArtifactBinding:
    """Exact immutable artifact accepted by the existing W2 reader run."""

    implementation_revision: str
    pdf_sha256: str
    pdf_byte_size: int
    evidence_sha256: str
    evidence_byte_size: int
    page_count: int
    semantic_fingerprint: str

    def __post_init__(self) -> None:
        if (
            _GIT_REVISION.fullmatch(self.implementation_revision) is None
            or _SHA256.fullmatch(self.pdf_sha256) is None
            or _SHA256.fullmatch(self.evidence_sha256) is None
            or _SHA256.fullmatch(self.semantic_fingerprint) is None
            or type(self.pdf_byte_size) is not int
            or self.pdf_byte_size < 1
            or type(self.evidence_byte_size) is not int
            or self.evidence_byte_size < 1
            or type(self.page_count) is not int
            or self.page_count < 1
        ):
            raise ValueError("invalid PWS-0307 reader artifact binding")


APPROVED_READER_ARTIFACT: Final = ReaderArtifactBinding(
    implementation_revision="e088bb7dd10088f3eb454763e3fd4bd203706eb4",
    pdf_sha256="05faf2055ccb07d85eca7afdb5f446c2b3aeba895d07c2f5fab4d769059fda3e",
    pdf_byte_size=9_485,
    evidence_sha256="23c3ea41b4f58739c43a2ef9b805fc6b957a713e10749989b51bad5b1c6c3c21",
    evidence_byte_size=5_300,
    page_count=5,
    semantic_fingerprint="162ab8bdbbefd3b91ecea8b35ae9836b63e21548dd56384622a472cebc0c2e07",
)

DEFAULT_WORKSPACE: Final = (
    PROJECT_ROOT / "output" / "pws0307-reader-test" / APPROVED_READER_ARTIFACT.pdf_sha256[:12]
)
CORPUS_PDF_NAME: Final = "pws-0307-overlay-reader-corpus.pdf"
CORPUS_EVIDENCE_NAME: Final = "pws-0307-overlay-reader-corpus.json"
TEST_CARD_NAME: Final = f"pws-0307-reader-test-card-v{READER_TEST_PROTOCOL_VERSION}.txt"


class ReaderSlot(StrEnum):
    """Remaining reader slots that need a qualifying human result."""

    W1 = "W1"
    W3 = "W3"
    W4 = "W4"


@dataclass(frozen=True, slots=True)
class ViewerDefinition:
    name: str
    mode_prompt: str
    modes: tuple[tuple[str, str], ...]


VIEWERS: Final[Mapping[ReaderSlot, ViewerDefinition]] = {
    ReaderSlot.W1: ViewerDefinition(
        name="Adobe Acrobat Reader desktop PDF viewer",
        mode_prompt=(
            "Confirm the unified Adobe desktop app is operating in Reader/free or reduced mode"
        ),
        modes=(("reader_free_or_reduced", "Reader/free or reduced mode (required for W1)"),),
    ),
    ReaderSlot.W3: ViewerDefinition(
        name="Google Chrome built-in PDF viewer",
        mode_prompt="Confirm the local PDF opened in Chrome's built-in viewer",
        modes=(("built_in_local_pdf_viewer", "Built-in local PDF viewer"),),
    ),
    ReaderSlot.W4: ViewerDefinition(
        name="Mozilla Firefox built-in PDF viewer",
        mode_prompt="Confirm the local PDF opened in Firefox's built-in viewer",
        modes=(("built_in_local_pdf_viewer", "Built-in local PDF viewer"),),
    ),
}


class ReaderDefect(StrEnum):
    """Closed defect vocabulary for content-free human evidence."""

    VIEWER_CONFIGURATION = "viewer_configuration"
    PAGE_SEQUENCE = "page_sequence"
    VISUAL_RENDERING = "visual_rendering"
    TEXT_SEARCH = "text_search"
    DOCUMENT_INTERACTIVITY = "document_interactivity"
    CLOSE_REOPEN = "close_reopen"


@dataclass(slots=True, repr=False)
class ReaderTestError(RuntimeError):
    """Stable path-free failure from the human-test wrapper."""

    code: str
    safe_message: str

    def __post_init__(self) -> None:
        self.args = (self.code, self.safe_message)

    def __str__(self) -> str:
        return f"{self.code}: {self.safe_message}"


class _DuplicateResultKeyError(ValueError):
    """Internal marker for a non-closed JSON object."""


def _reject_duplicate_result_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateResultKeyError
        result[key] = value
    return result


@dataclass(frozen=True, slots=True, repr=False)
class PreparedReaderTest:
    """Verified local paths plus the immutable public binding."""

    workspace: Path
    pdf_path: Path
    evidence_path: Path
    test_card_path: Path
    binding: ReaderArtifactBinding
    reused: bool


@dataclass(frozen=True, slots=True)
class ReaderAnswers:
    """Closed human observations used to derive a result."""

    configuration_confirmed: bool
    page_sequence_passed: bool
    visual_zoom_sweep_passed: bool
    search_matches: int
    noninteractive_passed: bool
    close_reopen_passed: bool
    affected_pages: tuple[int, ...] = ()

    @property
    def passed(self) -> bool:
        return (
            self.configuration_confirmed
            and self.page_sequence_passed
            and self.visual_zoom_sweep_passed
            and self.search_matches == 3
            and self.noninteractive_passed
            and self.close_reopen_passed
        )

    @property
    def defects(self) -> tuple[ReaderDefect, ...]:
        defects: list[ReaderDefect] = []
        if not self.configuration_confirmed:
            defects.append(ReaderDefect.VIEWER_CONFIGURATION)
        if not self.page_sequence_passed:
            defects.append(ReaderDefect.PAGE_SEQUENCE)
        if not self.visual_zoom_sweep_passed:
            defects.append(ReaderDefect.VISUAL_RENDERING)
        if self.search_matches != 3:
            defects.append(ReaderDefect.TEXT_SEARCH)
        if not self.noninteractive_passed:
            defects.append(ReaderDefect.DOCUMENT_INTERACTIVITY)
        if not self.close_reopen_passed:
            defects.append(ReaderDefect.CLOSE_REOPEN)
        return tuple(defects)


def _path_is_link_like(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError as error:
        raise ReaderTestError(
            "PWS0307_READER_WORKSPACE_INVALID",
            "The local reader-test workspace could not be inspected safely.",
        ) from error


def _workspace_components(root: Path, workspace: Path) -> tuple[Path, ...]:
    relative = workspace.relative_to(root)
    current = root
    components: list[Path] = []
    for part in relative.parts:
        current /= part
        components.append(current)
    return tuple(components)


def _ensure_workspace(workspace: Path, allowed_root: Path) -> tuple[Path, bool]:
    if not isinstance(workspace, Path) or not isinstance(allowed_root, Path):
        raise ReaderTestError(
            "PWS0307_READER_WORKSPACE_INVALID",
            "The local reader-test workspace is invalid.",
        )
    try:
        root = allowed_root.resolve(strict=True)
        candidate = Path(os.path.abspath(workspace))
        if candidate == root or not candidate.is_relative_to(root):
            raise ReaderTestError(
                "PWS0307_READER_WORKSPACE_INVALID",
                "The reader-test workspace must stay below its approved local output root.",
            )
        for component in _workspace_components(root, candidate):
            if component.exists() and _path_is_link_like(component):
                raise ReaderTestError(
                    "PWS0307_READER_WORKSPACE_INVALID",
                    "The reader-test workspace must not contain a link or junction.",
                )
        created = not candidate.exists()
        candidate.mkdir(parents=True, exist_ok=True)
        if not candidate.is_dir():
            raise ReaderTestError(
                "PWS0307_READER_WORKSPACE_INVALID",
                "The reader-test workspace is not a directory.",
            )
        for component in _workspace_components(root, candidate):
            if _path_is_link_like(component):
                raise ReaderTestError(
                    "PWS0307_READER_WORKSPACE_INVALID",
                    "The reader-test workspace must not contain a link or junction.",
                )
        resolved = candidate.resolve(strict=True)
        if resolved != candidate or not resolved.is_relative_to(root):
            raise ReaderTestError(
                "PWS0307_READER_WORKSPACE_INVALID",
                "The reader-test workspace resolved outside its approved local output root.",
            )
        return resolved, created
    except ReaderTestError:
        raise
    except OSError:
        raise ReaderTestError(
            "PWS0307_READER_WORKSPACE_INVALID",
            "The local reader-test workspace could not be created safely.",
        ) from None


def _read_exact_file(path: Path, expected_size: int) -> bytes:
    if _path_is_link_like(path):
        raise ReaderTestError(
            "PWS0307_READER_PAIR_MISMATCH",
            "The reader-test pair is not the approved immutable artifact.",
        )
    try:
        if not path.is_file():
            raise ReaderTestError(
                "PWS0307_READER_PAIR_INCOMPLETE",
                "Both approved reader-test files must be present.",
            )
        payload = bytearray()
        with path.open("rb", buffering=0) as stream:
            while len(payload) <= expected_size:
                block = stream.read(min(64 * 1024, expected_size + 1 - len(payload)))
                if not block:
                    break
                payload.extend(block)
        if len(payload) != expected_size:
            raise ReaderTestError(
                "PWS0307_READER_PAIR_MISMATCH",
                "The reader-test pair is not the approved immutable artifact.",
            )
        return bytes(payload)
    except ReaderTestError:
        raise
    except OSError:
        raise ReaderTestError(
            "PWS0307_READER_PAIR_UNREADABLE",
            "The approved reader-test files could not be read safely.",
        ) from None


def _read_bounded_file(path: Path, maximum_size: int) -> bytes:
    try:
        if _path_is_link_like(path) or not path.is_file():
            raise OSError("evidence file is unavailable or link-like")
        with path.open("rb", buffering=0) as stream:
            payload = stream.read(maximum_size + 1)
    except ReaderTestError as error:
        raise OSError("evidence file could not be inspected") from error
    if len(payload) > maximum_size:
        raise OSError("evidence file exceeds its byte limit")
    return payload


def _verify_evidence_payload(payload: bytes, binding: ReaderArtifactBinding) -> None:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        raise ReaderTestError(
            "PWS0307_READER_PAIR_MISMATCH",
            "The reader-test evidence file is not the approved deterministic record.",
        ) from None
    if type(decoded) is not dict:
        raise ReaderTestError(
            "PWS0307_READER_PAIR_MISMATCH",
            "The reader-test evidence file is not the approved deterministic record.",
        )
    evidence = cast(dict[str, object], decoded)
    artifact = evidence.get("artifact")
    manual = evidence.get("manual_reader_matrix")
    if (
        evidence.get("evidence_version") != EVIDENCE_VERSION
        or evidence.get("generator_version") != GENERATOR_VERSION
        or evidence.get("task_id") != TASK_ID
        or evidence.get("result") != "automated_verification_passed"
        or type(artifact) is not dict
        or artifact.get("sha256") != binding.pdf_sha256
        or artifact.get("byte_size") != binding.pdf_byte_size
        or artifact.get("page_count") != binding.page_count
        or artifact.get("semantic_fingerprint") != binding.semantic_fingerprint
        or type(manual) is not dict
        or manual.get("result") != "not_run"
    ):
        raise ReaderTestError(
            "PWS0307_READER_PAIR_MISMATCH",
            "The reader-test evidence file is not the approved deterministic record.",
        )


def verify_approved_pair(
    workspace: Path = DEFAULT_WORKSPACE,
    *,
    allowed_root: Path = PROJECT_ROOT,
    binding: ReaderArtifactBinding = APPROVED_READER_ARTIFACT,
) -> PreparedReaderTest:
    """Fail closed unless both files equal the exact W2-approved pair."""

    resolved, _ = _ensure_workspace(workspace, allowed_root)
    pdf_path = resolved / CORPUS_PDF_NAME
    evidence_path = resolved / CORPUS_EVIDENCE_NAME
    if pdf_path.exists() != evidence_path.exists():
        raise ReaderTestError(
            "PWS0307_READER_PAIR_INCOMPLETE",
            "Only one reader-test file exists; both files were preserved. Ask Codex to inspect it.",
        )
    if not pdf_path.exists():
        raise ReaderTestError(
            "PWS0307_READER_PAIR_INCOMPLETE",
            "The approved reader-test pair has not been prepared yet.",
        )
    pdf_payload = _read_exact_file(pdf_path, binding.pdf_byte_size)
    evidence_payload = _read_exact_file(evidence_path, binding.evidence_byte_size)
    if (
        hashlib.sha256(pdf_payload).hexdigest() != binding.pdf_sha256
        or hashlib.sha256(evidence_payload).hexdigest() != binding.evidence_sha256
    ):
        raise ReaderTestError(
            "PWS0307_READER_PAIR_MISMATCH",
            "The local pair differs from the artifact already accepted by W2. Do not test it.",
        )
    _verify_evidence_payload(evidence_payload, binding)
    return PreparedReaderTest(
        workspace=resolved,
        pdf_path=pdf_path,
        evidence_path=evidence_path,
        test_card_path=resolved / TEST_CARD_NAME,
        binding=binding,
        reused=True,
    )


def _test_card(binding: ReaderArtifactBinding) -> str:
    return f"""PWS-0307 READER TEST CARD - PROTOCOL {READER_TEST_PROTOCOL_VERSION}

OPEN ONLY THIS FILE
  {CORPUS_PDF_NAME}

IMMUTABLE BINDING (already accepted by W2 Edge)
  PDF SHA-256: {binding.pdf_sha256}
  PDF bytes: {binding.pdf_byte_size}
  Evidence JSON SHA-256: {binding.evidence_sha256}
  Pages: {binding.page_count}

RUN ONLY THE REMAINING SLOTS
  W1 - Adobe Acrobat Reader/free-reduced mode in the unified desktop app
  W3 - Google Chrome built-in PDF viewer
  W4 - Mozilla Firefox built-in PDF viewer

ONE COMPLETE VIEWER RUN
  1. Open About and keep the numeric dotted version/build for the recorder.
  2. Open the local PDF with the named desktop/built-in viewer. Confirm five pages.
  3. Navigate 1 > 2 > 3 > 4 > 5 > 4 > 3 > 2 > 1.
  4. Inspect every page at Fit Page, 100%, 200%, and 400%:
       P1 - heading plus crisp text, date, and initials; nothing clipped.
       P2 - blue checkmark and red cross stay in their reference regions.
       P3 - translucent stroked ellipse stays centered and contained.
       P4 - signature-image visual mark stays visible and aspect-preserved.
       P5 - translucent highlight stays oriented and inside the crop.
  5. On P1 search for all three visible overlay values; require 3/3 found.
  6. Click all eight overlay regions and Tab through the document surface.
     No document widget, editor, form prompt, script, attachment, or action may activate.
  7. Close the app/tab, reopen the same PDF, and recheck all pages at Fit Page and 400%.
  8. Return to the terminal. The recorder derives PASS; it never asks you to type a hash.

FAILURE RULE
  If anything fails, keep the artifact unchanged. Record the closed failure and affected pages.
  Do not regenerate, edit, download, upload, or replace the PDF between viewer runs.
"""


def _write_exclusive_text(path: Path, payload: str) -> None:
    stream: TextIO | None = None
    try:
        stream = path.open("x", encoding="utf-8", newline="\n")
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
    except FileExistsError:
        raise ReaderTestError(
            "PWS0307_READER_WORKSPACE_CONFLICT",
            "A reader-test support file already exists unexpectedly and was preserved.",
        ) from None
    except OSError:
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReaderTestError(
            "PWS0307_READER_WORKSPACE_INVALID",
            "A reader-test support file could not be written safely.",
        ) from None


def _verify_test_card(path: Path, binding: ReaderArtifactBinding) -> None:
    expected = _test_card(binding).encode("utf-8")
    try:
        actual = _read_bounded_file(path, len(expected))
    except OSError:
        raise ReaderTestError(
            "PWS0307_READER_WORKSPACE_CONFLICT",
            "The reader-test card differs from the reviewed protocol and was preserved.",
        ) from None
    if actual != expected:
        raise ReaderTestError(
            "PWS0307_READER_WORKSPACE_CONFLICT",
            "The reader-test card differs from the reviewed protocol and was preserved.",
        )


def _ensure_test_card(path: Path, binding: ReaderArtifactBinding) -> None:
    """Create the exact card once, accepting an exact concurrent creation."""

    if path.exists():
        _verify_test_card(path, binding)
        return
    try:
        _write_exclusive_text(path, _test_card(binding))
    except ReaderTestError as error:
        if error.code != "PWS0307_READER_WORKSPACE_CONFLICT" or not path.exists():
            raise
        _verify_test_card(path, binding)


def _cleanup_new_pack(paths: Sequence[Path], workspace: Path, created_workspace: bool) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if created_workspace:
        try:
            workspace.rmdir()
        except OSError:
            pass


def prepare_reader_test(
    workspace: Path = DEFAULT_WORKSPACE,
    *,
    allowed_root: Path = PROJECT_ROOT,
    binding: ReaderArtifactBinding = APPROVED_READER_ARTIFACT,
    generator: Callable[[Path, Path], dict[str, object]] = generate_reader_corpus,
) -> PreparedReaderTest:
    """Create once or verify/reuse the exact bound reader-test pack."""

    resolved, created_workspace = _ensure_workspace(workspace, allowed_root)
    pdf_path = resolved / CORPUS_PDF_NAME
    evidence_path = resolved / CORPUS_EVIDENCE_NAME
    card_path = resolved / TEST_CARD_NAME
    pair_exists = pdf_path.exists() or evidence_path.exists()
    if pair_exists:
        prepared = verify_approved_pair(
            resolved,
            allowed_root=allowed_root,
            binding=binding,
        )
        _ensure_test_card(card_path, binding)
        return prepared
    if card_path.exists() or any(resolved.glob("pws-0307-*-attempt-*.json")):
        raise ReaderTestError(
            "PWS0307_READER_WORKSPACE_CONFLICT",
            "Reader-test support files exist without their approved pair and were preserved.",
        )
    generated_paths = (pdf_path, evidence_path)
    generation_completed = False
    pair_verified = False
    try:
        generator(pdf_path, evidence_path)
        generation_completed = True
        prepared = verify_approved_pair(
            resolved,
            allowed_root=allowed_root,
            binding=binding,
        )
        pair_verified = True
        _ensure_test_card(card_path, binding)
    except BaseException:
        if generation_completed and not pair_verified:
            _cleanup_new_pack(generated_paths, resolved, created_workspace)
        raise
    return PreparedReaderTest(
        workspace=prepared.workspace,
        pdf_path=prepared.pdf_path,
        evidence_path=prepared.evidence_path,
        test_card_path=card_path,
        binding=binding,
        reused=False,
    )


def _safe_system_record() -> dict[str, str]:
    if sys.platform != "win32":
        raise ReaderTestError(
            "PWS0307_READER_WINDOWS_REQUIRED",
            "The reader matrix must be recorded on Windows.",
        )
    edition = platform.win32_edition()
    return {
        "platform": "Windows",
        "edition": edition or "unknown",
        "release": platform.release() or "unknown",
        "version_build": platform.version() or "unknown",
        "architecture": platform.machine() or "unknown",
    }


def _validate_viewer_version(value: str) -> str:
    if type(value) is not str:
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "Enter only the numeric dotted version/build shown by the viewer's About screen.",
        )
    normalized = value.strip()
    if _VIEWER_VERSION.fullmatch(normalized) is None:
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "Enter only the numeric dotted version/build shown by the viewer's About screen.",
        )
    return normalized


def _is_valid_utc_timestamp(value: object) -> bool:
    if type(value) is not str or _UTC_TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _validate_system_record(system_record: Mapping[str, str]) -> dict[str, str]:
    environment = dict(system_record)
    platform_name = environment.get("platform")
    edition = environment.get("edition")
    release = environment.get("release")
    version_build = environment.get("version_build")
    architecture = environment.get("architecture")
    if (
        set(environment) != _WINDOWS_KEYS
        or platform_name != "Windows"
        or type(edition) is not str
        or edition not in _WINDOWS_EDITIONS
        or type(release) is not str
        or _WINDOWS_RELEASE.fullmatch(release) is None
        or type(version_build) is not str
        or _WINDOWS_BUILD.fullmatch(version_build) is None
        or type(architecture) is not str
        or architecture not in _WINDOWS_ARCHITECTURES
    ):
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "The closed Windows environment record is invalid.",
        )
    return {
        "platform": platform_name,
        "edition": edition,
        "release": release,
        "version_build": version_build,
        "architecture": architecture,
    }


def _validate_answers(answers: ReaderAnswers) -> None:
    if type(answers) is not ReaderAnswers or type(answers.search_matches) is not int:
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "The closed reader observations are invalid.",
        )
    boolean_answers = (
        answers.configuration_confirmed,
        answers.page_sequence_passed,
        answers.visual_zoom_sweep_passed,
        answers.noninteractive_passed,
        answers.close_reopen_passed,
    )
    if any(type(value) is not bool for value in boolean_answers):
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "The closed reader observations must use explicit yes or no values.",
        )
    if not 0 <= answers.search_matches <= 3:
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "The search result must be a count from zero through three.",
        )
    if any(type(page) is not int or not 1 <= page <= 5 for page in answers.affected_pages):
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "Affected page numbers must be unique values from one through five.",
        )
    if tuple(sorted(set(answers.affected_pages))) != answers.affected_pages:
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "Affected page numbers must be sorted and unique.",
        )
    if answers.passed and answers.affected_pages:
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "A passing result cannot contain affected pages.",
        )


def _result_payload(
    slot: ReaderSlot,
    viewer_version: str,
    viewer_mode: str,
    answers: ReaderAnswers,
    attempt: int,
    binding: ReaderArtifactBinding,
    *,
    tested_at_utc: str | None = None,
    system_record: Mapping[str, str] | None = None,
) -> dict[str, object]:
    definition = VIEWERS[slot]
    allowed_modes = {mode for mode, _label in definition.modes}
    if type(viewer_mode) is not str or viewer_mode not in allowed_modes:
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "The selected viewer mode does not belong to that matrix slot.",
        )
    _validate_answers(answers)
    timestamp = tested_at_utc or datetime.now(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    if not _is_valid_utc_timestamp(timestamp):
        raise ReaderTestError(
            "PWS0307_READER_INPUT_INVALID",
            "The reader-test UTC timestamp is invalid.",
        )
    environment = _validate_system_record(
        system_record if system_record is not None else _safe_system_record()
    )
    return {
        "schema_version": READER_RESULT_SCHEMA_VERSION,
        "protocol_version": READER_TEST_PROTOCOL_VERSION,
        "task_id": TASK_ID,
        "attempt": attempt,
        "tested_at_utc": timestamp,
        "operator_role": "human_operator",
        "viewer": {
            "slot": slot.value,
            "name": definition.name,
            "version_build": _validate_viewer_version(viewer_version),
            "mode": viewer_mode,
        },
        "windows": environment,
        "artifact": {
            "implementation_revision": binding.implementation_revision,
            "generator_version": GENERATOR_VERSION,
            "pdf_sha256": binding.pdf_sha256,
            "pdf_byte_size": binding.pdf_byte_size,
            "page_count": binding.page_count,
            "evidence_json_sha256": binding.evidence_sha256,
        },
        "checks": {
            "configuration_confirmed": answers.configuration_confirmed,
            "page_sequence_1_2_3_4_5_4_3_2_1": answers.page_sequence_passed,
            "all_pages_fit_100_200_400": answers.visual_zoom_sweep_passed,
            "page_1_search_matches": answers.search_matches,
            "all_overlay_regions_noninteractive": answers.noninteractive_passed,
            "close_reopen_fit_and_400": answers.close_reopen_passed,
        },
        "result": "pass" if answers.passed else "fail",
        "defect_categories": [defect.value for defect in answers.defects],
        "affected_pages": list(answers.affected_pages),
    }


def _result_path(workspace: Path, slot: ReaderSlot, attempt: int) -> Path:
    return workspace / f"pws-0307-{slot.value}-attempt-{attempt:02d}.json"


def record_reader_result(
    prepared: PreparedReaderTest,
    slot: ReaderSlot,
    viewer_version: str,
    viewer_mode: str,
    answers: ReaderAnswers,
    *,
    allowed_root: Path = PROJECT_ROOT,
    tested_at_utc: str | None = None,
    system_record: Mapping[str, str] | None = None,
) -> Path:
    """Reverify the artifact and exclusively record one human attempt."""

    verified = verify_approved_pair(
        prepared.workspace,
        allowed_root=allowed_root,
        binding=prepared.binding,
    )
    if verified.pdf_path != prepared.pdf_path or verified.evidence_path != prepared.evidence_path:
        raise ReaderTestError(
            "PWS0307_READER_ARTIFACT_CHANGED",
            "The approved reader-test artifact changed during the viewer run. Discard that observation.",
        )
    for attempt in range(1, MAXIMUM_RESULT_ATTEMPTS_PER_SLOT + 1):
        path = _result_path(verified.workspace, slot, attempt)
        if path.exists():
            continue
        payload = _result_payload(
            slot,
            viewer_version,
            viewer_mode,
            answers,
            attempt,
            prepared.binding,
            tested_at_utc=tested_at_utc,
            system_record=system_record,
        )
        encoded = (
            json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
        )
        _write_exclusive_text(path, encoded)
        return path
    raise ReaderTestError(
        "PWS0307_READER_RESULT_LIMIT",
        "The bounded result-attempt limit was reached; existing evidence was preserved.",
    )


def _load_result(path: Path, binding: ReaderArtifactBinding) -> dict[str, object]:
    try:
        payload = _read_bounded_file(path, MAXIMUM_READER_RESULT_BYTES)
        if not payload:
            raise OSError("reader result is empty")
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_result_keys,
        )
    except ReaderTestError:
        raise
    except OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateResultKeyError:
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        ) from None
    if type(decoded) is not dict:
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        )
    result = cast(dict[str, object], decoded)
    if set(result) != _RESULT_KEYS:
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        )
    viewer = result.get("viewer")
    artifact = result.get("artifact")
    checks = result.get("checks")
    defects = result.get("defect_categories")
    affected = result.get("affected_pages")
    windows = result.get("windows")
    result_state = result.get("result")
    slot_value = viewer.get("slot") if type(viewer) is dict else None
    windows_platform = windows.get("platform") if type(windows) is dict else None
    windows_edition = windows.get("edition") if type(windows) is dict else None
    windows_release = windows.get("release") if type(windows) is dict else None
    windows_build = windows.get("version_build") if type(windows) is dict else None
    windows_architecture = windows.get("architecture") if type(windows) is dict else None
    if (
        result.get("schema_version") != READER_RESULT_SCHEMA_VERSION
        or result.get("protocol_version") != READER_TEST_PROTOCOL_VERSION
        or result.get("task_id") != TASK_ID
        or result.get("operator_role") != "human_operator"
        or type(result.get("attempt")) is not int
        or not 1 <= cast(int, result.get("attempt")) <= MAXIMUM_RESULT_ATTEMPTS_PER_SLOT
        or not _is_valid_utc_timestamp(result.get("tested_at_utc"))
        or type(result_state) is not str
        or result_state not in ("pass", "fail")
        or type(viewer) is not dict
        or set(viewer) != _VIEWER_KEYS
        or type(slot_value) is not str
        or slot_value not in tuple(slot.value for slot in ReaderSlot)
        or type(viewer.get("version_build")) is not str
        or _VIEWER_VERSION.fullmatch(cast(str, viewer.get("version_build"))) is None
        or type(artifact) is not dict
        or set(artifact) != _ARTIFACT_KEYS
        or artifact.get("implementation_revision") != binding.implementation_revision
        or artifact.get("generator_version") != GENERATOR_VERSION
        or artifact.get("pdf_sha256") != binding.pdf_sha256
        or artifact.get("pdf_byte_size") != binding.pdf_byte_size
        or artifact.get("page_count") != binding.page_count
        or artifact.get("evidence_json_sha256") != binding.evidence_sha256
        or type(checks) is not dict
        or type(defects) is not list
        or type(affected) is not list
        or type(windows) is not dict
        or set(windows) != _WINDOWS_KEYS
        or windows_platform != "Windows"
        or type(windows_edition) is not str
        or windows_edition not in _WINDOWS_EDITIONS
        or type(windows_release) is not str
        or _WINDOWS_RELEASE.fullmatch(windows_release) is None
        or type(windows_build) is not str
        or _WINDOWS_BUILD.fullmatch(windows_build) is None
        or type(windows_architecture) is not str
        or windows_architecture not in _WINDOWS_ARCHITECTURES
    ):
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        )
    slot = ReaderSlot(cast(str, viewer["slot"]))
    definition = VIEWERS[slot]
    viewer_mode = viewer.get("mode")
    if (
        viewer.get("name") != definition.name
        or type(viewer_mode) is not str
        or viewer_mode not in tuple(mode for mode, _label in definition.modes)
    ):
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        )
    expected_check_names = {
        "configuration_confirmed",
        "page_sequence_1_2_3_4_5_4_3_2_1",
        "all_pages_fit_100_200_400",
        "page_1_search_matches",
        "all_overlay_regions_noninteractive",
        "close_reopen_fit_and_400",
    }
    if set(checks) != expected_check_names:
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        )
    answer_values = (
        checks["configuration_confirmed"],
        checks["page_sequence_1_2_3_4_5_4_3_2_1"],
        checks["all_pages_fit_100_200_400"],
        checks["all_overlay_regions_noninteractive"],
        checks["close_reopen_fit_and_400"],
    )
    if any(type(value) is not bool for value in answer_values):
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        )
    search_matches = checks["page_1_search_matches"]
    if type(search_matches) is not int or not 0 <= search_matches <= 3:
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        )
    if any(type(page) is not int for page in affected):
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        )
    answers = ReaderAnswers(
        configuration_confirmed=cast(bool, checks["configuration_confirmed"]),
        page_sequence_passed=cast(bool, checks["page_sequence_1_2_3_4_5_4_3_2_1"]),
        visual_zoom_sweep_passed=cast(bool, checks["all_pages_fit_100_200_400"]),
        search_matches=search_matches,
        noninteractive_passed=cast(bool, checks["all_overlay_regions_noninteractive"]),
        close_reopen_passed=cast(bool, checks["close_reopen_fit_and_400"]),
        affected_pages=tuple(cast(list[int], affected)),
    )
    try:
        _validate_answers(answers)
    except ReaderTestError:
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        ) from None
    if result["result"] != ("pass" if answers.passed else "fail") or defects != [
        defect.value for defect in answers.defects
    ]:
        raise ReaderTestError(
            "PWS0307_READER_RESULT_INVALID",
            "A saved reader result is invalid; no matrix status was derived.",
        )
    return result


def reader_status(
    prepared: PreparedReaderTest, *, allowed_root: Path = PROJECT_ROOT
) -> dict[ReaderSlot, dict[str, object]]:
    """Load the latest validated attempt for each remaining slot."""

    verify_approved_pair(
        prepared.workspace,
        allowed_root=allowed_root,
        binding=prepared.binding,
    )
    latest: dict[ReaderSlot, dict[str, object]] = {}
    for slot in ReaderSlot:
        for attempt in range(1, MAXIMUM_RESULT_ATTEMPTS_PER_SLOT + 1):
            path = _result_path(prepared.workspace, slot, attempt)
            if not path.exists():
                continue
            result = _load_result(path, prepared.binding)
            if result.get("attempt") != attempt:
                raise ReaderTestError(
                    "PWS0307_READER_RESULT_INVALID",
                    "A saved reader result is invalid; no matrix status was derived.",
                )
            viewer = cast(dict[str, object], result["viewer"])
            if viewer.get("slot") != slot.value:
                raise ReaderTestError(
                    "PWS0307_READER_RESULT_INVALID",
                    "A saved reader result is invalid; no matrix status was derived.",
                )
            latest[slot] = result
    return latest


def _print_ready(prepared: PreparedReaderTest, output: TextIO) -> None:
    action = "reused and reverified" if prepared.reused else "generated and verified"
    print("READY [PWS0307_READER_TEST_READY]", file=output)
    print(f"The exact W2-bound pair was {action}.", file=output)
    print("Open this exact PDF (do not type or invent another path):", file=output)
    print(str(prepared.pdf_path), file=output)
    print(f"PDF SHA-256: {prepared.binding.pdf_sha256}", file=output)
    print(
        f"PDF bytes/pages: {prepared.binding.pdf_byte_size} / {prepared.binding.page_count}",
        file=output,
    )
    print(f"Test card: {prepared.test_card_path}", file=output)
    print("W2 Edge is already recorded; complete only W1, W3, and W4.", file=output)


def _print_status(prepared: PreparedReaderTest, output: TextIO, *, allowed_root: Path) -> bool:
    latest = reader_status(prepared, allowed_root=allowed_root)
    print("\nPWS-0307 reader status", file=output)
    for slot in ReaderSlot:
        result = latest.get(slot)
        if result is None:
            print(f"  {slot.value}: NOT RUN", file=output)
            continue
        viewer = cast(dict[str, object], result["viewer"])
        attempt = cast(int, result["attempt"])
        state = cast(str, result["result"]).upper()
        print(
            f"  {slot.value}: {state} - attempt {attempt:02d} - {viewer['version_build']}",
            file=output,
        )
    print("  W2: PASS - qualifying tracked Edge run (protocol 1 superset)", file=output)
    ready = all(latest.get(slot, {}).get("result") == "pass" for slot in ReaderSlot)
    if ready:
        print("MATRIX READY FOR CODEX REVIEW: W1, W3, and W4 latest attempts pass.", file=output)
    else:
        print("MATRIX INCOMPLETE: every remaining slot needs a latest PASS attempt.", file=output)
    return ready


def _prompt_yes_no(prompt: str, input_fn: Callable[[str], str], output: TextIO) -> bool:
    while True:
        answer = input_fn(f"{prompt} [y/n]: ").strip().casefold()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please enter y or n.", file=output)


def _prompt_mode(
    definition: ViewerDefinition,
    input_fn: Callable[[str], str],
    output: TextIO,
) -> str | None:
    print(definition.mode_prompt + ":", file=output)
    for index, (_mode, label) in enumerate(definition.modes, start=1):
        print(f"  {index}. {label}", file=output)
    while True:
        raw = input_fn("Mode number (or cancel): ").strip()
        if raw.casefold() == "cancel":
            return None
        if raw.isdecimal():
            index = int(raw)
            if 1 <= index <= len(definition.modes):
                return definition.modes[index - 1][0]
        print("Choose one listed mode number, or enter cancel.", file=output)


def _prompt_viewer_version(
    input_fn: Callable[[str], str],
    output: TextIO,
) -> str | None:
    while True:
        raw = input_fn("Numeric dotted About version/build (or cancel): ").strip()
        if raw.casefold() == "cancel":
            return None
        try:
            return _validate_viewer_version(raw)
        except ReaderTestError as error:
            print(error.safe_message, file=output)


def _prompt_search_count(input_fn: Callable[[str], str], output: TextIO) -> int:
    while True:
        raw = input_fn("How many of the three P1 searches were found [0-3]? ").strip()
        if raw in {"0", "1", "2", "3"}:
            return int(raw)
        print("Enter 0, 1, 2, or 3.", file=output)


def _prompt_affected_pages(input_fn: Callable[[str], str], output: TextIO) -> tuple[int, ...]:
    while True:
        raw = input_fn("Affected page numbers, comma-separated; Enter if none: ").strip()
        if not raw:
            return ()
        try:
            pages = tuple(sorted({int(part.strip()) for part in raw.split(",")}))
        except ValueError:
            pages = ()
        if pages and all(1 <= page <= 5 for page in pages):
            return pages
        print("Use only unique page numbers 1 through 5, such as 2,4.", file=output)


def _collect_answers(
    definition: ViewerDefinition,
    input_fn: Callable[[str], str],
    output: TextIO,
) -> tuple[str, str, ReaderAnswers] | None:
    version = _prompt_viewer_version(input_fn, output)
    if version is None:
        return None
    mode = _prompt_mode(definition, input_fn, output)
    if mode is None:
        return None
    configuration = _prompt_yes_no(
        "Did the named desktop/built-in viewer open the exact local PDF",
        input_fn,
        output,
    )
    sequence = _prompt_yes_no(
        "Did five pages and the 1>2>3>4>5>4>3>2>1 sequence pass",
        input_fn,
        output,
    )
    visuals = _prompt_yes_no(
        "Did every page pass its expected visual check at Fit, 100%, 200%, and 400%",
        input_fn,
        output,
    )
    searches = _prompt_search_count(input_fn, output)
    noninteractive = _prompt_yes_no(
        "Did all overlay clicks and Tab traversal remain noninteractive",
        input_fn,
        output,
    )
    reopen = _prompt_yes_no(
        "Did close/reopen and the five-page Fit/400% recheck pass",
        input_fn,
        output,
    )
    provisional = ReaderAnswers(
        configuration_confirmed=configuration,
        page_sequence_passed=sequence,
        visual_zoom_sweep_passed=visuals,
        search_matches=searches,
        noninteractive_passed=noninteractive,
        close_reopen_passed=reopen,
    )
    affected = () if provisional.passed else _prompt_affected_pages(input_fn, output)
    return (
        version,
        mode,
        ReaderAnswers(
            configuration_confirmed=configuration,
            page_sequence_passed=sequence,
            visual_zoom_sweep_passed=visuals,
            search_matches=searches,
            noninteractive_passed=noninteractive,
            close_reopen_passed=reopen,
            affected_pages=affected,
        ),
    )


def _record_interactively(
    prepared: PreparedReaderTest,
    slot: ReaderSlot,
    input_fn: Callable[[str], str],
    output: TextIO,
    *,
    allowed_root: Path,
) -> None:
    verify_approved_pair(
        prepared.workspace,
        allowed_root=allowed_root,
        binding=prepared.binding,
    )
    definition = VIEWERS[slot]
    print(f"\n{slot.value} - {definition.name}", file=output)
    print(_test_card(prepared.binding), file=output)
    confirmation = input_fn(
        "Complete the viewer run, then press Enter to record it; type cancel to return: "
    ).strip()
    if confirmation.casefold() == "cancel":
        print("No result was recorded.", file=output)
        return
    collected = _collect_answers(definition, input_fn, output)
    if collected is None:
        print("No result was recorded. Choose the slot again when ready.", file=output)
        return
    version, mode, answers = collected
    result_path = record_reader_result(
        prepared,
        slot,
        version,
        mode,
        answers,
        allowed_root=allowed_root,
    )
    state = "PASS" if answers.passed else "FAIL"
    print(f"Recorded {slot.value} {state}: {result_path}", file=output)
    if answers.defects:
        print(
            "Closed defects: " + ", ".join(defect.value for defect in answers.defects),
            file=output,
        )


def interactive_session(
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    workspace: Path = DEFAULT_WORKSPACE,
    allowed_root: Path = PROJECT_ROOT,
) -> int:
    """Run the restartable keyboard-only preparation and recording menu."""

    prepared = prepare_reader_test(workspace, allowed_root=allowed_root)
    _print_ready(prepared, output)
    while True:
        _print_status(prepared, output, allowed_root=allowed_root)
        print("\nChoose a remaining viewer, S for status, or Q to stop:", file=output)
        print("  1. W1 Adobe Acrobat Reader/free-reduced mode", file=output)
        print("  3. W3 Google Chrome built-in viewer", file=output)
        print("  4. W4 Mozilla Firefox built-in viewer", file=output)
        choice = input_fn("Choice [1/3/4/S/Q]: ").strip().casefold()
        if choice == "q":
            print(
                "Reader-test evidence was preserved. Rerun the same command to continue.",
                file=output,
            )
            return 0
        if choice == "s":
            continue
        selected = {"1": ReaderSlot.W1, "3": ReaderSlot.W3, "4": ReaderSlot.W4}.get(choice)
        if selected is None:
            print("Choose 1, 3, 4, S, or Q.", file=output)
            continue
        _record_interactively(
            prepared,
            selected,
            input_fn,
            output,
            allowed_root=allowed_root,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("prepare", help="prepare and verify the exact approved reader-test pair")
    commands.add_parser("status", help="show saved reader-result status")
    record = commands.add_parser("record", help="guide and record one remaining reader slot")
    record.add_argument("slot", choices=[slot.value for slot in ReaderSlot])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command is None:
            return interactive_session()
        prepared = prepare_reader_test()
        if arguments.command == "prepare":
            _print_ready(prepared, sys.stdout)
            return 0
        if arguments.command == "status":
            _print_ready(prepared, sys.stdout)
            _print_status(prepared, sys.stdout, allowed_root=PROJECT_ROOT)
            return 0
        slot = ReaderSlot(arguments.slot)
        _record_interactively(prepared, slot, input, sys.stdout, allowed_root=PROJECT_ROOT)
        return 0
    except EOFError, KeyboardInterrupt:
        print("\nReader-test input stopped; existing evidence was preserved.", file=sys.stderr)
        return 130
    except (ReaderTestError, ReaderCorpusError) as error:
        print(f"ERROR [{error.code}] {error.safe_message}", file=sys.stderr)
        return 2
    except FlattenedPdfExportError as error:
        print(f"ERROR [{error.code.value}] {error.safe_message}", file=sys.stderr)
        return 2
    except OSError:
        print(
            "ERROR [PWS0307_READER_IO_FAILED] "
            "The local synthetic reader-test evidence could not be written safely.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
