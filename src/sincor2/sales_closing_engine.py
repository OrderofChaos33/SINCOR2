"""
SINCOR Sales Closing Engine
Converts lead responses into closed deals with automated proposal generation
and objection handling
"""

import sqlite3
import json
import uuid
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SalesClosingEngine:
    """Manages complete sales conversion funnel from response to deal closure"""

    def __init__(self, db_path='data/sincor.db'):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_database()

    def init_database(self):
        """Create sales opportunities, proposals, and objections tables"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        # Sales opportunities table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sales_opportunities (
                id TEXT PRIMARY KEY,
                lead_id TEXT NOT NULL,
                status TEXT DEFAULT 'new',  -- new, qualified, proposal_sent, objection, follow_up, closed_won, closed_lost

                -- Response tracking
                response_text TEXT,
                response_channel TEXT,  -- email, form, linkedin, phone
                received_at TIMESTAMP,

                -- Qualification
                sentiment_score FLOAT,  -- -1.0 (negative) to 1.0 (positive)
                qualification_stage TEXT,  -- interested, objecting, exploring
                auto_qualified BOOLEAN DEFAULT 0,

                -- Proposal
                proposal_id TEXT,
                proposal_sent_at TIMESTAMP,
                proposal_engagement_score FLOAT,  -- opens, clicks, etc
                proposal_accepted BOOLEAN,

                -- Objection handling
                objection_text TEXT,
                objection_category TEXT,  -- price, competitor, timing, need, other
                objection_response_sent_at TIMESTAMP,

                -- Deal closing
                order_id TEXT,
                final_amount INTEGER,  -- cents
                closed_at TIMESTAMP,

                -- Agent tracking
                outreach_agent TEXT,  -- which agent contacted lead
                qualifying_agent TEXT,  -- which agent qualified response
                closing_agent TEXT,  -- which agent closed deal

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Proposals table (for detailed tracking)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                sales_opportunity_id TEXT NOT NULL REFERENCES sales_opportunities(id),
                lead_id TEXT NOT NULL,

                -- Proposal content
                title TEXT NOT NULL,
                content TEXT NOT NULL,  -- Customized proposal text
                estimated_price INTEGER,  -- cents
                estimated_timeline TEXT,  -- "2-4 weeks", "4-6 weeks", etc

                -- Engagement
                sent_at TIMESTAMP,
                opened_at TIMESTAMP,
                first_click_at TIMESTAMP,
                accepted_at TIMESTAMP,

                -- Score
                engagement_score FLOAT DEFAULT 0,  -- Increases on open/click

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Objections table (for tracking common patterns)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS objections (
                id TEXT PRIMARY KEY,
                sales_opportunity_id TEXT NOT NULL REFERENCES sales_opportunities(id),
                lead_id TEXT NOT NULL,

                -- Objection details
                objection_text TEXT NOT NULL,
                category TEXT,  -- price, competitor, timing, need, other
                confidence_score FLOAT,  -- How confident we are in categorization

                -- Response
                response_type TEXT,  -- discount, differentiation, follow_up, consultation
                response_text TEXT,
                sent_at TIMESTAMP,

                -- Outcome
                resolved BOOLEAN DEFAULT 0,
                resolved_at TIMESTAMP,
                resolution_type TEXT,  -- conceded, addressed, rescheduled

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Agent commissions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_commissions (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                lead_id TEXT NOT NULL,
                sales_opportunity_id TEXT,
                order_id TEXT,

                -- Commission details
                touchpoint_type TEXT NOT NULL,  -- outreach, qualification, closing, proposal_generation
                commission_amount FLOAT,  -- dollars (not cents)
                commission_percent FLOAT,  -- percentage of deal

                -- Status
                earned_at TIMESTAMP,
                paid BOOLEAN DEFAULT 0,
                paid_at TIMESTAMP,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

    def receive_lead_response(self, lead_id, response_text, channel, outreach_agent=None):
        """Receive and log a lead response"""
        opportunity_id = str(uuid.uuid4())

        # Auto-qualify response
        sentiment, qualification = self.qualify_response(response_text)

        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO sales_opportunities (
                id, lead_id, status, response_text, response_channel, received_at,
                sentiment_score, qualification_stage, auto_qualified, outreach_agent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            opportunity_id,
            lead_id,
            'qualified' if qualification == 'interested' else 'new',
            response_text,
            channel,
            datetime.utcnow(),
            sentiment,
            qualification,
            1,  # auto_qualified
            outreach_agent or 'system'
        ))

        conn.commit()
        conn.close()

        logger.info(f"Received response from lead {lead_id}: {qualification} (sentiment: {sentiment:.2f})")

        return {
            'opportunity_id': opportunity_id,
            'lead_id': lead_id,
            'qualification': qualification,
            'sentiment': sentiment,
            'next_action': 'generate_proposal' if qualification == 'interested' else 'follow_up'
        }

    def qualify_response(self, response_text):
        """
        Simple rule-based qualification of lead response.
        Returns: (sentiment_score, qualification_stage)
        sentiment_score: -1.0 to 1.0 (negative to positive)
        qualification_stage: interested, objecting, or exploring
        """

        text_lower = response_text.lower()

        # Positive signals (interested)
        interested_signals = [
            'interested', 'sounds great', 'love it', 'perfect', 'exactly what we need',
            'when can you', 'how soon', 'lets do this', 'lets move forward',
            'send me proposal', 'lets set up', 'meeting', 'call', 'discuss'
        ]

        # Objection signals (objecting)
        objection_signals = [
            'too expensive', 'too costly', 'too much', 'cost',
            'not sure', "don't think", 'think about it',
            'competitor', 'already using', 'have another',
            'later', 'next quarter', 'next year', "we'll think about it"
        ]

        # Neutral signals (exploring)
        exploring_signals = [
            'more information', 'tell me more', 'details', 'question',
            'how does it work', 'can you explain', 'send me',
            'portfolio', 'case study', 'pricing'
        ]

        interested_count = sum(1 for signal in interested_signals if signal in text_lower)
        objection_count = sum(1 for signal in objection_signals if signal in text_lower)
        exploring_count = sum(1 for signal in exploring_signals if signal in text_lower)

        # Calculate sentiment score
        sentiment = (interested_count - objection_count) / (interested_count + objection_count + exploring_count + 1)

        # Determine qualification stage
        if interested_count > objection_count and interested_count >= exploring_count:
            qualification = 'interested'
        elif objection_count > interested_count:
            qualification = 'objecting'
        else:
            qualification = 'exploring'

        return sentiment, qualification

    def generate_proposal(self, opportunity_id, lead_info, service_type, pricing_engine=None):
        """Generate a customized proposal for the lead"""
        proposal_id = str(uuid.uuid4())

        # Get opportunity details
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM sales_opportunities WHERE id = ?', (opportunity_id,))
        opportunity = cursor.fetchone()

        if not opportunity:
            logger.error(f"Opportunity {opportunity_id} not found")
            return None

        lead_id = opportunity[1]
        response_text = opportunity[3]

        # Build proposal based on service type
        proposal_title, proposal_content, estimated_price, timeline = self.build_proposal_content(
            lead_info, service_type, response_text
        )

        # Create proposal
        cursor.execute('''
            INSERT INTO proposals (
                id, sales_opportunity_id, lead_id, title, content,
                estimated_price, estimated_timeline, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            proposal_id,
            opportunity_id,
            lead_id,
            proposal_title,
            proposal_content,
            int(estimated_price * 100) if isinstance(estimated_price, float) else estimated_price,
            timeline,
            datetime.utcnow()
        ))

        # Update sales opportunity
        cursor.execute('''
            UPDATE sales_opportunities SET
                proposal_id = ?, proposal_sent_at = ?, status = ?, updated_at = ?
            WHERE id = ?
        ''', (
            proposal_id,
            datetime.utcnow(),
            'proposal_sent',
            datetime.utcnow(),
            opportunity_id
        ))

        conn.commit()
        conn.close()

        logger.info(f"Generated proposal {proposal_id} for opportunity {opportunity_id}")

        return {
            'proposal_id': proposal_id,
            'opportunity_id': opportunity_id,
            'lead_id': lead_id,
            'title': proposal_title,
            'content': proposal_content[:200] + "...",
            'estimated_price': estimated_price,
            'timeline': timeline
        }

    def build_proposal_content(self, lead_info, service_type, response_text):
        """Generate customized proposal based on service and lead response"""

        company = lead_info.get('company_name', 'your company')
        decision_maker = lead_info.get('decision_maker_name', 'there').split()[0]

        templates = {
            'intelligence': {
                'title': f'Competitive Intelligence Report - {company}',
                'content': f"""Hi {decision_maker},

