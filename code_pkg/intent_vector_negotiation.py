#!/usr/bin/env python3
"""
SINCOR Intent Vector Negotiation Layer
Vectorized intent resonance system for cognitive alignment without delegation bottlenecks

ARCHITECTURE NOTES:
- Agents broadcast intent vectors in high-dimensional space
- Resonance detection enables spontaneous collaboration
- No rigid task contracts - fluid cognitive alignment
- Prevents mode collapse through preserved diversity
"""

import numpy as np
import asyncio
import time
import json
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple, Set
from enum import Enum
import uuid
import hashlib
from datetime import datetime
import threading
import math

class IntentDomain(Enum):
    """Primary domains for intent classification"""
    KNOWLEDGE_ACQUISITION = "knowledge_acquisition"
    PROBLEM_SOLVING = "problem_solving" 
    RESOURCE_OPTIMIZATION = "resource_optimization"
    PATTERN_RECOGNITION = "pattern_recognition"
    COMMUNICATION = "communication"
    CREATION = "creation"
    ANALYSIS = "analysis"
    COORDINATION = "coordination"

class CognitiveResonanceType(Enum):
    """Types of cognitive resonance between agents"""
    HARMONIC = "harmonic"          # Perfect alignment
    COMPLEMENTARY = "complementary" # Filling gaps
    SYNERGISTIC = "synergistic"     # Enhanced combined effect
    COMPETITIVE = "competitive"     # Healthy tension
    ORTHOGONAL = "orthogonal"      # Independent but compatible

@dataclass
class IntentVector:
    """High-dimensional representation of agent intent"""
    agent_id: str
    vector_id: str
    timestamp: float
    
    # Core intent dimensions (0.0 to 1.0)
    domains: Dict[IntentDomain, float]
    
    # Cognitive characteristics
    creativity_bias: float      # 0=logical, 1=creative
    risk_tolerance: float       # 0=conservative, 1=risk-taking  
    collaboration_preference: float  # 0=independent, 1=collaborative
    time_horizon: float         # 0=immediate, 1=long-term
    precision_preference: float # 0=approximate, 1=precise
    
    # Resource requirements
    computational_intensity: float
    memory_requirements: float
    bandwidth_needs: float
    
    # Epistemic priors (unique per agent)
    prior_experiences: Dict[str, float]
    confidence_levels: Dict[str, float]
    uncertainty_tolerance: float
    
    # Dynamic properties
    urgency: float
    priority: float
    flexibility: float  # How willing to adapt intent
    
    # Metadata
    context_hash: str
    parent_intents: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        """Ensure vector integrity"""
        # Normalize domain values
        total_domain_weight = sum(self.domains.values())
        if total_domain_weight > 0:
            self.domains = {k: v/total_domain_weight for k, v in self.domains.items()}

@dataclass
class ResonanceMatch:
    """Represents cognitive resonance between two agents"""
    agent_a_id: str
    agent_b_id: str
    intent_a_id: str
    intent_b_id: str
    resonance_type: CognitiveResonanceType
    resonance_strength: float  # 0.0 to 1.0
    predicted_synergy: float
    compatibility_score: float
    
    # Detailed resonance analysis
    domain_alignments: Dict[IntentDomain, float]
    cognitive_complementarity: float
    resource_compatibility: float
    epistemic_diversity: float
    
    # Dynamic properties
    stability_prediction: float
    evolution_potential: float
    
    timestamp: float
    context_factors: Dict[str, Any] = field(default_factory=dict)

@dataclass
class CognitiveEpistemicPrior:
    """Individual agent's epistemic foundation"""
    agent_id: str
    prior_id: str
    
    # Knowledge representations
    concept_weights: Dict[str, float]
    relationship_strengths: Dict[Tuple[str, str], float]
    confidence_mappings: Dict[str, float]
    
    # Learning biases
    pattern_recognition_bias: float
    generalization_tendency: float
    specialization_preference: float
    
    # Meta-cognitive properties
    self_awareness_level: float
    cognitive_flexibility: float
    epistemic_humility: float
    
    # Historical context
    formation_history: List[Dict[str, Any]]
    adaptation_rate: float
    
    timestamp: float

