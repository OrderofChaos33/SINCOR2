# SINCOR Content Engine — Operations Manual (200–2000 pages)
**Version:** 1.0 • **Generated:** 2025-09-23 12:05:08

This manual is your canonical, end-to-end procedure to operate SINCOR’s long‑form Content Engine. 
It assumes you already have assets and prior code fragments. This document consolidates the workflow into one opinionated, reproducible playbook—dialable from **200** to **2000+** pages.

---

## Table of Contents
1. Architecture & Flow
2. Installation & Project Layout
3. Inputs & Config
4. Core Workflows (Minimal → Full)
5. Page Count Dial & Scaling
6. Output Targets & Exporters
7. Quality Gates (QA/Compliance)
8. Failure Modes & Recovery
9. Performance & Cost Controls
10. Ops Checklists (Daily/Weekly/Release)
11. Templates (Outlines, Prompts, Styles)
12. API Surface (CLI & Python)
13. Extending the Engine
14. Security, Compliance, and Traceability
15. Troubleshooting Matrix

---

## 1) Architecture & Flow

**Factory pipeline:**
- **Gather → Normalize → Expand → Compile → Export → Verify**
- **Idempotent** steps; each step can be re-run without trashing prior outputs.
- **Deterministic seeds** allow reproducible runs when needed.

```
assets/  ─┬─ raw/ (unsorted pulls)
          ├─ sorted/ (vetted brand assets)
          └─ pulls/facebook/ (FB video+flyer pulls)

engine/   ├─ gatherer.py
          ├─ expander.py      # page dial lives here
          ├─ compiler.py
          ├─ exporter.py
          └─ qa.py

outputs/  ├─ master_doc/      # PDF/DOCX/MD
          └─ sub_exports/     # flyers, scripts, posts
```

**Key principle:** one **engine.yaml** controls input paths, brand voice, tone variants, CTA library, offer matrix, and the target **page_count**.

---

## 2) Installation & Project Layout

Drop this bundle into your repo under `sincor/content_engine_manual/` or at project root.

Recommended structure:

```
sincor/
  content_engine/
    engine.yaml
    gatherer.py
    expander.py
    compiler.py
    exporter.py
    qa.py
    libs/
  assets/
    raw/
    sorted/
    pulls/facebook/
  outputs/
    master_doc/
    sub_exports/
  docs/
    MANUAL.md  (this file)
```

> You can colocate `engine.yaml` at the repo root if you prefer—just update paths accordingly.

---

## 3) Inputs & Config

**engine.yaml** (see `config/engine.yaml` in this bundle) controls:
- Paths to **assets**, **outputs**
- **brand** metadata: company name, slogans, testimonials, guarantee text
- **tone** variants: professional, hype, poetic, technical, local-pride
- **modules** on/off toggles (blog, scripts, flyers, adsets, SOPs, whitepapers)
- **page_count** target and **sections matrix** (weighting per section family)
- **seeding** for deterministic variations
- **compliance**: terms version, disclaimer blocks, claims guardrails

---

## 4) Core Workflows

### Minimal Run (50–200 pages)
1. Sort a **small** set of assets into `assets/sorted/` (at least 1 logo, 2 photos, 1 testimonial).
2. Set `page_count: 200` in `engine.yaml`.
3. Run:
   - `python content_engine/gatherer.py --config engine.yaml`
   - `python content_engine/expander.py --config engine.yaml --size 200 --seed 42`
   - `python content_engine/compiler.py --config engine.yaml`
   - `python content_engine/exporter.py --config engine.yaml --formats pdf,md,docx`
   - `python content_engine/qa.py --config engine.yaml`
4. Inspect `outputs/master_doc/MASTER.md` (or PDF).
5. Gate with QA checklist; iterate.

### Full Run (1000–2000+ pages)
- Increase `page_count` and section weights in `engine.yaml`.
- Add additional source sets (offers, city variants, use-cases, industries).
- Parallelize Expander with batches (see §9).

---

## 5) Page Count Dial & Scaling

