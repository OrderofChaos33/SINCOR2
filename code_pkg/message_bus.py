#!/usr/bin/env python3
"""
SINCOR Message Bus: Redis-Based Agent Communication System

High-performance message queues for 43-agent swarm coordination.
Handles task distribution, status updates, knowledge sharing, and real-time coordination.
"""

import asyncio
import json
import uuid
import time
from typing import Dict, List, Optional, Any, Callable, AsyncGenerator
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from enum import Enum
import redis.asyncio as redis
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MessageType(Enum):
    """Types of messages in the system"""
    TASK_BROADCAST = "task_broadcast"
    TASK_BID = "task_bid"
    TASK_ASSIGNMENT = "task_assignment"
    TASK_UPDATE = "task_update"
    TASK_COMPLETION = "task_completion"
    AGENT_STATUS = "agent_status"
    KNOWLEDGE_SHARE = "knowledge_share"
    COORDINATION = "coordination"
    SYSTEM_ALERT = "system_alert"
    HEARTBEAT = "heartbeat"

class MessagePriority(Enum):
    """Message priority levels"""
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4

@dataclass
class Message:
    """Standard message format for all agent communication"""
    message_id: str
    message_type: MessageType
    sender_id: str
    recipient_id: Optional[str]  # None for broadcast
    priority: MessagePriority
    timestamp: str
    ttl_seconds: int  # Time to live
    payload: Dict[str, Any]
    correlation_id: Optional[str] = None  # For request/response patterns
    retry_count: int = 0
    max_retries: int = 3

@dataclass
class AgentSubscription:
    """Agent subscription to message queues"""
    agent_id: str
    message_types: List[MessageType]
    callback: Optional[Callable] = None
    last_heartbeat: Optional[str] = None

