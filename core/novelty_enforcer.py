from __future__ import annotations

"""
NoveltyEnforcer

Production-grade module for forced novelty enforcement in the content/copy pipeline.

Used by the Content Critic (E-critic-46) to ensure generated copy does not repeat
structures, hooks, pillars, or phrasing from recent approved/high-performing posts.

Design goals:
- Simple and fast to start (no heavy ML deps initially)
- Easy to upgrade to embedding-based semantic similarity later
- Clear decision + actionable feedback for the Critic
- Integrated with episodic memory (recent approved posts)

This directly addresses the core problem of repetitive "garbage" output.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class NoveltyResult:
    """Result of a novelty check."""
    novelty_score: float          # 0.0 (identical) to 1.0 (highly novel)
    decision: str                 # "PASS" or "REVISE"
    reasons: List[str]            # Why it passed or failed
    suggested_fix: Optional[str] = None  # Actionable suggestion if REVISE
    similarity_details: Dict[str, float] = None  # Breakdown of similarity types


class NoveltyEnforcer:
    """
    Enforces novelty against recent content in episodic memory.

    Current implementation uses lightweight heuristics:
    - Hook type / opening pattern repetition
    - Pillar reuse at sentence level
    - Common phrase / n-gram overlap
    - Structural similarity (list vs story vs bold claim)

    Future upgrade path: Add embedding similarity using existing memory vectors.
    """

    def __init__(self, similarity_threshold: float = 0.75):
        self.similarity_threshold = similarity_threshold

    def check_novelty(
        self,
        new_content: str,
        recent_posts: List[Dict[str, Any]],
        current_pillar: Optional[str] = None,
    ) -> NoveltyResult:
        """
        Main entry point.

        Args:
            new_content: The newly generated copy (single post or thread)
            recent_posts: List of recent approved posts from episodic memory.
                       Each dict should contain at least: 'full_text', 'pillar_used', 'hook_type'
            current_pillar: The pillar the Strategist assigned for this brief (optional)

        Returns:
            NoveltyResult with decision and feedback
        """
        if not recent_posts:
            return NoveltyResult(
                novelty_score=1.0,
                decision="PASS",
                reasons=["No recent posts to compare against"],
                similarity_details={},
            )

        reasons = []
        similarity_scores = []

        # 1. Check for repeated hook / opening patterns
        hook_similarity = self._check_hook_repetition(new_content, recent_posts)
        similarity_scores.append(hook_similarity)
        if hook_similarity > 0.7:
            reasons.append(f"High hook pattern similarity ({hook_similarity:.2f}) with recent posts")

        # 2. Check pillar repetition at content level
        if current_pillar:
            pillar_similarity = self._check_pillar_repetition(new_content, recent_posts, current_pillar)
            similarity_scores.append(pillar_similarity)
            if pillar_similarity > 0.65:
                reasons.append(f"Heavy reuse of pillar '{current_pillar}' in recent content")

        # 3. N-gram / common phrase overlap
        ngram_similarity = self._check_ngram_overlap(new_content, recent_posts)
        similarity_scores.append(ngram_similarity)
        if ngram_similarity > 0.6:
            reasons.append(f"Significant phrase overlap with recent approved copy ({ngram_similarity:.2f})")

        # 4. Structural similarity (very rough)
        structure_similarity = self._check_structural_similarity(new_content, recent_posts)
        similarity_scores.append(structure_similarity)

        # Aggregate score (lower = more similar = less novel)
        avg_similarity = sum(similarity_scores) / len(similarity_scores) if similarity_scores else 0.0
        novelty_score = round(1.0 - avg_similarity, 3)

        # Decision
        if novelty_score >= (1.0 - self.similarity_threshold):
            decision = "PASS"
            suggested_fix = None
        else:
            decision = "REVISE"
            suggested_fix = self._generate_suggestion(new_content, recent_posts, current_pillar)

        return NoveltyResult(
            novelty_score=novelty_score,
            decision=decision,
            reasons=reasons if reasons else ["Passed all novelty heuristics"],
            suggested_fix=suggested_fix,
            similarity_details={
                "hook": round(hook_similarity, 3),
                "pillar": round(pillar_similarity, 3) if current_pillar else 0.0,
                "ngram": round(ngram_similarity, 3),
                "structure": round(structure_similarity, 3),
            },
        )

    # --- Internal heuristic methods ---

    def _check_hook_repetition(self, new_content: str, recent_posts: List[Dict]) -> float:
        """Detect if the opening/hook style is overused."""
        new_first_line = new_content.strip().split("\n")[0].lower()
        repeated = 0
        for post in recent_posts:
            text = post.get("full_text", "").strip().split("\n")[0].lower()
            if self._similar_opening(new_first_line, text):
                repeated += 1
        return min(repeated / max(len(recent_posts), 1), 1.0)

    def _similar_opening(self, a: str, b: str) -> bool:
        """Simple check for similar opening styles."""
        common_starters = ["what if", "the agents that", "still manually", "tired of", "imagine if", "bold claim"]
        for starter in common_starters:
            if starter in a and starter in b:
                return True
        # Simple word overlap on first 8 words
        words_a = set(a.split()[:8])
        words_b = set(b.split()[:8])
        overlap = len(words_a & words_b) / max(len(words_a), 1)
        return overlap > 0.6

    def _check_pillar_repetition(self, new_content: str, recent_posts: List[Dict], current_pillar: str) -> float:
        """Penalize heavy reuse of the same pillar recently."""
        recent_same_pillar = sum(
            1 for p in recent_posts if p.get("pillar_used") == current_pillar
        )
        return min(recent_same_pillar / max(len(recent_posts), 1), 1.0)

    def _check_ngram_overlap(self, new_content: str, recent_posts: List[Dict], n: int = 4) -> float:
        """Check for repeated 4-grams (phrases)."""
        def get_ngrams(text: str, n: int) -> set:
            words = re.findall(r"\b\w+\b", text.lower())
            return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}

        new_ngrams = get_ngrams(new_content, n)
        if not new_ngrams:
            return 0.0

        overlap_scores = []
        for post in recent_posts:
            old_ngrams = get_ngrams(post.get("full_text", ""), n)
            if old_ngrams:
                overlap = len(new_ngrams & old_ngrams) / len(new_ngrams)
                overlap_scores.append(overlap)

        return max(overlap_scores) if overlap_scores else 0.0

    def _check_structural_similarity(self, new_content: str, recent_posts: List[Dict]) -> float:
        """Very rough structural check (list, thread, bold claim, story, question)."""
        def detect_structure(text: str) -> str:
            text_lower = text.lower()
            if text_lower.startswith(("1.", "-", "•")) or "\n- " in text:
                return "list"
            if "thread" in text_lower or text.count("\n\n") > 2:
                return "thread"
            if any(x in text_lower for x in ["what if", "imagine", "suppose"]):
                return "visionary"
            if "?" in text and text.count("?") >= 2:
                return "question"
            return "statement"

        new_struct = detect_structure(new_content)
        matches = sum(1 for p in recent_posts if detect_structure(p.get("full_text", "")) == new_struct)
        return min(matches / max(len(recent_posts), 1), 0.8)  # Cap at 0.8

    def _generate_suggestion(
        self, new_content: str, recent_posts: List[Dict], current_pillar: Optional[str]
    ) -> str:
        """Generate actionable suggestion when revision is needed."""
        suggestions = []

        if current_pillar:
            suggestions.append(f"Try rotating to a different pillar than '{current_pillar}' or evolve how it's used.")

        suggestions.append("Use a different hook type (e.g. contrarian, data-backed, or story snippet instead of current style).")
        suggestions.append("Introduce a fresh metaphor from the bank (geodesic, wave collapse, swarm intelligence, etc.).")
        suggestions.append("Change structure (e.g. single punchy cast vs thread vs question-led).")

        return " | ".join(suggestions)


# Convenience function for quick use
 def enforce_novelty(
    new_content: str,
    recent_posts: List[Dict[str, Any]],
    current_pillar: Optional[str] = None,
    threshold: float = 0.75,
) -> NoveltyResult:
    """Quick function interface for the Critic."""
    enforcer = NoveltyEnforcer(similarity_threshold=threshold)
    return enforcer.check_novelty(new_content, recent_posts, current_pillar)
