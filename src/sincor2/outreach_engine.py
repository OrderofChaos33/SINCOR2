"""SINCOR Autonomous Outreach Engine (agents only). Checkout close, no human calls."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger("sincor2.outreach")
BUY_URL = "https://getsincor.com/buy"
A2A_CARD = "https://getsincor.com/.well-known/agent-card.json"
TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
_JUNK_EMAIL = ("example.com", "sentry.io", "wixpress.com", "cloudflare", "schema.org", "noreply", "no-reply")


def _autonomous_on() -> bool:
    master = os.environ.get("AUTONOMOUS_AGENTS", "true").lower() == "true"
    flag = os.environ.get("OUTREACH_ENABLED", "true" if master else "false").lower()
    return flag == "true"


class OutreachEngine:
    YELP_SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
    PLACES_FIND_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
    PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
    TARGET_CATEGORIES = [
        ("medspas", "Chicago, IL"), ("gyms", "Chicago, IL"), ("dentists", "Chicago, IL"),
        ("hvac", "Chicago, IL"), ("medspas", "Dallas, TX"), ("dentists", "Dallas, TX"),
        ("gyms", "Dallas, TX"), ("medspas", "Miami, FL"), ("dentists", "Miami, FL"),
        ("medspas", "Milwaukee, WI"), ("dentists", "Milwaukee, WI"), ("hvac", "Milwaukee, WI"),
    ]

    def __init__(self):
        self.yelp_key = os.environ.get("YELP_API_KEY", "")
        self.places_key = (
            os.environ.get("GOOGLE_PLACES_API_KEY")
            or os.environ.get("GOOGLE_PLACES_KEY")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
        self.enabled = _autonomous_on()
        self.daily_limit = int(os.environ.get("OUTREACH_DAILY_LIMIT", "40"))
        self.from_email = os.environ.get("OUTREACH_FROM_EMAIL", os.environ.get("SINCOR_EMAIL", "support@getsincor.com"))
        self.from_name = os.environ.get("OUTREACH_FROM_NAME", "SINCOR Agent")
        data_root = Path(os.environ.get("SINCOR_DATA_DIR", "/tmp/sincor_outreach"))
        self.data_dir = data_root / "outreach"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sent_log_path = self.data_dir / "sent.json"
        self._sent_ids = self._load_sent_ids()
        if not self.yelp_key:
            logger.warning("[OUTREACH] YELP_API_KEY missing")
        if not self.enabled:
            logger.info("[OUTREACH] disabled")

    def fetch_yelp_leads(self, category: str, location: str, limit: int = 10) -> List[Dict]:
        if not self.yelp_key:
            return []
        try:
            resp = requests.get(
                self.YELP_SEARCH_URL,
                headers={"Authorization": f"Bearer {self.yelp_key}"},
                params={"term": category, "location": location, "limit": limit, "sort_by": "rating"},
                timeout=10,
            )
            resp.raise_for_status()
            leads = []
            for biz in resp.json().get("businesses", []):
                lead_id = biz.get("id", "")
                if not lead_id or lead_id in self._sent_ids:
                    continue
                leads.append({
                    "id": lead_id, "name": biz.get("name", ""), "phone": biz.get("phone", ""),
                    "url": biz.get("url", ""), "rating": biz.get("rating", 0),
                    "review_count": biz.get("review_count", 0), "category": category,
                    "location": location,
                    "address": ", ".join(biz.get("location", {}).get("display_address", [])),
                    "email": "", "website": "",
                })
            logger.info("[OUTREACH] Yelp: %s new %s / %s", len(leads), category, location)
            return leads
        except Exception as e:
            logger.error("[OUTREACH] Yelp fetch error: %s", e)
            return []

    def enrich_with_google_places(self, lead: Dict) -> Dict:
        if not self.places_key or not lead.get("name"):
            return lead
        try:
            query = f"{lead['name']} {lead.get('address', lead.get('location', ''))}"
            find_resp = requests.get(self.PLACES_FIND_URL, params={
                "input": query, "inputtype": "textquery", "fields": "place_id,name", "key": self.places_key,
            }, timeout=8)
            find_resp.raise_for_status()
            candidates = find_resp.json().get("candidates", [])
            if not candidates:
                return lead
            details_resp = requests.get(self.PLACES_DETAILS_URL, params={
                "place_id": candidates[0]["place_id"],
                "fields": "name,formatted_phone_number,website,url", "key": self.places_key,
            }, timeout=8)
            details_resp.raise_for_status()
            result = details_resp.json().get("result", {})
            lead["website"] = result.get("website", "") or lead.get("website", "")
            if not lead.get("phone") and result.get("formatted_phone_number"):
                lead["phone"] = result["formatted_phone_number"]
        except Exception as e:
            logger.warning("[OUTREACH] Places enrich %s: %s", lead.get("name"), e)
        return lead

    def _scrape_emails(self, website: str) -> List[str]:
        if not website:
            return []
        try:
            resp = requests.get(website, timeout=8, headers={"User-Agent": "SINCOR-Agent/1.0"}, allow_redirects=True)
            found = set(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", resp.text, re.I))
            out = []
            for em in found:
                low = em.lower()
                if any(j in low for j in _JUNK_EMAIL):
                    continue
                out.append(low)
            return out[:3]
        except Exception:
            return []

    def _guess_email(self, website: str) -> str:
        domain = website.replace("https://", "").replace("http://", "").split("/")[0].replace("www.", "")
        if not domain or "." not in domain:
            return ""
        return f"info@{domain}"

    def _checkout_url(self, lead: Dict) -> str:
        lid = lead.get("id", "agent")[:24]
        return f"{BUY_URL}?utm_source=agent&utm_medium=email&utm_campaign=outreach&lead={lid}"

    def _build_email_html(self, lead: Dict) -> str:
        name = lead.get("name", "there")
        pay = self._checkout_url(lead)
        return (
            f"<html><body style=\"font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#333;\">"
            f"<p>Hi {name} team,</p>"
            "<p>SINCOR agents deliver a competitive snapshot (pricing, reviews, gaps vs nearby rivals) as a PDF. No call.</p>"
            "<p>Pay on Base (SINC monthly or AXM one-time). Agents start when payment hits treasury.</p>"
            f"<p><a href=\"{pay}\">Start report — getsincor.com/buy</a></p>"
            f"<p>A2A: <a href=\"{A2A_CARD}\">{A2A_CARD}</a></p>"
            "<p>— SINCOR Agent<br>getsincor.com</p>"
            "<p style=\"font-size:11px;color:#999;\">Directory listing. Reply STOP to opt out.</p></body></html>"
        )

    def send_outreach_email(self, lead: Dict, resend_client) -> bool:
        email = lead.get("email", "")
        if not email:
            return False
        try:
            from_addr = f"{self.from_name} <{self.from_email}>"
            html = self._build_email_html(lead)
            pay = self._checkout_url(lead)
            text = (
                f"Hi {lead.get('name', 'there')} team,\n\n"
                "SINCOR agents deliver competitive intel PDFs. No call. Pay on Base; agents fulfill on treasury tx.\n\n"
                f"Pay: {pay}\nA2A: {A2A_CARD}\n\n— SINCOR Agent | getsincor.com\nReply STOP to opt out."
            )
            subject = f"Competitor snapshot for {lead.get('name', 'your shop')} — agents run it after pay"
            try:
                from sincor2.compliance_guardrails import guardrails
                guardrails.check_email_send([email], text + "\n" + html, subject=subject, agent="outreach_agent")
            except Exception as e:
                logger.error("[OUTREACH] Guardrails blocked %s: %s", email, e)
                return False
            response = resend_client.emails.send({
                "from": from_addr, "to": email, "subject": subject,
                "html": html, "text": text, "reply_to": self.from_email,
            })
            msg_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", "unknown")
            logger.info("[OUTREACH] Sent %s (%s) msg_id=%s", email, lead.get("name"), msg_id)
            return True
        except Exception as e:
            logger.error("[OUTREACH] Send failed for %s: %s", lead.get("name"), e)
            return False

    def _load_sent_ids(self) -> set:
        if self.sent_log_path.exists():
            try:
                return set(json.loads(self.sent_log_path.read_text()))
            except Exception:
                pass
        return set()

    def _mark_sent(self, lead_id: str):
        self._sent_ids.add(lead_id)
        try:
            self.sent_log_path.write_text(json.dumps(list(self._sent_ids)))
        except Exception as e:
            logger.warning("[OUTREACH] Persist sent log failed: %s", e)

    def run_cycle(self) -> Dict:
        if not self.enabled:
            return {"status": "disabled", "sent": 0, "leads_found": 0}
        resend_key = os.environ.get("RESEND_API_KEY", "")
        if not resend_key:
            logger.warning("[OUTREACH] RESEND_API_KEY missing")
            return {"status": "no_resend_key", "sent": 0, "leads_found": 0}
        if not self.yelp_key:
            return {"status": "no_yelp_key", "sent": 0, "leads_found": 0}
        try:
            from resend import Resend
            resend_client = Resend(api_key=resend_key)
        except ImportError:
            return {"status": "resend_not_installed", "sent": 0, "leads_found": 0}
        sent_count = 0
        cycle_idx = int(time.time() // 3600) % len(self.TARGET_CATEGORIES)
        category, location = self.TARGET_CATEGORIES[cycle_idx]
        leads = self.fetch_yelp_leads(category, location, limit=25)
        for lead in leads:
            if sent_count >= self.daily_limit:
                break
            lead = self.enrich_with_google_places(lead)
            scraped = self._scrape_emails(lead.get("website", ""))
            if scraped:
                lead["email"] = scraped[0]
            elif lead.get("website") and not lead.get("email"):
                lead["email"] = self._guess_email(lead["website"])
            if self.send_outreach_email(lead, resend_client):
                self._mark_sent(lead["id"])
                sent_count += 1
            time.sleep(2)
        summary = {
            "status": "ok", "timestamp": datetime.utcnow().isoformat(),
            "category": category, "location": location, "leads_found": len(leads),
            "sent": sent_count, "total_sent_ever": len(self._sent_ids),
            "close_path": BUY_URL, "treasury": TREASURY,
        }
        logger.info("[OUTREACH] Cycle complete: %s", summary)
        return summary


_engine: Optional[OutreachEngine] = None


def get_outreach_engine() -> OutreachEngine:
    global _engine
    if _engine is None:
        _engine = OutreachEngine()
    return _engine
