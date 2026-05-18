# SINC Launch Content Playbook

**Launch:** 2026-05-19, 00:00–03:00 UTC (US evening prime)
**Drafted:** 2026-05-18, evening session
**Channels owned:** Farcaster, Telegram, X (Twitter), Facebook (Railway env vars wired)
**Approach:** SINCOR agents draft → user approves → manual or scripted publish.
The fully automated `launch_content_engine` per spec §5.5 lands as Plan 3 *after* launch — too big to build in <24h.

---

## Hard rules (non-negotiable, baked into every artifact)

These are the constraints from [[launch_tactics_legal_line]] memory. Every post the agents draft and every reply the agents send must clear all five:

1. **All AI-generated content is disclosed.** "Posted by SINCOR Agent — AI-generated" suffix or platform-appropriate variant. FTC Endorsement Guides 2023 update.
2. **No price predictions, no return promises.** No "$1", no "100×", no "moonshot", no "fair launch valued at". Promoters making implied return promises on unregistered tokens = federal securities exposure.
3. **No wash trading, no astroturfing, no fake testimonials.** No agent-to-agent buy/sell rings. No sockpuppet replies pretending to be different humans. No paid undisclosed shills.
4. **Factual on-chain data only.** Contract address, CertiK score, holder count, burn count, audit-DB status. Anything claimed must link to a Basescan or audit URL.
5. **Human approves every public post.** No agent posts to a public channel without user review. Agents can DRAFT freely; only the user clicks send (or runs the cleared script).

The Auditor archetype runs a pre-publish check against rules 1–4 on every draft. Anything that fails goes back to Synthesizer for rewrite.

---

## Channel strategy (priority order for max exposure)

| Rank | Channel | Why | Lift expected |
|---|---|---|---|
| 1 | **X / Twitter** | Largest crypto launch audience; thread format works; quote-tweet engagement is high-leverage | Highest |
| 2 | **Farcaster** | Base-native — direct line to Coinbase ecosystem; /base + /defi channels are the highest-quality buyers per dollar | High (concentrated) |
| 3 | **Telegram** | Real-time community during launch window; existing Base trading groups (user must already be a member — DO NOT spam-join then post) | Medium (depends on group access) |
| 4 | **Facebook** | Non-crypto-native audience; explains what SINCOR does (the agent platform) more than what SINC is | Low for SINC, useful for SINCOR brand |

DEXScreener + DEXTools listings happen automatically when the V4 pool is live with >$1k liquidity. No content cost.

---

## Agent role assignments

The 7 SINCOR archetypes map cleanly to the launch workflow:

