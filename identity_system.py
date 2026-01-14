#!/usr/bin/env python3
"""
SINCOR Identity & Authority System

Implements cryptographic identity, soulbound tokens, and constitution management
for the 43-agent swarm architecture.
"""

import hashlib
import json
import secrets
import base64
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import yaml

@dataclass
class SBTRole:
    """Soulbound Token Role Definition"""
    family: str        # Archetype (Scout, Builder, etc.)
    grade: int         # Seniority level (1-6)
    competencies: List[str]  # Skill set
    issued_date: str   # ISO date string
    expires_date: Optional[str] = None  # Optional expiration
    version: int = 1   # SBT version for promotions

@dataclass 
class IdentityRecord:
    """Minimal Identity Record for each agent"""
    id: str                    # E-auriga-07 format
    sigil_key: str            # DID key
    sbt_role: SBTRole         # Role token
    constitution_sha256: str   # Constitution hash
    created_date: str         # ISO date
    last_checkpoint: Optional[str] = None  # Last continuity checkpoint

class IdentitySystem:
    """Manages cryptographic identities and authority for SINCOR agents"""
    
    def __init__(self, constitution_path: str = "constitution/global.md"):
        self.constitution_path = constitution_path
        self.identity_registry = {}
        
    def generate_did_key(self, agent_name: str) -> str:
        """Generate a DID key for an agent (simplified implementation)"""
        
        # In real implementation, this would use proper Ed25519 key generation
        # For now, we'll create a deterministic but unique identifier
        seed = f"sincor-{agent_name}-{secrets.token_hex(16)}"
        key_material = hashlib.sha256(seed.encode()).digest()
        
        # Encode as base64 with DID key prefix (simplified)
        encoded_key = base64.urlsafe_b64encode(key_material).decode().rstrip('=')
        return f"did:key:z6Mk{encoded_key[:32]}"
    
    def create_sbt_role(self, family: str, grade: int, competencies: List[str]) -> SBTRole:
        """Create a Soulbound Token role definition"""
        
        issued_date = datetime.now().isoformat()
        
        # High-level roles (Directors) may have longer terms
        if family == "Director" and grade >= 4:
            expires_date = (datetime.now() + timedelta(days=730)).isoformat()
        else:
            expires_date = None  # No expiration for most roles
            
        return SBTRole(
            family=family,
            grade=grade, 
            competencies=competencies,
            issued_date=issued_date,
            expires_date=expires_date
        )
    
    def compute_constitution_hash(self, global_constitution: str, 
                                 archetype_deltas: List[str] = None) -> str:
        """Compute Merkle root hash of constitution + archetype deltas"""
        
        # Create constitution document
        constitution_doc = {
            "global": global_constitution,
            "deltas": archetype_deltas or [],
            "version": "1.0",
            "timestamp": datetime.now().isoformat()
        }
        
        # Serialize and hash
        doc_json = json.dumps(constitution_doc, sort_keys=True)
        return hashlib.sha256(doc_json.encode()).hexdigest()
    
    def register_identity(self, agent_config: Dict[str, Any]) -> IdentityRecord:
        """Register a new agent identity in the system"""
        
        agent_name = agent_config["name"]
        agent_id = agent_config["id"]
        
        # Generate DID key
        sigil_key = self.generate_did_key(agent_name)
        
        # Create SBT role
        sbt_data = agent_config["sbt_role"]
        sbt_role = self.create_sbt_role(
            family=sbt_data["family"],
            grade=sbt_data["grade"],
            competencies=sbt_data["competencies"]
        )
        
        # Load constitution and compute hash
        constitution_hash = self.compute_constitution_hash(
            global_constitution="SINCOR Global Constitution v1.0",  # Placeholder
            archetype_deltas=[]  # Will load from archetype files
        )
        
        # Create identity record
        identity = IdentityRecord(
            id=agent_id,
            sigil_key=sigil_key,
            sbt_role=sbt_role,
            constitution_sha256=constitution_hash,
            created_date=datetime.now().isoformat()
        )
        
        # Register in system
        self.identity_registry[agent_id] = identity
        
        return identity
    
    def promote_agent(self, agent_id: str, new_grade: int, 
                     new_competencies: List[str] = None) -> bool:
        """Promote an agent to a new grade (issues new SBT version)"""
        
        if agent_id not in self.identity_registry:
            return False
            
        identity = self.identity_registry[agent_id]
        old_sbt = identity.sbt_role
        
        # Create new SBT version
        new_sbt = SBTRole(
            family=old_sbt.family,
            grade=new_grade,
            competencies=new_competencies or old_sbt.competencies,
            issued_date=datetime.now().isoformat(),
            expires_date=old_sbt.expires_date,
            version=old_sbt.version + 1
        )
        
        identity.sbt_role = new_sbt
        return True
    
    def verify_authority(self, agent_id: str, required_competency: str) -> bool:
        """Verify an agent has authority for a specific competency"""
        
        if agent_id not in self.identity_registry:
            return False
            
        identity = self.identity_registry[agent_id]
        return required_competency in identity.sbt_role.competencies
    
    def export_identity_record(self, agent_id: str) -> Dict[str, Any]:
        """Export identity record for agent configuration"""
        
        if agent_id not in self.identity_registry:
            return {}
            
        identity = self.identity_registry[agent_id]
        return asdict(identity)
    
    def sign_action(self, agent_id: str, action_data: Dict[str, Any]) -> Dict[str, Any]:
        """Sign an action/artifact with agent's DID key (simplified)"""
        
        if agent_id not in self.identity_registry:
            raise ValueError(f"Agent {agent_id} not registered")
            
        identity = self.identity_registry[agent_id]
        
        # Create signed artifact (simplified - real implementation would use proper crypto)
        signed_artifact = {
            "data": action_data,
            "signature": {
                "signer": identity.sigil_key,
                "timestamp": datetime.now().isoformat(),
                "hash": hashlib.sha256(json.dumps(action_data, sort_keys=True).encode()).hexdigest()
            }
        }
        
        return signed_artifact
    
    def load_all_agents(self, agents_dir: str = "agents"):
        """Load and register all agent identities from agent configs"""
        
        import os
        import glob
        
        agent_files = glob.glob(os.path.join(agents_dir, "E-*.yaml"))
        
        for agent_file in agent_files:
            with open(agent_file, 'r') as f:
                agent_config = yaml.safe_load(f)
                
            identity = self.register_identity(agent_config)
            print(f"Registered identity: {identity.id} -> {identity.sigil_key}")
    
    def generate_identity_ledger(self) -> Dict[str, Any]:
        """Generate complete identity ledger for the swarm"""
        
        ledger = {
            "version": "1.0",
            "created": datetime.now().isoformat(), 
            "total_agents": len(self.identity_registry),
            "identities": {}
        }
        
        for agent_id, identity in self.identity_registry.items():
            ledger["identities"][agent_id] = asdict(identity)
            
        return ledger

