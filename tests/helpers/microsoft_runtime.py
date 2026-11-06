"""Synthetic receipt/PE seams only; never licenses or stages Microsoft bytes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tools import prepare_windows_microsoft_runtime as tool


def encoded(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, review_version: int = 1
) -> tool.Inputs:
    """Every positive gate in these tests is an explicit synthetic stand-in."""

    monkeypatch.setattr(tool, "WORKSPACE_ROOT", tmp_path)

    @contextmanager
    def no_native_pin(_paths: object) -> Iterator[None]:
        yield

    monkeypatch.setattr(tool, "_pin_directories", no_native_pin)
    inspection = tmp_path / "inspection"
    inspection.mkdir()
    members = []
    for index, member in enumerate(tool.MEMBERS):
        image = bytearray(128)
        image[:2] = b"MZ"
        image[60:64] = (64).to_bytes(4, "little")
        image[64:70] = b"PE\0\0\x64\x86"
        image[-1] = index
        payload = bytes(image)
        target = inspection / member.source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        members.append(replace(member, size=len(payload), sha256=tool._digest(payload)))
    monkeypatch.setattr(tool, "MEMBERS", tuple(members))
    containers = []
    for index, source in enumerate(tool.SOURCE_CONTAINERS):
        payload = f"synthetic container {index}".encode()
        target = inspection / source.source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        containers.append(replace(source, size=len(payload), sha256=tool._digest(payload)))
    monkeypatch.setattr(tool, "SOURCE_CONTAINERS", tuple(containers))
    reference = tmp_path / "reference.bin"
    reference.write_bytes(b"synthetic reference, not Microsoft software")
    monkeypatch.setattr(tool, "REFERENCE_SIZE", reference.stat().st_size)
    monkeypatch.setattr(tool, "REFERENCE_SHA256", tool._digest(reference.read_bytes()))
    (tmp_path / "output").mkdir()
    owner = {
        "schema_version": 1,
        "kind": "owner-acceptance",
        "owner": "Janne Vuorela",
        "accepted_as": "individual-developer",
        "agreement_url": tool.AGREEMENT_URL,
        "agreement_sha256": tool.AGREEMENT_SHA256,
        "accepted": True,
        "agent_acquisition_authorized": True,
        "evidence_reference": "synthetic-test-only",
    }
    owner_path = tmp_path / "owner.json"
    owner_path.write_bytes(encoded(owner))
    copy_root = tmp_path / "synthetic-product-copy"
    copy_root.mkdir()
    artifacts = []
    for index, role in enumerate(
        (
            "bootstrapper",
            "product-catalog",
            "layout-manifest",
            "product-package",
            "redistributable-package",
        )
    ):
        name = f"synthetic-{index}.bin"
        payload = ("synthetic " + role).encode()
        (copy_root / name).write_bytes(payload)
        artifacts.append(
            {
                "path": name,
                "role": role,
                "size_bytes": len(payload),
                "sha256": tool._digest(payload),
            }
        )
    copy = {
        "schema_version": 1,
        "kind": "community-product-copy",
        "status": "complete",
        "product_id": tool.PRODUCT_ID,
        "product_version": "17.14.99",
        "channel": "VisualStudio.17.Release",
        "component_id": tool.COMPONENT_ID,
        "agreement_sha256": tool.AGREEMENT_SHA256,
        "owner_acceptance_sha256": tool._digest(encoded(owner)),
        "licensed_copy_review": "approved-valid-community-copy",
        "artifacts": artifacts,
    }
    copy_path = tmp_path / "copy.json"
    copy_path.write_bytes(encoded(copy))
    terms = tmp_path / "synthetic-terms.txt"
    terms.write_bytes(b"Synthetic test text only. No legal agreement or grant.")
    review = {
        "schema_version": 1,
        "kind": "recipient-terms-review",
        "status": "approved",
        "agreement_sha256": tool.AGREEMENT_SHA256,
        "community_copy_receipt_sha256": tool._digest(encoded(copy)),
        "first_party_license": "MIT",
        "microsoft_scope": "component-only",
        "qt_lgpl_rights_preserved": True,
        "runtime_version": tool.VERSION,
        "runtime_architecture": "AMD64",
        "member_sha256": {member.name: member.sha256 for member in tool.MEMBERS},
        "terms_sha256": tool._digest(terms.read_bytes()),
        "pikepdf_alias_disposition": "unchanged-existing-downstream-route",
    }
    if review_version == 2:
        review.update(
            schema_version=2,
            pikepdf_alias_disposition=tool.COMMUNITY_ALIAS_DISPOSITION,
            pikepdf_alias_source={
                "path": tool.PIKEPDF_ALIAS,
                "canonical_member": "msvcp140.dll",
                "sha256": tool.MEMBERS[0].sha256,
                "filename_alias_only": True,
                "historical_notices_preserved": True,
            },
            qpdf_app_local_members=tool._qpdf_members(),
        )
    review_path = tmp_path / "review.json"
    review_path.write_bytes(encoded(review))
    return tool.Inputs(
        inspection,
        reference,
        tmp_path / "output/overlay",
        owner_path,
        tool._digest(encoded(owner)),
        copy_path,
        tool._digest(encoded(copy)),
        copy_root,
        review_path,
        tool._digest(encoded(review)),
        terms,
    )


def change_receipt(inputs: tool.Inputs, attribute: str, key: str, value: object) -> tool.Inputs:
    path = getattr(inputs, attribute)
    assert isinstance(path, Path)
    document = json.loads(path.read_bytes())
    document[key] = value
    payload = encoded(document)
    path.write_bytes(payload)
    digest = tool._digest(payload)
    if attribute == "owner_acceptance":
        return replace(inputs, owner_acceptance_sha256=digest)
    if attribute == "community_copy_receipt":
        return replace(inputs, community_copy_receipt_sha256=digest)
    if attribute == "recipient_terms_review":
        return replace(inputs, recipient_terms_review_sha256=digest)
    raise AssertionError("unknown synthetic receipt seam")
