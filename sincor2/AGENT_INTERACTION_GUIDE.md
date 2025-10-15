# SINCOR Agent Interaction Guide

## 🎯 You Now Have 3 Ways to Interact With Agents

---

## 1. **Agent Steering** ⭐ RECOMMENDED FOR CEO/STRATEGIC CONTROL
**URL:** `http://localhost:5000/agent-steering`

### What It Does:
**Guide autonomous agents in real-time without micromanaging**

### Features:
- ✅ **Strategic Directives** - Send high-level guidance to agents
- ✅ **Autonomy Control** - Set how independent each agent should be
- ✅ **Real-Time Monitoring** - See what all 9 agents are doing
- ✅ **Decision Overrides** - Step in when needed
- ✅ **Priority Setting** - Mark directives as low/medium/high/critical
- ✅ **Quick Actions** - One-click mode changes (Focus, Collaborate, Innovate, Optimize)
- ✅ **Activity Log** - Live feed of all agent decisions

### Perfect For:
- **Steering the swarm** - Guide multiple agents toward strategic goals
- **CEO-level oversight** - Set direction without managing details
- **Emergency controls** - Pause/resume/override when needed
- **Autonomous operations** - Let agents work independently with your guidance

### How to Use:
1. Select an agent from the left sidebar
2. Type a strategic directive (e.g., "Focus on cost optimization")
3. Set priority level
4. Click "Send Directive"
5. Agent autonomously executes with your guidance in mind
6. Watch the activity log for real-time updates

---

## 2. **Agent Chat** - CONVERSATIONAL INTERFACE
**URL:** `http://localhost:5000/agent-chat`

### What It Does:
**Have direct conversations with individual agents**

### Features:
- ✅ **1-on-1 Chat** - Talk to any of the 43 agents
- ✅ **Agent Personalities** - Each agent responds based on their archetype
- ✅ **Conversation History** - Maintains context across messages
- ✅ **Filter by Archetype** - Find Directors, Scouts, Builders, etc.
- ✅ **Real-time Responses** - Powered by Claude API

### Perfect For:
- Getting specific information from an agent
- Understanding an agent's reasoning
- Task clarification
- Learning what agents can do

### Agent Archetypes:
- **Director** (CEO/Leadership) - Strategic decisions
- **Scout** (Research) - Information gathering
- **Synthesizer** (Analysis) - Data processing
- **Builder** (Development) - Implementation
- **Negotiator** (Communication) - Collaboration
- **Caretaker** (Maintenance) - Organization
- **Auditor** (Quality) - Validation

---

## 3. **Agent Observability** - ANALYTICS & MONITORING
**URL:** `http://localhost:5000/agent-observability`

### What It Does:
**Monitor agent performance and interactions**

### Features:
- ✅ **Performance Charts** - Success rates, response times
- ✅ **Interaction Graph** - D3.js visualization of agent collaboration
- ✅ **Task Tracking** - See what tasks agents are working on
- ✅ **24-Hour Timeline** - Activity trends
- ✅ **Archetype Comparison** - Compare all 7 agent types

### Perfect For:
- Understanding agent effectiveness
- Spotting collaboration patterns
- Performance optimization
- System health monitoring

---

## 🎮 Quick Start Guide

### Scenario 1: "I Want to Guide the Agents Like a CEO"

**Use:** Agent Steering (`/agent-steering`)

1. Go to http://localhost:5000/agent-steering
2. Select "Almaak" (Director archetype) from the sidebar
3. In the directive box, type:
   ```
   Focus on identifying new revenue opportunities.
   Collaborate with Scout agents for market research.
   Report findings every 4 hours.
   ```
4. Set priority to "High"
5. Click "Send Directive"
6. Watch the activity log as the agent works autonomously

**Result:** Agent works independently following your strategic guidance

---

### Scenario 2: "I Want to Ask Agents Questions"

**Use:** Agent Chat (`/agent-chat`)

1. Go to http://localhost:5000/agent-chat
2. Filter by "Director" archetype
3. Select an agent (e.g., Almaak)
4. Type: "What are you currently working on?"
5. Agent responds with current status and context
6. Continue conversation as needed

**Result:** Direct Q&A with the agent

---

### Scenario 3: "I Want to See What's Happening"

