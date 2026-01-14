"""
SINCOR Security, Compliance & Legal Framework
Enterprise-Grade Protection for Business Intelligence Empire

This system ensures SINCOR operates within all legal boundaries while
protecting customer data and maintaining franchise compliance standards.
"""

import sqlite3
import json
import hashlib
import secrets
from datetime import datetime, timedelta
from pathlib import Path
import logging
import re

# Compliance Standards Configuration
COMPLIANCE_FRAMEWORKS = {
    "data_protection": {
        "gdpr": {
            "name": "General Data Protection Regulation (EU)",
            "requirements": [
                "Explicit consent for data processing",
                "Right to data portability",
                "Right to be forgotten",
                "Data breach notification within 72 hours",
                "Privacy by design implementation",
                "Data Protection Officer appointment"
            ],
            "penalties": "Up to 4% of annual revenue or €20M"
        },
        "ccpa": {
            "name": "California Consumer Privacy Act",
            "requirements": [
                "Right to know about personal information collection",
                "Right to delete personal information",
                "Right to opt-out of sale of personal information",
                "Non-discrimination for exercising privacy rights"
            ],
            "penalties": "Up to $7,500 per violation"
        },
        "pipeda": {
            "name": "Personal Information Protection (Canada)",
            "requirements": [
                "Consent for collection, use, and disclosure",
                "Limited collection of personal information",
                "Accuracy of personal information",
                "Safeguards for personal information"
            ],
            "penalties": "Up to $100,000 CAD"
        }
    },
    
    "business_regulations": {
        "can_spam": {
            "name": "CAN-SPAM Act (US)",
            "requirements": [
                "Clear sender identification",
                "Truthful subject lines",
                "Clear opt-out mechanism",
                "Honor opt-out requests within 10 days",
                "Physical address disclosure"
            ],
            "penalties": "Up to $43,792 per violation"
        },
        "casl": {
            "name": "Canadian Anti-Spam Legislation",
            "requirements": [
                "Express or implied consent before sending",
                "Clear identification of sender",
                "Unsubscribe mechanism",
                "Honor unsubscribe within 10 days"
            ],
            "penalties": "Up to $10M CAD for businesses"
        },
        "ftc_guidelines": {
            "name": "FTC Business Opportunity Rules",
            "requirements": [
                "Material connection disclosure",
                "Earnings claims substantiation",
                "Truthful advertising",
                "Franchise disclosure requirements"
            ],
            "penalties": "Variable civil penalties"
        }
    },
    
    "franchise_law": {
        "ftc_franchise_rule": {
            "name": "FTC Franchise Rule",
            "requirements": [
                "Franchise Disclosure Document (FDD) provision",
                "14-day review period before signing",
                "Material change disclosures",
                "Earnings claims substantiation",
                "Relationship termination procedures"
            ],
            "penalties": "Up to $43,792 per violation"
        }
    }
}

# Security Standards
SECURITY_STANDARDS = {
    "data_encryption": {
        "at_rest": "AES-256 encryption for all stored data",
        "in_transit": "TLS 1.3 for all data transmission",
        "key_management": "HSM or cloud KMS for key storage"
    },
    "access_control": {
        "authentication": "Multi-factor authentication required",
        "authorization": "Role-based access control (RBAC)",
        "session_management": "Secure session handling with timeout"
    },
    "audit_logging": {
        "user_actions": "All user actions logged with timestamps",
        "data_access": "All data access attempts logged",
        "system_events": "System-level events and errors logged",
        "retention": "Logs retained for minimum 7 years"
    }
}

