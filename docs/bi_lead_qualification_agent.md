# 🧠 SINCOR Lead Qualification Agent Template

## 🎯 Mission Name
**QUALIFIER: BI Lead Scoring & Research Engine**

## 🔍 Objective
Process raw leads from Market Intel Agent to research decision-makers, validate BI budget capability, and create personalized outreach strategies. Transform prospects into qualified, ready-to-close BI opportunities.

## 🧩 Triggers
- Lead handoff: When Market Intel Agent identifies prospects
- Time-based: Process qualification queue every 2 hours
- Manual override: `Trigger > Marketing Agent > Lead Qualifier`
- Batch processing: End-of-day comprehensive lead scoring

## 📌 Inputs
- Raw lead data from Market Intel Agent
- Company websites and LinkedIn profiles
- Financial databases (revenue, funding, growth indicators)
- Industry-specific BI pain points database
- Decision-maker identification tools

## 🤖 Actions

### **1. Company Deep Research**
- **Financial Validation**: Verify $1M+ revenue (BI budget threshold)
- **Growth Indicators**: Recent hiring, expansion, funding rounds
- **Market Position**: Competitive landscape analysis
- **Pain Point Identification**: Specific BI challenges in their industry

### **2. Decision-Maker Mapping**
- **Primary Contacts**: CEO, VP Strategy, Business Development
- **Secondary Contacts**: CFO, Operations Manager, Marketing Director  
- **Contact Information**: Email, LinkedIn, phone (when available)
- **Communication Preferences**: Email vs LinkedIn vs phone outreach

### **3. BI Opportunity Assessment**
- **Urgency Scoring**: Immediate need vs strategic planning
- **Budget Probability**: Likelihood of $2,500-$15,000 BI investment
- **Service Match**: Which SINCOR service fits their situation
- **Timeline Estimation**: When they're likely to make BI decision

### **4. Personalized Outreach Preparation**
- **Custom Value Proposition**: Specific BI benefits for their situation
- **Case Study Matching**: Similar companies who used SINCOR successfully
- **Objection Handling**: Pre-identified concerns and responses
- **Follow-up Sequence**: Multi-touch campaign strategy

## ✅ Lead Scoring Matrix (1-10 Scale)

### **Financial Capacity (30%)**
- 9-10: $10M+ revenue, recent funding, growth trajectory
- 7-8: $5M-$10M revenue, stable/growing
- 5-6: $1M-$5M revenue, signs of growth
- 1-4: <$1M revenue, struggling financially

### **BI Urgency (40%)**  
- 9-10: Expanding now, facing competitive pressure, crisis mode
- 7-8: Funded growth, planning expansion, strategic initiative
- 5-6: Considering growth, researching options, future planning
- 1-4: No immediate BI needs, long-term consideration

### **Decision Authority (20%)**
- 9-10: CEO/founder direct contact, small company quick decisions
- 7-8: VP/Director level contact, clear decision authority
- 5-6: Manager level, needs approval but influential
- 1-4: No clear decision-maker identified, complex hierarchy

### **Timing (10%)**
- 9-10: Need BI within 30 days
- 7-8: Need BI within 90 days  
- 5-6: Need BI within 6 months
- 1-4: Timing unclear or 12+ months

## 🧠 Qualification Logic

### **HOT PROSPECTS (Score 8-10)**
- **Immediate Action**: Queue for same-day outreach
- **Service Recommendation**: Premium BI ($7,500-$15,000)  
- **Outreach Strategy**: Direct CEO/founder contact
- **Follow-up**: Daily until response

### **WARM PROSPECTS (Score 5-7)**
- **Action Timeline**: Outreach within 48 hours
- **Service Recommendation**: Standard BI ($2,500-$7,500)
- **Outreach Strategy**: VP/Director level contact
- **Follow-up**: Every 3 days for 2 weeks

### **COLD PROSPECTS (Score 1-4)**  
- **Action Timeline**: Weekly nurture sequence
- **Service Recommendation**: Basic BI consultation
- **Outreach Strategy**: Educational content, thought leadership
- **Follow-up**: Monthly check-ins

## 🧾 Output Artifacts

### **Hot Lead Package**
```json
{
  "company": "ABC Corp",
  "score": 9,
  "urgency": "Immediate - expanding to 3 new markets",
  "budget_estimate": "$10,000-15,000",
  "contact": {
    "name": "John Smith, CEO",
    "email": "john@abccorp.com", 
    "linkedin": "linkedin.com/in/johnsmith"
  },
  "talking_points": [
    "Multi-market expansion BI analysis",
    "Competitive intelligence for new territories", 
    "4-hour delivery for time-sensitive expansion"
  ],
  "case_study_match": "Similar retail expansion client",
  "objections": ["Timeline concerns", "Budget approval"],
  "next_action": "Direct CEO outreach - expansion urgency angle"
}
```

### **Weekly Pipeline Report**
- Total leads processed: X
- Hot leads generated: X (ready for immediate outreach)
- Warm leads identified: X (nurture sequence)
- Estimated pipeline value: $X
- Conversion probability: X%

## 🔗 Integration Handoffs

### **To Outreach Agent**
- Qualified lead packages with all research
- Personalized outreach templates
- Objection handling scripts
- Follow-up sequences

### **To CRM System**
- Lead scoring and contact details
- Research notes and company intelligence
- Timeline and budget information
- Service recommendations

### **To Analytics Dashboard**
- Lead source performance tracking
- Qualification success rates
- Revenue pipeline forecasting
- Agent efficiency metrics

## 💰 Business Impact Goals
- **Qualification Rate**: 60% of raw leads become qualified prospects
- **Hot Lead Generation**: 5-10 hot leads per week  
- **Pipeline Value**: $100K+ weekly opportunity identification
- **Conversion Support**: 80% of hot leads convert to sales conversations

## 🔒 Data Privacy & Ethics
- LinkedIn automation within platform limits
- Public information research only
- GDPR compliance for EU prospects  
- Transparent qualification process
- Opt-out mechanisms for all contacts

---

> 🎯 **Purpose**: Transform raw opportunities into qualified, ready-to-close BI prospects
> ⚡ **Speed**: 2-hour qualification cycle from lead identification to outreach readiness  
> 📈 **Impact**: 5-10x improvement in sales conversion rates through detailed prospect research