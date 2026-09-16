# SaaS revenue path (2026-09-16)

Goal: saleable SaaS that takes a card → invoice → VC can diligence.

## What is already live

- Product pages: `/products/starter` ($297), `/products/professional` ($997)
- CTA: `/signup?plan=starter` returns 200
- Health, sitemap, robots, agent-card.json
- Headers: HSTS, X-Frame-Options, nosniff, Referrer-Policy
- Settlement tape: `docs/LOOP_SETTLEMENT.md`

## What is blocked (P0)

`GET /health`:
- `stripe.detail = not_configured`
- `paypal.detail = not_configured`

The page says credit card + instant activation. The runtime cannot charge.

Homepage first screen is still genesis / floor. SKUs are on inner URLs.
Starter title has mojibake (`â€”`). No CSP header.

## Stack we have from this chat

- Railway = production site (not the Vercel genesis-gate project)
- GitHub OrderofChaos33/SINCOR2
- Linear SIN-13 (Stripe), SIN-14 (homepage)
- Canva SaaS deck job a5654471-2b01-403b-8d0b-90e77d63699d
- Grok automations = draft desk only (no X write API here)

## 14-day sequence

1. Put Stripe keys on Railway. Test card on Starter. Health flips to configured.
2. Homepage hero = Starter / Pro cards. Token stays at `/buy`.
3. Fix Starter charset. Add Content-Security-Policy.
4. Ten design-partner conversations to `/products/starter` — not genesis.
5. VC conversation after first paid invoice.

No X autoposter on `@courtpaul33`. Agent handle is a later channel once checkout clears.
