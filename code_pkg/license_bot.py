#!/usr/bin/env python3
"""
LICENSE BOT - Converts demo interest into paid subscriptions
Handles payment processing, subscription setup, and license activation
Uses CAD proof metrics to justify pricing and build urgency
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from commons.event_contracts import EventEnvelope, ResultSchema
from loguru import logger
import sqlite3
import uuid

class LicenseBot:
    def __init__(self):
        self.db_path = "license_transactions.db"
        self.base_price = 49.0  # Monthly subscription
        self.setup_fee = 99.0   # One-time setup
        self.init_database()
    
    def init_database(self):
        """Initialize license transaction database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS license_transactions (
                transaction_id TEXT PRIMARY KEY,
                tenant_id TEXT,
                payment_status TEXT,
                subscription_tier TEXT,
                monthly_amount REAL,
                setup_fee REAL,
                payment_method TEXT,
                created_at TEXT,
                activated_at TEXT,
                trial_end_date TEXT,
                cancellation_date TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payment_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT,
                attempt_number INTEGER,
                payment_processor TEXT,
                amount REAL,
                status TEXT,
                error_message TEXT,
                attempted_at TEXT,
                FOREIGN KEY (transaction_id) REFERENCES license_transactions (transaction_id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    async def handle_license_request(self, envelope: EventEnvelope) -> ResultSchema:
        """Process license purchase request"""
        business_data = envelope.payload.get("business_data", {})
        payment_data = envelope.payload.get("payment_data", {})
        
        if not business_data or not payment_data:
            return ResultSchema(
                ok=False,
                reason="Missing business or payment data for license activation"
            )
        
        # Create transaction record
        transaction_id = str(uuid.uuid4())
        transaction_result = await self.create_license_transaction(
            transaction_id, 
            envelope.tenant_id,
            business_data,
            payment_data
        )
        
        if not transaction_result["success"]:
            return ResultSchema(
                ok=False,
                reason=f"Transaction creation failed: {transaction_result['error']}",
                outputs={"transaction_id": transaction_id}
            )
        
        # Process payment
        payment_result = await self.process_payment(transaction_id, payment_data)
        
        if not payment_result["success"]:
            await self.log_payment_attempt(transaction_id, payment_data, payment_result)
            return ResultSchema(
                ok=False,
                reason=f"Payment processing failed: {payment_result['error']}",
                outputs={"transaction_id": transaction_id, "retry_allowed": True}
            )
        
        # Activate license
        activation_result = await self.activate_license(transaction_id, business_data)
        
        return ResultSchema(
            ok=True,
            reason="License activated successfully",
            outputs={
                "transaction_id": transaction_id,
                "license_key": activation_result["license_key"],
                "access_url": activation_result["access_url"],
                "onboarding_url": f"https://setup.getsincor.com/{transaction_id}",
                "next_billing_date": activation_result["next_billing_date"]
            }
        )
    
    async def create_license_transaction(self, transaction_id: str, tenant_id: str, 
                                       business_data: Dict, payment_data: Dict) -> Dict:
        """Create new license transaction record"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Determine pricing based on business size/niche
            monthly_amount = self.calculate_pricing(business_data)
            setup_fee = self.setup_fee if business_data.get("new_customer", True) else 0.0
            
            cursor.execute("""
                INSERT INTO license_transactions 
                (transaction_id, tenant_id, payment_status, subscription_tier, 
                 monthly_amount, setup_fee, payment_method, created_at, trial_end_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                transaction_id,
                tenant_id,
                "pending",
                "standard",
                monthly_amount,
                setup_fee,
                payment_data.get("method", "card"),
                datetime.now().isoformat(),
                (datetime.now() + timedelta(days=7)).isoformat()  # 7-day trial
            ))
            
            conn.commit()
            conn.close()
            
            return {"success": True, "monthly_amount": monthly_amount, "setup_fee": setup_fee}
            
        except Exception as e:
            logger.error(f"Transaction creation failed: {e}")
            return {"success": False, "error": str(e)}
    
    def calculate_pricing(self, business_data: Dict) -> float:
        """Calculate pricing based on business characteristics"""
        base_price = self.base_price
        
        # Premium niches pay more
        premium_niches = ["roofing", "plumbing", "hvac", "electrical"]
        if business_data.get("niche", "").lower() in premium_niches:
            base_price *= 1.5
        
        # Larger markets pay more
        market_size = business_data.get("market_size", "small")
        if market_size == "large":
            base_price *= 2.0
        elif market_size == "medium":
            base_price *= 1.3
        
        return round(base_price, 2)
    
    async def process_payment(self, transaction_id: str, payment_data: Dict) -> Dict:
        """Process payment through payment processor"""
        try:
            # Simulate payment processing
            # In production, integrate with Stripe/PayPal/Square
            
            payment_method = payment_data.get("method", "card")
            amount = payment_data.get("amount", 0.0)
            
            # Simulate processing delay
            await asyncio.sleep(0.5)
            
            # Simulate 95% success rate
            import random
            if random.random() < 0.95:
                # Update transaction status
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE license_transactions 
                    SET payment_status = 'paid', activated_at = ?
                    WHERE transaction_id = ?
                """, (datetime.now().isoformat(), transaction_id))
                
                conn.commit()
                conn.close()
                
                return {
                    "success": True,
                    "payment_id": f"pay_{uuid.uuid4()}",
                    "amount": amount
                }
            else:
                # Simulate payment failure
                return {
                    "success": False,
                    "error": "Card declined - insufficient funds"
                }
                
        except Exception as e:
            logger.error(f"Payment processing failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def activate_license(self, transaction_id: str, business_data: Dict) -> Dict:
        """Activate license and generate access credentials"""
        license_key = f"SINCOR_{transaction_id[:8].upper()}"
        subdomain = business_data.get("business_name", "business").lower().replace(" ", "")
        access_url = f"https://{subdomain}.getsincor.com"
        next_billing_date = (datetime.now() + timedelta(days=30)).isoformat()
        
        # Log activation
        logger.info(f"License activated: {license_key} for {business_data.get('business_name')}")
        
        return {
            "license_key": license_key,
            "access_url": access_url,
            "next_billing_date": next_billing_date
        }
    
    async def log_payment_attempt(self, transaction_id: str, payment_data: Dict, result: Dict):
        """Log payment attempt for retry tracking"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Count existing attempts
        cursor.execute("""
            SELECT COUNT(*) FROM payment_attempts WHERE transaction_id = ?
        """, (transaction_id,))
        
        attempt_number = cursor.fetchone()[0] + 1
        
        cursor.execute("""
            INSERT INTO payment_attempts 
            (transaction_id, attempt_number, payment_processor, amount, status, error_message, attempted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            transaction_id,
            attempt_number,
            payment_data.get("processor", "stripe"),
            payment_data.get("amount", 0.0),
            "failed",
            result.get("error", "Unknown error"),
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()

# Bot instance
license_bot = LicenseBot()

async def handle_event(envelope: EventEnvelope) -> ResultSchema:
    """Handle license events"""
    try:
        return await license_bot.handle_license_request(envelope)
    except Exception as e:
        logger.error(f"License bot error: {e}")
        return ResultSchema(
            ok=False,
            reason=f"License processing failed: {e}"
        )