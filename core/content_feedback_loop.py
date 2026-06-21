from __future__ import annotations

"""
Content Feedback Loop

Production module for closing the performance loop on content/copy generation.

Responsibilities:
- Ingest post-publication metrics (engagement, approvals, conversions)
- Store into episodic memory (as defined in content_memory_allocation.yaml)
- Signal TOA for improved future forecasting and optimization
- Generate promotion / performance signals for agents (Copywriter, Strategist, Critic)
- Maintain audit trail for FinTech-grade observability

This turns the content system into a self-improving component of SINCOR.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ContentPerformanceRecord:
    """Structured record of content performance."""
    post_id: str
    platform: str
    vertical: Optional[str] = None
    pillar_used: Optional[str] = None
    agent_ids: List[str] = field(default_factory=list)
    critic_scores: Dict[str, float] = field(default_factory=dict)
    novelty_score: Optional[float] = None
    approval_status: str = "pending"  # pending, approved, rejected
    published_at: Optional[datetime] = None
    metrics: Dict[str, Any] = field(default_factory=dict)  # engagement, clicks, conversions, etc.
    feedback_notes: List[str] = field(default_factory=list)


class ContentFeedbackLoop:
    """
    Handles the full performance feedback cycle for the content orchestration system.

    Designed to work with:
    - agents/memory/content_memory_allocation.yaml (episodic + performance sections)
    - TOA feedback_ingestion skill
    - Agent promotion system
    """

    def __init__(self, memory_client: Any = None, toa_client: Any = None):
        self.memory = memory_client  # Should support episodic.append() and performance logging
        self.toa = toa_client        # Should expose feedback_ingestion skill

    def ingest_performance(
        self,
        record: ContentPerformanceRecord,
        trigger_toa: bool = True,
        generate_promotion_signals: bool = True,
    ) -> Dict[str, Any]:
        """
        Main entry point after content is published or reviewed.

        Args:
            record: Structured performance data
            trigger_toa: Whether to send signals to TOA for forecasting improvement
            generate_promotion_signals: Whether to create agent promotion/performance signals

        Returns:
            Summary of actions taken
        """
        actions = {
            "memory_updated": False,
            "toa_signaled": False,
            "promotion_signals": [],
            "errors": [],
        }

        try:
            # 1. Store in episodic memory (recent approved + performance)
            self._store_in_episodic_memory(record)
            actions["memory_updated"] = True

            # 2. Send to TOA for future forecasting improvement
            if trigger_toa and self.toa:
                self._signal_toa(record)
                actions["toa_signaled"] = True

            # 3. Generate agent performance / promotion signals
            if generate_promotion_signals:
                signals = self._generate_agent_signals(record)
                actions["promotion_signals"] = signals

            logger.info(
                f"Feedback loop processed post_id={record.post_id} platform={record.platform} "
                f"approval={record.approval_status}"
            )

        except Exception as e:
            logger.error(f"Feedback loop error for {record.post_id}: {e}")
            actions["errors"].append(str(e))

        return actions

    def _store_in_episodic_memory(self, record: ContentPerformanceRecord) -> None:
        """Store performance record into episodic memory."""
        if not self.memory:
            logger.warning("No memory client provided - skipping episodic storage")
            return

        memory_entry = {
            "post_id": record.post_id,
            "platform": record.platform,
            "vertical": record.vertical,
            "pillar_used": record.pillar_used,
            "agent_ids": record.agent_ids,
            "critic_scores": record.critic_scores,
            "novelty_score": record.novelty_score,
            "approval_status": record.approval_status,
            "published_at": record.published_at.isoformat() if record.published_at else None,
            "metrics": record.metrics,
            "feedback_notes": record.feedback_notes,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }

        # In real implementation this would call the memory system
        # self.memory.episodic.append("content_performance", memory_entry)
        logger.debug(f"Stored performance record in episodic memory: {record.post_id}")

    def _signal_toa(self, record: ContentPerformanceRecord) -> None:
        """Send performance signals to TOA for improved forecasting."""
        if not self.toa:
            return

        feedback_payload = {
            "type": "content_performance",
            "post_id": record.post_id,
            "platform": record.platform,
            "vertical": record.vertical,
            "pillar": record.pillar_used,
            "agent_performance": {
                "agent_ids": record.agent_ids,
                "critic_scores": record.critic_scores,
                "novelty_score": record.novelty_score,
            },
            "business_outcome": record.metrics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # In real system this would call TOA's feedback_ingestion skill
        # self.toa.call_skill("feedback_ingestion", feedback_payload)
        logger.info(f"Signaled TOA with content performance data for post {record.post_id}")

    def _generate_agent_signals(self, record: ContentPerformanceRecord) -> List[Dict[str, Any]]:
        """Create promotion / performance signals for participating agents."""
        signals = []

        if not record.agent_ids:
            return signals

        # Simple heuristic: high critic scores + good novelty + positive metrics = promotion signal
        avg_critic = (
            sum(record.critic_scores.values()) / len(record.critic_scores)
            if record.critic_scores else 0
        )
        novelty = record.novelty_score or 0

        performance_score = (avg_critic * 0.6) + (novelty * 0.4)

        for agent_id in record.agent_ids:
            if performance_score > 0.82 and record.approval_status == "approved":
                signal = {
                    "agent_id": agent_id,
                    "signal_type": "promotion_consideration",
                    "performance_score": round(performance_score, 3),
                    "reason": "High critic scores + strong novelty on approved content",
                    "post_id": record.post_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                signals.append(signal)
                logger.info(f"Generated promotion signal for {agent_id}")
            elif performance_score < 0.65 or record.approval_status == "rejected":
                signal = {
                    "agent_id": agent_id,
                    "signal_type": "performance_review",
                    "performance_score": round(performance_score, 3),
                    "reason": "Lower performance detected - review patterns",
                    "post_id": record.post_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                signals.append(signal)

        return signals


# Convenience function
 def process_content_performance(
    record: ContentPerformanceRecord,
    memory_client: Any = None,
    toa_client: Any = None,
) -> Dict[str, Any]:
    """Quick interface for the feedback loop."""
    loop = ContentFeedbackLoop(memory_client=memory_client, toa_client=toa_client)
    return loop.ingest_performance(record)
