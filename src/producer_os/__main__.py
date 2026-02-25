# src/producer_os/__main__.py
from __future__ import annotations

import sys


def _run_cli() -> int:
    """Run the CLI entrypoint."""
    from producer_os.cli import main as cli_main

    # Let the CLI parse sys.argv itself.
    return int(cli_main())


def _run_gui() -> int:
    """Run the GUI entrypoint (requires PySide6)."""
    try:
        from producer_os.gui import main as gui_main
    except ModuleNotFoundError as e:
        # Most common case: PySide6 not installed.
        if "PySide6" in str(e):
            print(
                "GUI dependencies are not installed.\n"
                'Install them, then re-run:\n  pip install -e ".[gui]"\n'
                "Or use the CLI:\n  producer-os --help\n"
                "  python -m producer_os --help"
            )
            return 1
        raise

    return int(gui_main())


def main() -> int:
    """
    Module entrypoint:
      - python -m producer_os            -> CLI help / CLI execution
      - python -m producer_os gui        -> GUI
      - python -m producer_os <command>  -> CLI command
    """
    # If the first arg is "gui", launch GUI.
    if len(sys.argv) > 1 and sys.argv[1].lower() in {"gui", "qt"}:
        # Remove the "gui" token before handing control to GUI.
        sys.argv.pop(1)
        return _run_gui()

    # Default: run CLI (it will show help if args are missing).
    return _run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
