"""SQLite persistence for the CHROMA shop — leads, quotes, bookings, outbound."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .config import DEFAULT_SHOP, default_packages


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> Path:
    override = os.environ.get("CHROMA_DB_PATH", "").strip()
    if override:
        path = Path(override)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    try:
        from sincor2.data_paths import data_dir

        base = data_dir()
    except Exception:
        base = Path(__file__).resolve().parents[2] / "data"
        base.mkdir(parents=True, exist_ok=True)
    return base / "chroma.db"


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    for key in (
        "vehicle_json",
        "ranking_json",
        "payload_json",
        "quote_json",
        "packages_json",
    ):
        if key in data and data[key]:
            try:
                data[key.replace("_json", "")] = json.loads(data[key])
            except json.JSONDecodeError:
                data[key.replace("_json", "")] = {}
    if "sent" in data:
        data["sent"] = bool(data["sent"])
    if "live" in data:
        data["live"] = bool(data["live"])
    return data


class ChromaStore:
    """Single-shop store. Thread-safe enough for Flask + pytest."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS leads (
                    lead_id TEXT PRIMARY KEY,
                    name TEXT,
                    source TEXT,
                    email TEXT,
                    phone TEXT,
                    message TEXT,
                    vehicle_json TEXT,
                    intent_score REAL,
                    band TEXT,
                    status TEXT,
                    ranking_json TEXT,
                    payload_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS quotes (
                    quote_id TEXT PRIMARY KEY,
                    lead_id TEXT,
                    package_id TEXT,
                    vehicle_size TEXT,
                    total REAL,
                    deposit REAL,
                    sent INTEGER DEFAULT 0,
                    payload_json TEXT,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id TEXT PRIMARY KEY,
                    lead_id TEXT,
                    quote_id TEXT,
                    calendly_url TEXT,
                    event TEXT,
                    slot TEXT,
                    status TEXT,
                    payload_json TEXT,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS outbound (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT,
                    channel TEXT,
                    kind TEXT,
                    to_addr TEXT,
                    subject TEXT,
                    body TEXT,
                    status TEXT,
                    toa_score REAL,
                    toa_note TEXT,
                    live INTEGER DEFAULT 0,
                    payload_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id TEXT,
                    kind TEXT,
                    detail TEXT,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS books (
                    id TEXT PRIMARY KEY,
                    direction TEXT,
                    amount REAL,
                    method TEXT,
                    note TEXT,
                    lead_id TEXT,
                    created_at TEXT
                );
                """
            )
            conn.commit()
        self._ensure_default_settings()

    def _ensure_default_settings(self) -> None:
        current = self.get_settings()
        if current.get("shop_name"):
            return
        seed = dict(DEFAULT_SHOP)
        seed["packages"] = default_packages()
        self.save_settings(seed)

    def wipe(self) -> None:
        with self._lock, self._connect() as conn:
            for table in ("events", "outbound", "bookings", "quotes", "leads", "settings", "books"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        self._ensure_default_settings()

    # ------------------------------------------------------------------ leads
    def upsert_lead(self, lead: Dict[str, Any]) -> Dict[str, Any]:
        ranking = lead.get("ranking") or {}
        record = {
            "lead_id": lead.get("lead_id") or f"LD-{uuid4().hex[:8].upper()}",
            "name": lead.get("name"),
            "source": lead.get("source"),
            "email": (lead.get("contact") or {}).get("email") or lead.get("email"),
            "phone": (lead.get("contact") or {}).get("phone") or lead.get("phone"),
            "message": lead.get("message"),
            "vehicle_json": json.dumps(lead.get("vehicle") or {}),
            "intent_score": float((ranking.get("score") if ranking else lead.get("intent_score")) or 0),
            "band": ranking.get("band") or lead.get("band") or "nurture",
            "status": lead.get("pipeline_stage") or lead.get("status") or "new",
            "ranking_json": json.dumps(ranking),
            "payload_json": json.dumps(lead),
            "created_at": lead.get("ingested_at") or lead.get("created_at") or _now(),
            "updated_at": _now(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO leads (
                    lead_id, name, source, email, phone, message, vehicle_json,
                    intent_score, band, status, ranking_json, payload_json,
                    created_at, updated_at
                ) VALUES (
                    :lead_id, :name, :source, :email, :phone, :message, :vehicle_json,
                    :intent_score, :band, :status, :ranking_json, :payload_json,
                    :created_at, :updated_at
                )
                ON CONFLICT(lead_id) DO UPDATE SET
                    name=excluded.name,
                    source=excluded.source,
                    email=excluded.email,
                    phone=excluded.phone,
                    message=excluded.message,
                    vehicle_json=excluded.vehicle_json,
                    intent_score=excluded.intent_score,
                    band=excluded.band,
                    status=excluded.status,
                    ranking_json=excluded.ranking_json,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                record,
            )
            conn.commit()
        return self.get_lead(record["lead_id"]) or record

    def get_lead(self, lead_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM leads WHERE lead_id=?", (lead_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def list_leads(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM leads ORDER BY intent_score DESC, created_at DESC"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def set_lead_status(self, lead_id: str, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE leads SET status=?, updated_at=? WHERE lead_id=?",
                (status, _now(), lead_id),
            )
            conn.commit()

    # ----------------------------------------------------------------- quotes
    def save_quote(self, quote: Dict[str, Any], lead_id: Optional[str] = None) -> Dict[str, Any]:
        quote_id = quote.get("quote_id") or f"QT-{uuid4().hex[:8].upper()}"
        record = {
            "quote_id": quote_id,
            "lead_id": lead_id or quote.get("lead_id"),
            "package_id": quote.get("package_id"),
            "vehicle_size": quote.get("vehicle_size"),
            "total": float(quote.get("total") or 0),
            "deposit": float(quote.get("deposit") or 0),
            "sent": 1 if quote.get("sent") else 0,
            "payload_json": json.dumps({**quote, "quote_id": quote_id}),
            "created_at": quote.get("created_at") or _now(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO quotes (
                    quote_id, lead_id, package_id, vehicle_size, total, deposit,
                    sent, payload_json, created_at
                ) VALUES (
                    :quote_id, :lead_id, :package_id, :vehicle_size, :total, :deposit,
                    :sent, :payload_json, :created_at
                )
                ON CONFLICT(quote_id) DO UPDATE SET
                    sent=excluded.sent,
                    payload_json=excluded.payload_json
                """,
                record,
            )
            conn.commit()
        saved = dict(quote)
        saved["quote_id"] = quote_id
        saved["lead_id"] = record["lead_id"]
        saved["sent"] = bool(record["sent"])
        return saved

    def list_quotes(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM quotes ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_quote(self, quote_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM quotes WHERE quote_id=?", (quote_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def mark_quote_sent(self, quote_id: str, sent: bool = True) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE quotes SET sent=? WHERE quote_id=?", (1 if sent else 0, quote_id))
            conn.commit()

    # --------------------------------------------------------------- bookings
    def save_booking(self, booking: Dict[str, Any]) -> Dict[str, Any]:
        booking_id = booking.get("booking_id") or f"BK-{uuid4().hex[:8].upper()}"
        record = {
            "booking_id": booking_id,
            "lead_id": booking.get("lead_id"),
            "quote_id": booking.get("quote_id"),
            "calendly_url": booking.get("calendly_url"),
            "event": booking.get("event"),
            "slot": booking.get("slot") or booking.get("preferred_slot"),
            "status": booking.get("status")
            or ("weather_hold" if booking.get("weather_hold") else "link_ready"),
            "payload_json": json.dumps({**booking, "booking_id": booking_id}),
            "created_at": booking.get("created_at") or _now(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bookings (
                    booking_id, lead_id, quote_id, calendly_url, event, slot,
                    status, payload_json, created_at
                ) VALUES (
                    :booking_id, :lead_id, :quote_id, :calendly_url, :event, :slot,
                    :status, :payload_json, :created_at
                )
                ON CONFLICT(booking_id) DO UPDATE SET
                    slot=excluded.slot,
                    status=excluded.status,
                    payload_json=excluded.payload_json
                """,
                record,
            )
            conn.commit()
        saved = dict(booking)
        saved["booking_id"] = booking_id
        saved["status"] = record["status"]
        return saved

    def list_bookings(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM bookings ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_booking(self, booking_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bookings WHERE booking_id=?", (booking_id,)
            ).fetchone()
        return _row_to_dict(row) if row else None

    # --------------------------------------------------------------- outbound
    def save_outbound(self, item: Dict[str, Any]) -> Dict[str, Any]:
        item_id = item.get("id") or f"OB-{uuid4().hex[:8].upper()}"
        record = {
            "id": item_id,
            "lead_id": item.get("lead_id"),
            "channel": item.get("channel") or "email",
            "kind": item.get("kind") or "draft",
            "to_addr": item.get("to") or item.get("to_addr"),
            "subject": item.get("subject"),
            "body": item.get("body") or "",
            "status": item.get("status") or "pending_approval",
            "toa_score": float(item.get("toa_score") or 0),
            "toa_note": item.get("toa_note"),
            "live": 1 if item.get("live") else 0,
            "payload_json": json.dumps({**item, "id": item_id}),
            "created_at": item.get("created_at") or _now(),
            "updated_at": _now(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO outbound (
                    id, lead_id, channel, kind, to_addr, subject, body, status,
                    toa_score, toa_note, live, payload_json, created_at, updated_at
                ) VALUES (
                    :id, :lead_id, :channel, :kind, :to_addr, :subject, :body, :status,
                    :toa_score, :toa_note, :live, :payload_json, :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    body=excluded.body,
                    subject=excluded.subject,
                    status=excluded.status,
                    toa_score=excluded.toa_score,
                    toa_note=excluded.toa_note,
                    live=excluded.live,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                record,
            )
            conn.commit()
        return self.get_outbound(item_id) or record

    def get_outbound(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM outbound WHERE id=?", (item_id,)).fetchone()
        if not row:
            return None
        data = _row_to_dict(row)
        data["to"] = data.get("to_addr")
        return data

    def list_outbound(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM outbound WHERE status=? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM outbound ORDER BY created_at DESC"
                ).fetchall()
        out = []
        for row in rows:
            data = _row_to_dict(row)
            data["to"] = data.get("to_addr")
            out.append(data)
        return out

    def update_outbound(self, item_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        item = self.get_outbound(item_id)
        if not item:
            return None
        item.update(fields)
        item["updated_at"] = _now()
        return self.save_outbound(item)

    # ----------------------------------------------------------------- events
    def add_event(self, lead_id: Optional[str], kind: str, detail: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events (lead_id, kind, detail, created_at) VALUES (?,?,?,?)",
                (lead_id, kind, detail, _now()),
            )
            conn.commit()

    def timeline(self, lead_id: str) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE lead_id=? ORDER BY id ASC",
                (lead_id,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # --------------------------------------------------------------- settings
    def get_settings(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        data: Dict[str, Any] = {}
        for row in rows:
            try:
                data[row["key"]] = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                data[row["key"]] = row["value"]
        return data

    def save_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            for key, value in settings.items():
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )
            conn.commit()
        return self.get_settings()

    # ----------------------------------------------------------------- books
    def add_book(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        item_id = entry.get("id") or f"LG-{uuid4().hex[:8].upper()}"
        direction = "out" if str(entry.get("direction") or "in").lower() in {"out", "expense", "spent"} else "in"
        record = {
            "id": item_id,
            "direction": direction,
            "amount": abs(float(entry.get("amount") or 0)),
            "method": entry.get("method") or "cash",
            "note": entry.get("note") or "",
            "lead_id": entry.get("lead_id"),
            "created_at": entry.get("created_at") or _now(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO books (id, direction, amount, method, note, lead_id, created_at)
                VALUES (:id, :direction, :amount, :method, :note, :lead_id, :created_at)
                """,
                record,
            )
            conn.commit()
        return record

    def list_books(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM books ORDER BY created_at DESC, id DESC").fetchall()
        return [_row_to_dict(r) for r in rows]

    def delete_book(self, item_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM books WHERE id=?", (item_id,))
            conn.commit()

    def books_totals(self) -> Dict[str, float]:
        with self._lock, self._connect() as conn:
            incoming = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM books WHERE direction='in'"
            ).fetchone()[0]
            outgoing = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM books WHERE direction='out'"
            ).fetchone()[0]
        incoming = float(incoming or 0)
        outgoing = float(outgoing or 0)
        return {
            "money_in": round(incoming, 2),
            "money_out": round(outgoing, 2),
            "net": round(incoming - outgoing, 2),
        }

    def counts(self) -> Dict[str, int]:
        with self._lock, self._connect() as conn:
            leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            quotes = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
            bookings = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM outbound WHERE status='pending_approval'"
            ).fetchone()[0]
            books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        return {
            "leads": int(leads),
            "quotes": int(quotes),
            "bookings": int(bookings),
            "pending_sends": int(pending),
            "books": int(books),
        }


_STORE: Optional[ChromaStore] = None
_STORE_LOCK = threading.Lock()


def get_store() -> ChromaStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ChromaStore()
        return _STORE


def reset_store(db_path: Optional[Path | str] = None) -> ChromaStore:
    global _STORE
    with _STORE_LOCK:
        _STORE = ChromaStore(db_path=db_path)
        return _STORE