Thank you for your interest in competitive intelligence for {company}.

Based on your inquiry, here's what we propose:

**Scope:**
- Comprehensive competitor analysis (5-10 key competitors)
- Market positioning assessment
- Strategic recommendations for differentiation
- Quarterly intelligence briefings

**Deliverables:**
1. Initial competitive landscape analysis (Week 1)
2. Detailed competitor profiles with SWOT analysis (Week 2)
3. Strategic positioning recommendations (Week 3)
4. Executive briefing presentation (Week 4)
5. Quarterly update mechanism for ongoing insights

**Timeline:** 4 weeks for initial report, then monthly updates

**Investment:** $8,500 for comprehensive analysis + $2,000/month for quarterly updates

We've helped similar companies in your industry identify $2M+ in competitive advantages.

Would you like to schedule a 30-minute discovery call to discuss your specific needs?

Best regards,
SINCOR Intelligence Team""",
                'price': 8500,
                'timeline': '4 weeks'
            },
            'predictive': {
                'title': f'Predictive Analytics Model - {company}',
                'content': f"""Hi {decision_maker},

Thank you for your interest in predictive analytics.

**What We'll Build:**
- Custom ML model trained on your data
- Churn prediction capability (predict customer loss 60+ days in advance)
- Lifetime value forecasting
- Revenue impact modeling

