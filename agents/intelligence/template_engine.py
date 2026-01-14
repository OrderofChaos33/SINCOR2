"""
Template Engine for SINCOR

Automatically generates personalized marketing content by populating templates
with business intelligence data. Creates targeted video scripts, email content,
and marketing materials for specific business types and personas.

Features:
- Business-specific template population
- Persona-based content adaptation
- HBPC framework integration (Hook-Benefit-Proof-CTA)
- Multi-format content generation (video scripts, emails, ads)
- A/B testing support for templates
"""

import json
import sqlite3
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from jinja2 import Template, Environment, FileSystemLoader
import yaml

import sys
sys.path.append(str(Path(__file__).parent.parent))
from base_agent import BaseAgent


class TemplateEngine(BaseAgent):
    """Engine for generating personalized marketing content from business data."""
    
    def __init__(self, name="TemplateEngine", log_path="logs/template_engine.log", config=None):
        super().__init__(name, log_path, config)
        
        # Template configuration
        self.templates_dir = Path("templates")
        self.templates_dir.mkdir(exist_ok=True)
        
        # Load HBPC framework and personas
        self.hbpc_framework = self._load_hbpc_framework()
        self.personas = self._load_personas()
        
        # Business intel database
        self.business_db = Path("data/business_intel.db")
        
        # Content generation database
        self.content_db = Path("data/generated_content.db")
        self.content_db.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize Jinja2 environment
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=True
        )
        
        # Add custom filters
        self._setup_jinja_filters()
        
        # Initialize content database
        self._init_content_database()
        
        self._log("Template Engine initialized")
    
    def _load_hbpc_framework(self) -> Dict:
        """Load the Hook-Benefit-Proof-CTA framework from training data."""
        try:
            hbpc_file = Path("training/ad_corpus.yaml")
            if hbpc_file.exists():
                with open(hbpc_file, 'r') as f:
                    data = yaml.safe_load(f)
                    return data.get("frameworks", [{}])[0] if data.get("frameworks") else {}
            
            # Default HBPC framework if file not found
            return {
                "id": "HBPC",
                "name": "Hook-Benefit-Proof-CTA",
                "hooks": [
                    "Tired of losing customers to dirty cars?",
                    "Your business deserves clients who notice the details.",
                    "First impressions matter - especially for your fleet.",
                    "Don't waste weekends cleaning when you could be earning."
                ],
                "benefits": [
                    "Professional detailing that keeps customers coming back.",
                    "Same-day service that keeps your business moving.",
                    "Spotless results that reflect your attention to quality.",
                    "Predictable pricing and invoice-ready service."
                ],
                "proofs": [
                    "Over 500 local businesses trust our detailing service.",
                    "Customer testimonial: 'Best investment for our company image.'",
                    "Before/after photos prove the dramatic difference.",
                    "Licensed, insured, and 10+ years serving local businesses."
                ],
                "ctas": [
                    "Book your fleet detailing consultation today.",
                    "Call now for same-day service: [PHONE]",
                    "Visit [WEBSITE] to schedule online.",
                    "Text 'DETAIL' to [PHONE] for instant quote."
                ]
            }
        except Exception as e:
            self._log(f"Error loading HBPC framework: {e}")
            return {}
    
    def _load_personas(self) -> Dict:
        """Load persona configurations."""
        try:
            personas_file = Path("training/ad_personas.yaml")
            if personas_file.exists():
                with open(personas_file, 'r') as f:
                    data = yaml.safe_load(f)
                    return data.get("personas", {})
            
            # Default personas
            return {
                "business_owner": {
                    "tone": "professional, efficient, ROI-focused",
                    "pain_points": ["time management", "professional image", "cost efficiency"],
                    "motivators": ["business growth", "customer retention", "operational efficiency"],
                    "preferred_contact": "email"
                },
                "fleet_manager": {
                    "tone": "reliable, uptime-focused, invoice-ready",
                    "pain_points": ["vehicle downtime", "maintenance costs", "scheduling"],
                    "motivators": ["operational efficiency", "cost predictability", "vendor reliability"],
                    "preferred_contact": "phone"
                },
                "senior": {
                    "tone": "gentle, convenient, trustworthy",
                    "pain_points": ["physical limitations", "scheduling flexibility", "trust"],
                    "motivators": ["convenience", "reliability", "personal service"],
                    "preferred_contact": "phone"
                }
            }
        except Exception as e:
            self._log(f"Error loading personas: {e}")
            return {}
    
    def _setup_jinja_filters(self):
        """Add custom Jinja2 filters for content generation."""
        
        def format_phone(phone):
            """Format phone number for display."""
            if not phone:
                return ""
            
            # Remove all non-digits
            digits = re.sub(r'\D', '', phone)
            
            # Format as (XXX) XXX-XXXX
            if len(digits) >= 10:
                return f"({digits[-10:-7]}) {digits[-7:-4]}-{digits[-4:]}"
            return phone
        
        def business_type_friendly(business_type):
            """Convert business_type to friendly name."""
            type_mapping = {
                "auto_detailing": "Auto Detailing",
                "car_wash": "Car Wash",
                "mobile_detailing": "Mobile Detailing",
                "fleet_services": "Fleet Services"
            }
            return type_mapping.get(business_type, business_type.replace("_", " ").title())
        
        def select_by_persona(items, persona):
            """Select content based on persona."""
            if not items or not persona:
                return items[0] if items else ""
            
            # Simple persona-based selection logic
            persona_preferences = {
                "business_owner": [0, 3],  # Professional, ROI-focused options
                "fleet_manager": [1, 2],   # Efficiency, reliability options  
                "senior": [2, 1]           # Trust, convenience options
            }
            
            if persona in persona_preferences and items:
                preferred_indices = persona_preferences[persona]
                for idx in preferred_indices:
                    if idx < len(items):
                        return items[idx]
                return items[0]
            
            return items[0] if items else ""
        
        # Register filters
        self.jinja_env.filters['format_phone'] = format_phone
        self.jinja_env.filters['business_type_friendly'] = business_type_friendly
        self.jinja_env.filters['select_by_persona'] = select_by_persona
    
    def _init_content_database(self):
        """Initialize database for tracking generated content."""
        try:
            conn = sqlite3.connect(self.content_db)
            cursor = conn.cursor()
            
            # Generated content table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS generated_content (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id INTEGER,
                    business_name TEXT,
                    content_type TEXT,
                    template_name TEXT,
                    persona TEXT,
                    subject_line TEXT,
                    content_body TEXT,
                    personalization_data TEXT,
                    performance_score REAL DEFAULT 0,
                    sent_date TEXT,
                    response_received BOOLEAN DEFAULT FALSE,
                    response_type TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Content performance tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS content_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id INTEGER,
                    metric_name TEXT,
                    metric_value REAL,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (content_id) REFERENCES generated_content (id)
                )
            ''')
            
            # A/B test tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ab_tests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_name TEXT,
                    variant_a_template TEXT,
                    variant_b_template TEXT,
                    variant_a_performance REAL DEFAULT 0,
                    variant_b_performance REAL DEFAULT 0,
                    winner TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            self._log("Content database initialized successfully")
            
        except Exception as e:
            self._log(f"Content database initialization error: {e}")
    
    def create_default_templates(self):
        """Create default email and content templates."""
        
        # Email template for business owners
        email_template = """Subject: Transform Your Business Image with Professional Auto Detailing

Dear {{ business_name }} Team,

{{ hbpc.hooks | select_by_persona(persona) }}

I noticed {{ business_name }} has {{ rating }} stars on Google - clearly you care about quality and customer experience. That's exactly why I wanted to reach out.

**Here's what professional detailing can do for your business:**
{{ hbpc.benefits | select_by_persona(persona) }}

**Why {{ city }} businesses choose our service:**
{{ hbpc.proofs | select_by_persona(persona) }}

**Special offer for {{ business_name }}:**
- Free assessment of your current vehicle situation
- 15% discount on first fleet service
- Flexible scheduling that works around your business hours

{{ hbpc.ctas | select_by_persona(persona) }}

Best regards,
[YOUR_NAME]
[YOUR_PHONE | format_phone]
[YOUR_EMAIL]

P.S. Your competition is already investing in their professional image. Don't let dirty vehicles be the reason customers choose them over {{ business_name }}.
"""
        
        # Video script template
        video_script_template = """# Video Script for {{ business_name }}
**Target**: {{ business_type | business_type_friendly }} in {{ city }}
**Persona**: {{ persona }}
**Duration**: 60 seconds

## Hook (0-5 seconds)
{{ hbpc.hooks | select_by_persona(persona) }}
*Visual: Split screen - dirty vs clean business vehicle*

## Problem (5-15 seconds)
You run {{ business_name }}, and every detail matters to your reputation. But when was the last time you really looked at your company vehicles?
*Visual: Close-up of dirty vehicle with business logo barely visible*

## Solution (15-35 seconds)
{{ hbpc.benefits | select_by_persona(persona) }}
*Visual: Before/after transformation of similar business vehicle*

## Proof (35-50 seconds)  
{{ hbpc.proofs | select_by_persona(persona) }}
*Visual: Customer testimonials, before/after photos*

## CTA (50-60 seconds)
Ready to elevate {{ business_name }}'s professional image?
{{ hbpc.ctas | select_by_persona(persona) }}
*Visual: Clean vehicle with sparkling logo, contact information*

---
**Personalization Data:**
- Business: {{ business_name }}
- Location: {{ city }}, {{ state }}
- Phone: {{ phone | format_phone }}
- Rating: {{ rating }}/5 ({{ review_count }} reviews)
- Lead Score: {{ lead_score }}/100
"""
        
        # Social media ad template
        social_ad_template = """🚗 Attention {{ city }} Business Owners! 🚗

Is your company vehicle sending the RIGHT message about {{ business_type | business_type_friendly }}?

{{ hbpc.hooks | select_by_persona(persona) }}

✨ Professional detailing that:
{{ hbpc.benefits | select_by_persona(persona) }}

🏆 {{ hbpc.proofs | select_by_persona(persona) }}

💼 Special offer for {{ business_name }}:
15% off first service + FREE assessment

{{ hbpc.ctas | select_by_persona(persona) }}

#{{ city }}Business #AutoDetailing #ProfessionalImage #{{ business_type.replace('_', '') }}
"""
        
        # Save templates to files
        templates = {
            "business_email.html": email_template,
            "video_script.md": video_script_template,
            "social_media_ad.txt": social_ad_template
        }
        
        for filename, content in templates.items():
            template_path = self.templates_dir / filename
            with open(template_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            self._log(f"Created template: {filename}")
    
    def generate_personalized_content(self, business_data: Dict, content_type: str = "email", 
                                    persona: str = "business_owner", template_name: str = None) -> Dict:
        """
        Generate personalized marketing content for a specific business.
        
        Args:
            business_data: Business information from database
            content_type: Type of content (email, video_script, social_ad)
            persona: Target persona (business_owner, fleet_manager, senior)
            template_name: Specific template to use
            
        Returns:
            Dictionary with generated content and metadata
        """
        try:
            # Select template
            if not template_name:
                template_mapping = {
                    "email": "business_email.html",
                    "video_script": "video_script.md", 
                    "social_ad": "social_media_ad.txt"
                }
                template_name = template_mapping.get(content_type, "business_email.html")
            
            # Load template
            template = self.jinja_env.get_template(template_name)
            
            # Prepare template context
            context = {
                **business_data,
                "hbpc": self.hbpc_framework,
                "persona": persona,
                "personas": self.personas,
                "generation_date": datetime.now().strftime("%Y-%m-%d"),
                "content_type": content_type
            }
            
            # Generate content
            generated_content = template.render(**context)
            
            # Extract subject line for emails
            subject_line = ""
            if content_type == "email" and "Subject:" in generated_content:
                subject_match = re.search(r'Subject:\s*(.+)', generated_content)
                if subject_match:
                    subject_line = subject_match.group(1).strip()
                    # Remove subject line from content body
                    generated_content = re.sub(r'Subject:\s*.+\n\n?', '', generated_content)
            
            # Create content record
            content_record = {
                "business_id": business_data.get("id"),
                "business_name": business_data.get("business_name"),
                "content_type": content_type,
                "template_name": template_name,
                "persona": persona,
                "subject_line": subject_line,
                "content_body": generated_content,
                "personalization_data": json.dumps({
                    "rating": business_data.get("rating"),
                    "review_count": business_data.get("review_count"),
                    "lead_score": business_data.get("lead_score"),
                    "city": business_data.get("city"),
                    "business_type": business_data.get("business_type")
                })
            }
            
            # Save to database
            content_id = self._save_generated_content(content_record)
            content_record["id"] = content_id
            
            self._log(f"Generated {content_type} content for {business_data.get('business_name')} (ID: {content_id})")
            
            return content_record
            
        except Exception as e:
            self._log(f"Error generating content: {e}")
            return {}
    
    def _save_generated_content(self, content_record: Dict) -> int:
        """Save generated content to database."""
        try:
            conn = sqlite3.connect(self.content_db)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO generated_content 
                (business_id, business_name, content_type, template_name, persona,
                 subject_line, content_body, personalization_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                content_record.get("business_id"),
                content_record.get("business_name"),
                content_record.get("content_type"),
                content_record.get("template_name"),
                content_record.get("persona"),
                content_record.get("subject_line"),
                content_record.get("content_body"),
                content_record.get("personalization_data")
            ))
            
            content_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return content_id
            
        except Exception as e:
            self._log(f"Error saving generated content: {e}")
            return 0
    
    def bulk_generate_content(self, business_list: List[Dict], content_type: str = "email",
                            persona_mapping: Dict = None) -> List[Dict]:
        """
        Generate content for multiple businesses in bulk.
        
        Args:
            business_list: List of business dictionaries
            content_type: Type of content to generate
            persona_mapping: Custom persona mapping based on business characteristics
            
        Returns:
            List of generated content records
        """
        generated_content = []
        
        for business in business_list:
            try:
                # Determine persona based on business characteristics
                persona = self._determine_persona(business, persona_mapping)
                
                # Generate content
                content = self.generate_personalized_content(
                    business, content_type, persona
                )
                
                if content:
                    generated_content.append(content)
                
            except Exception as e:
                self._log(f"Error in bulk generation for {business.get('business_name', 'Unknown')}: {e}")
        
        self._log(f"Bulk generated {len(generated_content)} pieces of {content_type} content")
        return generated_content
    
    def _determine_persona(self, business: Dict, persona_mapping: Dict = None) -> str:
        """Determine the best persona for a business based on characteristics."""
        
        if persona_mapping and business.get("business_type") in persona_mapping:
            return persona_mapping[business.get("business_type")]
        
        # Default persona logic
        review_count = business.get("review_count", 0)
        rating = business.get("rating", 0)
        
        # Fleet manager for high-volume businesses
        if review_count > 100 or "fleet" in business.get("business_name", "").lower():
            return "fleet_manager"
        
        # Senior for lower-tech, traditional businesses
        if rating > 4.5 and review_count < 20:
            return "senior"
        
        # Default to business owner
        return "business_owner"
    
    def get_content_performance(self, content_id: int) -> Dict:
        """Get performance metrics for generated content."""
        try:
            conn = sqlite3.connect(self.content_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get content details
            cursor.execute('''
                SELECT * FROM generated_content WHERE id = ?
            ''', (content_id,))
            
            content = cursor.fetchone()
            if not content:
                return {}
            
            # Get performance metrics
            cursor.execute('''
                SELECT metric_name, metric_value, recorded_at 
                FROM content_performance 
                WHERE content_id = ?
                ORDER BY recorded_at DESC
            ''', (content_id,))
            
            metrics = [dict(row) for row in cursor.fetchall()]
            conn.close()
            
            return {
                "content": dict(content),
                "performance_metrics": metrics
            }
            
        except Exception as e:
            self._log(f"Error getting content performance: {e}")
            return {}
    
    def record_content_performance(self, content_id: int, metric_name: str, metric_value: float):
        """Record a performance metric for generated content."""
        try:
            conn = sqlite3.connect(self.content_db)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO content_performance (content_id, metric_name, metric_value)
                VALUES (?, ?, ?)
            ''', (content_id, metric_name, metric_value))
            
            conn.commit()
            conn.close()
            
            self._log(f"Recorded {metric_name}={metric_value} for content {content_id}")
            
        except Exception as e:
            self._log(f"Error recording performance metric: {e}")
    
    def _run_custom_diagnostics(self) -> Optional[Dict[str, Any]]:
        """Run Template Engine-specific diagnostics."""
        try:
            diagnostics = {
                "templates_directory": str(self.templates_dir),
                "templates_exist": self.templates_dir.exists(),
                "content_database": str(self.content_db),
                "content_db_exists": self.content_db.exists(),
                "hbpc_framework_loaded": bool(self.hbpc_framework),
                "personas_loaded": bool(self.personas),
                "jinja_env_ready": self.jinja_env is not None
            }
            
            # Count available templates
            if self.templates_dir.exists():
                template_files = list(self.templates_dir.glob("*"))
                diagnostics["available_templates"] = len(template_files)
                diagnostics["template_files"] = [t.name for t in template_files]
            
            # Content database stats
            if self.content_db.exists():
                conn = sqlite3.connect(self.content_db)
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) FROM generated_content")
                diagnostics["total_generated_content"] = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM generated_content WHERE response_received = TRUE")
                diagnostics["content_with_responses"] = cursor.fetchone()[0]
                
                conn.close()
            
            return diagnostics
            
        except Exception as e:
            return {"diagnostics_error": str(e)}


if __name__ == "__main__":
    # Example usage
    engine = TemplateEngine()
    
    # Create default templates
    engine.create_default_templates()
    
    # Example business data
    business_data = {
        "id": 1,
        "business_name": "Austin Auto Detailing",
        "city": "Austin",
        "state": "TX",
        "phone": "+15125551234",
        "rating": 4.7,
        "review_count": 89,
        "business_type": "auto_detailing",
        "lead_score": 85
    }
    
    # Generate email content
    email_content = engine.generate_personalized_content(
        business_data, "email", "business_owner"
    )
    
    print("Generated Email:")
    print("Subject:", email_content.get("subject_line"))
    print("Content:", email_content.get("content_body")[:200] + "...")
    
    # Generate video script
    video_content = engine.generate_personalized_content(
        business_data, "video_script", "business_owner"
    )
    
    print("\nGenerated Video Script:")
    print(video_content.get("content_body")[:200] + "...")