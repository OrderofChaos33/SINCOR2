#!/usr/bin/env python3
"""
Cor-tecs Core - Claude Integration for SINCOR Nested Learning

Central reasoning engine that uses Claude as the core brain for:
- Multi-agent coordination
- Complex reasoning tasks
- Nested learning algorithms
- Strategic decision making
- Cross-agent knowledge synthesis

FIXED: Now uses real Anthropic Claude API instead of mocks
"""

import json
import os
import asyncio
import hashlib
from typing import Dict, List, Optional, Any, Union, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import uuid

# FIXED: Import real Anthropic SDK
from anthropic import Anthropic, AsyncAnthropic


class ClaudeClient:
    """Claude API client (PRODUCTION - uses real Anthropic API)"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

        if not self.api_key:
            print("WARNING: ANTHROPIC_API_KEY not set - Claude integration will fail")
            print("Set environment variable: ANTHROPIC_API_KEY=sk-ant-api03-...")
            self.client = None
            self.async_client = None
        else:
            self.client = Anthropic(api_key=self.api_key)
            self.async_client = AsyncAnthropic(api_key=self.api_key)

        # Use latest Sonnet model
        self.model = "claude-sonnet-4-5-20250929"

    async def complete(self, prompt: str, max_tokens: int = 4000, system: str = None) -> str:
        """Complete a prompt using Claude API (async)"""
        if not self.async_client:
            raise ValueError("Claude API not configured - set ANTHROPIC_API_KEY environment variable")

        try:
            # Build system message
            if not system:
                system = "You are a helpful AI assistant for business automation and agent coordination."

            # Call Claude API
            message = await self.async_client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            # Extract text content from response
            return message.content[0].text

        except Exception as e:
            error_msg = f"Claude API error: {str(e)}"
            print(error_msg)
            raise Exception(error_msg)

    def complete_sync(self, prompt: str, max_tokens: int = 4000, system: str = None) -> str:
        """Complete a prompt using Claude API (synchronous)"""
        if not self.client:
            raise ValueError("Claude API not configured - set ANTHROPIC_API_KEY environment variable")

        try:
            # Build system message
            if not system:
                system = "You are a helpful AI assistant for business automation and agent coordination."

            # Call Claude API synchronously
            message = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            # Extract text content from response
            return message.content[0].text

        except Exception as e:
            error_msg = f"Claude API error: {str(e)}"
            print(error_msg)
            raise Exception(error_msg)


@dataclass
class NestedLearningTask:
    """Task for nested learning algorithm"""
    task_id: str
    task_type: str  # reasoning, coordination, synthesis, learning
    context: Dict[str, Any]
    complexity_level: int  # 1-5, determines nesting depth
    assigned_agents: List[str]
    parent_task_id: Optional[str] = None
    child_task_ids: List[str] = None
    status: str = "pending"
    created: str = None

    def __post_init__(self):
        if not self.created:
            self.created = datetime.now().isoformat()
        if not self.child_task_ids:
            self.child_task_ids = []

@dataclass
class LearningOutcome:
    """Result from nested learning process"""
    outcome_id: str
    source_task_id: str
    reasoning_chain: List[Dict[str, Any]]
    confidence: float
    evidence: List[str]
    recommendations: List[str]
    timestamp: str = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

class CortecsBrain:
    """Central reasoning engine powered by Claude"""

    def __init__(self, api_key: Optional[str] = None):
        self.claude = ClaudeClient(api_key)
        self.task_history: List[NestedLearningTask] = []
        self.learning_outcomes: List[LearningOutcome] = []
        self.memory_cache: Dict[str, Any] = {}

    async def reason(self, context: Dict[str, Any], task_type: str = "general") -> str:
        """Perform reasoning using Claude"""

        # Build reasoning prompt
        prompt = self._build_reasoning_prompt(context, task_type)

        # Get Claude's response
        response = await self.claude.complete(
            prompt=prompt,
            max_tokens=4000,
            system="You are an expert AI reasoning engine for the SINCOR multi-agent platform. Provide structured, logical analysis with clear recommendations."
        )

        return response

    def reason_sync(self, context: Dict[str, Any], task_type: str = "general") -> str:
        """Perform reasoning using Claude (synchronous)"""

        # Build reasoning prompt
        prompt = self._build_reasoning_prompt(context, task_type)

        # Get Claude's response
        response = self.claude.complete_sync(
            prompt=prompt,
            max_tokens=4000,
            system="You are an expert AI reasoning engine for the SINCOR multi-agent platform. Provide structured, logical analysis with clear recommendations."
        )

        return response

    async def coordinate_agents(self, task: Dict[str, Any], available_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Coordinate agent assignment for a task"""

        # Build coordination prompt
        prompt = f"""
Task to coordinate:
{json.dumps(task, indent=2)}

Available agents:
{json.dumps(available_agents, indent=2)}

Please analyze this task and recommend:
1. Which agent(s) should handle this task
2. Task breakdown if multiple agents needed
3. Estimated timeline and resource allocation
4. Success criteria and checkpoints

Provide your response in structured JSON format.
"""

        response = await self.claude.complete(
            prompt=prompt,
            max_tokens=2000,
            system="You are the SINCOR swarm coordinator. Assign tasks to the most suitable agents based on their archetypes, skills, and current load."
        )

        try:
            # Try to parse as JSON
            coordination_plan = json.loads(response)
            return coordination_plan
        except json.JSONDecodeError:
            # Return as text if not valid JSON
            return {"recommendation": response}

    def coordinate_agents_sync(self, task: Dict[str, Any], available_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Coordinate agent assignment for a task (synchronous)"""

        # Build coordination prompt
        prompt = f"""
Task to coordinate:
{json.dumps(task, indent=2)}

Available agents:
{json.dumps(available_agents, indent=2)}

Please analyze this task and recommend:
1. Which agent(s) should handle this task
2. Task breakdown if multiple agents needed
3. Estimated timeline and resource allocation
4. Success criteria and checkpoints

Provide your response in structured JSON format.
"""

        response = self.claude.complete_sync(
            prompt=prompt,
            max_tokens=2000,
            system="You are the SINCOR swarm coordinator. Assign tasks to the most suitable agents based on their archetypes, skills, and current load."
        )

        try:
            # Try to parse as JSON
            coordination_plan = json.loads(response)
            return coordination_plan
        except json.JSONDecodeError:
            # Return as text if not valid JSON
            return {"recommendation": response}

    def _build_reasoning_prompt(self, context: Dict[str, Any], task_type: str) -> str:
        """Build a reasoning prompt based on context and task type"""

        prompt = f"Task Type: {task_type}\n\n"
        prompt += "Context:\n"
        prompt += json.dumps(context, indent=2)
        prompt += "\n\nPlease analyze this situation and provide:"
        prompt += "\n1. Key insights from the context"
        prompt += "\n2. Logical reasoning chain"
        prompt += "\n3. Recommended actions with confidence levels"
        prompt += "\n4. Potential risks or considerations"

        return prompt

    async def synthesize_knowledge(self, data_sources: List[Dict[str, Any]]) -> str:
        """Synthesize knowledge from multiple data sources"""

        prompt = f"""
Multiple data sources to synthesize:
{json.dumps(data_sources, indent=2)}

Please synthesize these data sources into:
1. Unified knowledge representation
2. Key patterns and insights
3. Contradictions or conflicts (if any)
4. Actionable conclusions
"""

        response = await self.claude.complete(
            prompt=prompt,
            max_tokens=3000,
            system="You are a knowledge synthesis engine for SINCOR. Combine multiple sources into coherent, actionable insights."
        )

        return response

    def get_task_history(self, limit: int = 10) -> List[NestedLearningTask]:
        """Get recent task history"""
        return self.task_history[-limit:]

    def get_learning_outcomes(self, limit: int = 10) -> List[LearningOutcome]:
        """Get recent learning outcomes"""
        return self.learning_outcomes[-limit:]


# Utility functions for backward compatibility
async def perform_reasoning(context: Dict[str, Any]) -> str:
    """Perform reasoning using Cortecs Brain"""
    brain = CortecsBrain()
    return await brain.reason(context)

def perform_reasoning_sync(context: Dict[str, Any]) -> str:
    """Perform reasoning using Cortecs Brain (synchronous)"""
    brain = CortecsBrain()
    return brain.reason_sync(context)


# Test function
async def test_cortecs():
    """Test Cortecs Brain with Claude API"""
    try:
        print("Testing Cortecs Brain with Claude API...")

        brain = CortecsBrain()

        # Test reasoning
        test_context = {
            "situation": "New client requesting instant BI service",
            "budget": 5000,
            "timeline": "1 week",
            "requirements": ["market analysis", "competitor research", "growth recommendations"]
        }

        print("\nTesting reasoning...")
        result = await brain.reason(test_context, task_type="business_intelligence")
        print(f"Claude's reasoning:\n{result[:500]}...\n")

        # Test agent coordination
        test_task = {
            "task_id": "test-001",
            "description": "Conduct market research for new product launch",
            "priority": "high"
        }

        test_agents = [
            {"id": "E-auriga-01", "archetype": "Scout", "specialization": "market_intelligence"},
            {"id": "E-vega-02", "archetype": "Scout", "specialization": "lead_prospecting"},
            {"id": "E-deneb-05", "archetype": "Synthesizer", "specialization": "data_analysis"}
        ]

        print("Testing agent coordination...")
        coordination = await brain.coordinate_agents(test_task, test_agents)
        print(f"Coordination plan:\n{json.dumps(coordination, indent=2)}\n")

        print("✅ Cortecs Brain integration successful!")

    except Exception as e:
        print(f"❌ Cortecs test failed: {e}")


if __name__ == "__main__":
    # Test if ANTHROPIC_API_KEY is set
    if os.getenv("ANTHROPIC_API_KEY"):
        asyncio.run(test_cortecs())
    else:
        print("⚠️  ANTHROPIC_API_KEY not set")
        print("Set it with: export ANTHROPIC_API_KEY='sk-ant-api03-...'")
        print("\nTesting basic instantiation...")
        brain = CortecsBrain()
        print("✅ CortecsBrain instantiated (API calls will fail without key)")
