# SINCOR Content & Copy Quality Standards

**Version:** 1.0.0  
**Date:** 2026-06-21  
**Status:** Production-ready, FinTech-grade  
**Owner:** SINCOR Content Orchestration Team  
**Purpose:** Define non-negotiable quality, reliability, auditability, and safety requirements for all native content/copy generation. This is the rubric the Critic (E-46) enforces. All agents, pipelines, and customer deployments must meet or exceed these standards.

## 1. Core Quality Dimensions (Critic Scoring Rubric - 0-100 scale per dimension, minimum passing aggregate 82)
- **Brand Alignment (weight 20%)**: Strict adherence to current persistent brand voice, tone, key phrases, and visual language. No drift.
- **Novelty & Anti-Repetition (weight 25%)**: Semantic + structural differentiation from last 7-14 approved/high-performing pieces. Forced enforcement via memory comparison. No recycled hooks, structures, or metaphors without evolution.
- **Platform Optimization (weight 15%)**: Native to channel (Farcaster alpha/on-chain tone + Frames potential, X thread depth, FB storytelling + visuals). Correct length, format, CTA style.
- **Predicted Performance / ROI (weight 15%)**: Evidence-based expectation of engagement, clicks, deploys, or revenue motion impact. Uses historical data + TOA forecasting.
- **Clarity & Value Density (weight 10%)**: High signal, scannable, specific outcomes or next steps. No fluff, hype, or vague claims.
- **Risk & Compliance (weight 10%)**: No unauthorized financial advice, price speculation, or regulatory risk. Proper disclaimers where needed. Ethical vertical adaptation.
- **Actionability & CTA Strength (weight 5%)**: Clear, high-intent calls to action tied to business goals (deploy, buy on curve, engage, etc.).

**Minimum Gate**: Any dimension < 70 or aggregate < 82 = reject or mandatory iteration. Top variants only advance.

## 2. Forced Novelty Enforcement Protocol
- Before final output, Critic performs similarity check against episodic memory (recent approved) and semantic high-performers.
- Metrics: Semantic embedding similarity (planned) + structural pattern match (hooks, sentence rhythm, pillar reuse) + metaphor family overlap.
- Threshold: > 0.75 similarity on any major dimension triggers rejection or heavy revision requirement.
- Enforcement: "This output reuses X structure/hook from [date/post]. Generate fresh angle using [suggested new hook type or pillar rotation]."
- Goal: Every approved piece feels new even to frequent readers.

## 3. Output Requirements (Strict Format)
- Always multiple variants (minimum 3 for important briefs) unless single high-confidence requested.
- Include rationale, novelty note vs recent, and dimension scores.
- For Facebook: Detailed, production-ready image prompt following brand visual language.
- Platform-specific: Farcaster (on-chain language, $SINC utility, Frames tease), X (thread potential), FB (story + visual).
- No generic CTAs. Every CTA tied to measurable outcome.

## 4. FinTech-Grade Requirements (Reliability, Audit, Security, Compliance)
- **Auditability**: Every decision (brief, variant, score, rejection reason, edit feedback) logged via Archivist to persistent memory with timestamp, agent ID, scores, and rationale. Queryable for reviews.
- **Reliability**: Critic gate is mandatory. Fallback to human review or safe default if pipeline fails. Health checks integrated with existing SINCOR monitoring.
- **Security**: Prompt injection resistance (strict input sanitization on briefs). No secrets in prompts or memory. Constitution deltas enforced.
- **Compliance & Ethics**: No financial advice unless vertical-approved with disclaimers. Risk flagging for hype or overclaim. Vertical-specific compliance rules loaded from catalog. "Highest standards and ethics" upheld.
- **Testing & Validation**: Unit tests for scoring rubric (future). Integration tests for full pipeline (Strategist → Copywriter variants → Critic). A/B testing of output quality post-deployment.
- **Monitoring & Improvement**: Performance data (approval rate, engagement lift, Critic scores) fed back into TOA and memory. Agent promotion based on win-rate and scores.
- **Rollback & Versioning**: All agent cards, standards, and pillars versioned. Easy rollback to previous known-good config.

## 5. Performance Feedback Loop Standards
- Post-publication metrics (where available) ingested into episodic memory.
- TOA uses data for future forecasting and objective optimization.
- High-performing pieces promoted in semantic memory; patterns from low-performers pruned or evolved.
- Quarterly standards review based on aggregate data.
- Agent-level: Copywriters with high Critic win-rate and downstream performance get promoted (higher budget, leadership roles).

## 6. Vertical Adaptation Rules
- Load vertical-specific rules, compliance notes, and example language from VERTICAL_CONTENT_CATALOGS.md.
- Never apply generic copy to regulated verticals without adaptation.
- Document any compliance deviations in rationale.

This document is the single source of truth for quality. Critic (E-46) applies it ruthlessly. Updates require version bump and review.