class SINCORMessageBus:
    """
    Redis-based message bus for SINCOR agent swarm coordination
    Handles high-throughput, reliable messaging between all 43 agents
    """
    
    def __init__(self, redis_url: str = "redis://localhost:6379", db: int = 0):
        self.redis_url = redis_url
        self.db = db
        self.redis_pool = None
        self.pubsub = None
        
        # Message routing and queues
        self.agent_subscriptions: Dict[str, AgentSubscription] = {}
        self.message_handlers: Dict[MessageType, List[Callable]] = {}
        
        # Performance metrics
        self.message_stats = {
            "messages_sent": 0,
            "messages_received": 0,
            "failed_deliveries": 0,
            "avg_latency_ms": 0.0
        }
        
        # Dead letter queue for failed messages
        self.dlq_enabled = True
        
        logger.info("🌐 SINCOR Message Bus initializing...")
    
    async def initialize(self):
        """Initialize Redis connections and message bus"""
        
        try:
            # Create Redis connection pool
            self.redis_pool = redis.ConnectionPool.from_url(
                self.redis_url,
                db=self.db,
                max_connections=100,  # Support high concurrency
                retry_on_timeout=True
            )
            
            # Test connection
            redis_client = redis.Redis(connection_pool=self.redis_pool)
            await redis_client.ping()
            
            # Initialize pub/sub
            self.pubsub = redis_client.pubsub()
            
            # Create message bus channels
            await self._create_standard_channels()
            
            logger.info("✅ Message bus initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize message bus: {str(e)}")
            raise
    
    async def _create_standard_channels(self):
        """Create standard message channels for different message types"""
        
        channels = [
            "sincor:tasks:broadcast",      # Task broadcasts to all agents
            "sincor:tasks:bids",          # Task bid submissions
            "sincor:tasks:assignments",    # Task assignments
            "sincor:tasks:updates",       # Task progress updates
            "sincor:agents:status",       # Agent status updates
            "sincor:knowledge:share",     # Knowledge sharing
            "sincor:coordination:global", # Global coordination messages
            "sincor:system:alerts",       # System-wide alerts
            "sincor:heartbeat",           # Agent heartbeats
            "sincor:dlq"                  # Dead letter queue
        ]
        
        redis_client = redis.Redis(connection_pool=self.redis_pool)
        
        for channel in channels:
            # Ensure channel exists and set up any necessary configurations
            await redis_client.sadd("sincor:channels:active", channel)
        
        logger.info(f"📺 Created {len(channels)} message channels")
    
    async def register_agent(self, agent_id: str, message_types: List[MessageType],
                           callback: Optional[Callable] = None) -> bool:
        """Register an agent to receive messages"""
        
        try:
            subscription = AgentSubscription(
                agent_id=agent_id,
                message_types=message_types,
                callback=callback,
                last_heartbeat=datetime.now(timezone.utc).isoformat()
            )
            
            self.agent_subscriptions[agent_id] = subscription
            
            # Subscribe to relevant channels
            for msg_type in message_types:
                channel = self._get_channel_for_message_type(msg_type)
                if channel:
                    await self.pubsub.subscribe(channel)
            
            # Subscribe to agent-specific channel
            agent_channel = f"sincor:agent:{agent_id}"
            await self.pubsub.subscribe(agent_channel)
            
            logger.info(f"🤖 Agent {agent_id} registered for {len(message_types)} message types")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to register agent {agent_id}: {str(e)}")
            return False
    
    async def send_message(self, message: Message) -> bool:
        """Send a message through the message bus"""
        
        start_time = time.time()
        
        try:
            redis_client = redis.Redis(connection_pool=self.redis_pool)
            
            # Serialize message
            message_data = json.dumps(asdict(message), default=str)
            
            # Determine routing
            if message.recipient_id:
                # Direct message to specific agent
                channel = f"sincor:agent:{message.recipient_id}"
                await redis_client.publish(channel, message_data)
                
                # Also store in agent's queue for reliability
                queue_key = f"sincor:queue:{message.recipient_id}"
                await redis_client.lpush(queue_key, message_data)
                await redis_client.expire(queue_key, message.ttl_seconds)
                
            else:
                # Broadcast message
                channel = self._get_channel_for_message_type(message.message_type)
                if channel:
                    await redis_client.publish(channel, message_data)
                    
                    # Store in broadcast queue
                    broadcast_key = f"sincor:broadcast:{message.message_type.value}"
                    await redis_client.lpush(broadcast_key, message_data)
                    await redis_client.expire(broadcast_key, message.ttl_seconds)
            
            # Update statistics
            self.message_stats["messages_sent"] += 1
            
            latency = (time.time() - start_time) * 1000
            self.message_stats["avg_latency_ms"] = (
                (self.message_stats["avg_latency_ms"] * (self.message_stats["messages_sent"] - 1) + latency) /
                self.message_stats["messages_sent"]
            )
            
            logger.debug(f"📤 Message {message.message_id} sent in {latency:.2f}ms")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to send message {message.message_id}: {str(e)}")
            
            # Send to dead letter queue if enabled
            if self.dlq_enabled:
                await self._send_to_dlq(message, str(e))
            
            self.message_stats["failed_deliveries"] += 1
            return False
    
    async def receive_messages(self, agent_id: str, timeout: int = 1) -> AsyncGenerator[Message, None]:
        """Receive messages for a specific agent"""
        
        if agent_id not in self.agent_subscriptions:
            logger.warning(f"⚠️ Agent {agent_id} not registered")
            return
        
        redis_client = redis.Redis(connection_pool=self.redis_pool)
        queue_key = f"sincor:queue:{agent_id}"
        
        try:
            while True:
                # Check agent queue for reliable delivery
                message_data = await redis_client.brpop(queue_key, timeout=timeout)
                
                if message_data:
                    try:
                        _, raw_message = message_data
                        message_dict = json.loads(raw_message)
                        
                        # Reconstruct message object
                        message_dict['message_type'] = MessageType(message_dict['message_type'])
                        message_dict['priority'] = MessagePriority(message_dict['priority'])
                        
                        message = Message(**message_dict)
                        
                        self.message_stats["messages_received"] += 1
                        logger.debug(f"📥 Agent {agent_id} received message {message.message_id}")
                        
                        yield message
                        
                    except Exception as e:
                        logger.error(f"❌ Failed to parse message for {agent_id}: {str(e)}")
                        continue
                
                # Small delay to prevent busy waiting
                await asyncio.sleep(0.01)
                
        except Exception as e:
            logger.error(f"❌ Error receiving messages for {agent_id}: {str(e)}")
    
    async def broadcast_task(self, task_data: Dict[str, Any], sender_id: str) -> str:
        """Broadcast a task to all available agents"""
        
        message_id = f"task-{uuid.uuid4().hex[:12]}"
        
        message = Message(
            message_id=message_id,
            message_type=MessageType.TASK_BROADCAST,
            sender_id=sender_id,
            recipient_id=None,  # Broadcast
            priority=MessagePriority.HIGH,
            timestamp=datetime.now(timezone.utc).isoformat(),
            ttl_seconds=3600,  # 1 hour TTL
            payload=task_data
        )
        
        success = await self.send_message(message)
        
        if success:
            logger.info(f"📢 Task {task_data.get('task_id', 'unknown')} broadcasted")
        
        return message_id
    
    async def submit_bid(self, task_id: str, agent_id: str, bid_data: Dict[str, Any]) -> str:
        """Submit a bid for a task"""
        
        message_id = f"bid-{uuid.uuid4().hex[:8]}"
        
        message = Message(
            message_id=message_id,
            message_type=MessageType.TASK_BID,
            sender_id=agent_id,
            recipient_id="task_market",  # Send to task market coordinator
            priority=MessagePriority.NORMAL,
            timestamp=datetime.now(timezone.utc).isoformat(),
            ttl_seconds=1800,  # 30 minutes TTL
            payload={
                "task_id": task_id,
                "agent_id": agent_id,
                **bid_data
            }
        )
        
        success = await self.send_message(message)
        
        if success:
            logger.info(f"💰 Bid submitted by {agent_id} for task {task_id}")
        
        return message_id
    
    async def send_heartbeat(self, agent_id: str, status_data: Dict[str, Any]) -> bool:
        """Send agent heartbeat with status information"""
        
        message = Message(
            message_id=f"heartbeat-{agent_id}-{int(time.time())}",
            message_type=MessageType.HEARTBEAT,
            sender_id=agent_id,
            recipient_id=None,  # Broadcast heartbeat
            priority=MessagePriority.LOW,
            timestamp=datetime.now(timezone.utc).isoformat(),
            ttl_seconds=300,  # 5 minutes TTL
            payload={
                "agent_id": agent_id,
                "status": "alive",
                **status_data
            }
        )
        
        # Update subscription heartbeat
        if agent_id in self.agent_subscriptions:
            self.agent_subscriptions[agent_id].last_heartbeat = message.timestamp
        
        return await self.send_message(message)
    
    async def share_knowledge(self, agent_id: str, knowledge_data: Dict[str, Any]) -> str:
        """Share knowledge with other agents"""
        
        message_id = f"knowledge-{uuid.uuid4().hex[:8]}"
        
        message = Message(
            message_id=message_id,
            message_type=MessageType.KNOWLEDGE_SHARE,
            sender_id=agent_id,
            recipient_id=None,  # Broadcast knowledge
            priority=MessagePriority.NORMAL,
            timestamp=datetime.now(timezone.utc).isoformat(),
            ttl_seconds=7200,  # 2 hours TTL
            payload=knowledge_data
        )
        
        success = await self.send_message(message)
        
        if success:
            logger.info(f"🧠 Knowledge shared by {agent_id}")
        
        return message_id
    
    async def _send_to_dlq(self, message: Message, error: str):
        """Send failed message to dead letter queue"""
        
        try:
            redis_client = redis.Redis(connection_pool=self.redis_pool)
            
            dlq_entry = {
                "original_message": asdict(message),
                "error": error,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "retry_count": message.retry_count
            }
            
            await redis_client.lpush("sincor:dlq", json.dumps(dlq_entry, default=str))
            logger.warning(f"💀 Message {message.message_id} sent to DLQ: {error}")
            
        except Exception as e:
            logger.error(f"❌ Failed to send message to DLQ: {str(e)}")
    
    def _get_channel_for_message_type(self, message_type: MessageType) -> str:
        """Get Redis channel name for message type"""
        
        channel_mapping = {
            MessageType.TASK_BROADCAST: "sincor:tasks:broadcast",
            MessageType.TASK_BID: "sincor:tasks:bids",
            MessageType.TASK_ASSIGNMENT: "sincor:tasks:assignments",
            MessageType.TASK_UPDATE: "sincor:tasks:updates",
            MessageType.TASK_COMPLETION: "sincor:tasks:completion",
            MessageType.AGENT_STATUS: "sincor:agents:status",
            MessageType.KNOWLEDGE_SHARE: "sincor:knowledge:share",
            MessageType.COORDINATION: "sincor:coordination:global",
            MessageType.SYSTEM_ALERT: "sincor:system:alerts",
            MessageType.HEARTBEAT: "sincor:heartbeat"
        }
        
        return channel_mapping.get(message_type, "sincor:general")
    
    async def get_message_bus_stats(self) -> Dict[str, Any]:
        """Get comprehensive message bus statistics"""
        
        redis_client = redis.Redis(connection_pool=self.redis_pool)
        
        # Get Redis info
        redis_info = await redis_client.info()
        
        # Get queue depths
        queue_depths = {}
        for agent_id in self.agent_subscriptions.keys():
            queue_key = f"sincor:queue:{agent_id}"
            depth = await redis_client.llen(queue_key)
            if depth > 0:
                queue_depths[agent_id] = depth
        
        # Get DLQ size
        dlq_size = await redis_client.llen("sincor:dlq")
        
        # Active agents (with recent heartbeats)
        current_time = datetime.now(timezone.utc)
        active_agents = 0
        
        for agent_id, subscription in self.agent_subscriptions.items():
            if subscription.last_heartbeat:
                heartbeat_time = datetime.fromisoformat(subscription.last_heartbeat.replace('Z', '+00:00'))
                if (current_time - heartbeat_time).total_seconds() < 300:  # 5 minutes
                    active_agents += 1
        
        return {
            "message_stats": self.message_stats.copy(),
            "agent_stats": {
                "total_registered": len(self.agent_subscriptions),
                "active_agents": active_agents,
                "queue_depths": queue_depths
            },
            "system_health": {
                "dlq_size": dlq_size,
                "redis_connected_clients": redis_info.get("connected_clients", 0),
                "redis_used_memory": redis_info.get("used_memory_human", "unknown"),
                "redis_keyspace_hits": redis_info.get("keyspace_hits", 0)
            }
        }
    
    async def cleanup_expired_messages(self):
        """Clean up expired messages and perform maintenance"""
        
        redis_client = redis.Redis(connection_pool=self.redis_pool)
        
        try:
            # Redis TTL handles most cleanup automatically, but we can do additional maintenance
            
            # Remove inactive agent queues
            current_time = datetime.now(timezone.utc)
            inactive_agents = []
            
            for agent_id, subscription in self.agent_subscriptions.items():
                if subscription.last_heartbeat:
                    heartbeat_time = datetime.fromisoformat(subscription.last_heartbeat.replace('Z', '+00:00'))
                    if (current_time - heartbeat_time).total_seconds() > 1800:  # 30 minutes
                        inactive_agents.append(agent_id)
            
            for agent_id in inactive_agents:
                queue_key = f"sincor:queue:{agent_id}"
                await redis_client.delete(queue_key)
                logger.info(f"🧹 Cleaned up inactive agent queue: {agent_id}")
            
            logger.info("✅ Message bus maintenance completed")
            
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {str(e)}")