**Use:** Agent Observability (`/agent-observability`)

1. Go to http://localhost:5000/agent-observability
2. View the stats at top (Active Agents, Success Rate, etc.)
3. Click on an agent in the left panel
4. See their performance charts
5. Check the interaction network graph
6. Review recent tasks

**Result:** Complete visibility into agent operations

---

## 🔄 Typical Workflow

### Morning Routine:
1. **Check Observability** (`/agent-observability`)
   - Review overnight activity
   - Check success rates
   - Identify any issues

2. **Send Directives** (`/agent-steering`)
   - Set today's priorities
   - Adjust autonomy levels
   - Send strategic guidance

3. **Monitor Progress** (throughout day)
   - Watch activity log in steering interface
   - Check observability dashboard periodically

4. **Evening Review** (`/agent-observability`)
   - Review completed tasks
   - Analyze performance trends
   - Plan tomorrow's directives

---

## 💡 Pro Tips

### For Steering:
- **Start with 80% autonomy** - Good balance of independence and control
- **Use High/Critical priority sparingly** - Makes them more effective
- **Be strategic, not tactical** - Let agents figure out the "how"
- **Quick Actions are powerful** - One click to change agent mode

### For Chat:
- **Agents remember context** - Reference previous messages
- **Each archetype has expertise** - Ask Directors about strategy, Scouts about research
- **Be specific** - Clear questions get clear answers

### For Observability:
- **Check interaction graph** - Shows collaboration patterns
- **Success rate trends** - Spot problems early
- **24-hour timeline** - Identify peak productivity times

---

## 🎯 Example Directives

### For Director Agents:
```
Focus on Q4 revenue optimization.
Coordinate with all archetypes.
Priority: Critical
```

### For Scout Agents:
```
Research competitive landscape for AI agents.
Focus on pricing models and features.
Report findings within 2 hours.
Priority: High
```

### For Builder Agents:
```
Optimize database query performance.
Target 50% reduction in response time.
Collaborate with Auditor for validation.
Priority: Medium
```

### For Synthesizer Agents:
```
Analyze last week's agent performance data.
Create executive summary with recommendations.
Priority: Medium
```

---

## 🔧 Starting the Server

```bash
cd C:\Users\cjay4\OneDrive\Desktop\SINCOR2
python app.py
```

Then visit:
- **Agent Steering:** http://localhost:5000/agent-steering
- **Agent Chat:** http://localhost:5000/agent-chat
- **Agent Observability:** http://localhost:5000/agent-observability

---

## 📊 Understanding Autonomy Levels

| Level | Meaning | Use When |
|-------|---------|----------|
| 0-20% | **Manual** | You want approval for every decision |
| 21-40% | **Guided** | Agent suggests, you approve |
| 41-60% | **Balanced** | Agent decides routine, asks for complex |
| 61-80% | **Autonomous** | Agent handles most decisions independently |
| 81-100% | **Full Auto** | Agent operates completely independently |

**Recommended:** Start at 80%, adjust based on trust and results

---

## 🚨 Emergency Controls

### Pause Single Agent:
1. Select agent in `/agent-steering`
2. Click "⏸️ Pause Agent"

### Pause All Agents:
1. In `/agent-steering` header
2. Click "⏸️ Pause All"

### Override Decision:
1. Select agent
2. Click "⚠️ Override Current"
3. Send new directive

---

## ✨ What Makes This Special

### Traditional AI Chatbots:
- You ask, it answers
- No memory between sessions
- No autonomous action
- Just a Q&A tool

### SINCOR Agent System:
- ✅ **Autonomous** - Agents work independently
- ✅ **Steerable** - You guide, don't micromanage
- ✅ **Collaborative** - Agents work together
- ✅ **Persistent** - Remember context and history
- ✅ **Specialized** - Each agent has unique expertise
- ✅ **Observable** - Full visibility into decisions

You're not chatting with a bot - **you're steering an autonomous AI workforce.**

---

## 🎊 Ready to Start?

1. **Start server:** `python app.py`
2. **Open steering interface:** http://localhost:5000/agent-steering
3. **Select a Director agent** (like Almaak)
4. **Send your first directive!**

The agents are waiting for your guidance! 🚀

---

**Last Updated:** 2025-10-01
**Status:** FULLY OPERATIONAL