**Deliverables:**
1. Data assessment and modeling approach (Week 1)
2. Initial model training and validation (Week 2-3)
3. Deployed model with dashboard (Week 4)
4. Hands-on team training (Week 5)
5. 6-month support and optimization

**Timeline:** 5 weeks to fully deployed production model

**Investment:** $12,000 for model development + $1,500/month for support

Typical ROI: 15-20% improvement in retention metrics

Ready to predict and prevent churn?

Let's talk,
SINCOR Analytics Team""",
                'price': 12000,
                'timeline': '5 weeks'
            },
            'agents': {
                'title': f'AI Agent Implementation - {company}',
                'content': f"""Hi {decision_maker},

Thank you for sharing details about your process automation needs.

**What We'll Automate:**
Based on your description, we see opportunities to automate:
- Data entry and processing (15+ hours/week saved)
- Report generation (8+ hours/week saved)
- Customer outreach and follow-up (12+ hours/week saved)
- Administrative tasks (10+ hours/week saved)

**Total Estimated Savings:** $180K+/year in labor costs

**Our Approach:**
1. Process audit and optimization (Week 1)
2. Agent development and training (Weeks 2-3)
3. Testing and refinement (Week 4)
4. Full deployment with monitoring (Week 5)
5. Ongoing optimization and scaling (Months 2-6)

**Timeline:** 6 weeks to full automation, ongoing optimization

**Investment:** $15,000 for implementation + $2,000/month for operation and updates

Expected payback: 3-4 months

Let's build your automation factory.

