"""
Generator-based LLM streaming for A2A message/stream.

Prefers Anthropic `client.messages.stream()` / `text_stream` so tokens
leave the process as they arrive. When no API key is configured, callers
chunk a completed string so SSE clients still see append/lastChunk events
instead of a single blob.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Generator, Iterable, Optional

logger = logging.getLogger("sincor.llm_stream")

DEFAULT_CHUNK_CHARS = 28


def has_anthropic() -> bool:
    return bool((os.getenv("ANTHROPIC_API_KEY") or "").strip())


def stream_claude(
    prompt: str,
    *,
    system: Optional[str] = None,
    max_tokens: int = 2048,
    model: Optional[str] = None,
) -> Generator[str, None, None]:
    """
    Yield text deltas from Anthropic `messages.stream()`.

    Raises if the client cannot be constructed; yields nothing if the API
    key is missing so callers can fall through to a stub.
    """
    if not has_anthropic():
        return
        yield  # pragma: no cover — makes this a generator even on early return

    from sincor2.cortecs_core import ClaudeClient

    client = ClaudeClient()
    yield from client.stream_sync(
        prompt,
        max_tokens=max_tokens,
        system=system,
        model=model,
    )


def chunk_text(
    text: str,
    *,
    size: int = DEFAULT_CHUNK_CHARS,
    delay: float = 0.0,
) -> Generator[str, None, None]:
    """Split a completed string into SSE-sized chunks (word-aware)."""
    if not text:
        return
    size = max(8, int(size))
    words = text.split(" ")
    buf: list[str] = []
    n = 0
    for word in words:
        extra = len(word) + (1 if buf else 0)
        if buf and n + extra > size:
            yield " ".join(buf)
            if delay:
                time.sleep(delay)
            buf = [word]
            n = len(word)
        else:
            buf.append(word)
            n += extra
    if buf:
        yield " ".join(buf)


def iter_chunks(parts: Iterable[str]) -> Generator[str, None, None]:
    for part in parts:
        if part:
            yield part


def skill_system_prompt(skill_id: str) -> str:
    return (
        "You are a SINCOR swarm skill running over the A2A protocol. "
        f"Skill id: {skill_id}. Answer as the specialist for that skill. "
        "Be concrete, structured, and skip filler."
    )