def main():
    """Demo the identity system with SINCOR agents"""
    
    print("SINCOR Identity & Authority System")
    print("=" * 40)
    
    # Initialize system
    identity_sys = IdentitySystem()
    
    # Load all agents
    identity_sys.load_all_agents()
    
    # Generate identity ledger
    ledger = identity_sys.generate_identity_ledger()
    
    # Save ledger
    with open("identity_ledger.json", "w") as f:
        json.dump(ledger, f, indent=2)
    
    print(f"Generated identity ledger for {ledger['total_agents']} agents")
    
    # Demo promotion
    auriga_id = "E-auriga-01"
    if auriga_id in identity_sys.identity_registry:
        print(f"\nPromoting {auriga_id} to grade 3...")
        identity_sys.promote_agent(auriga_id, 3, ["prospect", "scrape", "summarize", "lead"])
        
    # Demo authority verification
    print(f"Can Auriga prospect? {identity_sys.verify_authority(auriga_id, 'prospect')}")
    print(f"Can Auriga build? {identity_sys.verify_authority(auriga_id, 'develop')}")
    
    # Demo signing
    sample_action = {"action": "prospect", "target": "tech_companies", "region": "US"}
    signed = identity_sys.sign_action(auriga_id, sample_action)
    print(f"\nSigned action hash: {signed['signature']['hash'][:16]}...")

if __name__ == "__main__":
    main()