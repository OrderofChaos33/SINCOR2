"""
SINCOR Content Agent — Autonomous Blog & SEO Engine
====================================================
Writes high-quality, SEO-optimized blog posts that drive inbound traffic
and establish Sincor as a thought leader in CRM automation and SalesOps.

Capabilities:
  1. Blog post generator        — 2000+ word SEO-optimized posts
  2. Content calendar           — 12-week rolling calendar
  3. Comparison page generator  — Sincor vs [Competitor] pages
  4. Website integration        — Auto-publish to WordPress REST API
  5. Analytics hook             — Track posts, CTAs, keyword rankings

Usage:
    # One-shot blog post
    python -m sincor2.content_agent blog --keyword "CRM sync automation"

    # Generate 12-week calendar
    python -m sincor2.content_agent calendar

    # Generate comparison page
    python -m sincor2.content_agent compare --competitor "Zapier"

    # Run autonomous cycle (every 48h)
    python -m sincor2.content_agent daemon

    # Refresh low-performers
    python -m sincor2.content_agent refresh
"""

import os
import re
import json
import time
import uuid
import sqlite3
import logging
import argparse
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import anthropic

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] content_agent: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("content_agent")

# ─── Config ───────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONTENT_DIR = PROJECT_ROOT / "content" / "blog"
CONTENT_DB = PROJECT_ROOT / "content" / "content_agent.db"
CALENDAR_PATH = PROJECT_ROOT / "content" / "calendar.json"

CONTENT_DIR.mkdir(parents=True, exist_ok=True)
CALENDAR_PATH.parent.mkdir(parents=True, exist_ok=True)

SINCOR_BRAND = {
    "name": "Sincor",
    "url": "https://getsincor.com",
    "free_trial_url": "https://getsincor.com/buy",
    "tagline": "43 AI agents. One unstoppable growth engine.",
    "description": (
        "Sincor is an AI-powered CRM automation and SalesOps platform. "
        "43 specialized AI agents handle contact deduplication, CRM sync, "
        "lead enrichment, outbound automation, and pipeline intelligence — "
        "so your sales team can focus on closing."
    ),
    "features": [
        "Automatic duplicate contact detection and merge",
        "Real-time CRM sync across HubSpot, Salesforce, Pipedrive",
        "AI-powered lead scoring and enrichment",
        "Outbound sequence automation with personalization",
        "SalesOps analytics and pipeline forecasting",
        "No-code workflow builder (replace Zapier for CRM tasks)",
        "43 specialized AI agents working 24/7",
    ],
    "pricing_note": "Starter plan from $49/month. Free trial available.",
}

# ─── SEO Keyword Bank ─────────────────────────────────────────────────────────
# (keyword, seo_difficulty 1-10, content_type, priority)
KEYWORD_BANK = [
    # Core product keywords
    ("CRM sync automation", 4, "how-to", 1),
    ("duplicate contacts CRM", 3, "how-to", 1),
    ("SalesOps automation tools", 5, "comparison", 1),
    ("Zapier alternatives for CRM", 5, "alternatives", 1),
    ("HubSpot Operations Hub alternative", 6, "comparison", 1),
    ("automated lead enrichment", 4, "how-to", 1),
    ("CRM deduplication software", 3, "comparison", 1),
    ("AI sales automation", 7, "industry-trend", 2),
    ("outbound sales automation", 6, "how-to", 2),
    ("sales pipeline automation", 6, "how-to", 2),
    # Long-tail opportunities
    ("how to sync HubSpot and Salesforce", 3, "how-to", 1),
    ("duplicate contacts cost your business", 2, "case-study", 1),
    ("best CRM automation tools 2024", 5, "comparison", 1),
    ("PieSync alternative", 3, "alternatives", 1),
    ("Make vs Zapier for CRM", 4, "comparison", 2),
    ("automate CRM data entry", 3, "how-to", 2),
    ("lead enrichment without manual work", 3, "how-to", 2),
    ("SalesOps team efficiency", 4, "industry-trend", 2),
    ("AI agents for sales teams", 6, "industry-trend", 2),
    ("reduce CRM data quality issues", 3, "how-to", 1),
    # Comparison/competitor
    ("Sincor vs Zapier", 2, "comparison", 1),
    ("Sincor vs HubSpot Operations Hub", 3, "comparison", 1),
    ("Sincor vs Make.com", 2, "comparison", 1),
    ("Sincor vs Xano", 2, "comparison", 1),
    ("Sincor vs PieSync", 2, "comparison", 1),
    # Industry trends
    ("CRM automation trends 2024", 5, "industry-trend", 3),
    ("future of SalesOps AI", 6, "industry-trend", 3),
    ("RevOps automation best practices", 5, "industry-trend", 3),
]

