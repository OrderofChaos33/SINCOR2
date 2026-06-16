"""Rotate agent persona spotlights — product story, not price hype."""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PERSONAS = ROOT / "agent_personas.json"
TOPICS = ROOT / "content_topics.json"


def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def pick_persona() -> dict:
    personas = _load_json(PERSONAS)
    return random.choice(personas)


def draft_spotlight() -> tuple[str, str]:
    p = pick_persona()
    topics = _load_json(TOPICS)
    topic = random.choice(topics["agent_spotlight_topics"])
    title = f"Agent spotlight: {p['name']}"
    body = (
        f"🤖 {p['name']} ({p['category']})\n\n"
        f"Task completed: {p['task']}\n"
        f"Related capability: {topic}\n\n"
        f"The SINCOR swarm isn't one chatbot — it's {len(_load_json(PERSONAS))}+ specialized agents "
        f"coordinating on real ops.\n\n"
        f"Explore the swarm → https://getsincor.com\n"
        f"Hold a piece (self-serve, no sales call) → https://getsincor.com/sinc\n"
        f"Refer buyers, earn 3% on-chain → https://getsincor.com/refer"
    )
    return title, body


def draft_build_log() -> tuple[str, str]:
    title = "SINCOR build log — swarm ops"
    body = (
        "This week the ops agents ran:\n"
        "• Hook fill keeper (withdraw USDC from filled limit orders)\n"
        "• Buy watcher on the live bonding curve\n"
        "• Launch content engine → human review queue (you approve, agents draft)\n"
        "• A2A agent card for protocol discovery\n\n"
        "No price talk — just shipping.\n"
        "https://getsincor.com/sinc"
    )
    return title, body


def draft_referral_cta() -> tuple[str, str]:
    title = "SINC referral — automated promoter payouts"
    body = (
        "You don't need to know the founder to earn on SINC buys.\n\n"
        "Connect a wallet at https://getsincor.com/refer — your address becomes the referral ID.\n"
        "When someone buys through your link, the bonding curve pays you **3% in the same transaction**.\n\n"
        "Contracts enforce it. No invoice. No DMing a founder who hates sales."
    )
    return title, body