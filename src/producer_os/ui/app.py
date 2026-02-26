from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from producer_os.ui.window import ProducerOSWindow


def _load_app_icon() -> Optional[QIcon]:
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [
        repo_root / "assets" / "app_icon.ico",
        repo_root / "assets" / "app_icon.png",
        repo_root / "assets" / "banner.png",
    ]
    for path in candidates:
        if not path.exists():
            continue
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return None


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Producer OS")
    app.setOrganizationName("KidChadd")

    app_icon = _load_app_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)

    win = ProducerOSWindow(app_icon=app_icon)
    win.show()

    if str(os.environ.get("PRODUCER_OS_SMOKE_TEST", "")).strip() == "1":
        try:
            delay_ms = int(str(os.environ.get("PRODUCER_OS_SMOKE_TEST_MS", "250")).strip() or "250")
        except Exception:
            delay_ms = 250
        QTimer.singleShot(max(50, delay_ms), app.quit)

    try:
        return app.exec()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
