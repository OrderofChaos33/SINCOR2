# SINCOR Engine — Cohesive Manual (Quality‑First, Canva‑Enabled)
**Version:** 2.0  •  **Generated:** 2025-09-23 12:20:52

This manual is the canonical playbook for running SINCOR’s content engine with **quality as the only unit that matters**. It integrates **Swarm orchestration**, **Canva-polished MediaPax**, **Syndication rails**, and **BI telemetry**, without wasting prior work. The engine treats every paragraph as a strategic asset.

---

## 0) Principles
- **Quality density > word count.** No filler. Every section must persuade, teach, or prove.
- **Deterministic, inspectable runs.** Reproducible by seed and config hash.
- **Swarm over scripts.** A graph of roles with explicit state transitions.
- **Design‑first delivery.** Canva templates for final polish; no “doc-looking” output.
- **Closed feedback loop.** BI telemetry informs the next run’s choices (tones, offers, templates).

---

## 1) System Overview
**Pipeline:** `Ingest → Plan → Draft → Score → Repair → Dedupe → Pack (Canva) → Export → Syndicate → Telemetry`
- **Ingestor**: pulls brand assets (logos, offers, testimonials, prior winners) and normalizes.
- **Planner**: agenda + tone schedule + CTA rotation. Ensures variety/entropy.
- **Drafters[N]**: parallel shard writers. Produce first-pass, high-density copy blocks.
- **Scorer**: readability band, information-density, CTA presence, evidence checks.
- **Repairer**: tightens language, injects proof/snippets, raises score to threshold.
- **Dedupe/Reducer**: MinHash/LSH over n‑gram shingles → remove near-duplicates.
- **Packager (MediaPax)**: fills Canva templates with vetted copy + assets.
- **Exporter**: PNG/MP4/PDF/DOCX/MD bundles ready for channels.
- **Syndicator**: WordPress posts, Meta creatives, Google Ads sheets, email drips.
- **Telemetry**: emits run metadata to BI (see §6).

**Data contracts:** see `specs/contracts.md` + `bi/events_schema.json`.

---

## 2) Repository Layout
```
sincor/
  content_engine/
    engine.yaml
    specs/                 # role specs & contracts
    qa_scoring.md          # thresholds & formulas
    swarm_graph.md         # orchestration diagram
  canva/
    templates/             # design shells (IDs/URLs documented)
    manifest.json          # mapping content slots → template fields
    bulk_template.csv      # for CSV-driven batch renders
  syndication/
    wordpress_push.md
    meta_ads_push.md
    google_ads_push.md
  bi/
    events_schema.json
    dashboards/README.md
  checklists/
    DAILY_OPS.md
    WEEKLY_OPS.md
    RELEASE_READY.md
    TROUBLESHOOTING.md
  outputs/
    (generated at runtime)
```

---

## 3) Engine Configuration (`engine.yaml`)
- **paths**: assets, outputs, canva folders.
- **brand**: company, URL, phone, slogan, guarantee.
- **tones**: rotation schedule.
- **modules**: which artifacts to generate (flyers/adsets/scripts/blog/emails/whitepapers/SOPs).
- **quality**: scoring thresholds, readability band, dedupe % max.
- **swarm**: role concurrency & shard sizing.
- **canva**: template IDs, slot mappings, export formats.
- **syndication**: endpoints/keys/toggles per channel.
- **telemetry**: run metadata + sink (CSV/SQLite/HTTP).

See `content_engine/engine.yaml` in this bundle for a ready baseline.

---

## 4) Quality Engine
**Scoring dimensions (per section):**
- **Readability band**: persuasion pieces target 7–10th; whitepapers may exceed.
- **Information density**: minimum key-points per 200–300 words; forbid fluff phrases.
- **Evidence presence**: testimonial, stat, case snippet, or procedural SOP element.
- **CTA cadence**: public-facing sections expose a relevant CTA every 2–3 sections.
- **Brand integrity**: phone/URL/slogan/guarantee consistent with config.
- **Similarity**: Jaccard over shingles; reject >0.80 against recent neighbors.

**Loop:** `Draft → Score → Repair` until passing **Quality Threshold Q≥0.92** (adjustable).  
See `content_engine/qa_scoring.md` for formulas and examples.

---

## 5) MediaPax via Canva
**Goal:** Every outward asset looks agency-grade.