Talk soon,
SINCOR Automation Team""",
                'price': 15000,
                'timeline': '6 weeks'
            }
        }

        template = templates.get(service_type, templates['intelligence'])
        return template['title'], template['content'], template['price'], template['timeline']

    def handle_objection(self, opportunity_id, objection_text):
        """Route objection to appropriate handler"""
        objection_id = str(uuid.uuid4())

        # Categorize objection
        category = self.categorize_objection(objection_text)

        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        # Get opportunity
        cursor.execute('SELECT * FROM sales_opportunities WHERE id = ?', (opportunity_id,))
        opportunity = cursor.fetchone()
        lead_id = opportunity[1] if opportunity else None

        # Create objection record
        cursor.execute('''
            INSERT INTO objections (
                id, sales_opportunity_id, lead_id, objection_text, category, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            objection_id,
            opportunity_id,
            lead_id,
            objection_text,
            category,
            datetime.utcnow()
        ))

        # Update opportunity
        cursor.execute('''
            UPDATE sales_opportunities SET
                status = 'objection', objection_text = ?, objection_category = ?,
                updated_at = ?
            WHERE id = ?
        ''', (
            objection_text,
            category,
            datetime.utcnow(),
            opportunity_id
        ))

        conn.commit()

        # Generate and send response
        response_result = self.generate_objection_response(category, objection_text, cursor, opportunity_id, objection_id)

        conn.close()

        logger.info(f"Handled objection {objection_id}: {category}")

        return {
            'objection_id': objection_id,
            'category': category,
            'response_type': response_result['response_type'],
            'response': response_result['response']
        }

    def categorize_objection(self, objection_text):
        """Categorize objection into standard types"""
        text_lower = objection_text.lower()

        if any(x in text_lower for x in ['price', 'cost', 'expensive', 'too much', 'budget']):
            return 'price'
        elif any(x in text_lower for x in ['competitor', 'already using', 'have another', 'someone else']):
            return 'competitor'
        elif any(x in text_lower for x in ['later', 'next quarter', 'next month', 'next year', 'timing']):
            return 'timing'
        elif any(x in text_lower for x in ["don't need", "not sure we need", 'not applicable']):
            return 'need'
        else:
            return 'other'

    def generate_objection_response(self, category, objection_text, cursor, opportunity_id, objection_id):
        """Generate rule-based response to objection"""

        responses = {
            'price': {
                'response_type': 'discount',
                'response': """I understand cost is a consideration. Here are a few options:

1. **Start Small:** Begin with a pilot project at a lower price point ($5K instead of $15K) to prove ROI
2. **Flexible Terms:** Spread payments over 3 months instead of upfront
3. **Performance-Based:** If you're concerned about ROI, we can work on a success-based pricing model

What matters most is that you get the ROI. Let's find the right investment level for you.

Can we schedule a 20-min call this week to explore options?"""
            },
            'competitor': {
                'response_type': 'differentiation',
                'response': """Great question. Many companies evaluate multiple solutions. Here's what makes SINCOR different:

1. **Autonomous Execution:** Our AI agents work 24/7 without manual intervention
2. **Speed:** Results in days instead of weeks
3. **Integration:** Seamlessly works with your existing systems
4. **Support:** Dedicated team that optimizes continuously

The real difference is in the implementation and support quality. Would you be open to a 30-min demo showing how we'd approach your specific use case? No pressure, just a chance to see the difference firsthand.

Interested?"""
            },
            'timing': {
                'response_type': 'follow_up',
                'response': """Absolutely, timing matters. Rather than lose the opportunity, let's stay in contact.

Here's what I suggest:
1. **Lock It In:** I'll book a tentative 30-min call for [DATE] to discuss when you're ready
2. **Keep It Warm:** I'll send you case studies and insights monthly so you're prepared when budget becomes available
3. **Plan Ahead:** If Q2/Q3 is your target, let's do a discovery call now to be ready to move fast

Which approach works best for you?"""
            },
            'need': {
                'response_type': 'consultation',
                'response': """I hear you. Let me ask a different way:

1. **Current State:** How are you currently handling [the process]?
2. **Pain Points:** What's costing you the most time/money today?
3. **Future:** What would success look like in 12 months?

Often the need becomes clear once we look at the cost of the status quo. I'd like to have a 20-min conversation to understand your situation better - no pitch, just questions and insights.

Available this week?"""
            },
            'other': {
                'response_type': 'follow_up',
                'response': """Thanks for raising that. I want to make sure we address your specific concern properly.

Can we schedule a quick 20-min call to discuss this in detail? That way I can give you the most accurate answer for your situation.

