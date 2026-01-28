#!/usr/bin/env python3
"""
SINCOR Competitive Intelligence Demo: Multi-Agent Orchestrator

This demonstrates the FIRST real use case - competitive intelligence gathering
using coordinated SINCOR agents. Shows the power of multi-agent collaboration
for complex, multi-step business workflows.

Architecture:
- Scout Agents: Data gathering from multiple sources
- Synthesizer Agent: Process and analyze competitive data  
- Director Agent: Strategic synthesis and executive briefing
- Auditor Agent: Quality assurance and compliance
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

# Import our SINCOR systems
from ..cortecs_core.message_bus import SINCORMessageBus, Message, MessageType, MessagePriority
from ..cortecs_core.claude_integration import CorTecsClaudeCore
from ..memory_system import MemorySystem
from ..persona_engine import PersonaEngine
from ..agency_kernel import AgencyKernel, TaskGoal

@dataclass
class CompetitorProfile:
    """Structured competitor information"""
    company_name: str
    website: str
    industry: str
    employee_count: Optional[int]
    funding_info: Dict[str, Any]
    key_products: List[str]
    pricing_info: Dict[str, Any]
    recent_news: List[Dict[str, Any]]
    social_presence: Dict[str, str]
    last_updated: str

@dataclass
class CompetitiveIntelligence:
    """Processed competitive intelligence report"""
    report_id: str
    target_competitors: List[str]
    generated_at: str
    key_insights: List[str]
    competitor_profiles: List[CompetitorProfile]
    market_trends: List[str]
    strategic_recommendations: List[str]
    confidence_score: float
    data_sources: List[str]

class CompetitiveIntelligenceOrchestrator:
    """
    Orchestrates multiple SINCOR agents to gather and analyze competitive intelligence
    Demonstrates real multi-agent coordination for business value
    """
    
    def __init__(self, claude_api_key: str = None):
        # Core systems
        self.message_bus = SINCORMessageBus()
        self.claude_core = CorTecsClaudeCore(claude_api_key) if claude_api_key else None
        
        # Agent management  
        self.active_agents = {}
        self.intelligence_reports = {}
        
        # Demo configuration
        self.demo_mode = True
        self.mock_data_enabled = True
        
        print("🕵️ Competitive Intelligence Orchestrator initialized")
    
    async def initialize(self):
        """Initialize the orchestration system"""
        
        await self.message_bus.initialize()
        
        if self.claude_core:
            await self.claude_core.start_conversation("Competitive Intelligence Analysis")
        
        # Initialize agent systems (simplified for demo)
        await self._initialize_demo_agents()
        
        print("✅ CI Orchestrator ready for competitive intelligence gathering")
    
    async def _initialize_demo_agents(self):
        """Initialize demo versions of SINCOR agents"""
        
        # Scout Agent - E-auriga-01 (Primary data gatherer)
        self.active_agents["E-auriga-01"] = {
            "archetype": "Scout",
            "specialization": "web_research",
            "status": "ready",
            "current_tasks": [],
            "memory_system": MemorySystem("E-auriga-01"),
            "persona": PersonaEngine("E-auriga-01", "Scout")
        }
        
        # Synthesizer Agent - E-polaris-09 (Data processor)
        self.active_agents["E-polaris-09"] = {
            "archetype": "Synthesizer", 
            "specialization": "competitive_analysis",
            "status": "ready", 
            "current_tasks": [],
            "memory_system": MemorySystem("E-polaris-09"),
            "persona": PersonaEngine("E-polaris-09", "Synthesizer")
        }
        
        # Director Agent - E-alphard-37 (Strategic oversight)
        self.active_agents["E-alphard-37"] = {
            "archetype": "Director",
            "specialization": "strategic_intelligence", 
            "status": "ready",
            "current_tasks": [],
            "memory_system": MemorySystem("E-alphard-37"),
            "persona": PersonaEngine("E-alphard-37", "Director")
        }
        
        # Register agents with message bus
        for agent_id in self.active_agents.keys():
            await self.message_bus.register_agent(
                agent_id=agent_id,
                message_types=[MessageType.TASK_BROADCAST, MessageType.TASK_ASSIGNMENT, MessageType.COORDINATION]
            )
        
        print(f"🤖 Initialized {len(self.active_agents)} demo agents")
    
    async def run_competitive_intelligence_workflow(self, 
                                                   target_competitors: List[str],
                                                   analysis_focus: str = "general") -> CompetitiveIntelligence:
        """
        Main workflow: Orchestrate agents to gather competitive intelligence
        This demonstrates the full multi-agent coordination capability
        """
        
        workflow_id = f"ci-{uuid.uuid4().hex[:8]}"
        
        print(f"🚀 Starting competitive intelligence workflow: {workflow_id}")
        print(f"   Targets: {', '.join(target_competitors)}")
        print(f"   Focus: {analysis_focus}")
        
        # Phase 1: Task Broadcast - Director creates and broadcasts research tasks
        research_tasks = await self._create_research_tasks(target_competitors, analysis_focus)
        
        # Phase 2: Agent Coordination - Scouts gather data
        raw_data = await self._coordinate_data_gathering(research_tasks, workflow_id)
        
        # Phase 3: Data Synthesis - Synthesizer processes raw data
        processed_intelligence = await self._synthesize_competitive_data(raw_data, workflow_id)
        
        # Phase 4: Strategic Analysis - Director creates executive briefing
        strategic_report = await self._create_strategic_briefing(processed_intelligence, workflow_id)
        
        # Phase 5: Quality Assurance - Validate and finalize
        final_report = await self._finalize_intelligence_report(strategic_report, workflow_id)
        
        # Store report
        self.intelligence_reports[workflow_id] = final_report
        
        print(f"✅ Competitive intelligence workflow completed: {workflow_id}")
        return final_report
    
    async def _create_research_tasks(self, competitors: List[str], focus: str) -> List[Dict[str, Any]]:
        """Director agent creates research tasks for Scouts"""
        
        print("📋 Director creating research tasks...")
        
        tasks = []
        
        for competitor in competitors:
            # Company profile research task
            task = {
                "task_id": f"research-{competitor.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
                "competitor": competitor,
                "task_type": "company_profile",
                "priority": "high",
                "requirements": [
                    "Basic company information (size, funding, location)",
                    "Key products and services",
                    "Recent news and press releases", 
                    "Social media presence",
                    "Leadership team information"
                ],
                "data_sources": [
                    "company_website",
                    "crunchbase", 
                    "linkedin",
                    "recent_news",
                    "social_media"
                ],
                "deadline": (datetime.now(timezone.utc)).isoformat()
            }
            tasks.append(task)
            
            # Product/pricing research task  
            if focus in ["product", "pricing", "general"]:
                pricing_task = {
                    "task_id": f"pricing-{competitor.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
                    "competitor": competitor,
                    "task_type": "product_pricing",
                    "priority": "medium",
                    "requirements": [
                        "Product feature comparison",
                        "Pricing structure and tiers",
                        "Recent product updates",
                        "Customer reviews and feedback"
                    ],
                    "data_sources": [
                        "product_pages",
                        "pricing_pages", 
                        "g2_reviews",
                        "capterra_reviews"
                    ],
                    "deadline": (datetime.now(timezone.utc)).isoformat()
                }
                tasks.append(pricing_task)
        
        # Broadcast tasks to Scout agents
        for task in tasks:
            await self.message_bus.broadcast_task(task, "E-alphard-37")
        
        print(f"   Created {len(tasks)} research tasks")
        return tasks
    
    async def _coordinate_data_gathering(self, tasks: List[Dict[str, Any]], workflow_id: str) -> Dict[str, Any]:
        """Scout agents coordinate to gather competitive data"""
        
        print("🔍 Scouts gathering competitive data...")
        
        # In a real implementation, this would involve:
        # 1. Web scraping competitor websites
        # 2. API calls to data sources (Crunchbase, social media)
        # 3. News monitoring and aggregation
        # 4. Review site analysis
        
        # For demo, we'll simulate realistic data gathering
        raw_data = {
            "workflow_id": workflow_id,
            "gathered_at": datetime.now(timezone.utc).isoformat(),
            "data_sources": ["web_scraping", "api_calls", "news_feeds"],
            "competitors_data": {}
        }
        
        # Simulate Scout agent work
        for task in tasks:
            competitor = task["competitor"]
            
            if competitor not in raw_data["competitors_data"]:
                raw_data["competitors_data"][competitor] = {
                    "company_profile": await self._simulate_company_research(competitor),
                    "product_info": await self._simulate_product_research(competitor),
                    "recent_activity": await self._simulate_news_research(competitor),
                    "data_quality_score": 0.85,
                    "gathered_by": "E-auriga-01",
                    "verified": True
                }
        
        # Record Scout agent memory
        for agent_id in ["E-auriga-01"]:
            if agent_id in self.active_agents:
                agent = self.active_agents[agent_id]
                agent["memory_system"].record_episode(
                    event_type="competitive_research",
                    content={
                        "workflow_id": workflow_id,
                        "competitors_researched": list(raw_data["competitors_data"].keys()),
                        "tasks_completed": len(tasks)
                    },
                    confidence=0.9
                )
        
        print(f"   Gathered data on {len(raw_data['competitors_data'])} competitors")
        return raw_data
    
    async def _simulate_company_research(self, competitor: str) -> Dict[str, Any]:
        """Simulate realistic company research data"""
        
        # Mock realistic company data
        mock_data = {
            "Salesforce": {
                "name": "Salesforce",
                "website": "https://salesforce.com",
                "industry": "CRM Software",
                "employee_count": 73000,
                "founded": 1999,
                "headquarters": "San Francisco, CA",
                "revenue": "$26.5B (2023)",
                "key_executives": ["Marc Benioff (CEO)", "Amy Weaver (CFO)"],
                "recent_funding": "Public company (NYSE: CRM)"
            },
            "HubSpot": {
                "name": "HubSpot",
                "website": "https://hubspot.com", 
                "industry": "Inbound Marketing",
                "employee_count": 7000,
                "founded": 2006,
                "headquarters": "Cambridge, MA",
                "revenue": "$1.7B (2023)",
                "key_executives": ["Yamini Rangan (CEO)", "Kathryn Bueker (CFO)"],
                "recent_funding": "Public company (NYSE: HUBS)"
            },
            "Pipedrive": {
                "name": "Pipedrive",
                "website": "https://pipedrive.com",
                "industry": "CRM Software", 
                "employee_count": 850,
                "founded": 2010,
                "headquarters": "Tallinn, Estonia",
                "revenue": "$100M+ (estimated)",
                "key_executives": ["Raj Sabhlok (CEO)"],
                "recent_funding": "$90M Series C"
            }
        }
        
        return mock_data.get(competitor, {
            "name": competitor,
            "industry": "Software",
            "data_availability": "limited",
            "research_note": f"Limited public information available for {competitor}"
        })
    
    async def _simulate_product_research(self, competitor: str) -> Dict[str, Any]:
        """Simulate product and pricing research"""
        
        mock_products = {
            "Salesforce": {
                "key_products": ["Sales Cloud", "Service Cloud", "Marketing Cloud", "Commerce Cloud"],
                "pricing_tiers": {
                    "Essentials": "$25/user/month",
                    "Professional": "$75/user/month", 
                    "Enterprise": "$150/user/month",
                    "Unlimited": "$300/user/month"
                },
                "recent_updates": [
                    "Einstein AI integration across all clouds",
                    "Slack integration improvements",
                    "New automation workflows"
                ]
            },
            "HubSpot": {
                "key_products": ["Marketing Hub", "Sales Hub", "Service Hub", "CMS Hub"],
                "pricing_tiers": {
                    "Free": "$0/month",
                    "Starter": "$45/month",
                    "Professional": "$800/month",
                    "Enterprise": "$3200/month"
                },
                "recent_updates": [
                    "AI content assistant launch",
                    "Advanced reporting features",
                    "Enhanced integration marketplace"
                ]
            },
            "Pipedrive": {
                "key_products": ["Sales CRM", "Sales Intelligence", "LeadBooster", "Projects"],
                "pricing_tiers": {
                    "Essential": "$14.90/user/month",
                    "Advanced": "$24.90/user/month",
                    "Professional": "$49.90/user/month",
                    "Enterprise": "$99/user/month"
                },
                "recent_updates": [
                    "AI-powered deal insights",
                    "Enhanced mobile app",
                    "New project management features"
                ]
            }
        }
        
        return mock_products.get(competitor, {
            "products": "Information gathering in progress",
            "pricing": "Pricing research pending"
        })
    
    async def _simulate_news_research(self, competitor: str) -> List[Dict[str, Any]]:
        """Simulate recent news and activity research"""
        
        return [
            {
                "title": f"{competitor} announces new AI features",
                "date": "2025-08-15",
                "source": "TechCrunch",
                "summary": f"{competitor} continues to invest heavily in AI capabilities",
                "sentiment": "positive",
                "relevance_score": 0.8
            },
            {
                "title": f"{competitor} reports strong Q2 results",
                "date": "2025-07-28", 
                "source": "Business Wire",
                "summary": f"{competitor} shows continued growth in enterprise segment",
                "sentiment": "positive",
                "relevance_score": 0.9
            }
        ]
    
    async def _synthesize_competitive_data(self, raw_data: Dict[str, Any], workflow_id: str) -> Dict[str, Any]:
        """Synthesizer agent processes raw data into structured intelligence"""
        
        print("⚡ Synthesizer processing competitive data...")
        
        # Extract key insights
        competitors_data = raw_data["competitors_data"]
        
        synthesis = {
            "workflow_id": workflow_id,
            "synthesized_at": datetime.now(timezone.utc).isoformat(),
            "synthesized_by": "E-polaris-09",
            "competitor_count": len(competitors_data),
            "key_insights": [],
            "market_trends": [],
            "competitive_positioning": {},
            "data_quality_assessment": 0.0
        }
        
        # Analyze competitive landscape
        if len(competitors_data) >= 2:
            synthesis["key_insights"].extend([
                "Market shows strong demand for AI-enhanced features across all competitors",
                "Pricing strategies vary significantly with freemium to premium enterprise models",
                "Recent funding activity indicates continued investor confidence in sector",
                "Feature differentiation focusing on automation and intelligence capabilities"
            ])
            
            synthesis["market_trends"].extend([
                "AI integration becoming table stakes for competitive advantage",  
                "Shift toward comprehensive platform solutions vs point solutions",
                "Increased focus on user experience and ease of implementation",
                "Growing importance of integration ecosystems and partnerships"
            ])
        
        # Competitive positioning analysis
        for competitor, data in competitors_data.items():
            company_info = data.get("company_profile", {})
            product_info = data.get("product_info", {})
            
            synthesis["competitive_positioning"][competitor] = {
                "market_position": self._analyze_market_position(company_info, product_info),
                "strengths": self._identify_strengths(company_info, product_info),
                "weaknesses": self._identify_weaknesses(company_info, product_info),
                "recent_momentum": self._assess_momentum(data.get("recent_activity", []))
            }
        
        # Calculate overall data quality
        quality_scores = [data.get("data_quality_score", 0.5) for data in competitors_data.values()]
        synthesis["data_quality_assessment"] = sum(quality_scores) / len(quality_scores)
        
        # Record Synthesizer memory
        agent = self.active_agents["E-polaris-09"]
        agent["memory_system"].record_episode(
            event_type="competitive_synthesis",
            content={
                "workflow_id": workflow_id,
                "insights_generated": len(synthesis["key_insights"]),
                "competitors_analyzed": synthesis["competitor_count"]
            },
            confidence=synthesis["data_quality_assessment"]
        )
        
        print(f"   Generated {len(synthesis['key_insights'])} key insights")
        return synthesis
    
    def _analyze_market_position(self, company_info: Dict, product_info: Dict) -> str:
        """Analyze market position based on company data"""
        
        employee_count = company_info.get("employee_count", 0)
        
        if employee_count > 50000:
            return "Market Leader - Large enterprise with dominant market position"
        elif employee_count > 5000: 
            return "Strong Player - Established company with significant market presence"
        elif employee_count > 1000:
            return "Growth Company - Scaling business with expanding market share"
        else:
            return "Emerging Player - Smaller company with niche focus or early-stage growth"
    
    def _identify_strengths(self, company_info: Dict, product_info: Dict) -> List[str]:
        """Identify competitive strengths"""
        
        strengths = []
        
        if company_info.get("employee_count", 0) > 10000:
            strengths.append("Large established organization with significant resources")
            
        if "recent_funding" in company_info and "Public company" in str(company_info["recent_funding"]):
            strengths.append("Public company with access to capital markets")
            
        pricing_info = product_info.get("pricing_tiers", {})
        if "Free" in pricing_info or "$0" in str(pricing_info):
            strengths.append("Freemium model enables customer acquisition")
            
        if len(product_info.get("key_products", [])) > 3:
            strengths.append("Comprehensive product suite provides customer stickiness")
            
        return strengths or ["Competitive analysis in progress"]
    
    def _identify_weaknesses(self, company_info: Dict, product_info: Dict) -> List[str]:
        """Identify potential competitive weaknesses""" 
        
        weaknesses = []
        
        if company_info.get("employee_count", 0) > 50000:
            weaknesses.append("Large organization may have slower innovation cycles")
            
        pricing_info = product_info.get("pricing_tiers", {})
        if pricing_info and max([self._extract_price(p) for p in pricing_info.values()]) > 200:
            weaknesses.append("Premium pricing may limit market accessibility")
            
        return weaknesses or ["Further analysis needed to identify weaknesses"]
    
    def _extract_price(self, price_str: str) -> float:
        """Extract numeric price from pricing string"""
        import re
        numbers = re.findall(r'[\d.]+', str(price_str))
        return float(numbers[0]) if numbers else 0.0
    
    def _assess_momentum(self, recent_activity: List[Dict]) -> str:
        """Assess recent competitive momentum"""
        
        if not recent_activity:
            return "Limited recent activity visibility"
            
        positive_news = sum(1 for news in recent_activity if news.get("sentiment") == "positive")
        total_news = len(recent_activity)
        
        if positive_news / total_news > 0.7:
            return "Strong positive momentum with recent product launches and growth"
        elif positive_news / total_news > 0.4:
            return "Mixed momentum with both positive developments and challenges"
        else:
            return "Recent challenges may indicate competitive vulnerabilities"
    
    async def _create_strategic_briefing(self, synthesis: Dict[str, Any], workflow_id: str) -> Dict[str, Any]:
        """Director agent creates strategic executive briefing"""
        
        print("👑 Director creating strategic briefing...")
        
        briefing = {
            "workflow_id": workflow_id,
            "briefing_created": datetime.now(timezone.utc).isoformat(),
            "created_by": "E-alphard-37",
            "executive_summary": self._generate_executive_summary(synthesis),
            "strategic_recommendations": self._generate_strategic_recommendations(synthesis),
            "competitive_threats": self._identify_competitive_threats(synthesis),
            "market_opportunities": self._identify_market_opportunities(synthesis),
            "action_items": self._generate_action_items(synthesis),
            "confidence_level": synthesis.get("data_quality_assessment", 0.8)
        }
        
        # Record Director memory
        agent = self.active_agents["E-alphard-37"]
        agent["memory_system"].record_episode(
            event_type="strategic_briefing", 
            content={
                "workflow_id": workflow_id,
                "recommendations": len(briefing["strategic_recommendations"]),
                "confidence": briefing["confidence_level"]
            },
            confidence=briefing["confidence_level"]
        )
        
        print("   Strategic briefing completed")
        return briefing
    
    def _generate_executive_summary(self, synthesis: Dict) -> str:
        """Generate executive summary of competitive analysis"""
        
        competitor_count = synthesis["competitor_count"]
        key_insights_count = len(synthesis.get("key_insights", []))
        
        return f"""Competitive intelligence analysis completed on {competitor_count} key competitors. 
        
