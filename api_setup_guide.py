#!/usr/bin/env python3
"""
API Setup Guide for SINCOR
Complete configuration guide for all required APIs
"""

def get_api_setup_guide():
    """Return complete API setup instructions."""
    return {
        "google_calendar": {
            "name": "Google Calendar Integration",
            "required_keys": ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"],
            "setup_steps": [
                "1. Go to Google Cloud Console (console.cloud.google.com)",
                "2. Create new project or select existing",
                "3. Enable Google Calendar API",
                "4. Create OAuth 2.0 credentials",
                "5. Add https://getsincor.com to authorized origins",
                "6. Add callback URL: https://getsincor.com/auth/google/callback",
                "7. Copy Client ID and Client Secret to environment"
            ],
            "cost": "Free with usage limits"
        },
        
        "google_places": {
            "name": "Google Places API (Lead Generation)",
            "required_keys": ["GOOGLE_API_KEY"],
            "setup_steps": [
                "1. Same Google Cloud Console project",
                "2. Enable Google Places API",
                "3. Create API Key (not OAuth)",
                "4. Restrict key to Places API only",
                "5. Add key to GOOGLE_API_KEY environment variable"
            ],
            "cost": "$17 per 1000 searches (first $200/month free)"
        },
        
        "stripe_payments": {
            "name": "Stripe Payment Processing", 
            "required_keys": ["STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"],
            "setup_steps": [
                "1. Sign up at stripe.com",
                "2. Complete business verification",
                "3. Get API keys from Dashboard > API keys",
                "4. Use test keys for development",
                "5. Switch to live keys for production"
            ],
            "cost": "2.9% + 30¢ per transaction"
        },
        
        "email_automation": {
            "name": "Email SMTP Configuration",
            "required_keys": ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"],
            "setup_steps": [
                "Gmail: Enable 2FA, create App Password",
                "Outlook: Similar 2FA + App Password process",
                "Custom: Get SMTP settings from your provider",
                "Add credentials to environment variables"
            ],
            "cost": "Usually free with email provider"
        },
        
        "sms_automation": {
            "name": "Twilio SMS Integration",
            "required_keys": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"],
            "setup_steps": [
                "1. Sign up at twilio.com",
                "2. Verify your phone number", 
                "3. Buy a phone number ($1/month)",
                "4. Get Account SID and Auth Token from dashboard",
                "5. Add to environment variables"
            ],
            "cost": "$1/month + $0.0075 per SMS"
        },
        
        "yelp_business_data": {
            "name": "Yelp Fusion API (Optional)",
            "required_keys": ["YELP_API_KEY"],
            "setup_steps": [
                "1. Create Yelp developer account",
                "2. Create new app at yelp.com/developers",
                "3. Get API key from app dashboard",
                "4. Add to YELP_API_KEY environment"
            ],
            "cost": "Free with rate limits"
        }
    }

def check_current_configuration():
    """Check which APIs are currently configured."""
    import os
    
    apis = get_api_setup_guide()
    status = {}
    
    for api_name, config in apis.items():
        required_keys = config["required_keys"]
        configured_keys = [key for key in required_keys if os.getenv(key)]
        
        status[api_name] = {
            "name": config["name"],
            "configured": len(configured_keys) == len(required_keys),
            "missing_keys": [key for key in required_keys if not os.getenv(key)],
            "configured_keys": configured_keys,
            "cost": config["cost"]
        }
    
    return status

def get_priority_setup_order():
    """Get recommended setup order based on impact."""
    return [
        ("google_places", "Highest Priority - Enables real lead generation"),
        ("email_automation", "High Priority - Needed for lead outreach"),
        ("google_calendar", "Medium Priority - For appointment booking"),
        ("stripe_payments", "Medium Priority - For payment processing"),
        ("sms_automation", "Low Priority - Additional communication channel"),
        ("yelp_business_data", "Optional - Additional lead sources")
    ]

if __name__ == "__main__":
    print("SINCOR API Configuration Status")
    print("=" * 50)
    
    status = check_current_configuration()
    priorities = get_priority_setup_order()
    
    for api_key, description in priorities:
        api_status = status[api_key]
        status_icon = "✅" if api_status["configured"] else "❌"
        
        print(f"{status_icon} {api_status['name']}")
        print(f"   Priority: {description}")
        print(f"   Cost: {api_status['cost']}")
        
        if not api_status["configured"]:
            print(f"   Missing: {', '.join(api_status['missing_keys'])}")
        
        print()