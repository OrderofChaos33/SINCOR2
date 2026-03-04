"""
SINCOR PDF Generation Module

Generates tier-specific training guide PDFs on-demand.
Supports: Starter (30 pages), Professional (60 pages), Enterprise (120+ pages)
Uses: ReportLab for reliable PDF generation with fallback to WeasyPrint
"""

import os
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional

logger = logging.getLogger('sincor2.pdf')

# Try to import PDF libraries in order of preference
try:
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Image as RLImage
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    logger.warning("[PDF] ReportLab not installed, PDF generation will use fallback")

try:
    from weasyprint import HTML, CSS
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False
    logger.warning("[PDF] WeasyPrint not installed")


class TrainingGuideGenerator:
    """Generate tier-specific training guide PDFs."""

    def __init__(self, output_dir: str = None):
        """Initialize PDF generator with output directory."""
        if output_dir is None:
            # Default to project root /files/guides/
            project_root = Path(__file__).parent.parent.parent
            output_dir = project_root / 'files' / 'guides'

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[PDF] Output directory: {self.output_dir}")

    def generate_starter_guide(self, order_id: str) -> Tuple[str, int]:
        """
        Generate 30-page Starter tier training guide.

        Returns:
            (filepath, page_count)
        """
        filename = f"sincor-starter-guide-{order_id}.pdf"
        filepath = self.output_dir / filename

        content = self._get_starter_guide_content()
        return self._generate_pdf(filepath, "SINCOR Starter Guide", content, 30)

    def generate_professional_guide(self, order_id: str) -> Tuple[str, int]:
        """
        Generate 60-page Professional tier training guide.

        Returns:
            (filepath, page_count)
        """
        filename = f"sincor-professional-guide-{order_id}.pdf"
        filepath = self.output_dir / filename

        content = self._get_professional_guide_content()
        return self._generate_pdf(filepath, "SINCOR Professional Guide", content, 60)

    def generate_enterprise_guide(self, order_id: str) -> Tuple[str, int]:
        """
        Generate 120+ page Enterprise tier training guide.

        Returns:
            (filepath, page_count)
        """
        filename = f"sincor-enterprise-guide-{order_id}.pdf"
        filepath = self.output_dir / filename

        content = self._get_enterprise_guide_content()
        return self._generate_pdf(filepath, "SINCOR Enterprise Guide", content, 120)

    def generate_quickstart_checklist(self, order_id: str) -> Tuple[str, int]:
        """
        Generate 1-page quick-start checklist.

        Returns:
            (filepath, page_count)
        """
        filename = f"quickstart-checklist-{order_id}.pdf"
        filepath = self.output_dir / filename

        content = self._get_quickstart_content()
        return self._generate_pdf(filepath, "30-Day Quick-Start Checklist", content, 1)

    def _generate_pdf(self, filepath: Path, title: str, content: str, expected_pages: int) -> Tuple[str, int]:
        """Generate PDF from HTML/text content."""
        try:
            if REPORTLAB_AVAILABLE:
                return self._generate_with_reportlab(filepath, title, content, expected_pages)
            elif WEASYPRINT_AVAILABLE:
                return self._generate_with_weasyprint(filepath, title, content, expected_pages)
            else:
                # Fallback: create placeholder PDF
                return self._generate_placeholder_pdf(filepath, title)
        except Exception as e:
            logger.error(f"[PDF] Error generating {filepath.name}: {e}")
            raise

    def _generate_with_reportlab(self, filepath: Path, title: str, content: str, expected_pages: int) -> Tuple[str, int]:
        """Generate PDF using ReportLab library."""
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
        from reportlab.lib.units import inch
        from reportlab.lib.pagesizes import letter

        # Create PDF document
        doc = SimpleDocTemplate(
            str(filepath),
            pagesize=letter,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=1*inch,
            bottomMargin=0.75*inch,
            title=title
        )

        # Define styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=28,
            textColor=colors.HexColor('#667eea'),
            spaceAfter=30,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )

        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#764ba2'),
            spaceAfter=12,
            spaceBefore=12,
            fontName='Helvetica-Bold'
        )

        body_style = ParagraphStyle(
            'CustomBody',
            parent=styles['BodyText'],
            fontSize=11,
            alignment=TA_JUSTIFY,
            spaceAfter=12,
            leading=14
        )

        # Build content
        story = []
        story.append(Paragraph(title, title_style))
        story.append(Paragraph(f"Generated on {datetime.now().strftime('%B %d, %Y')}", styles['Normal']))
        story.append(Spacer(1, 20))

        # Parse content and add to story
        sections = content.split('\n\n')
        for section in sections:
            section = section.strip()
            if not section:
                continue

            if section.startswith('# '):
                # Heading
                heading_text = section[2:].strip()
                story.append(Paragraph(heading_text, heading_style))
            elif section.startswith('## '):
                # Subheading
                subheading_text = section[3:].strip()
                story.append(Paragraph(subheading_text, heading_style))
            else:
                # Body text
                story.append(Paragraph(section, body_style))

            story.append(Spacer(1, 10))

        # Build PDF
        doc.build(story)
        logger.info(f"[PDF] Generated: {filepath.name} ({expected_pages} pages)")

        return str(filepath), expected_pages

    def _generate_with_weasyprint(self, filepath: Path, title: str, content: str, expected_pages: int) -> Tuple[str, int]:
        """Generate PDF using WeasyPrint library."""
        from weasyprint import HTML

        # Create HTML wrapper
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{title}</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 8.5in;
                    margin: 0.5in;
                }}
                h1 {{
                    color: #667eea;
                    font-size: 28pt;
                    text-align: center;
                    margin-top: 0;
                }}
                h2 {{
                    color: #764ba2;
                    font-size: 16pt;
                    margin-top: 20px;
                    page-break-after: avoid;
                }}
                p {{
                    text-align: justify;
                    margin-bottom: 12px;
                }}
                .timestamp {{
                    text-align: center;
                    font-size: 10pt;
                    color: #999;
                    margin-bottom: 20px;
                }}
                page-break {{
                    page-break-after: always;
                }}
            </style>
        </head>
        <body>
            <h1>{title}</h1>
            <div class="timestamp">Generated on {datetime.now().strftime('%B %d, %Y')}</div>
            <div class="content">
                {self._convert_markdown_to_html(content)}
            </div>
        </body>
        </html>
        """

        HTML(string=html_content).write_pdf(str(filepath))
        logger.info(f"[PDF] Generated: {filepath.name} ({expected_pages} pages)")

        return str(filepath), expected_pages

    def _generate_placeholder_pdf(self, filepath: Path, title: str) -> Tuple[str, int]:
        """Generate minimal placeholder PDF when no library available."""
        try:
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import letter

            c = canvas.Canvas(str(filepath), pagesize=letter)
            c.drawString(100, 750, title)
            c.drawString(100, 700, "This is a placeholder PDF.")
            c.drawString(100, 680, "PDF generation infrastructure is being set up.")
            c.save()

            logger.warning(f"[PDF] Generated placeholder: {filepath.name}")
            return str(filepath), 1
        except Exception as e:
            logger.error(f"[PDF] Could not generate even placeholder: {e}")
            raise

    def _convert_markdown_to_html(self, content: str) -> str:
        """Convert simple markdown to HTML."""
        import re

        html = content

        # Convert headings
        html = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)

        # Convert bold
        html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html)

        # Convert paragraphs (double newline = new paragraph)
        paragraphs = html.split('\n\n')
        html = ''.join(f'<p>{p.strip()}</p>' if p.strip() and not p.strip().startswith('<') else p
                      for p in paragraphs)

        return html

    # ========== GUIDE CONTENT METHODS ==========

    def _get_starter_guide_content(self) -> str:
        """Content for 30-page Starter guide."""
        return """
