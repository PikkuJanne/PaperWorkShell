"""Small installed startup check that does not construct a case or worker."""

from __future__ import annotations

import sys
from pathlib import Path

from paperworkshell.app.installed_runtime import InstalledRuntimeError, discover_installed_runtime
from paperworkshell.domain.manifest_repository import CURRENT_MANIFEST_SCHEMA_VERSION


def main() -> int:
    if sys.argv[1:] != [str(CURRENT_MANIFEST_SCHEMA_VERSION)]:
        return 2
    try:
        runtime = discover_installed_runtime(
            package_root=Path(__file__).absolute().parent.parent,
            executable=Path(sys.executable),
            prefix=Path(sys.prefix),
            base_prefix=Path(sys.base_prefix),
        )
    except InstalledRuntimeError:
        return 2
    return 0 if runtime is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
