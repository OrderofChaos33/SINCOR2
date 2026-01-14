#!/usr/bin/env python3
"""
SINCOR Environment Setup Script

Interactive script to help configure environment variables for SINCOR.
Generates appropriate .env files based on deployment type.
"""

import os
import secrets
import string
from pathlib import Path
from datetime import datetime

class EnvironmentSetup:
    """Interactive environment configuration setup."""
    
    def __init__(self):
        self.root_path = Path(__file__).parent
        self.config_dir = self.root_path / "config"
        self.env_vars = {}
    
    def generate_secret_key(self, length=32):
        """Generate a secure random secret key."""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    def prompt_user(self, prompt, default="", required=False, password=False):
        """Prompt user for input with validation."""
        while True:
            if default:
                display_prompt = f"{prompt} [{default}]: "
            else:
                display_prompt = f"{prompt}: "
            
            if password:
                import getpass
                value = getpass.getpass(display_prompt)
            else:
                value = input(display_prompt).strip()
            
            if not value:
                value = default
            
            if required and not value:
                print("This field is required. Please provide a value.")
                continue
            
            return value
    
    def setup_development_environment(self):
        """Set up development environment configuration."""
        print("\n🛠️  Development Environment Setup")
        print("=" * 40)
        
        # Flask configuration
        print("\n📍 Flask Configuration")
        use_generated_key = self.prompt_user(
            "Generate secure Flask secret key automatically? (y/n)", 
            "y"
        ).lower() == 'y'
        
        if use_generated_key:
            self.env_vars['FLASK_SECRET_KEY'] = self.generate_secret_key()
            print("✅ Secure Flask secret key generated")
        else:
            self.env_vars['FLASK_SECRET_KEY'] = self.prompt_user(
                "Enter Flask secret key", 
                "sincor-dev-secret-key-2025"
            )
        
        self.env_vars['FLASK_ENV'] = 'development'
        
        # Email configuration (optional for dev)
        print("\n📧 Email Configuration (Optional for Development)")
        setup_email = self.prompt_user(
            "Configure email settings? (y/n)", 
            "n"
        ).lower() == 'y'
        
        if setup_email:
            self.env_vars['SMTP_HOST'] = self.prompt_user("SMTP Host", "smtp.gmail.com")
            self.env_vars['SMTP_PORT'] = self.prompt_user("SMTP Port", "587")
            self.env_vars['SMTP_USER'] = self.prompt_user("SMTP Username/Email", required=True)
            self.env_vars['SMTP_PASS'] = self.prompt_user("SMTP Password/App Password", password=True, required=True)
            self.env_vars['EMAIL_FROM'] = self.prompt_user("From Email", self.env_vars['SMTP_USER'])
            self.env_vars['EMAIL_TO'] = self.prompt_user("To Email(s) (comma separated)", "admin@sincor-dev.local")
        else:
            # Set empty values so emails are saved as .eml files
            self.env_vars.update({
                'SMTP_HOST': '',
                'SMTP_USER': '',
                'SMTP_PASS': '',
                'EMAIL_FROM': 'noreply@sincor-dev.local',
                'EMAIL_TO': 'admin@sincor-dev.local'
            })
        
        self.env_vars['NOTIFY_PHONE'] = self.prompt_user("Notification Phone", "+15551234567")
        
        # API Keys (optional for dev)
        print("\n🔑 API Keys (Optional for Development)")
        setup_apis = self.prompt_user(
            "Configure API keys? (y/n)", 
            "n"
        ).lower() == 'y'
        
        if setup_apis:
            self.env_vars['GOOGLE_API_KEY'] = self.prompt_user("Google Places API Key")
            self.env_vars['YELP_API_KEY'] = self.prompt_user("Yelp API Key")
            self.env_vars['STRIPE_SECRET_KEY'] = self.prompt_user("Stripe Secret Key (test)")
            self.env_vars['STRIPE_PUBLISHABLE_KEY'] = self.prompt_user("Stripe Publishable Key (test)")
        else:
            self.env_vars.update({
                'GOOGLE_API_KEY': '',
                'YELP_API_KEY': '',
                'STRIPE_SECRET_KEY': 'sk_test_placeholder',
                'STRIPE_PUBLISHABLE_KEY': 'pk_test_placeholder'
            })
        
        # Development settings
        self.env_vars.update({
            'PORT': '5000',
            'LOG_LEVEL': 'DEBUG',
            'DATABASE_URL': 'sqlite:///data/sincor_main.db',
            'BUSINESS_SEARCH_RADIUS': '25000',
            'RATE_LIMIT_DELAY': '0.5',
            'MAX_DAILY_API_CALLS': '100'
        })
    
    def setup_production_environment(self):
        """Set up production environment configuration."""
        print("\n🚀 Production Environment Setup")
        print("=" * 40)
        print("⚠️  WARNING: This will configure production settings!")
        print("Make sure you have all required credentials ready.")
        
        confirm = self.prompt_user("\nContinue with production setup? (y/n)", "n")
        if confirm.lower() != 'y':
            print("Production setup cancelled.")
            return False
        
        # Flask configuration
        print("\n📍 Flask Configuration")
        self.env_vars['FLASK_SECRET_KEY'] = self.generate_secret_key(64)  # Longer key for production
        self.env_vars['FLASK_ENV'] = 'production'
        print("✅ Secure Flask secret key generated (64 characters)")
        
        # Email configuration (required for production)
        print("\n📧 Email Configuration (Required)")
        self.env_vars['SMTP_HOST'] = self.prompt_user("SMTP Host", "smtp.gmail.com", required=True)
        self.env_vars['SMTP_PORT'] = self.prompt_user("SMTP Port", "587")
        self.env_vars['SMTP_USER'] = self.prompt_user("SMTP Username/Email", required=True)
        self.env_vars['SMTP_PASS'] = self.prompt_user("SMTP Password/App Password", password=True, required=True)
        self.env_vars['EMAIL_FROM'] = self.prompt_user("From Email", self.env_vars['SMTP_USER'])
        self.env_vars['EMAIL_TO'] = self.prompt_user("To Email(s) (comma separated)", required=True)
        self.env_vars['NOTIFY_PHONE'] = self.prompt_user("Notification Phone", required=True)
        
        # API Keys (recommended for production)
        print("\n🔑 API Keys (Recommended)")
        setup_apis = self.prompt_user(
            "Configure API keys for full functionality? (y/n)", 
            "y"
        ).lower() == 'y'
        
        if setup_apis:
            self.env_vars['GOOGLE_API_KEY'] = self.prompt_user("Google Places API Key")
            self.env_vars['YELP_API_KEY'] = self.prompt_user("Yelp API Key")
            
            # Stripe (optional)
            setup_stripe = self.prompt_user("Configure Stripe for payments? (y/n)", "y").lower() == 'y'
            if setup_stripe:
                print("⚠️  Use LIVE keys for production!")
                self.env_vars['STRIPE_SECRET_KEY'] = self.prompt_user("Stripe SECRET Key (sk_live_...)", password=True)
                self.env_vars['STRIPE_PUBLISHABLE_KEY'] = self.prompt_user("Stripe PUBLISHABLE Key (pk_live_...)")
        
        # Production settings
        self.env_vars.update({
            'PORT': '5000',
            'LOG_LEVEL': 'INFO',
            'DATABASE_URL': 'sqlite:///data/sincor_main.db',
            'BUSINESS_SEARCH_RADIUS': '50000',
            'RATE_LIMIT_DELAY': '1',
            'MAX_DAILY_API_CALLS': '1000'
        })
        
        return True
    
    def write_env_file(self, filename, is_production=False):
        """Write environment variables to file."""
        file_path = self.config_dir / filename
        
        # Create backup if file exists
        if file_path.exists():
            backup_path = file_path.with_suffix(f'.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}')
            file_path.rename(backup_path)
            print(f"📄 Existing file backed up to: {backup_path.name}")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(f"# SINCOR {'Production' if is_production else 'Development'} Environment Configuration\n")
            f.write(f"# Generated on {datetime.now().isoformat()}\n")
            if is_production:
                f.write("# IMPORTANT: Keep these values secure and private!\n")
            f.write("\n")
            
            # Group related variables
            groups = {
                "Flask Configuration": ["FLASK_SECRET_KEY", "FLASK_ENV"],
                "Email Configuration": ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "EMAIL_FROM", "EMAIL_TO", "NOTIFY_PHONE"],
                "API Keys": ["GOOGLE_API_KEY", "YELP_API_KEY", "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"],
                "Application Settings": ["PORT", "LOG_LEVEL", "DATABASE_URL", "BUSINESS_SEARCH_RADIUS", "RATE_LIMIT_DELAY", "MAX_DAILY_API_CALLS"]
            }
            
            for group_name, vars_in_group in groups.items():
                f.write(f"# {group_name}\n")
                for var in vars_in_group:
                    if var in self.env_vars:
                        f.write(f"{var}={self.env_vars[var]}\n")
                f.write("\n")
        
        print(f"✅ Environment file written to: {file_path}")
        
        # Set file permissions (read/write for owner only if production)
        if is_production:
            try:
                os.chmod(file_path, 0o600)
                print("🔒 File permissions set to owner-only (600)")
            except:
                print("⚠️  Could not set restrictive file permissions")
    
    def run_setup(self):
        """Run the interactive environment setup."""
        print("🔧 SINCOR Environment Configuration Setup")
        print("=" * 50)
        
        print("\nThis script will help you configure environment variables for SINCOR.")
        print("You can set up either development or production configurations.\n")
        
        # Choose environment type
        env_type = self.prompt_user(
            "Environment type (development/production)", 
            "development"
        ).lower()
        
        if env_type.startswith('p'):  # production
            if not self.setup_production_environment():
                return
            filename = "production.env"
            is_production = True
        else:  # development
            self.setup_development_environment()
            filename = ".env"
            is_production = False
        
        # Write configuration file
        print(f"\n💾 Saving Configuration")
        self.write_env_file(filename, is_production)
        
        # Provide next steps
        print("\n🎉 Setup Complete!")
        print("=" * 20)
        print(f"✅ Configuration saved to: config/{filename}")
        
        if is_production:
            print("\n🚀 Production Next Steps:")
            print("1. Verify all settings in config/production.env")
            print("2. Test the configuration: python test_crash_diagnostics.py")
            print("3. Run final validation: python run_final_checks.py")
            print("4. Deploy when all tests pass")
        else:
            print("\n🛠️  Development Next Steps:")
            print("1. Start the application: python sincor_app.py")
            print("2. Test functionality at http://localhost:5000")
            print("3. Run diagnostics if issues occur: python test_crash_diagnostics.py")
        
        print(f"\n📝 To reconfigure, run this script again or edit config/{filename} manually")


if __name__ == "__main__":
    setup = EnvironmentSetup()
    setup.run_setup()