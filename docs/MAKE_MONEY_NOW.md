# SINCOR - Make Money Now

## What This Does

Finds people on Reddit/Twitter who need business intelligence services and sells them SINCOR reports for $97-247.

**Fully autonomous. Set it and forget it.**

---

## Quick Start (5 Minutes)

### 1. Run the Sales Bot

```bash
cd OneDrive/Desktop/SINCOR2
python run_sales_bot.py
```

Bot runs every hour finding leads and sending outreach.

### 2. Get Real API Keys (Optional - For Full Automation)

**Reddit API** (free):
1. Go to https://www.reddit.com/prefs/apps
2. Create app → get client_id and client_secret
3. Add to `.env`:
```
REDDIT_CLIENT_ID=your_id
REDDIT_CLIENT_SECRET=your_secret
```

**Twitter API** (free tier works):
1. Go to https://developer.twitter.com
2. Create app → get API keys
3. Add to `.env`:
```
TWITTER_API_KEY=your_key
TWITTER_API_SECRET=your_secret
```

**Without APIs**: Bot runs in demo mode (shows you leads to manually contact)

### 3. Update PayPal Link

Edit `customer_acquisition_bot.py` line 194:
```python
paypal_link = f"https://paypal.me/YOUR_USERNAME/{service['price']}"
```

Replace `YOUR_USERNAME` with your actual PayPal.me username.

### 4. Check Results

Bot saves everything to `data/customer_acquisition/`:
- `leads.json` - People who need services
- `conversations.json` - Outreach sent

---

## What Gets Sold

1. **Business Intelligence Report** - $97
   - 10 minute delivery
   - Market analysis + recommendations

2. **Competitive Analysis** - $147
   - Analyzes 3 competitors
   - Strategic positioning advice

3. **90-Day Growth Forecast** - $247
   - Revenue projections
   - Growth opportunities

All delivered automatically via email after payment.

---

## Revenue Math

**Conservative**:
- Bot finds 10 leads/day
- 5% convert = 0.5 sales/day
- Avg $150/sale = **$75/day = $2,250/month**

**Realistic** (with APIs):
- Bot finds 50 leads/day
- 3% convert = 1.5 sales/day
- Avg $150/sale = **$225/day = $6,750/month**

**Optimized** (good messaging):
- 100 leads/day
- 5% convert = 5 sales/day
- Avg $150/sale = **$750/day = $22,500/month**

---

## How It Works

1. Bot scans Reddit/Twitter for keywords like "need business intelligence"
2. Finds people asking for help
3. Replies with value-first message + PayPal link
4. When they pay, SINCOR engines generate report
5. Report emailed automatically
6. Money in your PayPal account

---

## Manual Mode (No APIs Needed)

Run `python customer_acquisition_bot.py` to see demo leads.

Bot shows:
- Lead username
- What they need
- Suggested pitch message
- PayPal link

Copy/paste the pitch manually to Reddit/Twitter DMs.

---

## Files

- `customer_acquisition_bot.py` - Finds leads
- `sales_automation.py` - Handles payments & delivery
- `run_sales_bot.py` - Runs continuously
- `data/customer_acquisition/` - Lead & sales logs

---

## Next Steps

1. **Week 1**: Run in manual mode, test 10 leads
2. **Week 2**: Add Reddit API, automate outreach
3. **Week 3**: Add Twitter API, scale to 50 leads/day
4. **Week 4**: Optimize messaging, track conversion rates

---

## Troubleshooting

**Bot finds 0 leads**:
- APIs not configured (it's in demo mode)
- Add Reddit/Twitter API keys to `.env`

**Payment not processing**:
- Update PayPal link in `customer_acquisition_bot.py`
- Check PayPal credentials in `.env`

**Reports not sending**:
- Configure email SMTP in `.env`:
```
SINCOR_EMAIL=your@email.com
SINCOR_EMAIL_PASSWORD=your_app_password
```

---

## Support

Check logs in `data/customer_acquisition/` for debugging.

All transactions logged to `data/transactions/`.
