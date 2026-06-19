# SADAS Launch Checklist

**Goal:** Professional launch of the Alternative Derivative Alpha Swarm to Enterprise clients and institutional prospects.

## Pre-Launch (Do these first)
- [ ] Verify `SADAS_ENABLED=true` and `SADAS_INTERVAL_MINUTES=15` in production env
- [ ] Confirm `TREASURY_CONVERSION_ENABLED=true` and trading wallet addresses set
- [ ] Test one full SADAS cycle locally (`python -c "from src.sincor2.sadas_orchestrator import run_sadas_scheduled_cycle; print(run_sadas_scheduled_cycle())"`)
- [ ] Ensure `daily_ops_scheduler.py` is starting the SADAS job on app boot
- [ ] Replace remaining mock data in `_get_real_scout_result` with production data sources if needed

## Media & Positioning
- [ ] Review and customize `media/sadas/Press_Release_SADAS.md`
- [ ] Review `media/sadas/Executive_One_Pager_SADAS.md`
- [ ] Prepare X thread and LinkedIn post from `media/sadas/Social_Assets_SADAS.md`
- [ ] Identify target list (crypto hedge funds, family offices, quant desks)

## Technical Readiness
- [ ] Confirm revenue from SADAS anomalies flows through `treasury_policy.py`
- [ ] Verify anomaly JSON is being emitted/logged for dashboard consumption
- [ ] Add basic monitoring/alerting for SADAS job failures (if not already in Railway)
- [ ] Document any remaining TODOs in `sadas_orchestrator.py`

## Go-Live
- [ ] Deploy with SADAS enabled
- [ ] Monitor first 3–5 cycles in logs
- [ ] Send internal test anomalies to dashboard/team
- [ ] Distribute press release + one-pager to target list
- [ ] Post social assets

**Owner:** Court / Core Team
**Target Date:** TBD
**Status:** Ready for final review and deployment