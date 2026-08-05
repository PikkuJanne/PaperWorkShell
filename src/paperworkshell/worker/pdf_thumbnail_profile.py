"""GUI-safe identity for the fixed worker thumbnail-rendering profile."""

from __future__ import annotations

import hashlib
from typing import Final

PDF_THUMBNAIL_RENDERER_PROFILE_VERSION: Final = 1

# This identifier covers every rendering choice that can change thumbnail pixels.
# It intentionally contains no source- or case-derived value and imports no native engine.
_PDF_THUMBNAIL_RENDERER_PROFILE: Final = (
    b"paperworkshell.pdf-thumbnail-profile/v1\x00"
    b"pypdfium2/5.12.1\x00"
    b"pdfium/152.0.7947.0\x00"
    b"crop-box\x00rgba8\x00opaque-white\x00stored-annotation-appearances\x00"
    b"forms-disabled\x00xfa-disabled\x00"
)

PDF_THUMBNAIL_RENDERER_PROFILE_SHA256: Final = hashlib.sha256(
    _PDF_THUMBNAIL_RENDERER_PROFILE
).hexdigest()
