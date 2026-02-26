from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import sys
import time
from pathlib import Path


def _build_engine(sample_root: Path, hub_dir: Path):
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "src"))

    from producer_os.engine import ProducerOSEngine
    from producer_os.styles_service import StyleService

    return ProducerOSEngine(
        inbox_dir=sample_root,
        hub_dir=hub_dir,
        style_service=StyleService({"categories": {}, "buckets": {}}),
        config={},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile ProducerOSEngine._extract_features on WAV files.")
    parser.add_argument("--root", type=Path, required=True, help="Root folder to scan recursively for .wav files")
    parser.add_argument("--limit", type=int, default=500, help="Max WAV files to profile (0 = all)")
    parser.add_argument("--profile", action="store_true", help="Enable cProfile and print top cumulative functions")
    parser.add_argument("--stats", type=int, default=30, help="Number of cProfile rows to print")
    parser.add_argument("--sort", default="cumulative", help="cProfile sort key (default: cumulative)")
    parser.add_argument("--progress-every", type=int, default=100, help="Print progress every N files (0 disables)")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional JSON output path for benchmark metrics (e.g. benchmarks/latest_engine_extract.json)",
    )
    parser.add_argument(
        "--benchmark-mode",
        action="store_true",
        help="Convenience mode for reproducible local/CI perf runs (writes JSON if --json-out not provided)",
    )
    parser.add_argument("--compare", type=Path, default=None, help="Optional prior benchmark JSON to compare against")
    parser.add_argument(
        "--warn-threshold-pct",
        type=float,
        default=10.0,
        help="Warn when ms/file regresses by more than this percent (used with --compare)",
    )
    parser.add_argument(
        "--hub-dir",
        type=Path,
        default=Path(".tmp_profile_hub"),
        help="Temp hub dir used for engine init/cache loading (no writes unless engine.save is called)",
    )
    args = parser.parse_args()

    sample_root = args.root.resolve()
    if not sample_root.exists():
        print(f"error: sample root not found: {sample_root}", file=sys.stderr)
        return 2

    wavs = sorted(sample_root.rglob("*.wav"))
    if args.limit and args.limit > 0:
        wavs = wavs[: args.limit]
    if not wavs:
        print("error: no .wav files found", file=sys.stderr)
        return 3

    json_out = args.json_out
    if args.benchmark_mode and json_out is None:
        json_out = Path("benchmarks/latest_engine_extract.json")

    print(f"sample_root={sample_root}")
    print(f"wav_files={len(wavs)}")
    print(f"profile={args.profile}")

    engine = _build_engine(sample_root, args.hub_dir.resolve())
    engine._feature_cache = {}

    prof = cProfile.Profile() if args.profile else None
    start = time.perf_counter()
    if prof is not None:
        prof.enable()

    for idx, wav in enumerate(wavs, start=1):
        engine._extract_features(wav)
        if args.progress_every and idx % args.progress_every == 0:
            print(f"processed={idx}")

    if prof is not None:
        prof.disable()

    elapsed = time.perf_counter() - start
    files_per_sec = len(wavs) / elapsed if elapsed > 0 else 0.0
    ms_per_file = (elapsed * 1000.0) / len(wavs)
    print(f"elapsed_seconds={elapsed:.3f}")
    print(f"files_per_second={files_per_sec:.2f}")
    print(f"ms_per_file={ms_per_file:.2f}")

    metrics = {
        "version": 1,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sample_root": str(sample_root),
        "wav_files_profiled": len(wavs),
        "subset_strategy": "lexical_first_n",
        "limit": int(args.limit),
        "profile_enabled": bool(args.profile),
        "elapsed_seconds": round(elapsed, 6),
        "files_per_second": round(files_per_sec, 6),
        "ms_per_file": round(ms_per_file, 6),
        "progress_every": int(args.progress_every),
    }

    if json_out is not None:
        out_path = json_out.resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print(f"json_out={out_path}")

    if args.compare is not None:
        try:
            prev = json.loads(args.compare.resolve().read_text(encoding="utf-8"))
            prev_ms = float(prev.get("ms_per_file", 0.0) or 0.0)
            curr_ms = float(metrics.get("ms_per_file", 0.0) or 0.0)
            delta_ms = curr_ms - prev_ms
            delta_pct = ((delta_ms / prev_ms) * 100.0) if prev_ms > 0 else 0.0
            print(f"compare_prev_ms_per_file={prev_ms:.6f}")
            print(f"compare_curr_ms_per_file={curr_ms:.6f}")
            print(f"compare_delta_ms_per_file={delta_ms:+.6f}")
            print(f"compare_delta_pct={delta_pct:+.2f}")
            if delta_pct > float(args.warn_threshold_pct):
                print(
                    f"warning: performance regression exceeds threshold "
                    f"({delta_pct:+.2f}% > {float(args.warn_threshold_pct):.2f}%)",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(f"warning: failed to compare benchmark JSON: {exc}", file=sys.stderr)

    if prof is not None:
        stats = pstats.Stats(prof)
        stats.sort_stats(args.sort).print_stats(args.stats)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
