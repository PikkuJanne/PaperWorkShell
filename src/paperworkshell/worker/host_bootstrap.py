"""Trusted source-tree bootstrap used by the fixed isolated Python launch."""

from __future__ import annotations

import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SOURCE_ROOT))

from paperworkshell.worker.host import main  # noqa: E402,I001


if __name__ == "__main__":
    raise SystemExit(main())
