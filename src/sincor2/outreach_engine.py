"""
SINCOR Autonomous Outreach Engine
Fetches local business leads from Yelp, enriches with Google Places,
sends cold outreach emails via Resend.

ENV vars needed (set in Railway):
  YELP_API_KEY         - Yelp Fusion API key
  GOOGLE_PLACES_API_KEY or GOOGLE_API_KEY - Google Places API key
  RESEND_API_KEY       - Already set for transactional email
  OUTREACH_FROM_EMAIL  - Sender address (default: support@getsincor.com)
  OUTREACH_DAILY_LIMIT - Max emails/day (default: 20)
  OUTREACH_ENABLED     - Set to 'true' to enable (default: false, safety switch)
"""

import os
import json
import logging
import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import requests

logger = logging.getLogger('sincor2.outreach')


class OutreachEngine:
    """Fetch leads from Yelp + Google Places, send cold outreach via Resend."""

    YELP_SEARCH_URL = 'https://api.yelp.com/v3/businesses/search'
    PLACES_FIND_URL = 'https://maps.googleapis.com/maps/api/place/findplacefromtext/json'
    PLACES_DETAILS_URL = 'https://maps.googleapis.com/maps/api/place/details/json'

    # Target business categories — ICP: med spas, gyms, real estate agents
    # These are high-ticket service businesses that care about competitive intel
    TARGET_CATEGORIES = [
        ('medspas', 'Chicago, IL'),
        ('medspa', 'Chicago, IL'),
        ('gyms', 'Chicago, IL'),
        ('fitness', 'Chicago, IL'),
        ('real_estate', 'Chicago, IL'),
        ('yoga', 'Chicago, IL'),
        ('pilates', 'Chicago, IL'),
        ('personal_trainers', 'Chicago, IL'),
        ('medspas', 'Dallas, TX'),
        ('gyms', 'Dallas, TX'),
        ('real_estate', 'Dallas, TX'),
        ('medspas', 'Miami, FL'),
        ('gyms', 'Miami, FL'),
        ('real_estate', 'Miami, FL'),
    ]

    def __init__(self):
        self.yelp_key = os.environ.get('YELP_API_KEY', '')
        self.places_key = (
            os.environ.get('GOOGLE_PLACES_API_KEY') or
            os.environ.get('GOOGLE_API_KEY', '')
        )
        self.enabled = os.environ.get('OUTREACH_ENABLED', 'false').lower() == 'true'
        self.daily_limit = int(os.environ.get('OUTREACH_DAILY_LIMIT', '20'))
        self.from_email = os.environ.get(
            'OUTREACH_FROM_EMAIL',
            os.environ.get('SINCOR_EMAIL', 'support@getsincor.com')
        )
        self.from_name = os.environ.get('OUTREACH_FROM_NAME', 'Court at SINCOR')

        # Sent-log so we never double-email the same business
        self.data_dir = Path('/tmp/sincor_outreach')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sent_log_path = self.data_dir / 'sent.json'
        self._sent_ids = self._load_sent_ids()

        if not self.yelp_key:
            logger.warning('[OUTREACH] YELP_API_KEY not set — no leads will be fetched')
        if not self.places_key:
            logger.warning('[OUTREACH] GOOGLE_PLACES_API_KEY not set — email enrichment disabled')
        if not self.enabled:
            logger.info('[OUTREACH] Outreach disabled (OUTREACH_ENABLED != true). Set it in Railway to activate.')

    # -----------------------------------------------------------------------
    # Lead fetching

    def fetch_yelp_leads(self, category: str, location: str, limit: int = 10) -> List[Dict]:
        """Pull businesses from Yelp Fusion."""
        if not self.yelp_key:
            return []
        try:
            resp = requests.get(
                self.YELP_SEARCH_URL,
                headers={'Authorization': f'Bearer {self.yelp_key}'},
                params={
                    'term': category,
                    'location': location,
                    'limit': limit,
                    'sort_by': 'rating',
                },
                timeout=10,
            )
            resp.raise_for_status()
            businesses = resp.json().get('businesses', [])
            leads = []
            for biz in businesses:
                lead_id = biz.get('id', '')
                if not lead_id or lead_id in self._sent_ids:
                    continue
                leads.append({
                    'id': lead_id,
                    'name': biz.get('name', ''),
                    'phone': biz.get('phone', ''),
                    'url': biz.get('url', ''),
                    'rating': biz.get('rating', 0),
                    'review_count': biz.get('review_count', 0),
                    'category': category,
                    'location': location,
                    'address': ', '.join(biz.get('location', {}).get('display_address', [])),
                    'email': '',  # Yelp doesn't expose email — enrich via Places
                })
            logger.info(f'[OUTREACH] Yelp: {len(leads)} new leads for {category} in {location}')
            return leads
        except Exception as e:
            logger.error(f'[OUTREACH] Yelp fetch error: {e}')
            return []

    def enrich_with_google_places(self, lead: Dict) -> Dict:
        """Look up business in Google Places to find email/website."""
        if not self.places_key or not lead.get('name'):
            return lead
        try:
            query = f"{lead['name']} {lead.get('address', lead.get('location', ''))}"
            find_resp = requests.get(
                self.PLACES_FIND_URL,
                params={
                    'input': query,
                    'inputtype': 'textquery',
                    'fields': 'place_id,name',
                    'key': self.places_key,
                },
                timeout=8,
            )
            find_resp.raise_for_status()
            candidates = find_resp.json().get('candidates', [])
            if not candidates:
                return lead

            place_id = candidates[0]['place_id']
            details_resp = requests.get(
                self.PLACES_DETAILS_URL,
                params={
                    'place_id': place_id,
                    'fields': 'name,formatted_phone_number,website,url',
                    'key': self.places_key,
                },
                timeout=8,
            )
            details_resp.raise_for_status()
            result = details_resp.json().get('result', {})

            lead['website'] = result.get('website', '')
            if not lead.get('phone') and result.get('formatted_phone_number'):
                lead['phone'] = result['formatted_phone_number']

            logger.debug(f'[OUTREACH] Places enriched: {lead["name"]} → website={lead.get("website")}')
        except Exception as e:
            logger.warning(f'[OUTREACH] Places enrich error for {lead.get("name")}: {e}')
        return lead

    # -----------------------------------------------------------------------
    # Email sending

    def _build_email_html(self, lead: Dict) -> str:
        name = lead.get('name', 'there')
        rating = lead.get('rating', '')
        category = lead.get('category', '')
        
        # Personalize based on what we know about them
        rating_line = ''
        if rating:
            if float(rating) >= 4.5:
                rating_line = f'<p>Congrats on your strong {rating}⭐ rating — you\'re clearly doing something right.</p>'
            elif float(rating) < 4.0:
                rating_line = f'<p>I noticed your {rating}⭐ rating. I can show you exactly what your highest-rated competitors are doing differently.</p>'

        return f"""
<html>
<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#333;">
<p>Hi {name} team,</p>
<p>Quick question: do you know what your top 3 competitors are charging right now?</p>
{rating_line}
<p>I'm Court at <a href="https://getsincor.com">SINCOR</a>. We build competitive intelligence
reports for local service businesses — so you can see exactly what your rivals are doing
(pricing, marketing, reviews, everything) in one clear PDF.</p>
<p>It's $49 as a one-time report, or $149/month if you want fresh intel every month.</p>
<p>Most of our clients say the first report pays for itself the same week — usually by
catching a pricing gap they didn't know existed.</p>
<p>Interested? Just reply here and I'll get your report started. Takes 5 minutes on your end.</p>
<p>— Court<br>
SINCOR | getsincor.com<br>
<a href="mailto:support@getsincor.com">support@getsincor.com</a></p>
<hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
<p style="font-size:11px;color:#999;">
Your business appeared in a local directory. Not interested? Reply STOP and I won't reach out again.
</p>
</body>
</html>
"""

    def send_outreach_email(self, lead: Dict, resend_client) -> bool:
        """Send a cold-outreach email to a lead via Resend. Returns True on success."""
        # We only have email if we found it via website contact, otherwise skip
        # For now: if no email derivable, log and skip (don't spam blind)
        email = lead.get('email', '')
        if not email:
            logger.debug(f'[OUTREACH] No email for {lead.get("name")} — skipping send')
            return False

        try:
            from_addr = f"{self.from_name} <{self.from_email}>"
            html = self._build_email_html(lead)
            text = (
                f"Hi {lead.get('name', 'there')} team,\n\n"
                "Quick question: do you know what your top 3 competitors are charging right now?\n\n"
                "I'm Court at SINCOR. We build competitive intelligence reports for local service "
                "businesses — pricing, reviews, marketing, everything in one PDF.\n\n"
                "$49 one-time or $149/month for monthly intel.\n\n"
                "Most clients say it pays for itself the same week.\n\n"
                "Interested? Just reply and I'll get your report started.\n\n"
                "— Court\nSINCOR | getsincor.com\n"
                "---\nReply STOP to opt out."
            )
            response = resend_client.emails.send({
                'from': from_addr,
                'to': email,
                'subject': f"What are your competitors charging? (for {lead.get('name', 'your business')})",
                'html': html,
                'text': text,
                'reply_to': self.from_email,
            })
            msg_id = response.get('id') if isinstance(response, dict) else getattr(response, 'id', 'unknown')
            logger.info(f'[OUTREACH] Sent to {email} ({lead.get("name")}) msg_id={msg_id}')
            return True
        except Exception as e:
            logger.error(f'[OUTREACH] Email send failed for {lead.get("name")}: {e}')
            return False

    # -----------------------------------------------------------------------
    # Log management

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
            logger.warning(f'[OUTREACH] Failed to persist sent log: {e}')

    # -----------------------------------------------------------------------
    # Main cycle

    def run_cycle(self) -> Dict:
        """
        One outreach cycle:
          1. Fetch leads from Yelp (rotating through categories)
          2. Enrich with Google Places
          3. Email any with known addresses (respecting daily limit)
        Returns summary dict.
        """
        if not self.enabled:
            logger.info('[OUTREACH] Skipping cycle — OUTREACH_ENABLED is not true')
            return {'status': 'disabled', 'sent': 0, 'leads_found': 0}

        resend_key = os.environ.get('RESEND_API_KEY', '')
        if not resend_key:
            logger.warning('[OUTREACH] RESEND_API_KEY not set — cannot send outreach emails')
            return {'status': 'no_resend_key', 'sent': 0, 'leads_found': 0}

        try:
            from resend import Resend
            resend_client = Resend(api_key=resend_key)
        except ImportError:
            logger.error('[OUTREACH] resend library not installed')
            return {'status': 'resend_not_installed', 'sent': 0, 'leads_found': 0}

        sent_count = 0
        total_leads = 0

        # Rotate through one category per cycle to stay varied
        cycle_idx = int(time.time() // 3600) % len(self.TARGET_CATEGORIES)
        category, location = self.TARGET_CATEGORIES[cycle_idx]

        logger.info(f'[OUTREACH] Starting cycle — category={category} location={location}')

        leads = self.fetch_yelp_leads(category, location, limit=25)
        total_leads = len(leads)

        for lead in leads:
            if sent_count >= self.daily_limit:
                logger.info(f'[OUTREACH] Daily limit ({self.daily_limit}) reached')
                break

            lead = self.enrich_with_google_places(lead)

            # Try to derive email from website domain (simple heuristic)
            if not lead.get('email') and lead.get('website'):
                domain = lead['website'].replace('https://', '').replace('http://', '').split('/')[0]
                lead['email'] = f'info@{domain}'

            if send_ok := self.send_outreach_email(lead, resend_client):
                self._mark_sent(lead['id'])
                sent_count += 1

            # Polite rate limit: 1 email per 2 seconds
            time.sleep(2)

        summary = {
            'status': 'ok',
            'timestamp': datetime.utcnow().isoformat(),
            'category': category,
            'location': location,
            'leads_found': total_leads,
            'sent': sent_count,
            'total_sent_ever': len(self._sent_ids),
        }
        logger.info(f'[OUTREACH] Cycle complete: {summary}')
        return summary


# Singleton
_engine: Optional[OutreachEngine] = None

def get_outreach_engine() -> OutreachEngine:
    global _engine
    if _engine is None:
        _engine = OutreachEngine()
    return _engine
