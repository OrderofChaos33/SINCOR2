"""
SINCOR Autonomous Fulfillment Engine

Called on every successful payment. Spins up the agent swarm,
generates real deliverables using Claude, and delivers them to
the customer — zero human intervention required.

Flow:
  1. Payment webhook fires -> trigger_autonomous_fulfillment()
  2. Engine detects customer's industry from email domain
  3. Instantiates CortecsBrain + TaskMarket
  4. Runs InstantBusinessIntelligence to generate a real BI report
  5. Emails the report + dashboard access link to customer
  6. Logs everything to orders DB
"""

import os
import json
import logging
import asyncio
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger('sincor.fulfillment')

# Industry detection from email domain
DOMAIN_INDUSTRY_MAP = {
    # Tech
    'gmail.com': 'Small Business / Entrepreneurship',
    'yahoo.com': 'Small Business / Entrepreneurship',
    'outlook.com': 'Small Business / Entrepreneurship',
    'hotmail.com': 'Small Business / Entrepreneurship',
    'icloud.com': 'Small Business / Entrepreneurship',
    # Common business TLDs get generic
}

INDUSTRY_KEYWORDS = {
    'law': 'Legal Services',
    'legal': 'Legal Services',
    'med': 'Healthcare',
    'health': 'Healthcare',
    'dental': 'Healthcare / Dental',
    'clinic': 'Healthcare',
    'realty': 'Real Estate',
    'homes': 'Real Estate',
    'properties': 'Real Estate',
    'auto': 'Automotive',
    'cars': 'Automotive',
    'tech': 'Technology / SaaS',
    'software': 'Technology / SaaS',
    'digital': 'Digital Marketing',
    'market': 'Marketing',
    'agency': 'Marketing Agency',
    'consult': 'Consulting',
    'finance': 'Financial Services',
    'capital': 'Financial Services',
    'invest': 'Financial Services',
    'retail': 'Retail / E-Commerce',
    'shop': 'Retail / E-Commerce',
    'store': 'Retail / E-Commerce',
    'food': 'Food & Beverage',
    'restaurant': 'Food & Beverage',
    'fit': 'Health & Fitness',
    'gym': 'Health & Fitness',
    'coach': 'Coaching / Training',
    'edu': 'Education',
    'school': 'Education',
    'media': 'Media & Entertainment',
    'studio': 'Creative / Design',
    'design': 'Creative / Design',
    'build': 'Construction',
    'construct': 'Construction',
    'logistics': 'Logistics / Supply Chain',
    'ship': 'Logistics / Supply Chain',
}


def detect_industry(email: str) -> str:
    """Detect customer's likely industry from their email domain."""
    try:
        domain = email.split('@')[1].lower() if '@' in email else ''
        # Check exact domain map first
        if domain in DOMAIN_INDUSTRY_MAP:
            return DOMAIN_INDUSTRY_MAP[domain]
        # Check domain for industry keywords
        domain_base = domain.split('.')[0]
        for keyword, industry in INDUSTRY_KEYWORDS.items():
            if keyword in domain_base:
                return industry
        return 'Business Services'
    except Exception:
        return 'Business Services'


def get_plan_config(plan_id: str) -> dict:
    """
    Get configuration for each product tier.

    SINCOR Products:
    - starter ($49 one-time): Competitive Intelligence Report
      - What: Full competitive analysis PDF delivered within 24 hours
      - Includes: Pricing comparison, review analysis, SEO snapshot, marketing activity,
                  3 specific market gaps, action plan
      - No ongoing subscription

    - professional ($149/month): Monthly Intelligence Subscription
      - What: Fresh competitive report delivered every month
      - Includes: Everything in starter + month-over-month change tracking,
                  new competitor alerts, pricing trend analysis, priority support
      - Recurring monthly delivery
    """
    configs = {
        'starter': {
            'product_name': 'Competitive Intelligence Report',
            'agent_count': 1,
            'product_type': 'one_time_report',
            'report_depth': 'competitive_intelligence',
            'deliverable': 'PDF competitive analysis report',
            'delivery_time': '24 hours',
            'sections': [
                'Executive Summary & Top 3 Opportunities',
                'Competitor Pricing Comparison',
                'Review & Reputation Analysis',
                'Online Presence & SEO Snapshot',
                'Marketing & Ad Activity',
                'Market Gaps You Can Win',
                '30-Day Action Plan',
            ],
        },
        'professional': {
            'product_name': 'Monthly Competitive Intelligence',
            'agent_count': 1,
            'product_type': 'monthly_subscription',
            'report_depth': 'competitive_intelligence_monthly',
            'deliverable': 'Monthly PDF competitive analysis report',
            'delivery_time': '24 hours on purchase, monthly thereafter',
            'sections': [
                'Executive Summary & Top 3 Opportunities',
                'Competitor Pricing Comparison',
                'Review & Reputation Analysis',
                'Online Presence & SEO Snapshot',
                'Marketing & Ad Activity',
                'Month-over-Month Competitor Changes',
                'New Market Entrant Alerts',
                'Market Gaps You Can Win',
                '30-Day Action Plan',
            ],
        },
        'enterprise': {
            'product_name': 'Enterprise Competitive Intelligence',
            'agent_count': 1,
            'product_type': 'enterprise',
            'report_depth': 'enterprise_competitive_intelligence',
            'deliverable': 'Custom enterprise competitive analysis',
            'delivery_time': '48 hours',
            'sections': [
                'Full Market Landscape',
                'Competitor Deep Dives (top 10)',
                'Pricing Intelligence',
                'Marketing & Brand Analysis',
                'Digital Presence Audit',
                'Customer Sentiment Analysis',
                'Strategic Opportunities',
                'Custom Action Plan',
            ],
        },
    }
    return configs.get(plan_id, configs['starter'])


