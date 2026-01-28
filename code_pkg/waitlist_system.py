"""
SINCOR Waitlist Management System
Handles product waitlist signups and notifications
"""

import sqlite3
import hashlib
import secrets
import smtplib
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import request, jsonify
import re

class WaitlistManager:
    def __init__(self, db_path="data/waitlist.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize waitlist database with security measures"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS waitlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_hash TEXT UNIQUE NOT NULL,
                    encrypted_email TEXT NOT NULL,
                    product_interest TEXT NOT NULL,
                    company_name TEXT,
                    industry TEXT,
                    team_size TEXT,
                    monthly_revenue TEXT,
                    pain_points TEXT,
                    signup_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    ip_address TEXT,
                    user_agent TEXT,
                    verification_token TEXT,
                    is_verified BOOLEAN DEFAULT FALSE,
                    priority_score INTEGER DEFAULT 0,
                    notification_sent BOOLEAN DEFAULT FALSE,
                    referral_code TEXT,
                    utm_source TEXT,
                    utm_medium TEXT,
                    utm_campaign TEXT
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS product_analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_name TEXT NOT NULL,
                    signups_count INTEGER DEFAULT 0,
                    conversion_rate REAL DEFAULT 0.0,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Initialize product analytics if empty
            products = ['Growth Engine', 'Ops Core', 'Creative Forge', 'Intelligence Hub']
            for product in products:
                conn.execute('''
                    INSERT OR IGNORE INTO product_analytics (product_name) VALUES (?)
                ''', (product,))
            
            conn.commit()
    
    def hash_email(self, email):
        """Create secure hash of email for deduplication"""
        return hashlib.sha256(email.lower().encode()).hexdigest()
    
    def encrypt_email(self, email):
        """Simple encryption for email storage (use proper encryption in production)"""
        key = os.environ.get('ENCRYPTION_KEY', 'default_key_change_in_production')
        return hashlib.pbkdf2_hmac('sha256', email.encode(), key.encode(), 100000).hex()
    
    def validate_email(self, email):
        """Validate email format"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    def calculate_priority_score(self, data):
        """Calculate priority score based on signup data"""
        score = 0
        
        # Company size scoring
        team_size = data.get('team_size', '').lower()
        if '50+' in team_size or 'enterprise' in team_size:
            score += 50
        elif '10-50' in team_size:
            score += 30
        elif '5-10' in team_size:
            score += 20
        else:
            score += 10
        
        # Revenue scoring
        revenue = data.get('monthly_revenue', '').lower()
        if '$100k+' in revenue:
            score += 40
        elif '$50k' in revenue:
            score += 30
        elif '$10k' in revenue:
            score += 20
        else:
            score += 10
        
        # Industry scoring (prioritize high-value industries)
        industry = data.get('industry', '').lower()
        high_value_industries = ['saas', 'technology', 'finance', 'consulting', 'agency', 'real estate']
        if any(ind in industry for ind in high_value_industries):
            score += 20
        
        # Pain points scoring
        pain_points = data.get('pain_points', '').lower()
        urgent_keywords = ['urgent', 'asap', 'immediately', 'losing money', 'critical']
        if any(keyword in pain_points for keyword in urgent_keywords):
            score += 15
        
        return score
    
    def add_to_waitlist(self, signup_data):
        """Add user to waitlist with validation and security"""
        try:
            email = signup_data.get('email', '').lower().strip()
            
            if not self.validate_email(email):
                return {'success': False, 'error': 'Invalid email format'}
            
            email_hash = self.hash_email(email)
            encrypted_email = self.encrypt_email(email)
            verification_token = secrets.token_urlsafe(32)
            priority_score = self.calculate_priority_score(signup_data)
            
            # Get request metadata
            ip_address = request.remote_addr if request else 'unknown'
            user_agent = request.headers.get('User-Agent', 'unknown') if request else 'unknown'
            
            with sqlite3.connect(self.db_path) as conn:
                # Check if already registered
                existing = conn.execute(
                    'SELECT id FROM waitlist WHERE email_hash = ?', 
                    (email_hash,)
                ).fetchone()
                
                if existing:
                    return {'success': False, 'error': 'Email already registered'}
                
                # Insert new signup
                conn.execute('''
                    INSERT INTO waitlist (
                        email_hash, encrypted_email, product_interest, company_name,
                        industry, team_size, monthly_revenue, pain_points,
                        ip_address, user_agent, verification_token, priority_score,
                        referral_code, utm_source, utm_medium, utm_campaign
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    email_hash, encrypted_email, signup_data.get('product_interest'),
                    signup_data.get('company_name'), signup_data.get('industry'),
                    signup_data.get('team_size'), signup_data.get('monthly_revenue'),
                    signup_data.get('pain_points'), ip_address, user_agent,
                    verification_token, priority_score, signup_data.get('referral_code'),
                    signup_data.get('utm_source'), signup_data.get('utm_medium'),
                    signup_data.get('utm_campaign')
                ))
                
                # Update product analytics
                conn.execute('''
                    UPDATE product_analytics 
                    SET signups_count = signups_count + 1, last_updated = CURRENT_TIMESTAMP
                    WHERE product_name = ?
                ''', (signup_data.get('product_interest'),))
                
                conn.commit()
                
                # Send verification email (implement in production)
                # self.send_verification_email(email, verification_token)
                
                return {
                    'success': True, 
                    'message': 'Successfully added to waitlist',
                    'position': self.get_waitlist_position(email_hash),
                    'priority_score': priority_score
                }
                
        except Exception as e:
            return {'success': False, 'error': f'Signup failed: {str(e)}'}
    
    def get_waitlist_position(self, email_hash):
        """Get user's position in waitlist"""
        with sqlite3.connect(self.db_path) as conn:
            # Get signup date for this user
            user_data = conn.execute(
                'SELECT signup_date, priority_score FROM waitlist WHERE email_hash = ?',
                (email_hash,)
            ).fetchone()
            
            if not user_data:
                return None
            
            signup_date, priority_score = user_data
            
            # Count users ahead in queue (higher priority or earlier signup)
            position = conn.execute('''
                SELECT COUNT(*) FROM waitlist 
                WHERE (priority_score > ? OR (priority_score = ? AND signup_date < ?))
                AND is_verified = TRUE
            ''', (priority_score, priority_score, signup_date)).fetchone()[0]
            
            return position + 1
    
    def get_analytics(self):
        """Get waitlist analytics"""
        with sqlite3.connect(self.db_path) as conn:
            # Total signups by product
            products = conn.execute('''
                SELECT product_name, signups_count FROM product_analytics
                ORDER BY signups_count DESC
            ''').fetchall()
            
            # Recent signups
            recent_signups = conn.execute('''
                SELECT product_interest, COUNT(*) as count, DATE(signup_date) as date
                FROM waitlist 
                WHERE signup_date >= DATE('now', '-30 days')
                GROUP BY product_interest, DATE(signup_date)
                ORDER BY date DESC
            ''').fetchall()
            
            # Top priority users
            high_priority = conn.execute('''
                SELECT priority_score, product_interest, company_name, signup_date
                FROM waitlist 
                WHERE priority_score >= 70
                ORDER BY priority_score DESC
                LIMIT 10
            ''').fetchall()
            
            return {
                'products': dict(products),
                'recent_signups': recent_signups,
                'high_priority_signups': high_priority,
                'total_signups': sum(dict(products).values())
            }
    
    def notify_launch(self, product_name, batch_size=100):
        """Notify waitlist users about product launch"""
        with sqlite3.connect(self.db_path) as conn:
            # Get top priority users who haven't been notified
            users = conn.execute('''
                SELECT encrypted_email, priority_score FROM waitlist
                WHERE product_interest = ? AND notification_sent = FALSE AND is_verified = TRUE
                ORDER BY priority_score DESC, signup_date ASC
                LIMIT ?
            ''', (product_name, batch_size)).fetchall()
            
            # Mark as notified
            email_hashes = [self.hash_email(user[0]) for user in users]
            placeholders = ','.join('?' * len(email_hashes))
            conn.execute(f'''
                UPDATE waitlist SET notification_sent = TRUE 
                WHERE email_hash IN ({placeholders})
            ''', email_hashes)
            
            conn.commit()
            
            return len(users)

# Initialize waitlist manager
waitlist_manager = WaitlistManager()