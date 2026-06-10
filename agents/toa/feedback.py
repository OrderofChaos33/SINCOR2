from __future__ import annotations

"""Feedback loop agent for the TOA pipeline.

:class:`RollingFeedbackAgent` maintains a bounded ring-buffer of execution
events and computes an aggregated summary that is injected into the next
forecast context.  This enables recursive self-improvement: real-world
outcomes continuously refine the forecaster's priors.

Event sources include:
* On-chain settlement events (SINC/AXIOM transfers, liquidity events).
* Vertical task outcomes (success/failure, latency, quality rating).
* Marketplace reputation deltas (trust score changes).
* Any other ``{"source": str, "timestamp": str, "payload": dict}`` event.
"""

import collections
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from .base import FeedbackAgent
from .config import TOAConfig


class RollingFeedbackAgent(FeedbackAgent):
    """Ring-buffer feedback agent with aggregate summary generation.

    The summary returned by :meth:`get_feedback_summary` is designed to be
    directly merged into the ``context`` dict passed to
    :meth:`~agents.toa.base.ForecasterAgent.forecast`.

    Parameters
    ----------
    config:
        TOA configuration.
    """

    agent_name = "rolling_feedback"

    def __init__(self, config: Optional[TOAConfig] = None) -> None:
        super().__init__(config=config)
        max_events = self.config.feedback_buffer_size
        self._buffer: Deque[Dict[str, Any]] = collections.deque(maxlen=max_events)
        self._success_count: int = 0
        self._failure_count: int = 0
        self._quality_sum: float = 0.0
        self._quality_count: int = 0
        self._source_counts: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # FeedbackAgent interface
    # ------------------------------------------------------------------

    def ingest(self, event: Dict[str, Any]) -> None:
        """Record a new execution result or external signal.

        Parameters
        ----------
        event:
            Must contain ``"source"`` (str) and ``"timestamp"`` (ISO-8601 or
            empty).  An optional ``"payload"`` dict may contain:

            * ``"success"`` (bool) — used for success/failure counting.
            * ``"quality_rating"`` (float 0–5) — used for quality EMA.
            * Any additional keys are stored verbatim.
        """
        enriched = dict(event)
        if "timestamp" not in enriched or not enriched["timestamp"]:
            enriched["timestamp"] = datetime.now(timezone.utc).isoformat()

        self._buffer.append(enriched)

        payload: Dict[str, Any] = enriched.get("payload", {})
        if "success" in payload:
            if payload["success"]:
                self._success_count += 1
            else:
                self._failure_count += 1
        if "quality_rating" in payload:
            self._quality_sum += float(payload["quality_rating"])
            self._quality_count += 1

        source = enriched.get("source", "unknown")
        self._source_counts[source] = self._source_counts.get(source, 0) + 1

        self._log(
            "debug",
            "feedback ingested",
            source=source,
            buffer_size=len(self._buffer),
        )

    def get_feedback_summary(self) -> Dict[str, Any]:
        """Return an aggregated summary of all ingested feedback.

        The returned dict may be merged into a forecast context dict to give
        the forecaster a signal about recent execution quality.

        Returns
        -------
        Dict[str, Any]
            Keys:

            * ``"total_events"`` — total events ingested so far.
            * ``"success_rate"`` — proportion of successful outcomes (0–1).
            * ``"mean_quality"`` — mean quality rating normalised to [0, 1].
            * ``"source_distribution"`` — per-source event counts.
            * ``"recent_events"`` — last N events (N ≤ 10).
            * ``"feedback_signal"`` — composite 0–1 summary score.
        """
        total = self._success_count + self._failure_count
        success_rate = self._success_count / total if total > 0 else 0.5
        mean_quality = (
            (self._quality_sum / self._quality_count) / 5.0
            if self._quality_count > 0
            else 0.5
        )
        feedback_signal = round(0.6 * success_rate + 0.4 * mean_quality, 4)

        return {
            "total_events": len(self._buffer),
            "success_rate": round(success_rate, 4),
            "mean_quality": round(mean_quality, 4),
            "source_distribution": dict(self._source_counts),
            "recent_events": list(self._buffer)[-10:],
            "feedback_signal": feedback_signal,
        }

    def clear(self) -> None:
        """Reset the feedback buffer and all counters."""
        self._buffer.clear()
        self._success_count = 0
        self._failure_count = 0
        self._quality_sum = 0.0
        self._quality_count = 0
        self._source_counts = {}
        self._log("debug", "feedback buffer cleared")

    @property
    def event_count(self) -> int:
        """Current number of events in the rolling buffer."""
        return len(self._buffer)

    def get_recent_events(self, n: int = 10) -> List[Dict[str, Any]]:
        """Return the *n* most-recent ingested events."""
        return list(self._buffer)[-n:]
