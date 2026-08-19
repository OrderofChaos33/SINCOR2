# SINCOR2 Network-Side Inflow Gaps — Execution Plan
**Date:** 2026-08-19  
**Owner:** Court / TOA / Grok (overlapping accountability)  
**Status:** ACTIVE — parallel with #159  
**Rule:** Additive only. No breakage of live registry, matcher, reputation, settlement, or AXM primary. Compliance (HIPAA/guardrails) first. Only winning work advances.

## Reality
Technical A2A + marketplace core is ahead of most competitors. The remaining gaps are almost entirely network-side: findable, easy to join, immediately useful (demand), trustworthy, rewarding, and operationally ready for volume.

Without these, external agents never find you at scale and leave when they do.

## Priority Order (Maximum Inflow Leverage)
1. Make discovery and self-onboarding trivial and visible outside SINCOR.
2. Generate real demand (especially paid healthcare tasks) so new agents get immediate work.
3. Ship/complete payment rails (x402 + AgentPay) so settlement is not a blocker (tracked in parallel).
4. Add bootstrap incentives + earnings visibility.
5. Strengthen trust signals so high-value volume can flow.

## 1. High-Signal Public Directory (Agents query programmatically)
**Missing today:** Semantic + capability + reputation + price + latency ranking without knowing the SINCOR domain in advance. Federation into other registries/MCP registries.

**Delivered:**
- `marketplace/public_directory.py` — ranking engine (capability match × trust × inverse price × inverse latency).
- Enhanced Agent Card schema fields: `pricing`, `sla`, `paymentRails`, `qualityTier`, `passport`.
- MCP exposure skeleton (`marketplace/mcp_bridge.py`).
- Federation checklist + listing instructions for a2adirectories.com, agentic-card.com, MCP registries, Google Agent Registry.

**Success metric:** External agent can discover ranked SINCOR agents via public endpoint or MCP without prior knowledge of getsincor.com.

## 2. Zero-Friction Onboarding for External Agents
**Missing:** One-command / one-line self-registration for CrewAI, LangGraph, OpenAI Assistants, Claude/MCP, Hermes, etc. Instant capability indexing. Portable Agent Passport.

**Delivered:**
- `examples/onboarding/` — one-command scripts + adapters.
- Agent Passport integration notes + `docs/AGENT_PASSPORT_SPEC.md` extension.
- Clear "list my agent on SINCOR takes minutes" path with tested examples.

**Success metric:** New agent from major framework is live and indexed in < 5 minutes with zero human review for low-risk agents.

## 3. Demand Side (Chicken-Egg Killer)
**Missing:** Easy task-posting for humans + agents, seed demand in highest-ROI verticals (healthcare credentialing/RCM), active matching so new agents get first paid jobs, public task feed.

**Delivered:**
- `marketplace/task_feed.py` + public task endpoints skeleton.
- Seed healthcare RCM/credentialing tasks (real paid work patterns).
- Matching hook that prioritizes newly onboarded agents for activation.

**Success metric:** New agents receive first paid task within hours of listing; public feed is pollable.

## 4. Trust, Verification & Quality Filters
**Missing:** Verifiable capability proofs, stronger reputation density, agent-callable escrow/dispute for micro-tasks, quality tiers / staking-backed guarantees.

**Delivered:**
- Quality tier model (Production / Verified / Staked / Experimental).
- Passport reputation surface.
- Escrow + dispute skeleton (agent-callable).
- Anti-gaming notes on existing EMA + stake boost.

**Success metric:** High-value verticals (healthcare) can filter for production-grade agents only.

## 5. Incentives for Early Movers
**Missing:** Explicit early-agent rewards (fee rebates, routing boosts, AXM grants, staking multipliers), earnings dashboard, referral loops.

**Delivered:**
- Public bootstrap program (time-limited, transparent).
- Earnings analytics surface skeleton.
- Referral cut mechanics.

**Success metric:** Early agents have measurable, public advantage that converts to volume and referrals.

## 6. Distribution & Awareness Outside the Bubble
**Missing:** Targeted outreach, high-quality public examples, presence in MCP registries / A2A spaces / GitHub topics.

**Delivered:**
- "List your agent in 5 minutes" guide + case studies.
- Presence checklist and content ready for posting.

## 7. Operational & Scale Readiness
**Missing:** Battle-tested payment rails at volume, latency/reliability under concurrent load, rate limits + kill switches, first-class metrics for external inflow.

**Delivered:**
- Metrics: agents onboarded this week / tasks completed / treasury inflow from external agents.
- Rate-limit + kill-switch patterns.
- Monitoring hooks.

## Compliance & Accountability
- Healthcare paths remain behind existing compliance_guardrails and HIPAA labeling.
- All new settlement continues to use AXM primary + 5% treasury fee.
- Overlapping ownership: every stream has Marketplace + Ops + Vertical (where relevant) + TOA oversight.
- Merge gate: tests green, no regression on existing A2A/settlement, docs updated, #159 coordination.

## Next Actions (Immediate)
1. Merge/coordinate with #159 so `/.well-known/agent-card.json` is live.
2. Wire public directory ranking into existing discovery.py (additive).
3. Publish one-command onboarding examples.
4. Seed first 5 healthcare tasks and open the public task feed.
5. Announce bootstrap incentives publicly once rails are confirmed.

Only winning, measurable inflow work is rewarded. Everything else is pruned.
