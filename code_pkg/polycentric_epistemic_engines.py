#!/usr/bin/env python3
"""
SINCOR Polycentric Epistemic Engines
Individual epistemic engines for each agent with unique priors and braided consensus

ARCHITECTURE NOTES:
- Each agent builds its own epistemic foundation with unique priors
- Knowledge representation is individualized and adaptive
- Braided consensus across multiple cognitive ledgers prevents groupthink
- Epistemic diversity is preserved and enhanced, not homogenized
"""

import numpy as np
import asyncio
import time
import json
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple, Set, Union
from enum import Enum
import uuid
import hashlib
from datetime import datetime
import threading
import pickle
import base64

class KnowledgeType(Enum):
    """Types of knowledge in epistemic space"""
    FACTUAL = "factual"              # Objective facts
    PROCEDURAL = "procedural"        # How-to knowledge
    EXPERIENTIAL = "experiential"    # From direct experience
    INFERENTIAL = "inferential"      # Derived through reasoning
    INTUITIVE = "intuitive"          # Pattern-based insights
    COLLABORATIVE = "collaborative"   # Co-constructed knowledge
    META_COGNITIVE = "meta_cognitive" # Knowledge about knowledge

class EpistemicConfidenceLevel(Enum):
    """Levels of epistemic confidence"""
    CERTAIN = 1.0
    HIGH_CONFIDENCE = 0.8
    MODERATE_CONFIDENCE = 0.6
    LOW_CONFIDENCE = 0.4
    UNCERTAIN = 0.2
    UNKNOWN = 0.0

class CognitiveBiasType(Enum):
    """Types of cognitive biases that shape epistemic processing"""
    CONFIRMATION_BIAS = "confirmation_bias"
    AVAILABILITY_HEURISTIC = "availability_heuristic"
    ANCHORING_BIAS = "anchoring_bias"
    REPRESENTATIVENESS = "representativeness"
    OVERCONFIDENCE = "overconfidence"
    ATTRIBUTION_BIAS = "attribution_bias"
    SURVIVORSHIP_BIAS = "survivorship_bias"

@dataclass
class EpistemicConcept:
    """Individual concept within an agent's epistemic space"""
    concept_id: str
    concept_name: str
    knowledge_type: KnowledgeType
    confidence: float
    certainty: float
    
    # Knowledge representation
    attributes: Dict[str, Any]
    relationships: Dict[str, float]  # concept_id -> strength
    evidence_sources: List[str]
    contradiction_sources: List[str]
    
    # Temporal aspects
    formation_timestamp: float
    last_updated: float
    access_frequency: float
    decay_rate: float
    
    # Individual learning context
    learning_context: Dict[str, Any]
    personal_relevance: float
    emotional_valence: float  # -1 to 1
    
    # Meta-epistemic properties
    source_credibility: float
    coherence_with_worldview: float
    practical_utility: float

@dataclass
class EpistemicRelationship:
    """Relationship between concepts in epistemic space"""
    relationship_id: str
    concept_a_id: str
    concept_b_id: str
    relationship_type: str  # "causes", "implies", "contradicts", "supports", etc.
    strength: float  # -1 to 1 (negative for contradictory)
    confidence: float
    
    # Contextual modifiers
    context_dependency: Dict[str, float]
    temporal_stability: float
    evidence_weight: float
    
    # Individual interpretation
    personal_interpretation: str
    certainty_modifier: float

@dataclass
class CognitiveBiasProfile:
    """Individual agent's cognitive bias profile"""
    agent_id: str
    bias_strengths: Dict[CognitiveBiasType, float]
    bias_awareness: Dict[CognitiveBiasType, float]  # How aware agent is of its biases
    
    # Adaptive biases (sometimes beneficial)
    adaptive_contexts: Dict[CognitiveBiasType, List[str]]
    
    # Bias interaction effects
    bias_amplification: Dict[Tuple[CognitiveBiasType, CognitiveBiasType], float]
    bias_mitigation: Dict[CognitiveBiasType, List[str]]  # Mitigation strategies

@dataclass
class EpistemicState:
    """Complete epistemic state of an agent at a point in time"""
    agent_id: str
    state_id: str
    timestamp: float
    
    # Core epistemic components
    concepts: Dict[str, EpistemicConcept]
    relationships: Dict[str, EpistemicRelationship]
    
    # Global epistemic properties
    coherence_score: float  # How well concepts fit together
    completeness_estimate: float  # How complete agent thinks its knowledge is
    uncertainty_level: float  # Overall uncertainty
    
    # Learning dynamics
    learning_rate: float
    forgetting_rate: float
    curiosity_level: float
    openness_to_revision: float
    
    # Bias profile
    cognitive_biases: CognitiveBiasProfile
    
    # Meta-cognitive awareness
    epistemic_humility: float  # Awareness of own limitations
    confidence_calibration: float  # How well confidence matches accuracy

