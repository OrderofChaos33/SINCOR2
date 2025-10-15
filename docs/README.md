# CAD Flask Skeleton

Minimal, production-lean Flask app for Clinton Auto Detailing.

## Quickstart (Dev)
```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
flask --app app run --debug
```

Login (admin): `admin@example.com` / `admin123` (change this).

## Prod
- `Procfile` uses `gunicorn app:app` (Render/Heroku/Railway).
- Set `SECRET_KEY` and `DATABASE_URL` env vars.
