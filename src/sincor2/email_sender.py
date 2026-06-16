"""
SINCOR Email Delivery Module

Sends transactional emails (thank-you, confirmations) via SendGrid.
Includes template rendering and personalization.
"""

import os
import json
import logging
from typing import Dict, Optional, List
from datetime import datetime

logger = logging.getLogger('sincor2.email')

# Try to import Resend (or SendGrid as fallback)
try:
    import resend as resend_sdk
    RESEND_AVAILABLE = True
except ImportError:
    resend_sdk = None
    RESEND_AVAILABLE = False

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, Content, Personalization, Attachment
    SENDGRID_AVAILABLE = True
except ImportError:
    SENDGRID_AVAILABLE = False

# Try to import Twilio for SMS fallback
try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False


class EmailSender:
    """Send transactional emails via SendGrid."""

    def __init__(self, resend_api_key: Optional[str] = None, sendgrid_api_key: Optional[str] = None):
        """Initialize email sender with optional API keys (Resend preferred)."""
        self.resend_key = resend_api_key or os.environ.get('RESEND_API_KEY')
        self.sendgrid_key = sendgrid_api_key or os.environ.get('SENDGRID_API_KEY')
        self.from_email = os.environ.get('SINCOR_EMAIL', os.environ.get('SENDGRID_FROM_EMAIL', 'support@getsincor.com'))
        self.from_name = os.environ.get('SINCOR_EMAIL_FROM_NAME', os.environ.get('SENDGRID_FROM_NAME', 'SINCOR Team'))

        # Twilio SMS fallback
        self.twilio_sid = os.environ.get('TWILO_ID') or os.environ.get('TWILIO_ACCOUNT_SID')
        self.twilio_auth = os.environ.get('TWILO_AUTH') or os.environ.get('TWILIO_AUTH_TOKEN')
        self.twilio_number = os.environ.get('TWILO_NUMBER') or os.environ.get('TWILIO_FROM_NUMBER', '')
        self.twilio_client = None

        self.client = None

        # Priority: Resend > SendGrid > Twilio SMS > Stub
        if RESEND_AVAILABLE and self.resend_key:
            resend_sdk.api_key = self.resend_key
            self.mode = 'resend'
            logger.info("[EMAIL] Using Resend API for email delivery")
        elif SENDGRID_AVAILABLE and self.sendgrid_key:
            self.client = SendGridAPIClient(self.sendgrid_key)
            self.mode = 'sendgrid'
            logger.info("[EMAIL] Using SendGrid API for email delivery")
        elif TWILIO_AVAILABLE and self.twilio_sid and self.twilio_auth:
            self.twilio_client = TwilioClient(self.twilio_sid, self.twilio_auth)
            self.mode = 'sms_fallback'
            logger.info("[EMAIL] Resend/SendGrid not configured — using Twilio SMS as delivery fallback")
        else:
            self.mode = 'stub'
            logger.warning("[EMAIL] No email/SMS service configured, using stub mode")

    def send_thank_you_email(self, customer_email: str, customer_name: str, tier: str,
                            order_id: str, download_urls: Dict[str, str]) -> Dict[str, str]:
        """
        Send thank-you email after purchase.

        Args:
            customer_email: Customer's email address
            customer_name: Customer's name for personalization
            tier: Plan tier (Starter, Professional, Enterprise)
            order_id: Order ID for tracking
            download_urls: Dict with URL keys: 'starter', 'professional', 'enterprise', 'quickstart'

        Returns:
            {'status': 'sent|failed|stub', 'message_id': '...', 'error': '...'}
        """
        subject = f"Welcome to SINCOR {tier}! Your Training Guides Are Ready"

        html_content = self._render_thank_you_email(
            customer_name=customer_name,
            tier=tier,
            order_id=order_id,
            download_urls=download_urls
        )

        text_content = f"""
Welcome to SINCOR {tier}!

Thank you for your purchase. Your training guides are ready for download.

{tier} Guide:
{download_urls.get(tier.lower(), 'N/A')}

Quick-Start Checklist:
{download_urls.get('quickstart', 'N/A')}

Dashboard:
https://sincor.com/dashboard?email={customer_email}&order={order_id}

Support:
Email: support@sincor.com
Chat: https://sincor.com/support

Best regards,
SINCOR Team
"""

        return self.send_email(
            to_email=customer_email,
            to_name=customer_name,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            metadata={'order_id': order_id, 'tier': tier, 'type': 'thank_you'}
        )

    def send_welcome_email(self, customer_email: str, customer_name: str,
                            company_name: str, use_case: str, order_id: str = '') -> Dict[str, str]:
        """
        Send personalized welcome email after customer completes onboarding intake form.
        Uses their real name, company, and stated use case for personalization.
        """
        first_name = customer_name.split()[0] if customer_name else 'there'
        subject = f"{first_name}, your SINCOR agent team is being configured"

        html_content = f"""
        <div style="font-family:Inter,system-ui,sans-serif;max-width:600px;margin:0 auto;background:#0a0a0f;color:#e2e8f0;padding:40px 32px;border-radius:12px">
          <div style="margin-bottom:32px">
            <span style="background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;padding:8px 16px;border-radius:8px;font-weight:700;font-size:14px">SINCOR</span>
          </div>
          <h1 style="font-size:24px;font-weight:700;color:#f1f5f9;margin-bottom:16px">Hi {first_name}, your agents are spinning up 🚀</h1>
          <p style="color:#94a3b8;line-height:1.7;margin-bottom:24px">
            Thanks for telling us about <strong style="color:#f1f5f9">{company_name}</strong>. We've configured your agent team
            around your primary goal: <strong style="color:#a78bfa">{use_case}</strong>.
          </p>
          <div style="background:#111827;border:1px solid #1e293b;border-radius:12px;padding:24px;margin-bottom:24px">
            <h2 style="font-size:16px;font-weight:600;color:#f1f5f9;margin-bottom:16px">What happens next:</h2>
            <div style="display:flex;flex-direction:column;gap:12px">
              <div style="display:flex;gap:12px;align-items:flex-start">
                <span style="color:#6366f1;font-weight:700;min-width:24px">1.</span>
                <span style="color:#94a3b8">Your agents start working within <strong style="color:#f1f5f9">24 hours</strong> — no setup required from you</span>
              </div>
              <div style="display:flex;gap:12px;align-items:flex-start">
                <span style="color:#6366f1;font-weight:700;min-width:24px">2.</span>
                <span style="color:#94a3b8">You'll receive a <strong style="color:#f1f5f9">first activity report</strong> within 48 hours showing exactly what your agents accomplished</span>
              </div>
              <div style="display:flex;gap:12px;align-items:flex-start">
                <span style="color:#6366f1;font-weight:700;min-width:24px">3.</span>
                <span style="color:#94a3b8">Log into your <strong style="color:#f1f5f9">dashboard anytime</strong> to see live agent activity and results</span>
              </div>
            </div>
          </div>
          <a href="https://getsincor.com/dashboard" style="display:inline-block;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px">View Your Dashboard →</a>
          <p style="color:#475569;font-size:13px;margin-top:32px;line-height:1.6">
            Questions? Reply to this email anytime — a real human reads every message.<br>
            <a href="https://getsincor.com/terms" style="color:#6366f1">Terms</a> &nbsp;·&nbsp;
            <a href="https://getsincor.com/privacy" style="color:#6366f1">Privacy</a>
          </p>
        </div>
        """

        text_content = f"""Hi {first_name},

Your SINCOR agent team is being configured for {company_name}.

Focus: {use_case}

What happens next:
1. Agents start working within 24 hours
2. First activity report arrives within 48 hours
3. Log in anytime: https://getsincor.com/dashboard

Questions? Just reply to this email.

The SINCOR Team
https://getsincor.com
"""

        return self.send_email(
            to_email=customer_email,
            to_name=customer_name,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            metadata={'order_id': order_id, 'type': 'welcome', 'company': company_name}
        )

    def send_email(self, to_email: str, to_name: str, subject: str,
                  html_content: str, text_content: Optional[str] = None,
                  metadata: Optional[Dict] = None) -> Dict[str, str]:
        """
        Send email via SendGrid.

        Args:
            to_email: Recipient email
            to_name: Recipient name
            subject: Email subject
            html_content: HTML email body
            text_content: Plain text email body (optional)
            metadata: Custom metadata dict (for tracking)

        Returns:
            {'status': 'sent|failed|stub', 'message_id': '...', 'error': '...'}
        """
        metadata = metadata or {}

        if self.mode == 'resend':
            # Send via Resend
            try:
                from_addr = f"{self.from_name} <{self.from_email}>" if self.from_name else self.from_email
                response = resend_sdk.Emails.send({
                    "from": from_addr,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_content,
                    "text": text_content,
                    "reply_to": self.from_email
                })

                msg_id = getattr(response, "id", None) or (
                    response.get("id") if isinstance(response, dict) else None
                )
                if msg_id:
                    logger.info(f"[EMAIL-RESEND] Sent to {to_email} | msg_id={msg_id}")
                    return {
                        'status': 'sent',
                        'message_id': msg_id,
                        'provider': 'resend'
                    }
                else:
                    logger.error(f"[EMAIL-RESEND] No message ID in response for {to_email}: {response}")
                    return {'status': 'failed', 'error': 'No message ID in response', 'provider': 'resend'}

            except Exception as e:
                logger.error(f"[EMAIL-RESEND] Error sending to {to_email}: {e}")
                return {'status': 'failed', 'error': str(e), 'provider': 'resend'}

        elif self.mode == 'sendgrid':
            # Send via SendGrid
            try:
                from_email_obj = Email(self.from_email, self.from_name)
                to_email_obj = Email(to_email, to_name)
                content = Content("text/html", html_content)

                if text_content:
                    mail = Mail(
                        from_email=from_email_obj,
                        to_emails=to_email_obj,
                        subject=subject,
                        plain_text_content=Content("text/plain", text_content),
                        html_content=content
                    )
                else:
                    mail = Mail(
                        from_email=from_email_obj,
                        to_emails=to_email_obj,
                        subject=subject,
                        html_content=content
                    )

                mail.custom_args = metadata
                response = self.client.send(mail)

                logger.info(f"[EMAIL-SENDGRID] Sent to {to_email} | status={response.status_code}")
                return {
                    'status': 'sent',
                    'message_id': response.headers.get('X-Message-ID', 'unknown'),
                    'provider': 'sendgrid'
                }
            except Exception as e:
                logger.error(f"[EMAIL-SENDGRID] Error sending to {to_email}: {e}")
                return {'status': 'failed', 'error': str(e), 'provider': 'sendgrid'}

        elif self.mode == 'sms_fallback':
            # Twilio SMS fallback — notify admin of purchase
            order_id = metadata.get('order_id', 'unknown') if metadata else 'unknown'
            tier = metadata.get('tier', '') if metadata else ''
            vault_url = f"https://getsincor.com/admin/training-vault?email={to_email}&order_id={order_id}"

            logger.info(f"[EMAIL-SMS] Sending SMS notification for order {order_id} (customer: {to_email})")
            try:
                admin_notify = os.environ.get('ADMIN_SMS_NUMBER', self.twilio_number)
                self.twilio_client.messages.create(
                    body=f"[SINCOR ORDER] New purchase by {to_email}. {tier} plan. Order: {order_id}. Vault: {vault_url}",
                    from_=self.twilio_number,
                    to=admin_notify
                )
                logger.info(f"[EMAIL-SMS] Admin notified of order {order_id}")
            except Exception as sms_err:
                logger.error(f"[EMAIL-SMS] SMS failed: {sms_err}")

            return {
                'status': 'sms_fallback',
                'message_id': f"sms-{datetime.now().isoformat()}",
                'provider': 'twilio',
                'message': 'Customer email sent via SMS fallback'
            }

        else:  # stub mode
            logger.info(f"[EMAIL-STUB] Would send to {to_email} subject='{subject}'")
            return {
                'status': 'stub',
                'message_id': f"stub-{datetime.now().isoformat()}",
                'message': 'Email service not configured - stub mode'
            }

    def _render_thank_you_email(self, customer_name: str, tier: str,
                               order_id: str, download_urls: Dict[str, str]) -> str:
        """Render thank-you email HTML."""
        agent_counts = {'Starter': 10, 'Professional': 25, 'Enterprise': 42}
        agent_count = agent_counts.get(tier, 10)

        guide_url = download_urls.get(tier.lower(), f"/files/guides/sincor-{tier.lower()}-guide-{order_id}.pdf")
        quickstart_url = download_urls.get('quickstart', f"/files/guides/quickstart-checklist-{order_id}.pdf")

        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Welcome to SINCOR</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            margin: 0;
            padding: 20px;
        }}
        .email-container {{
            max-width: 600px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 50%, #f093fb 100%);
            color: white;
            padding: 40px 20px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0 0 10px 0;
            font-size: 28px;
        }}
        .header p {{
            margin: 0;
            opacity: 0.9;
            font-size: 14px;
        }}
        .content {{
            padding: 30px 20px;
        }}
        .greeting {{
            font-size: 16px;
            margin-bottom: 20px;
            line-height: 1.6;
        }}
        .button {{
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 600;
            margin: 10px 0;
            font-size: 14px;
        }}
        .feature-box {{
            background: #f8f9fa;
            border-left: 4px solid #667eea;
            padding: 15px;
            margin: 15px 0;
            border-radius: 4px;
        }}
        .feature-box strong {{
            color: #667eea;
            display: block;
            margin-bottom: 5px;
        }}
        .footer {{
            background: #f8f9fa;
            padding: 20px;
            border-top: 1px solid #e0e0e0;
            font-size: 12px;
            color: #666;
            text-align: center;
        }}
        .footer a {{
            color: #667eea;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="email-container">
        <div class="header">
            <h1>Welcome to SINCOR!</h1>
            <p>{tier} Plan - {agent_count} AI Agents Ready to Work</p>
        </div>

        <div class="content">
            <div class="greeting">
                <p>Hi {customer_name},</p>
                <p>Your SINCOR {tier} subscription is now active! We're excited to have you on board.</p>
                <p>Your training guides are ready to download and your new AI agents are standing by to start generating leads, automating workflows, and scaling your business.</p>
            </div>

            <h2 style="color: #667eea; margin-top: 30px;">Get Started in 3 Steps</h2>

            <div class="feature-box">
                <strong>1. Download Your Training Guide</strong>
                Click below to download the {tier}-tier setup guide ({{'30 pages' if tier == 'Starter' else '60 pages' if tier == 'Professional' else '120+ pages'}}):
                <p><a href="{guide_url}" class="button">Download {tier} Guide (PDF)</a></p>
            </div>

            <div class="feature-box">
                <strong>2. Print the Quick-Start Checklist</strong>
                A 1-page checklist for your first 30 days:
                <p><a href="{quickstart_url}" class="button">Download Checklist (PDF)</a></p>
            </div>

            <div class="feature-box">
                <strong>3. Access Your Dashboard</strong>
                Log in to your training vault with all resources:
                <p><a href="https://sincor.com/admin/training-vault?email={customer_name}@{customer_name.split('@')[1] if '@' in customer_name else 'example.com'}" class="button">Go to Dashboard</a></p>
            </div>

            <h2 style="color: #667eea; margin-top: 30px;">Your {tier} Plan Includes</h2>
            <ul style="line-height: 2; color: #555;">
                <li>{{'Scout, Synthesizer, Builder Agents' if tier == 'Starter' else 'All Scout/Synthesizer/Builder plus Negotiator, Caretaker Agents' if tier == 'Professional' else 'All 42 AI Agents with custom development'}} </li>
                <li>{{'5 core integrations' if tier == 'Starter' else '15 enterprise integrations' if tier == 'Professional' else '25+ white-label integrations'}}</li>
                <li>{{'Email support - 24 hour response' if tier == 'Starter' else 'Priority support - 4 hour response' if tier == 'Professional' else '24/7 dedicated success manager'}}</li>
                <li>Full knowledge base access</li>
                <li>{{'14-day free trial' if tier == 'Starter' else 'First month included training calls' if tier == 'Professional' else 'Unlimited onboarding & strategy'}}</li>
            </ul>

            <div style="background: #eef2ff; padding: 20px; border-radius: 6px; margin: 30px 0;">
                <h3 style="margin-top: 0; color: #667eea;">Need Help?</h3>
                <p style="margin-bottom: 10px;">Our support team is here for you:</p>
                <ul style="margin: 10px 0; padding-left: 20px;">
                    <li><strong>Email:</strong> support@sincor.com</li>
                    <li><strong>Chat:</strong> https://sincor.com/support</li>
                    <li><strong>Knowledge Base:</strong> https://help.sincor.com</li>
                    <li><strong>Video Tutorials:</strong> https://youtube.com/sincor</li>
                </ul>
            </div>

            <p style="color: #666; font-size: 14px; margin-top: 20px;">
                <strong>Tip:</strong> The best first step is to download the guide above and follow the "Day 1" setup instructions. You'll have your first workflow running within 24 hours!
            </p>
        </div>

        <div class="footer">
            <p style="margin: 0 0 10px 0;">SINCOR - AI-Powered Automation Platform</p>
            <p style="margin: 0;">
                <a href="https://sincor.com">Website</a> |
                <a href="https://sincor.com/privacy">Privacy</a> |
                <a href="https://sincor.com/terms">Terms</a>
            </p>
            <p style="margin: 10px 0 0 0; color: #999;">
                Order ID: {order_id}
            </p>
        </div>
    </div>
</body>
</html>
"""


def get_email_sender(sendgrid_api_key: Optional[str] = None) -> EmailSender:
    """Factory function to get email sender instance."""
    return EmailSender(sendgrid_api_key)