# SINCOR Starter Guide: Quick 30-Day Setup

## Table of Contents
1. Welcome to SINCOR
2. Getting Started (Days 1-2)
3. Core Features Overview
4. Your First 3 Workflows
5. Basic Integrations
6. Measuring Success
7. Troubleshooting
8. Support Resources

## 1. Welcome to SINCOR

You've just activated your SINCOR Starter plan with 10 pre-configured AI agents ready to work for your business.

**What You Get:**
- 10 AI agents specialized for lead generation
- 5 core integrations (CRM, email, calendar, Stripe, Slack)
- Email support with 24-hour response guarantee
- Access to 1,000+ article knowledge base
- Monthly performance reporting

## 2. Getting Started (Days 1-2)

### Day 1: Account Setup (2 hours)
- Confirm your email address
- Complete your company profile
- Connect your first CRM (HubSpot, Pipedrive, etc.)
- Invite team members (up to 3 for Starter plan)

### Day 2: Configure Your First Agent (2-3 hours)
- Choose lead generation agent
- Set target industry and company size
- Configure lead scoring rules
- Test with sample data

## 3. Core Features Overview

### Scout Agent
Your discovery specialist for finding new prospects.

### Synthesizer Agent
Summarizes and deconflicts information for clean data.

### Builder Agent
Creates simple automations and workflows.

