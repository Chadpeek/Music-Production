"""Producer OS package

This package contains the core engine, services, command‑line interface
and optional GUI for organising music sample packs.  See the
`README.md` in the project root for an overview and quickstart.

The v2 rewrite relocates the original modules into the `src/producer_os`
package to conform to a standard *src layout*.  Public classes are
re‑exported here for convenience, so that callers may still import
``producer_os.engine`` or ``producer_os.cli`` without needing to know
the internal layout.
"""

from .engine import ProducerOSEngine  # noqa: F401
from .styles_service import StyleService  # noqa: F401
from .config_service import ConfigService  # noqa: F401
from .bucket_service import BucketService  # noqa: F401

__all__ = [
    "ProducerOSEngine",
    "StyleService",
    "ConfigService",
    "BucketService",
]