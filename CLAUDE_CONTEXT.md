# SINCOR PROJECT CONTEXT - CRITICAL READ FIRST

## 🔥 PRIORITY INSTRUCTIONS FOR CLAUDE
**READ THIS IMMEDIATELY WHEN STARTING ANY SINCOR WORK**

### PROJECT OVERVIEW
SINCOR = AI-powered customer acquisition system for service businesses (auto detailing, HVAC, plumbing, electrical, etc.)
- Target: B2C service businesses that serve INDIVIDUAL CUSTOMERS, not other businesses
- Core function: Find people who NEED services (not businesses to partner with)

### CURRENT CRITICAL TASKS

#### 1. AGENT ACTIVATION (TOP PRIORITY)
**START WITH SYNDICATOR AGENT:**
- Location: `/agents/` folders
- Activate for 24/7 content creation and scraping
- Set up automated schedules for asset creation
- Connect to Canva API for ad generation
- Implement SAFE scraping with obscuring methods to avoid blocks

#### 2. AUTOMATED SYSTEMS TO BUILD
- 24/7 ad creation pipeline
- Automated outreach to prospects
- Content syndication across platforms
- Safe business data scraping (with proper protocols)
- Lead generation automation

#### 3. PAYMENT INTEGRATION
- **DO NOT USE STRIPE** (too difficult to implement)
- **USE PAYPAL INSTEAD** (user has PayPal integration code to provide)
- Remove all Stripe references, replace with PayPal

#### 4. AGENT FOLDER AUDIT
Go through ALL folders one by one:
- `/agents/` 
- `/agents/intelligence/`
- `/agents/media/`
- Check each agent file and activate functionality
- Make agents work together as an ecosystem

### DEMO STATUS ✅
- Discovery dashboard now shows B2C customers (not businesses)
- Holy Grail demo is complete and compelling
- Shows people who need services, not B2B partnerships

### CURRENT TECH STACK
- Flask app running on port 5000
- Google APIs working (Places, Calendar with OAuth)
- Railway deployment configured
- Templates system working

### KEY FILES TO REMEMBER
- Main app: `sincor_app.py`
- Agent folders: `agents/*/`  
- Templates: `templates/`
- Config: `config/.env`

### BUSINESS MODEL REMINDER
**SERVICE BUSINESSES SERVE PEOPLE, NOT OTHER BUSINESSES!**
- Auto detailing → Car owners
- HVAC → Homeowners  
- Plumbing → Homeowners
- Electrical → Homeowners
- Never show "business to business" partnerships

### USER PREFERENCES
- Prefers PayPal over Stripe
- Wants 24/7 automation
- Needs safe scraping protocols
- Wants Canva integration
- Wants agents working continuously

---
**ALWAYS START HERE WHEN WORKING ON SINCOR PROJECT**
**ALWAYS READ THIS CONTEXT BEFORE MAKING CHANGES**