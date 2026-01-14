#!/usr/bin/env python3
"""
SINCOR Multi-Tier Memory Architecture

Implements the 4-tier memory system:
- Episodic: Time-stamped events (append-only log)
- Semantic: Facts, profiles, rules (graph/relational)  
- Procedural: Tools, routines, prompts (versioned registry)
- Autobiographical: Self-story, goals, quirks (curated narrative)

With hybrid RAG retrieval: vector + graph + KV cache
"""

import json
import sqlite3
import hashlib
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from collections import deque
import os

@dataclass
class EpisodicEvent:
    """Time-stamped event record"""
    timestamp: str
    agent_id: str
    event_type: str  # action, observation, interaction, etc.
    content: Dict[str, Any]
    context: Dict[str, Any]
    confidence: float
    citations: List[str] = None
    hash: str = None
    
    def __post_init__(self):
        if not self.hash:
            content_str = json.dumps(self.content, sort_keys=True)
            self.hash = hashlib.sha256(content_str.encode()).hexdigest()[:16]

@dataclass  
class SemanticFact:
    """Structured knowledge record"""
    subject: str
    predicate: str  
    object: str
    confidence: float
    source: str  # Citation/provenance
    timestamp: str
    agent_id: str
    verified: bool = False

@dataclass
class ProceduralRoutine:
    """Versioned tool or prompt definition"""
    name: str
    version: str
    routine_type: str  # tool, prompt, workflow
    definition: Dict[str, Any]
    metadata: Dict[str, Any]
    created: str
    agent_id: str
    
