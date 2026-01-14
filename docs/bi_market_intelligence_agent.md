# 🧠 SINCOR BI Lead Generation Agent Template

## 🎯 Mission Name
**MARKET INTEL: Business Intelligence Lead Hunter**

## 🔍 Objective
Deploy market intelligence agents to monitor business expansion activities, funding rounds, and market disruption events to identify companies with immediate BI needs. Generate qualified prospects for SINCOR's Instant BI services ($2,500-$15,000 revenue opportunities).

## 🧩 Triggers
- Time-based: Run every 4 hours during business days (7 AM - 7 PM EST)
- Event-based: Major funding announcements, expansion news, market disruptions
- Manual override: `Trigger > Marketing Agent > BI Market Intel`

## 📌 Inputs
- Business news sources (TechCrunch, Business Insider, local business journals)
- Funding databases (Crunchbase, PitchBook APIs)
- Permit/expansion tracking (business license databases)
- Keywords: "expansion", "funding", "new market", "competition", "launch", "growth"
- Revenue thresholds: Companies with $1M+ annual revenue (BI budget capability)

## 🤖 Actions
1. **Expansion Opportunity Scanning**
   - Monitor business permit filings in target markets
   - Track "now hiring" posts from companies expanding operations
   - Identify businesses opening new locations or entering new markets

2. **Funding Event Detection**  
   - Scan venture capital funding announcements ($500K+ rounds)
   - Track companies that just received growth capital
   - Monitor acquisition announcements (acquirers need BI for integration)

3. **Market Disruption Alerts**
   - Identify businesses facing new competition
   - Track industry regulation changes affecting specific sectors
   - Monitor companies losing market share (need competitive intelligence)

4. **Lead Qualification Pipeline**
   - Extract company contact information and decision-makers
   - Research company size, industry, and recent activities
   - Score urgency: Immediate (expanding now), Medium (planning), Low (future)

## ✅ Confirmation Points
- Before first run: "Proceed to scan 15 business intel sources for BI opportunities?"
- If high-value targets found: "X qualified BI prospects identified. Queue for outreach?"
- Revenue opportunity validation: "Estimated pipeline value: $X. Continue processing?"

## 🧠 Escalation Logic
- **HOT LEADS** (Score 8-10): Companies expanding within 30 days → immediate handoff to outreach agent
- **WARM LEADS** (Score 5-7): Companies with funding/growth indicators → nurture sequence  
- **COLD LEADS** (Score 1-4): Future opportunity → quarterly follow-up queue
- If 5+ hot leads identified in one scan → notify supervisor for capacity planning

## 🎯 Target Lead Profiles

### **Tier 1 - Immediate BI Needs ($7,500-$15,000)**
- Companies expanding to new geographic markets
- Businesses facing sudden competitive pressure  
- Recent acquisition targets needing market analysis
- Franchise operations evaluating new territories

### **Tier 2 - Strategic BI Needs ($2,500-$7,500)**  
- Funded startups planning growth strategies
- Established businesses considering product launches
- Companies researching market opportunities
- Organizations evaluating competitive responses

### **Tier 3 - Future BI Pipeline ($2,500)**
- Growing businesses approaching expansion readiness
- Companies tracking industry trends
- Organizations building strategic planning capabilities

## 🧾 Output Artifacts
- `/leads/bi_prospects/hot_leads_[DATE].json` (immediate opportunities)
- `/leads/bi_prospects/pipeline_[DATE].csv` (full prospect database)
- `/reports/market_intel_summary_[DATE].md` (weekly intelligence report)
- Lead scoring dashboard data feed

## 🔗 Integration Points
- **Handoff to Outreach Agent**: Qualified leads with contact info and talking points
- **CRM Integration**: Populate lead database with scoring and timeline data  
- **Analytics Feed**: Track lead source performance and conversion rates
- **Revenue Forecasting**: Pipeline value calculations for business planning

## 💰 Revenue Impact Tracking
- **Target**: 20-30 qualified BI leads per week
- **Conversion Goal**: 10% close rate (2-3 new clients monthly)  
- **Revenue Pipeline**: $50K-$150K monthly opportunity identification
- **ROI Metric**: Agent operational cost vs generated revenue

## 🔒 Safety & Compliance
- Public information sources only (no hacking or unauthorized access)
- GDPR/privacy compliant data collection
- Transparent lead generation (no deceptive practices)
- Opt-out mechanisms for contacted prospects

---

> 🚀 **Business Impact**: This agent transforms SINCOR from reactive service provider to proactive revenue generator
> 💡 **Competitive Advantage**: While competitors wait for inbound leads, SINCOR agents find opportunities before they're publicly available
> ⚡ **Speed-to-Market**: 4-hour lead identification cycle vs weeks of manual prospecting