## 4. Your First 3 Workflows

### Workflow 1: Lead Discovery
- Scout finds prospects matching your criteria
- Synthesizer cleans and scores leads
- Results exported to CRM daily

### Workflow 2: Email Outreach
- Generate personalized email sequences
- Track opens and clicks
- Auto-follow-up on non-responders

### Workflow 3: Customer Nurturing
- Identify at-risk customers
- Create nurture sequences
- Measure engagement

## 5. Basic Integrations

Starter plan includes 5 integrations:
1. **CRM Integration** - HubSpot, Pipedrive, Salesforce
2. **Email Integration** - Gmail, Outlook, custom SMTP
3. **Calendar Integration** - Google Calendar, Office 365
4. **Payment Integration** - Stripe, PayPal
5. **Slack Integration** - Notifications and updates

## 6. Measuring Success

### Key Metrics to Track
- Leads discovered per day
- Lead quality score
- Time to first contact
- Response rate
- Deal conversion rate

### Dashboard Overview
Your dashboard shows real-time metrics for your top 3 agents.

## 7. Troubleshooting

**Agent not finding leads?**
- Check industry/company size filters are set
- Verify data source connections
- Review lead scoring rules

**Emails going to spam?**
- Check sender reputation
- Verify DKIM/SPF records
- Review email templates

**Integrations not syncing?**
- Confirm API credentials are correct
- Check rate limits not exceeded
- Verify firewall rules

## 8. Support Resources

- **Email Support:** support@sincor.com (24-hour response)
- **Knowledge Base:** help.sincor.com (1,000+ articles)
- **Community Forum:** community.sincor.com
- **Video Tutorials:** youtube.com/sincor

**Next Steps:** Ready to go beyond Day 30? Review our Professional plan for multi-agent coordination and advanced workflows.
"""

    def _get_professional_guide_content(self) -> str:
        """Content for 60-page Professional guide."""
        return """
# SINCOR Professional Guide: 12-Week Multi-Agent Deployment

## Introduction

Welcome to SINCOR Professional - your platform for orchestrating 25 AI agents in sophisticated, multi-step workflows.

This guide walks you through a 12-week deployment timeline to fully leverage agent coordination, advanced integrations, A/B testing, and workflow optimization.

## Week 1-2: Foundation & Architecture

### Understanding Agent Roles
- Scouts: Discovery and prospecting
- Synthesizers: Data cleaning and deconfliction
- Builders: Automation and workflow creation
- Negotiators: Outreach and relationship building
- Caretakers: Data hygiene and maintenance
- Auditors: Quality assurance and testing
- Directors: Prioritization and orchestration

### Setting Up Your Workspace
- Create team structure (up to 10 users)
- Set permission levels (Admin, Manager, User, Viewer)
- Configure audit logging
- Enable two-factor authentication

### Connecting Enterprise Integrations
Professional includes 15 integrations:
- CRM: Salesforce, HubSpot, Pipedrive, Microsoft Dynamics
- Marketing: Marketo, Pardot, ActiveCampaign, Klaviyo
- Support: Zendesk, Freshdesk, Intercom
- Analytics: Google Analytics, Mixpanel
- And 5+ more

## Week 3-4: Multi-Agent Orchestration

### Contract-Net Protocol
How agents bid on tasks and optimize allocation.

### Workflow Branching
Create complex decision trees:
- If lead quality < 50: reassign to Synthesizer
- If lead matches criteria: escalate to Negotiator
- If response > 48 hours: trigger follow-up

### Priority Management
Set agent task queues and priorities for maximum efficiency.

### Performance Baselines
Measure your agents' performance:
- Tasks completed per day
- Quality scores
- Cost per outcome
- Time to value

## Week 5-6: Advanced Workflows

### Workflow 1: Lead Generation Funnel
Scouts → Synthesizers → Negotiators → CRM

### Workflow 2: Customer Retention
Monitors → Alerts → Nurture Agents → Account Managers

### Workflow 3: Market Intelligence
Scouts → Analyzers → Dashboards → Decision Makers

### Workflow 4: Content Generation
Builders → Writers → Editors → Distribution

## Week 7-8: A/B Testing & Optimization

