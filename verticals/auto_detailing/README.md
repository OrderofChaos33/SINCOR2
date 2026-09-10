# Auto Detailing Vertical Pack — CHROMA

**Domain:** Autonomous growth OS for auto detailing, ceramic coating, PPF, and tint shops.

**Why this pack exists**

Detailers lose jobs to speed, not skill. A customer Googles three shops and books the first one that answers. CHROMA is the swarm that answers, qualifies, quotes, and books — then keeps the site, reviews, and socials compounding while the bays are full.

A shop owner logs in, sees the inbox, and says **“oh, this books me jobs.”** That sentence is the product.

## Setup

```bash
# from repo root
export PYTHONPATH=.
pip install -r requirements.txt   # flask, pydantic already in the platform
python scripts/chroma_seed_demo.py
CHROMA_DEMO=true python -m gunicorn sincor2.chroma_app:app --bind 0.0.0.0:8080
```

Open `/chroma/`. Demo mode skips login and loads 10 fake jobs for **Clinton Auto Detailing** (Clinton, IA).

Staff login (when `CHROMA_DEMO` is off) is the same Sept 9 operator pair: `ADMIN_USERNAME` or `ADMIN_EMAIL` + `ADMIN_PASSWORD`.

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `CHROMA_LIVE_SEND` | unset / false | Outbound is dry-run. Approve still only logs until this is `true`. |
| `CHROMA_DEMO` | false | Boot the dashboard pre-loaded. Loom environment. |
| `CHROMA_DB_PATH` | `$SINCOR_DATA_DIR/chroma.db` | SQLite for leads, quotes, bookings, send queue |
| `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | platform admin | Shop login (quotes stripped, email accepted) |

Nothing in this pack calls Twilio, Calendly, or Google unless you later wire providers. Quotes and Calendly URLs are constructed locally.

## Demo mode

```bash
CHROMA_DEMO=true python scripts/chroma_seed_demo.py
```

Reproduces from empty:

- 10 leads at mixed stages (hot ceramic Tesla, missed-call PPF from Fulton, pet-hair interior, booked Mustang…)
- ≥3 quotes (one marked sent)
- ≥2 bookings (one booked slot, one link-ready)
- Outreach queue waiting on Approve / Edit / Kill

## Shop dashboard

Mounted at **`/chroma`** on the existing Railway service (`getsincor.com/chroma`). A subdomain (`chroma.getsincor.com`) would need a second Railway service + DNS — `/chroma` is the simpler mount.

Pages:

1. **Leads** — name, source, color-coded intent, status. Tap → detail + timeline.
2. **Quotes** — package × size matrix, preview, sent / not-sent.
3. **Bookings** — Calendly link status, booked slots, handoff log.
4. **Send queue** — every email/SMS/social draft. Approve / Edit / Kill. Blocked without owner OK + `CHROMA_LIVE_SEND=true`.
5. **Shop** — name, Calendly URL, package prices.

Health: `GET /chroma/health` (also `/health` on the standalone `chroma_app`).

## Pipeline (dry-run)

`lead → score → quote → outreach queue → Calendly handoff → engagement`

Run in-process with zero network:

```python
from verticals.auto_detailing.pipeline import run_pipeline
run_pipeline({"source": "google", "name": "Ava", "message": "ceramic on a BMW X5", "email": "ava@x.test"})
```

## Tests

```bash
PYTHONPATH=. pytest verticals/auto_detailing/ -q
```

Covers ingest→score, quote matrix (unknown size, zero deposit), sequence ordering, Calendly URL construction, send-gate, seed, and the full dry-run pipeline.

## Loom script (≈3 min)

1. Open `/chroma/` in demo. “This is the bay board for Clinton Auto Detailing. Hot leads are orange.”
2. Tap **Miles Brennan** — ceramic Tesla Model X. Timeline: ingested → quoted → booking link → outreach queued.
3. **Quotes** — show the matrix. Ceramic SUV has a 50% deposit. Express wash is $0 hold. Preview an unknown size: it prices as sedan.
4. **Bays** — live path is text (815) 718-8936. Open the prefilled Calendly URL (vehicle + package in the query string) for when self-booking is back.
5. **Send** — Approve Miles’s email. Banner: dry-run, nothing left the building. Kill the hype one. Edit a text.
6. Inbox: paste a new lead (“need PPF on an F-150, I’m in Fulton”), hit **Score it**. Watch it land hot with a quote and a queued SMS.
7. Close: “The owner never writes a follow-up. They tap Approve. That’s $500/mo.”

**Core capabilities**

* Lead aggregation across Google, GBP, website, Instagram, Facebook Marketplace, ads, referrals, and missed calls
* Intent scoring with vehicle-value, photo-quote, and recency weights
* Instant quote by package × vehicle size, with deposit rules for ceramic / PPF
* Calendly (or native slot) handoff with prefilled service + vehicle
* Website presence agents: metadata, local SEO / AEO JSON-LD, gallery, review showcase
* Landing engagement popup that qualifies visitors 24/7 and steers to self-booking
* Social autopilot: scheduled posts, Marketplace replies, GBP posts, comment engagement
* Email / SMS outreach: quote follow-up, 30-day maintenance nudges, membership upsells, review asks
* Weather holds for exterior work, no-show recovery, photo-quote intake

**Protocols included (research-backed)**

| Protocol | Effect |
|---|---|
| Sub-5-minute first response | Captures comparison shoppers |
| Photo-quote intake | Cuts back-and-forth on ceramic / interior jobs |
| Deposit-on-booking for coatings | Collapses no-shows |
| T-24h / T-2h reminders | No-show rate toward <2% |
| Post-job review request (T+2h) | GBP ranking compounding |
| 30 / 60 / 90-day wash membership nudges | Smooths seasonal demand |
| Seasonal campaign engine | Salt, pollen, summer ceramic, fall PPF |
| AEO FAQ + LocalBusiness JSON-LD | Surfaces in AI search and Maps |
| Missed-call text-back | Recovers after-hours demand |
| Upsell graph wash → interior → ceramic → PPF | Raises ticket without extra ads |

**Integration points**

* Marketplace capability discovery (`agent_card.json`)
* Core task router with circuit-breaker protection
* A2A-compliant skill ids (`detailing-lead-ingest`, `detailing-booking`, `detailing-presence`, `detailing-social`, `detailing-copy`, `detailing-engage`)
* Calendly deep links via shop config (`calendly_handle`, event slugs)
* Shop dashboard at `/chroma` (this pack)

**Resilience**

Circuit breaker protection enabled on all agent executions. Outbound send gate defaults to dry-run.