COMPETITOR_DATA = {
    "Zapier": {
        "tagline": "Connect your apps and automate workflows",
        "pricing": "Free (100 tasks/month), Starter $29.99/mo, Professional $73.50/mo, Team $103.50/mo",
        "strengths": ["5,000+ integrations", "No-code interface", "Large ecosystem", "Well-known brand"],
        "weaknesses": [
            "Task-based pricing gets expensive fast",
            "Not CRM-specific",
            "No native deduplication",
            "No AI agents or intelligence layer",
            "Becomes complex for multi-step CRM workflows",
        ],
        "best_for": "General app-to-app automation with simple triggers",
        "sincor_wins": "CRM-specific intelligence, deduplication, lead scoring, AI agents",
    },
    "HubSpot Operations Hub": {
        "tagline": "Operations software that keeps your business running",
        "pricing": "Free, Starter $20/seat/mo, Professional $720/mo, Enterprise $2,000/mo",
        "strengths": ["Deep HubSpot integration", "Data sync", "Programmable automation", "RevOps reporting"],
        "weaknesses": [
            "Only works within HubSpot ecosystem",
            "Professional tier very expensive",
            "Limited to HubSpot data model",
            "No cross-CRM sync",
            "No outbound automation",
        ],
        "best_for": "HubSpot-first teams who are already paying for the suite",
        "sincor_wins": "Multi-CRM support, AI agents, outbound automation, lower cost",
    },
    "Make": {
        "tagline": "The visual platform for automation",
        "pricing": "Free (1,000 ops/month), Core $10.59/mo, Pro $18.82/mo, Teams $34.12/mo",
        "strengths": ["Visual workflow builder", "Affordable", "Flexible", "1,000+ integrations"],
        "weaknesses": [
            "Steep learning curve",
            "Not CRM-specific",
            "No AI intelligence layer",
            "Operations limits scale badly",
            "No native lead enrichment",
        ],
        "best_for": "Technical users who want flexible automation at low cost",
        "sincor_wins": "CRM focus, AI agents, no ops limits, built-in intelligence",
    },
    "Xano": {
        "tagline": "No-code backend development platform",
        "pricing": "Free, Launch $29/mo, Scale $75/mo, Business $150/mo",
        "strengths": ["No-code backend", "Scalable APIs", "Database + logic layer", "Fast prototyping"],
        "weaknesses": [
            "Not automation-focused",
            "Requires technical setup",
            "No pre-built CRM integrations",
            "No AI agents",
            "Not a SalesOps tool",
        ],
        "best_for": "Building custom app backends, not CRM automation",
        "sincor_wins": "Purpose-built for SalesOps, 43 AI agents, zero code required",
    },
    "PieSync": {
        "tagline": "Two-way contact sync between your business apps",
        "pricing": "Acquired by HubSpot (deprecated as standalone product in 2021)",
        "strengths": ["Was good at two-way sync", "Simple interface", "Contact-focused"],
        "weaknesses": [
            "No longer exists as standalone product",
            "Was limited to contact sync only",
            "No AI or intelligence features",
            "No deduplication",
            "No outbound automation",
        ],
        "best_for": "Historical alternative — now replaced by HubSpot Operations Hub",
        "sincor_wins": "Full replacement: sync + dedup + enrich + automate + AI agents",
    },
}


# ─── Database ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CONTENT_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                keyword TEXT NOT NULL,
                content_type TEXT NOT NULL,
                status TEXT DEFAULT 'draft',
                word_count INTEGER DEFAULT 0,
                seo_difficulty INTEGER DEFAULT 0,
                meta_description TEXT,
                markdown_path TEXT,
                wp_post_id INTEGER,
                wp_url TEXT,
                published_at TEXT,
                scheduled_for TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL,
                recorded_at TEXT DEFAULT (datetime('now')),
                page_views INTEGER DEFAULT 0,
                unique_visitors INTEGER DEFAULT 0,
                avg_time_on_page_sec INTEGER DEFAULT 0,
                cta_clicks INTEGER DEFAULT 0,
                cta_conversions INTEGER DEFAULT 0,
                keyword_position INTEGER,
                bounce_rate REAL DEFAULT 0.0,
                FOREIGN KEY (post_id) REFERENCES posts(id)
            );

            CREATE TABLE IF NOT EXISTS calendar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week INTEGER NOT NULL,
                publish_date TEXT NOT NULL,
                keyword TEXT NOT NULL,
                content_type TEXT NOT NULL,
                seo_difficulty INTEGER DEFAULT 0,
                status TEXT DEFAULT 'planned',
                post_id TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
            CREATE INDEX IF NOT EXISTS idx_posts_keyword ON posts(keyword);
            CREATE INDEX IF NOT EXISTS idx_analytics_post_id ON analytics(post_id);
        """)
    logger.info("[DB] Database initialized")


# ─── Anthropic Client ─────────────────────────────────────────────────────────

def get_claude(model: str = "claude-haiku-4-5") -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=api_key)


# ─── Slug & Util ──────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


# ─── 1. BLOG POST GENERATOR ───────────────────────────────────────────────────

BLOG_POST_SYSTEM = """You are an expert SEO content writer and SalesOps specialist.
You write for Sincor (getsincor.com) — an AI-powered CRM automation platform.
Your posts are:
- 2,000–2,500 words
- Authoritative, data-driven, practical
- Written for SalesOps managers, RevOps leads, sales directors
- Never salesy or hype-filled — lead with value, CTAs come naturally

