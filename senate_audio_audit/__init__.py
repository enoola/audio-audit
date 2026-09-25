"""Local, evidence-first audio analysis for French parliamentary recordings."""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__", "main"]


def main() -> int:
    """Console-script entry point kept at package root for editable installs."""
    from .cli import main as cli_main

    return cli_main()
