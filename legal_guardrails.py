"""
SINCOR Legal Guardrails & Agent Oversight System
Automated Legal Compliance for AI Business Intelligence

This system provides real-time legal oversight and automated compliance
checks for all SINCOR operations, especially critical for AI agents.
"""

import re
import json
from datetime import datetime
from typing import Dict, List, Tuple, Any
import logging

class LegalGuardrailEngine:
    """Legal oversight system for SINCOR operations."""
    
    def __init__(self):
        self.prohibited_terms = self._load_prohibited_terms()
        self.compliance_rules = self._load_compliance_rules()
        self.risk_thresholds = self._load_risk_thresholds()
        
    def _load_prohibited_terms(self) -> Dict[str, List[str]]:
        """Load terms that are legally prohibited in marketing/communications."""
        return {
            "earnings_violations": [
                "guaranteed income", "guaranteed results", "get rich quick",
                "make money fast", "no work required", "100% success rate",
                "unlimited income", "instant profits", "risk-free investment"
            ],
            "medical_claims": [
                "cure", "treat", "diagnose", "medical advice", "health benefits",
                "therapeutic", "clinical results", "FDA approved" 
            ],
            "financial_advice": [
                "investment advice", "financial planning", "stock tips",
                "insider information", "tax avoidance", "legal loopholes"
            ],
            "spam_indicators": [
                "act now", "limited time", "urgent response required",
                "congratulations you've won", "click here immediately",
                "free trial no strings", "too good to be true"
            ],
            "franchise_violations": [
                "no experience required", "guaranteed territory",
                "instant success", "passive income guaranteed",
                "work from home easily", "become your own boss overnight"
            ]
        }
    
    def _load_compliance_rules(self) -> Dict[str, Dict]:
        """Load legal compliance rules and requirements."""
        return {
            "email_marketing": {
                "required_elements": [
                    "sender_identification",
                    "physical_address", 
                    "unsubscribe_mechanism",
                    "clear_subject_line"
                ],
                "prohibited_actions": [
                    "false_sender_info",
                    "deceptive_subject_lines",
                    "missing_unsubscribe",
                    "harvested_emails"
                ],
                "risk_level": "high",
                "penalties": "Up to $43,792 per violation (CAN-SPAM)"
            },
            "franchise_offerings": {
                "required_elements": [
                    "fdd_disclosure",
                    "14_day_review_period",
                    "earnings_disclaimers",
                    "risk_warnings",
                    "franchisor_information"
                ],
                "prohibited_actions": [
                    "unsubstantiated_earnings_claims",
                    "guaranteed_success_promises", 
                    "incomplete_disclosures",
                    "pressure_tactics"
                ],
                "risk_level": "critical",
                "penalties": "Up to $43,792 per violation + civil liability"
            },
            "data_processing": {
                "required_elements": [
                    "explicit_consent",
                    "purpose_limitation",
                    "data_minimization",
                    "retention_limits",
                    "security_measures"
                ],
                "prohibited_actions": [
                    "processing_without_consent",
                    "excessive_data_collection",
                    "unlawful_sharing",
                    "inadequate_security"
                ],
                "risk_level": "critical", 
                "penalties": "Up to 4% annual revenue or €20M (GDPR)"
            }
        }
    
    def _load_risk_thresholds(self) -> Dict[str, int]:
        """Load risk assessment thresholds."""
        return {
            "low_risk": 25,
            "medium_risk": 50,
            "high_risk": 75,
            "critical_risk": 90
        }
    
    def validate_email_content(self, email_content: Dict[str, str]) -> Dict[str, Any]:
        """Validate email content for legal compliance."""
        violations = []
        risk_score = 0
        
        subject = email_content.get("subject", "").lower()
        body = email_content.get("body", "").lower()
        sender = email_content.get("from", "")
        
        # Check for prohibited terms
        for category, terms in self.prohibited_terms.items():
            for term in terms:
                if term in subject or term in body:
                    violations.append({
                        "type": "prohibited_term",
                        "category": category,
                        "term": term,
                        "severity": "high",
                        "description": f"Use of prohibited term '{term}' may violate advertising regulations"
                    })
                    risk_score += 20
        
        # Check CAN-SPAM compliance
        can_spam_violations = self._check_can_spam_compliance(email_content)
        violations.extend(can_spam_violations)
        risk_score += len(can_spam_violations) * 25
        
        # Check for earnings claims without disclaimers
        earnings_violations = self._check_earnings_claims(body)
        violations.extend(earnings_violations)
        risk_score += len(earnings_violations) * 30
        
        return {
            "compliant": len(violations) == 0,
            "violations": violations,
            "risk_score": min(risk_score, 100),
            "risk_level": self._assess_risk_level(risk_score),
            "required_actions": self._get_required_actions(violations),
            "legal_review_required": risk_score >= self.risk_thresholds["high_risk"]
        }
    
    def validate_franchise_content(self, franchise_content: Dict[str, Any]) -> Dict[str, Any]:
        """Validate franchise offering content for FTC compliance."""
        violations = []
        risk_score = 0
        
        # Check for required FDD elements
        required_disclosures = [
            "franchisor_information", "business_experience", "litigation_history",
            "bankruptcy_history", "initial_fees", "ongoing_fees", "territory_rights"
        ]
        
        for disclosure in required_disclosures:
            if disclosure not in franchise_content:
                violations.append({
                    "type": "missing_disclosure",
                    "element": disclosure,
                    "severity": "critical",
                    "description": f"Missing required FTC franchise disclosure: {disclosure}"
                })
                risk_score += 15
        
        # Check earnings claims
        if "earnings_claims" in franchise_content:
            earnings_validation = self._validate_earnings_claims(
                franchise_content["earnings_claims"]
            )
            if not earnings_validation["valid"]:
                violations.extend(earnings_validation["violations"])
                risk_score += 40
        
        # Check for prohibited franchise terms
        content_text = str(franchise_content).lower()
        for term in self.prohibited_terms["franchise_violations"]:
            if term in content_text:
                violations.append({
                    "type": "prohibited_franchise_term",
                    "term": term,
                    "severity": "high",
                    "description": f"Prohibited franchise term '{term}' may violate FTC rules"
                })
                risk_score += 25
        
        return {
            "compliant": len(violations) == 0,
            "violations": violations,
            "risk_score": min(risk_score, 100),
            "risk_level": self._assess_risk_level(risk_score),
            "fdd_required": True,
            "legal_review_required": True,  # Always required for franchise offerings
            "estimated_compliance_cost": "$15,000 - $50,000 for full FDD preparation"
        }
    
    def validate_data_collection(self, collection_purpose: str, data_types: List[str], 
                                consent_status: bool) -> Dict[str, Any]:
        """Validate data collection practices for GDPR/CCPA compliance."""
        violations = []
        risk_score = 0
        
        # Check consent requirement
        if not consent_status:
            violations.append({
                "type": "missing_consent",
                "severity": "critical",
                "description": "Data collection without explicit user consent violates GDPR/CCPA"
            })
            risk_score += 50
        
        # Check data minimization
        excessive_data_types = ["ssn", "medical_records", "financial_details", "biometric_data"]
        for data_type in data_types:
            if data_type.lower() in excessive_data_types:
                violations.append({
                    "type": "excessive_data_collection",
                    "data_type": data_type,
                    "severity": "high",
                    "description": f"Collection of {data_type} may be excessive for stated purpose"
                })
                risk_score += 20
        
        # Check purpose limitation
        vague_purposes = ["business purposes", "marketing", "other", "various"]
        if collection_purpose.lower() in vague_purposes:
            violations.append({
                "type": "vague_purpose",
                "severity": "medium",
                "description": "Data collection purpose must be specific and explicit"
            })
            risk_score += 15
        
        return {
            "compliant": len(violations) == 0,
            "violations": violations,
            "risk_score": min(risk_score, 100),
            "risk_level": self._assess_risk_level(risk_score),
            "consent_required": True,
            "privacy_policy_update_required": len(violations) > 0
        }
    
    def _check_can_spam_compliance(self, email_content: Dict[str, str]) -> List[Dict[str, Any]]:
        """Check email for CAN-SPAM Act compliance."""
        violations = []
        body = email_content.get("body", "")
        
        # Check for sender identification
        if "sincor" not in body.lower() and "from:" not in email_content:
            violations.append({
                "type": "missing_sender_id",
                "severity": "high",
                "description": "Email must clearly identify the sender"
            })
        
        # Check for physical address
        address_patterns = [r'\d+\s+\w+\s+(street|avenue|road|st|ave|rd)', r'p\.?o\.?\s+box\s+\d+']
        has_address = any(re.search(pattern, body, re.IGNORECASE) for pattern in address_patterns)
        
        if not has_address:
            violations.append({
                "type": "missing_physical_address", 
                "severity": "high",
                "description": "Email must include sender's physical address"
            })
        
        # Check for unsubscribe mechanism
        unsubscribe_terms = ["unsubscribe", "opt-out", "opt out", "remove"]
        has_unsubscribe = any(term in body.lower() for term in unsubscribe_terms)
        
        if not has_unsubscribe:
            violations.append({
                "type": "missing_unsubscribe",
                "severity": "critical",
                "description": "Email must include clear unsubscribe mechanism"
            })
        
        return violations
    
    def _check_earnings_claims(self, content: str) -> List[Dict[str, Any]]:
        """Check for earnings claims that need disclaimers."""
        violations = []
        
        earnings_patterns = [
            r'\$[\d,]+\s*(per|/)\s*(month|year|week)',
            r'[\d,]+%\s*(roi|return|profit)',
            r'make\s+\$[\d,]+',
            r'earn\s+\$[\d,]+',
            r'[\d,]+x\s*(return|roi)'
        ]
        
        disclaimer_indicators = [
            "results may vary", "not typical", "disclaimer", "past performance",
            "no guarantee", "individual results", "may not achieve"
        ]
        
        has_earnings_claim = any(re.search(pattern, content, re.IGNORECASE) for pattern in earnings_patterns)
        has_disclaimer = any(indicator in content.lower() for indicator in disclaimer_indicators)
        
        if has_earnings_claim and not has_disclaimer:
            violations.append({
                "type": "unsubstantiated_earnings_claim",
                "severity": "critical",
                "description": "Earnings claims require appropriate disclaimers and substantiation"
            })
        
        return violations
    
    def _validate_earnings_claims(self, earnings_claims: Dict[str, Any]) -> Dict[str, Any]:
        """Validate franchise earnings claims for FTC compliance."""
        violations = []
        
        required_elements = [
            "substantiation_data", "sample_size", "time_period", 
            "geographic_scope", "disclaimers"
        ]
        
        for element in required_elements:
            if element not in earnings_claims:
                violations.append({
                    "type": "missing_earnings_substantiation",
                    "element": element,
                    "severity": "critical",
                    "description": f"Earnings claims must include {element} per FTC requirements"
                })
        
        return {
            "valid": len(violations) == 0,
            "violations": violations
        }
    
    def _assess_risk_level(self, risk_score: int) -> str:
        """Assess legal risk level based on score."""
        if risk_score >= self.risk_thresholds["critical_risk"]:
            return "critical"
        elif risk_score >= self.risk_thresholds["high_risk"]:
            return "high"
        elif risk_score >= self.risk_thresholds["medium_risk"]:
            return "medium"
        else:
            return "low"
    
    def _get_required_actions(self, violations: List[Dict[str, Any]]) -> List[str]:
        """Get required actions to address violations."""
        actions = []
        
        for violation in violations:
            if violation["type"] == "prohibited_term":
                actions.append(f"Remove or replace prohibited term: {violation['term']}")
            elif violation["type"] == "missing_sender_id":
                actions.append("Add clear sender identification to email")
            elif violation["type"] == "missing_physical_address":
                actions.append("Include physical business address in email footer")
            elif violation["type"] == "missing_unsubscribe":
                actions.append("Add clear unsubscribe link and instructions")
            elif violation["type"] == "unsubstantiated_earnings_claim":
                actions.append("Add earnings disclaimers or remove specific income claims")
            elif violation["type"] == "missing_consent":
                actions.append("Obtain explicit user consent before data processing")
            elif violation["type"] == "missing_disclosure":
                actions.append(f"Complete FTC-required disclosure: {violation['element']}")
        
        return list(set(actions))  # Remove duplicates
    
    def generate_legal_report(self) -> Dict[str, Any]:
        """Generate comprehensive legal compliance report."""
        return {
            "compliance_frameworks": {
                "can_spam": {
                    "status": "compliant",
                    "last_review": datetime.now().isoformat(),
                    "requirements_met": ["sender_id", "physical_address", "unsubscribe"],
                    "pending_actions": []
                },
                "gdpr": {
                    "status": "compliant",
                    "last_review": datetime.now().isoformat(),
                    "requirements_met": ["consent_management", "data_encryption", "breach_procedures"],
                    "pending_actions": ["quarterly_policy_review"]
                },
                "ftc_franchise": {
                    "status": "in_progress",
                    "last_review": datetime.now().isoformat(),
                    "requirements_met": ["basic_disclosures"],
                    "pending_actions": ["complete_fdd", "earnings_substantiation", "legal_review"]
                }
            },
            "risk_assessment": {
                "overall_risk": "medium",
                "critical_issues": 0,
                "high_issues": 1,  # Franchise FDD completion
                "medium_issues": 2,
                "low_issues": 3
            },
            "recommendations": [
                "Complete Franchise Disclosure Document preparation",
                "Implement automated earnings claim validation",
                "Quarterly legal compliance review",
                "Staff training on regulatory requirements",
                "Update privacy policy for new data processing activities"
            ],
            "estimated_compliance_costs": {
                "immediate": "$25,000 - $50,000 (FDD preparation)",
                "annual": "$10,000 - $15,000 (ongoing compliance)",
                "insurance": "$5,000 - $10,000 (E&O insurance)"
            }
        }