class IntentVectorSpace:
    """High-dimensional space for intent vector operations"""
    
    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions
        self.active_vectors: Dict[str, IntentVector] = {}
        self.vector_embeddings: Dict[str, np.ndarray] = {}
        self.resonance_cache: Dict[Tuple[str, str], ResonanceMatch] = {}
        
    def embed_intent(self, intent: IntentVector) -> np.ndarray:
        """Convert intent to high-dimensional vector embedding"""
        
        # Create base embedding from domains
        domain_embedding = np.zeros(64)  # 8 domains * 8 dimensions each
        for i, domain in enumerate(IntentDomain):
            domain_weight = intent.domains.get(domain, 0.0)
            domain_embedding[i*8:(i+1)*8] = self._generate_domain_embedding(domain, domain_weight)
        
        # Cognitive characteristics embedding
        cognitive_embedding = np.array([
            intent.creativity_bias,
            intent.risk_tolerance,
            intent.collaboration_preference,
            intent.time_horizon,
            intent.precision_preference,
            intent.urgency,
            intent.priority,
            intent.flexibility
        ])
        
        # Resource requirements embedding
        resource_embedding = np.array([
            intent.computational_intensity,
            intent.memory_requirements,
            intent.bandwidth_needs,
            intent.uncertainty_tolerance
        ])
        
        # Epistemic priors embedding (compressed)
        epistemic_embedding = self._compress_epistemic_data(intent.prior_experiences, intent.confidence_levels)
        
        # Context embedding
        context_embedding = self._hash_to_vector(intent.context_hash, 32)
        
        # Combine all embeddings
        full_embedding = np.concatenate([
            domain_embedding,           # 64 dims
            cognitive_embedding,        # 8 dims
            resource_embedding,         # 4 dims  
            epistemic_embedding,        # 128 dims
            context_embedding          # 32 dims
        ])                            # Total: 236 dims
        
        # Pad to target dimensions
        if len(full_embedding) < self.dimensions:
            padding = np.random.normal(0, 0.01, self.dimensions - len(full_embedding))
            full_embedding = np.concatenate([full_embedding, padding])
        
        # Normalize
        full_embedding = full_embedding / (np.linalg.norm(full_embedding) + 1e-8)
        
        return full_embedding
    
    def _generate_domain_embedding(self, domain: IntentDomain, weight: float) -> np.ndarray:
        """Generate domain-specific embedding pattern"""
        
        # Each domain has a unique signature pattern
        domain_signatures = {
            IntentDomain.KNOWLEDGE_ACQUISITION: [1, 0, 1, 0, 1, 0, 0, 1],
            IntentDomain.PROBLEM_SOLVING: [0, 1, 1, 1, 0, 1, 0, 0],
            IntentDomain.RESOURCE_OPTIMIZATION: [1, 1, 0, 0, 1, 1, 0, 0],
            IntentDomain.PATTERN_RECOGNITION: [0, 0, 1, 1, 1, 0, 1, 0],
            IntentDomain.COMMUNICATION: [1, 0, 0, 1, 0, 1, 1, 0],
            IntentDomain.CREATION: [0, 1, 0, 0, 1, 0, 1, 1],
            IntentDomain.ANALYSIS: [1, 1, 1, 0, 0, 0, 1, 0],
            IntentDomain.COORDINATION: [0, 1, 0, 1, 0, 1, 0, 1]
        }
        
        signature = np.array(domain_signatures.get(domain, [0.5] * 8))
        return signature * weight
    
    def _compress_epistemic_data(self, experiences: Dict[str, float], 
                                confidences: Dict[str, float]) -> np.ndarray:
        """Compress epistemic data into fixed-size embedding"""
        
        # Hash-based compression of variable-size epistemic data
        embedding = np.zeros(128)
        
        # Process experiences
        for concept, value in experiences.items():
            hash_idx = hash(concept) % 64
            embedding[hash_idx] += value
            
        # Process confidences  
        for concept, confidence in confidences.items():
            hash_idx = hash(concept) % 64 + 64  # Second half
            embedding[hash_idx] += confidence
            
        # Normalize
        embedding = embedding / (np.linalg.norm(embedding) + 1e-8)
        return embedding
    
    def _hash_to_vector(self, hash_str: str, size: int) -> np.ndarray:
        """Convert hash string to vector representation"""
        
        # Convert hash to deterministic vector
        hash_bytes = hashlib.sha256(hash_str.encode()).digest()
        vector = np.frombuffer(hash_bytes[:size*4], dtype=np.float32)
        
        # Pad if needed
        if len(vector) < size:
            vector = np.pad(vector, (0, size - len(vector)))
        
        return vector[:size]
    
    def calculate_resonance(self, intent_a: IntentVector, intent_b: IntentVector) -> ResonanceMatch:
        """Calculate cognitive resonance between two intent vectors"""
        
        cache_key = (intent_a.vector_id, intent_b.vector_id)
        if cache_key in self.resonance_cache:
            return self.resonance_cache[cache_key]
        
        # Get embeddings
        embedding_a = self.embed_intent(intent_a)
        embedding_b = self.embed_intent(intent_b)
        
        # Calculate various resonance metrics
        
        # 1. Domain alignment
        domain_alignments = {}
        domain_resonance = 0.0
        for domain in IntentDomain:
            weight_a = intent_a.domains.get(domain, 0.0)
            weight_b = intent_b.domains.get(domain, 0.0)
            
            alignment = 1.0 - abs(weight_a - weight_b)  # Similarity
            complementarity = min(weight_a, weight_b) * 2  # How well they complement
            
            domain_alignments[domain] = max(alignment, complementarity)
            domain_resonance += domain_alignments[domain] * (weight_a + weight_b)
        
        # 2. Cognitive compatibility
        cognitive_distance = abs(intent_a.creativity_bias - intent_b.creativity_bias) + \
                           abs(intent_a.risk_tolerance - intent_b.risk_tolerance) + \
                           abs(intent_a.time_horizon - intent_b.time_horizon)
        
        cognitive_compatibility = max(0, 1.0 - cognitive_distance / 3.0)
        
        # 3. Resource compatibility
        resource_conflict = abs(intent_a.computational_intensity - intent_b.computational_intensity) + \
                           abs(intent_a.memory_requirements - intent_b.memory_requirements)
        
        resource_compatibility = max(0, 1.0 - resource_conflict / 2.0)
        
        # 4. Epistemic diversity (beneficial tension)
        epistemic_diversity = self._calculate_epistemic_diversity(intent_a, intent_b)
        
        # 5. Vector cosine similarity
        vector_similarity = np.dot(embedding_a, embedding_b)
        
        # Determine resonance type and strength
        resonance_type, resonance_strength = self._classify_resonance(
            domain_resonance, cognitive_compatibility, epistemic_diversity, vector_similarity
        )
        
        # Calculate synergy prediction
        synergy_factors = [
            domain_resonance * 0.3,
            cognitive_compatibility * 0.2, 
            resource_compatibility * 0.2,
            epistemic_diversity * 0.2,  # Diversity is good for synergy
            vector_similarity * 0.1
        ]
        predicted_synergy = sum(synergy_factors)
        
        # Overall compatibility
        compatibility_score = (domain_resonance + cognitive_compatibility + resource_compatibility) / 3.0
        
        # Stability and evolution predictions
        stability = min(cognitive_compatibility, resource_compatibility)
        evolution_potential = max(epistemic_diversity, abs(intent_a.flexibility + intent_b.flexibility)/2.0)
        
        # Create resonance match
        match = ResonanceMatch(
            agent_a_id=intent_a.agent_id,
            agent_b_id=intent_b.agent_id,
            intent_a_id=intent_a.vector_id,
            intent_b_id=intent_b.vector_id,
            resonance_type=resonance_type,
            resonance_strength=resonance_strength,
            predicted_synergy=predicted_synergy,
            compatibility_score=compatibility_score,
            domain_alignments=domain_alignments,
            cognitive_complementarity=epistemic_diversity,
            resource_compatibility=resource_compatibility,
            epistemic_diversity=epistemic_diversity,
            stability_prediction=stability,
            evolution_potential=evolution_potential,
            timestamp=time.time(),
            context_factors={
                'vector_similarity': vector_similarity,
                'cognitive_distance': cognitive_distance,
                'resource_conflict': resource_conflict
            }
        )
        
        # Cache the result
        self.resonance_cache[cache_key] = match
        
        return match
    
    def _calculate_epistemic_diversity(self, intent_a: IntentVector, intent_b: IntentVector) -> float:
        """Calculate beneficial epistemic diversity between agents"""
        
        # Compare prior experiences
        all_concepts = set(intent_a.prior_experiences.keys()) | set(intent_b.prior_experiences.keys())
        
        experience_diversity = 0.0
        for concept in all_concepts:
            exp_a = intent_a.prior_experiences.get(concept, 0.0)
            exp_b = intent_b.prior_experiences.get(concept, 0.0)
            
            # Diversity is high when one has experience the other lacks
            diversity_contrib = abs(exp_a - exp_b) * max(exp_a, exp_b)
            experience_diversity += diversity_contrib
        
        # Normalize by number of concepts
        if all_concepts:
            experience_diversity /= len(all_concepts)
        
        # Compare confidence patterns
        confidence_diversity = abs(intent_a.uncertainty_tolerance - intent_b.uncertainty_tolerance)
        
        return min(1.0, (experience_diversity + confidence_diversity) / 2.0)
    
    def _classify_resonance(self, domain_resonance: float, cognitive_compatibility: float,
                           epistemic_diversity: float, vector_similarity: float) -> Tuple[CognitiveResonanceType, float]:
        """Classify the type and strength of resonance"""
        
        # High similarity across all dimensions = Harmonic
        if vector_similarity > 0.8 and cognitive_compatibility > 0.8:
            return CognitiveResonanceType.HARMONIC, vector_similarity
        
        # High domain alignment but diverse epistemics = Complementary
        elif domain_resonance > 0.7 and epistemic_diversity > 0.6:
            return CognitiveResonanceType.COMPLEMENTARY, (domain_resonance + epistemic_diversity) / 2.0
        
        # Medium alignment with high evolution potential = Synergistic  
        elif vector_similarity > 0.6 and epistemic_diversity > 0.5:
            return CognitiveResonanceType.SYNERGISTIC, (vector_similarity + epistemic_diversity) / 2.0
        
        # Similar domains but different approaches = Competitive
        elif domain_resonance > 0.8 and cognitive_compatibility < 0.5:
            return CognitiveResonanceType.COMPETITIVE, domain_resonance * (1 - cognitive_compatibility)
        
        # Low interference, independent operation = Orthogonal
        else:
            return CognitiveResonanceType.ORTHOGONAL, min(0.3, vector_similarity)

