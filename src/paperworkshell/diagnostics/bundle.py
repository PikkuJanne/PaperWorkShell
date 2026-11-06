"""Closed, deterministic support-bundle bytes and the exact pre-export preview.

This module has no case, log-path, environment, document or network collector.
Only the established typed diagnostic summary can cross this boundary.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from io import BytesIO
from typing import Final
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from paperworkshell.diagnostics.errors import (
    DiagnosticErrorCode,
    DiagnosticErrorContext,
    reject_diagnostic,
)
from paperworkshell.diagnostics.models import MAX_DIAGNOSTIC_RECORDS
from paperworkshell.diagnostics.summary import (
    DiagnosticSummary,
    copy_diagnostic_summary,
    render_diagnostic_summary,
)

BUNDLE_VERSION: Final = 1
BUNDLE_FILENAMES: Final = ("summary.txt", "allowlist.txt")
# A format bound derived from the existing maximum summary identifier count.
# The writer additionally enforces the selected central ResourcePolicy.
MAX_BUNDLE_BYTES: Final = MAX_DIAGNOSTIC_RECORDS * 128
ALLOWLIST_TEXT: Final = """PaperWorkShell support bundle format 1
Files: summary.txt; allowlist.txt. No other archive members are permitted.
summary.txt: generated UTC; summary version; record/dropped/sink-failure counts;
numeric application/engine/schema versions; internal operation UUIDs;
closed diagnostic counts, failure codes and numeric resource measurements.
allowlist.txt: this fixed description of the included fields and exclusions.
No document, OCR or form text; names; addresses; original filenames; full paths;
metadata values; thumbnails; signature images; document bytes; raw exceptions;
environment variables; machine/user identity; arbitrary files or ordinary logs.
Missing measurements are not collected or inferred. A zero-record summary is valid.
This local export sends nothing. Share it separately only if you choose.
Saved bundles remain in the chosen folder until you delete them; case cleanup
and application removal do not remove them. Deletion does not promise secure erasure.
PaperWorkShell support evidence is independent of any other product's support data.
"""


def _reject() -> None:
    reject_diagnostic(DiagnosticErrorCode.BUNDLE_INVALID, DiagnosticErrorContext.BUNDLE)


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticBundleFile:
    """One fixed-name, immutable member; no external filename or source path."""

    name: str
    content: bytes = field(repr=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def __repr__(self) -> str:
        return "DiagnosticBundleFile()"


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticBundlePreview:
    """Freeze validated safe state once; saving never recollects runtime state."""

    summary: DiagnosticSummary = field(repr=False)
    files: tuple[DiagnosticBundleFile, ...] = field(init=False, repr=False)
    archive: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        summary = copy_diagnostic_summary(self.summary)
        object.__setattr__(self, "summary", summary)
        members = (
            DiagnosticBundleFile("summary.txt", render_diagnostic_summary(summary).encode("utf-8")),
            DiagnosticBundleFile("allowlist.txt", ALLOWLIST_TEXT.encode("utf-8")),
        )
        object.__setattr__(self, "files", members)
        target = BytesIO()
        with ZipFile(target, "w", compression=ZIP_STORED) as archive:
            for member in members:
                entry = ZipInfo(member.name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100600 << 16
                archive.writestr(entry, member.content)
        payload = target.getvalue()
        if len(payload) > MAX_BUNDLE_BYTES:
            _reject()
        object.__setattr__(self, "archive", payload)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.archive).hexdigest()

    def validate(self) -> None:
        """Reject mutation/forgery before showing or publishing any member bytes."""

        expected = DiagnosticBundlePreview(copy_diagnostic_summary(self.summary))
        if (
            type(self.files) is not tuple
            or len(self.files) != len(expected.files)
            or any(type(member) is not DiagnosticBundleFile for member in self.files)
            or self.files != expected.files
            or type(self.archive) is not bytes
            or self.archive != expected.archive
        ):
            _reject()

    def render(self) -> str:
        """Show exact members, hashes, sizes, categories and complete safe contents."""

        self.validate()
        rows = [
            "PaperWorkShell support bundle preview",
            "Nothing has been saved or sent. Save creates only this reviewed local ZIP.",
            f"Bundle format: {BUNDLE_VERSION}",
            f"ZIP bytes: {len(self.archive)}",
            f"ZIP SHA-256: {self.sha256}",
            "Exact included files:",
        ]
        for member in self.files:
            rows.extend(
                (
                    f"- {member.name}: {len(member.content)} bytes; SHA-256 {member.sha256}",
                    f"Contents of {member.name}:",
                    member.content.decode("utf-8"),
                )
            )
        return "\n".join(rows)

    def __repr__(self) -> str:
        return "DiagnosticBundlePreview()"