async def _generate_bi_report_async(customer_email: str, plan_id: str, order_id: str) -> dict:
    """
    Use CortecsBrain to generate a real business intelligence report for the customer.
    Returns dict with report content.
    """
    from sincor2.cortecs_core import CortecsBrain, ClaudeClient
    from sincor2.swarm_coordination import TaskMarket

    industry = detect_industry(customer_email)
    config = get_plan_config(plan_id)
    agent_count = config.get('agent_count', 1)
    agents = config.get('agents', ['Competitive Intelligence Engine'])

    logger.info(f"[FULFILL] Generating BI report for {customer_email} | industry={industry} | plan={plan_id}")

    # Init the brain — CortecsBrain takes api_key directly
    api_key = os.getenv('ANTHROPIC_API_KEY')
    brain = CortecsBrain(api_key=api_key)
    claude = brain.claude  # ClaudeClient is brain.claude
    task_market = TaskMarket()

    # Build the competitive intelligence report prompt
    sections_str = '\n'.join(f'{i+1}. {s}' for i, s in enumerate(config.get('sections', [])))

    system_prompt = """You are a competitive intelligence analyst. Generate professional, specific, and actionable competitive intelligence reports. Use concrete data, real patterns, and industry-specific insights. Format with clear markdown sections."""

    report_prompt = f"""Generate a Competitive Intelligence Report for a {industry} business that just purchased SINCOR.

CLIENT: {customer_email}
INDUSTRY: {industry}
REPORT DATE: {datetime.now().strftime('%B %d, %Y')}
PRODUCT: {config.get('product_name', 'Competitive Intelligence Report')}

Create a professional competitive intelligence report with these sections:
{sections_str}

REQUIREMENTS:
- Make it specific to the {industry} industry
- Competitor analysis should reflect realistic patterns in {industry} (pricing, marketing, reviews)
- Include specific, actionable recommendations — not generic advice
- Pricing section should show realistic price ranges for {industry} services
- Review analysis should identify common themes customers care about in {industry}
- Market gaps section should show real opportunities a {industry} business can act on TODAY
- Action plan should have 3 specific, concrete steps for the next 30 days

This report is the first thing the client receives. Make them immediately see the value.
Be direct, professional, and specific — no fluff."""

    try:
        report_content = await brain.claude.complete(
            prompt=report_prompt,
            max_tokens=3000,  # REDUCED: Haiku handles 3K tokens fine, cuts cost in half
            system=system_prompt,
            model='claude-3-5-haiku-20241022'  # CHEAPEST: Haiku instead of Sonnet
        )
        logger.info(f"[FULFILL] BI report generated successfully for {customer_email}")
        return {
            'success': True,
            'industry': industry,
            'agent_count': agent_count,
            'report': report_content,
            'generated_at': datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"[FULFILL] Report generation failed: {e}")
        return {
            'success': False,
            'error': str(e),
            'industry': industry,
            'agent_count': agent_count,
        }


def _send_fulfillment_email(customer_email: str, plan_id: str, order_id: str,
                             report: dict) -> bool:
    """Send the AI-generated report to the customer."""
    try:
        from sincor2.email_sender import get_email_sender
        sender = get_email_sender()
        if not sender:
            logger.error("[FULFILL] No email sender configured")
            return False

        industry = report.get('industry', 'Business Services')
        report_content = report.get('report', '')
        config = get_plan_config(plan_id)
        product_name = config.get('product_name', 'Competitive Intelligence Report')

        # Convert markdown to clean HTML
        import re as _re
        html_report = report_content
        # Headers
        html_report = _re.sub(r'^### (.+)$', r'<h4 style="color:#7c3aed;margin:20px 0 8px;">\1</h4>', html_report, flags=_re.MULTILINE)
        html_report = _re.sub(r'^## (.+)$', r'<h3 style="color:#1a1a2e;margin:24px 0 10px;border-bottom:2px solid #eee;padding-bottom:8px;">\1</h3>', html_report, flags=_re.MULTILINE)
        html_report = _re.sub(r'^# (.+)$', r'<h2 style="color:#1a1a2e;margin:28px 0 12px;">\1</h2>', html_report, flags=_re.MULTILINE)
        # Bold
        html_report = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html_report)
        # Lists
        html_report = _re.sub(r'^\- (.+)$', r'<li style="margin-bottom:6px;">\1</li>', html_report, flags=_re.MULTILINE)
        # Paragraphs
        html_report = html_report.replace('\n\n', '</p><p style="line-height:1.7;margin-bottom:12px;">')

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Helvetica Neue',Arial,sans-serif;max-width:680px;margin:0 auto;padding:20px;background:#f5f5f7;">

