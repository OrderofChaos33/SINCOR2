# Async task queue + real A2A streaming

Gunicorn runs a **single sync worker** with a **180s timeout**. Anything that
does real work on the request thread (A2A dispatch, content generation,
webbuilder phases) will 504 the site.

## Contract

| Endpoint | HTTP | Body |
|---|---|---|
| `POST /api/a2a` `message/send` | 200 (JSON-RPC) | Task, `status.state` is `submitted`/`working` until the worker finishes. Poll `tasks/get`. |
| `POST /api/a2a/tasks/send` | **202 Accepted** | `{ accepted, task_id, poll_url, result }` |
| `POST /admin/content/generate` | **202** | `{ accepted, task_id, poll_url }` |
| `POST /api/webbuilder/projects/<id>/run` | **202** | same |
| `POST /api/webbuilder/projects/<id>/rebuild` | **202** | same |
| `GET /api/tasks/<id>` | 200 | `{ task_id, kind, status, progress, result, error }` |
| `POST /api/a2a` `message/stream` | 200 `text/event-stream` | Token SSE (stays on the socket) |

Clients: return immediately, then poll `/api/tasks/<id>` (or A2A `tasks/get`)
until `status` is `completed` or `failed`.

## Backends

`SINCOR_TASK_QUEUE=auto|celery|thread|eager`

- **eager** — forced when `FLASK_ENV=test`. Runs the job inline so pytest
  still sees a completed A2A task.
- **celery** — when `REDIS_URL` / `CELERY_BROKER_URL` is reachable.
- **thread** — in-process `ThreadPoolExecutor`. Unsticks gunicorn even on a
  host that has no Redis yet (current Railway default). Shared memory, so
  keep `gunicorn.conf.py` at `workers = 1` unless you also set
  `A2A_TASK_STORE=redis`.

### Celery worker

```
celery -A sincor2.celery_app.celery worker --loglevel=info -Q sincor.long
```

Procfile defines a `worker` process. Railway: add a second service with that
start command and the same `REDIS_URL`. Compose: `docker compose up redis worker sincor2`.

Celery workers are a **separate process**. Set `A2A_TASK_STORE=redis` (or
sqlite on a shared volume) so the worker can see A2A tasks created by the web
process.

## Real A2A SSE

`message/stream` no longer waits for `_dispatch_to_swarm()` and dumps one
artifact. It:

1. Yields `status-update` `submitted`
2. Yields `status-update` `working`
3. Yields `artifact-update` chunks (`append: true`) from
   `client.messages.stream()` / `text_stream` when `ANTHROPIC_API_KEY` is set
4. Yields `lastChunk: true`
5. Yields `status-update` `completed` with `final: true`

Streaming stays on the HTTP connection on purpose. Use `message/send` + the
queue for jobs you don't want to hold a socket for.

## Health

`GET /health` includes `checks.task_queue.detail` = `celery|thread|eager`.
The queue is non-critical: the thread fallback always accepts work.
