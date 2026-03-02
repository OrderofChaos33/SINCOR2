"""
SINCOR Lead Intelligence Engine
Autonomous lead discovery, enrichment, and intelligent scoring
Uses Apollo.io for company data + NewsAPI for signal tracking
"""

import requests
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LeadIntelligenceEngine:
    """
    Intelligently discovers and scores leads using real data APIs.
    Combines company metrics, hiring signals, and news indicators.
    """

    def __init__(self, db_path='data/sincor.db'):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # API Keys from environment
        self.apollo_key = os.getenv('APOLLO_API_KEY')
        self.news_key = os.getenv('NEWS_API_KEY')

        # API Endpoints
        self.apollo_base = 'https://api.apollo.io/v1'
        self.news_base = 'https://newsapi.org/v2'

        if not self.apollo_key or not self.news_key:
            logger.warning("Missing API keys! Set APOLLO_API_KEY and NEWS_API_KEY in .env")

    # =========================================================================
    # APOLLO.IO INTEGRATION - Company & Contact Data
    # =========================================================================

    def search_companies(self, query, limit=10):
        """Search for companies matching criteria via Apollo"""
        try:
            headers = {
                'Content-Type': 'application/json',
                'X-API-KEY': self.apollo_key
            }

            params = {
                'q': query,
                'limit': limit
            }

            response = requests.post(
                f'{self.apollo_base}/companies/search',
                headers=headers,
                json=params,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                logger.info(f"Found {len(data.get('companies', []))} companies for '{query}'")
                return data.get('companies', [])
            else:
                logger.error(f"Apollo search failed: {response.status_code}")
                return []

        except Exception as e:
            logger.error(f"Error searching companies: {e}")
            return []

    def get_company_details(self, company_id):
        """Get detailed information about a company"""
        try:
            headers = {
                'Content-Type': 'application/json',
                'X-API-KEY': self.apollo_key
            }

            response = requests.get(
                f'{self.apollo_base}/companies/{company_id}',
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                return response.json().get('company', {})
            return {}

        except Exception as e:
            logger.error(f"Error getting company details: {e}")
            return {}

    def get_company_contacts(self, company_id, limit=5):
        """Get decision makers / contacts at a company"""
        try:
            headers = {
                'Content-Type': 'application/json',
                'X-API-KEY': self.apollo_key
            }

            params = {
                'company_id': company_id,
                'limit': limit,
                'sort_by': 'title'
            }

            response = requests.post(
                f'{self.apollo_base}/contacts/search',
                headers=headers,
                json=params,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                return data.get('contacts', [])
            return []

        except Exception as e:
            logger.error(f"Error getting contacts: {e}")
            return []

    # =========================================================================
    # NEWSAPI INTEGRATION - Signal Detection
    # =========================================================================

    def get_company_news(self, company_name, days_back=30):
        """Get recent news about a company"""
        try:
            params = {
                'q': company_name,
                'sortBy': 'publishedAt',
                'language': 'en',
                'apiKey': self.news_key
            }

            response = requests.get(
                f'{self.news_base}/everything',
                params=params,
                timeout=10
            )

            if response.status_code == 200:
                articles = response.json().get('articles', [])
                logger.info(f"Found {len(articles)} news articles for {company_name}")
                return articles
            return []

        except Exception as e:
            logger.error(f"Error getting news: {e}")
            return []

    def detect_signals(self, articles):
        """Detect business signals from news articles"""
        signals = {
            'funding': [],
            'hiring': [],
            'leadership': [],
            'product_launch': [],
            'acquisition': [],
            'expansion': []
        }

        funding_keywords = ['funding', 'series', 'round', 'investment', 'raised', '$M', '$B']
        hiring_keywords = ['hiring', 'jobs', 'careers', 'positions', 'recruiting']
        leadership_keywords = ['CEO', 'CTO', 'VP', 'appoints', 'announces', 'new leadership']
        product_keywords = ['launches', 'announces', 'introduces', 'releases', 'product', 'feature']
        acquisition_keywords = ['acquires', 'acquired', 'merger', 'merged', 'buyout']
        expansion_keywords = ['expansion', 'enters', 'market', 'opens', 'expansion']

        for article in articles:
            title = article.get('title', '').lower()
            description = article.get('description', '').lower()
            combined = f"{title} {description}"

            # Detect funding
            if any(kw in combined for kw in funding_keywords):
                signals['funding'].append({
                    'title': article['title'],
                    'date': article['publishedAt'],
                    'score': 30
                })

            # Detect hiring
            if any(kw in combined for kw in hiring_keywords):
                signals['hiring'].append({
                    'title': article['title'],
                    'date': article['publishedAt'],
                    'score': 25
                })

            # Detect leadership changes
            if any(kw in combined for kw in leadership_keywords):
                signals['leadership'].append({
                    'title': article['title'],
                    'date': article['publishedAt'],
                    'score': 20
                })

            # Detect product launches
            if any(kw in combined for kw in product_keywords):
                signals['product_launch'].append({
                    'title': article['title'],
                    'date': article['publishedAt'],
                    'score': 20
                })

            # Detect acquisitions
            if any(kw in combined for kw in acquisition_keywords):
                signals['acquisition'].append({
                    'title': article['title'],
                    'date': article['publishedAt'],
                    'score': 25
                })

            # Detect expansion
            if any(kw in combined for kw in expansion_keywords):
                signals['expansion'].append({
                    'title': article['title'],
                    'date': article['publishedAt'],
                    'score': 15
                })

        return signals

    # =========================================================================
    # INTELLIGENT SCORING
    # =========================================================================

    def calculate_fit_score(self, company_data):
        """Calculate FIT SCORE (0-100): Does company profile match our ideal customer?"""
        score = 50  # baseline

        # Revenue assessment - can they afford our services?
        annual_revenue = company_data.get('annual_revenue', 0)
        if annual_revenue == 0:
            score -= 10
        elif annual_revenue < 500_000:
            score -= 20  # Too small
        elif 500_000 <= annual_revenue < 1_000_000:
            score += 10
        elif 1_000_000 <= annual_revenue < 10_000_000:
            score += 25  # Sweet spot
        elif 10_000_000 <= annual_revenue < 50_000_000:
            score += 20
        elif annual_revenue >= 50_000_000:
            score -= 10  # Enterprise (slow sales cycle)

        # Employee count
        employee_count = company_data.get('employee_count', 0)
        if 20 < employee_count < 500:
            score += 20  # Strong fit
        elif 500 <= employee_count < 5000:
            score += 10
        elif employee_count >= 5000:
            score -= 15  # Enterprise

        # Industry fit
        industry = company_data.get('industry', '').lower()
        good_industries = ['saas', 'software', 'financial', 'fintech', 'e-commerce',
                          'technology', 'retail', 'healthcare tech', 'edtech']
        if any(ind in industry for ind in good_industries):
            score += 20

        # Tech stack readiness (can they use AI tools?)
        tech_stack = company_data.get('technologies', [])
        if isinstance(tech_stack, list):
            tech_lower = [t.lower() for t in tech_stack]
            ai_tech = ['python', 'machine learning', 'ai', 'data science', 'analytics',
                      'tensorflow', 'aws', 'cloud', 'api']
            if any(tech in tech_lower for tech in ai_tech):
                score += 15

        return min(100, max(0, score))

    def calculate_intent_score(self, signals):
        """Calculate INTENT SCORE (0-100): Are they showing buying signals?"""
        score = 0

        # Heavy signals
        if signals['funding']:
            score += 35

        if signals['leadership']:
            score += 25

        if signals['hiring']:
            score += 25

        # Medium signals
        if signals['product_launch']:
            score += 15

        if signals['expansion']:
            score += 15

        if signals['acquisition']:
            score += 10

        return min(100, score)

    def calculate_timing_score(self, signals):
        """Calculate TIMING SCORE (0-100): When will they be ready to buy?"""
        score = 0

        # Check recency of signals - fresh signals = urgent need
        now = datetime.utcnow()

        for signal_type, signal_list in signals.items():
            if not signal_list:
                continue

            latest_signal = signal_list[0]  # Sorted by date
            signal_date = datetime.fromisoformat(latest_signal['date'].replace('Z', '+00:00'))
            days_old = (now - signal_date).days

            # Allocate score based on signal type AND recency
            if days_old < 7:
                score += signal_list[0].get('score', 0) * 1.5  # Very recent = extra urgent
            elif days_old < 30:
                score += signal_list[0].get('score', 0) * 1.2
            elif days_old < 90:
                score += signal_list[0].get('score', 0)

        return min(100, max(0, score))

    def auto_qualify_company(self, company_name, domain=None):
        """
        Complete flow: Search → Enrich → Score → Qualify
        Returns scored lead ready for outreach
        """
        logger.info(f"Auto-qualifying company: {company_name}")

        # Search for company
        search_results = self.search_companies(company_name, limit=1)
        if not search_results:
            logger.warning(f"No companies found for '{company_name}'")
            return None

        company = search_results[0]
        company_id = company.get('id')

        # Get detailed company info
        company_details = self.get_company_details(company_id)
        logger.info(f"Company details retrieved: {company_details.get('name', 'Unknown')}")

        # Get decision makers
        contacts = self.get_company_contacts(company_id, limit=3)
        logger.info(f"Found {len(contacts)} decision makers")

        # Get news signals
        news = self.get_company_news(company_name)
        signals = self.detect_signals(news)
        logger.info(f"Detected signals: Funding={len(signals['funding'])}, "
                   f"Hiring={len(signals['hiring'])}, "
                   f"Leadership={len(signals['leadership'])}")

        # Calculate scores
        fit_score = self.calculate_fit_score(company_details)
        intent_score = self.calculate_intent_score(signals)
        timing_score = self.calculate_timing_score(signals)
        composite_score = (fit_score * 0.4) + (intent_score * 0.35) + (timing_score * 0.25)

        # Determine if we should contact
        should_contact = composite_score >= 60

        result = {
            'company_name': company_details.get('name'),
            'domain': company_details.get('website_url'),
            'industry': company_details.get('industry'),
            'company_size': company_details.get('employee_count'),
            'annual_revenue': company_details.get('annual_revenue'),
            'fit_score': round(fit_score, 1),
            'intent_score': round(intent_score, 1),
            'timing_score': round(timing_score, 1),
            'composite_score': round(composite_score, 1),
            'should_contact': should_contact,
            'decision_makers': [
                {
                    'name': c.get('first_name', '') + ' ' + c.get('last_name', ''),
                    'email': c.get('email'),
                    'title': c.get('title'),
                    'department': c.get('department')
                }
                for c in contacts if c.get('email')
            ],
            'signals': signals,
            'recommended_service': self.recommend_service(signals, company_details),
            'estimated_deal_size': self.estimate_deal_size(composite_score)
        }

        logger.info(f"Qualification complete: {company_details.get('name')} - "
                   f"Score: {composite_score:.1f} - Contact: {should_contact}")

        return result

    def recommend_service(self, signals, company_data):
        """Recommend which service best fits this company"""
        employee_count = company_data.get('employee_count', 0)
        revenue = company_data.get('annual_revenue', 0)

        # Hiring signals -> need automation
        if signals['hiring']:
            return 'agents'

        # Expansion signals -> need market intelligence
        if signals['expansion'] or signals['product_launch']:
            return 'intelligence'

        # Funding signals -> need financial/predictive
        if signals['funding']:
            return 'predictive'

        # Large companies often need automation or intelligence
        if employee_count > 100:
            return 'automation'

        # Default to intelligence (broadest appeal)
        return 'intelligence'

    def estimate_deal_size(self, composite_score):
        """Estimate how much they might spend"""
        if composite_score >= 85:
            return 25000  # Enterprise/high-value
        elif composite_score >= 75:
            return 15000  # Strong fit
        elif composite_score >= 65:
            return 8000   # Good fit
        else:
            return 5000   # Basic service

    def create_lead_from_qualification(self, lead_engine, qualification_result):
        """Take qualification result and create lead in database"""
        if not qualification_result or not qualification_result['should_contact']:
            return None

        # Get best decision maker
        best_contact = qualification_result['decision_makers'][0] if qualification_result['decision_makers'] else {}

        # Create lead
        lead_id = lead_engine.add_lead(
            company_name=qualification_result['company_name'],
            website=qualification_result['domain'],
            industry=qualification_result['industry'],
            company_size=qualification_result['company_size'],
            decision_maker_name=best_contact.get('name', ''),
            decision_maker_email=best_contact.get('email', ''),
            decision_maker_title=best_contact.get('title', ''),
            evidence={
                'fit_score': qualification_result['fit_score'],
                'intent_score': qualification_result['intent_score'],
                'timing_score': qualification_result['timing_score'],
                'signals': qualification_result['signals'],
                'recommendation': qualification_result['recommended_service']
            }
        )

        # Score the lead
        lead_engine.score_lead(
            lead_id=lead_id,
            fit_score=qualification_result['fit_score'],
            intent_score=qualification_result['intent_score'],
            timing_score=qualification_result['timing_score'],
            recommended_service=qualification_result['recommended_service'],
            estimated_deal_size=qualification_result['estimated_deal_size']
        )

        return lead_id
