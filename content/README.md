# Sincor Content Agent

Autonomous blog/SEO engine for Sincor. Writes, publishes, and measures blog content with zero human input after setup.

## What it does

| Feature | Description |
|---------|-------------|
| Blog post generator | 2,000+ word SEO-optimized posts via Claude |
| Content calendar | 12-week rolling calendar (2 posts/week) |
| Comparison pages | Sincor vs [Competitor] auto-generated pages |
| WordPress auto-publish | Schedule and publish via WP REST API |
| Analytics tracking | Track views, CTA clicks, keyword rankings |
| Auto-refresh | Rewrites low-traffic posts every 90 days |

## Quick Start

```bash
# Generate one blog post
python -m sincor2.content_agent blog --keyword "CRM sync automation" --type how-to

# Generate 12-week calendar
python -m sincor2.content_agent calendar --print

# Generate Sincor vs Zapier comparison page
python -m sincor2.content_agent compare --competitor Zapier

# Run one full autonomous cycle
python -m sincor2.content_agent cycle

# Start daemon (runs every 48h)
python -m sincor2.content_agent daemon

# Analytics report
python -m sincor2.content_agent analytics
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | — | Claude API key |
| `CONTENT_AGENT_ENABLED` | — | `false` | Set `true` to auto-start with app |
| `CONTENT_INTERVAL_HOURS` | — | `48` | Hours between cycles |
| `CONTENT_MODEL` | — | `claude-haiku-4-5` | Claude model (haiku = cheap, sonnet = better) |
| `WP_API_URL` | — | — | WordPress REST API URL (e.g. `https://getsincor.com`) |
| `WP_USERNAME` | — | — | WordPress username |
| `WP_APP_PASSWORD` | — | — | WordPress application password |
| `GA_PROPERTY_ID` | — | — | Google Analytics property ID |
| `GSC_SITE_URL` | — | — | Google Search Console site URL |

## Admin API Endpoints

All require admin JWT token (same as existing admin panel).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/content/status` | GET | Scheduler status, recent posts, upcoming calendar |
| `/admin/content/generate` | POST | Trigger post generation `{keyword, type, publish}` |
| `/admin/content/calendar` | GET | View full calendar |
| `/admin/content/calendar` | POST | Regenerate calendar |
| `/admin/content/analytics` | GET | Traffic, CTA performance, top/low performers |

## Content Calendar Overview (12 Weeks)

Starts April 7, 2026 • 24 posts total • 2 per week (Tue + Fri)

| Week | Date | Keyword | Type | Difficulty |
|------|------|---------|------|-----------|
| 1 | Apr 7 | duplicate contacts cost your business | how-to | ⭐⭐ |
| 1 | Apr 10 | duplicate contacts CRM | comparison | ⭐⭐⭐ |
| 2 | Apr 14 | CRM deduplication software | how-to | ⭐⭐⭐ |
| 2 | Apr 17 | how to sync HubSpot and Salesforce | alternatives | ⭐⭐⭐ |
| 3 | Apr 21 | PieSync alternative | how-to | ⭐⭐⭐ |
| 3 | Apr 24 | reduce CRM data quality issues | case-study | ⭐⭐⭐ |
| 4 | Apr 28 | CRM sync automation | how-to | ⭐⭐⭐⭐ |
| 4 | May 1 | automated lead enrichment | industry-trend | ⭐⭐⭐⭐ |
| 5 | May 5 | SalesOps automation tools | comparison | ⭐⭐⭐⭐⭐ |
| 5 | May 8 | Zapier alternatives for CRM | how-to | ⭐⭐⭐⭐⭐ |
| 6 | May 12 | best CRM automation tools 2024 | alternatives | ⭐⭐⭐⭐⭐ |
| 6 | May 15 | HubSpot Operations Hub alternative | case-study | ⭐⭐⭐⭐⭐⭐ |
| 7-12 | May-Jun | (AI agents, outbound automation, RevOps) | mixed | 3-7 |

**Focus order:** Start with low-difficulty keywords (⭐⭐-⭐⭐⭐) for quick ranking wins, then attack medium-difficulty as domain authority grows.

## Competitor Comparison Pages

Ready to generate for:
- **Zapier** — task-based pricing vs Sincor's CRM-specific AI
- **HubSpot Operations Hub** — HubSpot-only vs multi-CRM
- **Make** — visual automation vs purpose-built SalesOps
- **Xano** — backend builder vs CRM automation platform
- **PieSync** — deprecated tool vs modern alternative

```bash
python -m sincor2.content_agent compare --competitor "HubSpot Operations Hub" --publish
```

## Files

```
content/
├── README.md              # This file
├── calendar.json          # 12-week content calendar
├── content_agent.db       # Posts + analytics database
├── report_*.txt           # Analytics reports
└── blog/
    ├── crm-sync-automation.md
    ├── sincor-vs-zapier.md
    └── ...                # Generated posts
```

## Architecture

```
content_agent.py          ← Core engine (all 5 features)
content_scheduler.py      ← APScheduler integration (48h cycle)
mvp_app.py                ← Auto-starts scheduler, adds /admin/content/* routes
```

## Activation (Railway)

Set these Railway env vars to go live:

```
CONTENT_AGENT_ENABLED=true
CONTENT_MODEL=claude-haiku-4-5
WP_API_URL=https://getsincor.com          (if using WordPress)
WP_USERNAME=your-wp-user
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

Then redeploy. The agent will:
1. Start in background 60s after app boot
2. Check the calendar for posts due in next 48h
3. Generate + publish them
4. Refresh any low-performers
5. Repeat every 48h forever

**Cost estimate:** ~2 posts/cycle × ~3,000 tokens/post × haiku pricing ≈ $0.003/cycle ≈ **$0.05/month**