Format rules:
- Use proper H1 (title), H2, H3 markdown headers
- Include a <meta_description> tag at the top (150-160 chars)
- Include 4-6 internal links to https://getsincor.com pages
- 2-3 CTAs pointing to https://getsincor.com/buy (free trial)
- End with an FAQ section (3-5 questions)
- Include real statistics and data where available
- Use concrete examples and "before/after" scenarios
"""

def generate_blog_post(
    keyword: str,
    content_type: str = "how-to",
    competitor_context: Optional[str] = None,
    model: str = "claude-haiku-4-5",
) -> dict:
    """
    Generate a complete SEO blog post for a given keyword.
    Returns dict with title, meta_description, markdown, word_count, slug.
    """
    claude = get_claude(model)

    type_instructions = {
        "how-to": "Write a practical how-to guide with numbered steps, code examples if relevant, and real-world scenarios.",
        "comparison": "Write an objective comparison. Use a feature matrix table. Be fair — acknowledge competitor strengths.",
        "alternatives": "Write a 'X alternatives to Y' post. Cover 5-7 alternatives including Sincor. Be balanced.",
        "case-study": "Write a realistic case study with a fictional but plausible company (e.g., 'Apex Medical, a 45-person healthcare staffing firm...'). Include metrics, before/after, implementation timeline.",
        "industry-trend": "Write a forward-looking industry analysis. Cite real trends, data points, and what they mean for sales teams.",
    }

    competitor_block = ""
    if competitor_context:
        competitor_block = f"\n\nCompetitor context for this post:\n{competitor_context}"

    prompt = f"""Write a complete, publish-ready blog post for Sincor targeting the keyword: "{keyword}"

Content type: {content_type}
Type instruction: {type_instructions.get(content_type, type_instructions['how-to'])}

About Sincor:
{SINCOR_BRAND['description']}

Key features to naturally weave in (don't list them all — use what's relevant):
{chr(10).join(f"- {f}" for f in SINCOR_BRAND['features'])}

Free trial CTA URL: {SINCOR_BRAND['free_trial_url']}
{competitor_block}

IMPORTANT FORMAT:
1. Start with: <meta_description>Your 150-160 char meta description here</meta_description>
2. Then the full markdown post starting with # (H1 title)
3. The H1 title should naturally contain or be adjacent to the keyword
4. Minimum 2,000 words
5. At least 2 CTAs like: [Start your free trial →]({SINCOR_BRAND['free_trial_url']})
6. End with ## Frequently Asked Questions section

Write the complete post now:"""

    logger.info(f"[GENERATE] Writing post for keyword: '{keyword}' ({content_type})")

    response = claude.messages.create(
        model=model,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
        system=BLOG_POST_SYSTEM,
    )

    raw = response.content[0].text

    # Extract meta description
    meta_match = re.search(r"<meta_description>(.*?)</meta_description>", raw, re.DOTALL)
    meta_description = meta_match.group(1).strip() if meta_match else ""
    # Remove the tag from the markdown
    markdown = re.sub(r"<meta_description>.*?</meta_description>\s*", "", raw, flags=re.DOTALL).strip()

    # Extract title from H1
    title_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else keyword.title()

    slug = slugify(title)
    wc = count_words(markdown)

    logger.info(f"[GENERATE] Post ready: '{title}' ({wc} words)")

    return {
        "id": str(uuid.uuid4()),
        "title": title,
        "slug": slug,
        "keyword": keyword,
        "content_type": content_type,
        "meta_description": meta_description,
        "markdown": markdown,
        "word_count": wc,
    }


def save_post(post: dict) -> Path:
    """Save post to disk and record in DB. Returns path to markdown file."""
    slug = post["slug"]
    path = CONTENT_DIR / f"{slug}.md"

    # Add YAML front matter
    front_matter = f"""---
title: "{post['title'].replace('"', "'")}"
slug: "{slug}"
keyword: "{post['keyword']}"
content_type: "{post['content_type']}"
meta_description: "{post['meta_description'].replace('"', "'")}"
word_count: {post['word_count']}
status: "draft"
created_at: "{datetime.now().isoformat()}"
sincor_free_trial: "{SINCOR_BRAND['free_trial_url']}"
---

"""
    path.write_text(front_matter + post["markdown"], encoding="utf-8")
    logger.info(f"[SAVE] Written to {path}")

    # Record in DB
    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO posts
               (id, slug, title, keyword, content_type, meta_description,
                markdown_path, word_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft')""",
            (
                post["id"], slug, post["title"], post["keyword"],
                post["content_type"], post["meta_description"],
                str(path), post["word_count"],
            ),
        )

    return path


# ─── 2. CONTENT CALENDAR ──────────────────────────────────────────────────────

