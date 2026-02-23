"""Generate dummy test data for Producer OS.

This script creates an ``inbox`` directory populated with a few
sample packs and WAV/MIDI files.  The packs exercise common
classification buckets as well as low‑confidence cases.  Running
this script is idempotent – existing files will be removed and
recreated.

Usage::

    python generate_test_data.py --output /path/to/test_root

This will produce the following structure::

    test_root/
      inbox/
        My808Pack/
          808_boom.wav
          kick_punch.wav
        MyLoopsPack/
          melodic_loop_01.wav
          drumloop_02.wav
        UnknownPack/
          weirdo_sample.wav
          random.mid
      hub/           (initially empty)
"""

import argparse
import shutil
from pathlib import Path


def create_file(path: Path, size: int = 128) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"\0" * size)


def generate(output: Path) -> None:
    inbox = output / "inbox"
    hub = output / "hub"
    # Remove any existing directory
    if inbox.exists():
        shutil.rmtree(inbox)
    if hub.exists():
        shutil.rmtree(hub)
    inbox.mkdir(parents=True)
    hub.mkdir(parents=True)
    # Pack 1: contains 808 and kick samples
    pack1 = inbox / "My808Pack"
    create_file(pack1 / "808_boom.wav")
    create_file(pack1 / "kick_punch.wav")
    # Pack 2: loops
    pack2 = inbox / "MyLoopsPack"
    create_file(pack2 / "melodic_loop_01.wav")
    create_file(pack2 / "drumloop_02.wav")
    # Pack 3: unknown/unclassified
    pack3 = inbox / "UnknownPack"
    create_file(pack3 / "weirdo_sample.wav")
    create_file(pack3 / "random.mid")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate test data for Producer OS")
    parser.add_argument("--output", type=Path, default=Path("."), help="Directory to place test data")
    args = parser.parse_args()
    generate(args.output.resolve())
    print(f"Test data generated under {args.output.resolve()}")


if __name__ == "__main__":
    main()