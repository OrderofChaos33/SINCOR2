# SINCOR Railway Deployment Guide

## 🚀 Quick Railway Setup

### 1. Environment Variables Setup
Add these environment variables in Railway dashboard (Variables tab):

**Required for Payments:**
```
stripe = sk_live_YOUR_STRIPE_SECRET_KEY
```

**Required for Business Discovery:**
```
google places api = YOUR_GOOGLE_PLACES_API_KEY
GOOGLE_API_KEY = YOUR_GOOGLE_PLACES_API_KEY  
```

**Required for Email Campaigns:**
```
SMTP_HOST = smtp.gmail.com
SMTP_PORT = 587
SMTP_USER = your-email@gmail.com
SMTP_PASS = your-app-password
EMAIL_FROM = your-email@gmail.com
```

**System Variables (Railway provides):**
```
HOST = 0.0.0.0
PORT = 5000
```

### 2. Deploy Command
Railway should automatically detect and run:
```
python sincor_app.py
```

### 3. Health Check
Railway will check: `https://your-app.railway.app/health`

## 🎯 Live Features After Deployment

✅ **Professional AI Demo** - `/discovery-dashboard`
✅ **Real Stripe Payments** - All `/checkout/*` routes  
✅ **Business Discovery** - Google Places API integration
✅ **Email Campaigns** - SMTP integration
✅ **Analytics Dashboard** - `/analytics-dashboard`
✅ **Enterprise Features** - Full SINCOR system

## 🔧 Testing Checklist

1. **Demo Works**: Visit `/discovery-dashboard` - should show live AI processing
2. **Checkout Works**: Visit `/checkout/professional` - should load Stripe form
3. **API Connected**: Demo should use real Google Places API data
4. **Payments Process**: Test cards should work with Stripe

## 🚨 If Issues Occur

1. Check Railway logs for startup errors
2. Verify all environment variables are set correctly
3. Ensure Stripe keys match your Stripe dashboard
4. Test Google Places API key in Stripe dashboard

## 💡 Success Indicators

- Homepage shows "🔴 LIVE AI DEMO" prominently
- Demo page shows professional dark interface
- Checkout shows real Stripe payment form
- Business discovery finds real businesses
- No "demo mode" messages anywhere

**Ready for getsincor.com launch! 🎯**