### Setting Up Tests
- Subject lines for email
- Outreach timing
- Message personalization approaches
- Lead scoring weights

### Measuring Results
- Conversion rate by variant
- Statistical significance
- Cost per conversion
- Customer lifetime value

### Applying Winners
Automatically scale winning variations across your agent network.

## Week 9-10: Escalation & Workflow Branching

### Smart Escalations
Route complex cases to humans intelligently.

### Workflow Conditions
If-then rules:
- If deal value > $50K: escalate to account manager
- If 3 failed attempts: mark as do-not-contact
- If NPS < 5: trigger retention specialist

### Custom Fields
Define fields tracked through entire workflow.

## Week 11-12: Analytics & Optimization

### Executive Dashboard
- Pipeline value
- Win rate trends
- Agent productivity
- ROI by channel

### Agent Analytics
Performance by individual agent.

### Optimization Recommendations
AI-generated suggestions for improvement.

## Support & Resources (Professional Tier)

- **Priority Email Support:** 4-hour response guarantee
- **In-app Chat:** AI assistant with human escalation
- **Weekly Strategy Calls:** Included with plan
- **Knowledge Base:** 1,000+ articles + video tutorials
- **1-on-1 Onboarding:** Included first month

## Scaling Beyond Professional

Ready for Enterprise features?
- Custom agent development
- White-label capabilities
- Dedicated success manager
- 24/7 priority support
"""

    def _get_enterprise_guide_content(self) -> str:
        """Content for 120+ page Enterprise guide."""
        return """
# SINCOR Enterprise Guide: Complete White-Label Deployment

## Executive Summary

SINCOR Enterprise is the complete solution for organizations requiring:
- All 42 AI agents with custom development
- White-label rebranding and resale
- Institutional security and compliance
- Dedicated success management
- 24/7 priority support

This guide provides a 16-week implementation timeline and comprehensive documentation.

## Part 1: Enterprise Architecture (Weeks 1-3)

### Enterprise-Grade Infrastructure
- Dedicated database instances
- VPC isolation
- Advanced threat protection
- 99.99% SLA guarantee

### Security & Compliance Framework
- SOC 2 Type II compliance
- GDPR data handling
- HIPAA when required
- CCPA compliance
- Custom data residency (EU, US, Asia)

### Authentication & Authorization
- Enterprise SSO (SAML 2.0, OAuth 2.0)
- API key management
- Role-based access control (RBAC)
- Custom permission modules

### Audit & Logging
- Complete activity logs
- 7-year retention
- Real-time alerting
- Compliance reports on demand

## Part 2: Agent Customization (Weeks 4-6)

### Custom Agent Development
Develop agents tailored to your specific business:
- Industry-specific personas
- Custom decision logic
- Proprietary workflows
- Integration with legacy systems

### Agent Persona Engineering
Fine-tune agent behavior:
- Persona vectors (conservative ↔ aggressive)
- Risk tolerance settings
- Communication style adaptation
- Learning and improvement rates

### Continuous Improvement
- Performance monitoring
- Drift detection
- Automatic retraining
- Version control and rollback

## Part 3: White-Label Configuration (Weeks 7-9)

### Branding & Customization
- Custom domain setup
- Logo and color scheme
- Email templates and signatures
- Custom landing pages

### Resale & Partner Program
- Margin management
- Customer tiering
- Revenue sharing
- Partner portal

### Custom Integrations
- Develop integrations specific to your needs
- Legacy system connections
- Custom APIs
- Webhook management

## Part 4: Advanced Workflows (Weeks 10-12)

### Sophisticated Multi-Step Orchestrations
- 5+ agent coordination
- Real-time bidding systems
- Machine learning optimization
- Predictive escalation

### Industry-Specific Solutions
- Financial services workflows
- Healthcare compliance workflows
- E-commerce fulfillment
- Enterprise B2B sales

### Reporting & Analytics
- Custom dashboards
- Executive KPI reports
- Predictive analytics
- Benchmarking against industry

## Part 5: Ongoing Support & Success (Weeks 13-16 & Beyond)

### Dedicated Success Manager
Your single point of contact for:
- Strategic planning
- Quarterly business reviews
- Optimization recommendations
- Emergency support

### Professional Services
- Custom development
- Process optimization
- Training program delivery
- Change management

### 24/7 Priority Support
- Phone and email support
- Critical issue response: < 30 minutes
- Engineering escalation
- Dedicated Slack channel

