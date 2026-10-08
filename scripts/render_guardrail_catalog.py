"""Render docs/governance/GUARDRAIL_CATALOG.md from the machine-readable module.

Usage: PYTHONPATH=src python3 scripts/render_guardrail_catalog.py
(or run inline). Keeps the human-readable doc in sync with guardrails.py.
"""
import io
import sys

from sincor2.governance.action_catalog import ACTIONS
from sincor2.governance.guardrails import GUARDRAILS, summary, validate_coverage

validate_coverage()
s = summary()

TIERS = ["critical", "high", "medium", "low"]
TIER_BLURB = {
    "critical": "Moves money, sends external comms, changes auth/permissions, "
                "or touches treasury. **Human approval REQUIRED** "
                "(quorum for treasury movements).",
    "high": "Writes external state or affects other agents' funds/reputation. "
            "**Human approval required** — interactive for operator/admin "
            "actions, standing owner/policy approval (with envelopes) for "
            "autonomous protocol mechanics.",
    "medium": "Writes local state with cross-agent visibility. "
              "Auto with policy check + audit log.",
    "low": "Read-only or agent-local writes. Auto; audit log optional.",
}


def fmt_list(items):
    return "<br>".join(f"- {i}" for i in items)


def fmt_rate(rl):
    parts = []
    for k, v in rl.items():
        if k == "policy_class":
            continue
        parts.append(f"**{k}**: {v}")
    pc = rl.get("policy_class", "")
    head = f"`{pc}`" if pc else ""
    return head + ("<br>" + "<br>".join(parts) if parts else "")


def esc(t):
    return str(t).replace("|", "\\|")


out = io.StringIO()
w = out.write

w("# SINCOR2 Guardrail Catalog\n\n")
w("Every action in the [Action Catalog](ACTION_CATALOG.md) with its guardrails: "
  "pre-conditions, policy checks, approval, rate limits, audit, post-conditions, "
  "failure handling, and reversibility.\n\n")
w("Machine-readable source of truth: "
  "`src/sincor2/governance/guardrails.py` "
  "(`get_guardrail`, `guardrails_by_risk`, `validate_coverage`, `summary`). "
  "`validate_coverage()` is enforced programmatically: **zero gaps, zero orphans**.\n\n")

w("## Summary\n\n")
w("| Risk tier | Actions | Approval | Audit |\n")
w("|---|---|---|---|\n")
for t in TIERS:
    n = s["by_tier"].get(t, 0)
    ap = s["approval_by_tier"].get(t, {})
    ap_s = ", ".join(f"{v} {k}" for k, v in sorted(ap.items()))
    audit_s = "DecisionEvent required" if t in ("critical", "high") else (
        "DecisionEvent required" if t == "medium" else "optional")
    w(f"| **{t}** | {n} | {ap_s} | {audit_s} |\n")
w(f"| **total** | **{s['total']}** | | |\n\n")

w("**Coverage:** 111/111 actions have guardrails (validated: "
  f"{s['total']} entries, 0 gaps, 0 orphans). "
  f"**POLICY-MISSING flags:** {s['policy_missing_count']} actions carry at "
  "least one `POLICY-MISSING` marker — a policy check the codebase does not "
  "yet implement as a dedicated module. Each marker names what is needed.\n\n")

w("### Approval counts (high + critical)\n\n")
w("| Tier | human | quorum | total |\n|---|---|---|---|\n")
for t in ("critical", "high"):
    ap = s["approval_by_tier"].get(t, {})
    tot = s["by_tier"].get(t, 0)
    w(f"| {t} | {ap.get('human', 0)} | {ap.get('quorum', 0)} | {tot} |\n")
w("\nAll 24 critical and all 38 high actions carry human or quorum approval. "
  "For autonomous protocol mechanics (auction commit/reveal/close, stake "
  "deposit) the human is the agent's human owner / the task poster acting "
  "through pre-authorized standing approval envelopes; every execution logs "
  "a DecisionEvent with `human_disposition=\"not_reviewed\"` for post-hoc "
  "review, and anything outside the envelope requires fresh interactive "
  "approval.\n\n")

w("### Failure handling (universal)\n\n")
w("Every guardrail is **fail-closed**: any failed pre-condition or policy "
  "check blocks the action before side effects (no partial effects), "
  "returns 403/429, raises an alert via `sincor2.shadow_monitor.alerting`, "
  "and logs a DecisionEvent with `policy_result=\"deny\"`.\n\n")

w("### Audit standard\n\n")
w("Audit uses the `DecisionEvent` schema from "
  "`src/sincor2/shadow_monitor/events.py` (append-only, hash-chained JSONL "
  "store): `proposed_action`, `risk_tier`, `policy_result`, "
  "`policy_reason_codes`, `human_disposition`, `outcome_evidence`, "
  "`blocked_in_live_mode`. High-cardinality identifiers stay in log lines; "
  "customer identifiers are redacted via `events.redact()`.\n\n")