def generate_content_calendar(weeks: int = 12, posts_per_week: int = 2) -> list:
    """
    Generate a rolling content calendar.
    Returns list of calendar items, also saves to DB and JSON.
    """
    calendar = []
    today = datetime.now()
    # Start next Monday
    days_to_monday = (7 - today.weekday()) % 7 or 7
    start_date = today + timedelta(days=days_to_monday)

    # Mix of content types — vary across weeks
    type_rotation = [
        "how-to", "comparison",
        "how-to", "alternatives",
        "how-to", "case-study",
        "how-to", "industry-trend",
        "comparison", "how-to",
        "alternatives", "case-study",
    ]

    # Select keywords prioritizing high-priority, low-difficulty
    sorted_keywords = sorted(KEYWORD_BANK, key=lambda x: (x[3], x[1]))

    # Remove comparison keywords (they map to specific posts, not calendar slots)
    calendar_keywords = [(kw, diff, ctype, pri) for kw, diff, ctype, pri in sorted_keywords
                         if not kw.startswith("Sincor vs")]

    total_slots = weeks * posts_per_week
    selected = []
    for i in range(total_slots):
        kw_entry = calendar_keywords[i % len(calendar_keywords)]
        preferred_type = type_rotation[i % len(type_rotation)]
        selected.append((kw_entry[0], kw_entry[1], preferred_type))

    publish_days = [1, 4]  # Tuesday and Friday (0=Mon, 1=Tue, 3=Thu, 4=Fri)

    slot = 0
    for week in range(weeks):
        for day_offset in publish_days[:posts_per_week]:
            publish_date = start_date + timedelta(weeks=week, days=day_offset)
            keyword, difficulty, ctype = selected[slot]

            item = {
                "week": week + 1,
                "publish_date": publish_date.strftime("%Y-%m-%d"),
                "day_of_week": publish_date.strftime("%A"),
                "keyword": keyword,
                "content_type": ctype,
                "seo_difficulty": difficulty,
                "status": "planned",
                "title_suggestion": _suggest_title(keyword, ctype),
            }
            calendar.append(item)

            with get_db() as conn:
                conn.execute(
                    """INSERT INTO calendar
                       (week, publish_date, keyword, content_type, seo_difficulty)
                       VALUES (?, ?, ?, ?, ?)""",
                    (week + 1, item["publish_date"], keyword, ctype, difficulty),
                )

            slot += 1

    # Save JSON
    CALENDAR_PATH.write_text(json.dumps(calendar, indent=2), encoding="utf-8")
    logger.info(f"[CALENDAR] Generated {len(calendar)} posts across {weeks} weeks → {CALENDAR_PATH}")
    return calendar


def _suggest_title(keyword: str, content_type: str) -> str:
    title_templates = {
        "how-to": f"How to {keyword.title()}: A Complete Guide",
        "comparison": f"{keyword.title()}: Top Tools Compared",
        "alternatives": f"Best {keyword.title()}: {new_year()} Edition",
        "case-study": f"How [Company] Used {keyword.title()} to {achievement_phrase()}",
        "industry-trend": f"The State of {keyword.title()} in {new_year()}",
    }
    return title_templates.get(content_type, keyword.title())


def new_year() -> str:
    return str(datetime.now().year)


def achievement_phrase() -> str:
    phrases = [
        "Cut CRM Costs by 60%",
        "2x Their Pipeline in 90 Days",
        "Eliminate Data Quality Issues",
        "Scale Outbound Without Hiring",
    ]
    import random
    return random.choice(phrases)


def format_calendar_display(calendar: list) -> str:
    """Format calendar as markdown table for display."""
    lines = [
        "# Sincor 12-Week Content Calendar\n",
        "| Week | Date | Day | Keyword | Type | SEO Diff | Title Suggestion |",
        "|------|------|-----|---------|------|----------|-----------------|",
    ]
    for item in calendar:
        lines.append(
            f"| {item['week']:2d} | {item['publish_date']} | {item['day_of_week'][:3]} "
            f"| {item['keyword'][:35]:<35} | {item['content_type']:<14} "
            f"| {'⭐' * item['seo_difficulty']:10} | {item['title_suggestion'][:50]} |"
        )
    return "\n".join(lines)


# ─── 3. COMPARISON PAGE GENERATOR ─────────────────────────────────────────────

COMPARISON_SYSTEM = """You are a fair, data-driven SaaS copywriter creating comparison pages for Sincor.
Rules:
- Be factual and fair. Don't trash competitors.
- Lead with use-case fit: "Best for X, not best for Y"
- Feature matrix should be accurate
- Acknowledge competitor strengths honestly
- Sincor wins on: CRM focus, AI agents, deduplication, multi-CRM sync, price-performance
- 1,500-2,000 words
- Include a feature comparison table (markdown)
- End with a clear recommendation section
- 2 CTAs to https://getsincor.com/buy
"""

