# SINCOR Agent Ops — 24/7 coordination layer

This is the scheduling + dispatch infrastructure that lets the departments
(scouts, builders, negotiators, synthesizers, auditors, caretakers, TOA) run
continuously. It is deliberately **zero-dependency**: python3 stdlib + cron.

## Layout

| Path | Role |
|---|---|
| `agents/departments.json` | Department registry: mission, KPI, treasury link, escalation |
| `agents/tasks/*.json` | Recurring task definitions per department (interval, prompt, KPIs) |
| `agents/runner.py` | The loop: due-task dispatch, outbox queue, ledger, treasury metrics |
| `agents/outbox/` | Task envelopes waiting for a worker (this IS the queue) |
| `agents/results/` | Worker responses awaiting auditor validation |
| `agents/ledger/` | Append-only JSONL audit trail, one file per day |
| `agents/crontab.example` | System cron schedule |

## Bring it up (5 minutes)

```bash
cd /path/to/SINCOR2
mkdir -p agents/logs
export BASE_RPC_URL=https://base-rpc.publicnode.com   # or your node

# optional: point tasks at an LLM worker (OpenAI-compatible)
export SINCOR_LLM_ENDPOINT=https://your-worker/v1/chat/completions
export SINCOR_LLM_KEY=...
export SINCOR_LLM_MODEL=...

python3 agents/runner.py --once       # smoke test: runs all due tasks
python3 agents/runner.py --metrics    # treasury snapshot -> ledger
crontab agents/crontab.example        # go 24/7
```

Without `SINCOR_LLM_ENDPOINT`, envelopes accumulate in `outbox/` and any external
worker (Hermes, Claude, an n8n flow, a human) can consume + drop results into
`results/`. The ledger records everything either way.

## Where this sits vs LangGraph / n8n

- **This runner (today):** zero-infra, auditable, cron-native. Right answer while
  task graphs are linear (schedule → dispatch → validate → ledger).
- **LangGraph (when coordination gets stateful):** if departments need branching,
  human-in-the-loop gates, or long-running state machines, move the envelopes onto
  a LangGraph graph — the JSON envelopes map 1:1 onto graph nodes. Python-native,
  matches the backend stack.
- **n8n (if you want visual ops):** self-hosted n8n can read `outbox/` via webhook,
  run the flows visually, and write `results/`. Good for non-engineer operators.

Migration path is clean because the contract is the envelope JSON, not the engine.

## Rules enforced here (from departments.json)

1. Every task carries a treasury-linked KPI — no revenue path, no task.
2. Auditor validation before any merge or external send.
3. Canonical addresses only from `CANONICAL_ADDRESSES.md`.
4. No destructive ops without human sign-off.
5. Ledger is append-only; caretakers archive learnings weekly.