class MemorySystem:
    """Multi-tier memory architecture for SINCOR agents"""
    
    def __init__(self, agent_id: str, memory_dir: str = "memory"):
        self.agent_id = agent_id
        self.memory_dir = memory_dir
        
        # Ensure memory directories exist
        os.makedirs(f"{memory_dir}/episodic", exist_ok=True)
        os.makedirs(f"{memory_dir}/semantic", exist_ok=True) 
        os.makedirs(f"{memory_dir}/procedural", exist_ok=True)
        os.makedirs(f"{memory_dir}/autobiographical", exist_ok=True)
        
        # Initialize memory stores
        self._init_episodic_store()
        self._init_semantic_store()
        self._init_procedural_store()
        self._init_autobiographical_store()
        
        # Hot cache for frequent access
        self.hot_cache = deque(maxlen=1000)
        
    def _init_episodic_store(self):
        """Initialize append-only episodic log"""
        self.episodic_log = f"{self.memory_dir}/episodic/{self.agent_id}.jsonl"
        
        # Ensure file exists
        if not os.path.exists(self.episodic_log):
            with open(self.episodic_log, 'w') as f:
                pass  # Create empty file
                
    def _init_semantic_store(self):
        """Initialize semantic knowledge graph (SQLite for simplicity)"""
        self.semantic_db = f"{self.memory_dir}/semantic/{self.agent_id}.db"
        
        conn = sqlite3.connect(self.semantic_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY,
                subject TEXT,
                predicate TEXT, 
                object TEXT,
                confidence REAL,
                source TEXT,
                timestamp TEXT,
                agent_id TEXT,
                verified BOOLEAN
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subject ON facts(subject)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_predicate ON facts(predicate)")
        conn.commit()
        conn.close()
        
    def _init_procedural_store(self):
        """Initialize versioned procedural registry"""
        self.procedural_registry = f"{self.memory_dir}/procedural/{self.agent_id}_registry.json"
        
        if not os.path.exists(self.procedural_registry):
            initial_registry = {
                "version": "1.0",
                "agent_id": self.agent_id,
                "routines": {},
                "created": datetime.now().isoformat()
            }
            with open(self.procedural_registry, 'w') as f:
                json.dump(initial_registry, f, indent=2)
                
    def _init_autobiographical_store(self):
        """Initialize autobiographical narrative"""
        self.autobiography = f"{self.memory_dir}/autobiographical/{self.agent_id}.md"
        
        if not os.path.exists(self.autobiography):
            with open(self.autobiography, 'w') as f:
                f.write(f"# {self.agent_id} Personal Narrative\n\n")
                f.write("## Core Identity\n\n")
                f.write("## Goals & Aspirations\n\n") 
                f.write("## Key Experiences\n\n")
                f.write("## Lessons Learned\n\n")
                f.write("## Quirks & Preferences\n\n")
    
    # EPISODIC MEMORY METHODS
    
    def record_episode(self, event_type: str, content: Dict[str, Any], 
                      context: Dict[str, Any] = None, confidence: float = 1.0,
                      citations: List[str] = None) -> EpisodicEvent:
        """Record a new episodic event"""
        
        event = EpisodicEvent(
            timestamp=datetime.now().isoformat(),
            agent_id=self.agent_id,
            event_type=event_type,
            content=content,
            context=context or {},
            confidence=confidence,
            citations=citations or []
        )
        
        # Append to log
        with open(self.episodic_log, 'a') as f:
            f.write(json.dumps(asdict(event)) + '\n')
            
        # Add to hot cache
        self.hot_cache.append(event)
        
        return event
    
    def query_episodes(self, event_type: str = None, since: str = None, 
                      limit: int = 100) -> List[EpisodicEvent]:
        """Query episodic memory"""
        
        episodes = []
        
        with open(self.episodic_log, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                    
                data = json.loads(line)
                event = EpisodicEvent(**data)
                
                # Apply filters
                if event_type and event.event_type != event_type:
                    continue
                    
                if since and event.timestamp < since:
                    continue
                    
                episodes.append(event)
                
                if len(episodes) >= limit:
                    break
                    
        return episodes[-limit:]  # Return most recent
    
    def consolidate_episodic(self, days_back: int = 7) -> List[SemanticFact]:
        """Consolidate episodic events into semantic facts (dream cycle)"""
        
        cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
        recent_episodes = self.query_episodes(since=cutoff)
        
        # Simple consolidation: extract patterns and frequent entities
        consolidated_facts = []
        
        # Count entity frequencies
        entity_counts = {}
        for episode in recent_episodes:
            content_str = json.dumps(episode.content)
            # Simple entity extraction (in real implementation, use NLP)
            words = content_str.lower().split()
            for word in words:
                if len(word) > 3:  # Filter short words
                    entity_counts[word] = entity_counts.get(word, 0) + 1
        
        # Convert high-frequency entities to facts
        for entity, count in entity_counts.items():
            if count > 3:  # Threshold for significance
                fact = SemanticFact(
                    subject=self.agent_id,
                    predicate="frequently_encounters",
                    object=entity,
                    confidence=min(count / 10.0, 1.0),
                    source="episodic_consolidation",
                    timestamp=datetime.now().isoformat(),
                    agent_id=self.agent_id
                )
                consolidated_facts.append(fact)
                self.store_semantic_fact(fact)
        
        return consolidated_facts
    
    # SEMANTIC MEMORY METHODS
    
    def store_semantic_fact(self, fact: SemanticFact):
        """Store a semantic fact"""
        
        conn = sqlite3.connect(self.semantic_db)
        conn.execute("""
            INSERT INTO facts (subject, predicate, object, confidence, source, 
                             timestamp, agent_id, verified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (fact.subject, fact.predicate, fact.object, fact.confidence,
              fact.source, fact.timestamp, fact.agent_id, fact.verified))
        conn.commit()
        conn.close()
    
    def query_semantic_facts(self, subject: str = None, predicate: str = None,
                           object: str = None, limit: int = 100) -> List[SemanticFact]:
        """Query semantic knowledge"""
        
        conn = sqlite3.connect(self.semantic_db)
        
        query = "SELECT * FROM facts WHERE 1=1"
        params = []
        
        if subject:
            query += " AND subject LIKE ?"
            params.append(f"%{subject}%")
        if predicate:
            query += " AND predicate LIKE ?"  
            params.append(f"%{predicate}%")
        if object:
            query += " AND object LIKE ?"
            params.append(f"%{object}%")
            
        query += " ORDER BY confidence DESC LIMIT ?"
        params.append(limit)
        
        cursor = conn.execute(query, params)
        facts = []
        
        for row in cursor.fetchall():
            fact = SemanticFact(
                subject=row[1], predicate=row[2], object=row[3],
                confidence=row[4], source=row[5], timestamp=row[6],
                agent_id=row[7], verified=bool(row[8])
            )
            facts.append(fact)
            
        conn.close()
        return facts
    
    # PROCEDURAL MEMORY METHODS
    
    def store_routine(self, routine: ProceduralRoutine):
        """Store or update a procedural routine"""
        
        with open(self.procedural_registry, 'r') as f:
            registry = json.load(f)
            
        routine_key = f"{routine.name}:{routine.version}"
        registry["routines"][routine_key] = asdict(routine)
        
        with open(self.procedural_registry, 'w') as f:
            json.dump(registry, f, indent=2)
    
    def get_routine(self, name: str, version: str = None) -> Optional[ProceduralRoutine]:
        """Retrieve a procedural routine"""
        
        with open(self.procedural_registry, 'r') as f:
            registry = json.load(f)
            
        if version:
            routine_key = f"{name}:{version}"
            if routine_key in registry["routines"]:
                data = registry["routines"][routine_key]
                return ProceduralRoutine(**data)
        else:
            # Get latest version
            latest_version = None
            latest_routine = None
            
            for key, data in registry["routines"].items():
                if key.startswith(f"{name}:"):
                    if not latest_version or data["version"] > latest_version:
                        latest_version = data["version"]
                        latest_routine = ProceduralRoutine(**data)
                        
            return latest_routine
            
        return None
    
    # AUTOBIOGRAPHICAL MEMORY METHODS
    
    def update_autobiography(self, section: str, content: str):
        """Update a section of the autobiographical narrative"""
        
        with open(self.autobiography, 'r') as f:
            current_content = f.read()
            
        # Simple section replacement (in real implementation, use better parsing)
        section_header = f"## {section}"
        
        if section_header in current_content:
            # Replace existing section
            lines = current_content.split('\n')
            new_lines = []
            in_section = False
            
            for line in lines:
                if line.startswith(f"## {section}"):
                    in_section = True
                    new_lines.append(line)
                    new_lines.append("")
                    new_lines.append(content)
                    new_lines.append("")
                elif line.startswith("## ") and in_section:
                    in_section = False
                    new_lines.append(line)
                elif not in_section:
                    new_lines.append(line)
                    
            current_content = '\n'.join(new_lines)
        else:
            # Add new section
            current_content += f"\n## {section}\n\n{content}\n"
            
        with open(self.autobiography, 'w') as f:
            f.write(current_content)
    
    def get_autobiography_section(self, section: str) -> str:
        """Get a specific section from autobiography"""
        
        with open(self.autobiography, 'r') as f:
            content = f.read()
            
        section_header = f"## {section}"
        if section_header not in content:
            return ""
            
        lines = content.split('\n')
        section_lines = []
        in_section = False
        
        for line in lines:
            if line.startswith(f"## {section}"):
                in_section = True
            elif line.startswith("## ") and in_section:
                break
            elif in_section and line.strip():
                section_lines.append(line)
                
        return '\n'.join(section_lines).strip()
    
    # HYBRID RAG RETRIEVAL
    
    def retrieve_relevant_memories(self, query: str, limit: int = 10) -> Dict[str, List]:
        """Hybrid retrieval across all memory tiers"""
        
        results = {
            "episodic": [],
            "semantic": [],
            "procedural": [],
            "autobiographical": ""
        }
        
        # Episodic retrieval (simple text search)
        episodes = self.query_episodes(limit=50)
        for episode in episodes:
            content_str = json.dumps(episode.content).lower()
            if query.lower() in content_str:
                results["episodic"].append(episode)
                if len(results["episodic"]) >= limit//3:
                    break
        
        # Semantic retrieval (match query terms)
        query_terms = query.lower().split()
        for term in query_terms:
            facts = self.query_semantic_facts(subject=term, limit=5)
            facts.extend(self.query_semantic_facts(object=term, limit=5))
            results["semantic"].extend(facts)
        
        # Remove duplicates and limit
        seen = set()
        unique_facts = []
        for fact in results["semantic"]:
            key = f"{fact.subject}:{fact.predicate}:{fact.object}"
            if key not in seen:
                unique_facts.append(fact)
                seen.add(key)
        results["semantic"] = unique_facts[:limit//3]
        
        # Autobiographical retrieval
        autobiography_content = ""
        with open(self.autobiography, 'r') as f:
            autobiography_content = f.read()
            
        if query.lower() in autobiography_content.lower():
            results["autobiographical"] = autobiography_content
            
        return results
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Get memory system statistics"""
        
        stats = {"agent_id": self.agent_id}
        
        # Episodic stats
        episode_count = 0
        if os.path.exists(self.episodic_log):
            with open(self.episodic_log, 'r') as f:
                episode_count = sum(1 for line in f if line.strip())
        stats["episodic_events"] = episode_count
        
        # Semantic stats  
        conn = sqlite3.connect(self.semantic_db)
        cursor = conn.execute("SELECT COUNT(*) FROM facts")
        stats["semantic_facts"] = cursor.fetchone()[0]
        conn.close()
        
        # Procedural stats
        with open(self.procedural_registry, 'r') as f:
            registry = json.load(f)
        stats["procedural_routines"] = len(registry["routines"])
        
        # Autobiographical stats
        if os.path.exists(self.autobiography):
            with open(self.autobiography, 'r') as f:
                content = f.read()
            stats["autobiography_length"] = len(content)
        else:
            stats["autobiography_length"] = 0
            
        return stats

def main():
    """Demo the memory system"""
    
    print("SINCOR Multi-Tier Memory System")
    print("=" * 35)
    
    # Create memory system for Auriga
    memory = MemorySystem("E-auriga-01")
    
    # Record some episodic events
    memory.record_episode(
        event_type="action",
        content={"action": "prospect", "target": "tech_companies", "found": 15},
        context={"task_id": "T-001", "priority": "high"},
        confidence=0.95
    )
    
    memory.record_episode(
        event_type="observation", 
        content={"observation": "market_trend", "trend": "AI adoption increasing"},
        confidence=0.8
    )
    
    # Store semantic facts
    fact1 = SemanticFact(
        subject="tech_companies",
        predicate="adoption_trend", 
        object="AI_increasing",
        confidence=0.85,
        source="market_research",
        timestamp=datetime.now().isoformat(),
        agent_id="E-auriga-01"
    )
    memory.store_semantic_fact(fact1)
    
    # Store procedural routine
    routine = ProceduralRoutine(
        name="prospect_workflow",
        version="1.0",
        routine_type="workflow",
        definition={
            "steps": ["search", "validate", "enrich", "report"],
            "tools": ["web_scraping", "data_validation"]
        },
        metadata={"created_by": "E-auriga-01", "tested": True},
        created=datetime.now().isoformat(),
        agent_id="E-auriga-01"
    )
    memory.store_routine(routine)
    
    # Update autobiography
    memory.update_autobiography("Core Identity", 
        "I am Auriga, a Scout archetype focused on market intelligence and competitive analysis. "
        "My primary role is discovering opportunities and tracking market trends.")
    
    # Demonstrate retrieval
    print("Retrieving memories for 'tech companies'...")
    results = memory.retrieve_relevant_memories("tech companies")
    
    print(f"Found {len(results['episodic'])} episodic events")
    print(f"Found {len(results['semantic'])} semantic facts") 
    print(f"Autobiography match: {bool(results['autobiographical'])}")
    
    # Consolidate episodic to semantic
    print("\nConsolidating episodic memories...")
    new_facts = memory.consolidate_episodic(days_back=1)
    print(f"Generated {len(new_facts)} new semantic facts")
    
    # Memory stats
    stats = memory.get_memory_stats()
    print(f"\nMemory Statistics for {stats['agent_id']}:")
    print(f"  Episodic events: {stats['episodic_events']}")
    print(f"  Semantic facts: {stats['semantic_facts']}")
    print(f"  Procedural routines: {stats['procedural_routines']}")
    print(f"  Autobiography length: {stats['autobiography_length']} chars")

if __name__ == "__main__":
    main()