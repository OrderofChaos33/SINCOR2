"""
Campaign Automation Agent for SINCOR

Orchestrates the complete marketing automation pipeline:
1. Business Intelligence → Template Generation → Email Delivery → Response Tracking
2. Manages campaigns targeting 60,000+ detailing shops across the USA
3. Handles personalized email sequences, follow-ups, and performance analytics
4. Scales to multiple industries beyond auto detailing

Features:
- Automated campaign creation and execution
- Multi-stage email sequences with smart timing
- Response tracking and lead scoring updates
- A/B testing for templates and subject lines
- Performance analytics and optimization
- Integration with SMTP services and CRMs
"""

import json
import sqlite3
import smtplib
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import schedule
import threading
from dataclasses import dataclass
import os

import sys
sys.path.append(str(Path(__file__).parent.parent))
from base_agent import BaseAgent
from intelligence.business_intel_agent import BusinessIntelAgent
from intelligence.template_engine import TemplateEngine


@dataclass
class CampaignConfig:
    """Configuration for marketing campaigns."""
    name: str
    target_business_type: str = "auto_detailing"
    target_persona: str = "business_owner"
    min_lead_score: int = 70
    max_businesses_per_day: int = 50
    email_sequence_days: List[int] = None  # [0, 3, 7, 14] - days to send follow-ups
    subject_line_variants: List[str] = None
    template_variants: List[str] = None
    send_time_hour: int = 10  # 10 AM local time
    active: bool = True


