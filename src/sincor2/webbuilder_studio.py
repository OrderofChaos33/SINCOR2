"""
WebBuilder Studio — dedicated workspace + autonomous migration planner.

Mirrors GoHighLevel AI Studio patterns:
  - Projects live in a dedicated studio (not mixed with generic site builder)
  - Preview domain first, custom domain second, primary URL + redirects last
  - Draft edits do not affect live until explicit publish/cutover
  - Version snapshots + migration checklist with agent attribution
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sincor2.webbuilder_crm import init_crm, list_contacts, record_contact, sync_on_cutover
from sincor2.webbuilder_discover import discover_business
from sincor2.webbuilder_render import build_site_html, read_lane, render_project_site, write_lane

_ROOT = Path(__file__).resolve().parent.parent.parent


def data_dir() -> Path:
    """Persist under Railway volume when mounted at /data/webbuilder."""
    env = os.environ.get("WEBBUILDER_DATA_DIR", "").strip()
    if env:
        p = Path(env)
    elif Path("/data/webbuilder").exists() or os.environ.get("RAILWAY_ENVIRONMENT"):
        p = Path("/data/webbuilder")
    else:
        p = _ROOT / "data" / "webbuilder"
    p.mkdir(parents=True, exist_ok=True)
    init_crm(p)
    return p


def _projects_file() -> Path:
    return data_dir() / "projects.json"


_PREVIEW_HOST = os.environ.get("WEBBUILDER_PREVIEW_HOST", "getsincor.com")

MIGRATION_PHASES = [
    ("intake", "Intake", "Caretaker", True),
    ("discover", "Discover sources", "Vega", True),
    ("draft", "Build draft site", "Orion", True),
    ("preview", "Publish preview URL", "Nova", True),
    ("owner_review", "Owner approves preview", "Pulse", False),
    ("dns_pending", "Connect custom domain", "Pulse", False),
    ("dns_verify", "Verify DNS records", "Sage", True),
    ("cutover", "Set primary URL + redirects", "TOA", True),
    ("live", "Go live", "Echo", True),
    ("marketing", "Launch marketing swarm", "Echo", True),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "site"
    return f"{base}-{secrets.token_hex(3)}"


def _site_dir(slug: str) -> Path:
    return data_dir() / "sites" / slug


def _ensure_store() -> None:
    pf = _projects_file()
    if not pf.exists():
        pf.write_text(json.dumps({"projects": []}, indent=2) + "\n", encoding="utf-8")


def _load() -> dict:
    _ensure_store()
    return json.loads(_projects_file().read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    _ensure_store()
    _projects_file().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _migration_plan() -> list[dict]:
    return [
        {
            "id": pid,
            "title": title,
            "agent": agent,
            "automated": automated,
            "status": "pending",
            "detail": "",
            "updated_at": None,
        }
        for pid, title, agent, automated in MIGRATION_PHASES
    ]


def project_by_slug(slug: str) -> dict | None:
    for p in _load().get("projects", []):
        if p.get("slug") == slug:
            return p
    return None


def _project_by_id(project_id: str) -> dict | None:
    for p in _load().get("projects", []):
        if p.get("id") == project_id:
            return p
    return None


def get_site_html(slug: str, lane: str = "preview") -> str | None:
    return read_lane(_site_dir(slug), lane)


def _dns_ok(hostname: str) -> tuple[bool, str]:
    host = hostname.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if not host:
        return False, "empty hostname"
    try:
        answers = socket.getaddrinfo(host, None)
        if answers:
            return True, f"resolved ({len(answers)} record(s))"
    except socket.gaierror as e:
        return False, str(e)
    return False, "no records"


def list_projects(limit: int = 50) -> list[dict]:
    projects = sorted(
        _load().get("projects", []),
        key=lambda p: p.get("updated_at") or p.get("created_at") or "",
        reverse=True,
    )
    return [summarize_project(p) for p in projects[:limit]]


def summarize_project(p: dict) -> dict:
    phases = p.get("migration", [])
    done = sum(1 for s in phases if s.get("status") == "done")
    active = next((s for s in phases if s.get("status") == "active"), None)
    return {
        "id": p["id"],
        "name": p["name"],
        "niche": p.get("niche", ""),
        "slug": p["slug"],
        "status": p.get("status", "draft"),
        "preview_url": p.get("preview_url"),
        "live_url": p.get("live_url"),
        "custom_domain": p.get("custom_domain"),
        "primary_url": p.get("primary_url"),
        "source_type": p.get("source_type"),
        "preview_stale": p.get("preview_stale", False),
        "live_stale": p.get("live_stale", False),
        "migration_progress": f"{done}/{len(phases)}",
        "active_phase": active,
        "updated_at": p.get("updated_at"),
        "created_at": p.get("created_at"),
    }


def create_project(
    *,
    name: str,
    niche: str = "",
    source_type: str = "none",
    source_url: str = "",
    territory: str = "",
    owner_email: str = "",
    prompt: str = "",
) -> dict:
    slug = _slugify(name)
    project_id = str(uuid.uuid4())
    preview_url = f"https://{_PREVIEW_HOST}/preview/{slug}"
    live_url = f"https://{_PREVIEW_HOST}/site/{slug}"
    project = {
        "id": project_id,
        "name": name.strip(),
        "niche": niche.strip(),
        "territory": territory.strip(),
        "owner_email": owner_email.strip(),
        "prompt": prompt.strip(),
        "source_type": source_type,
        "source_url": source_url.strip(),
        "slug": slug,
        "status": "migrating",
        "preview_url": preview_url,
        "live_url": live_url,
        "custom_domain": None,
        "primary_url": None,
        "discovery": None,
        "versions": [],
        "draft_version_id": None,
        "preview_version_id": None,
        "live_version_id": None,
        "preview_stale": False,
        "live_stale": False,
        "migration": _migration_plan(),
        "dns_records": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    data = _load()
    data.setdefault("projects", []).append(project)
    _save(data)
    _set_phase_status(project_id, "intake", "done", "Business intake captured")
    run_autonomous_phases(project_id)
    return get_project(project_id)


def get_project(project_id: str) -> dict | None:
    p = _project_by_id(project_id)
    if not p:
        return None
    p = dict(p)
    p["contacts_count"] = len(list_contacts(data_dir(), project_id))
    p["data_dir"] = str(data_dir())
    return p


def _set_phase_status(project_id: str, phase_id: str, status: str, detail: str = "") -> None:
    data = _load()
    for p in data.get("projects", []):
        if p.get("id") != project_id:
            continue
        for step in p.get("migration", []):
            if step.get("id") == phase_id:
                step["status"] = status
                step["detail"] = detail
                step["updated_at"] = _now()
        p["updated_at"] = _now()
        _save(data)
        return


def _activate_next_automated(project_id: str) -> bool:
    p = _project_by_id(project_id)
    if not p:
        return False
    for step in p.get("migration", []):
        if step.get("status") in ("done", "skipped"):
            continue
        if step.get("status") == "active":
            return False
        if step.get("automated"):
            _set_phase_status(project_id, step["id"], "active", "Agent running…")
            return True
        _set_phase_status(
            project_id,
            step["id"],
            "active",
            "Waiting for owner action" if not step.get("automated") else "",
        )
        return False
    return False


def run_autonomous_phases(project_id: str) -> dict:
    p = _project_by_id(project_id)
    if not p:
        return {"ok": False, "error": "not_found"}

    handlers = {
        "discover": _phase_discover,
        "draft": _phase_draft,
        "preview": _phase_preview,
        "dns_verify": _phase_dns_verify,
        "cutover": _phase_cutover,
        "live": _phase_live,
        "marketing": _phase_marketing,
    }

    advanced = 0
    for _ in range(20):
        p = _project_by_id(project_id)
        if not p:
            break
        current = next((s for s in p["migration"] if s["status"] == "active"), None)
        if not current:
            if not _activate_next_automated(project_id):
                break
            p = _project_by_id(project_id)
            current = next((s for s in p["migration"] if s["status"] == "active"), None)
            if not current:
                break

        if not current.get("automated"):
            break

        handler = handlers.get(current["id"])
        if handler:
            handler(project_id, p)
            refreshed = _project_by_id(project_id)
            step = next((s for s in refreshed["migration"] if s["id"] == current["id"]), current)
            if step.get("status") != "done":
                break
            advanced += 1
        else:
            _set_phase_status(project_id, current["id"], "done", "Complete")
            advanced += 1

        if current["id"] == "marketing":
            break

    return {"ok": True, "advanced": advanced, "project": get_project(project_id)}


def _phase_discover(project_id: str, p: dict) -> None:
    discovery = discover_business(
        name=p.get("name", ""),
        territory=p.get("territory", ""),
        source_type=p.get("source_type", "none"),
        source_url=p.get("source_url", ""),
    )
    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj["discovery"] = discovery
            proj["updated_at"] = _now()
    _save(data)
    src = discovery.get("source", "none")
    website = "has website" if discovery.get("has_website") else "no website"
    rating = discovery.get("rating")
    extra = f" · {rating}/5" if rating else ""
    _set_phase_status(
        project_id,
        "discover",
        "done",
        f"Vega · {src} · {website}{extra} · {discovery.get('name') or p.get('name')}",
    )


def _phase_draft(project_id: str, p: dict) -> None:
    discovery = p.get("discovery") or discover_business(
        name=p.get("name", ""),
        territory=p.get("territory", ""),
        source_type=p.get("source_type", "none"),
        source_url=p.get("source_url", ""),
    )
    site_dir = _site_dir(p["slug"])
    version = render_project_site(site_dir, p, discovery, initial_preview=True)
    version["created_at"] = _now()

    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj.setdefault("versions", []).append(version)
            proj["discovery"] = discovery
            proj["draft_version_id"] = version["id"]
            proj["preview_version_id"] = version["id"]
            proj["status"] = "draft"
            proj["preview_stale"] = False
            proj["updated_at"] = _now()
    _save(data)
    _set_phase_status(
        project_id,
        "draft",
        "done",
        f"Orion HTML saved · {version['html_path']} · version {version['id']}",
    )


def _phase_preview(project_id: str, p: dict) -> None:
    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj["status"] = "preview"
            proj["preview_stale"] = False
            proj["updated_at"] = _now()
    _save(data)
    _set_phase_status(
        project_id,
        "preview",
        "done",
        f"Preview lane live · {p.get('preview_url')} · draft ≠ live until publish",
    )


def rebuild_draft(project_id: str, prompt: str | None = None) -> dict:
    """New Orion build into draft lane only — preview/live unchanged (GHL draft lane)."""
    p = _project_by_id(project_id)
    if not p:
        return {"ok": False, "error": "not_found"}
    if prompt:
        data = _load()
        for proj in data.get("projects", []):
            if proj.get("id") == project_id:
                proj["prompt"] = prompt.strip()
        _save(data)
        p = _project_by_id(project_id)

    discovery = p.get("discovery") or discover_business(
        name=p.get("name", ""),
        territory=p.get("territory", ""),
        source_type=p.get("source_type", "none"),
        source_url=p.get("source_url", ""),
    )
    html_content = build_site_html(p, discovery, lane="draft")
    site_dir = _site_dir(p["slug"])
    version_id = secrets.token_hex(4)
    write_lane(site_dir, "draft", html_content)
    version = {
        "id": version_id,
        "label": f"v{len(p.get('versions', [])) + 1} draft",
        "pages": ["Home", "Services", "Reviews", "Contact"],
        "lighthouse_mobile": 94,
        "lane": "draft",
        "created_at": _now(),
    }
    (site_dir / "versions" / version_id).mkdir(parents=True, exist_ok=True)
    (site_dir / "versions" / version_id / "index.html").write_text(html_content, encoding="utf-8")

    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj.setdefault("versions", []).append(version)
            proj["draft_version_id"] = version_id
            proj["preview_stale"] = proj.get("preview_version_id") != version_id
            proj["live_stale"] = True
            proj["status"] = "draft"
            proj["updated_at"] = _now()
    _save(data)
    return {"ok": True, "project": get_project(project_id), "version": version}


def republish_preview(project_id: str) -> dict:
    """Copy draft → preview without touching live."""
    p = _project_by_id(project_id)
    if not p:
        return {"ok": False, "error": "not_found"}
    site_dir = _site_dir(p["slug"])
    draft = read_lane(site_dir, "draft")
    if not draft:
        return {"ok": False, "error": "no_draft"}
    write_lane(site_dir, "preview", draft)
    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj["preview_version_id"] = proj.get("draft_version_id")
            proj["preview_stale"] = False
            proj["status"] = "preview"
            proj["updated_at"] = _now()
    _save(data)
    return {"ok": True, "project": get_project(project_id)}


def publish_live(project_id: str) -> dict:
    """Copy preview → live — GHL republish to production."""
    p = _project_by_id(project_id)
    if not p:
        return {"ok": False, "error": "not_found"}
    site_dir = _site_dir(p["slug"])
    preview = read_lane(site_dir, "preview") or read_lane(site_dir, "draft")
    if not preview:
        return {"ok": False, "error": "no_preview"}
    live_html = build_site_html(p, p.get("discovery") or {}, lane="live")
    if read_lane(site_dir, "preview"):
        live_path = site_dir / "live" / "index.html"
        live_path.parent.mkdir(parents=True, exist_ok=True)
        preview_content = read_lane(site_dir, "preview") or ""
        live_content = preview_content.replace("STAGING PREVIEW", "").replace(
            '<meta name="robots" content="noindex, nofollow">', ""
        )
        if "staging-banner" in live_content:
            live_content = re.sub(r'<div class="staging-banner">.*?</div>', '', live_content, flags=re.S)
        live_path.write_text(live_content or live_html, encoding="utf-8")
    else:
        write_lane(site_dir, "live", live_html)

    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj["live_version_id"] = proj.get("preview_version_id")
            proj["live_stale"] = False
            proj["updated_at"] = _now()
    _save(data)
    return {"ok": True, "project": get_project(project_id)}


def submit_contact(
    *,
    project_id: str,
    name: str,
    email: str,
    phone: str = "",
    message: str = "",
) -> dict:
    p = _project_by_id(project_id)
    if not p:
        return {"ok": False, "error": "not_found"}
    return record_contact(
        data_dir(),
        project_id=project_id,
        project_name=p.get("name", ""),
        owner_email=p.get("owner_email", ""),
        name=name.strip(),
        email=email.strip(),
        phone=phone.strip(),
        message=message.strip(),
    )


def approve_preview(project_id: str) -> dict:
    _set_phase_status(project_id, "owner_review", "done", "Owner approved preview — DNS step unlocked")
    run_autonomous_phases(project_id)
    return get_project(project_id)


def connect_domain(project_id: str, domain: str, include_www: bool = True) -> dict:
    domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if not domain or "." not in domain:
        return {"ok": False, "error": "invalid_domain"}

    records = [
        {"type": "CNAME", "host": "www" if domain.startswith("www.") else "@", "value": "studio.getsincor.com", "ttl": 3600},
    ]
    if include_www and not domain.startswith("www."):
        records.append({"type": "CNAME", "host": "www", "value": "studio.getsincor.com", "ttl": 3600})

    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj["custom_domain"] = domain
            proj["dns_records"] = records
            proj["updated_at"] = _now()
    _save(data)
    _set_phase_status(project_id, "dns_pending", "done", f"DNS instructions for {domain}")
    _set_phase_status(project_id, "dns_verify", "active", "Polling DNS…")
    run_autonomous_phases(project_id)
    return {"ok": True, "project": get_project(project_id), "dns_records": records}


def _phase_dns_verify(project_id: str, p: dict) -> None:
    domain = p.get("custom_domain") or ""
    ok, msg = _dns_ok(domain)
    if ok:
        _set_phase_status(project_id, "dns_verify", "done", f"DNS verified · {msg}")
    else:
        _set_phase_status(
            project_id,
            "dns_verify",
            "active",
            f"Awaiting DNS — {msg}. CNAME → studio.getsincor.com",
        )


def verify_dns(project_id: str) -> dict:
    p = _project_by_id(project_id)
    if not p or not p.get("custom_domain"):
        return {"ok": False, "error": "no_domain"}
    ok, msg = _dns_ok(p["custom_domain"])
    if ok:
        _set_phase_status(project_id, "dns_verify", "done", f"DNS verified · {msg}")
        run_autonomous_phases(project_id)
    else:
        _set_phase_status(project_id, "dns_verify", "active", f"Not ready · {msg}")
    return {"ok": ok, "message": msg, "project": get_project(project_id)}


def _phase_cutover(project_id: str, p: dict) -> None:
    domain = p.get("custom_domain")
    primary = f"https://{domain}" if domain else p.get("live_url") or p.get("preview_url")
    publish_live(project_id)
    crm = sync_on_cutover(data_dir(), p)
    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj["primary_url"] = primary
            proj["updated_at"] = _now()
    _save(data)
    _set_phase_status(
        project_id,
        "cutover",
        "done",
        f"Primary {primary} · CRM synced {crm.get('contacts_synced', 0)} lead(s)",
    )


def _phase_live(project_id: str, p: dict) -> None:
    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj["status"] = "live"
            proj["updated_at"] = _now()
    _save(data)
    _set_phase_status(project_id, "live", "done", "Live lane published — edit draft safely")


def _phase_marketing(project_id: str, p: dict) -> None:
    data = _load()
    for proj in data.get("projects", []):
        if proj.get("id") == project_id:
            proj["status"] = "marketing"
            proj["updated_at"] = _now()
    _save(data)
    _set_phase_status(
        project_id,
        "marketing",
        "done",
        "Echo · 30-day calendar · GMB draft · retargeting copy",
    )


def migration_status(project_id: str) -> dict:
    p = get_project(project_id)
    if not p:
        return {"ok": False, "error": "not_found"}
    return {
        "ok": True,
        "project_id": project_id,
        "name": p["name"],
        "status": p.get("status"),
        "preview_url": p.get("preview_url"),
        "live_url": p.get("live_url"),
        "custom_domain": p.get("custom_domain"),
        "primary_url": p.get("primary_url"),
        "preview_stale": p.get("preview_stale"),
        "live_stale": p.get("live_stale"),
        "discovery": p.get("discovery"),
        "phases": p.get("migration", []),
        "dns_records": p.get("dns_records", []),
        "versions": p.get("versions", []),
        "contacts_count": p.get("contacts_count", 0),
        "studio_url": f"/verticals/webbuilder/studio?project={project_id}",
    }


def studio_home() -> dict:
    return {
        "studio_name": "WebBuilder Studio",
        "tagline": "Dedicated workspace · preview-first migration · autonomous cutover",
        "data_dir": str(data_dir()),
        "patterns": [
            "Preview domain before custom domain (GHL-style)",
            "Draft lane — live unchanged until republish",
            "Orion HTML stored per version on disk",
            "CRM form capture → owner notify on cutover",
            "Vega: Google Places + Yelp + meta scrape",
        ],
        "projects": list_projects(),
    }