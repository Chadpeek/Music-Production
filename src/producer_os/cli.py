"""Command‑line interface for Producer OS (v2).

This module exposes a suite of subcommands mirroring the GUI wizard.
Each subcommand delegates to :class:`producer_os.engine.ProducerOSEngine`.
Run ``python -m producer_os.cli --help`` for usage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config_service import ConfigService
from .engine import ProducerOSEngine
from .styles_service import StyleService
from .bucket_service import BucketService

import json
from typing import Optional, List


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


def _load_bucket_mapping(config_service: ConfigService, portable: bool) -> dict:
    mapping = config_service.load_buckets(cli_portable=portable)
    if mapping:
        return mapping
    # Fallback: load example buckets mapping packaged with the module
    example_path = Path(__file__).resolve().parent.parent / "buckets.json"
    if example_path.exists():
        try:
            return json.loads(example_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Producer OS – A safe music pack organiser",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Common arguments for commands that require inbox and hub
    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("inbox", help="Path to the inbox directory")
        subparser.add_argument("hub", help="Path to the hub directory")
        subparser.add_argument(
            "--portable",
            "-p",
            action="store_true",
            help="Force portable mode (ignored if portable.flag is present)",
        )
        subparser.add_argument(
            "--overwrite-nfo",
            action="store_true",
            help="Overwrite existing .nfo files (if different)",
        )
        subparser.add_argument(
            "--normalize-pack-name",
            action="store_true",
            help="Normalize pack names (reserved for future use)",
        )
        subparser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable verbose logging (developer option)",
        )
        subparser.add_argument(
            "--workers",
            type=int,
            default=1,
            help="Worker count for feature extraction (used when parallel extraction is enabled)",
        )

    # analyze
    sp = subparsers.add_parser("analyze", help="Scan and classify packs, produce a report only")
    add_common(sp)
    # dry-run
    sp = subparsers.add_parser("dry-run", help="Show what would happen without moving/copying files")
    add_common(sp)
    # copy
    sp = subparsers.add_parser("copy", help="Copy files into the hub while preserving the inbox")
    add_common(sp)
    # move
    sp = subparsers.add_parser("move", help="Move files into the hub and record an audit trail")
    add_common(sp)
    # repair-styles
    sp = subparsers.add_parser("repair-styles", help="Regenerate missing or misplaced `.nfo` files")
    sp.add_argument("hub", help="Path to the hub directory")
    sp.add_argument(
        "--portable", "-p", action="store_true", help="Force portable mode (ignored if portable.flag is present)"
    )
    # preview-styles (placeholder)
    sp = subparsers.add_parser("preview-styles", help="Reserved for future visual preview of styles")
    sp.add_argument("hub", help="Path to the hub directory")
    sp.add_argument(
        "--portable", "-p", action="store_true", help="Force portable mode (ignored if portable.flag is present)"
    )
    # doctor (placeholder)
    sp = subparsers.add_parser(
        "doctor",
        help="Reserved for self‑healing integrity checks (not yet implemented)",
    )
    sp.add_argument("hub", help="Path to the hub directory")
    sp.add_argument(
        "--portable", "-p", action="store_true", help="Force portable mode (ignored if portable.flag is present)"
    )
    # undo-last-run
    sp = subparsers.add_parser("undo-last-run", help="Undo the last move operation using the audit trail")
    sp.add_argument("hub", help="Path to the hub directory")
    sp.add_argument(
        "--portable", "-p", action="store_true", help="Force portable mode (ignored if portable.flag is present)"
    )
    # benchmark-classifier
    sp = subparsers.add_parser(
        "benchmark-classifier",
        help="Run a read-only recursive classifier audit and write a benchmark report",
    )
    add_common(sp)
    sp.add_argument("--output", help="Path to benchmark JSON (default: <hub>/logs/benchmark_report.json)")
    sp.add_argument("--top-confusions", type=int, default=20, help="Number of confusion pairs to include")
    sp.add_argument("--max-files", type=int, default=None, help="Optional cap on benchmark summary entries")
    sp.add_argument("--compare", help="Optional prior benchmark JSON to compare low-confidence rate against")
    return parser.parse_args()


def _construct_engine(
    inbox_path: Path,
    hub_path: Path,
    config_service: ConfigService,
    portable: bool,
    verbose: bool,
    overwrite_nfo: bool = False,
    normalize_pack_name: bool = False,
) -> ProducerOSEngine:
    # Load config, styles and bucket mapping
    config = config_service.load_config(cli_portable=portable)
    try:
        config["config_dir"] = str(config_service.get_config_dir(cli_portable=portable))
        config["config_path"] = str(config_service.get_config_path(cli_portable=portable))
        config["bucket_hints_path"] = str(config_service.get_bucket_hints_path(cli_portable=portable))
        config["bucket_hints"] = config_service.load_bucket_hints(cli_portable=portable)
    except Exception:
        pass
    style_data = _load_style_data(config_service, portable)
    bucket_mapping = _load_bucket_mapping(config_service, portable)
    style_service = StyleService(style_data)
    bucket_service = BucketService(bucket_mapping)
    return ProducerOSEngine(
        inbox_dir=inbox_path,
        hub_dir=hub_path,
        style_service=style_service,
        config=config,
        bucket_service=bucket_service,
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_arguments() if argv is None else _parse_arguments()
    command = args.command
    # Determine if portable mode is requested from CLI
    cli_portable = bool(getattr(args, "portable", False))
    # Set up config service using the hub path as app_dir; if inbox is missing (repair/preview/doctor/undo), we still set app_dir to hub
    hub_path = Path(args.hub).expanduser().resolve() if hasattr(args, "hub") else None
    inbox_path = Path(args.inbox).expanduser().resolve() if hasattr(args, "inbox") else None
    # Undo-last-run does not require inbox/hub (hub only)
    if command == "undo-last-run":
        if not hub_path:
            print("Error: hub directory is required for undo-last-run")
            return 1
        config_service = ConfigService(app_dir=hub_path)
        # Even for undo we load style and bucket mapping to reconstruct current paths
        style_data = _load_style_data(config_service, cli_portable)
        bucket_mapping = _load_bucket_mapping(config_service, cli_portable)
        style_service = StyleService(style_data)
        bucket_service = BucketService(bucket_mapping)
        engine = ProducerOSEngine(
            inbox_dir=hub_path,  # placeholder; not used for undo
            hub_dir=hub_path,
            style_service=style_service,
            config={},
            bucket_service=bucket_service,
        )
        result = engine.undo_last_run()
        print(json.dumps(result, indent=2))
        return 0
    # repair-styles
    if command == "repair-styles":
        if not hub_path:
            print("Error: hub directory is required for repair-styles")
            return 1
        config_service = ConfigService(app_dir=hub_path)
        engine = _construct_engine(hub_path, hub_path, config_service, cli_portable, verbose=False)
        result = engine.repair_styles()
        print(json.dumps(result, indent=2))
        return 0
    # preview-styles (stub)
    if command == "preview-styles":
        print("Error: preview-styles is not yet implemented")
        return 1
    # doctor (stub)
    if command == "doctor":
        print("Error: doctor is not yet implemented")
        return 1
    # benchmark-classifier (read-only audit/report)
    if command == "benchmark-classifier":
        if not inbox_path or not hub_path:
            print("Error: inbox and hub directories are required for benchmark-classifier")
            return 1
        config_service = ConfigService(app_dir=hub_path)
        engine = _construct_engine(
            inbox_path, hub_path, config_service, cli_portable, args.verbose, args.overwrite_nfo, args.normalize_pack_name
        )
        output_path = (
            Path(args.output).expanduser().resolve()
            if getattr(args, "output", None)
            else (hub_path / "logs" / "benchmark_report.json")
        )
        benchmark = engine.run_benchmark(
            output_path=output_path,
            top_confusions=int(getattr(args, "top_confusions", 20) or 20),
            max_files=getattr(args, "max_files", None),
            workers=max(1, int(getattr(args, "workers", 1) or 1)),
            save_feature_cache=True,
        )
        low_conf = dict(benchmark.get("low_confidence") or {})
        print(
            f"Benchmark complete: files={benchmark.get('files_classified', 0)} "
            f"low_conf={low_conf.get('count', 0)} rate={float(low_conf.get('rate', 0.0) or 0.0):.4f}"
        )
        print("Top buckets:")
        for row in list(benchmark.get("bucket_distribution") or [])[:10]:
            print(f"  {row.get('bucket')}: {row.get('count')} ({row.get('percent')}%)")
        print("Top confusion pairs:")
        for row in list(benchmark.get("confusion_pairs") or [])[:10]:
            print(f"  {row.get('chosen')} -> {row.get('runner_up')}: {row.get('count')}")
        print(f"Benchmark report: {output_path}")
        compare_path = getattr(args, "compare", None)
        if compare_path:
            try:
                prev = json.loads(Path(compare_path).expanduser().resolve().read_text(encoding="utf-8"))
                prev_low = float(((prev or {}).get("low_confidence") or {}).get("rate", 0.0) or 0.0)
                curr_low = float(low_conf.get("rate", 0.0) or 0.0)
                print(f"Compare low-confidence rate: prev={prev_low:.4f} curr={curr_low:.4f} delta={curr_low - prev_low:+.4f}")
                prev_pairs = {
                    (str(p.get("chosen")), str(p.get("runner_up"))): int(p.get("count", 0) or 0)
                    for p in (prev.get("confusion_pairs") or [])
                    if isinstance(p, dict)
                }
                curr_pairs = {
                    (str(p.get("chosen")), str(p.get("runner_up"))): int(p.get("count", 0) or 0)
                    for p in (benchmark.get("confusion_pairs") or [])
                    if isinstance(p, dict)
                }
                all_keys = set(prev_pairs) | set(curr_pairs)
                deltas = sorted(
                    ((key, curr_pairs.get(key, 0) - prev_pairs.get(key, 0)) for key in all_keys),
                    key=lambda item: (-abs(item[1]), item[0]),
                )
                print("Top confusion pair deltas:")
                for (chosen, runner_up), delta in deltas[:10]:
                    print(
                        f"  {chosen} -> {runner_up}: prev={prev_pairs.get((chosen, runner_up), 0)} "
                        f"curr={curr_pairs.get((chosen, runner_up), 0)} delta={delta:+d}"
                    )
            except Exception as exc:
                print(f"Warning: failed to compare benchmark reports: {exc}")
        return 0
    # For other commands require inbox and hub
    if not inbox_path or not hub_path:
        print("Error: inbox and hub directories are required")
        return 1
    config_service = ConfigService(app_dir=hub_path)
    engine = _construct_engine(
        inbox_path, hub_path, config_service, cli_portable, args.verbose, args.overwrite_nfo, args.normalize_pack_name
    )
    if command == "analyze":
        mode = "analyze"
    elif command == "dry-run":
        mode = "dry-run"
    elif command == "copy":
        mode = "copy"
    elif command == "move":
        mode = "move"
    else:
        print(f"Error: unrecognized command {command}")
        return 1
    report = engine.run(
        mode=mode,
        overwrite_nfo=args.overwrite_nfo,
        normalize_pack_name=args.normalize_pack_name,
        developer_options={"verbose": args.verbose, "workers": max(1, int(getattr(args, "workers", 1) or 1))},
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