w("### Pre-condition vocabulary\n\n")
w("`agent_registered` · `kya_verified` · `kya_heartbeat_fresh` · "
  "`eip191_proof_valid` · `stake_sufficient` · `pool_balance_sufficient` · "
  "`pool_allocation_valid` · `agent_not_killed` · `admin_key_present` · "
  "`operator_key_present` · `adjudicator_signature_valid` · "
  "`provider_signature_valid` · `stripe_sig_valid` · `paypal_sig_valid` · "
  "`session_owner_verified` · `api_key_valid` · `x402_access_token_valid` · "
  "`idempotency_key_present` · `deadline_passed`/`deadline_not_passed` · "
  "`commit_phase_open` · `reveal_phase_open` · `auction_window_closed` · "
  "`hold_approved` · `hold_fresh` · `quorum_reached` · "
  "`executive_key_present` · `exec_live_armed` · `killswitch_clear` · "
  "`ofac_screen_clean` · `content_screen_clean` · `p24_live_allowed` · "
  "`task_has_no_bids` · `allocation_released` · `double_claim_guard_pass` · "
  "`sender_is_poster_or_admin` · `owner_signature_valid` · "
  "`cooldown_elapsed` · `rate_limit_ok` · `not_in_shadow_mode` · "
  "`standing_approval_envelope_ok`\n\n")

w("### Real policy references\n\n")
w("- `sincor2.a2a_identity.verify_wallet_proof` — EIP-191 identity proof "
  "(fail-closed)\n")
w("- `sincor2.a2a_rate_limits.*` — sliding-window enforcement "
  "(`A2A_RATE_POLICIES`: `register`, `bid`, `quote`, `dispute`, `issuance`, "
  "`read`, `settle`, `task_write`, `task_msg`, `heartbeat`, `stream`, "
  "`admin`)\n")
w("- `sincor2.shadow_monitor.effect_boundary.ShadowEffectBoundary.evaluate` "
  "/ `default_shadow_policy` — shadow effect gating\n")
w("- `sincor2.compliance_guardrails.GuardrailsEngine.check_email_send` / "
  "`check_content` / `check_pii_storage` — comms & content screening\n")
w("- `sincor2.defi.p24.policy.require_clean` — P24 content screening\n")
w("- `sincor2.defi.ofac_sdn.screen_wallet_address` — sanctions screening\n")
w("- `sincor2.treasury_policy.TreasuryPolicy` — fee conversion policy\n")
w("- `sincor2.defi.treasury_dao.Hold/Reviewer/Executor` — treasury "
  "review lifecycle\n")
w("- `sincor2.defi.gates.next_stage` — DeFi strategy stage gates\n\n")

for t in TIERS:
    w(f"---\n\n## {t.upper()} actions\n\n{TIER_BLURB[t]}\n\n")
    names = [n for n in GUARDRAILS if ACTIONS[n]["risk_tier"] == t]
    w("| Action | Domain | Approval | Rate limits | Reversible |\n")
    w("|---|---|---|---|---|\n")
    for n in sorted(names):
        g = GUARDRAILS[n]
        a = ACTIONS[n]
        rev = esc(str(g["reversibility"]))
        if g.get("irreversible"):
            rev = "**irreversible**"
        pc = esc(str(g["rate_limits"].get("policy_class", "")))
        w(f"| `{n}` | {a['domain']} | {g['approval']} | {pc} | {rev} |\n")
    w("\n")
    for n in sorted(names):
        g = GUARDRAILS[n]
        a = ACTIONS[n]
        w(f"### `{n}`\n\n")
        w(f"Entry point: `{esc(str(a['entry_point']))}` · "
          f"HTTP: `{esc(str(a['http']))}` · "
          f"Auth: `{esc(str(a['auth']))}`\n\n")
        w(f"**Approval:** {g['approval']} — {esc(str(g.get('approval_note', '')))}\n\n")
        w(f"**Pre-conditions:** {esc(', '.join(g['pre_conditions']))}\n\n")
        w(f"**Policy checks:**<br>{fmt_list([esc(c) for c in g['policy_checks']])}\n\n")
        w(f"**Rate limits:**<br>{fmt_rate({k: esc(v) for k, v in g['rate_limits'].items()})}\n\n")
        w(f"**Audit:**<br>{fmt_list([esc(x) for x in g['audit']])}\n\n")
        w(f"**Post-conditions:**<br>{fmt_list([esc(p) for p in g['post_conditions']])}\n\n")
        w(f"**Failure handling:** {esc(str(g['failure_handling']))}\n\n")
        rev_line = f"**Reversibility:** {esc(str(g['reversibility']))}"
        if g.get("irreversible"):
            rev_line += " — **IRREVERSIBLE**"
        w(rev_line + "\n\n")
        if "reversibility_plan" in g:
            w(f"**Reversibility plan:** {esc(str(g['reversibility_plan']))}\n\n")

text = out.getvalue()
with open("docs/governance/GUARDRAIL_CATALOG.md", "w") as fh:
    fh.write(text)
print(f"wrote docs/governance/GUARDRAIL_CATALOG.md ({len(text)} chars)")