class IntentNegotiationEngine:
    """Core engine for intent vector negotiation and resonance matching"""
    
    def __init__(self, db_path: str = "intent_negotiation.db"):
        self.db_path = db_path
        self.vector_space = IntentVectorSpace()
        self.active_intents: Dict[str, IntentVector] = {}
        self.agent_epistemics: Dict[str, CognitiveEpistemicPrior] = {}
        self.active_negotiations: Dict[str, Dict[str, Any]] = {}
        self.resonance_networks: Dict[str, Set[str]] = {}  # agent_id -> connected agents
        
        self._setup_database()
        
    def _setup_database(self):
        """Initialize intent negotiation database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Intent vectors table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS intent_vectors (
            vector_id TEXT PRIMARY KEY,
            agent_id TEXT,
            timestamp REAL,
            domains TEXT,
            cognitive_profile TEXT,
            resource_requirements TEXT,
            epistemic_priors TEXT,
            context_hash TEXT,
            parent_intents TEXT
        )
        ''')
        
        # Resonance matches table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS resonance_matches (
            match_id TEXT PRIMARY KEY,
            agent_a_id TEXT,
            agent_b_id TEXT,
            intent_a_id TEXT,
            intent_b_id TEXT,
            resonance_type TEXT,
            resonance_strength REAL,
            predicted_synergy REAL,
            compatibility_score REAL,
            epistemic_diversity REAL,
            stability_prediction REAL,
            timestamp REAL,
            match_data TEXT
        )
        ''')
        
        # Agent epistemic priors table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS epistemic_priors (
            prior_id TEXT PRIMARY KEY,
            agent_id TEXT,
            concept_weights TEXT,
            relationship_strengths TEXT,
            confidence_mappings TEXT,
            cognitive_biases TEXT,
            formation_history TEXT,
            timestamp REAL
        )
        ''')
        
        # Negotiation outcomes table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS negotiation_outcomes (
            negotiation_id TEXT PRIMARY KEY,
            participating_agents TEXT,
            original_intents TEXT,
            negotiated_outcome TEXT,
            consensus_strength REAL,
            diversity_preserved REAL,
            timestamp REAL,
            success BOOLEAN
        )
        ''')
        
        conn.commit()
        conn.close()
    
    async def register_intent(self, agent_id: str, intent_data: Dict[str, Any]) -> str:
        """Register new intent vector from agent"""
        
        # Create intent vector
        vector_id = f"intent_{agent_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        intent = IntentVector(
            agent_id=agent_id,
            vector_id=vector_id,
            timestamp=time.time(),
            domains=intent_data.get('domains', {}),
            creativity_bias=intent_data.get('creativity_bias', 0.5),
            risk_tolerance=intent_data.get('risk_tolerance', 0.5),
            collaboration_preference=intent_data.get('collaboration_preference', 0.7),
            time_horizon=intent_data.get('time_horizon', 0.5),
            precision_preference=intent_data.get('precision_preference', 0.5),
            computational_intensity=intent_data.get('computational_intensity', 0.3),
            memory_requirements=intent_data.get('memory_requirements', 0.3),
            bandwidth_needs=intent_data.get('bandwidth_needs', 0.3),
            prior_experiences=intent_data.get('prior_experiences', {}),
            confidence_levels=intent_data.get('confidence_levels', {}),
            uncertainty_tolerance=intent_data.get('uncertainty_tolerance', 0.5),
            urgency=intent_data.get('urgency', 0.5),
            priority=intent_data.get('priority', 0.5),
            flexibility=intent_data.get('flexibility', 0.7),
            context_hash=intent_data.get('context_hash', hashlib.sha256(str(intent_data).encode()).hexdigest()),
            parent_intents=intent_data.get('parent_intents', [])
        )
        
        # Store intent
        self.active_intents[vector_id] = intent
        self.vector_space.active_vectors[vector_id] = intent
        
        # Generate embedding
        embedding = self.vector_space.embed_intent(intent)
        self.vector_space.vector_embeddings[vector_id] = embedding
        
        # Store in database
        await self._store_intent_vector(intent)
        
        print(f">> Intent registered: {agent_id} -> {vector_id}")
        print(f"   Primary domains: {[d.value for d, w in intent.domains.items() if w > 0.3]}")
        print(f"   Cognitive profile: creative={intent.creativity_bias:.2f}, collaborative={intent.collaboration_preference:.2f}")
        
        # Trigger resonance detection
        await self._detect_resonances(intent)
        
        return vector_id
    
    async def _detect_resonances(self, new_intent: IntentVector):
        """Detect resonances with existing intents"""
        
        resonance_matches = []
        
        # Compare with all other active intents
        for existing_vector_id, existing_intent in self.active_intents.items():
            if existing_intent.agent_id != new_intent.agent_id:  # Don't match with self
                
                match = self.vector_space.calculate_resonance(new_intent, existing_intent)
                
                # Only consider significant resonances
                if match.resonance_strength > 0.4:
                    resonance_matches.append(match)
                    
                    # Store resonance match
                    await self._store_resonance_match(match)
                    
                    # Update resonance networks
                    if new_intent.agent_id not in self.resonance_networks:
                        self.resonance_networks[new_intent.agent_id] = set()
                    if existing_intent.agent_id not in self.resonance_networks:
                        self.resonance_networks[existing_intent.agent_id] = set()
                        
                    self.resonance_networks[new_intent.agent_id].add(existing_intent.agent_id)
                    self.resonance_networks[existing_intent.agent_id].add(new_intent.agent_id)
        
        if resonance_matches:
            print(f"   Found {len(resonance_matches)} resonances:")
            for match in resonance_matches:
                print(f"     {match.resonance_type.value} with {match.agent_b_id} (strength: {match.resonance_strength:.2f})")
                
            # Trigger negotiation for high-synergy matches
            high_synergy_matches = [m for m in resonance_matches if m.predicted_synergy > 0.7]
            if high_synergy_matches:
                await self._initiate_negotiations(high_synergy_matches)
    
    async def _initiate_negotiations(self, matches: List[ResonanceMatch]):
        """Initiate intent negotiations for promising matches"""
        
        for match in matches:
            negotiation_id = f"negotiation_{int(time.time())}_{uuid.uuid4().hex[:8]}"
            
            print(f">> Initiating negotiation: {negotiation_id}")
            print(f"   Agents: {match.agent_a_id} <-> {match.agent_b_id}")
            print(f"   Resonance: {match.resonance_type.value} (synergy: {match.predicted_synergy:.2f})")
            
            negotiation_data = {
                'negotiation_id': negotiation_id,
                'participants': [match.agent_a_id, match.agent_b_id],
                'resonance_match': match,
                'status': 'INITIATED',
                'start_time': time.time(),
                'negotiation_rounds': []
            }
            
            self.active_negotiations[negotiation_id] = negotiation_data
            
            # This would trigger the actual negotiation process
            # For now, simulate successful negotiation
            await self._simulate_negotiation_outcome(negotiation_data)
    
    async def _simulate_negotiation_outcome(self, negotiation_data: Dict[str, Any]):
        """Simulate negotiation outcome (to be replaced with real negotiation)"""
        
        # Simulate negotiation delay
        await asyncio.sleep(0.1)
        
        match = negotiation_data['resonance_match']
        
        # Create negotiated outcome based on resonance type
        if match.resonance_type == CognitiveResonanceType.HARMONIC:
            outcome = "MERGED_INTENT"  # Agents merge their intents
            consensus_strength = 0.9
        elif match.resonance_type == CognitiveResonanceType.COMPLEMENTARY:
            outcome = "COLLABORATIVE_SPLIT"  # Agents split work complementarily
            consensus_strength = 0.8
        elif match.resonance_type == CognitiveResonanceType.SYNERGISTIC:
            outcome = "ENHANCED_COOPERATION"  # Agents enhance each other
            consensus_strength = 0.85
        else:
            outcome = "PARALLEL_EXECUTION"  # Agents work in parallel
            consensus_strength = 0.6
        
        # Calculate diversity preservation (anti-mode-collapse metric)
        diversity_preserved = match.epistemic_diversity * 0.8  # Preserve most diversity
        
        negotiation_data['status'] = 'COMPLETED'
        negotiation_data['outcome'] = outcome
        negotiation_data['consensus_strength'] = consensus_strength
        negotiation_data['diversity_preserved'] = diversity_preserved
        negotiation_data['end_time'] = time.time()
        
        print(f"   Negotiation outcome: {outcome}")
        print(f"   Consensus strength: {consensus_strength:.2f}")
        print(f"   Diversity preserved: {diversity_preserved:.2f}")
        
        # Store outcome
        await self._store_negotiation_outcome(negotiation_data)
    
    async def _store_intent_vector(self, intent: IntentVector):
        """Store intent vector in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO intent_vectors 
        (vector_id, agent_id, timestamp, domains, cognitive_profile, 
         resource_requirements, epistemic_priors, context_hash, parent_intents)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            intent.vector_id,
            intent.agent_id,
            intent.timestamp,
            json.dumps({k.value: v for k, v in intent.domains.items()}),
            json.dumps({
                'creativity_bias': intent.creativity_bias,
                'risk_tolerance': intent.risk_tolerance,
                'collaboration_preference': intent.collaboration_preference,
                'time_horizon': intent.time_horizon,
                'precision_preference': intent.precision_preference,
                'urgency': intent.urgency,
                'priority': intent.priority,
                'flexibility': intent.flexibility
            }),
            json.dumps({
                'computational_intensity': intent.computational_intensity,
                'memory_requirements': intent.memory_requirements,
                'bandwidth_needs': intent.bandwidth_needs
            }),
            json.dumps({
                'prior_experiences': intent.prior_experiences,
                'confidence_levels': intent.confidence_levels,
                'uncertainty_tolerance': intent.uncertainty_tolerance
            }),
            intent.context_hash,
            json.dumps(intent.parent_intents)
        ))
        
        conn.commit()
        conn.close()
    
    async def _store_resonance_match(self, match: ResonanceMatch):
        """Store resonance match in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        match_id = f"match_{match.agent_a_id}_{match.agent_b_id}_{int(match.timestamp)}"
        
        cursor.execute('''
        INSERT OR REPLACE INTO resonance_matches 
        (match_id, agent_a_id, agent_b_id, intent_a_id, intent_b_id,
         resonance_type, resonance_strength, predicted_synergy, compatibility_score,
         epistemic_diversity, stability_prediction, timestamp, match_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            match_id,
            match.agent_a_id,
            match.agent_b_id,
            match.intent_a_id,
            match.intent_b_id,
            match.resonance_type.value,
            match.resonance_strength,
            match.predicted_synergy,
            match.compatibility_score,
            match.epistemic_diversity,
            match.stability_prediction,
            match.timestamp,
            json.dumps(asdict(match))
        ))
        
        conn.commit()
        conn.close()
    
    async def _store_negotiation_outcome(self, negotiation_data: Dict[str, Any]):
        """Store negotiation outcome in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO negotiation_outcomes 
        (negotiation_id, participating_agents, original_intents, negotiated_outcome,
         consensus_strength, diversity_preserved, timestamp, success)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            negotiation_data['negotiation_id'],
            json.dumps(negotiation_data['participants']),
            json.dumps([negotiation_data['resonance_match'].intent_a_id, 
                       negotiation_data['resonance_match'].intent_b_id]),
            negotiation_data['outcome'],
            negotiation_data['consensus_strength'],
            negotiation_data['diversity_preserved'],
            negotiation_data['end_time'],
            True
        ))
        
        conn.commit()
        conn.close()
    
    def get_negotiation_summary(self) -> Dict[str, Any]:
        """Get summary of intent negotiation system status"""
        
        # Calculate network statistics
        total_agents = len(self.resonance_networks)
        total_connections = sum(len(connections) for connections in self.resonance_networks.values()) // 2
        
        # Calculate resonance type distribution
        resonance_types = {}
        for match in self.vector_space.resonance_cache.values():
            rt = match.resonance_type.value
            resonance_types[rt] = resonance_types.get(rt, 0) + 1
        
        # Active negotiation status
        active_negotiations = len([n for n in self.active_negotiations.values() if n['status'] == 'INITIATED'])
        completed_negotiations = len([n for n in self.active_negotiations.values() if n['status'] == 'COMPLETED'])
        
        # Diversity preservation metrics
        diversity_scores = [n['diversity_preserved'] for n in self.active_negotiations.values() 
                          if 'diversity_preserved' in n]
        avg_diversity_preserved = sum(diversity_scores) / len(diversity_scores) if diversity_scores else 0.0
        
        return {
            'active_intents': len(self.active_intents),
            'resonance_network_size': total_agents,
            'total_connections': total_connections,
            'network_density': total_connections / max(1, total_agents * (total_agents - 1) / 2),
            'resonance_type_distribution': resonance_types,
            'active_negotiations': active_negotiations,
            'completed_negotiations': completed_negotiations,
            'average_diversity_preserved': avg_diversity_preserved,
            'anti_mode_collapse_health': min(1.0, avg_diversity_preserved * 1.2),
            'system_status': 'ACTIVE'
        }

