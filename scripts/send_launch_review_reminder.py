#!/usr/bin/env python3
"""Send daily /launch/review approval reminder email."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sincor2.launch_review_notify import send_launch_review_reminder


def main() -> None:
    result = send_launch_review_reminder()
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()