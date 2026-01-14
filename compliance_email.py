"""
SINCOR Compliance Email Notification System
Send notifications for GDPR requests and compliance events
"""

import os
from datetime import datetime

def send_gdpr_notification_email(user_email: str, request_type: str, reference_id: str, details: str = ""):
    """
    Send GDPR request notification to privacy team
    
    Args:
        user_email: Email of user making the request
        request_type: Type of GDPR request (access, delete, rectify, portability)
        reference_id: Unique reference ID for tracking
        details: Additional details from the user
    """
    
    # Email configuration (set these in environment variables)
    SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    PRIVACY_EMAIL = os.getenv('PRIVACY_EMAIL', 'privacy@getsincor.com')
    
    if not SMTP_USER or not SMTP_PASSWORD:
        print("⚠️  Email not configured. Set SMTP_USER and SMTP_PASSWORD environment variables.")
        return False
    
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'[URGENT] GDPR {request_type.upper()} Request - {reference_id}'
        msg['From'] = SMTP_USER
        msg['To'] = PRIVACY_EMAIL
        msg['Reply-To'] = user_email
        
        # Email body
        text = f"""
GDPR DATA SUBJECT REQUEST
{"=" * 50}

Reference ID: {reference_id}
Request Type: {request_type.upper()}
User Email: {user_email}
Received: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

USER DETAILS:
{details if details else "No additional details provided"}

{"=" * 50}
REQUIRED ACTION:

You must respond to this request within 30 days as required by GDPR Article 12(3).

Request Type Actions:
- ACCESS: Provide copy of all personal data held
- DELETE: Permanently delete all user data (Right to be Forgotten)
- RECTIFY: Correct inaccurate personal data
- PORTABILITY: Export data in machine-readable format

RESPONSE CHECKLIST:
[ ] Verify identity of requester
[ ] Gather all relevant data from systems
[ ] Prepare response document
[ ] Send response to user: {user_email}
[ ] Log response in compliance tracker
[ ] Update reference {reference_id} status

Contact user at: {user_email}

---
This is an automated notification from SINCOR Compliance System.
DO NOT REPLY to this email. Contact user directly.
"""
        
        html = f"""
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .header {{ background: #dc2626; color: white; padding: 20px; text-align: center; }}
        .content {{ padding: 20px; background: #f9fafb; }}
        .box {{ background: white; border: 2px solid #dc2626; padding: 15px; margin: 15px 0; border-radius: 8px; }}
        .info {{ background: #eff6ff; border-left: 4px solid #3b82f6; padding: 15px; margin: 15px 0; }}
        .checklist {{ background: #f0fdf4; border-left: 4px solid #10b981; padding: 15px; margin: 15px 0; }}
        h2 {{ color: #dc2626; margin-top: 0; }}
        .ref {{ font-family: monospace; background: #fef3c7; padding: 5px 10px; border-radius: 4px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
        td {{ padding: 8px; border-bottom: 1px solid #e5e7eb; }}
        td:first-child {{ font-weight: bold; width: 150px; }}
        .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #6b7280; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>⚠️ URGENT GDPR REQUEST</h1>
        <p style="margin: 0; font-size: 14px;">Data Subject Rights Request Received</p>
    </div>
    
    <div class="content">
        <div class="box">
            <h2>📋 Request Details</h2>
            <table>
                <tr><td>Reference ID:</td><td><span class="ref">{reference_id}</span></td></tr>
                <tr><td>Request Type:</td><td><strong>{request_type.upper()}</strong></td></tr>
                <tr><td>User Email:</td><td><a href="mailto:{user_email}">{user_email}</a></td></tr>
                <tr><td>Received:</td><td>{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</td></tr>
                <tr><td>Deadline:</td><td><strong style="color: #dc2626;">30 days from now</strong></td></tr>
            </table>
        </div>
        
        <div class="info">
            <h3>📝 User Details</h3>
            <p>{details if details else "<em>No additional details provided</em>"}</p>
        </div>
        
        <div class="checklist">
            <h3>✅ Response Checklist</h3>
            <ul>
                <li>Verify identity of requester</li>
                <li>Gather all relevant data from systems</li>
                <li>Prepare response document</li>
                <li>Send response to user: <a href="mailto:{user_email}">{user_email}</a></li>
                <li>Log response in compliance tracker</li>
                <li>Update reference {reference_id} status</li>
            </ul>
        </div>
        
        <div class="box">
            <h3>🎯 Required Actions by Request Type</h3>
            <ul>
                <li><strong>ACCESS:</strong> Provide complete copy of all personal data held</li>
                <li><strong>DELETE:</strong> Permanently delete all user data (Right to be Forgotten - Article 17)</li>
                <li><strong>RECTIFY:</strong> Correct any inaccurate personal data (Article 16)</li>
                <li><strong>PORTABILITY:</strong> Export data in machine-readable format (Article 20)</li>
            </ul>
        </div>
    </div>
    
    <div class="footer">
        <p>This is an automated notification from SINCOR Compliance System.</p>
        <p>DO NOT REPLY to this email. Contact user directly at <a href="mailto:{user_email}">{user_email}</a></p>
        <p>© 2026 SINCOR, LLC | 16192 Coastal Highway, Lewes, DE 19958, USA</p>
    </div>
</body>
</html>
"""
        
        # Attach both plain text and HTML versions
        part1 = MIMEText(text, 'plain')
        part2 = MIMEText(html, 'html')
        msg.attach(part1)
        msg.attach(part2)
        
        # Send email
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        
        print(f"✅ GDPR notification email sent successfully for {reference_id}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to send GDPR notification email: {e}")
        return False


