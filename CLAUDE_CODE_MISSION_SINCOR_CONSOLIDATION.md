# SINCOR Consolidation Mission (Claude Code) — Surgical Edition
**Owner:** Court (Clinton Auto Detailing)  
**Timezone:** America/Chicago

## Objective
Unify *all* project-related artifacts scattered across the device into **`sincor-clean`** as the **working consolidation area**, while keeping **`SINCOR2`** (both local and GitHub) **sacred and pristine**. Only copy **necessary missing components** into `SINCOR2` with surgical precision. No redundancies unless absolutely necessary. No destructive operations. All steps verifiable and logged.

## Non‑Negotiable Rules
- **Sacred Repo:** `SINCOR2` is the perfected live build. Do **not** overwrite or bulk-import into it. Only add missing necessities after verification.
- **Primary Sink:** `sincor-clean` receives *everything* from the device (deduplicated), including conflicts and large assets.
- **Two‑Pass Discipline:** Discover → Stage (dry run) → Copy (real) → Verify → (optional) selectively promote to `SINCOR2`.
- **No deletes** of originals. Never modify source files in place.
- **No secrets committed.** `.env`, keys, tokens go to `sincor-clean/secrets_local/` and get `.gitignore` coverage.
- **Exclusion set:** Skip heavy junk and caches (`node_modules`, `.git`, `.cache`, `__pycache__`, `.venv`, `venv`, `.mypy_cache`, `dist`, `build`, `.parcel-cache`, `DerivedData`, `tmp`, `Temp`).
- **Provenance:** Every action emits machine-readable logs: `manifest_*.jsonl`, `copy_plan.csv`, `copy_log.csv`, `collision_report.csv`, `promotions.csv`.
- **Reversibility:** If unsure, put it in `sincor-clean/_incoming_conflicts/` *not* in `SINCOR2`.

## High‑Signal Keywords (ranked)
sincor, sincor2, clinton, clinton auto detailing, detailing, auto_detail, booking, agents, observability, analytics, monetization, revenue, flyer, canva, promo, calendar, google, compliance, jwt, limiter, security, dashboard, archetype, agent_analytics, agent_interaction, almanak

## File Types of Interest
Code: .py .js .ts .tsx .json .yml .yaml .md .rst .ini .cfg .toml .sql .ps1 .bat .sh .html .css  
Assets: .png .jpg .jpeg .svg .gif .webp  
Docs/Data: .pdf .docx .pptx .xlsx .csv .ipynb  
Archives: .zip .7z .rar (store in `archives/`)

---

# Phase 0 — Setup & Safety (PowerShell)
1. Create staging and reports:
```powershell
$env:TZ = "America/Chicago"
$STAGING = "C:\_sincor_consolidation"
$REPORTS = "$STAGING\reports"
$LOGS = "$STAGING\logs"
New-Item -ItemType Directory -Force -Path $STAGING,$REPORTS,$LOGS | Out-Null

$S2 = "C:\Users\cjay4\OneDrive\Desktop\SINCOR2"
$CLEAN = "C:\Users\cjay4\OneDrive\Desktop\sincor-clean"
$ROOTS = @(
  "$HOME\Desktop",
  "$HOME\Documents",
  "$HOME\Downloads",
  "$HOME\OneDrive\Desktop",
  "$HOME\OneDrive\Documents",
  "C:\", "D:\", "E:\"
)
$ROOTS | Out-File "$REPORTS\roots.txt" -Encoding utf8
```

2. Safeguards:
- Confirm `$S2` exists; if not, **pause and ask for the correct path**.  
- Do **not** write into `$S2` during Phases 1–3.

---

# Phase 1 — Device‑Wide Discovery (Python)
Create `C:\_sincor_consolidation\scan_device.py` that:
- Walks `$ROOTS` recursively.
- Skips excluded dirs.
- Scores each file by keyword hits in path/name.
- Emits `manifest_raw.jsonl` (one object per line) with: `path, size, mtime_iso, mtime_epoch, sha256, topic_score, ext`.
- Also writes `manifest_preview.csv` (path, sizeMB, sha12, topicScore).

**Claude Code Task:** Write and run this Python script, then save console output to `logs/scan.log`. Finally produce `reports/top200.csv` ranked by topic_score and recency.

---

# Phase 2 — Dedupe & Copy Plan (Dry)
- Create `dedupe_map.json` by SHA256; choose the newest path; **prefer paths within SINCOR/SINCOR2** if equal recency.
- Build `copy_plan.csv` to route files into `sincor-clean` under a normalized tree:  
  - `code/` (code & configs)  
  - `data/` (csv/xlsx/json/ipynb/sql)  
  - `docs/` (md/pdf/docx/pptx)  
  - `assets/` (images)  
  - `scripts/` (.ps1/.sh/.bat/.py utilities)  
  - `archives/` (.zip/.7z/.rar)  
  - `secrets_local/` (env/keys)  
  - `unknown/` (anything else)  
