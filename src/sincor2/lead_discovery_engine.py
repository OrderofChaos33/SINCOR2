"""
SINCOR Autonomous Lead Discovery Engine
Continuously finds qualified leads without human intervention
"""

import sqlite3
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LeadDiscoveryEngine:
    def __init__(self, db_path='data/sincor.db'):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_database()

    def init_database(self):
        """Create leads and outreach tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Leads table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leads (
                id TEXT PRIMARY KEY,
                company_name TEXT NOT NULL,
                website TEXT,
                industry TEXT,
                company_size TEXT,
                founded_year INT,

                -- Contact info
                decision_maker_name TEXT,
                decision_maker_email TEXT,
                decision_maker_title TEXT,

                -- Scoring
                fit_score FLOAT,          -- 0-100: company fit (size, industry, stage)
                intent_score FLOAT,       -- 0-100: purchase intent signals
                timing_score FLOAT,       -- 0-100: buying readiness
                composite_score FLOAT,    -- 0-100: overall lead quality

                -- Status
                status TEXT DEFAULT 'new',  -- new, contacted, interested, proposal_sent, won, lost
                last_contact TIMESTAMP,
                outreach_attempts INT DEFAULT 0,

                -- Service fit
                recommended_service TEXT,   -- intelligence, predictive, agents, automation, market
                estimated_deal_size DECIMAL,

                -- Evidence
                evidence JSONB,             -- why we think they need us

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Outreach attempts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS outreach_log (
                id TEXT PRIMARY KEY,
                lead_id TEXT NOT NULL REFERENCES leads(id),
                channel TEXT,               -- email, linkedin, phone
                message TEXT,
                sent_at TIMESTAMP,
                status TEXT,               -- sent, opened, clicked, replied, bounced
                response TEXT,
                agent_assigned TEXT,       -- which agent handled this
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

    def add_lead(self, company_name, website=None, industry=None, company_size=None,
                 decision_maker_name=None, decision_maker_email=None, decision_maker_title=None,
                 evidence=None):
        """Add a new lead to database"""
        lead_id = str(uuid.uuid4())

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO leads (
                id, company_name, website, industry, company_size,
                decision_maker_name, decision_maker_email, decision_maker_title,
                evidence, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            lead_id,
            company_name,
            website,
            industry,
            company_size,
            decision_maker_name,
            decision_maker_email,
            decision_maker_title,
            json.dumps(evidence) if evidence else None,
            'new',
            datetime.utcnow(),
            datetime.utcnow()
        ))

        conn.commit()
        conn.close()

        logger.info(f"Added lead: {company_name} ({lead_id})")
        return lead_id

    def score_lead(self, lead_id, fit_score, intent_score, timing_score,
                   recommended_service=None, estimated_deal_size=None):
        """Score a lead on multiple dimensions"""

        # Calculate composite score (weighted average)
        composite_score = (fit_score * 0.4) + (intent_score * 0.35) + (timing_score * 0.25)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE leads SET
                fit_score = ?,
                intent_score = ?,
                timing_score = ?,
                composite_score = ?,
                recommended_service = ?,
                estimated_deal_size = ?,
                updated_at = ?
            WHERE id = ?
        ''', (
            fit_score,
            intent_score,
            timing_score,
            composite_score,
            recommended_service,
            estimated_deal_size,
            datetime.utcnow(),
            lead_id
        ))

        conn.commit()
        conn.close()

        logger.info(f"Scored lead {lead_id}: Composite {composite_score:.1f}")
        return composite_score

    def get_hot_leads(self, threshold=75):
        """Get leads ready for outreach (score >= threshold)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM leads
            WHERE composite_score >= ?
            AND status IN ('new', 'interested')
            AND (last_contact IS NULL OR last_contact < datetime('now', '-7 days'))
            ORDER BY composite_score DESC
            LIMIT 20
        ''', (threshold,))

        results = cursor.fetchall()
        conn.close()

        return results

    def log_outreach(self, lead_id, channel, message, agent_assigned):
        """Log an outreach attempt"""
        outreach_id = str(uuid.uuid4())

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO outreach_log (
                id, lead_id, channel, message, sent_at, status, agent_assigned
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            outreach_id,
            lead_id,
            channel,
            message,
            datetime.utcnow(),
            'sent',
            agent_assigned
        ))

        # Increment outreach attempts
        cursor.execute('''
            UPDATE leads SET
                outreach_attempts = outreach_attempts + 1,
                last_contact = ?,
                updated_at = ?
            WHERE id = ?
        ''', (datetime.utcnow(), datetime.utcnow(), lead_id))

        conn.commit()
        conn.close()

        logger.info(f"Logged outreach to lead {lead_id} via {channel}")
        return outreach_id

    def update_lead_status(self, lead_id, status):
        """Update lead status (new → contacted → interested → proposal_sent → won)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE leads SET
                status = ?,
                updated_at = ?
            WHERE id = ?
        ''', (status, datetime.utcnow(), lead_id))

        conn.commit()
        conn.close()

        logger.info(f"Updated lead {lead_id} status to: {status}")

    def get_lead_stats(self):
        """Get summary stats on leads"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM leads')
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM leads WHERE status = 'new'")
        new_leads = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM leads WHERE status IN ('contacted', 'interested')")
        engaged = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM leads WHERE status = 'won'")
        closed = cursor.fetchone()[0]

        cursor.execute("SELECT AVG(composite_score) FROM leads WHERE composite_score IS NOT NULL")
        avg_score = cursor.fetchone()[0] or 0

        conn.close()

        return {
            'total_leads': total,
            'new_leads': new_leads,
            'engaged_leads': engaged,
            'closed_leads': closed,
            'avg_lead_score': round(avg_score, 1)
        }
