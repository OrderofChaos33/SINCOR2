#!/usr/bin/env python3
"""
PLANNER - Routes events between all 7 bots
Handles retries, DLQ, idempotency, and trace logging
"""

import asyncio
import time
from typing import Dict, Any
import redis
from loguru import logger
from commons.event_contracts import EventEnvelope, ResultSchema, EventType
from commons.queue import get_queue

# Import all bot handlers
from bots.demo_bot import handle_event as handle_demo_event
from bots.license_bot import handle_event as handle_license_event
from bots.setup_bot import handle_event as handle_setup_event
from bots.deploy_bot import handle_event as handle_deploy_event
from bots.tutor_bot import handle_event as handle_tutor_event
from bots.support_bot import handle_event as handle_support_event
from bots.upsell_bot import handle_event as handle_upsell_event
from bots.overseer_bot import handle_event as handle_overseer_event

class EventPlanner:
    def __init__(self):
        try:
            self.redis_client = redis.Redis(decode_responses=True)
            # Test connection
            self.redis_client.ping()
            self.redis_available = True
        except:
            logger.warning("Redis not available - running without caching")
            self.redis_client = None
            self.redis_available = False
        self.max_retries = 3
        self.bot_handlers = {
            EventType.DEMO: handle_demo_event,
            EventType.PURCHASE: handle_license_event, 
            EventType.SETUP: handle_setup_event,
            EventType.DEPLOY: handle_deploy_event,
            EventType.TUTORIAL: handle_tutor_event,
            EventType.SUPPORT: handle_support_event,
            EventType.UPSELL: handle_upsell_event,
            EventType.OVERSEER: handle_overseer_event
        }
    
    async def route_event(self, envelope: EventEnvelope) -> ResultSchema:
        """Route event to appropriate bot with retries and DLQ"""
        start_time = time.time()
        
        # Check idempotency (if Redis available)
        if self.redis_available:
            idempotency_key = f"idempotent:{envelope.idempotency_key}"
            try:
                cached_result = self.redis_client.get(idempotency_key)
                if cached_result:
                    logger.info(f"Returning cached result for {envelope.idempotency_key}")
                    return ResultSchema.model_validate_json(cached_result)
            except Exception as e:
                logger.warning(f"Redis cache check failed: {e}")
        
        # Log event
        await self.log_event(envelope)
        
        # Route to bot handler
        bot_handler = self.bot_handlers.get(envelope.event_type)
        if not bot_handler:
            return ResultSchema(
                ok=False,
                reason=f"No handler for event_type: {envelope.event_type}",
                latency_ms=int((time.time() - start_time) * 1000)
            )
        
        # Try with retries
        result = await self.execute_with_retries(bot_handler, envelope)
        
        # Cache successful results for 30 days (if Redis available)
        if result.ok and self.redis_available:
            try:
                idempotency_key = f"idempotent:{envelope.idempotency_key}"
                self.redis_client.setex(
                    idempotency_key,
                    30 * 24 * 60 * 60,  # 30 days
                    result.model_dump_json()
                )
            except Exception as e:
                logger.warning(f"Redis cache set failed: {e}")
        
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result
    
    async def execute_with_retries(self, bot_handler, envelope: EventEnvelope) -> ResultSchema:
        """Execute bot handler with exponential backoff retries"""
        bot_name = bot_handler.__module__.split('.')[-1]  # Get bot name from module
        
        for attempt in range(self.max_retries):
            try:
                result = await bot_handler(envelope)
                if result.ok:
                    return result
                
                # Log failure and retry
                logger.warning(f"Bot {bot_name} failed attempt {attempt + 1}: {result.reason}")
                
                if attempt < self.max_retries - 1:
                    delay = (2 ** attempt) + (0.1 * attempt)  # Exponential backoff with jitter
                    await asyncio.sleep(delay)
                
            except Exception as e:
                logger.error(f"Bot {bot_name} exception attempt {attempt + 1}: {e}")
                if attempt < self.max_retries - 1:
                    delay = (2 ** attempt) + (0.1 * attempt)
                    await asyncio.sleep(delay)
                else:
                    # Final attempt failed - send to DLQ
                    await self.send_to_dlq(envelope, str(e))
                    return ResultSchema(
                        ok=False,
                        reason=f"All retries failed: {e}"
                    )
        
        # All retries failed - send to DLQ
        await self.send_to_dlq(envelope, "Max retries exceeded")
        return ResultSchema(
            ok=False,
            reason="Max retries exceeded, sent to DLQ"
        )
    
    
    async def send_to_dlq(self, envelope: EventEnvelope, reason: str):
        """Send failed events to Dead Letter Queue"""
        dlq_data = {
            "envelope": envelope.model_dump(),
            "reason": reason,
            "failed_at": time.time()
        }
        
        try:
            dlq_queue = get_queue("dead_letter:queue")
            dlq_queue.push(dlq_data)
            logger.error(f"Sent to DLQ: {envelope.event_id} - {reason}")
        except Exception as e:
            logger.critical(f"Failed to send to DLQ: {e}")
    
    async def log_event(self, envelope: EventEnvelope):
        """Log all events for audit and analytics"""
        log_data = {
            "event_id": envelope.event_id,
            "event_type": envelope.event_type,
            "tenant_id": envelope.tenant_id,
            "trace_id": envelope.trace_id,
            "timestamp": envelope.created_at.isoformat()
        }
        
        logger.info(f"Event logged", extra=log_data)
        
        # Store in Redis for analytics (if available)
        if self.redis_available:
            try:
                log_key = f"event_log:{envelope.event_id}"
                self.redis_client.setex(log_key, 7 * 24 * 60 * 60, envelope.model_dump_json())  # 7 days
            except Exception as e:
                logger.warning(f"Redis event log failed: {e}")

# Singleton planner
planner = EventPlanner()