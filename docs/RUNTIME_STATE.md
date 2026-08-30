# Runtime state — honesty sheet

## Agent / task state today

| Store | Backend | Used for |
|---|---|---|
| `data/orders.db` | SQLite | Orders, customers (gunicorn volume `/data` on Railway) |
| `data/a2a_inbound.json` | JSON file | Inbound A2A directory (agents, tasks, bids) |
| `sincor2.task_queue` | in-process thread (Redis/Celery if `REDIS_URL`) | Long jobs |
| `agents/outbox` + `ledger` | JSON files | Department runner |

Postgres is available on Railway. **Next (P1):** point inbound fabric + task_queue at `DATABASE_URL` when it is `postgres://`. Do not pretend Redis is on until Railway Redis is attached (`health.checks.task_queue.redis` is currently false).

## V4 hook

`onchain/src/SincLimitOrderHook.sol` is source. `onchain/lib/` is not vendored, so `forge build` is skipped in CI until those deps exist.
