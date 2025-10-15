# Auto-run Deployment

Two turnkey paths: **Docker one-command** or **GitHub Actions nightly**. Both use the code from `auto_detailers_us`.

## A) Docker (local/server) — 1 command

1) Put your Google/Yelp API keys in `.env` (copy `.env.example` → `.env`).
2) Save your Google Sheets service account JSON to `secrets/credentials.json` and share your target Sheet with that service account email.
3) From the project root (where the Dockerfile lives), run:
```bash
docker compose --env-file .env up --build
```
Output CSV goes to `output/auto_detailers_us.csv`. If Sheets vars are set, the job pushes to your Sheet automatically.

## B) GitHub Actions (nightly, serverless)

1) Create a new **private** repo. Add all files from **both** folders:
   - `auto_detailers_us/` (the pipeline code)
   - `auto_detailers_us_autorun/` (this folder; merge into repo root)
2) In GitHub → Settings → **Secrets and variables → Actions**, add:
   - `GOOGLE_PLACES_KEY`
   - `YELP_API_KEY`
   - `GOOGLE_SHEETS_SPREADSHEET_ID`
   - `GOOGLE_SHEETS_WORKSHEET` (e.g., `detailers`)
   - `GOOGLE_SHEETS_CREDENTIALS_JSON` (the **entire** service account JSON as a single secret value)
3) Commit & push. The workflow runs on push and nightly at 03:30 UTC, writes CSV to `output/` artifact, and pushes to your Sheet.

---

## Notes
- Tune density with `CELLS` / `RADIUS_M` / `MAX_PER_CELL`. Higher density = more coverage (and more API cost).
- To scope to a few states first, lower `CELLS` (e.g., 400–800) to sanity-check before going national.
