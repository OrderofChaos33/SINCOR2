"""
SINCOR Promo Code System - For Prototype Testing
Allows bypassing paywall for select testers and friends
"""

import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

class PromoCodeSystem:
    """Manage promo codes for SINCOR prototype testing."""
    
    def __init__(self):
        self.db_path = Path(__file__).parent / "data" / "promo_codes.db"
        self.init_db()
        self.create_default_codes()
    
    def init_db(self):
        """Initialize promo codes database."""
        self.db_path.parent.mkdir(exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                description TEXT,
                discount_percent INTEGER DEFAULT 0,
                free_trial_days INTEGER DEFAULT 0,
                bypass_payment BOOLEAN DEFAULT FALSE,
                max_uses INTEGER DEFAULT 1,
                current_uses INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                created_by TEXT DEFAULT 'admin',
                active BOOLEAN DEFAULT TRUE
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS code_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                user_email TEXT,
                user_name TEXT,
                business_name TEXT,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    def create_default_codes(self):
        """Create default promo codes for testing."""
        default_codes = [
            {
                "code": "PROTOTYPE2025",
                "description": "Full free access for prototype testing - friends & select testers",
                "bypass_payment": True,
                "free_trial_days": 90,
                "max_uses": 10
            },
            {
                "code": "COURTTESTER",
                "description": "Court's personal testing account - unlimited access",
                "bypass_payment": True,
                "free_trial_days": 365,
                "max_uses": 5
            },
            {
                "code": "FRIENDSTEST",
                "description": "Friends and family testing - 3 months free",
                "bypass_payment": True,
                "free_trial_days": 90,
                "max_uses": 20
            },
            {
                "code": "SINCORVIP",
                "description": "VIP early access - 6 months free",
                "bypass_payment": True,
                "free_trial_days": 180,
                "max_uses": 5
            }
        ]
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for code_data in default_codes:
            cursor.execute("""
                INSERT OR IGNORE INTO promo_codes 
                (code, description, bypass_payment, free_trial_days, max_uses)
                VALUES (?, ?, ?, ?, ?)
            """, (
                code_data["code"],
                code_data["description"],
                code_data["bypass_payment"],
                code_data["free_trial_days"],
                code_data["max_uses"]
            ))
        
        conn.commit()
        conn.close()
    
    def validate_code(self, code):
        """Validate and return promo code details."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM promo_codes 
            WHERE code = ? AND active = TRUE 
            AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
        """, (code.upper(),))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return {"valid": False, "error": "Invalid or expired promo code"}
        
        # Convert to dict
        columns = ["id", "code", "description", "discount_percent", "free_trial_days", 
                  "bypass_payment", "max_uses", "current_uses", "created_at", "expires_at", 
                  "created_by", "active"]
        code_data = dict(zip(columns, result))
        
        # Check usage limits
        if code_data["current_uses"] >= code_data["max_uses"]:
            return {"valid": False, "error": "Promo code usage limit exceeded"}
        
        return {"valid": True, "data": code_data}
    
    def use_code(self, code, user_email="", user_name="", business_name="", ip_address=""):
        """Use a promo code and record usage."""
        validation = self.validate_code(code)
        if not validation["valid"]:
            return validation
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Record usage
        cursor.execute("""
            INSERT INTO code_usage (code, user_email, user_name, business_name, ip_address)
            VALUES (?, ?, ?, ?, ?)
        """, (code.upper(), user_email, user_name, business_name, ip_address))
        
        # Update usage count
        cursor.execute("""
            UPDATE promo_codes SET current_uses = current_uses + 1
            WHERE code = ?
        """, (code.upper(),))
        
        conn.commit()
        conn.close()
        
        return {"valid": True, "data": validation["data"], "message": "Promo code applied successfully"}
    
    def create_code(self, code, description, discount_percent=0, free_trial_days=0, 
                   bypass_payment=False, max_uses=1, expires_days=None):
        """Create a new promo code."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        expires_at = None
        if expires_days:
            expires_at = datetime.now() + timedelta(days=expires_days)
        
        try:
            cursor.execute("""
                INSERT INTO promo_codes 
                (code, description, discount_percent, free_trial_days, bypass_payment, max_uses, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (code.upper(), description, discount_percent, free_trial_days, 
                 bypass_payment, max_uses, expires_at))
            
            conn.commit()
            return {"success": True, "message": f"Promo code {code} created successfully"}
        except sqlite3.IntegrityError:
            return {"success": False, "error": "Promo code already exists"}
        finally:
            conn.close()
    
    def list_codes(self):
        """List all promo codes with usage stats."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT code, description, discount_percent, free_trial_days, bypass_payment,
                   max_uses, current_uses, created_at, expires_at, active
            FROM promo_codes
            ORDER BY created_at DESC
        """)
        
        codes = cursor.fetchall()
        conn.close()
        
        return [dict(zip(["code", "description", "discount_percent", "free_trial_days", 
                         "bypass_payment", "max_uses", "current_uses", "created_at", 
                         "expires_at", "active"], code)) for code in codes]
    
    def get_usage_stats(self):
        """Get usage statistics for all codes."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT code, user_email, user_name, business_name, used_at, ip_address
            FROM code_usage
            ORDER BY used_at DESC
        """)
        
        usage = cursor.fetchall()
        conn.close()
        
        return [dict(zip(["code", "user_email", "user_name", "business_name", 
                         "used_at", "ip_address"], record)) for record in usage]

# Global instance
promo_system = PromoCodeSystem()