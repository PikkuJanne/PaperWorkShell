"""Import-level tests for the bootstrap package."""

import paperworkshell


def test_package_identity() -> None:
    assert paperworkshell.PRODUCT_NAME == "PaperWorkShell"
    assert paperworkshell.__version__ == "0.0.0"
