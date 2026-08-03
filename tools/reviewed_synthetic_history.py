"""Authenticate exact synthetic document bytes reviewed on sibling task branches."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final

_MAX_DOCUMENT_BYTES: Final = 1 * 1024 * 1024
_EXPECTED_ENTRY_COUNT: Final = 22


@dataclass(frozen=True)
class ReviewedCrossBranchDocument:
    """Closed provenance for one synthetic document reachable on another branch."""

    fixture_id: str
    relative_path: str
    media_type: str
    byte_count: int
    recipe: str
    sha256: str
    introducing_commit: str
    decision_ref: str = "PWS-D035"
    provenance: str = "closed-algorithmic-no-input"


REVIEWED_CROSS_BRANCH_DOCUMENTS: Final[tuple[ReviewedCrossBranchDocument, ...]] = (
    ReviewedCrossBranchDocument(
        "pdf-render-annotation-appearance",
        "pdf/render-annotation-appearance.pdf",
        "application/pdf",
        921,
        "render-annotation-appearance-pdf-v1",
        "f62ae16b6bfc9fe89110b4a5c597ce4b2d8c01564ea5fdea187c272fdd7bba8c",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "pdf-render-calibration",
        "pdf/render-calibration.pdf",
        "application/pdf",
        1050,
        "render-calibration-pdf-v1",
        "a0b937f3cf2fd2e4653d4fc067aae459f8dd17c5ad88edc9a3a5482b6f94dbc0",
        "ac6ed43d2d0b09fadfdf6b0b13b3aab65cdb781b",
    ),
    ReviewedCrossBranchDocument(
        "pdf-render-cmyk",
        "pdf/render-cmyk.pdf",
        "application/pdf",
        984,
        "render-cmyk-pdf-v1",
        "b51ac42dff2d2a21014123d8e133d1049de0b8dd12726e4540bc12d58370d69f",
        "ac6ed43d2d0b09fadfdf6b0b13b3aab65cdb781b",
    ),
    ReviewedCrossBranchDocument(
        "pdf-render-colorspaces",
        "pdf/render-colorspaces.pdf",
        "application/pdf",
        1578,
        "render-colorspaces-pdf-v1",
        "c0e74a04f6c7b0468d128128dcc5e263b59d8b3c90fc20074638818c53384865",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "pdf-render-embedded-type3-font",
        "pdf/render-embedded-type3-font.pdf",
        "application/pdf",
        1032,
        "render-embedded-type3-font-pdf-v1",
        "35fc07e396e843f8fd95e3fee746ee4a5fcf074ddaede701df6fa54531ae2af2",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "pdf-render-geometry-matrix",
        "pdf/render-geometry-matrix.pdf",
        "application/pdf",
        2308,
        "render-geometry-matrix-pdf-v1",
        "b94972c3f45516fc1dbb38f58594c35721253bf252a76b8452430e054b90fccc",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "pdf-render-high-dimension",
        "pdf/render-high-dimension.pdf",
        "application/pdf",
        595,
        "render-high-dimension-pdf-v1",
        "5200af06d81559aef0c48c42b190ac7edbd5a5ab07456d6ac86b7e02a90c4409",
        "ac6ed43d2d0b09fadfdf6b0b13b3aab65cdb781b",
    ),
    ReviewedCrossBranchDocument(
        "pdf-render-malformed-page-tree",
        "pdf/render-malformed-page-tree.pdf",
        "application/pdf",
        305,
        "render-malformed-page-tree-pdf-v1",
        "2cefb2633b0f639f8b914e078ef9dd32ae58fb68bc5e571d487d7efe0b098458",
        "ac6ed43d2d0b09fadfdf6b0b13b3aab65cdb781b",
    ),
    ReviewedCrossBranchDocument(
        "pdf-render-soft-mask-blend",
        "pdf/render-soft-mask-blend.pdf",
        "application/pdf",
        1132,
        "render-soft-mask-blend-pdf-v1",
        "313594e8a0851e9523df5ce3f97db45a5c23150305958b076c4bcbedd6e32058",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "pdf-render-transparency",
        "pdf/render-transparency.pdf",
        "application/pdf",
        750,
        "render-transparency-pdf-v1",
        "28fe792cf1083f331d72fab162df7f1099fb86324704ba87de1cc7db9a42bc05",
        "ac6ed43d2d0b09fadfdf6b0b13b3aab65cdb781b",
    ),
    ReviewedCrossBranchDocument(
        "png-render-annotation-appearance",
        "image/render-annotation-appearance.png",
        "image/png",
        12509,
        "render-annotation-appearance-png-v1",
        "e5b3a19338b69a3e9fc1458178374930052e5666d5883f91046003f60640e916",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "png-render-calibration-crop-rotate",
        "image/render-calibration-crop-rotate.png",
        "image/png",
        7416,
        "render-calibration-crop-rotate-png-v1",
        "90648e4c601382ca3b96f7282cd2f33a6ef577a223ef185584b088372738ccf3",
        "ac6ed43d2d0b09fadfdf6b0b13b3aab65cdb781b",
    ),
    ReviewedCrossBranchDocument(
        "png-render-calibration-media-box",
        "image/render-calibration-media-box.png",
        "image/png",
        20969,
        "render-calibration-media-box-png-v1",
        "412a3a6068b87271bcff388cead2a00a9bd6ee83bc3568f244a50aa439662b67",
        "ac6ed43d2d0b09fadfdf6b0b13b3aab65cdb781b",
    ),
    ReviewedCrossBranchDocument(
        "png-render-embedded-type3-font",
        "image/render-embedded-type3-font.png",
        "image/png",
        24798,
        "render-embedded-type3-font-png-v1",
        "26ecec7f512b6239b21dcb52f301a0f749b0f0d9a89afaa267f701449dcebd4f",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "png-render-geometry-direct-box",
        "image/render-geometry-direct-box.png",
        "image/png",
        2576,
        "render-geometry-direct-box-png-v1",
        "b27619c6aa76af98303747a48bf079d7e1811cf701daf3c5764e2abbe6535cdf",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "png-render-geometry-inherited-rotate-0",
        "image/render-geometry-inherited-rotate-0.png",
        "image/png",
        1973,
        "render-geometry-inherited-rotate-0-png-v1",
        "d3072e8731477721e211578ea14de7c1d23e3840273dfe2c6cda23e79cc8afd6",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "png-render-geometry-inherited-rotate-180",
        "image/render-geometry-inherited-rotate-180.png",
        "image/png",
        1975,
        "render-geometry-inherited-rotate-180-png-v1",
        "43c13ee3e3aa5c20ad21dd66c3eca826887de0821b11eb0ade2e38e47eda2823",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "png-render-geometry-inherited-rotate-270",
        "image/render-geometry-inherited-rotate-270.png",
        "image/png",
        1985,
        "render-geometry-inherited-rotate-270-png-v1",
        "9ce3fafd5a09f7ff67161588b89e0ac5ae9f398924f355761effdcd04dccecad",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "png-render-geometry-inherited-rotate-90",
        "image/render-geometry-inherited-rotate-90.png",
        "image/png",
        1984,
        "render-geometry-inherited-rotate-90-png-v1",
        "341bb01fc684fd3d6c86e9a4b2fcec3b210158f3bb70c95ac9d2f4a40de77f1e",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "png-render-geometry-user-unit",
        "image/render-geometry-user-unit.png",
        "image/png",
        2761,
        "render-geometry-user-unit-png-v1",
        "a3eebb062e0d8cb17c724a22f4a26c301e089788d1c98fbd85f1161748437d68",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "png-render-soft-mask-blend",
        "image/render-soft-mask-blend.png",
        "image/png",
        12509,
        "render-soft-mask-blend-png-v1",
        "27ec3692e145af5649d6af158f1e46febb35a1b2172569dde3ec9afd2090c54d",
        "529a6da1e67d514fad859d233b7dd9da5257e636",
    ),
    ReviewedCrossBranchDocument(
        "png-render-transparency",
        "image/render-transparency.png",
        "image/png",
        12502,
        "render-transparency-png-v1",
        "48b14820387a1343d2910cc8bf8b9a283a68b9dcf862d55392df18cb352753c4",
        "ac6ed43d2d0b09fadfdf6b0b13b3aab65cdb781b",
    ),
)

REVIEWED_CROSS_BRANCH_LEDGER_FINGERPRINT: Final = (
    "bd8c33f4ee95c7334870905a4564a8a8d5630a0bfcae1da0e92b6adbb2e81a56"
)


def _canonical_ledger_payload() -> bytes:
    entries = sorted(REVIEWED_CROSS_BRANCH_DOCUMENTS, key=lambda entry: entry.fixture_id)
    return json.dumps(
        [
            {
                "byte_count": entry.byte_count,
                "decision_ref": entry.decision_ref,
                "fixture_id": entry.fixture_id,
                "introducing_commit": entry.introducing_commit,
                "media_type": entry.media_type,
                "provenance": entry.provenance,
                "recipe": entry.recipe,
                "relative_path": entry.relative_path,
                "sha256": entry.sha256,
            }
            for entry in entries
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def reviewed_cross_branch_document_digests() -> frozenset[bytes]:
    """Validate the closed ledger and return its exact approved SHA-256 digests."""

    fixture_ids: set[str] = set()
    relative_paths: set[str] = set()
    digests: set[bytes] = set()
    invalid = len(REVIEWED_CROSS_BRANCH_DOCUMENTS) != _EXPECTED_ENTRY_COUNT
    for entry in REVIEWED_CROSS_BRANCH_DOCUMENTS:
        try:
            digest = bytes.fromhex(entry.sha256)
        except ValueError:
            invalid = True
            continue
        expected_media_type = (
            "application/pdf" if entry.relative_path.endswith(".pdf") else "image/png"
        )
        invalid = invalid or any(
            (
                re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", entry.fixture_id) is None,
                re.fullmatch(r"(?:pdf|image)/[a-z0-9-]+\.(?:pdf|png)", entry.relative_path) is None,
                re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", entry.recipe) is None,
                re.fullmatch(r"[0-9a-f]{64}", entry.sha256) is None,
                re.fullmatch(r"[0-9a-f]{40}", entry.introducing_commit) is None,
                len(digest) != 32,
                entry.media_type != expected_media_type,
                not 0 < entry.byte_count <= _MAX_DOCUMENT_BYTES,
                entry.decision_ref != "PWS-D035",
                entry.provenance != "closed-algorithmic-no-input",
                entry.fixture_id in fixture_ids,
                entry.relative_path in relative_paths,
                digest in digests,
            )
        )
        fixture_ids.add(entry.fixture_id)
        relative_paths.add(entry.relative_path)
        digests.add(digest)

    invalid = invalid or (
        hashlib.sha256(_canonical_ledger_payload()).hexdigest()
        != REVIEWED_CROSS_BRANCH_LEDGER_FINGERPRINT
    )
    if invalid:
        raise RuntimeError("reviewed cross-branch synthetic ledger is not authenticated")
    return frozenset(digests)
