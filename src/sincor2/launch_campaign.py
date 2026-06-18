"""
KPI-driven TGE campaign engine — pre-deposit, points, milestones, social whitelist.

MegaETH-style: performance KPIs unlock rollout waves; TGE allocation tied to points.
Pre-deposit opens July 1 · TGE July 7.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from sincor2.data_paths import data_dir, project_root

logger = logging.getLogger("sincor2.launch_campaign")

WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
TX_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _config_path() -> Path:
    return project_root() / "config" / "launch_campaign.yaml"


def _db_path() -> Path:
    p = data_dir() / "launch_campaign.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def load_config() -> dict[str, Any]:
    return yaml.safe_load(_config_path().read_text(encoding="utf-8"))


def init_campaign_db() -> None:
    with _conn() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS participants (
                wallet TEXT PRIMARY KEY,
                email TEXT,
                twitter TEXT,
                farcaster TEXT,
                registered_at TEXT NOT NULL,
                social_verified INTEGER DEFAULT 0,
                mercenary_flag INTEGER DEFAULT 0,
                whale_tier INTEGER DEFAULT 0,
                farmer_score REAL DEFAULT 0,
                total_points INTEGER DEFAULT 0,
                allocation_multiplier REAL DEFAULT 1.0,
                lbp_priority REAL DEFAULT 1.0,
                predeposit_usdc REAL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS points_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                action TEXT NOT NULL,
                points INTEGER NOT NULL,
                multiplier_delta REAL DEFAULT 0,
                proof TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS predeposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                amount_usdc REAL NOT NULL,
                tx_hash TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS milestone_log (
                milestone_id TEXT PRIMARY KEY,
                achieved_at TEXT NOT NULL,
                value_at_achieve REAL
            );
            CREATE INDEX IF NOT EXISTS idx_points_wallet ON points_ledger(wallet);
            CREATE INDEX IF NOT EXISTS idx_predep_wallet ON predeposits(wallet);
            """
        )
        db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _onchain_verify_enabled() -> bool:
    return os.environ.get("CAMPAIGN_VERIFY_ONCHAIN", "true").lower() != "false"


def _tx_already_used(tx_hash: str) -> bool:
    tx = tx_hash.strip().lower()
    with _conn() as db:
        in_pre = db.execute(
            "SELECT 1 FROM predeposits WHERE lower(tx_hash)=?", (tx,)
        ).fetchone()
        in_pts = db.execute(
            "SELECT 1 FROM points_ledger WHERE lower(proof)=?", (tx,)
        ).fetchone()
    return bool(in_pre or in_pts)


def _verify_usdc_to_treasury(
    tx_hash: str,
    *,
    min_usdc: float,
    payer_wallet: str = "",
) -> dict[str, Any]:
    from sincor2.platform_payments import TREASURY, display_to_atomic, verify_treasury_transfer

    expected = display_to_atomic(min_usdc, "USDC")
    return verify_treasury_transfer(
        tx_hash,
        token="USDC",
        expected_atomic=expected,
        treasury=TREASURY,
        payer_wallet=payer_wallet,
    )


