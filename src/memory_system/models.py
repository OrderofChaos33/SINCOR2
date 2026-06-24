from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4
from pydantic import BaseModel, Field, model_validator

def utc_now():
    return datetime.now(timezone.utc)

def generate_id(prefix=""):
    return f"{prefix}_{uuid4().hex[:16]}" if prefix else uuid4().hex

def compute_content_hash(content):
    if isinstance(content, (dict, list)):
        content = json.dumps(content, sort_keys=True, default=str)
    elif not isinstance(content, str):
        content = str(content)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

class EventType(str, Enum):
    TASK_COMPLETION = "task_completion"
    DECISION = "decision"
    OBSERVATION = "observation"
    ERROR = "error"
    USER_INTERACTION = "user_interaction"
    AGENT_ACTION = "agent_action"
    LEARNING = "learning"
    SYSTEM = "system"

class RelationType(str, Enum):
    CAUSES = "causes"
    ENABLES = "enables"
    CONTRADICTS = "contradicts"
    SUPPORTS = "supports"
    SIMILAR_TO = "similar_to"
    PART_OF = "part_of"
    DEPENDS_ON = "depends_on"
    EVOLVED_FROM = "evolved_from"
    CUSTOM = "custom"

class EpisodicEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: generate_id("evt"))
    agent_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    event_type: EventType
    content_hash: str = ""
    content_json: Dict[str, Any]
    source_task_id: Optional[str] = None
    retention_days: int = Field(default=90, ge=1, le=3650)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def compute_hash(self):
        if not self.content_hash:
            self.content_hash = compute_content_hash(self.content_json)
        return self

class SemanticFact(BaseModel):
    fact_id: str = Field(default_factory=lambda: generate_id("fact"))
    agent_id: str
    fact_text: str = Field(..., min_length=3, max_length=4000)
    confidence_score: float = Field(default=0.8, ge=0.0, le=1.0)
    source_events: List[str] = Field(default_factory=list)
    last_verified: datetime = Field(default_factory=utc_now)
    verification_count: int = Field(default=0, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class GraphEdge(BaseModel):
    source_id: str
    target_id: str
    relation_type: RelationType = RelationType.CUSTOM
    strength: float = Field(default=0.7, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class Procedure(BaseModel):
    procedure_id: str = Field(default_factory=lambda: generate_id("proc"))
    agent_id: str
    name: str = Field(..., min_length=2, max_length=128)
    version: int = Field(default=1, ge=1)
    prompt_template: Optional[str] = None
    code: Optional[str] = None
    success_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    last_used: Optional[datetime] = None
    usage_count: int = Field(default=0, ge=0)
    immutable: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

class AutobiographicalNarrative(BaseModel):
    narrative_id: str = Field(default_factory=lambda: generate_id("narr"))
    agent_id: str
    narrative_text: str = Field(..., min_length=10)
    themes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    version: int = Field(default=1)
    source_event_count: int = Field(default=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class RetrievalResult(BaseModel):
    id: str
    content: str
    final_score: float
    source_tier: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    explanation: Optional[str] = None

class MemoryConfig(BaseModel):
    agent_id: str
    base_dir: str = "./memory_data"
    db_path: str = "memory.db"
    vector_persist_dir: str = "chroma"
    graph_persist_path: str = "knowledge_graph.pkl"
    kv_cache_path: str = "kv_cache.db"
    embedding_model: str = "all-MiniLM-L6-v2"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    default_retention_days: int = 90
    auto_purge_enabled: bool = True
    auto_summarize_every_n_events: int = 100
    max_kv_cache_size: int = 10000

    def resolved_db_path(self):
        from pathlib import Path
        return str(Path(self.base_dir) / self.db_path)

    def resolved_vector_dir(self):
        from pathlib import Path
        return str(Path(self.base_dir) / self.vector_persist_dir)

    def resolved_graph_path(self):
        from pathlib import Path
        return str(Path(self.base_dir) / self.graph_persist_path)

    def resolved_kv_path(self):
        from pathlib import Path
        return str(Path(self.base_dir) / self.kv_cache_path)