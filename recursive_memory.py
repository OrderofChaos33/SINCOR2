#!/usr/bin/env python3
"""
SINCOR Recursive Memory System

Extends the base memory system with recursive learning patterns:
- Memory learns from memory (meta-patterns)
- Self-improving consolidation algorithms
- Knowledge bootstraps new knowledge generation
- Recursive validation loops for truth convergence
"""

import json
import sqlite3
import hashlib
import numpy as np
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
import os

from memory_system import MemorySystem, EpisodicEvent, SemanticFact

@dataclass
class RecursivePattern:
    """Self-discovered memory pattern"""
    pattern_id: str
    pattern_type: str  # consolidation, validation, generation
    source_memories: List[str]  # Memory hashes that created this pattern
    algorithm: Dict[str, Any]   # The learned algorithm
    confidence: float
    applications: int  # How many times successfully applied
    created: str
    last_evolved: str

@dataclass
class MetaKnowledge:
    """Knowledge about knowledge - recursive insights"""
    meta_id: str
    subject_knowledge_ids: List[str]  # Knowledge this is about
    insight: str
    pattern_reference: Optional[str]  # Pattern that generated this
    validation_score: float
    agent_id: str
    created: str

class RecursiveMemorySystem(MemorySystem):
    """Memory system that learns and improves itself recursively"""
    
    def __init__(self, agent_id: str, memory_dir: str = "memory"):
        super().__init__(agent_id, memory_dir)
        
        # Initialize recursive components
        self._init_pattern_store()
        self._init_meta_knowledge_store()
        
        # Recursive learning parameters
        self.consolidation_threshold = 100  # Events before consolidation
        self.pattern_discovery_window = 1000  # Events to analyze for patterns
        self.meta_learning_cycles = 0
        
        # Self-improvement metrics
        self.learning_efficiency = []
        self.knowledge_quality_scores = []
        self.recursive_depth_achieved = 0
        
    def _init_pattern_store(self):
        """Initialize recursive pattern database"""
        self.patterns_db = f"{self.memory_dir}/recursive_patterns_{self.agent_id}.db"
        
        conn = sqlite3.connect(self.patterns_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS patterns (
                pattern_id TEXT PRIMARY KEY,
                pattern_type TEXT,
                source_memories TEXT,
                algorithm TEXT,
                confidence REAL,
                applications INTEGER,
                created TEXT,
                last_evolved TEXT
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_knowledge (
                meta_id TEXT PRIMARY KEY,
                subject_knowledge_ids TEXT,
                insight TEXT,
                pattern_reference TEXT,
                validation_score REAL,
                agent_id TEXT,
                created TEXT
            )
        """)
        conn.commit()
        conn.close()
        
    def _init_meta_knowledge_store(self):
        """Initialize meta-knowledge tracking"""
        self.meta_knowledge_log = f"{self.memory_dir}/meta_knowledge_{self.agent_id}.jsonl"
        
        if not os.path.exists(self.meta_knowledge_log):
            with open(self.meta_knowledge_log, 'w') as f:
                pass
                
    def store_episodic_event(self, event: EpisodicEvent) -> str:
        """Store episodic event and trigger recursive learning"""
        event_hash = super().store_episodic_event(event)
        
        # Check if consolidation threshold reached
        if self._get_recent_events_count() >= self.consolidation_threshold:
            self._recursive_consolidation()
            
        return event_hash
    
    def _get_recent_events_count(self) -> int:
        """Count recent episodic events"""
        recent_cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        count = 0
        
        with open(self.episodic_log, 'r') as f:
            for line in f:
                if line.strip():
                    event_data = json.loads(line)
                    if event_data['timestamp'] >= recent_cutoff:
                        count += 1
        return count
    
    def _recursive_consolidation(self):
        """Recursively consolidate memories using discovered patterns"""
        print(f"[{self.agent_id}] Starting recursive consolidation cycle {self.meta_learning_cycles}")
        
        # Phase 1: Discover new patterns in recent memories
        new_patterns = self._discover_memory_patterns()
        
        # Phase 2: Apply existing patterns to consolidate knowledge
        consolidated_facts = self._apply_consolidation_patterns()
        
        # Phase 3: Generate meta-knowledge about the consolidation process
        meta_insights = self._generate_meta_knowledge(new_patterns, consolidated_facts)
        
        # Phase 4: Evolve existing patterns based on performance
        self._evolve_patterns()
        
        self.meta_learning_cycles += 1
        self._update_learning_metrics()
        
    def _discover_memory_patterns(self) -> List[RecursivePattern]:
        """Discover patterns in how memories relate and consolidate"""
        recent_events = self._get_recent_episodic_events(self.pattern_discovery_window)
        discovered_patterns = []
        
        # Pattern 1: Co-occurrence patterns
        co_occurrence_patterns = self._find_co_occurrence_patterns(recent_events)
        discovered_patterns.extend(co_occurrence_patterns)
        
        # Pattern 2: Causal sequence patterns  
        causal_patterns = self._find_causal_patterns(recent_events)
        discovered_patterns.extend(causal_patterns)
        
        # Pattern 3: Consolidation effectiveness patterns
        consolidation_patterns = self._find_consolidation_patterns()
        discovered_patterns.extend(consolidation_patterns)
        
        # Store new patterns
        for pattern in discovered_patterns:
            self._store_pattern(pattern)
            
        return discovered_patterns
    
    def _find_co_occurrence_patterns(self, events: List[EpisodicEvent]) -> List[RecursivePattern]:
        """Find events that frequently occur together"""
        patterns = []
        
        # Group events by time windows
        time_windows = self._group_events_by_time_windows(events, window_minutes=60)
        
        # Find frequent co-occurrences
        co_occurrences = defaultdict(int)
        for window_events in time_windows:
            event_types = [e.event_type for e in window_events]
            for i, type1 in enumerate(event_types):
                for type2 in event_types[i+1:]:
                    key = tuple(sorted([type1, type2]))
                    co_occurrences[key] += 1
        
        # Create patterns for strong co-occurrences
        total_windows = len(time_windows)
        for (type1, type2), count in co_occurrences.items():
            if count / total_windows > 0.3:  # 30% co-occurrence threshold
                pattern = RecursivePattern(
                    pattern_id=f"cooccur_{hashlib.sha256(f'{type1}_{type2}'.encode()).hexdigest()[:16]}",
                    pattern_type="co_occurrence",
                    source_memories=[e.hash for window in time_windows for e in window if e.event_type in [type1, type2]],
                    algorithm={
                        "event_types": [type1, type2],
                        "co_occurrence_rate": count / total_windows,
                        "consolidation_rule": f"When {type1} and {type2} occur together, create compound semantic fact"
                    },
                    confidence=min(0.95, (count / total_windows) * 1.5),
                    applications=0,
                    created=datetime.now().isoformat(),
                    last_evolved=datetime.now().isoformat()
                )
                patterns.append(pattern)
                
        return patterns
    
    def _find_causal_patterns(self, events: List[EpisodicEvent]) -> List[RecursivePattern]:
        """Find causal relationships between event types"""
        patterns = []
        
        # Sort events by time
        sorted_events = sorted(events, key=lambda e: e.timestamp)
        
        # Look for A->B sequences within time windows
        causal_sequences = defaultdict(int)
        
        for i, event_a in enumerate(sorted_events[:-1]):
            # Look at next few events within reasonable time window
            for event_b in sorted_events[i+1:i+10]:  # Next 10 events max
                time_diff = (datetime.fromisoformat(event_b.timestamp) - 
                           datetime.fromisoformat(event_a.timestamp)).total_seconds()
                
                if time_diff > 3600:  # More than 1 hour apart
                    break
                    
                if event_a.event_type != event_b.event_type:
                    key = (event_a.event_type, event_b.event_type)
                    causal_sequences[key] += 1
        
        # Create causal patterns
        for (cause, effect), count in causal_sequences.items():
            if count >= 5:  # At least 5 observations
                pattern = RecursivePattern(
                    pattern_id=f"causal_{hashlib.sha256(f'{cause}_{effect}'.encode()).hexdigest()[:16]}",
                    pattern_type="causal_sequence", 
                    source_memories=[],  # Would need to track specific sequences
                    algorithm={
                        "cause_event": cause,
                        "effect_event": effect,
                        "observed_count": count,
                        "consolidation_rule": f"When {cause} occurs, prepare for likely {effect}"
                    },
                    confidence=min(0.9, count / 10),
                    applications=0,
                    created=datetime.now().isoformat(),
                    last_evolved=datetime.now().isoformat()
                )
                patterns.append(pattern)
                
        return patterns
    
    def _find_consolidation_patterns(self) -> List[RecursivePattern]:
        """Find patterns in how consolidation itself works"""
        patterns = []
        
        # Analyze previous consolidation cycles
        consolidation_history = self._get_consolidation_history()
        
        if len(consolidation_history) < 3:
            return patterns  # Need more history
            
        # Pattern: What types of facts get validated most often?
        validation_patterns = self._analyze_validation_patterns(consolidation_history)
        patterns.extend(validation_patterns)
        
        # Pattern: What consolidation strategies work best?
        strategy_patterns = self._analyze_strategy_effectiveness()
        patterns.extend(strategy_patterns)
        
        return patterns
    
    def _apply_consolidation_patterns(self) -> List[SemanticFact]:
        """Apply discovered patterns to consolidate episodic into semantic"""
        consolidated_facts = []
        
        # Get existing patterns
        patterns = self._load_patterns()
        
        # Apply each pattern
        for pattern in patterns:
            if pattern.pattern_type == "co_occurrence":
                facts = self._apply_co_occurrence_pattern(pattern)
                consolidated_facts.extend(facts)
                
            elif pattern.pattern_type == "causal_sequence":
                facts = self._apply_causal_pattern(pattern)
                consolidated_facts.extend(facts)
                
            elif pattern.pattern_type == "consolidation_meta":
                # Meta-patterns that improve consolidation itself
                self._apply_meta_consolidation_pattern(pattern)
                
            # Update pattern application count
            pattern.applications += 1
            self._update_pattern(pattern)
        
        return consolidated_facts
    
    def _apply_co_occurrence_pattern(self, pattern: RecursivePattern) -> List[SemanticFact]:
        """Apply co-occurrence pattern to create compound facts"""
        facts = []
        algorithm = pattern.algorithm
        
        # Find recent co-occurrences matching this pattern
        recent_events = self._get_recent_episodic_events(100)
        
        windows = self._group_events_by_time_windows(recent_events, 60)
        for window_events in windows:
            event_types = [e.event_type for e in window_events]
            
            if all(et in event_types for et in algorithm["event_types"]):
                # Create compound semantic fact
                matching_events = [e for e in window_events if e.event_type in algorithm["event_types"]]
                
                fact = SemanticFact(
                    subject=f"compound_{algorithm['event_types'][0]}_{algorithm['event_types'][1]}",
                    predicate="co_occurred_with_pattern",
                    object=json.dumps({
                        "events": [{"type": e.event_type, "content": e.content} for e in matching_events],
                        "pattern_confidence": algorithm["co_occurrence_rate"]
                    }),
                    confidence=pattern.confidence,
                    source=f"recursive_pattern_{pattern.pattern_id}",
                    timestamp=datetime.now().isoformat(),
                    agent_id=self.agent_id,
                    verified=False
                )
                facts.append(fact)
                self.store_semantic_fact(fact)
        
        return facts
    
    def _generate_meta_knowledge(self, new_patterns: List[RecursivePattern], 
                                consolidated_facts: List[SemanticFact]) -> List[MetaKnowledge]:
        """Generate knowledge about the knowledge consolidation process itself"""
        meta_insights = []
        
        # Meta-insight: Pattern discovery effectiveness
        if new_patterns:
            insight = MetaKnowledge(
                meta_id=f"meta_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                subject_knowledge_ids=[p.pattern_id for p in new_patterns],
                insight=f"Discovered {len(new_patterns)} new patterns this cycle. " +
                       f"Pattern types: {[p.pattern_type for p in new_patterns]}. " +
                       f"This indicates {'high' if len(new_patterns) > 3 else 'moderate'} learning activity.",
                pattern_reference=None,
                validation_score=0.8,  # High confidence in meta-observations
                agent_id=self.agent_id,
                created=datetime.now().isoformat()
            )
            meta_insights.append(insight)
        
        # Meta-insight: Consolidation quality
        if consolidated_facts:
            quality_scores = [f.confidence for f in consolidated_facts]
            avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0
            
            insight = MetaKnowledge(
                meta_id=f"meta_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                subject_knowledge_ids=[f.subject for f in consolidated_facts],
                insight=f"Consolidated {len(consolidated_facts)} facts with average confidence {avg_quality:.3f}. " +
                       f"Quality trend: {'improving' if avg_quality > 0.7 else 'needs attention'}.",
                pattern_reference=None,
                validation_score=0.9,
                agent_id=self.agent_id,
                created=datetime.now().isoformat()
            )
            meta_insights.append(insight)
        
        # Store meta-insights
        for insight in meta_insights:
            self._store_meta_knowledge(insight)
            
        return meta_insights
    
    def _evolve_patterns(self):
        """Evolve existing patterns based on their performance"""
        patterns = self._load_patterns()
        
        for pattern in patterns:
            if pattern.applications > 10:  # Has enough usage data
                # Calculate evolution based on application success
                evolution_factor = min(pattern.applications / 50, 1.0)  # Cap at 50 applications
                
                # Increase confidence if pattern is frequently used
                old_confidence = pattern.confidence
                pattern.confidence = min(0.98, old_confidence + (evolution_factor * 0.1))
                
                # Update last evolved timestamp
                pattern.last_evolved = datetime.now().isoformat()
                
                # Save evolved pattern
                self._update_pattern(pattern)
                
                if pattern.confidence != old_confidence:
                    print(f"[EVOLUTION] Pattern {pattern.pattern_id} confidence: {old_confidence:.3f} -> {pattern.confidence:.3f}")
    
    def _store_pattern(self, pattern: RecursivePattern):
        """Store a discovered pattern"""
        conn = sqlite3.connect(self.patterns_db)
        conn.execute("""
            INSERT OR REPLACE INTO patterns 
            (pattern_id, pattern_type, source_memories, algorithm, confidence, applications, created, last_evolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pattern.pattern_id,
            pattern.pattern_type,
            json.dumps(pattern.source_memories),
            json.dumps(pattern.algorithm),
            pattern.confidence,
            pattern.applications,
            pattern.created,
            pattern.last_evolved
        ))
        conn.commit()
        conn.close()
        
    def _store_meta_knowledge(self, meta: MetaKnowledge):
        """Store meta-knowledge insight"""
        with open(self.meta_knowledge_log, 'a') as f:
            f.write(json.dumps(asdict(meta)) + '\n')
            
    def _load_patterns(self) -> List[RecursivePattern]:
        """Load all discovered patterns"""
        patterns = []
        
        if not os.path.exists(self.patterns_db):
            return patterns
            
        conn = sqlite3.connect(self.patterns_db)
        cursor = conn.execute("SELECT * FROM patterns")
        
        for row in cursor.fetchall():
            pattern = RecursivePattern(
                pattern_id=row[0],
                pattern_type=row[1],
                source_memories=json.loads(row[2]),
                algorithm=json.loads(row[3]),
                confidence=row[4],
                applications=row[5],
                created=row[6],
                last_evolved=row[7]
            )
            patterns.append(pattern)
            
        conn.close()
        return patterns
    
    def _update_pattern(self, pattern: RecursivePattern):
        """Update an existing pattern"""
        conn = sqlite3.connect(self.patterns_db)
        conn.execute("""
            UPDATE patterns SET 
            confidence = ?, applications = ?, last_evolved = ?
            WHERE pattern_id = ?
        """, (pattern.confidence, pattern.applications, pattern.last_evolved, pattern.pattern_id))
        conn.commit()
        conn.close()
    
    def _get_recent_episodic_events(self, count: int) -> List[EpisodicEvent]:
        """Get recent episodic events for analysis"""
        events = []
        
        if not os.path.exists(self.episodic_log):
            return events
            
        with open(self.episodic_log, 'r') as f:
            lines = f.readlines()
            
        # Get last N lines
        for line in lines[-count:]:
            if line.strip():
                data = json.loads(line)
                event = EpisodicEvent(**data)
                events.append(event)
                
        return events
    
    def _group_events_by_time_windows(self, events: List[EpisodicEvent], 
                                     window_minutes: int) -> List[List[EpisodicEvent]]:
        """Group events into time windows"""
        if not events:
            return []
            
        sorted_events = sorted(events, key=lambda e: e.timestamp)
        windows = []
        current_window = [sorted_events[0]]
        window_start = datetime.fromisoformat(sorted_events[0].timestamp)
        
        for event in sorted_events[1:]:
            event_time = datetime.fromisoformat(event.timestamp)
            
            if (event_time - window_start).total_seconds() <= window_minutes * 60:
                current_window.append(event)
            else:
                windows.append(current_window)
                current_window = [event]
                window_start = event_time
                
        if current_window:
            windows.append(current_window)
            
        return windows
    
    def _update_learning_metrics(self):
        """Update learning efficiency metrics"""
        patterns_count = len(self._load_patterns())
        
        # Learning efficiency: patterns discovered per cycle
        efficiency = patterns_count / max(self.meta_learning_cycles, 1)
        self.learning_efficiency.append(efficiency)
        
        # Calculate recursive depth
        meta_patterns = [p for p in self._load_patterns() if p.pattern_type.startswith("meta_")]
        self.recursive_depth_achieved = len(meta_patterns)
        
        print(f"[METRICS] Learning efficiency: {efficiency:.2f}, Recursive depth: {self.recursive_depth_achieved}")
    
    def get_recursive_stats(self) -> Dict[str, Any]:
        """Get recursive learning statistics"""
        patterns = self._load_patterns()
        
        return {
            "meta_learning_cycles": self.meta_learning_cycles,
            "total_patterns": len(patterns),
            "pattern_types": {ptype: len([p for p in patterns if p.pattern_type == ptype]) 
                            for ptype in set(p.pattern_type for p in patterns)},
            "recursive_depth": self.recursive_depth_achieved,
            "average_learning_efficiency": sum(self.learning_efficiency) / len(self.learning_efficiency) if self.learning_efficiency else 0,
            "most_applied_patterns": sorted([(p.pattern_id, p.applications) for p in patterns], 
                                          key=lambda x: x[1], reverse=True)[:5]
        }

def main():
    """Demo recursive memory system"""
    print("SINCOR Recursive Memory System Demo")
    print("=" * 40)
    
    # Create recursive memory system
    memory = RecursiveMemorySystem("E-auriga-01")
    
    # Simulate some learning events
    events = [
        EpisodicEvent("2025-01-01T10:00:00", "E-auriga-01", "market_research", 
                     {"company": "TechCorp", "revenue": "50M"}, {}, 0.9),
        EpisodicEvent("2025-01-01T10:15:00", "E-auriga-01", "competitor_analysis",
                     {"competitor": "RivalCorp", "strength": "pricing"}, {}, 0.8),
        EpisodicEvent("2025-01-01T10:30:00", "E-auriga-01", "market_research",
                     {"company": "StartupX", "revenue": "5M"}, {}, 0.7),
        EpisodicEvent("2025-01-01T10:45:00", "E-auriga-01", "competitor_analysis",
                     {"competitor": "BigTech", "strength": "distribution"}, {}, 0.85),
    ]
    
    # Store events and trigger recursive learning
    for event in events:
        memory.store_episodic_event(event)
    
    # Add more events to trigger consolidation
    for i in range(100):
        event = EpisodicEvent(
            f"2025-01-01T{11 + i//4}:{(i%4)*15:02d}:00",
            "E-auriga-01", 
            "market_research" if i % 2 == 0 else "competitor_analysis",
            {"data_point": f"value_{i}"}, {}, 0.8
        )
        memory.store_episodic_event(event)
    
    # Show recursive learning stats
    stats = memory.get_recursive_stats()
    print("\nRecursive Learning Statistics:")
    for key, value in stats.items():
        print(f"  {key}: {value}")

if __name__ == "__main__":
    main()