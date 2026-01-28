#!/usr/bin/env python3
"""
Mock Claude API for development and testing
Works exactly like the real Claude integration but with simulated responses
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, Any
from claude_integration import CorTecsClaudeCore, ConversationTurn
import random

class MockClaudeCore(CorTecsClaudeCore):
    """Mock version of Claude Core for development without API key"""
    
    def __init__(self, data_dir: str = "cortecs_data_mock"):
        # Initialize without API key
        self.client = None  # No real client
        self.data_dir = data_dir
        
        # Initialize the rest normally
        super().__init__("mock-api-key", data_dir)
        print("🧠 Mock Claude Core initialized - Ready for development!")
    
    async def chat(self, user_message: str, model: str = "claude-3-5-sonnet-20241022") -> str:
        """
        Mock chat that simulates Claude responses
        Still does all the learning and data gathering
        """
        
        if not self.current_conversation_id:
            await self.start_conversation()
        
        print(f"🔄 Processing message with Mock Claude {model}...")
        
        # Simulate Claude response based on message content
        claude_response = self._generate_mock_response(user_message)
        
        # Simulate token usage
        tokens_used = {
            "input": len(user_message.split()) * 4,  # Rough estimate
            "output": len(claude_response.split()) * 4,
            "total": (len(user_message.split()) + len(claude_response.split())) * 4
        }
        
        # Create conversation turn record (same as real version)
        turn_id = f"turn-{random.randint(10000, 99999)}"
        
        conversation_turn = ConversationTurn(
            turn_id=turn_id,
            conversation_id=self.current_conversation_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_message=user_message,
            claude_response=claude_response,
            model_used=f"mock-{model}",
            tokens_used=tokens_used,
            conversation_context={"turn_number": len(self.conversation_history) + 1},
            business_relevance_score=0.0,
            knowledge_extracted=[],
            metadata={"mock": True},
            backup_hash=""
        )
        
        # All the same learning and storage as real version
        if self.learning_enabled:
            await self._analyze_and_extract_knowledge(conversation_turn)
        
        conversation_turn.backup_hash = self._calculate_backup_hash(conversation_turn)
        await self._store_conversation_turn(conversation_turn)
        
        if self.auto_backup_enabled:
            await self._create_backup(conversation_turn)
        
        self.conversation_history.append(conversation_turn)
        
        print(f"✅ Mock turn {turn_id} - Tokens: {tokens_used['total']}")
        return claude_response
    
    def _generate_mock_response(self, user_message: str) -> str:
        """Generate realistic mock responses based on message content"""
        
        msg_lower = user_message.lower()
        
        # SINCOR/business related
        if 'sincor' in msg_lower or 'agent' in msg_lower:
            return """Excellent! SINCOR's multi-agent architecture is fascinating. With 43 persistent agents across 7 archetypes, you're building something truly innovative. The contract-net protocol for task coordination is particularly clever - it eliminates central bottlenecks while maintaining organized workflow.

For your competitive intelligence use case, I'd recommend starting with Scout agents for data gathering, Synthesizer agents for analysis, and a Director agent for strategic oversight. This creates a powerful intelligence pipeline that can scale naturally."""
        
        # Enterprise/business questions
        elif 'enterprise' in msg_lower or 'pilot' in msg_lower:
            return """For enterprise pilots, I'd focus on companies facing multi-step workflow challenges - financial services for regulatory compliance, healthcare for patient data processing, or manufacturing for supply chain optimization. 

The key is demonstrating clear ROI through automation of complex, knowledge-intensive processes that currently require significant human coordination."""
        
        # Technical questions
        elif 'technical' in msg_lower or 'code' in msg_lower or 'api' in msg_lower:
            return """From a technical perspective, your architecture is sound. The Redis message bus will handle high-throughput communication efficiently, and the multi-tier memory system ensures agents learn from experience.

I'd prioritize building the web dashboard next - visibility into agent behavior will be crucial for debugging and demonstrating value to stakeholders."""
        
        # Competitive intelligence
        elif 'competitive' in msg_lower or 'intelligence' in msg_lower:
            return """Competitive intelligence is an ideal first use case because it's:

1. High-value but labor-intensive for humans
2. Benefits from continuous monitoring (perfect for persistent agents)  
3. Requires diverse skills (research, analysis, synthesis) - showcasing your multi-archetype approach
4. Has clear, measurable outcomes that executives understand

I'd structure it as: Scout agents for data gathering → Synthesizer for analysis → Director for strategic insights → presentation to stakeholders."""
        
        # General business
        else:
            return f"""I understand you're working on {user_message}. As your AI business partner, I'm here to help you think through the strategic and technical aspects.

Based on our conversations, I know you're building something innovative with SINCOR and focusing on practical business applications. What specific aspects would you like to explore further?"""

# Quick demo
async def demo_mock_claude():
    """Demo the mock Claude system"""
    print("🎭 Mock Claude Demo")
    print("=" * 30)
    
    # Initialize mock system
    mock_claude = MockClaudeCore()
    
    # Test conversation
    await mock_claude.start_conversation("SINCOR Development")
    
    response1 = await mock_claude.chat("I'm building SINCOR with 43 agents. What should I focus on first?")
    print(f"Mock Claude: {response1[:150]}...")
    
    response2 = await mock_claude.chat("How do I approach enterprise pilots?")
    print(f"Mock Claude: {response2[:150]}...")
    
    # Show analytics
    analytics = mock_claude.get_conversation_analytics()
    print("\\n📊 Analytics:")
    print(f"Turns: {analytics['conversation_stats']['total_turns']}")
    print(f"Knowledge records: {analytics['learning_stats']['total_knowledge_records']}")

if __name__ == "__main__":
    asyncio.run(demo_mock_claude())