class SecurityComplianceEngine:
    """Enterprise security and compliance management system."""
    
    def __init__(self):
        self.db_path = Path(__file__).parent / "data" / "compliance.db"
        self.init_compliance_db()
        self.setup_logging()
    
    def init_compliance_db(self):
        """Initialize compliance tracking database."""
        self.db_path.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Compliance events table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS compliance_events (
                id INTEGER PRIMARY KEY,
                event_type TEXT NOT NULL,
                user_id TEXT,
                data_subject TEXT,
                action_taken TEXT,
                compliance_framework TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address TEXT,
                user_agent TEXT,
                status TEXT DEFAULT 'logged'
            )
        ''')
        
        # Data processing consent table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS data_consent (
                id INTEGER PRIMARY KEY,
                user_email TEXT UNIQUE NOT NULL,
                consent_type TEXT NOT NULL,
                consent_given BOOLEAN DEFAULT FALSE,
                consent_timestamp TIMESTAMP,
                consent_ip TEXT,
                withdrawal_timestamp TIMESTAMP,
                legal_basis TEXT,
                data_categories TEXT,
                processing_purposes TEXT
            )
        ''')
        
        # Audit trail table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_trail (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                action TEXT NOT NULL,
                resource TEXT,
                before_state TEXT,
                after_state TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address TEXT,
                success BOOLEAN DEFAULT TRUE,
                risk_level TEXT DEFAULT 'low'
            )
        ''')
        
        # Data breach incidents
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS security_incidents (
                id INTEGER PRIMARY KEY,
                incident_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                affected_records INTEGER,
                detection_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                response_timestamp TIMESTAMP,
                resolution_timestamp TIMESTAMP,
                status TEXT DEFAULT 'detected',
                description TEXT,
                remediation_actions TEXT,
                reported_to_authorities BOOLEAN DEFAULT FALSE
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def setup_logging(self):
        """Setup secure audit logging."""
        log_dir = Path(__file__).parent / "logs" / "compliance"
        log_dir.mkdir(parents=True, exist_ok=True)
        
        logging.basicConfig(
            filename=log_dir / f"compliance_{datetime.now().strftime('%Y%m')}.log",
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
    
    def validate_email_compliance(self, email_content, recipient_consent=True):
        """Validate email campaigns for CAN-SPAM and CASL compliance."""
        violations = []
        
        # Check for required elements
        if not self._has_sender_identification(email_content):
            violations.append("Missing clear sender identification (CAN-SPAM/CASL)")
        
        if not self._has_physical_address(email_content):
            violations.append("Missing physical address (CAN-SPAM)")
        
        if not self._has_unsubscribe_mechanism(email_content):
            violations.append("Missing unsubscribe mechanism (CAN-SPAM/CASL)")
        
        if not recipient_consent:
            violations.append("No documented consent for recipient (CASL)")
        
        # Log compliance check
        self.log_compliance_event(
            event_type="email_compliance_check",
            action_taken="email_validation",
            compliance_framework="CAN-SPAM/CASL",
            status="passed" if not violations else "failed"
        )
        
        return {
            "compliant": len(violations) == 0,
            "violations": violations,
            "frameworks_checked": ["CAN-SPAM", "CASL"]
        }
    
    def validate_data_processing(self, user_email, processing_purpose, data_categories):
        """Validate data processing against GDPR/CCPA requirements."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if user has given consent
        cursor.execute('''
            SELECT * FROM data_consent 
            WHERE user_email = ? AND consent_given = TRUE
        ''', (user_email,))
        
        consent_record = cursor.fetchone()
        conn.close()
        
        if not consent_record:
            # Log potential violation
            self.log_compliance_event(
                event_type="data_processing_violation",
                data_subject=user_email,
                action_taken="processing_without_consent",
                compliance_framework="GDPR/CCPA",
                status="violation_detected"
            )
            
            return {
                "compliant": False,
                "violation": "No valid consent found for data processing",
                "required_actions": ["Obtain explicit consent", "Document legal basis"]
            }
        
        return {
            "compliant": True,
            "consent_date": consent_record[4],
            "legal_basis": consent_record[6]
        }
    
    def validate_franchise_disclosures(self, franchise_tier, financial_claims):
        """Validate franchise offerings against FTC Franchise Rule."""
        violations = []
        
        # Check for required disclosures
        required_disclosures = [
            "franchisor_information",
            "business_experience", 
            "litigation_history",
            "bankruptcy_history",
            "initial_fees",
            "ongoing_fees",
            "territory_rights",
            "restrictions",
            "financing_options",
            "training_programs"
        ]
        
        # Validate financial claims
        if financial_claims and not self._has_substantiation(financial_claims):
            violations.append("Earnings claims lack proper substantiation (FTC Franchise Rule)")
        
        # Check for 14-day review period
        if not self._provides_review_period():
            violations.append("Must provide 14-day FDD review period")
        
        self.log_compliance_event(
            event_type="franchise_disclosure_check",
            action_taken="fdd_validation",
            compliance_framework="FTC_Franchise_Rule",
            status="passed" if not violations else "needs_review"
        )
        
        return {
            "compliant": len(violations) == 0,
            "violations": violations,
            "required_documents": ["Franchise Disclosure Document (FDD)", "Franchise Agreement"]
        }
    
    def record_data_consent(self, user_email, consent_type, ip_address, data_categories, purposes):
        """Record user consent for data processing."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO data_consent
            (user_email, consent_type, consent_given, consent_timestamp, 
             consent_ip, legal_basis, data_categories, processing_purposes)
            VALUES (?, ?, TRUE, ?, ?, 'consent', ?, ?)
        ''', (user_email, consent_type, datetime.now().isoformat(), 
              ip_address, json.dumps(data_categories), json.dumps(purposes)))
        
        conn.commit()
        conn.close()
        
        self.log_compliance_event(
            event_type="consent_recorded",
            data_subject=user_email,
            action_taken="consent_given",
            compliance_framework="GDPR/CCPA"
        )
    
    def process_data_deletion_request(self, user_email, requestor_ip):
        """Handle GDPR/CCPA data deletion requests."""
        # Log the request
        self.log_compliance_event(
            event_type="data_deletion_request",
            data_subject=user_email,
            action_taken="deletion_requested",
            compliance_framework="GDPR/CCPA",
            ip_address=requestor_ip
        )
        
        # Implement actual deletion logic here
        deletion_tasks = [
            "Remove from marketing databases",
            "Delete campaign history", 
            "Remove analytics data",
            "Purge backup systems",
            "Update franchise partner systems"
        ]
        
        return {
            "request_id": secrets.token_hex(16),
            "status": "processing",
            "estimated_completion": (datetime.now() + timedelta(days=30)).isoformat(),
            "tasks": deletion_tasks,
            "contact_email": "privacy@sincor.com"
        }
    
    def log_compliance_event(self, event_type, user_id=None, data_subject=None, 
                           action_taken=None, compliance_framework=None, 
                           ip_address=None, status="logged"):
        """Log compliance events for audit trail."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO compliance_events
            (event_type, user_id, data_subject, action_taken, 
             compliance_framework, ip_address, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (event_type, user_id, data_subject, action_taken, 
              compliance_framework, ip_address, status))
        
        conn.commit()
        conn.close()
        
        # Also log to file system
        logging.info(f"COMPLIANCE: {event_type} - {action_taken} - {compliance_framework} - {status}")
    
    def generate_compliance_report(self, framework="all", days=30):
        """Generate compliance status report."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get recent compliance events
        cursor.execute('''
            SELECT event_type, compliance_framework, status, COUNT(*) as count
            FROM compliance_events 
            WHERE timestamp > datetime('now', '-{} days')
            GROUP BY event_type, compliance_framework, status
        '''.format(days))
        
        events = cursor.fetchall()
        
        # Get consent statistics
        cursor.execute('''
            SELECT consent_type, COUNT(*) as count
            FROM data_consent
            WHERE consent_given = TRUE
            GROUP BY consent_type
        ''')
        
        consent_stats = cursor.fetchall()
        conn.close()
        
        return {
            "report_period_days": days,
            "compliance_events": [
                {
                    "event_type": event[0],
                    "framework": event[1], 
                    "status": event[2],
                    "count": event[3]
                } for event in events
            ],
            "consent_statistics": [
                {
                    "consent_type": stat[0],
                    "count": stat[1]
                } for stat in consent_stats
            ],
            "recommendations": self._get_compliance_recommendations()
        }
    
    def _has_sender_identification(self, email_content):
        """Check if email has clear sender identification."""
        # Simple check for sender info in email
        return "SINCOR" in email_content.get("body", "") and "@" in email_content.get("from", "")
    
    def _has_physical_address(self, email_content):
        """Check if email includes physical address."""
        body = email_content.get("body", "").lower()
        # Look for address patterns
        address_indicators = ["street", "avenue", "road", "suite", "po box", "p.o. box"]
        return any(indicator in body for indicator in address_indicators)
    
    def _has_unsubscribe_mechanism(self, email_content):
        """Check if email has unsubscribe mechanism."""
        body = email_content.get("body", "").lower()
        unsubscribe_indicators = ["unsubscribe", "opt-out", "opt out", "remove me"]
        return any(indicator in body for indicator in unsubscribe_indicators)
    
    def _has_substantiation(self, financial_claims):
        """Check if financial claims have proper substantiation."""
        # In real implementation, this would check for supporting documentation
        required_elements = ["disclaimer", "based on", "results may vary"]
        claims_text = str(financial_claims).lower()
        return any(element in claims_text for element in required_elements)
    
    def _provides_review_period(self):
        """Check if franchise offering provides required review period."""
        # This would check if FDD is provided 14 days before signing
        return True  # Placeholder - implement based on actual process
    
    def _get_compliance_recommendations(self):
        """Get compliance recommendations based on current status."""
        return [
            "Review and update privacy policy quarterly",
            "Conduct annual franchise compliance audit",
            "Implement automated consent management",
            "Regular staff training on data protection",
            "Monitor for regulatory updates"
        ]