class CampaignAutomationAgent(BaseAgent):
    """Agent for automating marketing campaigns at scale."""
    
    def __init__(self, name="CampaignAutomation", log_path="logs/campaign_automation.log", config=None):
        super().__init__(name, log_path, config)
        
        # Dependencies
        self.business_intel = BusinessIntelAgent(config=config)
        self.template_engine = TemplateEngine(config=config)
        
        # Email configuration
        self.smtp_config = {
            "host": config.get("smtp_host", "smtp.gmail.com") if config else "smtp.gmail.com",
            "port": config.get("smtp_port", 587) if config else 587,
            "user": config.get("smtp_user", "") if config else "",
            "password": config.get("smtp_password", "") if config else "",
            "from_email": config.get("from_email", "") if config else "",
            "from_name": config.get("from_name", "SINCOR Marketing") if config else "SINCOR Marketing"
        }
        
        # Campaign database
        self.campaign_db = Path("data/campaign_automation.db")
        self.campaign_db.parent.mkdir(parents=True, exist_ok=True)
        
        # Rate limiting
        self.emails_per_hour = config.get("emails_per_hour", 100) if config else 100
        self.daily_email_limit = config.get("daily_email_limit", 500) if config else 500
        
        # Initialize database
        self._init_campaign_database()
        
        # Campaign scheduler
        self.scheduler_active = False
        self.scheduler_thread = None
        
        self._log("Campaign Automation Agent initialized")
    
    def _init_campaign_database(self):
        """Initialize campaign tracking database."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            # Campaigns table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    config TEXT,
                    status TEXT DEFAULT 'draft',
                    target_business_type TEXT,
                    businesses_targeted INTEGER DEFAULT 0,
                    emails_sent INTEGER DEFAULT 0,
                    emails_opened INTEGER DEFAULT 0,
                    emails_clicked INTEGER DEFAULT 0,
                    responses_received INTEGER DEFAULT 0,
                    conversions INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    completed_at TEXT
                )
            ''')
            
            # Campaign emails table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS campaign_emails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER,
                    business_id INTEGER,
                    business_name TEXT,
                    business_email TEXT,
                    sequence_step INTEGER DEFAULT 0,
                    subject_line TEXT,
                    content_id INTEGER,
                    sent_at TEXT,
                    delivery_status TEXT,
                    opened_at TEXT,
                    clicked_at TEXT,
                    response_received_at TEXT,
                    response_type TEXT,
                    bounce_reason TEXT,
                    tracking_id TEXT,
                    FOREIGN KEY (campaign_id) REFERENCES campaigns (id)
                )
            ''')
            
            # Email performance tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS email_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id INTEGER,
                    event_type TEXT,
                    event_data TEXT,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (email_id) REFERENCES campaign_emails (id)
                )
            ''')
            
            # A/B test results
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ab_test_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER,
                    test_type TEXT,
                    variant_a TEXT,
                    variant_b TEXT,
                    variant_a_performance REAL,
                    variant_b_performance REAL,
                    confidence_level REAL,
                    winner TEXT,
                    sample_size INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (campaign_id) REFERENCES campaigns (id)
                )
            ''')
            
            conn.commit()
            conn.close()
            self._log("Campaign database initialized successfully")
            
        except Exception as e:
            self._log(f"Campaign database initialization error: {e}")
    
    def create_campaign(self, campaign_config: CampaignConfig) -> int:
        """Create a new marketing campaign."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            # Set defaults for sequence
            if campaign_config.email_sequence_days is None:
                campaign_config.email_sequence_days = [0, 3, 7, 14]
            
            cursor.execute('''
                INSERT INTO campaigns (name, config, target_business_type, status)
                VALUES (?, ?, ?, ?)
            ''', (
                campaign_config.name,
                json.dumps(campaign_config.__dict__),
                campaign_config.target_business_type,
                'draft'
            ))
            
            campaign_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            self._log(f"Created campaign '{campaign_config.name}' with ID {campaign_id}")
            return campaign_id
            
        except Exception as e:
            self._log(f"Error creating campaign: {e}")
            return 0
    
    def start_campaign(self, campaign_id: int) -> bool:
        """Start executing a campaign."""
        try:
            # Get campaign config
            campaign_config = self._get_campaign_config(campaign_id)
            if not campaign_config:
                self._log(f"Campaign {campaign_id} not found")
                return False
            
            # Get target businesses
            businesses = self.business_intel.get_high_value_prospects(
                limit=campaign_config.max_businesses_per_day,
                min_score=campaign_config.min_lead_score
            )
            
            if not businesses:
                self._log(f"No businesses found for campaign {campaign_id}")
                return False
            
            # Update campaign status
            self._update_campaign_status(campaign_id, 'active', started_at=datetime.now().isoformat())
            
            # Generate content for each business
            content_generated = 0
            for business in businesses:
                try:
                    # Generate personalized content
                    content = self.template_engine.generate_personalized_content(
                        business, "email", campaign_config.target_persona
                    )
                    
                    if content:
                        # Schedule email
                        self._schedule_campaign_email(
                            campaign_id, business, content, sequence_step=0
                        )
                        content_generated += 1
                
                except Exception as e:
                    self._log(f"Error generating content for {business.get('business_name', 'Unknown')}: {e}")
            
            # Update campaign stats
            self._update_campaign_stats(campaign_id, businesses_targeted=content_generated)
            
            self._log(f"Started campaign {campaign_id} with {content_generated} businesses targeted")
            return True
            
        except Exception as e:
            self._log(f"Error starting campaign {campaign_id}: {e}")
            return False
    
    def _get_campaign_config(self, campaign_id: int) -> Optional[CampaignConfig]:
        """Get campaign configuration."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            cursor.execute('SELECT config FROM campaigns WHERE id = ?', (campaign_id,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                config_data = json.loads(result[0])
                return CampaignConfig(**config_data)
            
            return None
            
        except Exception as e:
            self._log(f"Error getting campaign config: {e}")
            return None
    
    def _schedule_campaign_email(self, campaign_id: int, business: Dict, content: Dict, sequence_step: int = 0):
        """Schedule an email for a campaign."""
        try:
            # Get campaign config
            campaign_config = self._get_campaign_config(campaign_id)
            if not campaign_config:
                return
            
            # Calculate send time
            send_delay_days = campaign_config.email_sequence_days[sequence_step] if sequence_step < len(campaign_config.email_sequence_days) else 0
            send_time = datetime.now() + timedelta(days=send_delay_days)
            
            # Store in database
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO campaign_emails 
                (campaign_id, business_id, business_name, business_email, sequence_step,
                 subject_line, content_id, sent_at, delivery_status, tracking_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                campaign_id,
                business.get("id"),
                business.get("business_name"),
                business.get("email"),
                sequence_step,
                content.get("subject_line"),
                content.get("id"),
                send_time.isoformat() if send_delay_days == 0 else None,
                'scheduled' if send_delay_days > 0 else 'pending',
                self._generate_tracking_id(campaign_id, business.get("id"), sequence_step)
            ))
            
            conn.commit()
            conn.close()
            
            # Send immediately if sequence_step is 0
            if send_delay_days == 0:
                self._send_campaign_email(cursor.lastrowid, business, content)
            
        except Exception as e:
            self._log(f"Error scheduling email: {e}")
    
    def _generate_tracking_id(self, campaign_id: int, business_id: int, sequence_step: int) -> str:
        """Generate unique tracking ID for email."""
        import hashlib
        data = f"{campaign_id}_{business_id}_{sequence_step}_{datetime.now().timestamp()}"
        return hashlib.md5(data.encode()).hexdigest()[:12]
    
    def _send_campaign_email(self, email_id: int, business: Dict, content: Dict) -> bool:
        """Send a campaign email."""
        try:
            if not self._can_send_email():
                self._log("Rate limit reached, skipping email send")
                return False
            
            if not business.get("email"):
                self._log(f"No email address for {business.get('business_name')}")
                return False
            
            # Create email message
            msg = MIMEMultipart('alternative')
            msg['From'] = f"{self.smtp_config['from_name']} <{self.smtp_config['from_email']}>"
            msg['To'] = business.get("email")
            msg['Subject'] = content.get("subject_line", "Professional Auto Detailing Services")
            
            # Add tracking pixel and links
            tracked_content = self._add_email_tracking(content.get("content_body"), email_id)
            
            # Create both text and HTML versions
            text_part = MIMEText(tracked_content, 'plain')
            html_part = MIMEText(self._convert_to_html(tracked_content), 'html')
            
            msg.attach(text_part)
            msg.attach(html_part)
            
            # Send email
            with smtplib.SMTP(self.smtp_config['host'], self.smtp_config['port']) as server:
                server.starttls()
                server.login(self.smtp_config['user'], self.smtp_config['password'])
                server.send_message(msg)
            
            # Update email status
            self._update_email_status(email_id, 'sent', sent_at=datetime.now().isoformat())
            
            # Mark business as contacted
            self.business_intel.mark_contacted(business.get("id"), "email_sent")
            
            # Track email sent
            self._track_email_event(email_id, "sent", {
                "recipient": business.get("email"),
                "subject": content.get("subject_line")
            })
            
            self._log(f"Sent email to {business.get('business_name')} ({business.get('email')})")
            return True
            
        except Exception as e:
            self._log(f"Error sending email: {e}")
            self._update_email_status(email_id, 'failed', bounce_reason=str(e))
            return False
    
    def _can_send_email(self) -> bool:
        """Check if we can send email based on rate limits."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            # Check hourly limit
            one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
            cursor.execute('''
                SELECT COUNT(*) FROM campaign_emails 
                WHERE delivery_status = 'sent' AND sent_at > ?
            ''', (one_hour_ago,))
            
            hourly_count = cursor.fetchone()[0]
            
            # Check daily limit
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            cursor.execute('''
                SELECT COUNT(*) FROM campaign_emails 
                WHERE delivery_status = 'sent' AND sent_at > ?
            ''', (today_start,))
            
            daily_count = cursor.fetchone()[0]
            conn.close()
            
            return hourly_count < self.emails_per_hour and daily_count < self.daily_email_limit
            
        except Exception as e:
            self._log(f"Error checking email limits: {e}")
            return False
    
    def _add_email_tracking(self, content: str, email_id: int) -> str:
        """Add tracking pixels and links to email content."""
        # Add tracking pixel (1x1 transparent image)
        tracking_pixel = f"\n\n<!-- Tracking -->\n<img src='https://your-domain.com/track/open/{email_id}' width='1' height='1' style='display:none;' />"
        
        # Replace URLs with tracked versions
        import re
        def replace_url(match):
            url = match.group(0)
            return f"https://your-domain.com/track/click/{email_id}?url={url}"
        
        # Simple URL tracking replacement
        tracked_content = re.sub(r'https?://[^\s\]]+', replace_url, content)
        tracked_content += tracking_pixel
        
        return tracked_content
    
    def _convert_to_html(self, text_content: str) -> str:
        """Convert text content to basic HTML."""
        html_content = text_content.replace('\n', '<br>\n')
        html_content = f"<html><body>{html_content}</body></html>"
        return html_content
    
    def _update_email_status(self, email_id: int, status: str, **kwargs):
        """Update email delivery status."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            # Build update query
            set_clause = "delivery_status = ?"
            params = [status]
            
            for key, value in kwargs.items():
                set_clause += f", {key} = ?"
                params.append(value)
            
            params.append(email_id)
            
            cursor.execute(f'''
                UPDATE campaign_emails SET {set_clause} WHERE id = ?
            ''', params)
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self._log(f"Error updating email status: {e}")
    
    def _track_email_event(self, email_id: int, event_type: str, event_data: Dict):
        """Track email events for analytics."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO email_performance (email_id, event_type, event_data)
                VALUES (?, ?, ?)
            ''', (email_id, event_type, json.dumps(event_data)))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self._log(f"Error tracking email event: {e}")
    
    def _update_campaign_status(self, campaign_id: int, status: str, **kwargs):
        """Update campaign status."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            set_clause = "status = ?"
            params = [status]
            
            for key, value in kwargs.items():
                set_clause += f", {key} = ?"
                params.append(value)
            
            params.append(campaign_id)
            
            cursor.execute(f'''
                UPDATE campaigns SET {set_clause} WHERE id = ?
            ''', params)
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self._log(f"Error updating campaign status: {e}")
    
    def _update_campaign_stats(self, campaign_id: int, **stats):
        """Update campaign statistics."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            # Build update query
            set_clause = []
            params = []
            
            for key, value in stats.items():
                set_clause.append(f"{key} = ?")
                params.append(value)
            
            if set_clause:
                params.append(campaign_id)
                cursor.execute(f'''
                    UPDATE campaigns SET {", ".join(set_clause)} WHERE id = ?
                ''', params)
                
                conn.commit()
            
            conn.close()
            
        except Exception as e:
            self._log(f"Error updating campaign stats: {e}")
    
    def process_scheduled_emails(self):
        """Process emails scheduled for sending."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            cursor = conn.cursor()
            
            # Get emails scheduled for now or earlier
            current_time = datetime.now().isoformat()
            cursor.execute('''
                SELECT ce.*, c.config 
                FROM campaign_emails ce
                JOIN campaigns c ON ce.campaign_id = c.id
                WHERE ce.delivery_status = 'scheduled' 
                AND ce.sent_at <= ?
                ORDER BY ce.sent_at
                LIMIT 50
            ''', (current_time,))
            
            scheduled_emails = cursor.fetchall()
            conn.close()
            
            for email_row in scheduled_emails:
                email_id = email_row[0]
                business_id = email_row[2]
                content_id = email_row[7]
                
                # Get business data
                business = self._get_business_data(business_id)
                if not business:
                    continue
                
                # Get content data
                content = self._get_content_data(content_id)
                if not content:
                    continue
                
                # Send email
                success = self._send_campaign_email(email_id, business, content)
                
                if success:
                    # Schedule next sequence email if applicable
                    self._schedule_next_sequence_email(email_row)
                
                # Rate limiting
                time.sleep(1)
            
            if scheduled_emails:
                self._log(f"Processed {len(scheduled_emails)} scheduled emails")
                
        except Exception as e:
            self._log(f"Error processing scheduled emails: {e}")
    
    def _get_business_data(self, business_id: int) -> Optional[Dict]:
        """Get business data from business intel database."""
        try:
            conn = sqlite3.connect(self.business_intel.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM businesses WHERE id = ?', (business_id,))
            result = cursor.fetchone()
            conn.close()
            
            return dict(result) if result else None
            
        except Exception as e:
            self._log(f"Error getting business data: {e}")
            return None
    
    def _get_content_data(self, content_id: int) -> Optional[Dict]:
        """Get content data from template engine database."""
        try:
            conn = sqlite3.connect(self.template_engine.content_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM generated_content WHERE id = ?', (content_id,))
            result = cursor.fetchone()
            conn.close()
            
            return dict(result) if result else None
            
        except Exception as e:
            self._log(f"Error getting content data: {e}")
            return None
    
    def _schedule_next_sequence_email(self, current_email_row):
        """Schedule the next email in the sequence."""
        try:
            campaign_id = current_email_row[1]
            business_id = current_email_row[2]
            current_sequence_step = current_email_row[5]
            
            # Get campaign config
            campaign_config = self._get_campaign_config(campaign_id)
            if not campaign_config:
                return
            
            # Check if there's a next step in the sequence
            next_step = current_sequence_step + 1
            if next_step >= len(campaign_config.email_sequence_days):
                return
            
            # Get business and generate follow-up content
            business = self._get_business_data(business_id)
            if not business:
                return
            
            # Generate follow-up content
            follow_up_content = self.template_engine.generate_personalized_content(
                business, "email", campaign_config.target_persona
            )
            
            if follow_up_content:
                self._schedule_campaign_email(
                    campaign_id, business, follow_up_content, next_step
                )
            
        except Exception as e:
            self._log(f"Error scheduling next sequence email: {e}")
    
    def start_scheduler(self):
        """Start the campaign scheduler."""
        if self.scheduler_active:
            return
        
        self.scheduler_active = True
        
        # Schedule email processing every 10 minutes
        schedule.every(10).minutes.do(self.process_scheduled_emails)
        
        def scheduler_worker():
            while self.scheduler_active:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        
        self.scheduler_thread = threading.Thread(target=scheduler_worker)
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()
        
        self._log("Campaign scheduler started")
    
    def stop_scheduler(self):
        """Stop the campaign scheduler."""
        self.scheduler_active = False
        if self.scheduler_thread:
            self.scheduler_thread.join()
        
        schedule.clear()
        self._log("Campaign scheduler stopped")
    
    def get_campaign_analytics(self, campaign_id: int) -> Dict:
        """Get comprehensive campaign analytics."""
        try:
            conn = sqlite3.connect(self.campaign_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Basic campaign info
            cursor.execute('SELECT * FROM campaigns WHERE id = ?', (campaign_id,))
            campaign = cursor.fetchone()
            if not campaign:
                return {}
            
            # Email stats
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_emails,
                    COUNT(CASE WHEN delivery_status = 'sent' THEN 1 END) as emails_sent,
                    COUNT(CASE WHEN opened_at IS NOT NULL THEN 1 END) as emails_opened,
                    COUNT(CASE WHEN clicked_at IS NOT NULL THEN 1 END) as emails_clicked,
                    COUNT(CASE WHEN response_received_at IS NOT NULL THEN 1 END) as responses
                FROM campaign_emails WHERE campaign_id = ?
            ''', (campaign_id,))
            
            email_stats = dict(cursor.fetchone())
            
            # Calculate rates
            sent = email_stats.get('emails_sent', 0)
            if sent > 0:
                email_stats['open_rate'] = round((email_stats.get('emails_opened', 0) / sent) * 100, 2)
                email_stats['click_rate'] = round((email_stats.get('emails_clicked', 0) / sent) * 100, 2)
                email_stats['response_rate'] = round((email_stats.get('responses', 0) / sent) * 100, 2)
            else:
                email_stats.update({'open_rate': 0, 'click_rate': 0, 'response_rate': 0})
            
            # Performance by sequence step
            cursor.execute('''
                SELECT 
                    sequence_step,
                    COUNT(*) as sent,
                    COUNT(CASE WHEN opened_at IS NOT NULL THEN 1 END) as opened,
                    COUNT(CASE WHEN response_received_at IS NOT NULL THEN 1 END) as responses
                FROM campaign_emails 
                WHERE campaign_id = ? AND delivery_status = 'sent'
                GROUP BY sequence_step
                ORDER BY sequence_step
            ''', (campaign_id,))
            
            sequence_performance = [dict(row) for row in cursor.fetchall()]
            
            conn.close()
            
            return {
                "campaign": dict(campaign),
                "email_stats": email_stats,
                "sequence_performance": sequence_performance
            }
            
        except Exception as e:
            self._log(f"Error getting campaign analytics: {e}")
            return {}
    
    def _run_custom_diagnostics(self) -> Optional[Dict[str, Any]]:
        """Run Campaign Automation agent-specific diagnostics."""
        try:
            diagnostics = {
                "campaign_database": str(self.campaign_db),
                "campaign_db_exists": self.campaign_db.exists(),
                "smtp_configured": bool(self.smtp_config.get("user")),
                "scheduler_active": self.scheduler_active,
                "emails_per_hour_limit": self.emails_per_hour,
                "daily_email_limit": self.daily_email_limit,
                "business_intel_ready": self.business_intel is not None,
                "template_engine_ready": self.template_engine is not None
            }
            
            # Campaign database stats
            if self.campaign_db.exists():
                conn = sqlite3.connect(self.campaign_db)
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) FROM campaigns")
                diagnostics["total_campaigns"] = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'active'")
                diagnostics["active_campaigns"] = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM campaign_emails")
                diagnostics["total_emails_tracked"] = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM campaign_emails WHERE delivery_status = 'sent'")
                diagnostics["emails_sent"] = cursor.fetchone()[0]
                
                conn.close()
            
            return diagnostics
            
        except Exception as e:
            return {"diagnostics_error": str(e)}


if __name__ == "__main__":
    # Example usage
    config = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "your-email@gmail.com",
        "smtp_password": "your-app-password",
        "from_email": "your-email@gmail.com",
        "from_name": "SINCOR Marketing",
        "emails_per_hour": 100,
        "daily_email_limit": 500
    }
    
    agent = CampaignAutomationAgent(config=config)
    
    # Create a campaign
    campaign_config = CampaignConfig(
        name="Auto Detailing Texas Q1 2025",
        target_business_type="auto_detailing",
        target_persona="business_owner",
        min_lead_score=75,
        max_businesses_per_day=25,
        email_sequence_days=[0, 3, 7, 14]
    )
    
    campaign_id = agent.create_campaign(campaign_config)
    print(f"Created campaign ID: {campaign_id}")
    
    # Start the campaign
    if campaign_id:
        success = agent.start_campaign(campaign_id)
        print(f"Campaign started: {success}")
        
        # Start scheduler for automated email processing
        agent.start_scheduler()
        
        # Get analytics after some time
        analytics = agent.get_campaign_analytics(campaign_id)
        print("Campaign Analytics:", json.dumps(analytics, indent=2))