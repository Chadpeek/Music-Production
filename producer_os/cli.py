"""Command‑line interface for Producer OS.

This module exposes a suite of subcommands mirroring the GUI wizard.
Each subcommand delegates to the :class:`producer_os.engine.ProducerOSEngine`.
Run ``python -m producer_os.cli --help`` for usage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config_service import ConfigService
from .engine import ProducerOSEngine
from .styles_service import StyleService

import json


def _load_style_data(config_service: ConfigService, portable: bool) -> dict:
    # Attempt to load styles from config directory; if missing fall back to
    # bundled example located in the package next to this file.
    style_data = config_service.load_styles(cli_portable=portable)
    if not style_data:
        # Fallback: load example styles file packaged with the module
        example_path = Path(__file__).resolve().parent.parent / "bucket_styles.json"
        if example_path.exists():
            import json
            style_data = json.loads(example_path.read_text(encoding="utf-8"))
    return style_data or {}


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Producer OS – A safe music pack organiser",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("command", choices=[
        "analyze", "dry-run", "copy", "move", "repair-styles", "preview-styles", "doctor", "undo-last-run"
    ], help="Action to perform")
    parser.add_argument("inbox", nargs="?", help="Path to the inbox directory")
    parser.add_argument("hub", nargs="?", help="Path to the hub directory")
    parser.add_argument("--portable", "-p", action="store_true", help="Force portable mode")
    parser.add_argument("--overwrite-nfo", action="store_true", help="Overwrite existing .nfo files (not yet used)")
    parser.add_argument("--normalize-pack-name", action="store_true", help="Normalize pack names (not yet used)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging (developer option)")
    return parser.parse_args()


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_arguments() if argv is None else _parse_arguments()
    command = args.command
    portable = bool(args.portable)
    # Undo-last-run does not require inbox/hub
    if command == "undo-last-run":
        # Need hub path for undo
        if not args.hub:
            print("Error: hub directory is required for undo-last-run")
            return 1
        hub_path = Path(args.hub).expanduser().resolve()
        inbox_path = hub_path  # placeholder
        config_service = ConfigService(app_dir=hub_path)
        style_data = _load_style_data(config_service, portable)
        style_service = StyleService(style_data)
        engine = ProducerOSEngine(
            inbox_dir=inbox_path,
            hub_dir=hub_path,
            style_service=style_service,
            config={},
        )
        result = engine.undo_last_run()
        print(json.dumps(result, indent=2))
        return 0
    # For all other commands require inbox and hub
    if not args.inbox or not args.hub:
        print("Error: inbox and hub directories are required")
        return 1
    inbox_path = Path(args.inbox).expanduser().resolve()
    hub_path = Path(args.hub).expanduser().resolve()
    # Create config service and load config
    config_service = ConfigService(app_dir=hub_path)
    config = config_service.load_config(cli_portable=portable)
    style_data = _load_style_data(config_service, portable)
    style_service = StyleService(style_data)
    engine = ProducerOSEngine(
        inbox_dir=inbox_path,
        hub_dir=hub_path,
        style_service=style_service,
        config=config,
    )
    if command == "repair-styles":
        result = engine.repair_styles()
        print(json.dumps(result, indent=2))
        return 0
    # run other actions
    report = engine.run(
        mode=command.replace("dry-run", "dry-run").replace("analyze", "analyze"),
        overwrite_nfo=args.overwrite_nfo,
        normalize_pack_name=args.normalize_pack_name,
        developer_options={"verbose": args.verbose},
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())