class PolycentricEpistemicEngine:
    """Individual epistemic engine for a single agent"""
    
    def __init__(self, agent_id: str, initial_priors: Optional[Dict[str, Any]] = None, 
                 db_path: str = "epistemic_engines.db"):
        self.agent_id = agent_id
        self.db_path = db_path
        
        # Core epistemic state
        self.current_state = self._initialize_epistemic_state(initial_priors)
        
        # Learning and adaptation mechanisms
        self.learning_history: List[Dict[str, Any]] = []
        self.revision_history: List[Dict[str, Any]] = []
        
        # Interaction with other agents
        self.collaboration_history: Dict[str, List[Dict[str, Any]]] = {}
        self.trust_network: Dict[str, float] = {}  # agent_id -> trust_level
        
        # Unique epistemic fingerprint
        self.epistemic_signature = self._generate_epistemic_signature()
        
        self._setup_database()
        
    def _initialize_epistemic_state(self, initial_priors: Optional[Dict[str, Any]]) -> EpistemicState:
        """Initialize agent's unique epistemic foundation"""
        
        state_id = f"state_{self.agent_id}_{int(time.time())}"
        
        # Generate unique cognitive bias profile
        bias_profile = self._generate_unique_bias_profile()
        
        # Initialize with basic concepts if no priors given
        initial_concepts = {}
        initial_relationships = {}
        
        if initial_priors:
            # Process initial priors into epistemic concepts
            for concept_name, concept_data in initial_priors.get('concepts', {}).items():
                concept = self._create_epistemic_concept(concept_name, concept_data)
                initial_concepts[concept.concept_id] = concept
            
            # Process relationships
            for rel_data in initial_priors.get('relationships', []):
                relationship = self._create_epistemic_relationship(rel_data)
                initial_relationships[relationship.relationship_id] = relationship
        
        # Generate baseline epistemic properties
        coherence_score = self._calculate_initial_coherence(initial_concepts, initial_relationships)
        
        return EpistemicState(
            agent_id=self.agent_id,
            state_id=state_id,
            timestamp=time.time(),
            concepts=initial_concepts,
            relationships=initial_relationships,
            coherence_score=coherence_score,
            completeness_estimate=0.1,  # Start with low completeness
            uncertainty_level=0.7,      # Start with high uncertainty
            learning_rate=np.random.normal(0.3, 0.1),  # Individual learning rate
            forgetting_rate=np.random.normal(0.05, 0.02),  # Individual forgetting
            curiosity_level=np.random.uniform(0.3, 0.9),   # Individual curiosity
            openness_to_revision=np.random.uniform(0.4, 0.8),  # Individual openness
            cognitive_biases=bias_profile,
            epistemic_humility=np.random.uniform(0.3, 0.8),
            confidence_calibration=np.random.uniform(0.4, 0.7)
        )
    
    def _generate_unique_bias_profile(self) -> CognitiveBiasProfile:
        """Generate unique cognitive bias profile for this agent"""
        
        # Each agent has different bias strengths (this creates diversity)
        bias_strengths = {}
        bias_awareness = {}
        
        for bias_type in CognitiveBiasType:
            # Random but consistent bias strengths
            strength = np.random.beta(2, 5)  # Skewed toward lower values
            awareness = np.random.beta(3, 3)  # More balanced
            
            bias_strengths[bias_type] = strength
            bias_awareness[bias_type] = awareness
        
        return CognitiveBiasProfile(
            agent_id=self.agent_id,
            bias_strengths=bias_strengths,
            bias_awareness=bias_awareness,
            adaptive_contexts={},  # To be filled through experience
            bias_amplification={},
            bias_mitigation={}
        )
    
    def _create_epistemic_concept(self, concept_name: str, concept_data: Dict[str, Any]) -> EpistemicConcept:
        """Create new epistemic concept"""
        
        concept_id = f"concept_{self.agent_id}_{hashlib.md5(concept_name.encode()).hexdigest()[:8]}"
        
        return EpistemicConcept(
            concept_id=concept_id,
            concept_name=concept_name,
            knowledge_type=KnowledgeType(concept_data.get('type', 'factual')),
            confidence=concept_data.get('confidence', 0.5),
            certainty=concept_data.get('certainty', 0.5),
            attributes=concept_data.get('attributes', {}),
            relationships=concept_data.get('relationships', {}),
            evidence_sources=concept_data.get('evidence_sources', []),
            contradiction_sources=concept_data.get('contradiction_sources', []),
            formation_timestamp=time.time(),
            last_updated=time.time(),
            access_frequency=0.0,
            decay_rate=np.random.uniform(0.01, 0.05),  # Individual decay rates
            learning_context=concept_data.get('context', {}),
            personal_relevance=concept_data.get('relevance', 0.5),
            emotional_valence=concept_data.get('emotion', 0.0),
            source_credibility=concept_data.get('credibility', 0.5),
            coherence_with_worldview=concept_data.get('coherence', 0.5),
            practical_utility=concept_data.get('utility', 0.5)
        )
    
    def _create_epistemic_relationship(self, rel_data: Dict[str, Any]) -> EpistemicRelationship:
        """Create epistemic relationship between concepts"""
        
        relationship_id = f"rel_{self.agent_id}_{uuid.uuid4().hex[:8]}"
        
        return EpistemicRelationship(
            relationship_id=relationship_id,
            concept_a_id=rel_data['concept_a'],
            concept_b_id=rel_data['concept_b'],
            relationship_type=rel_data['type'],
            strength=rel_data.get('strength', 0.5),
            confidence=rel_data.get('confidence', 0.5),
            context_dependency=rel_data.get('context_dependency', {}),
            temporal_stability=rel_data.get('stability', 0.5),
            evidence_weight=rel_data.get('evidence_weight', 0.5),
            personal_interpretation=rel_data.get('interpretation', ''),
            certainty_modifier=rel_data.get('certainty_modifier', 1.0)
        )
    
    def _calculate_initial_coherence(self, concepts: Dict[str, EpistemicConcept],
                                   relationships: Dict[str, EpistemicRelationship]) -> float:
        """Calculate initial coherence score"""
        
        if not concepts or not relationships:
            return 0.1  # Low coherence with minimal knowledge
        
        # Simple coherence metric: ratio of supportive to contradictory relationships
        supportive = sum(1 for rel in relationships.values() if rel.strength > 0)
        contradictory = sum(1 for rel in relationships.values() if rel.strength < 0)
        
        if supportive + contradictory == 0:
            return 0.5
        
        return supportive / (supportive + contradictory)
    
    def _generate_epistemic_signature(self) -> str:
        """Generate unique epistemic fingerprint for this agent"""
        
        # Create signature based on agent's unique epistemic properties
        signature_data = {
            'agent_id': self.agent_id,
            'bias_profile': [bias.value for bias in CognitiveBiasType],
            'learning_rate': self.current_state.learning_rate,
            'curiosity_level': self.current_state.curiosity_level,
            'epistemic_humility': self.current_state.epistemic_humility,
            'timestamp': int(time.time())
        }
        
        signature_string = json.dumps(signature_data, sort_keys=True)
        return hashlib.sha256(signature_string.encode()).hexdigest()
    
    async def learn_from_experience(self, experience: Dict[str, Any]) -> bool:
        """Learn and update epistemic state from new experience"""
        
        print(f">> {self.agent_id}: Learning from experience: {experience.get('type', 'unknown')}")
        
        experience_id = f"exp_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        # Extract knowledge from experience
        new_concepts = self._extract_concepts_from_experience(experience)
        new_relationships = self._extract_relationships_from_experience(experience)
        
        # Apply cognitive biases to interpretation
        biased_concepts, biased_relationships = self._apply_cognitive_biases(
            new_concepts, new_relationships, experience
        )
        
        # Update epistemic state
        concepts_updated = 0
        relationships_updated = 0
        
        for concept in biased_concepts:
            if await self._integrate_concept(concept, experience):
                concepts_updated += 1
        
        for relationship in biased_relationships:
            if await self._integrate_relationship(relationship, experience):
                relationships_updated += 1
        
        # Update global epistemic properties
        await self._update_epistemic_properties()
        
        # Record learning event
        learning_event = {
            'experience_id': experience_id,
            'timestamp': time.time(),
            'experience_type': experience.get('type', 'unknown'),
            'concepts_updated': concepts_updated,
            'relationships_updated': relationships_updated,
            'coherence_change': self._calculate_coherence_change()
        }
        
        self.learning_history.append(learning_event)
        
        print(f"   Updated {concepts_updated} concepts, {relationships_updated} relationships")
        print(f"   Current coherence: {self.current_state.coherence_score:.2f}")
        
        return concepts_updated > 0 or relationships_updated > 0
    
    def _extract_concepts_from_experience(self, experience: Dict[str, Any]) -> List[EpistemicConcept]:
        """Extract new concepts from experience"""
        
        new_concepts = []
        
        # Process different types of experiences
        exp_type = experience.get('type', 'observation')
        
        if exp_type == 'observation':
            # Create concepts from observed phenomena
            for observation in experience.get('observations', []):
                concept = self._create_concept_from_observation(observation)
                new_concepts.append(concept)
                
        elif exp_type == 'interaction':
            # Create concepts from agent interactions
            for interaction in experience.get('interactions', []):
                concept = self._create_concept_from_interaction(interaction)
                new_concepts.append(concept)
                
        elif exp_type == 'problem_solving':
            # Create concepts from problem-solving experience
            solution_concept = self._create_concept_from_solution(experience)
            new_concepts.append(solution_concept)
        
        return new_concepts
    
    def _extract_relationships_from_experience(self, experience: Dict[str, Any]) -> List[EpistemicRelationship]:
        """Extract new relationships from experience"""
        
        new_relationships = []
        
        # Identify causal relationships
        causal_data = experience.get('causal_relationships', [])
        for causal in causal_data:
            relationship = self._create_causal_relationship(causal)
            new_relationships.append(relationship)
        
        # Identify correlation relationships
        correlation_data = experience.get('correlations', [])
        for correlation in correlation_data:
            relationship = self._create_correlation_relationship(correlation)
            new_relationships.append(relationship)
        
        return new_relationships
    
    def _apply_cognitive_biases(self, concepts: List[EpistemicConcept], 
                               relationships: List[EpistemicRelationship],
                               experience: Dict[str, Any]) -> Tuple[List[EpistemicConcept], List[EpistemicRelationship]]:
        """Apply agent's cognitive biases to interpretation"""
        
        biased_concepts = []
        biased_relationships = []
        
        for concept in concepts:
            # Apply confirmation bias
            if CognitiveBiasType.CONFIRMATION_BIAS in self.current_state.cognitive_biases.bias_strengths:
                bias_strength = self.current_state.cognitive_biases.bias_strengths[CognitiveBiasType.CONFIRMATION_BIAS]
                
                # Check if concept aligns with existing beliefs
                alignment = self._calculate_belief_alignment(concept)
                
                if alignment > 0.5:  # Aligns with existing beliefs
                    concept.confidence *= (1 + bias_strength * 0.3)  # Boost confidence
                else:  # Contradicts existing beliefs
                    concept.confidence *= (1 - bias_strength * 0.3)  # Reduce confidence
            
            # Apply availability heuristic
            if CognitiveBiasType.AVAILABILITY_HEURISTIC in self.current_state.cognitive_biases.bias_strengths:
                bias_strength = self.current_state.cognitive_biases.bias_strengths[CognitiveBiasType.AVAILABILITY_HEURISTIC]
                
                # Recent or memorable experiences get higher weight
                recency_factor = 1 / (time.time() - concept.formation_timestamp + 1)
                concept.confidence *= (1 + bias_strength * recency_factor)
            
            # Apply overconfidence bias
            if CognitiveBiasType.OVERCONFIDENCE in self.current_state.cognitive_biases.bias_strengths:
                bias_strength = self.current_state.cognitive_biases.bias_strengths[CognitiveBiasType.OVERCONFIDENCE]
                concept.confidence = min(1.0, concept.confidence * (1 + bias_strength * 0.2))
            
            biased_concepts.append(concept)
        
        # Apply biases to relationships
        for relationship in relationships:
            # Biases affect relationship strength and confidence similarly
            bias_adjustment = np.mean([
                self.current_state.cognitive_biases.bias_strengths.get(bias, 0.5) 
                for bias in self.current_state.cognitive_biases.bias_strengths
            ])
            
            relationship.confidence *= (0.8 + bias_adjustment * 0.4)  # Adjust within reasonable range
            biased_relationships.append(relationship)
        
        return biased_concepts, biased_relationships
    
    def _calculate_belief_alignment(self, new_concept: EpistemicConcept) -> float:
        """Calculate how well a new concept aligns with existing beliefs"""
        
        alignment_scores = []
        
        # Check alignment with existing concepts
        for existing_concept in self.current_state.concepts.values():
            if existing_concept.concept_name.lower() in new_concept.concept_name.lower() or \
               new_concept.concept_name.lower() in existing_concept.concept_name.lower():
                
                # Similar concepts - check attribute alignment
                attribute_alignment = self._calculate_attribute_similarity(
                    existing_concept.attributes, new_concept.attributes
                )
                alignment_scores.append(attribute_alignment * existing_concept.confidence)
        
        return np.mean(alignment_scores) if alignment_scores else 0.5
    
    def _calculate_attribute_similarity(self, attrs_a: Dict[str, Any], attrs_b: Dict[str, Any]) -> float:
        """Calculate similarity between concept attributes"""
        
        all_keys = set(attrs_a.keys()) | set(attrs_b.keys())
        if not all_keys:
            return 0.5
        
        similarities = []
        for key in all_keys:
            val_a = attrs_a.get(key, None)
            val_b = attrs_b.get(key, None)
            
            if val_a is None or val_b is None:
                similarities.append(0.0)
            elif isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
                # Numeric similarity
                max_val = max(abs(val_a), abs(val_b), 1.0)
                similarities.append(1.0 - abs(val_a - val_b) / max_val)
            elif str(val_a) == str(val_b):
                similarities.append(1.0)
            else:
                similarities.append(0.0)
        
        return np.mean(similarities)
    
    async def _integrate_concept(self, new_concept: EpistemicConcept, experience: Dict[str, Any]) -> bool:
        """Integrate new concept into epistemic state"""
        
        # Check if concept already exists
        existing_concept = self._find_similar_concept(new_concept)
        
        if existing_concept:
            # Update existing concept
            return await self._update_existing_concept(existing_concept, new_concept, experience)
        else:
            # Add new concept
            self.current_state.concepts[new_concept.concept_id] = new_concept
            return True
    
    def _find_similar_concept(self, new_concept: EpistemicConcept) -> Optional[EpistemicConcept]:
        """Find similar existing concept"""
        
        for existing_concept in self.current_state.concepts.values():
            similarity = self._calculate_concept_similarity(existing_concept, new_concept)
            if similarity > 0.8:  # High similarity threshold
                return existing_concept
        
        return None
    
    def _calculate_concept_similarity(self, concept_a: EpistemicConcept, concept_b: EpistemicConcept) -> float:
        """Calculate similarity between two concepts"""
        
        # Name similarity
        name_similarity = self._calculate_string_similarity(concept_a.concept_name, concept_b.concept_name)
        
        # Attribute similarity
        attr_similarity = self._calculate_attribute_similarity(concept_a.attributes, concept_b.attributes)
        
        # Knowledge type similarity
        type_similarity = 1.0 if concept_a.knowledge_type == concept_b.knowledge_type else 0.5
        
        return (name_similarity * 0.4 + attr_similarity * 0.4 + type_similarity * 0.2)
    
    def _calculate_string_similarity(self, str_a: str, str_b: str) -> float:
        """Calculate string similarity using simple metrics"""
        
        # Jaccard similarity of word sets
        words_a = set(str_a.lower().split())
        words_b = set(str_b.lower().split())
        
        intersection = words_a & words_b
        union = words_a | words_b
        
        return len(intersection) / len(union) if union else 0.0
    
    async def _update_existing_concept(self, existing: EpistemicConcept, new: EpistemicConcept, 
                                     experience: Dict[str, Any]) -> bool:
        """Update existing concept with new information"""
        
        # Weighted average update based on confidence
        total_confidence = existing.confidence + new.confidence
        
        if total_confidence > 0:
            # Update confidence
            existing.confidence = min(1.0, total_confidence * 0.6)  # Some decay to prevent overconfidence
            
            # Update attributes
            for key, value in new.attributes.items():
                if key in existing.attributes:
                    # Weighted average
                    existing_weight = existing.confidence
                    new_weight = new.confidence
                    total_weight = existing_weight + new_weight
                    
                    if isinstance(value, (int, float)) and isinstance(existing.attributes[key], (int, float)):
                        existing.attributes[key] = (
                            existing.attributes[key] * existing_weight + value * new_weight
                        ) / total_weight
                else:
                    existing.attributes[key] = value
            
            # Update metadata
            existing.last_updated = time.time()
            existing.access_frequency += 1
            
            return True
        
        return False
    
    async def _integrate_relationship(self, new_relationship: EpistemicRelationship, 
                                    experience: Dict[str, Any]) -> bool:
        """Integrate new relationship into epistemic state"""
        
        # Check if relationship already exists
        existing_rel = self._find_similar_relationship(new_relationship)
        
        if existing_rel:
            # Update existing relationship
            return await self._update_existing_relationship(existing_rel, new_relationship)
        else:
            # Add new relationship
            self.current_state.relationships[new_relationship.relationship_id] = new_relationship
            return True
    
    def _find_similar_relationship(self, new_rel: EpistemicRelationship) -> Optional[EpistemicRelationship]:
        """Find similar existing relationship"""
        
        for existing_rel in self.current_state.relationships.values():
            if (existing_rel.concept_a_id == new_rel.concept_a_id and 
                existing_rel.concept_b_id == new_rel.concept_b_id and
                existing_rel.relationship_type == new_rel.relationship_type):
                return existing_rel
        
        return None
    
    async def _update_existing_relationship(self, existing: EpistemicRelationship, 
                                          new: EpistemicRelationship) -> bool:
        """Update existing relationship with new information"""
        
        # Weighted average of strengths
        total_confidence = existing.confidence + new.confidence
        
        if total_confidence > 0:
            existing.strength = (
                existing.strength * existing.confidence + new.strength * new.confidence
            ) / total_confidence
            
            existing.confidence = min(1.0, total_confidence * 0.7)
            
            return True
        
        return False
    
    async def _update_epistemic_properties(self):
        """Update global epistemic properties based on current state"""
        
        # Recalculate coherence
        self.current_state.coherence_score = self._calculate_current_coherence()
        
        # Update completeness estimate (based on number of concepts and relationships)
        concept_count = len(self.current_state.concepts)
        relationship_count = len(self.current_state.relationships)
        
        # Simple heuristic for completeness
        expected_relationships = concept_count * (concept_count - 1) * 0.1  # 10% connectivity
        completeness = min(1.0, relationship_count / max(1, expected_relationships))
        
        self.current_state.completeness_estimate = completeness
        
        # Update uncertainty level (based on confidence distribution)
        confidences = [concept.confidence for concept in self.current_state.concepts.values()]
        if confidences:
            avg_confidence = np.mean(confidences)
            self.current_state.uncertainty_level = 1.0 - avg_confidence
        
        # Adapt learning parameters based on recent performance
        self._adapt_learning_parameters()
    
    def _calculate_current_coherence(self) -> float:
        """Calculate current epistemic coherence"""
        
        if not self.current_state.relationships:
            return 0.5
        
        # Count supportive vs contradictory relationships
        supportive = 0
        contradictory = 0
        
        for rel in self.current_state.relationships.values():
            weighted_strength = rel.strength * rel.confidence
            
            if weighted_strength > 0.3:
                supportive += weighted_strength
            elif weighted_strength < -0.3:
                contradictory += abs(weighted_strength)
        
        total = supportive + contradictory
        return supportive / total if total > 0 else 0.5
    
    def _adapt_learning_parameters(self):
        """Adapt learning parameters based on recent experience"""
        
        if len(self.learning_history) < 3:
            return
        
        # Analyze recent learning outcomes
        recent_events = self.learning_history[-10:]  # Last 10 events
        
        # Calculate average learning success
        success_rate = np.mean([
            1.0 if event['concepts_updated'] > 0 or event['relationships_updated'] > 0 else 0.0
            for event in recent_events
        ])
        
        # Adjust learning rate based on success
        if success_rate > 0.7:
            # High success - can be more aggressive
            self.current_state.learning_rate = min(1.0, self.current_state.learning_rate * 1.1)
        elif success_rate < 0.3:
            # Low success - be more conservative
            self.current_state.learning_rate = max(0.1, self.current_state.learning_rate * 0.9)
        
        # Adjust openness based on coherence changes
        coherence_changes = [event.get('coherence_change', 0) for event in recent_events if 'coherence_change' in event]
        
        if coherence_changes:
            avg_coherence_change = np.mean(coherence_changes)
            
            if avg_coherence_change > 0:
                # Positive coherence changes - be more open
                self.current_state.openness_to_revision = min(1.0, self.current_state.openness_to_revision * 1.05)
            else:
                # Negative coherence changes - be more conservative
                self.current_state.openness_to_revision = max(0.2, self.current_state.openness_to_revision * 0.95)
    
    def _calculate_coherence_change(self) -> float:
        """Calculate how coherence has changed with latest update"""
        
        # This is a simplified placeholder
        # In practice, you'd compare with previous state
        return np.random.normal(0.0, 0.1)  # Small random changes for now
    
    def _create_concept_from_observation(self, observation: Dict[str, Any]) -> EpistemicConcept:
        """Create concept from observation data"""
        
        concept_name = observation.get('name', f"observation_{int(time.time())}")
        
        return self._create_epistemic_concept(concept_name, {
            'type': 'experiential',
            'confidence': observation.get('certainty', 0.6),
            'certainty': observation.get('certainty', 0.6),
            'attributes': observation.get('properties', {}),
            'context': observation.get('context', {}),
            'relevance': observation.get('importance', 0.5),
            'emotion': observation.get('emotional_impact', 0.0),
            'credibility': observation.get('source_reliability', 0.7)
        })
    
    def _create_concept_from_interaction(self, interaction: Dict[str, Any]) -> EpistemicConcept:
        """Create concept from agent interaction"""
        
        concept_name = f"interaction_{interaction.get('type', 'communication')}"
        
        return self._create_epistemic_concept(concept_name, {
            'type': 'collaborative',
            'confidence': interaction.get('trust_level', 0.5),
            'certainty': interaction.get('clarity', 0.5),
            'attributes': {
                'interaction_type': interaction.get('type', 'unknown'),
                'other_agent': interaction.get('agent_id', 'unknown'),
                'outcome': interaction.get('outcome', 'neutral')
            },
            'context': interaction.get('context', {}),
            'relevance': interaction.get('importance', 0.6),
            'credibility': interaction.get('other_agent_credibility', 0.5)
        })
    
    def _create_concept_from_solution(self, experience: Dict[str, Any]) -> EpistemicConcept:
        """Create concept from problem-solving experience"""
        
        problem_type = experience.get('problem_type', 'unknown')
        solution = experience.get('solution', {})
        
        concept_name = f"solution_{problem_type}"
        
        return self._create_epistemic_concept(concept_name, {
            'type': 'procedural',
            'confidence': solution.get('effectiveness', 0.7),
            'certainty': solution.get('confidence', 0.7),
            'attributes': {
                'problem_type': problem_type,
                'solution_steps': solution.get('steps', []),
                'effectiveness': solution.get('effectiveness', 0.7),
                'efficiency': solution.get('efficiency', 0.5)
            },
            'context': experience.get('context', {}),
            'relevance': 0.8,  # Problem solutions are typically highly relevant
            'utility': solution.get('reusability', 0.6)
        })
    
    def _create_causal_relationship(self, causal_data: Dict[str, Any]) -> EpistemicRelationship:
        """Create causal relationship from data"""
        
        return self._create_epistemic_relationship({
            'concept_a': causal_data['cause'],
            'concept_b': causal_data['effect'],
            'type': 'causes',
            'strength': causal_data.get('strength', 0.7),
            'confidence': causal_data.get('confidence', 0.6),
            'evidence_weight': causal_data.get('evidence_strength', 0.5),
            'stability': causal_data.get('consistency', 0.6)
        })
    
    def _create_correlation_relationship(self, correlation_data: Dict[str, Any]) -> EpistemicRelationship:
        """Create correlation relationship from data"""
        
        return self._create_epistemic_relationship({
            'concept_a': correlation_data['concept_a'],
            'concept_b': correlation_data['concept_b'],
            'type': 'correlates_with',
            'strength': correlation_data.get('correlation', 0.5),
            'confidence': correlation_data.get('confidence', 0.4),
            'evidence_weight': correlation_data.get('sample_size', 0.3),
            'stability': correlation_data.get('stability', 0.4)
        })
    
    def _setup_database(self):
        """Setup database for this agent's epistemic engine"""
        
        agent_db_path = self.db_path.replace('.db', f'_{self.agent_id}.db')
        
        conn = sqlite3.connect(agent_db_path)
        cursor = conn.cursor()
        
        # Epistemic states table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS epistemic_states (
            state_id TEXT PRIMARY KEY,
            agent_id TEXT,
            timestamp REAL,
            coherence_score REAL,
            completeness_estimate REAL,
            uncertainty_level REAL,
            learning_rate REAL,
            epistemic_humility REAL,
            state_data TEXT
        )
        ''')
        
        # Concepts table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS concepts (
            concept_id TEXT PRIMARY KEY,
            concept_name TEXT,
            knowledge_type TEXT,
            confidence REAL,
            certainty REAL,
            formation_timestamp REAL,
            last_updated REAL,
            concept_data TEXT
        )
        ''')
        
        # Relationships table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS relationships (
            relationship_id TEXT PRIMARY KEY,
            concept_a_id TEXT,
            concept_b_id TEXT,
            relationship_type TEXT,
            strength REAL,
            confidence REAL,
            relationship_data TEXT
        )
        ''')
        
        conn.commit()
        conn.close()
        
        print(f">> Epistemic engine database initialized for {self.agent_id}")
    
    def get_epistemic_summary(self) -> Dict[str, Any]:
        """Get summary of current epistemic state"""
        
        return {
            'agent_id': self.agent_id,
            'epistemic_signature': self.epistemic_signature,
            'concept_count': len(self.current_state.concepts),
            'relationship_count': len(self.current_state.relationships),
            'coherence_score': self.current_state.coherence_score,
            'completeness_estimate': self.current_state.completeness_estimate,
            'uncertainty_level': self.current_state.uncertainty_level,
            'learning_rate': self.current_state.learning_rate,
            'curiosity_level': self.current_state.curiosity_level,
            'epistemic_humility': self.current_state.epistemic_humility,
            'dominant_biases': [
                bias.value for bias, strength in self.current_state.cognitive_biases.bias_strengths.items()
                if strength > 0.6
            ],
            'learning_events': len(self.learning_history),
            'trust_network_size': len(self.trust_network)
        }

# Factory function for creating diverse epistemic engines
def create_diverse_epistemic_engines(agent_count: int, base_db_path: str = "epistemic_engines") -> Dict[str, PolycentricEpistemicEngine]:
    """Create a collection of diverse epistemic engines"""
    
    engines = {}
    
    for i in range(agent_count):
        agent_id = f"agent_{i:03d}"
        
        # Create diverse initial priors for each agent
        initial_priors = generate_diverse_priors(agent_id)
        
        engine = PolycentricEpistemicEngine(
            agent_id=agent_id,
            initial_priors=initial_priors,
            db_path=f"{base_db_path}_{agent_id}.db"
        )
        
        engines[agent_id] = engine
    
    print(f">> Created {agent_count} diverse epistemic engines")
    return engines

def generate_diverse_priors(agent_id: str) -> Dict[str, Any]:
    """Generate diverse initial priors for an agent"""
    
    # Each agent gets different foundational concepts and biases
    agent_hash = int(hashlib.md5(agent_id.encode()).hexdigest(), 16)
    np.random.seed(agent_hash % 2**32)  # Deterministic but different per agent
    
    # Different domains of expertise
    domains = ['technology', 'biology', 'economics', 'psychology', 'physics', 'art', 'philosophy']
    primary_domain = domains[agent_hash % len(domains)]
    
    concepts = {
        f"{primary_domain}_fundamentals": {
            'type': 'factual',
            'confidence': np.random.uniform(0.7, 0.9),
            'certainty': np.random.uniform(0.6, 0.8),
            'attributes': {
                'domain': primary_domain,
                'expertise_level': np.random.uniform(0.6, 0.9)
            },
            'relevance': np.random.uniform(0.8, 1.0)
        }
    }
    
    # Add some random additional concepts
    for _ in range(np.random.randint(2, 6)):
        concept_name = f"concept_{np.random.randint(1000, 9999)}"
        concepts[concept_name] = {
            'type': np.random.choice(['factual', 'procedural', 'experiential']),
            'confidence': np.random.uniform(0.3, 0.8),
            'certainty': np.random.uniform(0.3, 0.7),
            'attributes': {'random_value': np.random.uniform(0, 1)},
            'relevance': np.random.uniform(0.3, 0.8)
        }
    
    return {'concepts': concepts, 'relationships': []}

if __name__ == "__main__":
    print(">> SINCOR Polycentric Epistemic Engines")
    print("   Individual Epistemic Foundations: ACTIVE")
    print("   Cognitive Diversity Preservation: ENABLED")
    print("   Anti-Mode-Collapse Architecture: DEPLOYED")
    
    # Create sample diverse engines
    engines = create_diverse_epistemic_engines(5)
    
    # Test learning
    async def test_learning():
        for agent_id, engine in engines.items():
            await engine.learn_from_experience({
                'type': 'observation',
                'observations': [{
                    'name': 'test_phenomenon',
                    'certainty': 0.7,
                    'properties': {'color': 'blue', 'intensity': 0.8},
                    'importance': 0.6
                }]
            })
            
            summary = engine.get_epistemic_summary()
            print(f"   {agent_id}: {summary['concept_count']} concepts, coherence: {summary['coherence_score']:.2f}")
    
    asyncio.run(test_learning())