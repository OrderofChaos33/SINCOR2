"""Make the standalone package importable without the platform tree.

Inserts only this package's own ``src/`` directory on ``sys.path`` so
the test suite proves the package stands alone (PACK-2 smoke property).
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
