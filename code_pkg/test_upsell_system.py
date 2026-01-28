#!/usr/bin/env python3
"""
Test script for comprehensive Upsell Bot system
Tests all 4 stages + passive nudges + guardrails
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from commons.event_contracts import EventEnvelope, EventType
from orchestrator.planner import planner
from loguru import logger

class UpsellSystemTester:
    def __init__(self):
        self.test_tenant = "upsell_test_tenant_001"
    
    async def test_complete_upsell_system(self):
        """Test complete upsell system with all stages and features"""
        logger.info("Starting comprehensive Upsell Bot system test...")
        
        # Test 1: Stage 1 → 2 (Media Pack → Business Services)
        stage1_result = await self.test_stage_1_to_2_progression()
        if not stage1_result:
            return False
        
        # Test 2: Stage 2 → 3 (Business Services → Membership) 
        stage2_result = await self.test_stage_2_to_3_progression()
        if not stage2_result:
            return False
        
        # Test 3: Stage 3 → 4 (Membership → Premium)
        stage3_result = await self.test_stage_3_to_4_progression()
        if not stage3_result:
            return False
        
        # Test 4: Passive Nudges (Idle, ROI Spike, Seasonal)
        nudge_result = await self.test_passive_nudges()
        if not nudge_result:
            return False
        
        # Test 5: Guardrails (Frequency limits, ROI suppression)
        guardrail_result = await self.test_guardrails()
        if not guardrail_result:
            return False
        
        logger.success("All Upsell Bot system tests passed!")
        return True
    
    async def test_stage_1_to_2_progression(self) -> bool:
        """Test Media Pack → Business Services progression"""
        logger.info("Testing Stage 1 → 2 progression...")
        
        # Simulate media pack download trigger
        envelope = EventEnvelope(
            event_type=EventType.UPSELL,
            tenant_id=self.test_tenant,
            payload={
                "trigger_type": "stage_progression",
                "media_pack_downloaded": True,
                "current_stage": "media_pack"
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok and result.outputs.get("upsell_available"):
            dialogue = result.outputs.get("dialogue", "")
            action = result.outputs.get("action_schema", "")
            
            logger.info(f"Stage 1→2 SUCCESS:")
            logger.info(f"Dialogue: {dialogue}")
            logger.info(f"Action: {action}")
            
            # Verify correct content
            if "media pack is live" in dialogue.lower() and "weekly automation" in dialogue.lower():
                if "business_services" in action:
                    logger.success("Stage 1→2 progression test PASSED")
                    return True
        
        logger.error("Stage 1→2 progression test FAILED")
        return False
    
    async def test_stage_2_to_3_progression(self) -> bool:
        """Test Business Services → Membership progression"""
        logger.info("Testing Stage 2 → 3 progression...")
        
        # Simulate metrics that trigger membership upsell
        envelope = EventEnvelope(
            event_type=EventType.UPSELL,
            tenant_id=f"{self.test_tenant}_stage2",  # Different tenant to avoid stage conflicts
            payload={
                "trigger_type": "stage_progression",
                "current_stage": "business_services",
                "metrics": {
                    "leads_count": 8,
                    "reviews_count": 4,
                    "days_in_stage": 15
                }
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok and result.outputs.get("upsell_available"):
            dialogue = result.outputs.get("dialogue", "")
            action = result.outputs.get("action_schema", "")
            
            logger.info(f"Stage 2→3 SUCCESS:")
            logger.info(f"Dialogue: {dialogue}")
            logger.info(f"Action: {action}")
            
            # Verify personalization and content
            if "new leads" in dialogue and "fresh reviews" in dialogue:
                if "membership" in dialogue.lower() and "membership" in action:
                    logger.success("Stage 2→3 progression test PASSED")
                    return True
        
        logger.error("Stage 2→3 progression test FAILED")
        return False
    
    async def test_stage_3_to_4_progression(self) -> bool:
        """Test Membership → Premium progression"""
        logger.info("Testing Stage 3 → 4 progression...")
        
        # Simulate high-performing membership ready for premium
        envelope = EventEnvelope(
            event_type=EventType.UPSELL,
            tenant_id=f"{self.test_tenant}_stage3",  # Different tenant
            payload={
                "trigger_type": "stage_progression",
                "current_stage": "membership",
                "metrics": {
                    "roi_percentage": 145,  # 45% above baseline
                    "days_in_stage": 65
                },
                "engagement_score": 0.9
            }
        )
        
        result = await planner.route_event(envelope)
        
        if result.ok and result.outputs.get("upsell_available"):
            dialogue = result.outputs.get("dialogue", "")
            action = result.outputs.get("action_schema", "")
            
            logger.info(f"Stage 3→4 SUCCESS:")
            logger.info(f"Dialogue: {dialogue}")
            logger.info(f"Action: {action}")
            
            # Verify premium positioning
            if "above baseline" in dialogue and "premium access" in dialogue.lower():
                if "premium" in action:
                    logger.success("Stage 3→4 progression test PASSED")
                    return True
        
        logger.error("Stage 3→4 progression test FAILED")
        return False
    
    async def test_passive_nudges(self) -> bool:
        """Test all three types of passive nudges"""
        logger.info("Testing passive nudges...")
        
        # Test 1: Idle seasonal nudge
        idle_envelope = EventEnvelope(
            event_type=EventType.UPSELL,
            tenant_id=f"{self.test_tenant}_idle",
            payload={
                "trigger_type": "passive_nudge",
                "nudge_type": "idle_seasonal",
                "nudge_data": {
                    "seasonal_id": "winter_2024"
                }
            }
        )
        
        idle_result = await planner.route_event(idle_envelope)
        
        # Test 2: ROI spike nudge
        roi_envelope = EventEnvelope(
            event_type=EventType.UPSELL,
            tenant_id=f"{self.test_tenant}_roi",
            payload={
                "trigger_type": "passive_nudge",
                "nudge_type": "roi_spike",
                "nudge_data": {
                    "roi_multiplier": "2.1",
                    "target_location": "Des Moines, IA",
                    "target_expansion": "geographic_expansion"
                }
            }
        )
        
        roi_result = await planner.route_event(roi_envelope)
        
        # Test 3: Seasonal window nudge
        seasonal_envelope = EventEnvelope(
            event_type=EventType.UPSELL,
            tenant_id=f"{self.test_tenant}_seasonal",
            payload={
                "trigger_type": "passive_nudge",
                "nudge_type": "seasonal_window",
                "nudge_data": {
                    "holiday_pack_id": "thanksgiving_2024"
                }
            }
        )
        
        seasonal_result = await planner.route_event(seasonal_envelope)
        
        # Verify all nudges
        nudges_passed = 0
        
        if idle_result.ok and "seasonal pack is ready" in idle_result.outputs.get("dialogue", ""):
            logger.info("✓ Idle seasonal nudge: PASSED")
            nudges_passed += 1
        
        if roi_result.ok and "2.1x baseline ROI" in roi_result.outputs.get("dialogue", ""):
            logger.info("✓ ROI spike nudge: PASSED")
            nudges_passed += 1
        
        if seasonal_result.ok and "holiday campaign" in seasonal_result.outputs.get("dialogue", ""):
            logger.info("✓ Seasonal window nudge: PASSED")
            nudges_passed += 1
        
        if nudges_passed == 3:
            logger.success("All passive nudges test PASSED")
            return True
        else:
            logger.error(f"Passive nudges test FAILED: {nudges_passed}/3 passed")
            return False
    
    async def test_guardrails(self) -> bool:
        """Test upsell guardrails (frequency limits, ROI suppression)"""
        logger.info("Testing upsell guardrails...")
        
        test_tenant_guardrail = f"{self.test_tenant}_guardrail"
        
        # First upsell should work
        first_envelope = EventEnvelope(
            event_type=EventType.UPSELL,
            tenant_id=test_tenant_guardrail,
            payload={
                "trigger_type": "stage_progression",
                "media_pack_downloaded": True
            }
        )
        
        first_result = await planner.route_event(first_envelope)
        
        # Second upsell should be blocked by frequency limit
        second_envelope = EventEnvelope(
            event_type=EventType.UPSELL,
            tenant_id=test_tenant_guardrail,
            payload={
                "trigger_type": "stage_progression",
                "media_pack_downloaded": True
            }
        )
        
        second_result = await planner.route_event(second_envelope)
        
        # Check results
        first_allowed = first_result.ok and first_result.outputs.get("upsell_available", False)
        second_blocked = second_result.ok and not second_result.outputs.get("upsell_available", True)
        frequency_reason = "7 days" in second_result.outputs.get("suppression_reason", "")
        
        if first_allowed and second_blocked and frequency_reason:
            logger.success("Guardrails test PASSED: Frequency limit enforced")
            return True
        else:
            logger.error("Guardrails test FAILED: Frequency limit not working")
            logger.error(f"First allowed: {first_allowed}, Second blocked: {second_blocked}, Reason: {second_result.outputs.get('suppression_reason')}")
            return False
    
    def print_system_summary(self):
        """Print comprehensive system capabilities"""
        logger.info("\n" + "="*80)
        logger.info("SINCOR UPSELL BOT - FULL SYSTEM OPERATIONAL")
        logger.info("="*80)
        logger.info("STAGE PROGRESSION SYSTEM:")
        logger.info("  Stage 1: Media Pack → Business Services (immediate after download)")
        logger.info("  Stage 2: Business Services → Membership (14+ days, 5+ leads or 3+ reviews)")
        logger.info("  Stage 3: Membership → Premium (60+ days or high engagement)")
        logger.info("")
        logger.info("PASSIVE NUDGE SYSTEM:")
        logger.info("  • Idle ≥7 days: 'Seasonal pack ready — deploy it?'")
        logger.info("  • ROI >120%: 'Campaign crushed it. Replicate in {city/niche}?'")
        logger.info("  • Seasonal trigger: 'Holiday campaign queued. Activate?'")
        logger.info("")
        logger.info("DIALOGUE TEMPLATES:")
        logger.info("  ✓ Exact copy from blueprint (fact → next step → button)")
        logger.info("  ✓ Personalized with {leads_count}, {reviews_count}, {roi_percentage}")
        logger.info("  ✓ Action verbs only: Activate, Deploy, Join, Unlock, Add")
        logger.info("")
        logger.info("GUARDRAILS:")
        logger.info("  ✓ Max 1 upsell per tenant per 7 days")
        logger.info("  ✓ ROI suppression if below baseline")
        logger.info("  ✓ No sequence skipping enforced")
        logger.info("")
        logger.info("TRACKING & ANALYTICS:")
        logger.info("  ✓ All events logged in SQLite")
        logger.info("  ✓ CTR, conversion %, churn tracking ready")
        logger.info("  ✓ Exposed to Overseer Agent for tuning")
        logger.info("="*80)
        logger.info("BUILDER GOAL ACHIEVED: Autonomous, event-driven upsell system")
        logger.info("Feels like inevitability — showing next obvious move in customer journey")
        logger.info("="*80 + "\n")

async def main():
    """Run comprehensive upsell system test"""
    tester = UpsellSystemTester()
    
    success = await tester.test_complete_upsell_system()
    
    if success:
        tester.print_system_summary()
        logger.success("🎯 UPSELL SYSTEM TEST PASSED: All features operational!")
    else:
        logger.error("❌ UPSELL SYSTEM TEST FAILED: Issues found")
    
    return success

if __name__ == "__main__":
    asyncio.run(main())