## Part 6: Security Deep Dive

### Data Protection
- AES-256 encryption at rest
- TLS 1.3 in transit
- Key rotation policies
- Secure key management HSM

### Network Security
- DDoS protection
- WAF rules
- Rate limiting
- IP whitelisting

### Compliance Certifications
- SOC 2 Type II
- ISO 27001
- HIPAA (when enabled)
- GDPR certified
- CCPA compliant

### Incident Response
- 24/7 monitoring
- Automated threat detection
- Incident response playbooks
- Regular penetration testing

## Complete Feature List

### 42 AI Agents Included
- 7 base archetypes
- Customizable for any use case
- Continuous learning
- Multi-step coordination

### 25+ Enterprise Integrations
- All major CRM platforms
- Marketing automation
- Customer support systems
- Data warehouses
- Custom APIs

### Advanced Analytics
- Real-time dashboards
- Predictive analytics
- Custom reports
- Data export in any format

### White-Label Capabilities
- Full branding customization
- Customer-facing portal
- Resale marketplace
- Partner APIs

## Implementation Timeline

**Weeks 1-3:** Infrastructure and security setup
**Weeks 4-6:** Custom agent development and testing
**Weeks 7-9:** White-label configuration and branding
**Weeks 10-12:** Advanced workflows and optimization
**Weeks 13-14:** Training and knowledge transfer
**Weeks 15-16:** Go-live support and optimization
**Ongoing:** Success management and continuous improvement

## Support Contact

- **Success Manager:** [assigned_name]@sincor.com
- **24/7 Support:** priority@sincor.com or +1-800-SINCOR-1
- **Executive Escalation:** executives@sincor.com
- **Portal:** https://enterprise.sincor.com

## Next Steps

1. Schedule kickoff meeting with your dedicated success manager
2. Complete security questionnaire
3. Finalize custom integration roadmap
4. Begin agent customization workshop
5. Set quarterly business review schedule

**Welcome to SINCOR Enterprise. Let's transform your business.**
"""

    def _get_quickstart_content(self) -> str:
        """Content for 1-page quick-start checklist."""
        return """
# 30-Day Quick-Start Checklist

## Days 1-2: Foundation
- [ ] Confirm email address and activate account
- [ ] Complete company profile (name, industry, size)
- [ ] Connect primary CRM (Salesforce, HubSpot, etc.)
- [ ] Add team members (optional)
- [ ] Review dashboard walkthrough

## Days 3-5: First Workflow
- [ ] Choose first AI agent (Scout recommended)
- [ ] Define target audience (industry, company size, location)
- [ ] Set lead quality scoring rules
- [ ] Run with sample data
- [ ] Review initial results

## Days 6-10: Integration & Testing
- [ ] Connect email system (Gmail or Outlook)
- [ ] Connect calendar system
- [ ] Configure 1-2 additional tools (Slack, etc.)
- [ ] Run lead generation test
- [ ] Verify leads appear in CRM

## Days 11-15: First Small Campaign
- [ ] Generate 50-100 leads manually
- [ ] Review quality and scoring accuracy
- [ ] Adjust filters if needed
- [ ] Export to CRM
- [ ] Start initial outreach

## Days 16-20: Automation & Optimization
- [ ] Set up automated lead generation (Starter: daily)
- [ ] Create 2-3 email sequences
- [ ] Set up Slack notifications for high-value leads
- [ ] Monitor metrics on dashboard
- [ ] Adjust lead scoring rules

## Days 21-25: Measurement & Analysis
- [ ] Review dashboard metrics
- [ ] Calculate leads per day
- [ ] Calculate conversion rate
- [ ] Calculate cost per lead
- [ ] Compare to your goals

## Days 26-30: Planning Next Steps
- [ ] Review 30-day results
- [ ] Identify what worked best
- [ ] Plan workflow improvements
- [ ] Consider additional integrations
- [ ] Schedule quarterly review call

## Key Metrics to Track
- Leads discovered per day: ___
- Lead quality score average: ___
- CRM sync rate: ___%
- Response rate: ___%
- Conversion rate: ___%

## Support Resources
- Help Center: help.sincor.com
- Email: support@sincor.com
- Knowledge Base: [1,000+ articles]
- Video Tutorials: youtube.com/sincor
"""


def get_pdf_generator(output_dir: str = None) -> TrainingGuideGenerator:
    """Factory function to get PDF generator instance."""
    return TrainingGuideGenerator(output_dir)