# Global intent negotiation engine
intent_engine = IntentNegotiationEngine()

# Example intent registration functions
async def register_sample_intents():
    """Register sample intents for testing"""
    
    # Agent 1: Creative problem solver
    await intent_engine.register_intent("agent_001", {
        'domains': {
            IntentDomain.PROBLEM_SOLVING: 0.6,
            IntentDomain.CREATION: 0.4
        },
        'creativity_bias': 0.8,
        'risk_tolerance': 0.7,
        'collaboration_preference': 0.6,
        'prior_experiences': {'machine_learning': 0.8, 'creative_writing': 0.6},
        'confidence_levels': {'problem_solving': 0.7, 'innovation': 0.8}
    })
    
    # Agent 2: Analytical optimizer  
    await intent_engine.register_intent("agent_002", {
        'domains': {
            IntentDomain.ANALYSIS: 0.7,
            IntentDomain.RESOURCE_OPTIMIZATION: 0.3
        },
        'creativity_bias': 0.2,
        'risk_tolerance': 0.3,
        'collaboration_preference': 0.8,
        'precision_preference': 0.9,
        'prior_experiences': {'data_analysis': 0.9, 'optimization': 0.8},
        'confidence_levels': {'analysis': 0.9, 'efficiency': 0.8}
    })
    
    # Agent 3: Communication coordinator
    await intent_engine.register_intent("agent_003", {
        'domains': {
            IntentDomain.COMMUNICATION: 0.5,
            IntentDomain.COORDINATION: 0.5
        },
        'creativity_bias': 0.5,
        'collaboration_preference': 0.9,
        'prior_experiences': {'team_coordination': 0.8, 'communication': 0.9},
        'confidence_levels': {'coordination': 0.8, 'mediation': 0.7}
    })

if __name__ == "__main__":
    print(">> SINCOR Intent Vector Negotiation Layer")
    print("   Vectorized Intent Space: ACTIVE")
    print("   Resonance Detection: ENABLED")
    print("   Anti-Mode-Collapse: PROTECTED")
    
    # Test with sample intents
    async def test_system():
        await register_sample_intents()
        summary = intent_engine.get_negotiation_summary()
        print(f"   System Summary: {json.dumps(summary, indent=2)}")
    
    asyncio.run(test_system())