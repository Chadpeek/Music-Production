# build_gui_entry.py
import os
import sys
from pathlib import Path

# Disable numba JIT (important for frozen builds)
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import sys
from producer_os.gui import main

if __name__ == "__main__":
    raise SystemExit(main())