def generate_comparison_page(competitor: str, model: str = "claude-haiku-4-5") -> dict:
    """
    Generate a 'Sincor vs [Competitor]' comparison page.
    """
    if competitor not in COMPETITOR_DATA:
        raise ValueError(f"Unknown competitor: {competitor}. Available: {list(COMPETITOR_DATA.keys())}")

    comp = COMPETITOR_DATA[competitor]
    claude = get_claude(model)

    prompt = f"""Write a complete "Sincor vs {competitor}" comparison page.

Sincor:
{json.dumps(SINCOR_BRAND, indent=2)}

{competitor}:
- Tagline: {comp['tagline']}
- Pricing: {comp['pricing']}
- Strengths: {json.dumps(comp['strengths'])}
- Weaknesses: {json.dumps(comp['weaknesses'])}
- Best for: {comp['best_for']}
- Where Sincor wins: {comp['sincor_wins']}

Include:
1. <meta_description> tag (150-160 chars)
2. H1: "Sincor vs {competitor}: Which CRM Automation Tool Is Right for You?"
3. Quick verdict box (2-3 sentences)
4. Feature comparison table with these rows:
   - CRM sync (multi-platform)
   - Duplicate contact detection
   - AI-powered lead scoring
   - Outbound automation
   - No-code workflow builder
   - Pricing model
   - Setup time
   - Best for
5. Detailed section for each tool
6. Use case breakdowns: "Choose {competitor} if..." / "Choose Sincor if..."
7. Pricing comparison
8. Final recommendation
9. 2 CTAs: [Try Sincor Free →](https://getsincor.com/buy)

Write the full page now:"""

    logger.info(f"[COMPARE] Generating Sincor vs {competitor}")

    response = claude.messages.create(
        model=model,
        max_tokens=3500,
        messages=[{"role": "user", "content": prompt}],
        system=COMPARISON_SYSTEM,
    )

    raw = response.content[0].text

    meta_match = re.search(r"<meta_description>(.*?)</meta_description>", raw, re.DOTALL)
    meta_description = meta_match.group(1).strip() if meta_match else f"Sincor vs {competitor}: detailed comparison of features, pricing, and use cases for CRM automation teams."
    markdown = re.sub(r"<meta_description>.*?</meta_description>\s*", "", raw, flags=re.DOTALL).strip()

    title_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else f"Sincor vs {competitor}"
    slug = slugify(f"sincor-vs-{competitor.lower()}")
    wc = count_words(markdown)

    logger.info(f"[COMPARE] Done: '{title}' ({wc} words)")

    return {
        "id": str(uuid.uuid4()),
        "title": title,
        "slug": slug,
        "keyword": f"Sincor vs {competitor}",
        "content_type": "comparison",
        "meta_description": meta_description,
        "markdown": markdown,
        "word_count": wc,
    }


# ─── 4. WORDPRESS INTEGRATION ─────────────────────────────────────────────────

