"""Application coordination for local, checksum-admitted OCR languages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from types import MappingProxyType
from typing import Final, Protocol

from paperworkshell.domain.ocr import OcrError, OcrErrorCode, reject_ocr
from paperworkshell.domain.ocr_languages import (
    DEFAULT_OCR_LANGUAGE_SET_ID,
    resolve_ocr_language_set,
    select_ocr_language_set,
)

SUPPORTED_OCR_LANGUAGE_CODES: Final = ("eng", "deu")
OCR_LANGUAGE_DISPLAY_NAMES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "eng": "English",
        "deu": "German",
    }
)


class OcrLanguageRuntimeStatus(StrEnum):
    """Closed local discovery states safe for routine presentation."""

    UNCHECKED = "unchecked"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True, repr=False)
class OcrLanguageDiscoveryResult:
    """Path-free installed-language facts returned by one local adapter."""

    runtime_status: OcrLanguageRuntimeStatus
    installed_language_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.runtime_status) is not OcrLanguageRuntimeStatus
            or type(self.installed_language_codes) is not tuple
            or len(set(self.installed_language_codes)) != len(self.installed_language_codes)
            or any(
                type(code) is not str or code not in SUPPORTED_OCR_LANGUAGE_CODES
                for code in self.installed_language_codes
            )
            or self.installed_language_codes
            != tuple(
                code
                for code in SUPPORTED_OCR_LANGUAGE_CODES
                if code in self.installed_language_codes
            )
            or (
                self.runtime_status is not OcrLanguageRuntimeStatus.AVAILABLE
                and bool(self.installed_language_codes)
            )
            or (
                self.runtime_status is OcrLanguageRuntimeStatus.AVAILABLE
                and "eng" not in self.installed_language_codes
            )
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_discovery")

    def __repr__(self) -> str:
        return (
            "OcrLanguageDiscoveryResult("
            f"runtime_status={self.runtime_status!r}, "
            f"installed_language_count={len(self.installed_language_codes)})"
        )


class OcrLanguageDiscovery(Protocol):
    """Discover only authenticated local runtime and language-pack facts."""

    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        """Return a bounded path-free result without using the network."""


@dataclass(frozen=True, slots=True)
class OcrLanguageOption:
    """One supported language and its current local installation state."""

    code: str
    display_name: str
    installed: bool

    def __post_init__(self) -> None:
        if (
            type(self.code) is not str
            or self.code not in SUPPORTED_OCR_LANGUAGE_CODES
            or type(self.display_name) is not str
            or self.display_name != OCR_LANGUAGE_DISPLAY_NAMES[self.code]
            or type(self.installed) is not bool
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_option")


@dataclass(frozen=True, slots=True, repr=False)
class OcrLanguageSnapshot:
    """Complete path-free language-management state for UI and preflight."""

    runtime_status: OcrLanguageRuntimeStatus
    language_options: tuple[OcrLanguageOption, ...]
    selected_language_set_id: str
    selected_language_codes: tuple[str, ...]
    selected_available: bool

    def __post_init__(self) -> None:
        if (
            type(self.runtime_status) is not OcrLanguageRuntimeStatus
            or type(self.language_options) is not tuple
            or any(type(option) is not OcrLanguageOption for option in self.language_options)
            or tuple(option.code for option in self.language_options)
            != SUPPORTED_OCR_LANGUAGE_CODES
            or type(self.selected_language_set_id) is not str
            or type(self.selected_language_codes) is not tuple
            or type(self.selected_available) is not bool
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_snapshot")
        try:
            resolved = resolve_ocr_language_set(self.selected_language_set_id)
        except OcrError:
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_snapshot")
        installed = frozenset(option.code for option in self.language_options if option.installed)
        expected_available = self.runtime_status is OcrLanguageRuntimeStatus.AVAILABLE and set(
            resolved
        ).issubset(installed)
        if (
            self.selected_language_codes != resolved
            or self.selected_available is not expected_available
            or (self.runtime_status is not OcrLanguageRuntimeStatus.AVAILABLE and bool(installed))
            or (
                self.runtime_status is OcrLanguageRuntimeStatus.AVAILABLE and "eng" not in installed
            )
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_snapshot")

    @property
    def installed_language_codes(self) -> frozenset[str]:
        """Return the installed code set without exposing adapter storage."""

        return frozenset(option.code for option in self.language_options if option.installed)

    @property
    def missing_selected_language_codes(self) -> tuple[str, ...]:
        """Return selected supported languages that are not currently admitted."""

        installed = self.installed_language_codes
        return tuple(code for code in self.selected_language_codes if code not in installed)

    def __repr__(self) -> str:
        return (
            "OcrLanguageSnapshot("
            f"runtime_status={self.runtime_status!r}, "
            f"installed_language_count={len(self.installed_language_codes)}, "
            f"selected_language_count={len(self.selected_language_codes)}, "
            f"selected_available={self.selected_available})"
        )


class _UnavailableOcrLanguageDiscovery:
    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        return OcrLanguageDiscoveryResult(OcrLanguageRuntimeStatus.UNAVAILABLE, ())


class OcrLanguageManager:
    """Lazily discover and select only closed, local OCR language sets."""

    __slots__ = ("_discovery", "_lock", "_runtime_authority_id", "_snapshot")

    def __init__(
        self,
        discovery: OcrLanguageDiscovery,
        *,
        runtime_authority_id: str | None = None,
    ) -> None:
        if not callable(getattr(discovery, "discover_ocr_languages", None)) or (
            runtime_authority_id is not None
            and (
                type(runtime_authority_id) is not str
                or len(runtime_authority_id) != 64
                or any(character not in "0123456789abcdef" for character in runtime_authority_id)
            )
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_manager")
        self._discovery = discovery
        self._runtime_authority_id = runtime_authority_id
        self._lock = RLock()
        self._snapshot = _build_snapshot(
            OcrLanguageRuntimeStatus.UNCHECKED,
            (),
            DEFAULT_OCR_LANGUAGE_SET_ID,
        )

    @property
    def snapshot(self) -> OcrLanguageSnapshot:
        """Return the current immutable process-local selection snapshot."""

        with self._lock:
            return self._snapshot

    @property
    def runtime_authority_id(self) -> str | None:
        """Return the path-free production binding, when this manager has one."""

        return self._runtime_authority_id

    def refresh(self) -> OcrLanguageSnapshot:
        """Refresh authenticated local facts without changing the selected set."""

        with self._lock:
            try:
                result = self._discovery.discover_ocr_languages()
            except OcrError as error:
                if type(error) is not OcrError:
                    reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.language_discovery")
                reject_ocr(error.code, error.context)
            except BaseException:
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.language_discovery")
            if type(result) is not OcrLanguageDiscoveryResult:
                reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.language_discovery")
            self._snapshot = _build_snapshot(
                result.runtime_status,
                result.installed_language_codes,
                self._snapshot.selected_language_set_id,
            )
            return self._snapshot

    def select_language_codes(self, language_codes: tuple[str, ...]) -> OcrLanguageSnapshot:
        """Select an exact canonical set only when every language is available."""

        with self._lock:
            language_set_id = select_ocr_language_set(language_codes)
            canonical_codes = resolve_ocr_language_set(language_set_id)
            current = self.refresh()
            if current.runtime_status is not OcrLanguageRuntimeStatus.AVAILABLE or not set(
                canonical_codes
            ).issubset(current.installed_language_codes):
                reject_ocr(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.language_selection")
            self._snapshot = _build_snapshot(
                current.runtime_status,
                tuple(
                    code
                    for code in SUPPORTED_OCR_LANGUAGE_CODES
                    if code in current.installed_language_codes
                ),
                language_set_id,
            )
            if self._snapshot.selected_language_codes != canonical_codes:
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.language_selection")
            return self._snapshot

    def require_available(self, language_set_id: str) -> None:
        """Fail before OCR when the exact closed set is not locally available."""

        with self._lock:
            self._require_available_locked(language_set_id)

    def require_selected_available(self) -> str:
        """Atomically preflight and return the exact process-session selection."""

        with self._lock:
            language_set_id = self._snapshot.selected_language_set_id
            self._require_available_locked(language_set_id)
            return language_set_id

    def _require_available_locked(self, language_set_id: str) -> None:
        required_codes = resolve_ocr_language_set(language_set_id)
        # Re-admit the captured local runtime for every operation.  A prior UI
        # snapshot is presentation state, not authority after pack replacement
        # or removal.
        snapshot = self.refresh()
        if snapshot.runtime_status is not OcrLanguageRuntimeStatus.AVAILABLE or not set(
            required_codes
        ).issubset(snapshot.installed_language_codes):
            reject_ocr(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.language_preflight")

    def __repr__(self) -> str:
        snapshot = self.snapshot
        return (
            "OcrLanguageManager("
            f"runtime_status={snapshot.runtime_status!r}, "
            f"installed_language_count={len(snapshot.installed_language_codes)}, "
            f"selected_available={snapshot.selected_available})"
        )


def unavailable_ocr_language_manager() -> OcrLanguageManager:
    """Return one lazy manager whose local runtime is safely unavailable."""

    return OcrLanguageManager(_UnavailableOcrLanguageDiscovery())


def _build_snapshot(
    runtime_status: OcrLanguageRuntimeStatus,
    installed_language_codes: tuple[str, ...],
    selected_language_set_id: str,
) -> OcrLanguageSnapshot:
    if (
        type(runtime_status) is not OcrLanguageRuntimeStatus
        or type(installed_language_codes) is not tuple
        or len(set(installed_language_codes)) != len(installed_language_codes)
        or any(code not in SUPPORTED_OCR_LANGUAGE_CODES for code in installed_language_codes)
        or installed_language_codes
        != tuple(code for code in SUPPORTED_OCR_LANGUAGE_CODES if code in installed_language_codes)
        or (
            runtime_status is not OcrLanguageRuntimeStatus.AVAILABLE
            and bool(installed_language_codes)
        )
        or (
            runtime_status is OcrLanguageRuntimeStatus.AVAILABLE
            and "eng" not in installed_language_codes
        )
    ):
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.language_discovery")
    selected_language_codes = resolve_ocr_language_set(selected_language_set_id)
    installed = frozenset(installed_language_codes)
    return OcrLanguageSnapshot(
        runtime_status=runtime_status,
        language_options=tuple(
            OcrLanguageOption(
                code,
                OCR_LANGUAGE_DISPLAY_NAMES[code],
                code in installed,
            )
            for code in SUPPORTED_OCR_LANGUAGE_CODES
        ),
        selected_language_set_id=selected_language_set_id,
        selected_language_codes=selected_language_codes,
        selected_available=(
            runtime_status is OcrLanguageRuntimeStatus.AVAILABLE
            and set(selected_language_codes).issubset(installed)
        ),
    )