async def demo_message_bus():
    """Demo the SINCOR Message Bus system"""
    
    print("🌐 SINCOR Message Bus Demo")
    print("=" * 40)
    
    # Initialize message bus
    bus = SINCORMessageBus()
    await bus.initialize()
    
    # Register some demo agents
    scout_types = [MessageType.TASK_BROADCAST, MessageType.TASK_ASSIGNMENT, MessageType.KNOWLEDGE_SHARE]
    await bus.register_agent("E-auriga-01", scout_types)
    
    director_types = [MessageType.TASK_BROADCAST, MessageType.COORDINATION, MessageType.SYSTEM_ALERT]
    await bus.register_agent("E-polaris-09", director_types)
    
    # Send some demo messages
    
    # 1. Broadcast a task
    task_data = {
        "task_id": "T-demo-001",
        "goal": "Research competitive landscape",
        "skills_required": ["prospect", "research"],
        "reward": 150
    }
    
    msg_id = await bus.broadcast_task(task_data, "system")
    print(f"📢 Task broadcasted: {msg_id}")
    
    # 2. Submit a bid
    bid_data = {
        "confidence": 0.85,
        "estimated_cost_tokens": 6000,
        "plan": ["search", "analyze", "report"]
    }
    
    bid_id = await bus.submit_bid("T-demo-001", "E-auriga-01", bid_data)
    print(f"💰 Bid submitted: {bid_id}")
    
    # 3. Send heartbeats
    await bus.send_heartbeat("E-auriga-01", {"status": "active", "current_task": "T-demo-001"})
    await bus.send_heartbeat("E-polaris-09", {"status": "coordinating", "agents_managed": 5})
    
    # 4. Share knowledge
    knowledge = {
        "knowledge_type": "market_insight",
        "content": "AI market showing 40% growth in enterprise segment",
        "confidence": 0.9,
        "source": "competitive_research"
    }
    
    knowledge_id = await bus.share_knowledge("E-auriga-01", knowledge)
    print(f"🧠 Knowledge shared: {knowledge_id}")
    
    # Get statistics
    stats = await bus.get_message_bus_stats()
    print("\\n📊 Message Bus Statistics:")
    print(f"  Messages sent: {stats['message_stats']['messages_sent']}")
    print(f"  Active agents: {stats['agent_stats']['active_agents']}")
    print(f"  Average latency: {stats['message_stats']['avg_latency_ms']:.2f}ms")
    
    print("\\n✅ Message bus demo completed successfully!")

if __name__ == "__main__":
    asyncio.run(demo_message_bus())