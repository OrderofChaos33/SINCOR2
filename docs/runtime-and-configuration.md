# Runtime and Configuration

## Canonical runtime targets

- Flask app object: `sincor2.mvp_app:app`
- Local development command: `python run.py`
- Production command: `gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 --preload sincor2.mvp_app:app`

## Environment variable policy

All environment variable names and defaults are documented in `.env.example`.

### Required in production

- `SECRET_KEY`
- `JWT_SECRET_KEY` (or `JWT_SECRET`)
- `ADMIN_PASSWORD` (must not be `changeme123`)
- At least one payment integration:
  - `STRIPE_SECRET_KEY` (or Stripe aliases), or
  - `PAYPAL_REST_API_ID` + `PAYPAL_REST_API_SECRET`

### Optional

- `ADMIN_USERNAME` (defaults to `admin`)
- `ANTHROPIC_API_KEY`
- `BASE_RPC_URL`
- PayPal/Stripe non-critical helper settings

## Startup behavior

- Settings are parsed/validated by `sincor2.settings.Settings`.
- Production misconfiguration fails fast at startup.
- Request correlation IDs are added to all responses via `X-Request-ID`.
- API errors follow a consistent schema:

```json
{
  "status": "error",
  "code": "string",
  "message": "human-readable message",
  "details": {},
  "request_id": "uuid"
}
```