Analysis reveals {key_insights_count} critical market insights with AI integration emerging as the primary 
competitive battleground. Market shows continued growth with diverse pricing strategies from freemium 
to premium enterprise models.

Key finding: Companies investing in AI-enhanced automation and user experience are gaining competitive 
advantage and market momentum. Immediate strategic action recommended to maintain competitive position."""
    
    def _generate_strategic_recommendations(self, synthesis: Dict) -> List[str]:
        """Generate strategic recommendations based on analysis"""
        
        recommendations = [
            "Accelerate AI integration across core product features to match competitive pace",
            "Consider freemium or starter tier pricing to expand market accessibility", 
            "Invest in comprehensive platform strategy rather than point solution approach",
            "Enhance integration ecosystem to improve customer stickiness and switching costs",
            "Monitor emerging competitive threats from well-funded growth-stage companies"
        ]
        
        # Customize based on specific insights
        insights = synthesis.get("key_insights", [])
        
        if any("AI" in insight for insight in insights):
            recommendations.insert(0, "PRIORITY: AI capabilities are becoming table stakes - accelerate development")
            
        if any("automation" in insight.lower() for insight in insights):
            recommendations.append("Focus on workflow automation features to differentiate from basic CRM functionality")
        
        return recommendations
    
    def _identify_competitive_threats(self, synthesis: Dict) -> List[str]:
        """Identify immediate competitive threats"""
        
        threats = []
        positioning = synthesis.get("competitive_positioning", {})
        
        for competitor, position in positioning.items():
            if "Strong positive momentum" in position.get("recent_momentum", ""):
                threats.append(f"{competitor}: Strong momentum with recent product launches may capture market share")
                
            if "Market Leader" in position.get("market_position", ""):
                threats.append(f"{competitor}: Dominant market position with resources for aggressive expansion")
        
        return threats or ["No immediate competitive threats identified"]
    
    def _identify_market_opportunities(self, synthesis: Dict) -> List[str]:
        """Identify market opportunities based on competitive gaps"""
        
        opportunities = [
            "AI-powered competitive intelligence automation (demonstrated by this SINCOR analysis)",
            "Mid-market segment appears underserved by current major players",
            "Integration-first approach could differentiate from feature-heavy competitors",
            "Vertical specialization opportunities in underserved industries"
        ]
        
        # Look for pricing gaps
        positioning = synthesis.get("competitive_positioning", {})
        expensive_competitors = [
            comp for comp, pos in positioning.items() 
            if "Premium pricing may limit" in str(pos.get("weaknesses", []))
        ]
        
        if expensive_competitors:
            opportunities.append(f"Price-competitive positioning against premium players: {', '.join(expensive_competitors)}")
        
        return opportunities
    
    def _generate_action_items(self, synthesis: Dict) -> List[Dict[str, Any]]:
        """Generate specific action items with owners and timelines"""
        
        return [
            {
                "action": "Conduct deeper AI capability assessment vs. top 3 competitors",
                "owner": "Product Team", 
                "timeline": "2 weeks",
                "priority": "High"
            },
            {
                "action": "Analyze pricing strategy effectiveness across different customer segments",
                "owner": "Revenue Team",
                "timeline": "3 weeks", 
                "priority": "Medium"
            },
            {
                "action": "Evaluate partnership opportunities with complementary solution providers",
                "owner": "Business Development",
                "timeline": "4 weeks",
                "priority": "Medium"
            },
            {
                "action": "Implement continuous competitive monitoring using SINCOR agent framework",
                "owner": "Strategy Team",
                "timeline": "6 weeks",
                "priority": "High"
            }
        ]
    
    async def _finalize_intelligence_report(self, strategic_briefing: Dict, workflow_id: str) -> CompetitiveIntelligence:
        """Finalize and structure the complete intelligence report"""
        
        print("📄 Finalizing intelligence report...")
        
        # Create structured report
        report = CompetitiveIntelligence(
            report_id=workflow_id,
            target_competitors=list(strategic_briefing.get("competitive_positioning", {}).keys()) if "competitive_positioning" in strategic_briefing else [],
            generated_at=datetime.now(timezone.utc).isoformat(),
            key_insights=strategic_briefing.get("key_insights", []),
            competitor_profiles=[],  # Would be populated with structured CompetitorProfile objects
            market_trends=strategic_briefing.get("market_trends", []),
            strategic_recommendations=strategic_briefing.get("strategic_recommendations", []),
            confidence_score=strategic_briefing.get("confidence_level", 0.8),
            data_sources=["web_research", "news_monitoring", "social_media_analysis", "company_databases"]
        )
        
        print("✅ Intelligence report finalized")
        return report
    
    async def get_orchestrator_status(self) -> Dict[str, Any]:
        """Get current status of the orchestrator and agents"""
        
        agent_statuses = {}
        for agent_id, agent_data in self.active_agents.items():
            memory_stats = agent_data["memory_system"].get_memory_stats()
            
            agent_statuses[agent_id] = {
                "archetype": agent_data["archetype"],
                "specialization": agent_data["specialization"],
                "status": agent_data["status"],
                "active_tasks": len(agent_data["current_tasks"]),
                "memory_events": memory_stats["episodic_events"],
                "memory_facts": memory_stats["semantic_facts"]
            }
        
        # Get message bus stats
        bus_stats = await self.message_bus.get_message_bus_stats()
        
        return {
            "orchestrator_status": "operational",
            "active_agents": len(self.active_agents),
            "completed_reports": len(self.intelligence_reports),
            "agent_details": agent_statuses,
            "message_bus_stats": bus_stats,
            "demo_mode": self.demo_mode
        }

async def demo_competitive_intelligence():
    """Demo the complete competitive intelligence workflow"""
    
    print("🕵️‍♂️ SINCOR Competitive Intelligence Demo")
    print("=" * 50)
    
    # Initialize orchestrator (using mock Claude for demo)
    orchestrator = CompetitiveIntelligenceOrchestrator()
    await orchestrator.initialize()
    
    # Define competitive intelligence target
    competitors = ["Salesforce", "HubSpot", "Pipedrive"]
    analysis_focus = "product_and_pricing"
    
    print(f"\\n🎯 Analysis Target: {', '.join(competitors)}")
    print(f"📊 Focus Area: {analysis_focus}")
    
    # Run the complete workflow
    intelligence_report = await orchestrator.run_competitive_intelligence_workflow(
        target_competitors=competitors,
        analysis_focus=analysis_focus
    )
    
    # Display results
    print("\\n" + "=" * 50)
    print("📋 COMPETITIVE INTELLIGENCE REPORT")
    print("=" * 50)
    
    print(f"Report ID: {intelligence_report.report_id}")
    print(f"Generated: {intelligence_report.generated_at}")
    print(f"Confidence: {intelligence_report.confidence_score:.1%}")
    print(f"Competitors Analyzed: {len(intelligence_report.target_competitors)}")
    
    print("\\n🔍 Key Insights:")
    for i, insight in enumerate(intelligence_report.key_insights, 1):
        print(f"  {i}. {insight}")
    
    print("\\n📈 Market Trends:")
    for i, trend in enumerate(intelligence_report.market_trends, 1):
        print(f"  {i}. {trend}")
        
    print("\\n🎯 Strategic Recommendations:")
    for i, rec in enumerate(intelligence_report.strategic_recommendations, 1):
        print(f"  {i}. {rec}")
    
    # Show orchestrator status
    status = await orchestrator.get_orchestrator_status()
    print("\\n📊 Orchestrator Status:")
    print(f"  Active Agents: {status['active_agents']}")
    print(f"  Messages Processed: {status['message_bus_stats']['message_stats']['messages_sent']}")
    print(f"  Agent Memory Events: {sum(agent['memory_events'] for agent in status['agent_details'].values())}")
    
    print("\\n✅ Demo completed successfully!")
    print("\\n💡 This demonstrates SINCOR's ability to coordinate multiple agents")
    print("   for complex business intelligence workflows with measurable ROI.")

if __name__ == "__main__":
    asyncio.run(demo_competitive_intelligence())