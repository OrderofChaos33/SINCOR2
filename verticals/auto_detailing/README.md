# Auto Detailing Vertical Pack — CHROMA

**Domain:** Autonomous growth OS for auto detailing, ceramic coating, PPF, and tint shops.

**Why this pack exists**

Detailers lose jobs to speed, not skill. A customer Googles three shops and books the first one that answers. CHROMA is the swarm that answers, qualifies, quotes, and books — then keeps the site, reviews, and socials compounding while the bays are full.

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

**Resilience**

Circuit breaker protection enabled on all agent executions.