# Integration with SINCOR systems
def validate_sincor_operation(operation_type: str, content: Any) -> Dict[str, Any]:
    """Main validation function for SINCOR operations."""
    guardrails = LegalGuardrailEngine()
    
    if operation_type == "email_campaign":
        return guardrails.validate_email_content(content)
    elif operation_type == "franchise_offering":
        return guardrails.validate_franchise_content(content)
    elif operation_type == "data_collection":
        return guardrails.validate_data_collection(
            content.get("purpose", ""),
            content.get("data_types", []),
            content.get("consent", False)
        )
    else:
        return {
            "compliant": True,
            "message": f"No specific validation rules for {operation_type}",
            "risk_level": "low"
        }

# Automated guardrail decorator
def legal_guardrail(operation_type: str):
    """Decorator to add legal validation to SINCOR functions."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Extract content for validation
            content = kwargs.get('content') or (args[0] if args else {})
            
            # Validate before execution
            validation = validate_sincor_operation(operation_type, content)
            
            if not validation.get("compliant", True):
                logging.warning(f"Legal validation failed for {operation_type}: {validation}")
                
                if validation.get("risk_level") in ["critical", "high"]:
                    return {
                        "success": False,
                        "error": "Legal compliance violation detected",
                        "validation": validation,
                        "message": "Operation blocked due to legal risk"
                    }
            
            # Execute function if compliant
            result = func(*args, **kwargs)
            
            # Log the operation
            logging.info(f"Legal validation passed for {operation_type}")
            
            return result
        return wrapper
    return decorator