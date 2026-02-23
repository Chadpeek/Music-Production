"""GUI wizard for Producer OS.

This module provides a PySide6‑based wizard interface for running
Producer OS through a series of steps: selecting the inbox and hub
folders, configuring options, and executing the run.  The current
implementation is a minimal placeholder to satisfy packaging until
the full UX is developed.  It displays a main window with a message
indicating that the GUI is under construction.

To launch the GUI run ``python -m producer_os.gui``.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PySide6.QtWidgets import QApplication, QLabel, QMainWindow
    from PySide6.QtCore import Qt
except ImportError:
    # PySide6 is an optional dependency for the GUI; if not installed
    # show a friendly error when the module is executed as a script.
    PySide6 = None


def main() -> int:
    # Check if PySide6 is available
    try:
        from PySide6.QtWidgets import QApplication, QLabel, QMainWindow
    except ImportError:
        print("Error: PySide6 is not installed. Please install PySide6 to use the GUI.")
        return 1
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("Producer OS – GUI (Under Construction)")
    label = QLabel(
        "The Producer OS GUI wizard is under development.\n"
        "Please use the command‑line interface for now.",
        alignment=Qt.AlignCenter,
    )
    window.setCentralWidget(label)
    window.resize(600, 200)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())