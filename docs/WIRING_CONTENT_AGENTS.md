# Wiring Plan: Content Orchestration Agents

**Date:** 2026-06-21  
**Status:** Ready to implement  
**Priority:** High

## Goal
Make the new content orchestration components actually callable and integrated into the running SINCOR multi-agent system.

## Components to Wire

### 1. E-strategist-45
- Should be discoverable via `AgentCardRegistry`
- Needs to be callable for `generate_brief`, `select_pillars`, `vertical_adapt`, and `forecast_performance`
- Should integrate with TOA for forecasting

### 2. E-critic-46
- Must be callable for `score_variants`, `enforce_novelty`, `apply_standards`, and `rank_and_feedback`
- Needs access to `NoveltyEnforcer` (already in `core/novelty_enforcer.py`)
- Should be used as a mandatory gate in content workflows

### 3. ContentFeedbackLoop (`core/content_feedback_loop.py`)
- Needs to be triggered after content approval/publication
- Should write to episodic memory
- Should signal TOA with performance data
- Should generate agent performance/promotion signals

## Recommended Implementation Steps

1. **Registry Confirmation**
   - Ensure `E-strategist-45` and `E-critic-46` are loaded in `marketplace/agent_cards.json`
   - Run `scripts/register_agent_cards.py` if needed

2. **Create Workflow Orchestrator**
   - Create a lightweight content workflow coordinator (can start in `core/content_workflow.py`)
   - Flow: Strategist → Copywriters → Critic (with NoveltyEnforcer) → optional human approval → FeedbackLoop

3. **Integrate NoveltyEnforcer**
   - Already built in `core/novelty_enforcer.py`
   - Wire it into `E-critic-46` skill `enforce_novelty`

4. **Connect Feedback Loop**
   - Trigger `ContentFeedbackLoop.ingest_performance()` after content is approved or published
   - Pass TOA client and memory client

5. **Testing**
   - Create a simple test script that runs a full brief → variants → critic → feedback loop

## Open Questions
- Should the content workflow be triggered via TaskRouter or have its own dedicated coordinator?
- How do we handle human-in-the-loop approval in the initial version?

## Next Action
Start with creating `core/content_workflow.py` as a thin orchestrator, then wire the existing components into it.