The **dial** is enforced in `expander.py`:
- `--size N` or `engine.yaml: page_count: N`
- Section family weights define proportional growth (e.g., 20% case studies, 10% SOPs, 15% adsets, etc.).
- **Hard stops**: cut overflow; **padding**: generate filler only from allowed template families (no empty fluff—use “Explainer”, “Comparative analysis”, “FAQ deep dives”, “SOP variants”, “Local SEO pages”, “Industry vertical mappings”).

**Rule:** content density > word count. No filler. Each page must serve a goal: persuade, educate, or rank.

---

## 6) Output Targets & Exporters

- Master: `MASTER.md` → compiled to **PDF**, **DOCX**.
- Sub-exports:
  - **Flyers**: sizes A4/Letter, headline+hero+CTA variants
  - **Adsets**: Google, Meta, Local Services—headline/desc matrices
  - **Blog**: pillar + cluster strategy
  - **Scripts**: 15s/30s/60s voiceover + storyboard frames
  - **Email drips**: 5–12 touch cadence with CTAs and objections handling
  - **SOPs**: internal process docs, checklists, safety, QA
  - **Whitepapers**: authority & trust anchors

---

## 7) Quality Gates (QA/Compliance)

- **Claim checker**: strip or footnote unverifiable claims.
- **Consistency**: brand name, phone, URL, offer, guarantee text.
- **Local compliance**: advertising rules, disclosures, refund/guarantee language.
- **Readability**: ± 7–10th grade where needed; expert tone for whitepapers.
- **Diversity**: avoid repetitive n-grams; enforce style rotation.

Use `checklists/QA_RELEASE.md`.

---

## 8) Failure Modes & Recovery

- **Duplicate sections**: enable dedupe in compiler merge phase.
- **Incoherent expansions**: tighten templates; freeze tone; reseed.
- **Bloat without value**: raise minimum-info density per section.
- **Broken images/links**: run asset validator; auto-rewrite relative paths.
- **PDF export errors**: fallback to MD/DOCX, then re-render.

See `checklists/TROUBLESHOOTING.md`.

---

## 9) Performance & Cost Controls

- Batch expansion in **shards** (e.g., 200 pages per shard) then compile.
- Cache expensive steps; key by `seed+template+section`.
- Pre‑render static pieces (testimonials, guarantee, pricing cards).
- Use “budget caps” per run: max tokens / time per module.

---

## 10) Ops Checklists

- `checklists/DAILY_OPS.md`
- `checklists/WEEKLY_OPS.md`
- `checklists/RELEASE_READY.md`

---

## 11) Templates

See `templates/`:
- **Outlines/**: book, media kit, whitepaper, SOPs
- **Prompts/**: long-form, adsets, scripts, email drips
- **Styles/**: tone transforms, CTA banks, offer matrices

---

## 12) API Surface

### CLI
```
python content_engine/gatherer.py --config engine.yaml
python content_engine/expander.py --config engine.yaml --size 1200 --seed 1337
python content_engine/compiler.py --config engine.yaml --shards 6
python content_engine/exporter.py --config engine.yaml --formats pdf,md,docx
python content_engine/qa.py --config engine.yaml --strict
```

### Python
```python
from content_engine import run
run.pipeline(config="engine.yaml", size=1200, strict=True)
```

---

## 13) Extending the Engine

- Add new **section families** by dropping a template folder + weight in `engine.yaml`.
- Register exporters (EPUB, HTML site, PowerPoint pitch).
- City‑/niche‑expansions: matrix in `config/variants.json`.

---

## 14) Security, Compliance, Traceability

- Immutable run logs → `outputs/runlogs/`
- Embed run‑metadata at document head (seed, config hash).
- Terms/Privacy templates in `compliance/`.
- “No medical/financial claims” guard by default unless documented.

---

## 15) Troubleshooting Matrix

| Symptom | Probable Cause | Fix |
|---|---|---|
| Repetitive sections | weak template pool | add variants, raise dedupe threshold |
| Broken TOC | headings missing | enforce h2/h3 policy in compiler |
| Thin pages | mis-weighted families | adjust weights; enforce min info density |
| Export fails | dependency missing | fallback to MD; re-render later |

---

**This manual is the canonical reference.** Update `engine.yaml`, run minimal → full, and ship.
