# SINCOR Production Deployment Protocol

## Railway Environment Setup

### 1. Create Separate Services
```bash
# Sandbox Environment: sincor-sandbox
PAYPAL_ENV=sandbox
PAYPAL_CLIENT_ID=<sandbox_client_id>
PAYPAL_CLIENT_SECRET=<sandbox_client_secret>
PAYPAL_WEBHOOK_ID=<sandbox_webhook_id>
APP_BASE_URL=https://sincor-sandbox.up.railway.app
DB_URL=<sandbox_database_url>

# Production Environment: sincor-prod
PAYPAL_ENV=live
PAYPAL_CLIENT_ID=<live_client_id>
PAYPAL_CLIENT_SECRET=<live_client_secret>
PAYPAL_WEBHOOK_ID=<live_webhook_id>
APP_BASE_URL=https://getsincor.com
DB_URL=<production_database_url>
```

### 2. Production Environment Variables
```bash
# PayPal Live Configuration
PAYPAL_ENV=live
PAYPAL_CLIENT_ID=<live_client_id>
PAYPAL_CLIENT_SECRET=<live_client_secret>
PAYPAL_WEBHOOK_ID=<live_webhook_id>

# Application Configuration
APP_BASE_URL=https://getsincor.com
SESSION_SECRET=<cryptographically_secure_random_string>
FLASK_ENV=production
FLASK_DEBUG=false

# Database Configuration
DATABASE_URL=<production_postgres_url>
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=30

# Security & Monitoring
LOG_LEVEL=info
SENTRY_DSN=<sentry_production_dsn>
RATE_LIMIT_PER_MINUTE=100
RATE_LIMIT_PER_HOUR=1000
ALLOWED_ORIGINS=https://getsincor.com,https://www.getsincor.com

# Performance
GUNICORN_WORKERS=4
GUNICORN_TIMEOUT=30
REDIS_URL=<redis_production_url>
```

### 3. Health Check Configuration
- Railway Health Check Path: `/readyz`
- Port: Automatic (Railway assigns)
- Timeout: 30 seconds
- Interval: 30 seconds

### 4. Database Setup
```bash
# Create production database (separate instance)
# Run migrations on clean production DB
# Zero sandbox data contamination
```

### 5. Deployment Protocol
1. Deploy to sandbox first
2. Test all PayPal workflows
3. Verify webhook handling
4. Run health checks
5. Deploy to production
6. Monitor logs and metrics