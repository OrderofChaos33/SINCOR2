#!/usr/bin/env python3
"""Run the onchain fee-event listener as a persistent service (PRE-5).

Two modes:
    python scripts/run_fee_listener.py          # poll forever (systemd)
    python scripts/run_fee_listener.py --once   # one poll cycle (cron)

Required env:
    BASE_RPC_URL            Base RPC endpoint (FEE_LISTENER_RPC_URL overrides)
Optional env (see docs/ops/FEE_LISTENER_DEPLOY.md for the full table):
    SINCOR_FEE_RECIPIENT    fee-recipient address (default: treasury)
    FEE_LISTENER_*          confirmations (default 12), poll secs, cursor path,
                            token, min wei, fee bps (locked 500), backfill
    SINCOR_FEE_LEDGER_PATH  conversion-ledger path
    FEE_LISTENER_LOG_LEVEL  default INFO

Hard rules (build-out): this service never signs, never broadcasts, never
arms the conversion executor, and never moves funds — obligations queue as
pending only.  See docs/ops/FEE_EXECUTOR_RUNBOOK.md for the arming ceremony.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys

# Make the repo importable when run as `python scripts/run_fee_listener.py`
# from anywhere (repo-root layout: <root>/src, <root>/scripts).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _cand in (os.path.join(_REPO_ROOT, "src"), _REPO_ROOT):
    if _cand not in sys.path:
        sys.path.insert(0, _cand)

from sincor2.onchain.fee_event_listener import build_listener  # noqa: E402

logger = logging.getLogger("sincor.treasury.fee_listener.service")

EXIT_OK = 0
EXIT_CYCLE_FAILED = 1
EXIT_CONFIG_ERROR = 2


def _rpc_url() -> str:
    """Fail closed if no RPC endpoint is configured."""
    url = (
        os.environ.get("FEE_LISTENER_RPC_URL", "")
        or os.environ.get("BASE_RPC_URL", "")
    ).strip()
    if not url:
        raise RuntimeError(
            "no RPC endpoint configured: set BASE_RPC_URL "
            "(or FEE_LISTENER_RPC_URL)"
        )
    return url


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("FEE_LISTENER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def _install_termination_handler() -> None:
    """SIGTERM (e.g. `systemctl stop`) must end the poll loop cleanly.

    FeeEventListener.run_forever() already catches KeyboardInterrupt and the
    cursor is saved after every successful cycle, so translating SIGTERM
    into KeyboardInterrupt gives a clean, gap-free stop.
    """

    def _on_sigterm(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _on_sigterm)


def _run_once_mode() -> int:
    """Single poll cycle for cron: prints the summary as JSON."""
    listener = build_listener()
    summary = listener.run_once()
    print(json.dumps(summary, default=str))
    if summary.get("ok"):
        return EXIT_OK
    logger.error("fee-listener --once cycle failed: %s", summary.get("error"))
    return EXIT_CYCLE_FAILED


def main(argv: list[str] | None = None) -> int:
    """Entry point.  Returns a process exit code; never raises on RPC errors."""
    parser = argparse.ArgumentParser(
        description="SINCOR fee-event listener service (read-only; executor "
                    "stays disarmed)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run exactly one poll cycle and exit (for cron)",
    )
    args = parser.parse_args(argv)

    _configure_logging()

    try:
        _rpc_url()  # fail closed before touching the network
    except RuntimeError as exc:
        logger.error("fee-listener config error: %s", exc)
        return EXIT_CONFIG_ERROR

    recipient = (
        os.environ.get("SINCOR_FEE_RECIPIENT", "").strip()
        or "(treasury default)"
    )
    logger.info(
        "fee-listener starting mode=%s rpc_configured=yes recipient=%s",
        "once" if args.once else "forever",
        recipient,
    )

    if args.once:
        return _run_once_mode()

    _install_termination_handler()
    build_listener().run_forever()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
