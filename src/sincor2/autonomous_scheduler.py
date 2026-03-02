"""
SINCOR Autonomous Background Task Scheduler
Runs lead discovery and outreach continuously without manual intervention
"""

import schedule
import time
import logging
import json
import threading
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import intelligence engine for real lead discovery
try:
    from src.sincor2.lead_intelligence_engine import LeadIntelligenceEngine
    intelligence_engine = LeadIntelligenceEngine()
except ImportError:
    intelligence_engine = None
    logger.warning("LeadIntelligenceEngine not available")


class AutonomousTaskScheduler:
    """Schedules and runs autonomous revenue generation tasks"""

    def __init__(self, lead_engine, outreach_handler, closing_engine=None):
        self.lead_engine = lead_engine
        self.outreach_handler = outreach_handler
        self.closing_engine = closing_engine

    def task_discover_leads(self):
        """Discover new leads using Apollo.io + NewsAPI"""
        logger.info("Running lead discovery task...")

        if not intelligence_engine:
            logger.warning("Intelligence engine not available")
            return {
                'task': 'lead_discovery',
                'timestamp': datetime.utcnow().isoformat(),
                'leads_found': 0,
                'status': 'engine_not_available'
            }

        # For MVP: Get hot industries and search them
        # In production: would have custom lead source queries
        search_queries = [
            'SaaS companies 50-100 employees',
            'FinTech startups Series A-B funding',
            'E-commerce platforms scaling',
            'Enterprise software Series B-C',
            'Data analytics companies hiring'
        ]

        leads_found = 0
        leads_qualified = 0

        for query in search_queries:
            try:
                logger.info(f"Searching: {query}")
                companies = intelligence_engine.search_companies(query, limit=3)

                for company in companies:
                    try:
                        company_name = company.get('name')
                        logger.info(f"Qualifying: {company_name}")

                        # Auto-qualify the company
                        qualification = intelligence_engine.auto_qualify_company(company_name)

                        if qualification and qualification['should_contact']:
                            # Create lead in database
                            lead_id = intelligence_engine.create_lead_from_qualification(
                                self.lead_engine,
                                qualification
                            )

                            if lead_id:
                                leads_qualified += 1
                                logger.info(f"Created lead: {company_name} "
                                           f"(Score: {qualification['composite_score']:.1f})")

                        leads_found += 1

                    except Exception as e:
                        logger.error(f"Error qualifying company: {e}")
                        continue

            except Exception as e:
                logger.error(f"Error in search query '{query}': {e}")
                continue

        logger.info(f"Lead discovery complete: {leads_found} searched, {leads_qualified} qualified")

        return {
            'task': 'lead_discovery',
            'timestamp': datetime.utcnow().isoformat(),
            'companies_searched': leads_found,
            'leads_qualified': leads_qualified,
            'status': 'complete'
        }

    def task_score_leads(self):
        """Score unscored leads and calculate fit"""
        logger.info("Running lead scoring task...")

        # TODO: Get unscored leads and run scoring model
        # For now, placeholder

        return {
            'task': 'lead_scoring',
            'timestamp': datetime.utcnow().isoformat(),
            'leads_scored': 0,
            'status': 'pending_integration'
        }

    def task_autonomous_outreach(self):
        """Run autonomous agent outreach cycle"""
        logger.info("Running autonomous outreach task...")

        result = self.outreach_handler.run_outreach_cycle()

        logger.info(f"Outreach complete: {result['outreach_attempts']} attempts")

        return result

    def task_follow_up(self):
        """Follow up with leads that haven't responded"""
        logger.info("Running follow-up task...")

        # TODO: Check outreach log for non-responses
        # Send follow-up to leads with no engagement after 3 days

        return {
            'task': 'follow_up',
            'timestamp': datetime.utcnow().isoformat(),
            'follow_ups_sent': 0,
            'status': 'pending_implementation'
        }

    def task_generate_proposals(self):
        """Generate and send proposals for qualified leads"""
        logger.info("Running proposal generation task...")

        if not self.closing_engine:
            return {'task': 'generate_proposals', 'proposals_sent': 0, 'status': 'engine_not_available'}

        # Check for qualified opportunities without proposals
        import sqlite3
        try:
            conn = sqlite3.connect('data/sincor.db')
            cursor = conn.cursor()

            # Get qualified opportunities without proposals
            cursor.execute('''
                SELECT id, lead_id FROM sales_opportunities
                WHERE qualification_stage = 'interested'
                AND proposal_id IS NULL
                AND received_at > datetime('now', '-3 days')
                LIMIT 5
            ''')

            opportunities = cursor.fetchall()
            proposals_sent = 0

            for opp_id, lead_id in opportunities:
                try:
                    # Get lead info
                    cursor.execute('SELECT * FROM leads WHERE id = ?', (lead_id,))
                    lead_info = cursor.fetchone()

                    if lead_info:
                        # Generate proposal with basic lead info
                        lead_data = {
                            'company_name': lead_info[1],
                            'decision_maker_name': lead_info[6]
                        }

                        # Determine service type from lead's recommended_service
                        service_type = lead_info[13] or 'intelligence'

                        self.closing_engine.generate_proposal(
                            opportunity_id=opp_id,
                            lead_info=lead_data,
                            service_type=service_type
                        )
                        proposals_sent += 1
                except Exception as e:
                    logger.error(f"Error generating proposal for {opp_id}: {e}")

            conn.close()

            logger.info(f"Proposal generation complete: {proposals_sent} proposals sent")

            return {
                'task': 'generate_proposals',
                'timestamp': datetime.utcnow().isoformat(),
                'proposals_sent': proposals_sent,
                'status': 'complete'
            }
        except Exception as e:
            logger.error(f"Error in proposal generation task: {e}")
            return {
                'task': 'generate_proposals',
                'proposals_sent': 0,
                'status': 'error',
                'error': str(e)
            }

    def task_handle_objections(self):
        """Handle outstanding objections and follow up"""
        logger.info("Running objection handling task...")

        if not self.closing_engine:
            return {'task': 'handle_objections', 'objections_handled': 0, 'status': 'engine_not_available'}

        # Scan for opportunities in objection status without responses
        import sqlite3
        try:
            conn = sqlite3.connect('data/sincor.db')
            cursor = conn.cursor()

            # Get recent objections without responses sent in last 4 hours
            cursor.execute('''
                SELECT id FROM sales_opportunities
                WHERE status = 'objection'
                AND objection_response_sent_at IS NULL
                AND received_at > datetime('now', '-4 hours')
                LIMIT 3
            ''')

            opportunities = cursor.fetchall()
            objections_handled = 0

            for (opp_id,) in opportunities:
                try:
                    # The objection response is auto-generated during handle_objection
                    # Just mark that we've processed it
                    cursor.execute('''
                        UPDATE sales_opportunities SET
                            objection_response_sent_at = ?
                        WHERE id = ? AND objection_response_sent_at IS NULL
                    ''', (datetime.utcnow(), opp_id))

                    objections_handled += 1
                except Exception as e:
                    logger.error(f"Error handling objection {opp_id}: {e}")

            conn.commit()
            conn.close()

            logger.info(f"Objection handling complete: {objections_handled} handled")

            return {
                'task': 'handle_objections',
                'timestamp': datetime.utcnow().isoformat(),
                'objections_handled': objections_handled,
                'status': 'complete'
            }
        except Exception as e:
            logger.error(f"Error in objection handling task: {e}")
            return {
                'task': 'handle_objections',
                'objections_handled': 0,
                'status': 'error',
                'error': str(e)
            }

    def schedule_jobs(self):
        """Schedule all background jobs"""

        # Discover leads every 12 hours
        schedule.every(12).hours.do(self.task_discover_leads)

        # Score leads every 6 hours
        schedule.every(6).hours.do(self.task_score_leads)

        # Autonomous outreach every 3 hours (during business hours conceptually)
        schedule.every(3).hours.do(self.task_autonomous_outreach)

        # Follow-ups every 24 hours
        schedule.every(24).hours.do(self.task_follow_up)

        # Generate proposals every 6 hours for qualified leads
        schedule.every(6).hours.do(self.task_generate_proposals)

        # Handle objections every 4 hours
        schedule.every(4).hours.do(self.task_handle_objections)

        logger.info("Scheduled autonomous tasks:")
        logger.info("  - Lead discovery: Every 12 hours")
        logger.info("  - Lead scoring: Every 6 hours")
        logger.info("  - Autonomous outreach: Every 3 hours")
        logger.info("  - Follow-ups: Every 24 hours")
        logger.info("  - Proposal generation: Every 6 hours")
        logger.info("  - Objection handling: Every 4 hours")

    def run_scheduler(self, background=False):
        """Run the scheduler loop"""

        self.schedule_jobs()

        if background:
            # Run in background (return immediately)
            logger.info("Scheduler starting in background mode")
            return

        # Run continuously (blocking)
        logger.info("Scheduler starting continuous mode")
        while True:
            schedule.run_pending()
            time.sleep(60)

    def run_once_immediate(self):
        """Run all tasks once immediately (for testing)"""
        logger.info("=== Running all tasks immediately ===")

        results = {
            'lead_discovery': self.task_discover_leads(),
            'lead_scoring': self.task_score_leads(),
            'autonomous_outreach': self.task_autonomous_outreach(),
            'follow_up': self.task_follow_up(),
            'generate_proposals': self.task_generate_proposals(),
            'handle_objections': self.task_handle_objections(),
            'timestamp': datetime.utcnow().isoformat()
        }

        logger.info(f"=== Immediate execution complete ===")
        logger.info(json.dumps(results, indent=2, default=str))

        return results


# Helper function to start scheduler as background thread
def start_scheduler_background(lead_engine, outreach_handler, closing_engine=None):
    """Start the scheduler in a background thread"""
    import threading
    import json

    scheduler = AutonomousTaskScheduler(lead_engine, outreach_handler, closing_engine)

    def run():
        scheduler.schedule_jobs()
        while True:
            schedule.run_pending()
            time.sleep(60)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    logger.info("Background scheduler thread started")
    return scheduler