**Templates (examples):**
- Flyers: A4/Letter, 3 variants (Hero/Proof/Offer/CTA).
- Social: IG Story, IG Square, FB Post, YT Thumbnail.
- Ad Creatives: headline cards, callout tiles.
- Whitepaper/Pitch Deck: multi-page typography rules baked in.
- Testimonial Cards: pull quotes + author/company.

**Mapping:** `canva/manifest.json` defines slots:
```json
{
  "Flyer_A4_V1": {
    "template_id": "TEMPLATE_ID_A4_V1",
    "slots": {
      "Headline": "{{headline}}",
      "Subhead": "{{subhead}}",
      "Testimonial": "{{testimonial}}",
      "CTA": "{{cta}}",
      "Phone": "{{brand.phone}}",
      "URL": "{{brand.website}}",
      "HeroImage": "{{asset.hero}}"
    },
    "export": ["PDF", "PNG"]
  }
}
```
**Bulk:** duplicate rows in `canva/bulk_template.csv` to mass‑render variant families.

**Flow:** After scoring passes, the **Packager** replaces placeholders and calls Canva export (or prepares a CSV for manual/batch fill).

---

## 6) BI & Telemetry (feedback loop)
**Event:** `content_unit_emitted`
- `run_id`, `seed`, `tone`, `template_id`, `cta_id`, `section_family`, `score`, `similarity`, `export_ids`

**Event:** `syndication_published`
- `channel` (wp/meta/google/email), `asset_id`, `target_url`, `campaign_id`

**Event:** `conversion_signal`
- `channel`, `creative_id`, `clicks`, `leads`, `bookings`, `revenue`

**Dashboards:** See `bi/dashboards/README.md` for core charts:
- Template win-rate by tone/CTA
- Offer elasticity (discount vs conversion)
- Channel mix ROAS
- Decay curves for content freshness

---

## 7) Syndication Rails
- **WordPress**: REST posts (title, content, status=publish/schedule), category/tags, featured media. Map pillar articles + images. See `syndication/wordpress_push.md`.
- **Meta Ads**: creatives from Canva exports; naming convention ties creative → run_id/cta_id. RSAs (Advantage+) ready; ensure policy-safe copy. See `syndication/meta_ads_push.md`.
- **Google Ads**: asset‑based RSAs via CSV/Sheets/API. Ensure headlines/desc lengths. See `syndication/google_ads_push.md`.
- **Email**: drip sequences (5–12) with subject/preview/CTA links (ESP-agnostic CSV export).

**Safety:** Always run compliance lints before publish (claims, restricted categories, PII).

---

## 8) Checklists (must pass)
- **Daily**: update assets inbox; run partial; publish 1–2 social sets; refresh top performers.
- **Weekly**: rotate tones; ship 2 pillar clusters; new flyer variants; review BI.
- **Release**: QA pass; dedupe <5%; compliance headers; metadata embedded.

(Full checklists under `/checklists/`.)

---

## 9) Troubleshooting (fast paths)
- **Looks templated** → raise style entropy, widen tone rotation, add new CTA phrasing.
- **Thin output** → increase evidence minimums; inject SOP or case fragments.
- **Export fails** → fall back to MD/DOCX; re-render later.
- **Underperforming creatives** → pivot offer, swap template family, adjust headline archetypes.

---

## 10) Integration Steps (no code surprises)
1. Copy this bundle into your repo (see §2 layout).
2. Fill `engine.yaml` with brand data, paths, toggles.
3. Populate `canva/manifest.json` with real template IDs and slot names.
4. Drop initial assets into `assets/sorted` and `assets/raw` (if auto‑classifying).
5. Run minimal cycle (quality‑first mode) → export Canva → publish → measure.
6. Iterate based on BI signals; keep a change log per run in `outputs/runlogs/`.

---

## 11) Security & Compliance
- Immutable run logs with config hash.
- No unverifiable claims; mandatory disclaimers injected on public docs.
- Strip PII from internal SOPs before external distribution.
- Respect platform policies for Ads and Email (spam/claims rules, unsub links).

---

## 12) What “Done” Looks Like
- You press go → out comes **polished MediaPax** + scheduled posts/ads + telemetry.
- You review BI → the next run automatically biases toward winners.
- No arguing with page counts; **quality thresholds** are the only gates.