| Archetype | Role on launch | Concrete deliverables |
|---|---|---|
| **Scout** | Discovery + intel | Identifies the 5-10 small KOLs to DM; finds the trending Base/AI topics at launch hour to ride; pulls live on-chain stats for posts |
| **Synthesizer** | Drafting | Writes all post variants, threads, reply templates, KOL DMs, the Facebook long-form, the Telegram pinned message |
| **Builder** | Scripts + automation | Wires the cross-post adapters using Railway env vars; sets up a scheduled `T-0 broadcast` script (user runs it); builds the human-approval queue if needed |
| **Negotiator** | Outreach | Personalizes the 5-10 KOL DMs (drafts only — user reviews + sends); drafts replies to comments during launch hour |
| **Caretaker** | Hygiene + dedup | Strips duplicate content across channels (same launch tweet ≠ same Farcaster cast — they're different audiences); redacts any PII or wallet keys from drafts |
| **Auditor** | Compliance + fact-check | Runs every draft against the five hard rules; verifies every on-chain claim against Basescan; flags anything that even smells like a price prediction |
| **Director** | Sequencing | Owns the launch-hour timeline below; escalates if a post gets bad reception (sentiment collapse, scam reports) and triggers the response playbook |

Pick agents from `agents/E-*.yaml` matching each archetype. The persona vectors of named agents (E-vega-02, E-auriga-01, etc.) determine voice differences across drafts — useful for variety within a single channel.

---

## Tonight's deliverables (content kit production)

Drafted by agents, approved by user, ready by midnight tonight (still 2026-05-18). The Auditor signs off on every artifact before it goes into the approved bucket.

### Twitter / X
- [ ] **Hero launch thread** — 7-10 tweets. Structure: hook → problem → SINC solution → audit proof → contract address → how to buy → how to verify on-chain → final CTA. Embed CertiK badge image, Basescan link, Dune dashboard link.
- [ ] **Teaser thread** — 3 tweets, T-6h. Builds anticipation without leaking the exact launch tx hash.
- [ ] **5 reply templates** for: bullish-noob, bullish-degen, bearish-FUD, "is this a scam?", "what's the contract?"
- [ ] **Quote-tweet templates** for replying to Base / Coinbase / Uniswap official launch-day tweets (only if they post about ecosystem launches — Scout watches for this in real time).

### Farcaster
- [ ] **Launch cast** in /base channel (and /defi, /ai if user has reputation there). Single cast, 320-char limit per cast, can be a thread. Include a Frame if Builder gets to it — frame can be "Buy SINC" CTA linking to /sinc.
- [ ] **Reply templates** for the 3 most likely cast replies.

### Telegram
- [ ] **Pinned launch announcement** for SINCOR's own channel.
- [ ] **3-5 announcement variants** for posting in Base-ecosystem trading groups user is ALREADY a member of. Each one tuned to the specific group's culture; no copy-paste spamming. If user isn't already a member of group X, do not post there.
- [ ] **5 reply templates** for live-chat engagement during the launch hour.

### Facebook
- [ ] **Long-form launch post** — 400-600 words. Explains SINCOR (the agent platform), positions SINC as the platform's utility token, no degen language. Audience here is non-crypto-native — link to /sinc landing page for the on-chain proof.

### Cross-channel assets
- [ ] **Image set**: SINC logo at launch-ready sizes (1500×500 Twitter banner, 1200×630 OG image, 400×400 profile, 1080×1080 IG/FB square). Use the 4-image brand system from spec §13.1.
- [ ] **30-second launch video script** (Synthesizer drafts; user records OR runs through HeyGen/Synthesia). Voiceover via ElevenLabs.
- [ ] **5-10 personalized KOL DMs** — Scout identifies the targets (3K-30K crypto-Twitter followers, Base-active, posts about new launches), Negotiator drafts personalized openers, user reviews + sends manually. **No mass-blast.**

### Week 1 sustain (drafted tonight, scheduled or queued)
- [ ] **7 daily content cards** — one per day, rotating: hook math explainer, agent burn loop, referral mechanism explained, CertiK audit walkthrough, Dune dashboard tour, "first 100 holders" celebration, week-1 recap.
- [ ] **Cross-platform schedule** — same idea, channel-tuned copy.

---

## Launch-day choreography (2026-05-19, all times UTC)

| Time | Action | Channel | Owner |
|---|---|---|---|
| **T-24h (today 00:00)** | Tonight's drafting kicks off — agents produce, user approves | All | Synthesizer + Auditor + user |
| **T-12h (today 12:00)** | All content kit signed off; KOL DM list locked; cross-post scripts smoke-tested against a sandbox account | All | Builder + user |
| **T-6h (today 18:00)** | **Teaser thread** posted on Twitter ("something's launching tonight, here's what we've fixed since v2") | X | User publishes |
| **T-3h (today 21:00)** | Teaser cast on Farcaster; Telegram "tomorrow at 00:00 UTC" pin | Farcaster + Telegram | User |
| **T-2h (today 22:00)** | KOL DMs go out — 5-10 of them, personalized, NOT identical copy | X + Farcaster DMs | User |
| **T-30min (tomorrow 23:30)** | Live launch thread initialized on Twitter ("starting in 30 min, here's what we'll post when it goes live") | X | User |
| **T-0 (tomorrow 00:00)** | Graduation tx confirmed on-chain; **hero launch thread published simultaneously** across X, Farcaster, Telegram, Facebook. Each is the channel-tuned version, not copy-paste. Director coordinates timing. | All 4 | User publishes; Builder script can fan-out the cross-channel mirrors if pre-cleared |
| **T+15min (tomorrow 00:15)** | First on-chain stats update tweet: live price, holder count, first buy tx hashes | X + Farcaster | Scout pulls data, Synthesizer drafts, Auditor checks, user posts |
| **T+1h (tomorrow 01:00)** | Reply wave — Negotiator drafts response to every legit comment within 60 min. Auditor pre-checks each. User sends. | X + Farcaster | User |
| **T+2h (tomorrow 02:00)** | **First-hour recap** — quote-tweet the hero thread with stats (volume, holders, top buyers, audit-DB statuses) | X | User |
| **T+3h (tomorrow 03:00)** | Launch window closes; community-handoff message ("we're here for questions in Telegram, see you tomorrow for the 24h recap") | All | User |
| **T+24h (2026-05-20 00:00)** | 24h recap thread — verifiable on-chain stats, top holder distribution, burn count, Dune dashboard embed | X | User |

---

## Engagement rules during launch hour (T+0 to T+3h)

- **Respond to every comment within 60 minutes.** No exceptions for first 3 hours; agents draft, Auditor checks, user clicks send.
- **Quote-tweet supporters** (not flat retweet) — amplifies your reply visibility.
- **Do not feed FUD trolls.** One factual reply with on-chain proof links, then mute. Negotiator drafts the template; never engage past one reply.
- **Pin the hero thread** on Twitter for 7 days.
- **Update Telegram pinned message** every 6 hours with stats.

---

## What gets automated tonight vs stays manual

| Task | Mode | Why |
|---|---|---|
| Drafting all content | Agent-automated (SINCOR platform) | Cheap, fast, parallel |
| Compliance review | Agent-automated (Auditor archetype) | Catches obvious issues; user does final scan |
| KOL identification | Agent-assisted | Scout pulls candidates; user picks |
| KOL DMs | **Manual send** | Platform rate-limits + spam detection; manual sending looks human |
| Hero launch posts | **Manual send** | User wants to react in real-time to early comments |
| Telegram trading-group announcements | **Manual send** | Must be a member, not a join-and-spam — each group has different norms |
| Cross-channel mirror posts | Scripted via Railway env-var keys | Once the hero is up on X, Builder script can fan out the channel-tuned variants to Farcaster + Telegram + Facebook. Cleared by user before run. |
| 24h+ scheduled drip posts | Scripted | Lower-risk repeatable content; agents draft, user batch-approves, scripts schedule |
| Reply drafting | Agent-automated | Auditor + user approve each reply before send |

---

## Measurement (what we track + where it goes)

The Dune dashboard at `dune.com/sincor/sinc` per spec §5.6 is the public credibility tool. Privately, track these in `data/launch_metrics_2026-05-19.jsonl`:

- Impressions per channel per hour
- Comment volume + sentiment (Auditor classifies)
- Click-through to /sinc landing page (server logs)
- Wallet connects + buys triggered (V4 pool tx volume)
- Audit-DB status changes (DEXTools / Dexscreener / GoPlus trust ratings as they update)
- KOL response rate from DMs

---

## Failure modes + response playbook

| Failure | Detect | Respond |
|---|---|---|
| Sentiment collapse (>30% negative replies in first hour) | Auditor flags | Director triggers "pause + investigate" — stop new posts, identify root cause, drop a single factual correction post with links, do not argue |
| A KOL publicly accuses scam | Real-time alert | One factual reply with audit links, then mute. Do NOT escalate. Auditor logs for post-launch retrospective. |
| Twitter / Farcaster throttles the account | Rate-limit error | Switch to other channels; spread the message across the remaining three; wait it out (most throttles lift in 1-24h) |
| Audit DB (DEXTools etc.) flags new contract | Watch their status pages | Submit appeal with evidence packet (CertiK link, contract verification, holder distribution); has 24-72h lead time; do not retry naively |
| Graduation tx fails on-chain (curve bug) | tx revert | Per Plan 1 error handling §8.1: communicate the issue, recommend everyone sell back to curve, redeploy v2 of curve. **Pause all launch content immediately** — Director kills the scheduled drip. |
| Compromised wallet (`0x35cb` or `0x6E36`) shows transfer activity | On-chain monitor | Per memory [[signing_authority]]: those wallets are abandoned. Any activity = somebody else has the key. Pause launch, investigate, notify holders. |

---

## Pre-launch checklist (sign off before T-0)

- [ ] All content drafts approved by user
- [ ] Auditor signoff on all five hard rules for every artifact
- [ ] KOL DM list finalized (5-10 names, hand-picked)
- [ ] Cross-post script tested on a sandbox account
- [ ] Railway env vars confirmed: Twitter, Farcaster (Neynar), Telegram bot, Facebook Page
- [ ] Image assets uploaded to /static and rendered correctly at /sinc, /axiom, /
- [ ] Pinned tweet + cast pre-staged in drafts
- [ ] Dune dashboard live and embedded on /sinc
- [ ] CertiK Skynet badge live on /sinc (per Plan 1 Task 22 gate)
- [ ] V4 pool is actually live + LP burned (per Plan 1 graduation)
- [ ] User available + sober + caffeinated for full T-0 to T+3h window

---

## What this playbook is NOT

- **Not a substitute for Plan 3** (the automated content engine per spec §5.5). That's the post-launch sustained-operations build. This is the manual launch-day version.
- **Not a paid-promotion plan.** §5.8 of spec covers paid placement (DEXScreener boost, etc.) — separate budget, separate timing, kicks in day +7.
- **Not a price-pump playbook.** No coordinated buy waves, no calls to "send it." The goal is *exposure to factual information*; price emerges from real demand.

---

## Connection to spec + plan

- Replaces **spec §5.5** (launch_content_engine module) for launch-day execution
- Cross-refs **spec §7 runbook step 16** ("Announce launch — agent burn loop active, x402 endpoint live, content engine producing")
- Cross-refs **spec §5.8** for paid placement starting day +7
- Adheres to **[[launch_tactics_legal_line]]** memory — no wash, no astroturf, all AI disclosed, factual only
- Adheres to **[[signing_authority]]** memory — agents and scripts NEVER touch private keys; user signs any on-chain action
