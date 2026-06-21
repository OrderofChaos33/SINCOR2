# Agent Add-ons Implementation Spec

**Version:** 1.0  
**Date:** 2026-06-21  
**Status:** Ready for implementation  
**Owner:** SINCOR Platform Team

## Goal
Allow customers to purchase additional agents beyond their base plan quota in a clean, scalable, and low-friction way.

Primary objectives:
- Remove artificial hard caps that limit growth
- Create a clear, high-margin upsell path
- Maintain simplicity for customers
- Prepare infrastructure for future usage-based billing

## Current State
- Plans have fixed agent counts (10 / 25 / 100)
- No native way for customers to buy more agents
- Top tier now set at 100 agents as a high, complaint-resistant cap

## Proposed Model

**Base Quota + Add-on Slots**

- Every subscription has a **base agent quota** based on their plan.
- Customers can purchase **additional agent slots** as monthly add-ons.
- Add-ons are simple, transparent, and recurring.

Example:
- Scale plan = 100 agents included
- Customer buys +25 agent add-on = 125 total agents

## Data Model Recommendations

### Subscription / Organization Level
```python
class Organization:
    base_agent_quota: int          # From plan (e.g. 100)
    purchased_addon_slots: int     # Total extra agents bought
    total_agent_quota: int         # base + purchased_addon_slots
    active_agents: int             # Currently deployed
```

### Add-on Product Definition
- Should be defined as billable products in Stripe / payment system
- Possible tiers:
  - +10 agents
  - +25 agents
  - +50 agents
  - Custom enterprise quantities

## Phased Rollout Plan

### Phase 1: Soft Launch (Recommended first step)
- Allow purchasing add-ons
- Show total available agents in dashboard
- No hard enforcement yet (soft warnings only)
- Goal: Validate demand and UX

### Phase 2: Basic Enforcement
- Prevent deploying more agents than total quota
- Clear upgrade prompts in UI

### Phase 3: Advanced
- Usage analytics on agent consumption
- Dynamic pricing / volume discounts
- Integration with future usage-based billing

## Technical Work Required

1. **Subscription / Quota Service**
   - Extend subscription model to track `base_quota` + `purchased_addons`
   - Calculate `total_quota` in real time

2. **Agent Deployment Gate**
   - Check quota before allowing new agent deployment
   - Surface clear messaging when near/over limit

3. **Billing Integration**
   - Add add-on products in payment system
   - Handle prorated billing for mid-cycle purchases
   - Sync purchased add-ons back to subscription record

4. **Dashboard & UX**
   - Show current usage vs quota
   - Clear "Buy more agents" flow
   - Usage trends over time

5. **Admin / Internal Tools**
   - Ability to grant temporary or permanent extra agents
   - Reporting on add-on adoption and revenue

## Enforcement Strategy

- **Phase 1**: Warnings only
- **Phase 2+**: Hard blocks on exceeding quota
- Always provide clear path to purchase more

## Open Questions

- Should add-on pricing be the same across all plans, or tiered?
- Do we want volume discounts for large add-on purchases?
- Should we eventually allow temporary agent boosts (e.g. for campaigns)?
- How do we handle downgrades when a customer reduces their base plan?

## Success Metrics
- Add-on attach rate (what % of Scale customers buy extra agents)
- Average revenue per Scale customer
- Reduction in churn due to capacity limits
- Time from hitting cap to purchasing add-on

---

This spec is intentionally kept simple and phased so we can ship value quickly while building toward a more sophisticated usage-based system later.