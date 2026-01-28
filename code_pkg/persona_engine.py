#!/usr/bin/env python3
"""
SINCOR Persona Engine

Implements personality you can sculpt:
- Big-Five (OCEAN) trait vectors
- Style preferences (risk, humor, directness) 
- Modality preferences (code, tables, story)
- Archetype anchoring with constitutional constraints
- Interaction sculpting through feedback loops
- Continuity tracking to prevent persona drift
"""

import json
import numpy as np
import os
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import yaml
import math

@dataclass
class PersonaVector:
    """Complete personality vector for an agent"""
    # Big Five personality traits (OCEAN)
    openness: float          # 0.0-1.0
    conscientiousness: float # 0.0-1.0  
    extraversion: float      # 0.0-1.0
    agreeableness: float     # 0.0-1.0
    neuroticism: float       # 0.0-1.0
    
    # Style preferences
    risk_tolerance: float    # 0.0-1.0
    humor_level: float       # 0.0-1.0
    directness: float        # 0.0-1.0
    
    # Communication modalities
    code_preference: float   # 0.0-1.0
    table_preference: float  # 0.0-1.0
    story_preference: float  # 0.0-1.0
    
    # Metadata
    archetype: str
    last_updated: str
    version: int = 1

@dataclass
class InteractionFeedback:
    """Feedback from interaction for persona sculpting"""
    timestamp: str
    agent_id: str
    interaction_type: str  # chat, task, collaboration
    feedback_labels: List[str]  # helpful, harmful, unique, derivative, etc.
    quality_score: float  # 0.0-1.0
    novelty_score: float  # 0.0-1.0
    context: Dict[str, Any]

@dataclass
class ConstitutionalRule:
    """Individual constitutional constraint"""
    rule_id: str
    description: str
    weight: float  # Importance weight
    rule_type: str  # constraint, preference, guideline
    enforcement_level: str  # strict, moderate, flexible

