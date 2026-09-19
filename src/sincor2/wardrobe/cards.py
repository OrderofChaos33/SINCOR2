"""Public roster cards. Accepts wardrobe identity docs and legacy agent YAML."""

from __future__ import annotations

from html import escape
from typing import Any

from sincor2.wardrobe.trust import trust_score

ROSTER_PUBLIC = 43


def _ident(doc: dict[str, Any]) -> dict[str, str]:
    ident = doc.get("identity") if isinstance(doc.get("identity"), dict) else {}
    return {
        "id": str(ident.get("id") or doc.get("id") or ""),
        "name": str(ident.get("name") or doc.get("name") or ""),
        "archetype": str(ident.get("archetype") or doc.get("archetype") or ""),
        "status": str(ident.get("status") or doc.get("status") or "WardrobeDraft"),
    }


def _trust(doc: dict[str, Any]) -> int:
    metrics = doc.get("metrics") if isinstance(doc.get("metrics"), dict) else {}
    score, _ = trust_score(metrics)
    return int(score)


def swarm_index(docs: list[dict[str, Any]]) -> dict[str, Any]:
    agents = []
    for doc in docs or []:
        if not isinstance(doc, dict):
            continue
        row = _ident(doc)
        if not row["id"]:
            continue
        agents.append({**row, "trust": _trust(doc)})
    return {
        "agents": agents,
        "loaded": len(agents),
        "rosterPublic": ROSTER_PUBLIC,
    }


def machine_card(doc: dict[str, Any]) -> dict[str, Any]:
    row = _ident(doc)
    return {
        "id": row["id"],
        "name": row["name"],
        "archetype": row["archetype"],
        "status": row["status"],
        "trust": _trust(doc),
        "skills": doc.get("specializations") or (doc.get("skills") or []),
    }


def human_card_html(doc: dict[str, Any]) -> str:
    card = machine_card(doc)
    skills = ", ".join(escape(str(s)) for s in (card.get("skills") or [])[:12]) or "-"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"<title>{escape(card['name'] or card['id'])}</title>"
        "<style>body{font-family:Inter,system-ui;margin:40px;color:#1A1A1A;max-width:720px}"
        "a{color:#0E6B6B}</style></head><body>"
        "<p><a href='/agents'>Roster</a></p>"
        f"<h1>{escape(card['name'] or card['id'])}</h1>"
        f"<p>{escape(card['archetype'])} · {escape(card['status'])} · trust {int(card['trust'])}</p>"
        f"<p>Skills: {skills}</p>"
        "</body></html>"
    )