def send_unsubscribe_confirmation(user_email: str):
    """
    Send confirmation email when user unsubscribes (optional but good practice)
    
    Args:
        user_email: Email of user who unsubscribed
    """
    
    SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    
    if not SMTP_USER or not SMTP_PASSWORD:
        return False
    
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Unsubscribe Confirmation - SINCOR'
        msg['From'] = SMTP_USER
        msg['To'] = user_email
        
        text = f"""
You have been successfully unsubscribed from SINCOR marketing communications.

You will no longer receive:
- Product updates and announcements
- Marketing emails and promotions
- Newsletter and tips

You will still receive (if applicable):
- Transaction confirmations
- Account security alerts
- Service-related communications

Changed your mind? You can resubscribe anytime at:
https://getsincor.com/#waitlist

Questions? Contact us at support@getsincor.com

---
SINCOR, LLC
16192 Coastal Highway, Lewes, DE 19958, USA
© 2026 All rights reserved
"""
        
        html = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
    <div style="background: #1a1a2e; color: white; padding: 30px; text-align: center;">
        <h1 style="margin: 0;">✅ Unsubscribe Confirmed</h1>
    </div>
    
    <div style="padding: 30px; background: #f9fafb;">
        <p>You have been successfully unsubscribed from SINCOR marketing communications.</p>
        
        <div style="background: white; border-left: 4px solid #10b981; padding: 15px; margin: 20px 0;">
            <h3 style="margin-top: 0;">You will no longer receive:</h3>
            <ul>
                <li>Product updates and announcements</li>
                <li>Marketing emails and promotions</li>
                <li>Newsletter and tips</li>
            </ul>
        </div>
        
        <div style="background: white; border-left: 4px solid #3b82f6; padding: 15px; margin: 20px 0;">
            <h3 style="margin-top: 0;">You will still receive:</h3>
            <ul>
                <li>Transaction confirmations</li>
                <li>Account security alerts</li>
                <li>Service-related communications</li>
            </ul>
        </div>
        
        <div style="text-align: center; margin: 30px 0;">
            <p><strong>Changed your mind?</strong></p>
            <a href="https://getsincor.com/#waitlist" style="display: inline-block; background: #667eea; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: 600;">Resubscribe</a>
        </div>
        
        <p style="font-size: 14px; color: #6b7280;">Questions? Contact us at <a href="mailto:support@getsincor.com">support@getsincor.com</a></p>
    </div>
    
    <div style="text-align: center; padding: 20px; font-size: 12px; color: #6b7280;">
        <p>SINCOR, LLC<br>16192 Coastal Highway, Lewes, DE 19958, USA</p>
        <p>© 2026 All rights reserved</p>
    </div>
</body>
</html>
"""
        
        part1 = MIMEText(text, 'plain')
        part2 = MIMEText(html, 'html')
        msg.attach(part1)
        msg.attach(part2)
        
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        
        print(f"✅ Unsubscribe confirmation sent to {user_email}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to send unsubscribe confirmation: {e}")
        return False
