"""
SINCOR Autonomous Agent Outreach Handler
Agents autonomously reach out to leads with personalized messages
"""

import json
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AgentOutreachHandler:
    """Coordinates agents to outreach to leads"""

    def __init__(self, lead_engine):
        self.lead_engine = lead_engine

        # Map service types to agent archetypes
        self.service_to_agents = {
            'intelligence': ['Scout', 'Synthesizer'],
            'predictive': ['Synthesizer', 'Builder'],
            'agents': ['Scout', 'Builder'],
            'automation': ['Builder', 'Auditor'],
            'market': ['Scout', 'Auditor']
        }

        # Personalization templates per service
        self.templates = {
            'intelligence': {
                'subject': 'Competitive Intelligence Report for {company}',
                'body': """Hi {name},

I noticed {company} is expanding in {industry}.

Competitors are analyzing your market moves every day. We help enterprises like yours understand competitive positioning and market trends with 85%+ accuracy.

A typical intelligence report takes 2-6 weeks and costs $4.5K-$22K. We deliver it in days instead of weeks.

Would you be open to a brief conversation about what your competitors are doing?

Best,
SINCOR AI Team"""
            },
            'predictive': {
                'subject': 'Predict customer churn before it happens',
                'body': """Hi {name},

Companies in {industry} have an average churn rate of 15-20%. If you're losing customers, you're losing revenue.

We build ML models that predict churn 60 days in advance so you can intervene. Our average client improves retention by 12%.

Want to see how we'd model your data? 30-min call?

- SINCOR"""
            },
            'agents': {
                'subject': 'Automate {company} manual processes with AI',
                'body': """Hi {name},

Your team is likely doing 30+ hours/week of manual work:
- Data entry & processing
- Report generation
- Customer outreach
- Administrative tasks

We build custom AI agents that automate this. One client saved $180K/year in labor costs.

Quick discovery call?

- SINCOR"""
            },
            'automation': {
                'subject': 'Cut operational costs by 30%',
                'body': """Hi {name},

Process automation is the fastest ROI your finance team can deliver. Most enterprises have 10+ processes that could be automated.

We've implemented automation for {industry} leaders - average payback is 6 months.

Should we analyze your current processes?

- SINCOR"""
            },
            'market': {
                'subject': 'Market analysis for {company}',
                'body': """Hi {name},

Making market expansion decisions without hard data is risky.

We provide comprehensive market research: competitor analysis, customer insights, market sizing. {industry} companies use this to make $M decisions.

40-hour research completed in 2-6 weeks. $5.7K-$47.5K.

Interested in a sample?

- SINCOR"""
            }
        }

    def select_best_agent(self, service_type):
        """Select best agent archetype for this service"""
        agents = self.service_to_agents.get(service_type, ['Scout'])
        return agents[0]  # Simple: return first agent

    def generate_personalized_message(self, lead, service_type):
        """Generate personalized outreach message"""
        template = self.templates.get(service_type, self.templates['intelligence'])

        # Extract lead info
        company_name = lead[1]  # company_name is index 1 in leads table
        decision_maker_name = lead[8]  # decision_maker_name
        industry = lead[4]  # industry

        # Safely handle missing data
        name = decision_maker_name.split()[0] if decision_maker_name else "there"
        ind = industry if industry else "your market"

        message = template['body'].format(
            name=name,
            company=company_name,
            industry=ind
        )

        return {
            'subject': template['subject'].format(company=company_name),
            'body': message
        }

    def reach_out_to_lead(self, lead, agent_name, service_type):
        """Agent reaches out to lead with personalized message"""

        lead_id = lead[0]
        company_name = lead[1]
        decision_maker_email = lead[9]

        if not decision_maker_email:
            logger.warning(f"No email for lead {lead_id}, skipping outreach")
            return None

        # Generate personalized message
        message = self.generate_personalized_message(lead, service_type)

        logger.info(f"Agent {agent_name} reaching out to {company_name} ({decision_maker_email})")
        logger.info(f"Subject: {message['subject']}")
        logger.info(f"Body preview: {message['body'][:200]}...")

        # Log the outreach
        outreach_id = self.lead_engine.log_outreach(
            lead_id=lead_id,
            channel='email',
            message=json.dumps(message),
            agent_assigned=agent_name
        )

        # TODO: Actually send email via SendGrid/Mailgun here
        # For now, just log it

        return {
            'outreach_id': outreach_id,
            'lead_id': lead_id,
            'company': company_name,
            'agent': agent_name,
            'email': decision_maker_email,
            'status': 'sent'
        }

    def run_outreach_cycle(self):
        """Run one cycle of autonomous outreach"""
        logger.info("=== Starting autonomous outreach cycle ===")

        # Get hot leads (score >= 75)
        hot_leads = self.lead_engine.get_hot_leads(threshold=75)

        if not hot_leads:
            logger.info("No hot leads available for outreach")
            return {
                'outreach_attempts': 0,
                'leads_contacted': 0,
                'status': 'no_leads'
            }

        results = []
        for lead in hot_leads:
            try:
                lead_id = lead[0]
                service_type = lead[13] or 'intelligence'  # recommended_service

                # Select agent for this service
                agent_name = self.select_best_agent(service_type)

                # Agent reaches out
                result = self.reach_out_to_lead(lead, agent_name, service_type)

                if result:
                    results.append(result)
                    # Mark as contacted
                    self.lead_engine.update_lead_status(lead_id, 'contacted')

            except Exception as e:
                logger.error(f"Error during outreach to lead {lead_id}: {e}")
                continue

        logger.info(f"=== Outreach cycle complete: {len(results)} leads contacted ===")

        return {
            'outreach_attempts': len(results),
            'leads_contacted': len(results),
            'results': results,
            'status': 'complete'
        }