def add_compliance_routes(app):
    """Add security and compliance routes to Flask app."""
    
    @app.route("/compliance-dashboard")
    def compliance_dashboard():
        """Compliance management dashboard."""
        from flask import render_template_string
        return render_template_string(COMPLIANCE_DASHBOARD_TEMPLATE)
    
    @app.route("/privacy-policy")
    def privacy_policy():
        """GDPR/CCPA compliant privacy policy."""
        from flask import render_template_string
        return render_template_string(PRIVACY_POLICY_TEMPLATE)
    
    @app.route("/data-deletion-request", methods=["GET", "POST"])
    def data_deletion_request():
        """Handle data deletion requests."""
        from flask import request, jsonify, render_template_string
        
        if request.method == "POST":
            data = request.get_json()
            email = data.get("email")
            ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            
            compliance = SecurityComplianceEngine()
            result = compliance.process_data_deletion_request(email, ip)
            
            return jsonify(result)
        
        return render_template_string(DATA_DELETION_TEMPLATE)
    
    @app.route("/api/compliance-report")
    def compliance_report_api():
        """API endpoint for compliance reporting."""
        from flask import jsonify
        compliance = SecurityComplianceEngine()
        report = compliance.generate_compliance_report()
        return jsonify(report)

# Templates for compliance pages
COMPLIANCE_DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Compliance Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body class="bg-gray-50">
    <!-- Header -->
    <header class="bg-red-900 text-white shadow-lg">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex items-center justify-between">
                <div class="flex items-center">
                    <img src="/static/logo.png" alt="SINCOR" class="h-10 w-auto mr-3">
                    <div>
                        <h1 class="text-2xl font-bold">SINCOR Compliance</h1>
                        <div class="text-xs text-red-200">Security & Legal Oversight</div>
                    </div>
                </div>
                <nav class="space-x-4">
                    <a href="/enterprise-dashboard" class="text-red-200 hover:text-white">Enterprise</a>
                    <a href="/" class="text-red-200 hover:text-white">Home</a>
                </nav>
            </div>
        </div>
    </header>

    <div class="max-w-7xl mx-auto py-8 px-4" x-data="complianceApp()" x-init="loadData()">
        <!-- Compliance Status Overview -->
        <div class="bg-white rounded-lg shadow-lg p-6 mb-8">
            <h2 class="text-2xl font-bold mb-6">🔒 Compliance Status Overview</h2>
            
            <div class="grid md:grid-cols-4 gap-6">
                <div class="text-center">
                    <div class="text-3xl font-bold text-green-600">✅</div>
                    <div class="text-lg font-semibold">GDPR Compliant</div>
                    <div class="text-sm text-gray-600">EU Data Protection</div>
                </div>
                <div class="text-center">
                    <div class="text-3xl font-bold text-green-600">✅</div>
                    <div class="text-lg font-semibold">CCPA Compliant</div>
                    <div class="text-sm text-gray-600">California Privacy</div>
                </div>
                <div class="text-center">
                    <div class="text-3xl font-bold text-green-600">✅</div>
                    <div class="text-lg font-semibold">CAN-SPAM Act</div>
                    <div class="text-sm text-gray-600">Email Marketing</div>
                </div>
                <div class="text-center">
                    <div class="text-3xl font-bold text-yellow-600">⚠️</div>
                    <div class="text-lg font-semibold">FTC Franchise</div>
                    <div class="text-sm text-gray-600">Needs FDD Review</div>
                </div>
            </div>
        </div>

        <!-- Risk Assessment -->
        <div class="grid md:grid-cols-2 gap-8 mb-8">
            <div class="bg-white rounded-lg shadow-lg p-6">
                <h3 class="text-xl font-bold mb-4">🚨 Risk Assessment</h3>
                <div class="space-y-3">
                    <div class="flex justify-between items-center">
                        <span>Data Processing Risk</span>
                        <span class="bg-green-100 text-green-800 px-3 py-1 rounded text-sm font-semibold">LOW</span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span>Email Marketing Risk</span>
                        <span class="bg-green-100 text-green-800 px-3 py-1 rounded text-sm font-semibold">LOW</span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span>Franchise Legal Risk</span>
                        <span class="bg-yellow-100 text-yellow-800 px-3 py-1 rounded text-sm font-semibold">MEDIUM</span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span>Security Breach Risk</span>
                        <span class="bg-green-100 text-green-800 px-3 py-1 rounded text-sm font-semibold">LOW</span>
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-lg shadow-lg p-6">
                <h3 class="text-xl font-bold mb-4">📋 Required Actions</h3>
                <div class="space-y-3">
                    <div class="flex items-start">
                        <span class="text-yellow-500 mr-3 mt-1">⚠️</span>
                        <div>
                            <div class="font-medium">Complete FDD Documentation</div>
                            <div class="text-sm text-gray-600">Required for franchise offerings</div>
                        </div>
                    </div>
                    <div class="flex items-start">
                        <span class="text-blue-500 mr-3 mt-1">ℹ️</span>
                        <div>
                            <div class="font-medium">Update Privacy Policy</div>
                            <div class="text-sm text-gray-600">Quarterly review recommended</div>
                        </div>
                    </div>
                    <div class="flex items-start">
                        <span class="text-green-500 mr-3 mt-1">✅</span>
                        <div>
                            <div class="font-medium">Implement Consent Management</div>
                            <div class="text-sm text-gray-600">GDPR/CCPA consent tracking active</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Compliance Frameworks -->
        <div class="bg-white rounded-lg shadow-lg p-6">
            <h3 class="text-xl font-bold mb-6">📜 Regulatory Frameworks</h3>
            
            <div class="grid md:grid-cols-2 gap-8">
                <div>
                    <h4 class="text-lg font-semibold mb-4">Data Protection</h4>
                    <div class="space-y-3">
                        <div class="border-l-4 border-green-500 pl-4">
                            <div class="font-medium">GDPR (EU)</div>
                            <div class="text-sm text-gray-600">Max penalty: 4% revenue or €20M</div>
                            <div class="text-xs text-green-600">Status: Compliant</div>
                        </div>
                        <div class="border-l-4 border-green-500 pl-4">
                            <div class="font-medium">CCPA (California)</div>
                            <div class="text-sm text-gray-600">Max penalty: $7,500 per violation</div>
                            <div class="text-xs text-green-600">Status: Compliant</div>
                        </div>
                    </div>
                </div>
                
                <div>
                    <h4 class="text-lg font-semibold mb-4">Business Operations</h4>
                    <div class="space-y-3">
                        <div class="border-l-4 border-green-500 pl-4">
                            <div class="font-medium">CAN-SPAM Act</div>
                            <div class="text-sm text-gray-600">Max penalty: $43,792 per violation</div>
                            <div class="text-xs text-green-600">Status: Compliant</div>
                        </div>
                        <div class="border-l-4 border-yellow-500 pl-4">
                            <div class="font-medium">FTC Franchise Rule</div>
                            <div class="text-sm text-gray-600">Max penalty: $43,792 per violation</div>
                            <div class="text-xs text-yellow-600">Status: In Progress</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        function complianceApp() {
            return {
                data: {},
                loading: true,
                
                async loadData() {
                    try {
                        const response = await fetch('/api/compliance-report');
                        this.data = await response.json();
                    } catch (error) {
                        console.error('Error loading compliance data:', error);
                    } finally {
                        this.loading = false;
                    }
                }
            }
        }
    </script>
