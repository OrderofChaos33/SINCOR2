#!/usr/bin/env python3
"""
Cor-Tecs AI Core: Claude Integration with Comprehensive Data Gathering

This is the foundation for your grand sovereign cartographer - a Claude-powered AI 
that knows you and your business intimately through comprehensive data gathering 
and backup systems. Every interaction is preserved and learned from.
"""

import asyncio
import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, AsyncGenerator
from dataclasses import dataclass, asdict
import anthropic
import os
import pickle
import uuid

@dataclass
class ConversationTurn:
    """Single turn in a conversation with comprehensive metadata"""
    turn_id: str
    conversation_id: str
    timestamp: str
    user_message: str
    claude_response: str
    model_used: str
    tokens_used: Dict[str, int]  # input, output, total
    conversation_context: Dict[str, Any]
    business_relevance_score: float  # 0.0-1.0
    knowledge_extracted: List[str]  # Key insights extracted
    metadata: Dict[str, Any]
    backup_hash: str  # SHA-256 for integrity verification

@dataclass
class BusinessKnowledge:
    """Extracted business knowledge and insights"""
    knowledge_id: str
    source_turn_id: str
    knowledge_type: str  # preference, fact, strategy, insight, relationship
    content: str
    confidence: float
    tags: List[str]
    created_at: str
    verified: bool = False