def _verify_usdc_to_wallet(tx_hash: str, *, wallet: str, min_usdc: float) -> dict[str, Any]:
    """Verify Base USDC transfer credited to participant wallet (bridge qualification)."""
    from sincor2.platform_payments import (
        USDC,
        TREASURY,
        _parse_transfer_logs,
        _rpc_call,
        atomic_to_display,
        display_to_atomic,
    )

    wallet_l = wallet.strip().lower()
    if not TX_RE.match(tx_hash):
        return {"ok": False, "error": "invalid_tx_hash"}
    try:
        receipt = _rpc_call("eth_getTransactionReceipt", [tx_hash])
    except Exception as exc:
        return {"ok": False, "error": "rpc_failed", "detail": str(exc)}
    if not receipt:
        return {"ok": False, "error": "tx_pending"}
    if receipt.get("status") != "0x1":
        return {"ok": False, "error": "tx_failed"}

    min_atomic = display_to_atomic(min_usdc, "USDC")
    transfers = _parse_transfer_logs(receipt, USDC, wallet_l)
    if not transfers:
        return {"ok": False, "error": "no_wallet_usdc_transfer"}
    paid = max(t["amount_atomic"] for t in transfers)
    if paid < min_atomic:
        return {
            "ok": False,
            "error": "insufficient_amount",
            "paid_display": atomic_to_display(paid, "USDC"),
        }
    return {
        "ok": True,
        "amount_atomic": paid,
        "amount_display": atomic_to_display(paid, "USDC"),
        "payer_wallet": transfers[0]["from"],
        "treasury": TREASURY,
    }


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def campaign_phase() -> str:
    cfg = load_config()
    now = datetime.now(timezone.utc)
    pre = _parse_ts(cfg["predeposit_opens"])
    tge = _parse_ts(cfg["tge_date"])
    if now < pre:
        return "pre_campaign"
    if now < tge:
        return "predeposit_live"
    return "tge_live"