<div style="background:linear-gradient(135deg,#7c3aed,#2563eb);padding:28px 32px;border-radius:12px 12px 0 0;text-align:center;">
  <h1 style="color:white;margin:0;font-size:26px;font-weight:700;">SINCOR</h1>
  <p style="color:#e0cfff;margin:6px 0 0;font-size:15px;">Your Competitive Intelligence Report is Ready</p>
</div>

<div style="background:white;padding:32px;border-radius:0 0 12px 12px;box-shadow:0 4px 20px rgba(0,0,0,0.08);">

  <p style="font-size:17px;color:#1a1a2e;margin-top:0;"><strong>Here's your {product_name}.</strong></p>

  <div style="background:#f0f4ff;border-left:4px solid #7c3aed;padding:14px 18px;border-radius:6px;margin:20px 0;font-size:14px;">
    <strong>Industry:</strong> {industry} &nbsp;|&nbsp;
    <strong>Product:</strong> {product_name} &nbsp;|&nbsp;
    <strong>Order:</strong> {order_id}
  </div>

  <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">

  <div style="font-size:14px;line-height:1.7;color:#333;">
    <p style="line-height:1.7;margin-bottom:12px;">{html_report}</p>
  </div>

  <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">

  <div style="text-align:center;margin:28px 0;">
    <a href="https://getsincor.com/" style="background:#7c3aed;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;">
      View at getsincor.com →
    </a>
  </div>

  <p style="color:#999;font-size:12px;text-align:center;margin-top:24px;">
    Questions? Reply to this email or visit <a href="https://getsincor.com" style="color:#7c3aed;">getsincor.com</a><br>
    Order ID: {order_id}
  </p>
</div>
</body>
</html>"""

        result = sender.send_email(
            to_email=customer_email,
            to_name=customer_email.split('@')[0].title(),
            subject=f"📊 Your {product_name} — {industry} | SINCOR",
            html_content=html,
            text_content=f"Your {product_name} for {industry} is ready. View it at https://getsincor.com/ | Order: {order_id}",
        )
        logger.info(f"[FULFILL] Email sent to {customer_email}: {result}")
        return True

    except Exception as e:
        logger.error(f"[FULFILL] Email send failed: {e}")
        return False


def trigger_autonomous_fulfillment(customer_email: str, plan_id: str,
                                    plan_name: str, order_id: str,
                                    amount: float = 10.0) -> None:
    """
    Entry point called from webhook handler.
    Runs in a background thread so it doesn't block the HTTP response.
    """
    def _run():
        logger.info(f"[FULFILL] Starting autonomous fulfillment for {customer_email} | {plan_name}")
        try:
            # Run async report generation
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            report = loop.run_until_complete(
                _generate_bi_report_async(customer_email, plan_id, order_id)
            )
            loop.close()

            if report.get('success'):
                # Send report email
                email_sent = _send_fulfillment_email(
                    customer_email, plan_id, order_id, report
                )
                logger.info(f"[FULFILL] ✅ Complete for {customer_email} | email_sent={email_sent}")
            else:
                logger.error(f"[FULFILL] Report generation failed for {customer_email}: {report.get('error')}")
                # Still send a welcome email even if report fails
                _send_fallback_welcome(customer_email, plan_id, plan_name, order_id)

        except Exception as e:
            logger.error(f"[FULFILL] Fulfillment error for {customer_email}: {e}")
            _send_fallback_welcome(customer_email, plan_id, plan_name, order_id)

    # Run in background thread — don't block webhook response
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info(f"[FULFILL] Background fulfillment thread started for {customer_email}")


def _send_fallback_welcome(customer_email: str, plan_id: str, plan_name: str, order_id: str):
    """Fallback email if report generation fails."""
    try:
        from sincor2.email_sender import get_email_sender
        sender = get_email_sender()
        if not sender:
            return
        config = get_plan_config(plan_id)
        product_name = config.get('product_name', 'Competitive Intelligence Report')
        sender.send_email(
            to_email=customer_email,
            to_name=customer_email.split('@')[0].title(),
            subject=f"📊 Your {product_name} — we\'re on it | SINCOR",
            html_content=f"""<!DOCTYPE html>
<html>
<body style="font-family:'Helvetica Neue',Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#1a1a2e;">Your {product_name} is being prepared.</h2>
<p>Thanks for your purchase! We're building your competitive intelligence report right now.</p>
<p>You'll receive the full report in your inbox within <strong>24 hours</strong>.</p>
<p>Questions? Just reply to this email.</p>
<p>— Court at SINCOR</p>
<p style="color:#888;font-size:12px;">Order ID: {order_id}</p>
</body>
</html>""",
            text_content=f"Your {product_name} is being prepared. You'll receive it within 24 hours. Order: {order_id}",
        )
    except Exception as e:
        logger.error(f"[FULFILL] Fallback email also failed: {e}")