When works best for you?"""
            }
        }

        response_obj = responses.get(category, responses['other'])

        # Update objection with response
        cursor.execute('''
            UPDATE objections SET
                response_type = ?, response_text = ?, sent_at = ?
            WHERE id = ?
        ''', (
            response_obj['response_type'],
            response_obj['response'],
            datetime.utcnow(),
            objection_id
        ))

        return response_obj

    def track_agent_commission(self, agent_name, lead_id, sales_opportunity_id=None, order_id=None,
                               touchpoint_type='outreach', commission_amount=0, commission_percent=0):
        """Track agent contribution for commission payment"""
        commission_id = str(uuid.uuid4())

        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO agent_commissions (
                id, agent_name, lead_id, sales_opportunity_id, order_id,
                touchpoint_type, commission_amount, commission_percent, earned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            commission_id,
            agent_name,
            lead_id,
            sales_opportunity_id,
            order_id,
            touchpoint_type,
            commission_amount,
            commission_percent,
            datetime.utcnow()
        ))

        conn.commit()
        conn.close()

        logger.info(f"Tracked commission: {agent_name} earns ${commission_amount} for {touchpoint_type}")

        return commission_id

    def close_deal(self, opportunity_id, order_id, deal_amount, closing_agent=None):
        """Mark deal as closed and process final commission"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        # Update opportunity to closed_won
        cursor.execute('''
            UPDATE sales_opportunities SET
                status = 'closed_won', order_id = ?, final_amount = ?,
                closing_agent = ?, closed_at = ?, updated_at = ?
            WHERE id = ?
        ''', (
            order_id,
            int(deal_amount * 100),  # Convert to cents
            closing_agent or 'system',
            datetime.utcnow(),
            datetime.utcnow(),
            opportunity_id
        ))

        # Get lead_id for commission
        cursor.execute('SELECT lead_id FROM sales_opportunities WHERE id = ?', (opportunity_id,))
        lead_id = cursor.fetchone()[0]

        conn.commit()
        conn.close()

        # Pay closing agent commission (10% of deal if closing_agent provided)
        if closing_agent:
            commission = deal_amount * 0.10
            self.track_agent_commission(
                agent_name=closing_agent,
                lead_id=lead_id,
                sales_opportunity_id=opportunity_id,
                order_id=order_id,
                touchpoint_type='closing',
                commission_amount=commission,
                commission_percent=10.0
            )

        logger.info(f"Deal closed: {order_id} for ${deal_amount}")

        return {
            'opportunity_id': opportunity_id,
            'order_id': order_id,
            'status': 'closed_won',
            'deal_amount': deal_amount
        }

    def get_opportunity_status(self, opportunity_id):
        """Get current status of a sales opportunity"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM sales_opportunities WHERE id = ?', (opportunity_id,))
        result = cursor.fetchone()
        conn.close()

        if not result:
            return None

        return {
            'opportunity_id': result[0],
            'lead_id': result[1],
            'status': result[2],
            'qualification_stage': result[7],
            'sentiment_score': result[6],
            'proposal_sent': result[9] is not None,
            'objection_category': result[12],
            'closed': result[2] == 'closed_won'
        }

    def get_agent_commissions(self, agent_name=None):
        """Get earned commissions for an agent"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        if agent_name:
            cursor.execute('''
                SELECT id, agent_name, commission_amount, touchpoint_type, earned_at, paid
                FROM agent_commissions
                WHERE agent_name = ?
                ORDER BY earned_at DESC
            ''', (agent_name,))
        else:
            cursor.execute('''
                SELECT id, agent_name, commission_amount, touchpoint_type, earned_at, paid
                FROM agent_commissions
                ORDER BY earned_at DESC
            ''')

        results = cursor.fetchall()
        conn.close()

        total_earned = sum(r[2] for r in results if not r[5])  # Sum unpaid commissions
        total_paid = sum(r[2] for r in results if r[5])

        return {
            'commissions': results,
            'total_earned': total_earned,
            'total_paid': total_paid,
            'agent_name': agent_name
        }