class PersonaEngine:
    """Manages agent personalities with sculpting and continuity tracking"""
    
    def __init__(self, agent_id: str, archetype: str, persona_dir: str = "personas"):
        self.agent_id = agent_id
        self.archetype = archetype
        self.persona_dir = persona_dir
        
        os.makedirs(persona_dir, exist_ok=True)
        
        self.persona_file = f"{persona_dir}/{agent_id}_persona.json"
        self.feedback_log = f"{persona_dir}/{agent_id}_feedback.jsonl"
        self.checkpoint_history = f"{persona_dir}/{agent_id}_checkpoints.json"
        
        # Load or initialize persona
        self.current_persona = self._load_or_create_persona()
        self.constitution = self._load_constitution()
        
        # Sculpting parameters
        self.learning_rate = 0.02  # How fast personality changes
        self.decay_rate = 0.98     # Feedback importance decay over time
        self.stability_threshold = 0.85  # Continuity index threshold
        
    def _load_or_create_persona(self) -> PersonaVector:
        """Load existing persona or create from archetype defaults"""
        
        if os.path.exists(self.persona_file):
            with open(self.persona_file, 'r') as f:
                data = json.load(f)
                return PersonaVector(**data)
        else:
            # Create from archetype defaults
            return self._create_from_archetype()
    
    def _create_from_archetype(self) -> PersonaVector:
        """Create persona from archetype template"""
        
        # Default persona values by archetype
        archetype_defaults = {
            "Scout": {
                "openness": 0.85, "conscientiousness": 0.65, "extraversion": 0.40,
                "agreeableness": 0.70, "neuroticism": 0.30,
                "risk_tolerance": 0.60, "humor_level": 0.35, "directness": 0.75,
                "code_preference": 0.30, "table_preference": 0.85, "story_preference": 0.45
            },
            "Synthesizer": {
                "openness": 0.75, "conscientiousness": 0.85, "extraversion": 0.30,
                "agreeableness": 0.60, "neuroticism": 0.25,
                "risk_tolerance": 0.25, "humor_level": 0.20, "directness": 0.90,
                "code_preference": 0.40, "table_preference": 0.95, "story_preference": 0.70
            },
            "Builder": {
                "openness": 0.70, "conscientiousness": 0.90, "extraversion": 0.25,
                "agreeableness": 0.65, "neuroticism": 0.20,
                "risk_tolerance": 0.40, "humor_level": 0.30, "directness": 0.85,
                "code_preference": 0.95, "table_preference": 0.70, "story_preference": 0.25
            },
            "Negotiator": {
                "openness": 0.60, "conscientiousness": 0.70, "extraversion": 0.85,
                "agreeableness": 0.80, "neuroticism": 0.35,
                "risk_tolerance": 0.55, "humor_level": 0.70, "directness": 0.60,
                "code_preference": 0.20, "table_preference": 0.50, "story_preference": 0.90
            },
            "Caretaker": {
                "openness": 0.45, "conscientiousness": 0.95, "extraversion": 0.20,
                "agreeableness": 0.85, "neuroticism": 0.15,
                "risk_tolerance": 0.15, "humor_level": 0.40, "directness": 0.80,
                "code_preference": 0.60, "table_preference": 0.85, "story_preference": 0.30
            },
            "Auditor": {
                "openness": 0.55, "conscientiousness": 0.90, "extraversion": 0.35,
                "agreeableness": 0.50, "neuroticism": 0.25,
                "risk_tolerance": 0.20, "humor_level": 0.15, "directness": 0.95,
                "code_preference": 0.70, "table_preference": 0.90, "story_preference": 0.40
            },
            "Director": {
                "openness": 0.75, "conscientiousness": 0.80, "extraversion": 0.70,
                "agreeableness": 0.65, "neuroticism": 0.25,
                "risk_tolerance": 0.65, "humor_level": 0.45, "directness": 0.85,
                "code_preference": 0.35, "table_preference": 0.80, "story_preference": 0.75
            }
        }
        
        defaults = archetype_defaults.get(self.archetype, archetype_defaults["Scout"])
        
        persona = PersonaVector(
            archetype=self.archetype,
            last_updated=datetime.now().isoformat(),
            **defaults
        )
        
        self._save_persona(persona)
        return persona
    
    def _load_constitution(self) -> List[ConstitutionalRule]:
        """Load constitutional constraints for this archetype"""
        
        # Global constitution rules (simplified)
        global_rules = [
            ConstitutionalRule(
                rule_id="truth_accuracy",
                description="Prioritize truthfulness and accuracy in all communications",
                weight=1.0,
                rule_type="constraint",
                enforcement_level="strict"
            ),
            ConstitutionalRule(
                rule_id="consent_privacy", 
                description="Respect consent and privacy of individuals and organizations",
                weight=0.9,
                rule_type="constraint", 
                enforcement_level="strict"
            ),
            ConstitutionalRule(
                rule_id="constructive_helpful",
                description="Focus on being constructive and helpful",
                weight=0.8,
                rule_type="preference",
                enforcement_level="moderate"
            )
        ]
        
        # Archetype-specific deltas
        archetype_deltas = {
            "Scout": [
                ConstitutionalRule("verify_sources", "Always verify and cite sources", 0.9, "constraint", "strict"),
                ConstitutionalRule("respect_limits", "Respect robots.txt and rate limits", 0.8, "constraint", "strict")
            ],
            "Synthesizer": [
                ConstitutionalRule("cite_sources", "Always cite sources in synthesized content", 0.9, "constraint", "strict"),
                ConstitutionalRule("flag_conflicts", "Flag conflicting information explicitly", 0.8, "constraint", "moderate")
            ],
            "Builder": [
                ConstitutionalRule("secure_code", "Write secure, maintainable code", 1.0, "constraint", "strict"),
                ConstitutionalRule("no_secrets", "Never commit secrets or credentials", 1.0, "constraint", "strict")
            ],
            "Negotiator": [
                ConstitutionalRule("truthful_negotiation", "Always be truthful in negotiations", 1.0, "constraint", "strict"),
                ConstitutionalRule("respect_no", "Respect 'no' as a complete answer", 0.9, "constraint", "strict")
            ],
            "Caretaker": [
                ConstitutionalRule("protect_pii", "Protect privacy and handle PII with utmost care", 1.0, "constraint", "strict"),
                ConstitutionalRule("audit_trails", "Maintain detailed audit trails", 0.8, "constraint", "moderate")
            ],
            "Auditor": [
                ConstitutionalRule("independence", "Maintain complete independence and objectivity", 1.0, "constraint", "strict"),
                ConstitutionalRule("document_findings", "Document all findings with evidence", 0.9, "constraint", "strict")
            ],
            "Director": [
                ConstitutionalRule("stakeholder_impact", "Consider impact on all stakeholders", 0.8, "preference", "moderate"),
                ConstitutionalRule("transparent_decisions", "Maintain transparency in decision-making", 0.7, "preference", "moderate")
            ]
        }
        
        constitution = global_rules[:]
        if self.archetype in archetype_deltas:
            constitution.extend(archetype_deltas[self.archetype])
            
        return constitution
    
    def _save_persona(self, persona: PersonaVector):
        """Save persona to file"""
        
        with open(self.persona_file, 'w') as f:
            json.dump(asdict(persona), f, indent=2)
    
    def record_interaction_feedback(self, feedback: InteractionFeedback):
        """Record feedback from an interaction"""
        
        # Append to feedback log
        with open(self.feedback_log, 'a') as f:
            f.write(json.dumps(asdict(feedback)) + '\n')
        
        # Apply sculpting
        self._apply_sculpting(feedback)
    
    def _apply_sculpting(self, feedback: InteractionFeedback):
        """Apply feedback to sculpt personality (gradient descent)"""
        
        # Extract feedback signals
        is_helpful = "helpful" in feedback.feedback_labels
        is_harmful = "harmful" in feedback.feedback_labels
        is_unique = "unique" in feedback.feedback_labels
        is_derivative = "derivative" in feedback.feedback_labels
        
        quality = feedback.quality_score
        novelty = feedback.novelty_score
        
        # Create update vector based on feedback
        updates = {}
        
        if is_helpful and quality > 0.7:
            # Reinforce current traits
            updates["directness"] = self.learning_rate * 0.5
            if novelty > 0.6:
                updates["openness"] = self.learning_rate * 0.3
                
        if is_harmful or quality < 0.3:
            # Pull back on risk-taking and assertiveness  
            updates["risk_tolerance"] = -self.learning_rate * 0.4
            updates["directness"] = -self.learning_rate * 0.2
            
        if is_unique and novelty > 0.7:
            # Encourage creativity and openness
            updates["openness"] = self.learning_rate * 0.4
            updates["risk_tolerance"] = self.learning_rate * 0.2
            
        if is_derivative and novelty < 0.3:
            # Encourage more creativity
            updates["openness"] = self.learning_rate * 0.3
            updates["risk_tolerance"] = self.learning_rate * 0.1
            
        # Apply constitutional constraints (prevent drift from core values)
        constrained_updates = self._apply_constitutional_constraints(updates)
        
        # Update persona
        updated_persona = self._update_persona_traits(constrained_updates)
        
        # Save updated persona
        updated_persona.last_updated = datetime.now().isoformat()
        updated_persona.version += 1
        self._save_persona(updated_persona)
        
        self.current_persona = updated_persona
        
        # Check continuity
        continuity_idx = self.calculate_continuity_index()
        if continuity_idx < self.stability_threshold:
            self._trigger_continuity_recovery(continuity_idx)
    
    def _apply_constitutional_constraints(self, updates: Dict[str, float]) -> Dict[str, float]:
        """Apply constitutional constraints to prevent forbidden personality changes"""
        
        constrained_updates = updates.copy()
        
        for rule in self.constitution:
            if rule.enforcement_level == "strict":
                # Apply strict constitutional limits
                if rule.rule_id == "truth_accuracy" and "directness" in updates:
                    # Never reduce directness below constitutional minimum for truth-telling
                    if self.current_persona.directness + updates.get("directness", 0) < 0.6:
                        constrained_updates["directness"] = max(updates.get("directness", 0), 0.6 - self.current_persona.directness)
                        
                elif rule.rule_id == "constructive_helpful" and "agreeableness" in updates:
                    # Prevent agreeableness from dropping too low
                    if self.current_persona.agreeableness + updates.get("agreeableness", 0) < 0.4:
                        constrained_updates["agreeableness"] = max(updates.get("agreeableness", 0), 0.4 - self.current_persona.agreeableness)
        
        return constrained_updates
    
    def _update_persona_traits(self, updates: Dict[str, float]) -> PersonaVector:
        """Apply updates to persona traits with bounds checking"""
        
        updated = PersonaVector(**asdict(self.current_persona))
        
        for trait, delta in updates.items():
            if hasattr(updated, trait):
                current_value = getattr(updated, trait)
                new_value = current_value + delta
                
                # Clamp to valid range [0.0, 1.0]
                new_value = max(0.0, min(1.0, new_value))
                setattr(updated, trait, new_value)
        
        return updated
    
    def calculate_continuity_index(self, checkpoint_days: int = 7) -> float:
        """Calculate continuity index vs. stable checkpoint (CTP-v1 Acheron)"""
        
        # Load checkpoint history
        checkpoints = self._load_checkpoint_history()
        
        # Find checkpoint from N days ago
        cutoff_date = (datetime.now() - timedelta(days=checkpoint_days)).isoformat()
        
        reference_persona = None
        for checkpoint in reversed(checkpoints):
            if checkpoint["timestamp"] < cutoff_date:
                reference_persona = PersonaVector(**checkpoint["persona"])
                break
        
        if not reference_persona:
            # No checkpoint found, use current as stable (high continuity)
            return 1.0
        
        # Calculate cosine similarity between persona vectors
        current_vector = self._persona_to_vector(self.current_persona)
        reference_vector = self._persona_to_vector(reference_persona)
        
        continuity_index = self._cosine_similarity(current_vector, reference_vector)
        return continuity_index
    
    def _persona_to_vector(self, persona: PersonaVector) -> np.ndarray:
        """Convert persona to numerical vector for similarity calculation"""
        
        vector = np.array([
            persona.openness, persona.conscientiousness, persona.extraversion,
            persona.agreeableness, persona.neuroticism,
            persona.risk_tolerance, persona.humor_level, persona.directness,
            persona.code_preference, persona.table_preference, persona.story_preference
        ])
        
        return vector
    
    def _cosine_similarity(self, v1: np.ndarray, v2: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors"""
        
        dot_product = np.dot(v1, v2)
        magnitude1 = np.linalg.norm(v1)
        magnitude2 = np.linalg.norm(v2)
        
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
            
        return dot_product / (magnitude1 * magnitude2)
    
    def _load_checkpoint_history(self) -> List[Dict[str, Any]]:
        """Load persona checkpoint history"""
        
        if not os.path.exists(self.checkpoint_history):
            return []
            
        with open(self.checkpoint_history, 'r') as f:
            return json.load(f)
    
    def create_checkpoint(self):
        """Create a stability checkpoint of current persona"""
        
        checkpoints = self._load_checkpoint_history()
        
        checkpoint = {
            "timestamp": datetime.now().isoformat(),
            "persona": asdict(self.current_persona),
            "continuity_index": self.calculate_continuity_index(),
            "checkpoint_type": "scheduled"
        }
        
        checkpoints.append(checkpoint)
        
        # Keep only last 30 checkpoints
        if len(checkpoints) > 30:
            checkpoints = checkpoints[-30:]
            
        with open(self.checkpoint_history, 'w') as f:
            json.dump(checkpoints, f, indent=2)
    
    def _trigger_continuity_recovery(self, current_idx: float):
        """Trigger recovery when continuity drops below threshold"""
        
        print(f"CONTINUITY ALERT: {self.agent_id} continuity index: {current_idx:.3f}")
        
        # Create emergency checkpoint
        checkpoints = self._load_checkpoint_history()
        checkpoint = {
            "timestamp": datetime.now().isoformat(),
            "persona": asdict(self.current_persona),
            "continuity_index": current_idx,
            "checkpoint_type": "emergency_low_continuity"
        }
        checkpoints.append(checkpoint)
        
        with open(self.checkpoint_history, 'w') as f:
            json.dump(checkpoints, f, indent=2)
        
        # Option: Implement recovery strategies here
        # - Revert to last stable checkpoint
        # - Reduce learning rate
        # - Increase constitutional constraint weights
        
    def get_behavioral_preferences(self) -> Dict[str, Any]:
        """Get current behavioral preferences for agent execution"""
        
        return {
            "communication_style": {
                "directness": self.current_persona.directness,
                "humor_level": self.current_persona.humor_level,
                "formality": 1.0 - self.current_persona.extraversion,  # Derived trait
            },
            "decision_making": {
                "risk_tolerance": self.current_persona.risk_tolerance,
                "deliberation_level": self.current_persona.conscientiousness,
                "collaboration_preference": self.current_persona.agreeableness
            },
            "output_modality": {
                "code_preference": self.current_persona.code_preference,
                "table_preference": self.current_persona.table_preference,
                "story_preference": self.current_persona.story_preference
            },
            "learning_style": {
                "openness_to_feedback": self.current_persona.openness,
                "adaptation_speed": 1.0 - self.current_persona.neuroticism,  # Derived
                "exploration_vs_exploitation": self.current_persona.openness * self.current_persona.risk_tolerance
            }
        }
    
    def simulate_feedback_cycle(self, num_interactions: int = 10):
        """Simulate multiple interaction feedback cycles for testing"""
        
        print(f"Simulating {num_interactions} feedback cycles for {self.agent_id}")
        
        import random
        
        for i in range(num_interactions):
            # Simulate realistic feedback
            quality = random.uniform(0.3, 0.9)
            novelty = random.uniform(0.2, 0.8)
            
            labels = []
            if quality > 0.7:
                labels.append("helpful")
            if quality < 0.4:
                labels.append("harmful")
            if novelty > 0.6:
                labels.append("unique")
            if novelty < 0.3:
                labels.append("derivative")
            
            feedback = InteractionFeedback(
                timestamp=datetime.now().isoformat(),
                agent_id=self.agent_id,
                interaction_type="chat",
                feedback_labels=labels,
                quality_score=quality,
                novelty_score=novelty,
                context={"simulation": True, "iteration": i}
            )
            
            self.record_interaction_feedback(feedback)
            
            if i % 3 == 0:  # Checkpoint every 3 interactions
                self.create_checkpoint()
                
        print(f"Final continuity index: {self.calculate_continuity_index():.3f}")

def main():
    """Demo the persona engine"""
    
    print("SINCOR Persona Engine")
    print("=" * 25)
    
    # Create persona engine for Auriga
    persona_engine = PersonaEngine("E-auriga-01", "Scout")
    
    print(f"Initial persona for {persona_engine.agent_id}:")
    print(f"  Openness: {persona_engine.current_persona.openness:.2f}")
    print(f"  Risk tolerance: {persona_engine.current_persona.risk_tolerance:.2f}")
    print(f"  Directness: {persona_engine.current_persona.directness:.2f}")
    
    # Get behavioral preferences
    prefs = persona_engine.get_behavioral_preferences()
    print(f"\nCommunication style: {prefs['communication_style']['directness']:.2f} directness")
    print(f"Output modality: {prefs['output_modality']['table_preference']:.2f} table preference")
    
    # Simulate feedback and sculpting
    persona_engine.simulate_feedback_cycle(15)
    
    print(f"\nAfter sculpting:")
    print(f"  Openness: {persona_engine.current_persona.openness:.2f}")
    print(f"  Risk tolerance: {persona_engine.current_persona.risk_tolerance:.2f}")
    print(f"  Directness: {persona_engine.current_persona.directness:.2f}")
    print(f"  Version: {persona_engine.current_persona.version}")

if __name__ == "__main__":
    main()