def register_wallet(
    wallet: str,
    *,
    email: str = "",
    twitter: str = "",
    farcaster: str = "",
) -> dict[str, Any]:
    init_campaign_db()
    wallet = wallet.strip().lower()
    if not WALLET_RE.match(wallet):
        return {"ok": False, "error": "invalid_wallet"}

    now = _now()
    with _conn() as db:
        existing = db.execute(
            "SELECT wallet FROM participants WHERE wallet=?", (wallet,)
        ).fetchone()
        if not existing:
            db.execute(
                """INSERT INTO participants
                   (wallet, email, twitter, farcaster, registered_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (wallet, email[:120], twitter[:80], farcaster[:80], now, now),
            )
            db.commit()
            award_points(wallet, "register_wallet", proof="registration")
        else:
            db.execute(
                """UPDATE participants SET email=COALESCE(NULLIF(?,''),email),
                   twitter=COALESCE(NULLIF(?,''),twitter),
                   farcaster=COALESCE(NULLIF(?,''),farcaster), updated_at=?
                   WHERE wallet=?""",
                (email, twitter, farcaster, now, wallet),
            )
            db.commit()

    return {"ok": True, "wallet": wallet, "phase": campaign_phase()}


def award_points(
    wallet: str,
    action: str,
    *,
    proof: str = "",
    amount_usdc: float = 0,
) -> dict[str, Any]:
    init_campaign_db()
    cfg = load_config()
    actions = cfg.get("points_actions", {})
    spec = actions.get(action)
    if not spec:
        return {"ok": False, "error": "unknown_action"}

    wallet = wallet.strip().lower()
    verified_usdc = 0.0
    if action == "bridge_usdc":
        if not proof or proof == "self_claim":
            return {"ok": False, "error": "tx_hash_required"}
        if _tx_already_used(proof):
            return {"ok": False, "error": "tx_already_used"}
        if _onchain_verify_enabled():
            min_bridge = float(os.environ.get("CAMPAIGN_MIN_BRIDGE_USDC", "1"))
            v = _verify_usdc_to_wallet(proof, wallet=wallet, min_usdc=min_bridge)
            if not v.get("ok"):
                return v
            verified_usdc = float(v.get("amount_display", 0))
            amount_usdc = verified_usdc

    with _conn() as db:
        row = db.execute(
            "SELECT * FROM participants WHERE wallet=?", (wallet,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "not_registered"}

        for req in spec.get("requires", []):
            prior = db.execute(
                "SELECT 1 FROM points_ledger WHERE wallet=? AND action=?",
                (wallet, req),
            ).fetchone()
            if not prior:
                return {"ok": False, "error": f"requires_{req}"}

        dup = db.execute(
            "SELECT 1 FROM points_ledger WHERE wallet=? AND action=? AND proof=?",
            (wallet, action, proof[:200]),
        ).fetchone()
        if dup and action not in ("refer_wallet", "sinc_gateway_buy"):
            return {"ok": False, "error": "already_claimed"}

        pts = int(spec.get("points", 0))
        mult = float(spec.get("multiplier", 1.0))
        early_before = spec.get("early_bird_before")
        if early_before and datetime.now(timezone.utc) < _parse_ts(early_before):
            mult *= float(spec.get("early_bird_multiplier", 1.0))

        if action == "predeposit_early" and amount_usdc > 0:
            pts += int(min(amount_usdc / 10, 5000))

        now = _now()
        db.execute(
            """INSERT INTO points_ledger (wallet, action, points, multiplier_delta, proof, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (wallet, action, pts, mult - 1.0, proof[:500], now),
        )
        new_points = int(row["total_points"] or 0) + pts
        new_mult = float(row["allocation_multiplier"] or 1.0) * mult
        db.execute(
            """UPDATE participants SET total_points=?, allocation_multiplier=?, updated_at=?
               WHERE wallet=?""",
            (new_points, round(new_mult, 4), now, wallet),
        )
        db.commit()

    _refresh_participant_tiers(wallet)
    if action == "social_whitelist":
        _evaluate_social_whitelist(wallet)
    if action == "bridge_usdc" and amount_usdc > 0:
        _evaluate_defi_farmer(wallet, amount_usdc)
    check_milestones()
    out = {"ok": True, "points_awarded": pts, "action": action}
    if verified_usdc:
        out["verified_usdc"] = verified_usdc
    return out


def record_predeposit(
    wallet: str,
    amount_usdc: float,
    tx_hash: str = "",
) -> dict[str, Any]:
    init_campaign_db()
    wallet = wallet.strip().lower()
    if not WALLET_RE.match(wallet):
        return {"ok": False, "error": "invalid_wallet"}
    if amount_usdc <= 0:
        return {"ok": False, "error": "invalid_amount"}
    if not tx_hash or not TX_RE.match(tx_hash):
        return {"ok": False, "error": "tx_hash_required"}
    if _tx_already_used(tx_hash):
        return {"ok": False, "error": "tx_already_used"}

    if campaign_phase() == "pre_campaign":
        return {"ok": False, "error": "predeposit_not_open", "opens": load_config()["predeposit_opens"]}

    reg = register_wallet(wallet)
    if not reg.get("ok"):
        return reg

    verified_amount = amount_usdc
    if _onchain_verify_enabled():
        v = _verify_usdc_to_treasury(tx_hash, min_usdc=amount_usdc, payer_wallet=wallet)
        if not v.get("ok"):
            return v
        verified_amount = float(v.get("amount_display", amount_usdc))

    now = _now()
    with _conn() as db:
        db.execute(
            """INSERT INTO predeposits (wallet, amount_usdc, tx_hash, status, created_at)
               VALUES (?, ?, ?, 'confirmed', ?)""",
            (wallet, round(verified_amount, 2), tx_hash[:66], now),
        )
        total = db.execute(
            "SELECT COALESCE(SUM(amount_usdc),0) FROM predeposits WHERE wallet=?",
            (wallet,),
        ).fetchone()[0]
        db.execute(
            "UPDATE participants SET predeposit_usdc=?, updated_at=? WHERE wallet=?",
            (float(total), now, wallet),
        )
        db.commit()

    award_points(
        wallet,
        "predeposit_early",
        proof=tx_hash,
        amount_usdc=verified_amount,
    )
    _refresh_participant_tiers(wallet)
    _evaluate_whale_capture(wallet)
    check_milestones()
    return {"ok": True, "wallet": wallet, "predeposit_usdc": float(total)}


def _refresh_participant_tiers(wallet: str) -> None:
    cfg = load_config()
    tiers = sorted(cfg.get("predeposit_tiers", []), key=lambda t: t.get("min_usdc", 0), reverse=True)
    with _conn() as db:
        row = db.execute(
            "SELECT predeposit_usdc FROM participants WHERE wallet=?", (wallet,)
        ).fetchone()
        if not row:
            return
        usdc = float(row["predeposit_usdc"] or 0)
        priority = 1.0
        for tier in tiers:
            if usdc >= float(tier.get("min_usdc", 0)):
                priority = float(tier.get("lbp_priority", 1.0))
                break
        db.execute(
            "UPDATE participants SET lbp_priority=?, updated_at=? WHERE wallet=?",
            (priority, _now(), wallet),
        )
        db.commit()


def _evaluate_social_whitelist(wallet: str) -> None:
    cfg = load_config()
    sw = cfg.get("social_whitelist", {})
    with _conn() as db:
        row = db.execute("SELECT * FROM participants WHERE wallet=?", (wallet,)).fetchone()
        if not row:
            return
        has_tw = bool((row["twitter"] or "").strip())
        has_fc = bool((row["farcaster"] or "").strip())
        verified = has_tw and has_fc
        mercenary = False
        if verified:
            bridge = db.execute(
                "SELECT 1 FROM points_ledger WHERE wallet=? AND action='bridge_usdc'",
                (wallet,),
            ).fetchone()
            onchain = db.execute(
                "SELECT 1 FROM points_ledger WHERE wallet=? AND action IN ('sinc_gateway_buy','hook_interaction')",
                (wallet,),
            ).fetchone()
            if not bridge and not onchain:
                mercenary = True
        mult = float(row["allocation_multiplier"] or 1.0)
        if mercenary:
            mult *= float(sw.get("mercenary_penalty", 0.5))
        db.execute(
            """UPDATE participants SET social_verified=?, mercenary_flag=?,
               allocation_multiplier=?, updated_at=? WHERE wallet=?""",
            (1 if verified else 0, 1 if mercenary else 0, round(mult, 4), _now(), wallet),
        )
        db.commit()


def _evaluate_defi_farmer(wallet: str, bridge_usdc: float) -> None:
    cfg = load_config()
    cap = cfg.get("capture", {})
    min_bridge = float(cap.get("farmer_bridge_min_usdc", 10000))
    cap_mult = float(cap.get("farmer_multiplier_cap", 1.1))
    with _conn() as db:
        row = db.execute("SELECT * FROM participants WHERE wallet=?", (wallet,)).fetchone()
        if not row:
            return
        score = float(row["farmer_score"] or 0)
        if bridge_usdc >= min_bridge:
            score += min(bridge_usdc / min_bridge, 5.0)
        mult = float(row["allocation_multiplier"] or 1.0)
        if score >= 1.0 and mult > cap_mult:
            mult = cap_mult
        db.execute(
            """UPDATE participants SET farmer_score=?, allocation_multiplier=?, updated_at=?
               WHERE wallet=?""",
            (round(score, 2), round(mult, 4), _now(), wallet),
        )
        db.commit()


def _evaluate_whale_capture(wallet: str) -> None:
    cfg = load_config()
    cap = cfg.get("capture", {})
    min_usdc = float(cap.get("whale_min_usdc", 5000))
    with _conn() as db:
        row = db.execute("SELECT predeposit_usdc FROM participants WHERE wallet=?", (wallet,)).fetchone()
        if row and float(row["predeposit_usdc"] or 0) >= min_usdc:
            db.execute(
                "UPDATE participants SET whale_tier=1, updated_at=? WHERE wallet=?",
                (_now(), wallet),
            )
            db.commit()


def _metric_value(metric: str) -> float:
    init_campaign_db()
    with _conn() as db:
        if metric == "predeposit_usdc_total":
            return float(
                db.execute("SELECT COALESCE(SUM(amount_usdc),0) FROM predeposits").fetchone()[0]
            )
        if metric == "qualified_wallets":
            return float(
                db.execute(
                    "SELECT COUNT(*) FROM participants WHERE total_points >= 100"
                ).fetchone()[0]
            )
        if metric == "social_whitelist_count":
            return float(
                db.execute(
                    "SELECT COUNT(*) FROM participants WHERE social_verified=1 AND mercenary_flag=0"
                ).fetchone()[0]
            )
        if metric == "sinc_buy_volume_usd":
            try:
                from sincor2.revenue_snapshot import fetch_orders_revenue

                rev = fetch_orders_revenue()
                return float(rev.get("completed_revenue_usd", 0))
            except Exception:
                return 0.0
    return 0.0


def check_milestones() -> list[dict[str, Any]]:
    init_campaign_db()
    cfg = load_config()
    newly: list[dict[str, Any]] = []
    for ms in cfg.get("milestones", []):
        mid = ms["id"]
        with _conn() as db:
            if db.execute(
                "SELECT 1 FROM milestone_log WHERE milestone_id=?", (mid,)
            ).fetchone():
                continue
        current = _metric_value(ms["metric"])
        target = float(ms["target"])
        if current >= target:
            now = _now()
            with _conn() as db:
                db.execute(
                    "INSERT INTO milestone_log (milestone_id, achieved_at, value_at_achieve) VALUES (?,?,?)",
                    (mid, now, current),
                )
                db.commit()
            newly.append({**ms, "achieved_at": now, "current": current})
            logger.info("[CAMPAIGN] Milestone achieved: %s (%.2f >= %.2f)", mid, current, target)
    return newly


def active_prereq_waves() -> list[dict[str, Any]]:
    cfg = load_config()
    achieved = set()
    with _conn() as db:
        for row in db.execute("SELECT milestone_id FROM milestone_log"):
            achieved.add(row["milestone_id"])

    active: list[dict[str, Any]] = []
    for wave in cfg.get("prereq_cascade", []):
        reqs = wave.get("requires", [])
        if all(r in achieved for r in reqs):
            active.append({**wave, "status": "unlocked"})
        else:
            missing = [r for r in reqs if r not in achieved]
            active.append({**wave, "status": "locked", "missing": missing})
    return active


def campaign_status() -> dict[str, Any]:
    init_campaign_db()
    cfg = load_config()
    check_milestones()

    with _conn() as db:
        participants = db.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
        whales = db.execute("SELECT COUNT(*) FROM participants WHERE whale_tier=1").fetchone()[0]
        social_ok = db.execute(
            "SELECT COUNT(*) FROM participants WHERE social_verified=1 AND mercenary_flag=0"
        ).fetchone()[0]
        pre_total = float(
            db.execute("SELECT COALESCE(SUM(amount_usdc),0) FROM predeposits").fetchone()[0]
        )
        achieved = [
            dict(row)
            for row in db.execute("SELECT * FROM milestone_log ORDER BY achieved_at")
        ]

    milestones = []
    for ms in cfg.get("milestones", []):
        current = _metric_value(ms["metric"])
        milestones.append({
            **ms,
            "current": round(current, 2),
            "pct": round(min(100.0, current / float(ms["target"]) * 100), 1) if ms["target"] else 0,
            "achieved": any(a["milestone_id"] == ms["id"] for a in achieved),
        })

    return {
        "ok": True,
        "campaign_id": cfg.get("campaign_id"),
        "phase": campaign_phase(),
        "predeposit_opens": cfg.get("predeposit_opens"),
        "tge_date": cfg.get("tge_date"),
        "treasury": cfg.get("treasury"),
        "usdc_base": cfg.get("usdc_base"),
        "participants": participants,
        "predeposit_usdc_total": round(pre_total, 2),
        "social_whitelist_qualified": social_ok,
        "whale_count": whales,
        "milestones": milestones,
        "prereq_waves": active_prereq_waves(),
        "points_actions": list(cfg.get("points_actions", {}).keys()),
        "predeposit_tiers": cfg.get("predeposit_tiers", []),
        "announce_channels": cfg.get("announce_channels", []),
    }


def leaderboard(limit: int = 50) -> list[dict[str, Any]]:
    init_campaign_db()
    with _conn() as db:
        rows = db.execute(
            """SELECT wallet, total_points, allocation_multiplier, lbp_priority,
                      predeposit_usdc, social_verified, whale_tier, mercenary_flag
               FROM participants ORDER BY total_points DESC, predeposit_usdc DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def whale_capture_list() -> list[dict[str, Any]]:
    init_campaign_db()
    with _conn() as db:
        rows = db.execute(
            """SELECT wallet, predeposit_usdc, lbp_priority, allocation_multiplier, email
               FROM participants WHERE whale_tier=1 ORDER BY predeposit_usdc DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def write_announcement_draft(milestone: dict[str, Any]) -> Path:
    """Draft integration announcement for review queue."""
    out_dir = data_dir() / "launch_campaign" / "announcements"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    channels = ", ".join(cfg.get("announce_channels", [])[:6])
    title = f"KPI milestone unlocked: {milestone.get('title', '')}"
    body = (
        f"🎯 {milestone.get('title')} — ACHIEVED\n\n"
        f"Reward: {milestone.get('reward', '')}\n"
        f"Allocation pool: +{milestone.get('allocation_pool_pct', 0)}%\n\n"
        f"SINC Genesis Campaign · TGE {cfg.get('tge_date', '')[:10]}\n"
        f"Pre-deposit: {cfg.get('campaign_url', '')}\n"
        f"Buy: {cfg.get('buy', '')}\n\n"
        f"Post to: {channels}\n"
        f"Verified contracts · $1.50 USDC floor · 42-agent platform on Base"
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"announce_{milestone.get('id', 'ms')}_{stamp}.md"
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")

    try:
        root = str(project_root())
        import sys

        if root not in sys.path:
            sys.path.insert(0, root)
        from launch_content_engine.review_queue import enqueue

        enqueue("campaign_milestone", "twitter", body, title=title, meta={"milestone_id": milestone.get("id")})
    except Exception as exc:
        logger.warning("[CAMPAIGN] review queue enqueue failed: %s", exc)

    return path


def draft_campaign_kpi_post() -> tuple[str, str]:
    """Social draft summarizing live campaign KPIs for content rotation."""
    status = campaign_status()
    lines = [
        "SINC Genesis Campaign — live KPIs",
        f"Phase: {status.get('phase')}",
        f"Pre-deposit TVL: ${status.get('predeposit_usdc_total', 0):,.0f}",
        f"Qualified wallets: {status.get('social_whitelist_qualified', 0)}",
        f"Whales: {status.get('whale_count', 0)}",
        "",
        "Milestones:",
    ]
    for ms in status.get("milestones", [])[:4]:
        mark = "✓" if ms.get("achieved") else f"{ms.get('pct', 0)}%"
        lines.append(f"• {ms.get('title')} — {mark}")
    lines.extend([
        "",
        f"Pre-deposit opens Jul 1 · TGE Jul 7",
        f"{status.get('campaign_id', 'sinc-tge-2026')} · getsincor.com/launch/campaign",
    ])
    body = "\n".join(lines)
    return "Campaign KPI snapshot", body


def run_campaign_ops() -> dict[str, Any]:
    """Milestone check + announcement drafts (scheduler / swarm)."""
    init_campaign_db()
    newly = check_milestones()
    drafts = []
    for ms in newly:
        path = write_announcement_draft(ms)
        drafts.append({"milestone_id": ms["id"], "path": str(path)})
    status = campaign_status()
    return {
        "ok": True,
        "new_milestones": len(newly),
        "drafts": drafts,
        "predeposit_usdc_total": status.get("predeposit_usdc_total"),
        "phase": status.get("phase"),
        "whale_count": status.get("whale_count"),
    }