class WordPressPublisher:
    """
    Publish markdown posts to a WordPress site via REST API.
    Requires WP_API_URL, WP_USERNAME, WP_APP_PASSWORD env vars.
    """

    def __init__(self):
        self.api_url = os.environ.get("WP_API_URL", "").rstrip("/")
        self.username = os.environ.get("WP_USERNAME", "")
        self.app_password = os.environ.get("WP_APP_PASSWORD", "")
        self.enabled = bool(self.api_url and self.username and self.app_password)

        if not self.enabled:
            logger.warning("[WP] WordPress not configured (WP_API_URL/WP_USERNAME/WP_APP_PASSWORD missing)")

    def _headers(self):
        import base64
        credentials = f"{self.username}:{self.app_password}"
        token = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    def markdown_to_html(self, markdown: str) -> str:
        """Basic markdown → HTML conversion (install mistune for full support)."""
        try:
            import mistune
            return mistune.html(markdown)
        except ImportError:
            # Fallback: wrap in pre or minimal conversion
            html = markdown
            html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
            html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
            html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
            html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
            html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)
            html = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', html)
            paragraphs = []
            for p in re.split(r"\n\n+", html):
                p = p.strip()
                if p and not p.startswith("<h"):
                    p = f"<p>{p}</p>"
                paragraphs.append(p)
            return "\n".join(paragraphs)

    def publish(self, post: dict, scheduled_for: Optional[str] = None) -> dict:
        """
        Publish or schedule a post to WordPress.
        Returns WP API response with post_id and URL.
        """
        import urllib.request
        import urllib.error

        if not self.enabled:
            logger.warning("[WP] Skipping publish — WordPress not configured")
            return {"error": "WordPress not configured", "saved_locally": True}

        html_content = self.markdown_to_html(post["markdown"])
        payload = {
            "title": post["title"],
            "content": html_content,
            "slug": post["slug"],
            "status": "future" if scheduled_for else "publish",
            "meta": {
                "_yoast_wpseo_metadesc": post.get("meta_description", ""),
                "_yoast_wpseo_focuskw": post.get("keyword", ""),
            },
            "categories": [],
            "tags": [],
        }
        if scheduled_for:
            payload["date"] = scheduled_for  # ISO 8601

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.api_url}/wp-json/wp/v2/posts",
            data=data,
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                wp_id = result.get("id")
                wp_url = result.get("link")
                logger.info(f"[WP] Published: {wp_url} (ID {wp_id})")

                with get_db() as conn:
                    conn.execute(
                        """UPDATE posts SET wp_post_id=?, wp_url=?, status='published',
                           published_at=? WHERE slug=?""",
                        (wp_id, wp_url, datetime.now().isoformat(), post["slug"]),
                    )

                return {"wp_post_id": wp_id, "wp_url": wp_url}

        except urllib.error.HTTPError as e:
            body = e.read().decode()
            logger.error(f"[WP] HTTP {e.code}: {body[:200]}")
            return {"error": f"HTTP {e.code}", "body": body[:200]}
        except Exception as e:
            logger.error(f"[WP] Publish failed: {e}")
            return {"error": str(e)}

    def schedule_post(self, post: dict, publish_date: str) -> dict:
        """Schedule a post for future publication."""
        # WP expects date in ISO 8601 format
        try:
            dt = datetime.strptime(publish_date, "%Y-%m-%d")
            scheduled = dt.replace(hour=9, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S")
            return self.publish(post, scheduled_for=scheduled)
        except Exception as e:
            logger.error(f"[WP] Schedule failed: {e}")
            return {"error": str(e)}


# ─── 5. ANALYTICS HOOK ────────────────────────────────────────────────────────

class ContentAnalytics:
    """
    Track content performance and identify posts needing refresh.
    Integrates with Google Analytics, Search Console, or manual tracking.
    """

    def __init__(self):
        self.ga_property = os.environ.get("GA_PROPERTY_ID", "")
        self.gsc_site = os.environ.get("GSC_SITE_URL", SINCOR_BRAND["url"])

    def record_performance(
        self,
        post_id: str,
        page_views: int = 0,
        unique_visitors: int = 0,
        avg_time_sec: int = 0,
        cta_clicks: int = 0,
        cta_conversions: int = 0,
        keyword_position: Optional[int] = None,
        bounce_rate: float = 0.0,
    ):
        """Record analytics snapshot for a post."""
        with get_db() as conn:
            conn.execute(
                """INSERT INTO analytics
                   (post_id, page_views, unique_visitors, avg_time_on_page_sec,
                    cta_clicks, cta_conversions, keyword_position, bounce_rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (post_id, page_views, unique_visitors, avg_time_sec,
                 cta_clicks, cta_conversions, keyword_position, bounce_rate),
            )

    def get_top_performers(self, limit: int = 10) -> list:
        """Return posts sorted by traffic and conversions."""
        with get_db() as conn:
            rows = conn.execute(
                """SELECT p.slug, p.title, p.keyword,
                          COALESCE(SUM(a.page_views), 0) as total_views,
                          COALESCE(SUM(a.cta_conversions), 0) as total_conversions,
                          COALESCE(AVG(a.keyword_position), 999) as avg_position,
                          p.published_at
                   FROM posts p
                   LEFT JOIN analytics a ON p.id = a.post_id
                   WHERE p.status = 'published'
                   GROUP BY p.id
                   ORDER BY total_views DESC, total_conversions DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_low_performers(self, min_age_days: int = 90, max_views: int = 100) -> list:
        """Return posts that should be refreshed."""
        cutoff = (datetime.now() - timedelta(days=min_age_days)).isoformat()
        with get_db() as conn:
            rows = conn.execute(
                """SELECT p.id, p.slug, p.title, p.keyword, p.published_at,
                          COALESCE(SUM(a.page_views), 0) as total_views
                   FROM posts p
                   LEFT JOIN analytics a ON p.id = a.post_id
                   WHERE p.status = 'published'
                     AND p.published_at < ?
                   GROUP BY p.id
                   HAVING total_views < ?
                   ORDER BY total_views ASC""",
                (cutoff, max_views),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_best_cta(self) -> dict:
        """Find which CTA language drives most conversions."""
        # Placeholder — in production, track with UTM params or click events
        with get_db() as conn:
            rows = conn.execute(
                """SELECT p.content_type,
                          SUM(a.cta_clicks) as clicks,
                          SUM(a.cta_conversions) as conversions,
                          CASE WHEN SUM(a.cta_clicks) > 0
                               THEN ROUND(100.0 * SUM(a.cta_conversions) / SUM(a.cta_clicks), 2)
                               ELSE 0 END as conversion_rate
                   FROM analytics a
                   JOIN posts p ON p.id = a.post_id
                   GROUP BY p.content_type
                   ORDER BY conversion_rate DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def summary_report(self) -> str:
        """Generate a plain-text analytics summary."""
        top = self.get_top_performers(5)
        low = self.get_low_performers()
        cta = self.get_best_cta()

        lines = [
            "=" * 60,
            "SINCOR CONTENT ANALYTICS REPORT",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
            "\n📈 TOP PERFORMERS",
        ]
        if top:
            for p in top:
                lines.append(f"  [{p['total_views']:,} views | {p['total_conversions']} conv] {p['title'][:50]}")
        else:
            lines.append("  No published posts with analytics yet.")

        lines.append("\n🔄 NEEDS REFRESH (low traffic)")
        if low:
            for p in low[:5]:
                lines.append(f"  [{p['total_views']} views] {p['title'][:50]} ({p['published_at'][:10]})")
        else:
            lines.append("  No posts need refreshing.")

        lines.append("\n🎯 CTA PERFORMANCE BY TYPE")
        if cta:
            for c in cta:
                lines.append(f"  {c['content_type']:<15} {c['conversion_rate']:.1f}% CVR ({c['clicks']} clicks)")
        else:
            lines.append("  No CTA data yet.")

        lines.append("=" * 60)
        return "\n".join(lines)


# ─── REFRESH LOW-PERFORMERS ───────────────────────────────────────────────────

def refresh_low_performers(max_refresh: int = 3, model: str = "claude-haiku-4-5"):
    """Identify low-traffic posts and rewrite them with updated content."""
    analytics = ContentAnalytics()
    low = analytics.get_low_performers()

    if not low:
        logger.info("[REFRESH] No posts need refreshing.")
        return

    logger.info(f"[REFRESH] Found {len(low)} low-performer(s). Refreshing up to {max_refresh}.")

    wp = WordPressPublisher()
    refreshed = 0

    for post_record in low[:max_refresh]:
        logger.info(f"[REFRESH] Rewriting: {post_record['title']}")

        # Find existing markdown
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM posts WHERE id = ?", (post_record["id"],)
            ).fetchone()

        if not row:
            continue

        # Generate fresh post for same keyword
        new_post = generate_blog_post(
            keyword=row["keyword"],
            content_type=row["content_type"],
            model=model,
        )
        new_post["id"] = row["id"]  # keep same ID
        new_post["slug"] = row["slug"]  # keep same slug

        path = save_post(new_post)

        # Mark as updated
        with get_db() as conn:
            conn.execute(
                "UPDATE posts SET status='draft', updated_at=? WHERE id=?",
                (datetime.now().isoformat(), row["id"]),
            )

        if wp.enabled and row["wp_post_id"]:
            # Re-publish to same WP post
            wp.publish(new_post)

        logger.info(f"[REFRESH] Refreshed: {new_post['title']} → {path}")
        refreshed += 1

    logger.info(f"[REFRESH] Done. Refreshed {refreshed} post(s).")


# ─── AUTONOMOUS CYCLE ─────────────────────────────────────────────────────────

def run_autonomous_cycle(model: str = "claude-haiku-4-5"):
    """
    Full autonomous publishing cycle:
    1. Check content calendar for posts due in the next 48h
    2. Generate those posts
    3. Publish to WordPress (or save as drafts)
    4. Check for low performers and refresh
    5. Log analytics summary
    """
    logger.info("[CYCLE] Starting autonomous content cycle")
    init_db()

    # 1. Load or generate calendar
    if not CALENDAR_PATH.exists():
        logger.info("[CYCLE] No calendar found — generating 12-week calendar")
        generate_content_calendar()

    calendar = json.loads(CALENDAR_PATH.read_text())

    # 2. Find posts due in next 48h
    now = datetime.now()
    due_cutoff = now + timedelta(hours=48)
    due_posts = [
        item for item in calendar
        if item["status"] == "planned"
        and datetime.strptime(item["publish_date"], "%Y-%m-%d") <= due_cutoff
    ]

    logger.info(f"[CYCLE] {len(due_posts)} post(s) due in the next 48h")

    wp = WordPressPublisher()
    published_count = 0

    for item in due_posts[:3]:  # cap at 3 per cycle to avoid API burn
        keyword = item["keyword"]
        ctype = item["content_type"]

        # Check if already exists
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM posts WHERE keyword = ? AND status IN ('draft','published')",
                (keyword,),
            ).fetchone()

        if existing:
            logger.info(f"[CYCLE] Already have post for '{keyword}' — skipping")
            _mark_calendar_done(calendar, keyword)
            continue

        # Generate
        post = generate_blog_post(keyword, ctype, model=model)
        path = save_post(post)

        # Publish
        publish_date = item["publish_date"]
        if wp.enabled:
            result = wp.schedule_post(post, publish_date)
            logger.info(f"[CYCLE] Scheduled to WP: {result}")
        else:
            logger.info(f"[CYCLE] Saved draft: {path}")

        _mark_calendar_done(calendar, keyword)
        published_count += 1

        # Polite delay
        time.sleep(2)

    # Save updated calendar
    CALENDAR_PATH.write_text(json.dumps(calendar, indent=2))

    # 3. Refresh low performers
    refresh_low_performers(max_refresh=2, model=model)

    # 4. Analytics summary
    analytics = ContentAnalytics()
    report = analytics.summary_report()
    logger.info("\n" + report)

    # Save report
    report_path = PROJECT_ROOT / "content" / f"report_{now.strftime('%Y%m%d_%H%M')}.txt"
    report_path.write_text(report, encoding="utf-8")

    logger.info(f"[CYCLE] Done. Published {published_count} new posts. Report: {report_path}")
    return {"published": published_count, "report": report}


def _mark_calendar_done(calendar: list, keyword: str):
    for item in calendar:
        if item["keyword"] == keyword and item["status"] == "planned":
            item["status"] = "published"
            break


# ─── DAEMON (every 48h) ───────────────────────────────────────────────────────

def run_daemon(interval_hours: int = 48):
    """Run autonomous cycle every N hours."""
    logger.info(f"[DAEMON] Starting — will run every {interval_hours}h")
    init_db()
    while True:
        try:
            run_autonomous_cycle()
        except Exception as e:
            logger.error(f"[DAEMON] Cycle error: {e}", exc_info=True)
        logger.info(f"[DAEMON] Sleeping {interval_hours}h until next cycle")
        time.sleep(interval_hours * 3600)


# ─── CLI ENTRYPOINT ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sincor Content Agent — autonomous blog/SEO engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # blog command
    blog_p = subparsers.add_parser("blog", help="Generate a single blog post")
    blog_p.add_argument("--keyword", required=True, help='Target keyword, e.g. "CRM sync automation"')
    blog_p.add_argument("--type", default="how-to",
                        choices=["how-to", "comparison", "alternatives", "case-study", "industry-trend"])
    blog_p.add_argument("--publish", action="store_true", help="Auto-publish to WordPress")
    blog_p.add_argument("--model", default="claude-haiku-4-5")

    # calendar command
    cal_p = subparsers.add_parser("calendar", help="Generate 12-week content calendar")
    cal_p.add_argument("--weeks", type=int, default=12)
    cal_p.add_argument("--posts-per-week", type=int, default=2)
    cal_p.add_argument("--print", action="store_true", help="Print calendar table")

    # compare command
    cmp_p = subparsers.add_parser("compare", help="Generate Sincor vs Competitor page")
    cmp_p.add_argument("--competitor", required=True,
                       choices=list(COMPETITOR_DATA.keys()), help="Competitor name")
    cmp_p.add_argument("--publish", action="store_true")
    cmp_p.add_argument("--model", default="claude-haiku-4-5")

    # cycle command
    subparsers.add_parser("cycle", help="Run one autonomous content cycle")

    # daemon command
    daemon_p = subparsers.add_parser("daemon", help="Run continuously every 48h")
    daemon_p.add_argument("--interval", type=int, default=48, help="Hours between cycles")

    # refresh command
    subparsers.add_parser("refresh", help="Refresh low-performing posts")

    # analytics command
    subparsers.add_parser("analytics", help="Print analytics report")

    # track command
    track_p = subparsers.add_parser("track", help="Record analytics for a post")
    track_p.add_argument("--post-id", required=True)
    track_p.add_argument("--views", type=int, default=0)
    track_p.add_argument("--visitors", type=int, default=0)
    track_p.add_argument("--cta-clicks", type=int, default=0)
    track_p.add_argument("--cta-conversions", type=int, default=0)
    track_p.add_argument("--kw-position", type=int, default=None)

    args = parser.parse_args()
    init_db()

    if args.command == "blog":
        post = generate_blog_post(args.keyword, args.type, model=args.model)
        path = save_post(post)
        print(f"\n✅ Blog post generated: {path}")
        print(f"   Title: {post['title']}")
        print(f"   Words: {post['word_count']}")
        print(f"   Meta: {post['meta_description'][:80]}...")
        if args.publish:
            wp = WordPressPublisher()
            result = wp.publish(post)
            print(f"   WP: {result}")

    elif args.command == "calendar":
        calendar = generate_content_calendar(args.weeks, args.posts_per_week)
        print(f"\n✅ Generated {len(calendar)}-entry calendar → {CALENDAR_PATH}")
        if args.print:
            print(format_calendar_display(calendar))

    elif args.command == "compare":
        post = generate_comparison_page(args.competitor, model=args.model)
        path = save_post(post)
        print(f"\n✅ Comparison page generated: {path}")
        print(f"   Title: {post['title']}")
        print(f"   Words: {post['word_count']}")
        if args.publish:
            wp = WordPressPublisher()
            result = wp.publish(post)
            print(f"   WP: {result}")

    elif args.command == "cycle":
        result = run_autonomous_cycle()
        print(f"\n✅ Cycle complete. Published {result['published']} posts.")

    elif args.command == "daemon":
        run_daemon(args.interval)

    elif args.command == "refresh":
        refresh_low_performers()
        print("✅ Refresh complete.")

    elif args.command == "analytics":
        analytics = ContentAnalytics()
        print(analytics.summary_report())

    elif args.command == "track":
        analytics = ContentAnalytics()
        analytics.record_performance(
            post_id=args.post_id,
            page_views=args.views,
            unique_visitors=args.visitors,
            cta_clicks=args.cta_clicks,
            cta_conversions=args.cta_conversions,
            keyword_position=args.kw_position,
        )
        print(f"✅ Analytics recorded for post {args.post_id}")


if __name__ == "__main__":
    main()
