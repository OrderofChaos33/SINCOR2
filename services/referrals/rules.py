from datetime import datetime, timedelta
import hashlib
import json
from typing import Dict, Optional

class ReferralRules:
    def __init__(self, cookie_days: int = 30):
        self.cookie_days = cookie_days
    
    def generate_fingerprint(self, ip: str, user_agent: str, additional_data: Dict = None) -> str:
        """Generate a browser fingerprint for attribution"""
        data = f"{ip}:{user_agent}"
        if additional_data:
            data += f":{json.dumps(additional_data, sort_keys=True)}"
        
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    def calculate_payout(self, ref_code_data: Dict, conversion_amount_cents: int) -> int:
        """Calculate referral payout based on rules"""
        if ref_code_data['payout_type'] == 'fixed':
            return ref_code_data['payout_cents']
        elif ref_code_data['payout_type'] == 'percentage':
            # payout_cents represents percentage (e.g., 1000 = 10%)
            percentage = ref_code_data['payout_cents'] / 10000  # 1000 cents = 10%
            return int(conversion_amount_cents * percentage)
        else:
            return 0
    
    def is_valid_attribution(self, attribution_data: Dict, ref_code_data: Dict) -> bool:
        """Check if attribution is valid for payout"""
        
        # Check if ref code is active
        if not ref_code_data.get('active', False):
            return False
        
        # Check expiration
        if ref_code_data.get('expires_at'):
            expires = datetime.fromisoformat(ref_code_data['expires_at'])
            if datetime.now() > expires:
                return False
        
        # Check attribution window (30 days by default)
        first_seen = datetime.fromisoformat(attribution_data['first_seen'])
        if datetime.now() > first_seen + timedelta(days=self.cookie_days):
            return False
        
        return True
    
    def should_override_attribution(self, existing_code: str, new_code: str, 
                                  existing_date: datetime, new_date: datetime) -> bool:
        """Determine if a new referral code should override existing attribution"""
        
        # Last-touch attribution: newer referral wins
        if new_date > existing_date:
            return True
        
        # Could add more sophisticated rules:
        # - Higher value codes override lower ones
        # - Direct campaigns override generic ones
        # - Paid campaigns override organic ones
        
        return False
    
    def generate_tracking_script(self, ref_code: str, domain: str = "") -> str:
        """Generate embeddable JavaScript for referral tracking"""
        return f"""
        <script>
        (function() {{
            // Set referral cookie
            function setCookie(name, value, days) {{
                var expires = "";
                if (days) {{
                    var date = new Date();
                    date.setTime(date.getTime() + (days*24*60*60*1000));
                    expires = "; expires=" + date.toUTCString();
                }}
                document.cookie = name + "=" + (value || "")  + expires + "; path=/; SameSite=Lax";
            }}
            
            // Get browser fingerprint data
            function getFingerprint() {{
                return btoa(JSON.stringify({{
                    screen: screen.width + 'x' + screen.height,
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    language: navigator.language,
                    platform: navigator.platform
                }}));
            }}
            
            // Track the referral
            setCookie('ref', '{ref_code}', {self.cookie_days});
            setCookie('ref_fp', getFingerprint(), {self.cookie_days});
            setCookie('ref_ts', new Date().toISOString(), {self.cookie_days});
            
            // Send tracking pixel
            var img = new Image();
            img.src = '{domain}/ref/pixel?code={ref_code}&fp=' + getFingerprint();
            
            console.log('Referral tracked: {ref_code}');
        }})();
        </script>
        """