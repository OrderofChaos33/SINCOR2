#!/usr/bin/env python3
"""
Test script for complete 7-bot autonomous revenue flow
Simulates the THISONE.txt journey: curiosity -> demo -> license -> setup -> deploy -> tutor -> support -> upsell
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from commons.event_contracts import EventEnvelope, EventType
from orchestrator.planner import planner
from loguru import logger

class AutonomousFlowTester:
    def __init__(self):
        self.tenant_id = "test_business_001"
        self.test_business = {
            "business_name": "Premier Plumbing Services",
            "niche": "plumbing", 
            "city": "Cedar Rapids, IA",
            "phone": "(319) 555-0123",
            "owner_name": "Mike Johnson"
        }
    
    async def test_complete_flow(self):
        """Test complete autonomous revenue flow"""
        logger.info("Starting 7-bot autonomous revenue flow test...")
        
        # Step 1: DEMO - Convert curiosity to interest
        demo_result = await self.test_demo_bot()
        if not demo_result.ok:
            logger.error(f"DEMO bot failed: {demo_result.reason}")
            return False
        
        # Step 2: LICENSE - Convert interest to paid subscription
        license_result = await self.test_license_bot(demo_result)
        if not license_result.ok:
            logger.error(f"LICENSE bot failed: {license_result.reason}")
            return False
        
        # Step 3: SETUP - Automated system deployment
        setup_result = await self.test_setup_bot(license_result)
        if not setup_result.ok:
            logger.error(f"SETUP bot failed: {setup_result.reason}")
            return False
        
        # Step 4: DEPLOY - Production launch
        deploy_result = await self.test_deploy_bot(setup_result)
        if not deploy_result.ok:
            logger.error(f"DEPLOY bot failed: {deploy_result.reason}")
            return False
        
        # Step 5: TUTOR - Post-launch optimization coaching
        tutor_result = await self.test_tutor_bot(deploy_result)
        if not tutor_result.ok:
            logger.error(f"TUTOR bot failed: {tutor_result.reason}")
            return False
        
        # Step 6: SUPPORT - Autonomous issue resolution
        support_result = await self.test_support_bot()
        if not support_result.ok:
            logger.error(f"SUPPORT bot failed: {support_result.reason}")
            return False
        
        # Step 7: UPSELL - Irresistible expansion opportunities
        upsell_result = await self.test_upsell_bot()
        if not upsell_result.ok:
            logger.error(f"UPSELL bot failed: {upsell_result.reason}")
            return False
        
        # Step 8: OVERSEER - System monitoring and drift prevention
        overseer_result = await self.test_overseer_bot()
        if not overseer_result.ok:
            logger.error(f"OVERSEER bot failed: {overseer_result.reason}")
            return False
        
        logger.success("All 7 bots completed successfully! Autonomous revenue flow operational.")
        return True
    
    async def test_demo_bot(self):
        """Test DEMO bot - curiosity to interest conversion"""
        logger.info("Testing DEMO bot...")
        
        envelope = EventEnvelope(
            event_type=EventType.DEMO,
            tenant_id=self.tenant_id,
            payload={
                "business_data": self.test_business,
                "curiosity_signals": {
                    "viewed_cad_proof": True,
                    "spent_time_on_page": 45,  # seconds
                    "clicked_preview": True
                }
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok:
            logger.info(f"DEMO bot success: {result.reason}")
            logger.info(f"Generated demo: {result.outputs.get('preview_url', 'N/A')}")
        
        return result
    
    async def test_license_bot(self, demo_result):
        """Test LICENSE bot - interest to paid conversion"""
        logger.info("Testing LICENSE bot...")
        
        envelope = EventEnvelope(
            event_type=EventType.PURCHASE,
            tenant_id=self.tenant_id,
            payload={
                "business_data": self.test_business,
                "payment_data": {
                    "method": "card",
                    "amount": 148.0,  # $49/mo + $99 setup
                    "processor": "stripe"
                },
                "demo_data": demo_result.outputs
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok:
            logger.info(f"LICENSE bot success: {result.reason}")
            logger.info(f"License key: {result.outputs.get('license_key', 'N/A')}")
        
        return result
    
    async def test_setup_bot(self, license_result):
        """Test SETUP bot - automated deployment"""
        logger.info("Testing SETUP bot...")
        
        envelope = EventEnvelope(
            event_type=EventType.SETUP,
            tenant_id=self.tenant_id,
            payload={
                "license_data": license_result.outputs,
                "business_data": self.test_business
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok:
            logger.info(f"SETUP bot success: {result.reason}")
            logger.info(f"Site URL: {result.outputs.get('site_url', 'N/A')}")
        
        return result
    
    async def test_deploy_bot(self, setup_result):
        """Test DEPLOY bot - production launch"""
        logger.info("Testing DEPLOY bot...")
        
        envelope = EventEnvelope(
            event_type=EventType.DEPLOY,
            tenant_id=self.tenant_id,
            payload={
                "setup_data": setup_result.outputs,
                "business_data": self.test_business
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok:
            logger.info(f"DEPLOY bot success: {result.reason}")
            logger.info(f"Production URL: {result.outputs.get('production_url', 'N/A')}")
        
        return result
    
    async def test_tutor_bot(self, deploy_result):
        """Test TUTOR bot - optimization coaching"""
        logger.info("Testing TUTOR bot...")
        
        # Simulate post-launch metrics
        simulated_metrics = {
            "daily_leads": 0.5,  # Below CAD baseline of 0.7
            "conversion_rate": 0.3,  # Below CAD baseline of 0.4
            "cost_per_lead": 75.0,  # Above CAD baseline of 50.0
            "revenue_per_lead": 85.0,
            "weekly_revenue": 180.0  # Below CAD target of 250
        }
        
        envelope = EventEnvelope(
            event_type=EventType.TUTORIAL,
            tenant_id=self.tenant_id,
            payload={
                "session_type": "launch_review",
                "business_metrics": simulated_metrics,
                "deploy_data": deploy_result.outputs
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok:
            logger.info(f"TUTOR bot success: {result.reason}")
            recommendations = result.outputs.get('recommendations', [])
            logger.info(f"Generated {len(recommendations)} optimization recommendations")
        
        return result
    
    async def test_support_bot(self):
        """Test SUPPORT bot - autonomous issue resolution"""
        logger.info("Testing SUPPORT bot...")
        
        envelope = EventEnvelope(
            event_type=EventType.SUPPORT,
            tenant_id=self.tenant_id,
            payload={
                "issue_category": "lead_generation",
                "issue_description": "Low lead volume, only getting 2-3 leads per week instead of expected 5+",
                "priority": "high"
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok:
            logger.info(f"SUPPORT bot success: {result.reason}")
            steps_taken = result.outputs.get('steps_taken', [])
            logger.info(f"Executed {len(steps_taken)} resolution steps")
        
        return result
    
    async def test_upsell_bot(self):
        """Test UPSELL bot - irresistible expansion opportunities"""
        logger.info("Testing UPSELL bot...")
        
        envelope = EventEnvelope(
            event_type=EventType.UPSELL,
            tenant_id=self.tenant_id,
            payload={
                "trigger": "success_moment",  # Only trigger during success moments
                "current_performance": {
                    "monthly_revenue": 800.0,
                    "lead_volume": 15,
                    "roi": 1.8  # Above baseline, good time for upsell
                }
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok:
            logger.info(f"UPSELL bot success: {result.reason}")
            if result.outputs.get('upsell_available'):
                recommendation = result.outputs.get('recommendation', 'N/A')
                logger.info(f"Upsell opportunity: {recommendation}")
            else:
                logger.info("No profitable upsell opportunities found")
        
        return result
    
    async def test_overseer_bot(self):
        """Test OVERSEER bot - system monitoring and drift prevention"""
        logger.info("Testing OVERSEER bot...")
        
        envelope = EventEnvelope(
            event_type=EventType.OVERSEER,
            tenant_id=self.tenant_id,
            payload={
                "check_type": "full",
                "current_metrics": {
                    "daily_budget_used": 45.0,  # Within $500 ceiling
                    "error_rate": 0.02,  # Below 5% threshold
                    "drift_indicators": {"drift_score": 0.15}  # Below 30% threshold
                }
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok:
            logger.info(f"OVERSEER bot success: {result.reason}")
            checks_performed = result.outputs.get('checks_performed', [])
            logger.info(f"Performed {len(checks_performed)} oversight checks")
        
        return result
    
    def print_flow_summary(self):
        """Print summary of autonomous flow capabilities"""
        logger.info("\n" + "="*80)
        logger.info("7-BOT AUTONOMOUS REVENUE SYSTEM OPERATIONAL")
        logger.info("="*80)
        logger.info("DEMO Bot: Converts curiosity -> interest using CAD social proof")
        logger.info("LICENSE Bot: Converts interest -> paid subscription with payment processing")
        logger.info("SETUP Bot: Automated deployment of complete marketing infrastructure")
        logger.info("DEPLOY Bot: Production launch with zero-downtime go-live coordination")
        logger.info("TUTOR Bot: Post-launch optimization coaching using CAD-proven strategies")
        logger.info("SUPPORT Bot: Autonomous issue resolution without human escalation")
        logger.info("UPSELL Bot: Irresistible expansion opportunities (>1.4x ROI threshold)")
        logger.info("OVERSEER Bot: Prevents system drift, enforces budget/error limits")
        logger.info("="*80)
        logger.info("READY FOR SCALE: Complete autonomous revenue generation without humans")
        logger.info("="*80 + "\n")

async def main():
    """Run the autonomous flow test"""
    tester = AutonomousFlowTester()
    
    success = await tester.test_complete_flow()
    
    if success:
        tester.print_flow_summary()
        logger.success("TEST PASSED: 7-bot autonomous revenue system is operational!")
    else:
        logger.error("TEST FAILED: Issues found in autonomous revenue flow")
    
    return success

if __name__ == "__main__":
    asyncio.run(main())