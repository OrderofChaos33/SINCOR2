"""Swarm Command board state store (stdlib only).

JSON-file persistence behind a ``threading.Lock`` around every
read-modify-write cycle. Holds the orchestration state for the Swarm
Command dashboard / MCP layer: lanes + cards, daily stacks, dispatches /
assignments, and discussion threads.

Never touches money paths, auth systems, ledgers, or production.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

DEFAULT_PATH = "data/swarm_board.json"

LANES = ("brainstorm", "in_design", "approved", "parked")

STACK_STATUSES = ("proposed", "approved", "dispatched")
DISPATCH_STATUSES = ("assigned", "in_progress", "done")


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return secrets.token_hex(6)  # 12 hex chars


def _require_nonempty_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non-empty string" % name)
    return value.strip()


class BoardStore:
    """Thread-safe JSON-file-backed Swarm Command board store."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.environ.get("SWARM_BOARD_PATH", DEFAULT_PATH)
        self._lock = threading.Lock()
        with self._lock:
            state = self._load_locked()
            if state is None:
                state = self._fresh_state()
                self._write_locked(state)

    # -- persistence helpers (caller must hold self._lock) -----------------
    def _fresh_state(self) -> Dict[str, Any]:
        return {
            "cards": [],
            "stacks": [],
            "assignments": [],
            "discussions": {},
        }

    def _load_locked(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if not isinstance(state, dict):
            return None
        state.setdefault("cards", [])
        state.setdefault("stacks", [])
        state.setdefault("assignments", [])
        state.setdefault("discussions", {})
        return state

    def _write_locked(self, state: Dict[str, Any]) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def _modify(self, fn):
        with self._lock:
            state = self._load_locked()
            if state is None:
                state = self._fresh_state()
            result = fn(state)
            self._write_locked(state)
            return result

    def _read(self, fn):
        with self._lock:
            state = self._load_locked()
            if state is None:
                state = self._fresh_state()
            return fn(state)

    # -- cards -----------------------------------------------------------
    def lanes_read(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return {lane: [cards]} for every lane."""
        def _do(state):
            out: Dict[str, List[Dict[str, Any]]] = {lane: [] for lane in LANES}
            for card in state["cards"]:
                out.setdefault(card.get("lane"), []).append(dict(card))
            return out
        return self._read(_do)

    def card_add(self, lane: str, title: str, body: str, owner: str) -> Dict[str, Any]:
        lane = _require_nonempty_str("lane", lane)
        if lane not in LANES:
            raise ValueError("unknown lane %r; known lanes: %s" % (lane, ", ".join(LANES)))
        title = _require_nonempty_str("title", title)
        body = _require_nonempty_str("body", body)
        owner = _require_nonempty_str("owner", owner)
        card = {
            "id": _new_id(),
            "lane": lane,
            "title": title,
            "body": body,
            "owner": owner,
            "comments": [],
            "created_at": _now(),
        }

        def _do(state):
            state["cards"].append(card)
            return dict(card)
        return self._modify(_do)

    def _find_card(self, state, card_id: str) -> Dict[str, Any]:
        for card in state["cards"]:
            if card["id"] == card_id:
                return card
        raise ValueError("unknown card id %r" % (card_id,))

    def card_move(self, card_id: str, to_lane: str) -> Dict[str, Any]:
        card_id = _require_nonempty_str("card_id", card_id)
        to_lane = _require_nonempty_str("to_lane", to_lane)
        if to_lane not in LANES:
            raise ValueError("unknown lane %r; known lanes: %s" % (to_lane, ", ".join(LANES)))

        def _do(state):
            card = self._find_card(state, card_id)
            card["lane"] = to_lane
            return dict(card)
        return self._modify(_do)

    def card_comment(self, card_id: str, author: str, text: str) -> Dict[str, Any]:
        card_id = _require_nonempty_str("card_id", card_id)
        author = _require_nonempty_str("author", author)
        text = _require_nonempty_str("text", text)

        def _do(state):
            card = self._find_card(state, card_id)
            card.setdefault("comments", []).append(
                {"author": author, "text": text, "ts": _now()}
            )
            return dict(card)
        return self._modify(_do)

    # -- daily stacks ----------------------------------------------------
    def _find_stack(self, state, stack_id: str) -> Dict[str, Any]:
        for stack in state["stacks"]:
            if stack["id"] == stack_id:
                return stack
        raise ValueError("unknown stack id %r" % (stack_id,))

    def stack_propose(
        self, items: List[Dict[str, Any]], proposed_by: str
    ) -> Dict[str, Any]:
        if not isinstance(items, list) or not items:
            raise ValueError("items must be a non-empty list")
        clean_items = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError("items[%d] must be an object" % i)
            clean_items.append(
                {
                    "title": _require_nonempty_str("items[%d].title" % i, item.get("title")),
                    "detail": _require_nonempty_str("items[%d].detail" % i, item.get("detail")),
                    "owner": _require_nonempty_str("items[%d].owner" % i, item.get("owner")),
                }
            )
        proposed_by = _require_nonempty_str("proposed_by", proposed_by)
        stack = {
            "id": _new_id(),
            "items": clean_items,
            "status": "proposed",
            "proposed_by": proposed_by,
            "approved_by": None,
            "created_at": _now(),
            "decided_at": None,
        }

        def _do(state):
            state["stacks"].append(stack)
            return dict(stack)
        return self._modify(_do)

    def stack_approve(self, stack_id: str, approver: str) -> Dict[str, Any]:
        stack_id = _require_nonempty_str("stack_id", stack_id)
        approver = _require_nonempty_str("approver", approver)

        def _do(state):
            stack = self._find_stack(state, stack_id)
            if stack["status"] != "proposed":
                raise ValueError(
                    "stack %r is already %s; approval is one-shot" % (stack_id, stack["status"])
                )
            stack["status"] = "approved"
            stack["approved_by"] = approver
            stack["decided_at"] = _now()
            return dict(stack)
        return self._modify(_do)

    # -- dispatches / assignments ----------------------------------------
    def _find_assignment(self, state, assignment_id: str) -> Dict[str, Any]:
        for a in state["assignments"]:
            if a["id"] == assignment_id:
                return a
        raise ValueError("unknown assignment id %r" % (assignment_id,))

    def dispatch_assign(self, stack_id: str) -> List[Dict[str, Any]]:
        stack_id = _require_nonempty_str("stack_id", stack_id)

        def _do(state):
            stack = self._find_stack(state, stack_id)
            if stack["status"] != "approved":
                raise ValueError(
                    "stack %r has status %s; only approved stacks can dispatch"
                    % (stack_id, stack["status"])
                )
            created = []
            for item in stack["items"]:
                a = {
                    "id": _new_id(),
                    "stack_id": stack_id,
                    "title": item["title"],
                    "owner": item["owner"],
                    "status": "assigned",
                    "result": None,
                    "updated_at": _now(),
                }
                state["assignments"].append(a)
                created.append(dict(a))
            stack["status"] = "dispatched"
            stack["decided_at"] = _now()
            return created
        return self._modify(_do)

    def results_loopback(self, assignment_id: str, result: str) -> Dict[str, Any]:
        assignment_id = _require_nonempty_str("assignment_id", assignment_id)
        result = _require_nonempty_str("result", result)

        def _do(state):
            a = self._find_assignment(state, assignment_id)
            a["status"] = "done"
            a["result"] = result
            a["updated_at"] = _now()
            return dict(a)
        return self._modify(_do)

    # -- discussions ------------------------------------------------------
    def discussion_post(self, topic: str, author: str, text: str) -> Dict[str, Any]:
        topic = _require_nonempty_str("topic", topic)
        author = _require_nonempty_str("author", author)
        text = _require_nonempty_str("text", text)

        def _do(state):
            threads = state.setdefault("discussions", {})
            thread = threads.setdefault(topic, {"topic": topic, "posts": []})
            post = {"author": author, "text": text, "ts": _now()}
            thread["posts"].append(post)
            return {"topic": topic, "posts": [dict(p) for p in thread["posts"]]}
        return self._modify(_do)

    def discussion_read(self, topic: Optional[str] = None) -> Any:
        def _do(state):
            threads = state.get("discussions", {})
            if topic is None:
                return {
                    t: {"topic": t, "posts": [dict(p) for p in th["posts"]]}
                    for t, th in threads.items()
                }
            _require_nonempty_str("topic", topic)
            th = threads.get(topic)
            if th is None:
                return {"topic": topic, "posts": []}
            return {"topic": topic, "posts": [dict(p) for p in th["posts"]]}
        return self._read(_do)