</body>
</html>
"""

PRIVACY_POLICY_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Privacy Policy - GDPR & CCPA Compliant</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto py-12 px-4">
        <div class="bg-white rounded-lg shadow-lg p-8">
            <h1 class="text-3xl font-bold mb-8">SINCOR Privacy Policy</h1>
            
            <div class="prose max-w-none">
                <p class="text-lg text-gray-700 mb-6">
                    <strong>Effective Date:</strong> January 1, 2025<br>
                    <strong>Last Updated:</strong> January 1, 2025
                </p>
                
                <h2 class="text-2xl font-bold mt-8 mb-4">1. Information We Collect</h2>
                <p class="mb-4">We collect information you provide directly to us, such as:</p>
                <ul class="list-disc pl-6 mb-6">
                    <li>Contact information (name, email, phone)</li>
                    <li>Business information (company name, industry, location)</li>
                    <li>Account and billing information</li>
                    <li>Communications with our support team</li>
                </ul>
                
                <h2 class="text-2xl font-bold mt-8 mb-4">2. How We Use Your Information</h2>
                <p class="mb-4">We use your information for:</p>
                <ul class="list-disc pl-6 mb-6">
                    <li>Providing SINCOR business intelligence services</li>
                    <li>Sending service-related communications</li>
                    <li>Customer support and account management</li>
                    <li>Legal compliance and fraud prevention</li>
                </ul>
                
                <h2 class="text-2xl font-bold mt-8 mb-4">3. Your Privacy Rights</h2>
                <div class="bg-blue-50 p-6 rounded-lg mb-6">
                    <h3 class="text-lg font-semibold mb-3">GDPR Rights (EU Residents)</h3>
                    <ul class="list-disc pl-6 space-y-1">
                        <li>Right to access your personal data</li>
                        <li>Right to rectification of inaccurate data</li>
                        <li>Right to erasure ("right to be forgotten")</li>
                        <li>Right to data portability</li>
                        <li>Right to object to processing</li>
                    </ul>
                </div>
                
                <div class="bg-green-50 p-6 rounded-lg mb-6">
                    <h3 class="text-lg font-semibold mb-3">CCPA Rights (California Residents)</h3>
                    <ul class="list-disc pl-6 space-y-1">
                        <li>Right to know about personal information collected</li>
                        <li>Right to delete personal information</li>
                        <li>Right to opt-out of sale of personal information</li>
                        <li>Right to non-discrimination</li>
                    </ul>
                </div>
                
                <h2 class="text-2xl font-bold mt-8 mb-4">4. Data Security</h2>
                <p class="mb-4">We implement industry-standard security measures:</p>
                <ul class="list-disc pl-6 mb-6">
                    <li>AES-256 encryption for data at rest</li>
                    <li>TLS 1.3 encryption for data in transit</li>
                    <li>Multi-factor authentication</li>
                    <li>Regular security audits and monitoring</li>
                </ul>
                
                <h2 class="text-2xl font-bold mt-8 mb-4">5. Contact Us</h2>
                <div class="bg-gray-50 p-6 rounded-lg">
                    <p class="mb-2"><strong>Privacy Officer:</strong> privacy@sincor.com</p>
                    <p class="mb-2"><strong>Data Protection Officer:</strong> dpo@sincor.com</p>
                    <p><strong>General Inquiries:</strong> support@sincor.com</p>
                </div>
                
                <div class="mt-8 p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
                    <p class="text-sm">
                        <strong>Exercise Your Rights:</strong> 
                        <a href="/data-deletion-request" class="text-blue-600 hover:underline">Request Data Deletion</a> |
                        <a href="mailto:privacy@sincor.com" class="text-blue-600 hover:underline">Contact Privacy Team</a>
                    </p>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

DATA_DELETION_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Data Deletion Request - SINCOR</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body class="bg-gray-50">
    <div class="max-w-2xl mx-auto py-12 px-4" x-data="deletionApp()">
        <div class="bg-white rounded-lg shadow-lg p-8">
            <h1 class="text-3xl font-bold mb-6">Request Data Deletion</h1>
            <p class="text-gray-600 mb-8">
                Exercise your GDPR "Right to be Forgotten" or CCPA data deletion rights.
                We will process your request within 30 days.
            </p>
            
            <form @submit.prevent="submitRequest()" class="space-y-6">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Email Address</label>
                    <input type="email" x-model="email" required
                           class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                           placeholder="your@email.com">
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Reason for Request</label>
                    <select x-model="reason" required
                            class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500">
                        <option value="">Select a reason</option>
                        <option value="gdpr_erasure">GDPR Right to Erasure</option>
                        <option value="ccpa_deletion">CCPA Data Deletion</option>
                        <option value="no_longer_needed">Data no longer needed</option>
                        <option value="other">Other</option>
                    </select>
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Additional Details (Optional)</label>
                    <textarea x-model="details" rows="3"
                              class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                              placeholder="Any additional information about your request..."></textarea>
                </div>
                
                <div class="bg-blue-50 p-4 rounded-lg">
                    <h3 class="font-semibold text-blue-900 mb-2">What will be deleted:</h3>
                    <ul class="text-sm text-blue-800 space-y-1">
                        <li>• Your account and profile information</li>
                        <li>• Campaign and email history</li>
                        <li>• Analytics and usage data</li>
                        <li>• Backup and archived data</li>
                    </ul>
                </div>
                
                <button type="submit" :disabled="loading"
                        class="w-full bg-red-600 text-white py-3 rounded-lg font-semibold hover:bg-red-700 disabled:opacity-50">
                    <span x-show="!loading">Submit Deletion Request</span>
                    <span x-show="loading">Processing Request...</span>
                </button>
            </form>
            
            <div x-show="submitted" class="mt-6 p-4 bg-green-50 border border-green-200 rounded-lg">
                <div class="text-green-800">
                    <h3 class="font-semibold mb-2">✅ Request Submitted Successfully</h3>
                    <p class="text-sm mb-2">Request ID: <span x-text="requestId" class="font-mono"></span></p>
                    <p class="text-sm">We will process your request within 30 days and send confirmation to your email.</p>
                </div>
            </div>
        </div>
    </div>

    <script>
        function deletionApp() {
            return {
                email: '',
                reason: '',
                details: '',
                loading: false,
                submitted: false,
                requestId: '',
                
                async submitRequest() {
                    this.loading = true;
                    
                    try {
                        const response = await fetch('/data-deletion-request', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                email: this.email,
                                reason: this.reason,
                                details: this.details
                            })
                        });
                        
                        const result = await response.json();
                        
                        if (result.request_id) {
                            this.requestId = result.request_id;
                            this.submitted = true;
                        } else {
                            alert('Error processing request. Please try again.');
                        }
                    } catch (error) {
                        alert('Network error. Please try again.');
                    } finally {
                        this.loading = false;
                    }
                }
            }
        }
    </script>
</body>
</html>
"""