- Do **not** copy yet. Emit stats: candidates, kept, duplicates pruned, large assets (>200MB) to `large_assets.csv`.

---

# Phase 3 — Execute Copy to `sincor-clean` (Real)
- Create folders in `sincor-clean` if missing (`code`, `data`, `docs`, `assets`, `scripts`, `archives`, `secrets_local`, `unknown`, `_incoming_conflicts`).  
- Copy per `copy_plan.csv` preserving relative folder structure when possible.  
- Log to `copy_log.csv` with `src, dst, sha256, status`.
- Re-scan `sincor-clean` to build `manifest_staged.jsonl`.  
- Produce integrity reports: `diff_counts.txt`, `missing_after_copy.csv`, `by_dir_summary.csv`.

**Outcome:** `sincor-clean` now contains **everything relevant** from the device (deduped), plus **all conflicts** in `_incoming_conflicts/`. `SINCOR2` untouched so far.

---

# Phase 4 — Surgical Promotion → `SINCOR2` (Selective Only)
**Goal:** Only add **missing necessities** into `SINCOR2`. Nothing wholesale.

1. Dry‑diff staged vs `SINCOR2` (no writes):
```powershell
robocopy "$CLEAN" "$S2" /L /E /NFL /NDL /NJH /NJS /NP /NS /NC > "$REPORTS\robocopy_dry.txt"
```
2. Build `promotion_candidates.csv` by comparing `manifest_staged.jsonl` with a fresh `manifest_s2.jsonl` (Claude Code to write script):
   - Include only items **required** for functionality, docs, or pipeline that are **absent** in `SINCOR2`.
   - Exclude anything already present by hash or functionally duplicated.
3. For each candidate, check collisions (same relpath, different hash):
   - **Default action:** do **not** overwrite. Place the newer/different version under `sincor-clean/_incoming_conflicts/` and **do not** push to `SINCOR2` unless explicitly whitelisted per `promotions_whitelist.txt`.
4. Execute *approved* promotions:
```powershell
# For rows in promotions.csv with status=approved:
# Copy from $CLEAN to $S2 under the designated target path
```
5. Emit `promotions.csv` with: `src, target_relpath, reason, approved_by, result`.

---

# Phase 5 — Verify & Clean
In `SINCOR2` (read‑only except promoted files):
- Update `.gitignore` (ensure `secrets_local/`, `.env`, `.venv/`, caches excluded).
- Run JSON/YAML parse checks; log to `reports/validation.log`.
- Optional test run (`pytest`, etc.) — log to `logs/tests.log`.
- Generate `reports/final_tree.txt` and `manifest_final.jsonl`.
- **Acceptance:** Counts in `manifest_final.jsonl` ≥ `manifest_s2.jsonl` + promoted rows; 0 missing from approved promotions; no parsing errors; no secrets staged.

---

# Phase 6 — Optimize & Refine (Only if Safe)
- Non‑destructive structure normalization (create folders if missing; never move working code blindly):
  - `/app or /src`, `/scripts`, `/docs`, `/configs`, `/assets`, `/tests`.
- Add/update docs: `README.md` (quickstart, booking URL), and mkdocs config if present.
- CI: add/update minimal workflows if they already exist; otherwise **skip**.
- `.env.example` only if env usage detected; never commit real secrets.
- Commit messages (conventional):
  - `chore: consolidate artifacts into sincor-clean (no changes to SINCOR2)`
  - `feat: promote missing necessary components to SINCOR2 (surgical)`
  - `docs: refresh monetization/agents/ops`

---

# Phase 7 — Push
- Push `sincor-clean` to its own archival repo (optional).  
- Push `SINCOR2` only after **promotions.csv** approved and tests (if any) pass.

---

## Deliverables Claude Must Produce
- `C:\_sincor_consolidation\reports\manifest_raw.jsonl`
- `.../manifest_preview.csv`, `top200.csv`
- `.../dedupe_map.json`, `copy_plan.csv`, `copy_log.csv`
- `.../manifest_staged.jsonl`, `diff_counts.txt`, `by_dir_summary.csv`, `large_assets.csv`
- `.../robocopy_dry.txt`, `manifest_s2.jsonl`
- `.../promotion_candidates.csv`, `promotions_whitelist.txt`, `promotions.csv`
- `.../validation.log`, `final_tree.txt`, `manifest_final.jsonl`

---

## Guardrails
- If any acceptance check fails, **stop and report**. Do **not** continue to promotion.
- If a file might be a secret, assume it is; place only in `sincor-clean/secrets_local/` and never commit.
- If the same logical artifact exists in multiple places, **choose the best version by**: recency → location (prefer SINCOR/SINCOR2 lineage) → size → topic_score.
- All paths are logged; nothing is “forgotten.”

---

## Optional: End‑of‑Run Summary
Claude prints: items scanned, staged, promoted; conflicts held; next actions.
