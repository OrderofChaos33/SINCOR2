"""
SINCOR Database Layer - PostgreSQL with SQLite fallback
Tracks agent metrics, interactions, and analytics
"""

import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

Base = declarative_base()

# ============================================================
# DATABASE MODELS
# ============================================================

class AgentMetric(Base):
    """Agent performance metrics over time"""
    __tablename__ = 'agent_metrics'

    id = Column(Integer, primary_key=True)
    agent_id = Column(String(50), nullable=False, index=True)
    agent_name = Column(String(100), nullable=False)
    archetype = Column(String(50), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Performance metrics
    tasks_completed = Column(Integer, default=0)
    tasks_failed = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    avg_response_time = Column(Float, default=0.0)  # milliseconds

    # Quality metrics
    quality_score = Column(Float, default=0.0)
    continuity_index = Column(Float, default=1.0)

    # Resource metrics
    cpu_usage = Column(Float, default=0.0)
    memory_usage = Column(Float, default=0.0)

    # Status
    status = Column(String(20), default='active')  # active, idle, error, offline
    health_score = Column(Float, default=100.0)

    # Metadata (renamed to avoid conflict with SQLAlchemy's metadata)
    extra_data = Column(JSON, nullable=True)


class AgentInteraction(Base):
    """Agent-to-agent interactions and task handoffs"""
    __tablename__ = 'agent_interactions'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Source and target agents
    source_agent_id = Column(String(50), nullable=False, index=True)
    source_agent_name = Column(String(100), nullable=False)
    target_agent_id = Column(String(50), nullable=False, index=True)
    target_agent_name = Column(String(100), nullable=False)

    # Interaction details
    interaction_type = Column(String(50), nullable=False)  # task_handoff, collaboration, query, response
    task_id = Column(String(100), nullable=True)
    task_description = Column(Text, nullable=True)

    # Outcome
    success = Column(Boolean, default=True)
    duration_ms = Column(Float, default=0.0)

    # Metadata (renamed to avoid conflict with SQLAlchemy's metadata)
    extra_data = Column(JSON, nullable=True)


class AgentTask(Base):
    """Individual agent tasks and their outcomes"""
    __tablename__ = 'agent_tasks'

    id = Column(Integer, primary_key=True)
    task_id = Column(String(100), unique=True, nullable=False, index=True)
    agent_id = Column(String(50), nullable=False, index=True)
    agent_name = Column(String(100), nullable=False)
    archetype = Column(String(50), nullable=False)

    # Task details
    task_type = Column(String(50), nullable=False)
    task_description = Column(Text, nullable=False)
    priority = Column(String(20), default='medium')  # low, medium, high, critical

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Status and outcome
    status = Column(String(20), default='pending')  # pending, in_progress, completed, failed, cancelled
    success = Column(Boolean, nullable=True)
    quality_score = Column(Float, nullable=True)

    # Performance
    duration_ms = Column(Float, nullable=True)

    # Results
    output = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    # Metadata (renamed to avoid conflict with SQLAlchemy's metadata)
    extra_data = Column(JSON, nullable=True)


class SystemMetric(Base):
    """System-wide performance metrics"""
    __tablename__ = 'system_metrics'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # System resources
    cpu_percent = Column(Float, default=0.0)
    memory_percent = Column(Float, default=0.0)
    disk_percent = Column(Float, default=0.0)

    # Application metrics
    active_agents = Column(Integer, default=0)
    active_tasks = Column(Integer, default=0)
    requests_per_sec = Column(Float, default=0.0)
    errors_per_min = Column(Float, default=0.0)
    avg_response_time_ms = Column(Float, default=0.0)

    # Security metrics
    threats_blocked = Column(Integer, default=0)
    rate_limit_hits = Column(Integer, default=0)

    # Metadata (renamed to avoid conflict with SQLAlchemy's metadata)
    extra_data = Column(JSON, nullable=True)


# ============================================================
# DATABASE CONNECTION
# ============================================================

class SINCORDatabase:
    """SINCOR Database Manager with PostgreSQL/SQLite support"""

    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self.db_type = None
        self.connect()

    def connect(self):
        """Connect to PostgreSQL (Railway) or fallback to SQLite"""
        # Try PostgreSQL first (Railway provides DATABASE_URL)
        database_url = os.environ.get('DATABASE_URL')

        if database_url:
            # Railway/Heroku provide postgres:// but SQLAlchemy needs postgresql://
            if database_url.startswith('postgres://'):
                database_url = database_url.replace('postgres://', 'postgresql://', 1)

            try:
                self.engine = create_engine(
                    database_url,
                    poolclass=NullPool,  # Better for serverless
                    echo=False
                )
                self.db_type = 'postgresql'
                print(f"[OK] Connected to PostgreSQL database")
            except Exception as e:
                print(f"[WARNING] PostgreSQL connection failed: {e}")
                print("Falling back to SQLite...")
                database_url = None

        # Fallback to SQLite
        if not database_url:
            db_path = os.path.join('data', 'sincor.db')
            os.makedirs('data', exist_ok=True)
            self.engine = create_engine(
                f'sqlite:///{db_path}',
                echo=False
            )
            self.db_type = 'sqlite'
            print(f"[OK] Connected to SQLite database: {db_path}")

        # Create tables
        Base.metadata.create_all(self.engine)

        # Create session factory
        self.SessionLocal = sessionmaker(bind=self.engine)
        print(f"[OK] Database tables initialized ({self.db_type})")

    def get_session(self) -> Session:
        """Get a new database session"""
        return self.SessionLocal()

    # ============================================================
    # AGENT METRICS
    # ============================================================

    def record_agent_metric(self, agent_data: Dict[str, Any]) -> bool:
        """Record agent performance metric"""
        try:
            session = self.get_session()
            metric = AgentMetric(
                agent_id=agent_data.get('agent_id'),
                agent_name=agent_data.get('agent_name'),
                archetype=agent_data.get('archetype'),
                tasks_completed=agent_data.get('tasks_completed', 0),
                tasks_failed=agent_data.get('tasks_failed', 0),
                success_rate=agent_data.get('success_rate', 0.0),
                avg_response_time=agent_data.get('avg_response_time', 0.0),
                quality_score=agent_data.get('quality_score', 0.0),
                continuity_index=agent_data.get('continuity_index', 1.0),
                cpu_usage=agent_data.get('cpu_usage', 0.0),
                memory_usage=agent_data.get('memory_usage', 0.0),
                status=agent_data.get('status', 'active'),
                health_score=agent_data.get('health_score', 100.0),
                extra_data=agent_data.get('metadata', {})
            )
            session.add(metric)
            session.commit()
            session.close()
            return True
        except Exception as e:
            print(f"Error recording agent metric: {e}")
            return False

    def get_agent_metrics(self, agent_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get recent agent metrics"""
        try:
            session = self.get_session()
            query = session.query(AgentMetric)

            if agent_id:
                query = query.filter(AgentMetric.agent_id == agent_id)

            metrics = query.order_by(AgentMetric.timestamp.desc()).limit(limit).all()

            result = [{
                'id': m.id,
                'agent_id': m.agent_id,
                'agent_name': m.agent_name,
                'archetype': m.archetype,
                'timestamp': m.timestamp.isoformat(),
                'tasks_completed': m.tasks_completed,
                'tasks_failed': m.tasks_failed,
                'success_rate': m.success_rate,
                'avg_response_time': m.avg_response_time,
                'quality_score': m.quality_score,
                'continuity_index': m.continuity_index,
                'status': m.status,
                'health_score': m.health_score
            } for m in metrics]

            session.close()
            return result
        except Exception as e:
            print(f"Error getting agent metrics: {e}")
            return []

    # ============================================================
    # AGENT INTERACTIONS
    # ============================================================

    def record_interaction(self, interaction_data: Dict[str, Any]) -> bool:
        """Record agent-to-agent interaction"""
        try:
            session = self.get_session()
            interaction = AgentInteraction(
                source_agent_id=interaction_data.get('source_agent_id'),
                source_agent_name=interaction_data.get('source_agent_name'),
                target_agent_id=interaction_data.get('target_agent_id'),
                target_agent_name=interaction_data.get('target_agent_name'),
                interaction_type=interaction_data.get('interaction_type'),
                task_id=interaction_data.get('task_id'),
                task_description=interaction_data.get('task_description'),
                success=interaction_data.get('success', True),
                duration_ms=interaction_data.get('duration_ms', 0.0),
                extra_data=interaction_data.get('metadata', {})
            )
            session.add(interaction)
            session.commit()
            session.close()
            return True
        except Exception as e:
            print(f"Error recording interaction: {e}")
            return False

    def get_interactions(self, agent_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get recent agent interactions"""
        try:
            session = self.get_session()
            query = session.query(AgentInteraction)

            if agent_id:
                query = query.filter(
                    (AgentInteraction.source_agent_id == agent_id) |
                    (AgentInteraction.target_agent_id == agent_id)
                )

            interactions = query.order_by(AgentInteraction.timestamp.desc()).limit(limit).all()

            result = [{
                'id': i.id,
                'timestamp': i.timestamp.isoformat(),
                'source_agent_id': i.source_agent_id,
                'source_agent_name': i.source_agent_name,
                'target_agent_id': i.target_agent_id,
                'target_agent_name': i.target_agent_name,
                'interaction_type': i.interaction_type,
                'task_id': i.task_id,
                'success': i.success,
                'duration_ms': i.duration_ms
            } for i in interactions]

            session.close()
            return result
        except Exception as e:
            print(f"Error getting interactions: {e}")
            return []

    # ============================================================
    # AGENT TASKS
    # ============================================================

    def create_task(self, task_data: Dict[str, Any]) -> Optional[str]:
        """Create a new agent task"""
        try:
            session = self.get_session()
            task = AgentTask(
                task_id=task_data.get('task_id'),
                agent_id=task_data.get('agent_id'),
                agent_name=task_data.get('agent_name'),
                archetype=task_data.get('archetype'),
                task_type=task_data.get('task_type'),
                task_description=task_data.get('task_description'),
                priority=task_data.get('priority', 'medium'),
                extra_data=task_data.get('metadata', {})
            )
            session.add(task)
            session.commit()
            task_id = task.task_id
            session.close()
            return task_id
        except Exception as e:
            print(f"Error creating task: {e}")
            return None

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> bool:
        """Update task status and results"""
        try:
            session = self.get_session()
            task = session.query(AgentTask).filter(AgentTask.task_id == task_id).first()

            if task:
                for key, value in updates.items():
                    if hasattr(task, key):
                        setattr(task, key, value)

                session.commit()
                session.close()
                return True

            session.close()
            return False
        except Exception as e:
            print(f"Error updating task: {e}")
            return False

    def get_tasks(self, agent_id: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Get agent tasks"""
        try:
            session = self.get_session()
            query = session.query(AgentTask)

            if agent_id:
                query = query.filter(AgentTask.agent_id == agent_id)

            if status:
                query = query.filter(AgentTask.status == status)

            tasks = query.order_by(AgentTask.created_at.desc()).limit(limit).all()

            result = [{
                'id': t.id,
                'task_id': t.task_id,
                'agent_id': t.agent_id,
                'agent_name': t.agent_name,
                'archetype': t.archetype,
                'task_type': t.task_type,
                'task_description': t.task_description,
                'priority': t.priority,
                'status': t.status,
                'created_at': t.created_at.isoformat(),
                'success': t.success,
                'quality_score': t.quality_score,
                'duration_ms': t.duration_ms
            } for t in tasks]

            session.close()
            return result
        except Exception as e:
            print(f"Error getting tasks: {e}")
            return []

    # ============================================================
    # SYSTEM METRICS
    # ============================================================

    def record_system_metric(self, metric_data: Dict[str, Any]) -> bool:
        """Record system-wide metric"""
        try:
            session = self.get_session()
            metric = SystemMetric(
                cpu_percent=metric_data.get('cpu_percent', 0.0),
                memory_percent=metric_data.get('memory_percent', 0.0),
                disk_percent=metric_data.get('disk_percent', 0.0),
                active_agents=metric_data.get('active_agents', 0),
                active_tasks=metric_data.get('active_tasks', 0),
                requests_per_sec=metric_data.get('requests_per_sec', 0.0),
                errors_per_min=metric_data.get('errors_per_min', 0.0),
                avg_response_time_ms=metric_data.get('avg_response_time_ms', 0.0),
                threats_blocked=metric_data.get('threats_blocked', 0),
                rate_limit_hits=metric_data.get('rate_limit_hits', 0),
                extra_data=metric_data.get('metadata', {})
            )
            session.add(metric)
            session.commit()
            session.close()
            return True
        except Exception as e:
            print(f"Error recording system metric: {e}")
            return False

    def get_system_metrics(self, limit: int = 100) -> List[Dict]:
        """Get recent system metrics"""
        try:
            session = self.get_session()
            metrics = session.query(SystemMetric).order_by(SystemMetric.timestamp.desc()).limit(limit).all()

            result = [{
                'id': m.id,
                'timestamp': m.timestamp.isoformat(),
                'cpu_percent': m.cpu_percent,
                'memory_percent': m.memory_percent,
                'disk_percent': m.disk_percent,
                'active_agents': m.active_agents,
                'active_tasks': m.active_tasks,
                'requests_per_sec': m.requests_per_sec,
                'errors_per_min': m.errors_per_min,
                'avg_response_time_ms': m.avg_response_time_ms,
                'threats_blocked': m.threats_blocked,
                'rate_limit_hits': m.rate_limit_hits
            } for m in metrics]

            session.close()
            return result
        except Exception as e:
            print(f"Error getting system metrics: {e}")
            return []

    # ============================================================
    # ANALYTICS
    # ============================================================

    def get_agent_analytics(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """Get aggregated agent analytics"""
        try:
            session = self.get_session()
            query = session.query(AgentTask)

            if agent_id:
                query = query.filter(AgentTask.agent_id == agent_id)

            all_tasks = query.all()

            total_tasks = len(all_tasks)
            completed_tasks = len([t for t in all_tasks if t.status == 'completed'])
            failed_tasks = len([t for t in all_tasks if t.status == 'failed'])
            avg_duration = sum([t.duration_ms for t in all_tasks if t.duration_ms]) / max(len([t for t in all_tasks if t.duration_ms]), 1)
            avg_quality = sum([t.quality_score for t in all_tasks if t.quality_score]) / max(len([t for t in all_tasks if t.quality_score]), 1)

            session.close()

            return {
                'total_tasks': total_tasks,
                'completed_tasks': completed_tasks,
                'failed_tasks': failed_tasks,
                'success_rate': (completed_tasks / max(total_tasks, 1)) * 100,
                'avg_duration_ms': avg_duration,
                'avg_quality_score': avg_quality
            }
        except Exception as e:
            print(f"Error getting agent analytics: {e}")
            return {}


# ============================================================
# GLOBAL INSTANCE
# ============================================================

db = SINCORDatabase()
