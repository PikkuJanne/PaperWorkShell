"""Quiet qualification accepts only the exact, independently bound Microsoft component terms."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_qualify_windows_installer import _candidate, _write
from tools import qualify_windows_installer as qualifier
from tools.release_artifacts import file_hashes
from tools.release_staging import inventory_files


def _complete_candidate(root: Path) -> tuple[dict[str, Any], Path, Path]:
    _legacy, installer, receipt = _candidate(root)
    base = receipt.parent
    terms = "notices/Microsoft-runtime-recipient-terms.txt"
    overlay = "evidence/microsoft-runtime/overlay-receipt.json"
    terms_hash = _write(base / "payload" / terms, b"Synthetic component-only reviewed terms")
    overlay_hash = _write(base / "payload" / overlay, b'{"synthetic":"acquisition-authority"}\n')
    marker = "runtime/paperworkshell-installed-v1.json"
    inventory = {
        "candidate": {"id": "test-internal"},
        "files": inventory_files(
            base / "payload", {name: "synthetic" for name in (marker, terms, overlay)}
        ),
    }
    inventory_hash = _write(base / "resolved-inventory.json", json.dumps(inventory).encode())
    data = json.loads(receipt.read_bytes())
    data.update(
        inventory_sha256=inventory_hash,
        microsoft_runtime_delivery="app-local-community2022",
        microsoft_recipient_terms_sha256=terms_hash,
        microsoft_overlay_receipt_sha256=overlay_hash,
    )
    receipt_hash = _write(receipt, json.dumps(data).encode())
    return (
        qualifier.inspect_candidate(
            installer,
            receipt,
            expected_installer_sha256=file_hashes(installer)[1],
            expected_receipt_sha256=receipt_hash,
            root=root,
        ),
        installer,
        receipt,
    )


def test_complete_candidate_binds_terms_and_overlay_to_independent_receipt_and_payload(
    tmp_path: Path,
) -> None:
    candidate, _installer, _receipt = _complete_candidate(tmp_path)
    assert candidate["microsoft_runtime_delivery"] == "app-local-community2022"
    assert qualifier.quiet_arguments(candidate) == (
        *qualifier.QUIET_ARGUMENTS,
        "/ACCEPTMICROSOFTRUNTIME=" + candidate["microsoft_recipient_terms_sha256"],
    )
    assert (
        len(
            [
                arg
                for arg in qualifier.quiet_arguments(candidate)
                if arg.startswith("/ACCEPTMICROSOFTRUNTIME=")
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("microsoft_runtime_delivery", "unknown"),
        ("microsoft_runtime_delivery", None),
        ("microsoft_recipient_terms_sha256", "0" * 63),
        ("microsoft_recipient_terms_sha256", "A" * 64),
        ("microsoft_overlay_receipt_sha256", None),
        ("microsoft_overlay_receipt_sha256", True),
    ],
)
def test_malformed_or_unknown_receipt_authority_is_refused(
    tmp_path: Path, field: str, value: Any
) -> None:
    candidate, installer, receipt = _complete_candidate(tmp_path)
    data = json.loads(receipt.read_bytes())
    data[field] = value
    changed = _write(receipt, json.dumps(data).encode())
    with pytest.raises(qualifier.QualificationError, match="MICROSOFT_AUTHORITY"):
        qualifier.inspect_candidate(
            installer,
            receipt,
            expected_installer_sha256=candidate["installer_sha256"],
            expected_receipt_sha256=changed,
            root=tmp_path,
        )


@pytest.mark.parametrize("mutation", ["terms", "overlay", "receipt", "duplicate"])
def test_quiet_arguments_cannot_invent_acceptance_without_exact_authority(
    tmp_path: Path, mutation: str
) -> None:
    candidate, _installer, _receipt = _complete_candidate(tmp_path)
    if mutation in {"terms", "overlay"}:
        candidate[
            "microsoft_recipient_terms_sha256"
            if mutation == "terms"
            else "microsoft_overlay_receipt_sha256"
        ] = "0" * 64
    elif mutation == "receipt":
        candidate.pop("build_receipt_sha256")
    else:
        candidate["files"].append(
            dict(
                next(
                    record
                    for record in candidate["files"]
                    if record["path"].endswith("recipient-terms.txt")
                )
            )
        )
    with pytest.raises(qualifier.QualificationError, match="MICROSOFT_AUTHORITY"):
        qualifier.quiet_arguments(candidate)


@pytest.mark.parametrize(
    "legacy",
    [
        {},
        {
            "microsoft_runtime_delivery": "external-prerequisite-pending-entitlement",
            "microsoft_recipient_terms_sha256": None,
            "microsoft_overlay_receipt_sha256": None,
        },
    ],
)
def test_historical_prerequisite_candidate_does_not_add_runtime_acceptance(
    legacy: dict[str, Any],
) -> None:
    assert qualifier.quiet_arguments(legacy) == qualifier.QUIET_ARGUMENTS