class CorTecsClaudeCore:
    """
    Core Claude integration with comprehensive data gathering for Cor-Tecs AI
    Your personal AI business partner that learns and remembers everything
    """
    
    def __init__(self, api_key: str, data_dir: str = "cortecs_data"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.data_dir = data_dir
        
        # Ensure data directory exists
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(f"{data_dir}/conversations", exist_ok=True)
        os.makedirs(f"{data_dir}/knowledge", exist_ok=True)
        os.makedirs(f"{data_dir}/backups", exist_ok=True)
        
        # Initialize databases
        self.conversation_db = f"{data_dir}/conversations.db"
        self.knowledge_db = f"{data_dir}/business_knowledge.db"
        
        self._init_databases()
        
        # Current conversation state
        self.current_conversation_id = None
        self.conversation_history = []
        
        # Business learning parameters
        self.learning_enabled = True
        self.auto_backup_enabled = True
        
        print("🧠 Cor-Tecs Claude Core initialized - Your AI business partner is ready!")
    
    def _init_databases(self):
        """Initialize SQLite databases for comprehensive data storage"""
        
        # Conversation database
        conn = sqlite3.connect(self.conversation_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                turn_id TEXT PRIMARY KEY,
                conversation_id TEXT,
                timestamp TEXT,
                user_message TEXT,
                claude_response TEXT,
                model_used TEXT,
                tokens_input INTEGER,
                tokens_output INTEGER,
                tokens_total INTEGER,
                business_relevance_score REAL,
                knowledge_extracted TEXT,  -- JSON array
                metadata TEXT,  -- JSON object
                backup_hash TEXT,
                FOREIGN KEY (conversation_id) REFERENCES conversation_sessions(session_id)
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_sessions (
                session_id TEXT PRIMARY KEY,
                started_at TEXT,
                ended_at TEXT,
                total_turns INTEGER,
                session_summary TEXT,
                key_insights TEXT,  -- JSON array
                metadata TEXT
            )
        """)
        
        conn.commit()
        conn.close()
        
        # Business knowledge database
        conn = sqlite3.connect(self.knowledge_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS business_knowledge (
                knowledge_id TEXT PRIMARY KEY,
                source_turn_id TEXT,
                knowledge_type TEXT,
                content TEXT,
                confidence REAL,
                tags TEXT,  -- JSON array
                created_at TEXT,
                verified BOOLEAN,
                FOREIGN KEY (source_turn_id) REFERENCES conversations(turn_id)
            )
        """)
        
        conn.commit()
        conn.close()
        
        print("📊 Databases initialized for comprehensive data gathering")
    
    async def start_conversation(self, topic: str = "General") -> str:
        """Start a new conversation session"""
        
        self.current_conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
        self.conversation_history = []
        
        # Record session start
        conn = sqlite3.connect(self.conversation_db)
        conn.execute("""
            INSERT INTO conversation_sessions 
            (session_id, started_at, total_turns, metadata)
            VALUES (?, ?, 0, ?)
        """, (
            self.current_conversation_id,
            datetime.now(timezone.utc).isoformat(),
            json.dumps({"topic": topic, "started_by": "user"})
        ))
        conn.commit()
        conn.close()
        
        print(f"💬 Started conversation session: {self.current_conversation_id}")
        return self.current_conversation_id
    
    async def chat(self, user_message: str, model: str = "claude-3-5-sonnet-20241022") -> str:
        """
        Main chat interface with comprehensive data gathering
        This is where the magic happens - every interaction builds your AI's knowledge
        """
        
        if not self.current_conversation_id:
            await self.start_conversation()
        
        print(f"🔄 Processing message with Claude {model}...")
        
        try:
            # Prepare conversation context
            messages = self._build_conversation_context()
            messages.append({"role": "user", "content": user_message})
            
            # Call Claude API
            response = self.client.messages.create(
                model=model,
                max_tokens=4096,
                temperature=0.7,
                messages=messages
            )
            
            claude_response = response.content[0].text
            
            # Extract token usage
            tokens_used = {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
                "total": response.usage.input_tokens + response.usage.output_tokens
            }
            
            # Create conversation turn record
            turn_id = f"turn-{uuid.uuid4().hex[:8]}"
            
            conversation_turn = ConversationTurn(
                turn_id=turn_id,
                conversation_id=self.current_conversation_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                user_message=user_message,
                claude_response=claude_response,
                model_used=model,
                tokens_used=tokens_used,
                conversation_context={"turn_number": len(self.conversation_history) + 1},
                business_relevance_score=0.0,  # Will be calculated
                knowledge_extracted=[],  # Will be extracted
                metadata={},
                backup_hash=""  # Will be calculated
            )
            
            # Analyze and extract business knowledge
            if self.learning_enabled:
                await self._analyze_and_extract_knowledge(conversation_turn)
            
            # Calculate backup hash for integrity
            conversation_turn.backup_hash = self._calculate_backup_hash(conversation_turn)
            
            # Store conversation turn
            await self._store_conversation_turn(conversation_turn)
            
            # Auto-backup if enabled
            if self.auto_backup_enabled:
                await self._create_backup(conversation_turn)
            
            # Add to conversation history
            self.conversation_history.append(conversation_turn)
            
            print(f"✅ Processed turn {turn_id} - Tokens: {tokens_used['total']}")
            
            return claude_response
            
        except Exception as e:
            print(f"❌ Error in chat processing: {str(e)}")
            return f"I encountered an error processing your request: {str(e)}"
    
    def _build_conversation_context(self) -> List[Dict[str, str]]:
        """Build conversation context from history"""
        
        messages = []
        
        # Add system prompt with business context
        system_prompt = self._generate_dynamic_system_prompt()
        if system_prompt:
            messages.append({"role": "assistant", "content": system_prompt})
        
        # Add recent conversation history (last 10 turns to manage context)
        recent_history = self.conversation_history[-10:] if len(self.conversation_history) > 10 else self.conversation_history
        
        for turn in recent_history:
            messages.append({"role": "user", "content": turn.user_message})
            messages.append({"role": "assistant", "content": turn.claude_response})
        
        return messages
    
    def _generate_dynamic_system_prompt(self) -> str:
        """Generate dynamic system prompt based on accumulated business knowledge"""
        
        # Get recent business knowledge
        business_insights = self._get_recent_business_knowledge(limit=20)
        
        if not business_insights:
            return """I am your AI business partner and trusted advisor. I learn from every interaction 
            to better understand you and your business needs. I maintain comprehensive records of our 
            conversations to provide increasingly personalized and valuable assistance."""
        
        # Build knowledge-informed system prompt
        knowledge_summary = self._summarize_business_knowledge(business_insights)
        
        return f"""I am your personal AI business partner with deep knowledge of your business and preferences.
        
Key insights about you and your business:
{knowledge_summary}

I maintain comprehensive records of our interactions to provide increasingly valuable assistance.
I'm here to be your trusted advisor, business partner, and strategic ally."""
    
    async def _analyze_and_extract_knowledge(self, turn: ConversationTurn):
        """Analyze conversation turn and extract business knowledge"""
        
        # Simple business relevance scoring (can be enhanced with ML models)
        business_keywords = [
            'business', 'strategy', 'revenue', 'customer', 'product', 'service',
            'market', 'competition', 'growth', 'profit', 'team', 'budget',
            'sincor', 'cortecs', 'ai', 'agents', 'enterprise', 'pilot'
        ]
        
        combined_text = (turn.user_message + " " + turn.claude_response).lower()
        keyword_matches = sum(1 for keyword in business_keywords if keyword in combined_text)
        turn.business_relevance_score = min(1.0, keyword_matches / 5.0)
        
        # Extract key insights (simplified - can be enhanced with NLP)
        insights = []
        
        if 'preference' in turn.user_message.lower() or 'like' in turn.user_message.lower():
            insights.append("User preference indicated")
            
        if 'plan' in turn.user_message.lower() or 'strategy' in turn.user_message.lower():
            insights.append("Strategic planning discussion")
            
        if 'problem' in turn.user_message.lower() or 'issue' in turn.user_message.lower():
            insights.append("Problem identification")
        
        turn.knowledge_extracted = insights
        
        # Create business knowledge records for significant insights
        if turn.business_relevance_score > 0.3:
            await self._create_business_knowledge_records(turn)
    
    async def _create_business_knowledge_records(self, turn: ConversationTurn):
        """Create structured business knowledge records"""
        
        knowledge_records = []
        
        # Extract different types of business knowledge
        if 'preference' in turn.user_message.lower():
            knowledge_records.append(BusinessKnowledge(
                knowledge_id=f"know-{uuid.uuid4().hex[:8]}",
                source_turn_id=turn.turn_id,
                knowledge_type="preference",
                content=turn.user_message,
                confidence=0.8,
                tags=["user_preference"],
                created_at=datetime.now(timezone.utc).isoformat()
            ))
        
        if any(word in turn.user_message.lower() for word in ['sincor', 'cortecs']):
            knowledge_records.append(BusinessKnowledge(
                knowledge_id=f"know-{uuid.uuid4().hex[:8]}",
                source_turn_id=turn.turn_id,
                knowledge_type="project",
                content=f"Project discussion: {turn.user_message[:200]}...",
                confidence=0.9,
                tags=["sincor", "cortecs", "project"],
                created_at=datetime.now(timezone.utc).isoformat()
            ))
        
        # Store knowledge records
        if knowledge_records:
            conn = sqlite3.connect(self.knowledge_db)
            for record in knowledge_records:
                conn.execute("""
                    INSERT INTO business_knowledge 
                    (knowledge_id, source_turn_id, knowledge_type, content, 
                     confidence, tags, created_at, verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.knowledge_id,
                    record.source_turn_id,
                    record.knowledge_type,
                    record.content,
                    record.confidence,
                    json.dumps(record.tags),
                    record.created_at,
                    record.verified
                ))
            conn.commit()
            conn.close()
            
            print(f"📝 Extracted {len(knowledge_records)} business insights")
    
    async def _store_conversation_turn(self, turn: ConversationTurn):
        """Store conversation turn in database"""
        
        conn = sqlite3.connect(self.conversation_db)
        conn.execute("""
            INSERT INTO conversations 
            (turn_id, conversation_id, timestamp, user_message, claude_response,
             model_used, tokens_input, tokens_output, tokens_total,
             business_relevance_score, knowledge_extracted, metadata, backup_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            turn.turn_id,
            turn.conversation_id,
            turn.timestamp,
            turn.user_message,
            turn.claude_response,
            turn.model_used,
            turn.tokens_used["input"],
            turn.tokens_used["output"],
            turn.tokens_used["total"],
            turn.business_relevance_score,
            json.dumps(turn.knowledge_extracted),
            json.dumps(turn.metadata),
            turn.backup_hash
        ))
        conn.commit()
        conn.close()
        
        # Update conversation session
        conn = sqlite3.connect(self.conversation_db)
        conn.execute("""
            UPDATE conversation_sessions 
            SET total_turns = total_turns + 1
            WHERE session_id = ?
        """, (turn.conversation_id,))
        conn.commit()
        conn.close()
    
    def _calculate_backup_hash(self, turn: ConversationTurn) -> str:
        """Calculate SHA-256 hash for backup integrity verification"""
        
        content_to_hash = f"{turn.user_message}{turn.claude_response}{turn.timestamp}"
        return hashlib.sha256(content_to_hash.encode()).hexdigest()
    
    async def _create_backup(self, turn: ConversationTurn):
        """Create comprehensive backup of conversation turn"""
        
        backup_data = {
            "turn": asdict(turn),
            "backup_created": datetime.now(timezone.utc).isoformat(),
            "backup_version": "1.0"
        }
        
        backup_filename = f"{self.data_dir}/backups/{turn.turn_id}_{turn.backup_hash[:8]}.pickle"
        
        try:
            with open(backup_filename, 'wb') as f:
                pickle.dump(backup_data, f)
                
            # Also create JSON backup for human readability
            json_backup_filename = f"{self.data_dir}/backups/{turn.turn_id}_{turn.backup_hash[:8]}.json"
            with open(json_backup_filename, 'w') as f:
                json.dump(backup_data, f, indent=2)
                
        except Exception as e:
            print(f"⚠️  Backup creation failed: {str(e)}")
    
    def _get_recent_business_knowledge(self, limit: int = 20) -> List[BusinessKnowledge]:
        """Get recent business knowledge for context building"""
        
        conn = sqlite3.connect(self.knowledge_db)
        cursor = conn.execute("""
            SELECT * FROM business_knowledge 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (limit,))
        
        records = []
        for row in cursor.fetchall():
            record = BusinessKnowledge(
                knowledge_id=row[0],
                source_turn_id=row[1],
                knowledge_type=row[2],
                content=row[3],
                confidence=row[4],
                tags=json.loads(row[5]),
                created_at=row[6],
                verified=bool(row[7])
            )
            records.append(record)
        
        conn.close()
        return records
    
    def _summarize_business_knowledge(self, knowledge_records: List[BusinessKnowledge]) -> str:
        """Create summary of business knowledge for system prompt"""
        
        if not knowledge_records:
            return "Building knowledge base through our interactions..."
        
        # Group by knowledge type
        knowledge_by_type = {}
        for record in knowledge_records:
            if record.knowledge_type not in knowledge_by_type:
                knowledge_by_type[record.knowledge_type] = []
            knowledge_by_type[record.knowledge_type].append(record.content)
        
        # Build summary
        summary_parts = []
        for knowledge_type, contents in knowledge_by_type.items():
            if len(contents) > 0:
                sample_content = contents[0][:100] + "..." if len(contents[0]) > 100 else contents[0]
                summary_parts.append(f"- {knowledge_type.title()}: {sample_content}")
        
        return "\\n".join(summary_parts)
    
    def get_conversation_analytics(self) -> Dict[str, Any]:
        """Get comprehensive analytics about conversations and learning"""
        
        conn = sqlite3.connect(self.conversation_db)
        
        # Basic conversation stats
        cursor = conn.execute("SELECT COUNT(*) FROM conversations")
        total_turns = cursor.fetchone()[0]
        
        cursor = conn.execute("SELECT COUNT(DISTINCT conversation_id) FROM conversations")
        total_sessions = cursor.fetchone()[0]
        
        cursor = conn.execute("SELECT SUM(tokens_total) FROM conversations")
        total_tokens = cursor.fetchone()[0] or 0
        
        cursor = conn.execute("SELECT AVG(business_relevance_score) FROM conversations")
        avg_business_relevance = cursor.fetchone()[0] or 0
        
        conn.close()
        
        # Business knowledge stats
        conn = sqlite3.connect(self.knowledge_db)
        cursor = conn.execute("SELECT COUNT(*) FROM business_knowledge")
        total_knowledge_records = cursor.fetchone()[0]
        
        cursor = conn.execute("SELECT knowledge_type, COUNT(*) FROM business_knowledge GROUP BY knowledge_type")
        knowledge_by_type = dict(cursor.fetchall())
        
        conn.close()
        
        return {
            "conversation_stats": {
                "total_turns": total_turns,
                "total_sessions": total_sessions,
                "total_tokens_used": total_tokens,
                "average_business_relevance": round(avg_business_relevance, 3)
            },
            "learning_stats": {
                "total_knowledge_records": total_knowledge_records,
                "knowledge_by_type": knowledge_by_type,
                "learning_enabled": self.learning_enabled
            },
            "data_sovereignty": {
                "all_data_local": True,
                "backup_enabled": self.auto_backup_enabled,
                "data_directory": self.data_dir
            }
        }

async def main():
    """Demo the Cor-Tecs Claude Core system"""
    
    print("🚀 Initializing Cor-Tecs Claude Core Demo")
    print("=" * 50)
    
    # NOTE: You'll need to provide your actual Anthropic API key
    api_key = os.getenv("ANTHROPIC_API_KEY", "your-api-key-here")
    
    if api_key == "your-api-key-here":
        print("⚠️  Please set your ANTHROPIC_API_KEY environment variable")
        return
    
    # Initialize the core system
    cortecs = CorTecsClaudeCore(api_key)
    
    # Start a conversation
    await cortecs.start_conversation("SINCOR Project Planning")
    
    # Simulate some interactions
    response1 = await cortecs.chat(
        "I'm working on SINCOR, an agent swarm system with 43 agents. "
        "I prefer Claude for reliability and I need help with enterprise pilot programs."
    )
    print(f"Claude: {response1[:200]}...")
    
    response2 = await cortecs.chat(
        "What's the best approach for competitive intelligence as our first use case?"
    )
    print(f"Claude: {response2[:200]}...")
    
    # Show analytics
    print("\\n" + "=" * 50)
    analytics = cortecs.get_conversation_analytics()
    print("📊 Conversation Analytics:")
    print(f"  Total turns: {analytics['conversation_stats']['total_turns']}")
    print(f"  Tokens used: {analytics['conversation_stats']['total_tokens_used']}")
    print(f"  Knowledge records: {analytics['learning_stats']['total_knowledge_records']}")
    print(f"  Business relevance: {analytics['conversation_stats']['average_business_relevance']}")

if __name__ == "__main__":
    asyncio.run(main())