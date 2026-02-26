from __future__ import annotations

import os
import sys
import traceback
import json
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


def _run_tiny_analyze_smoke() -> int:
    """Run a tiny engine analyze smoke test for packaged-runtime CI validation."""
    try:
        from producer_os.bucket_service import BucketService
        from producer_os.config_service import ConfigService
        from producer_os.engine import ProducerOSEngine
        from producer_os.styles_service import StyleService
    except Exception as exc:
        print(f"Smoke tiny-analyze import failure: {exc}")
        return 21

    inbox_env = str(os.environ.get("PRODUCER_OS_SMOKE_INBOX", "")).strip()
    hub_env = str(os.environ.get("PRODUCER_OS_SMOKE_HUB", "")).strip()
    out_env = str(os.environ.get("PRODUCER_OS_SMOKE_OUT", "")).strip()
    if not inbox_env or not hub_env or not out_env:
        print("Smoke tiny-analyze missing one or more required env vars: INBOX/HUB/OUT")
        return 22

    inbox_dir = Path(inbox_env)
    hub_dir = Path(hub_env)
    out_path = Path(out_env)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)

    if not inbox_dir.exists():
        out_path.write_text('{"ok": false, "error": "inbox_missing"}', encoding="utf-8")
        print(f"Smoke tiny-analyze inbox does not exist: {inbox_dir}")
        return 23

    app_dir = Path(__file__).resolve().parents[2]
    try:
        config_service = ConfigService(app_dir=app_dir)
        style_service = StyleService(config_service.load_styles())
        bucket_service = BucketService(config_service.load_buckets())
        engine = ProducerOSEngine(
            inbox_dir=inbox_dir,
            hub_dir=hub_dir,
            style_service=style_service,
            config={"developer_tools": {"workers": 1}},
            bucket_service=bucket_service,
        )
        report = engine.run(mode="analyze", developer_options={"workers": 1}, log_to_console=False)
        packs = list(report.get("packs") or [])
        files_processed = int(report.get("files_processed", 0) or 0)
        failed = int(report.get("failed", 0) or 0)
        ok = files_processed > 0 and len(packs) > 0 and failed == 0
        payload = {
            "ok": ok,
            "files_processed": files_processed,
            "files_skipped_non_wav": int(report.get("files_skipped_non_wav", 0) or 0),
            "failed": failed,
            "packs": len(packs),
            "feature_cache_stats": report.get("feature_cache_stats", {}),
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Smoke tiny-analyze wrote {out_path}")
        if not ok:
            print(f"Smoke tiny-analyze validation failed: {payload}")
            return 24
        return 0
    except Exception as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        try:
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass
        print(f"Smoke tiny-analyze failed: {exc}")
        return 25


def main() -> int:
    if str(os.environ.get("PRODUCER_OS_SMOKE_TINY_ANALYZE", "")).strip() == "1":
        return _run_tiny_analyze_smoke()

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
