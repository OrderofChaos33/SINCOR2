#!/usr/bin/env python3
"""
A2A Task Store — pluggable persistence for A2A tasks and push configs.

Backends
--------
memory   pure dict (dev only)
sqlite   uses PersistentStore (Railway volume /data recommended)
redis    REDIS_URL, keys a2a:task:{id} and a2a:push:{id}

Selection: A2A_TASK_STORE=memory|sqlite|redis  (default memory)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.a2a.store")


class TaskStore(ABC):
    @abstractmethod
    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def put(self, task_id: str, task_dict: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def delete(self, task_id: str) -> None:
        ...

    @abstractmethod
    def list_all(self) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_push(self, task_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def set_push(self, task_id: str, config: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def delete_push(self, task_id: str) -> None:
        ...

    @abstractmethod
    def list_push(self) -> List[Dict[str, Any]]:
        ...


class MemoryTaskStore(TaskStore):
    def __init__(self) -> None:
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._push: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._tasks.get(task_id)

    def put(self, task_id: str, task_dict: Dict[str, Any]) -> None:
        with self._lock:
            self._tasks[task_id] = task_dict

    def delete(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)

    def list_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._tasks.values())

    def get_push(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._push.get(task_id)

    def set_push(self, task_id: str, config: Dict[str, Any]) -> None:
        with self._lock:
            self._push[task_id] = config

    def delete_push(self, task_id: str) -> None:
        with self._lock:
            self._push.pop(task_id, None)

    def list_push(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._push.values())


class SqliteTaskStore(TaskStore):
    """Durable store backed by PersistentStore.a2a_tasks table."""

    def __init__(self) -> None:
        from sincor2.persistent_store import get_store
        self._store = get_store()
        self._push: Dict[str, Dict[str, Any]] = {}
        self._push_lock = threading.Lock()

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get_task(task_id)

    def put(self, task_id: str, task_dict: Dict[str, Any]) -> None:
        task_dict = dict(task_dict)
        task_dict["id"] = task_id
        self._store.upsert_task(task_dict)

    def delete(self, task_id: str) -> None:
        existing = self.get(task_id)
        if existing:
            existing["state"] = "canceled"
            self.put(task_id, existing)

    def list_all(self) -> List[Dict[str, Any]]:
        return self._store.list_tasks(limit=5000)

    def get_push(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._push_lock:
            return self._push.get(task_id)

    def set_push(self, task_id: str, config: Dict[str, Any]) -> None:
        with self._push_lock:
            self._push[task_id] = config

    def delete_push(self, task_id: str) -> None:
        with self._push_lock:
            self._push.pop(task_id, None)

    def list_push(self) -> List[Dict[str, Any]]:
        with self._push_lock:
            return list(self._push.values())


class RedisTaskStore(TaskStore):
    """Redis-backed store. Requires REDIS_URL. Keys: a2a:task:{id}, a2a:push:{id}."""

    def __init__(self, redis_url: Optional[str] = None) -> None:
        try:
            import redis
        except ImportError as err:
            raise RuntimeError(
                "redis package required for A2A_TASK_STORE=redis. "
                "Add redis>=5.0.0 to requirements and install."
            ) from err

        url = redis_url or os.getenv("REDIS_URL") or os.getenv("REDIS_PRIVATE_URL")
        if not url:
            raise RuntimeError("REDIS_URL (or REDIS_PRIVATE_URL) must be set for A2A_TASK_STORE=redis")

        self._r = redis.from_url(url, decode_responses=True, socket_connect_timeout=5)
        self._r.ping()
        self._prefix = os.getenv("A2A_REDIS_PREFIX", "a2a")
        logger.info("RedisTaskStore connected  prefix=%s", self._prefix)

    def _task_key(self, task_id: str) -> str:
        return f"{self._prefix}:task:{task_id}"

    def _push_key(self, task_id: str) -> str:
        return f"{self._prefix}:push:{task_id}"

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        raw = self._r.get(self._task_key(task_id))
        if not raw:
            return None
        return json.loads(raw)

    def put(self, task_id: str, task_dict: Dict[str, Any]) -> None:
        task_dict = dict(task_dict)
        task_dict["id"] = task_id
        ttl = int(os.getenv("A2A_TASK_TTL_SECONDS", str(7 * 24 * 3600)))
        self._r.set(self._task_key(task_id), json.dumps(task_dict, default=str), ex=ttl)
        self._r.sadd(f"{self._prefix}:task_ids", task_id)

    def delete(self, task_id: str) -> None:
        self._r.delete(self._task_key(task_id))
        self._r.srem(f"{self._prefix}:task_ids", task_id)

    def list_all(self) -> List[Dict[str, Any]]:
        ids = self._r.smembers(f"{self._prefix}:task_ids") or set()
        out: List[Dict[str, Any]] = []
        for tid in ids:
            t = self.get(tid)
            if t:
                out.append(t)
        return out

    def get_push(self, task_id: str) -> Optional[Dict[str, Any]]:
        raw = self._r.get(self._push_key(task_id))
        return json.loads(raw) if raw else None

    def set_push(self, task_id: str, config: Dict[str, Any]) -> None:
        ttl = int(os.getenv("A2A_TASK_TTL_SECONDS", str(7 * 24 * 3600)))
        self._r.set(self._push_key(task_id), json.dumps(config, default=str), ex=ttl)

    def delete_push(self, task_id: str) -> None:
        self._r.delete(self._push_key(task_id))

    def list_push(self) -> List[Dict[str, Any]]:
        keys = self._r.keys(f"{self._prefix}:push:*")
        out = []
        for k in keys:
            raw = self._r.get(k)
            if raw:
                out.append(json.loads(raw))
        return out


_store_instance: Optional[TaskStore] = None
_store_lock = threading.Lock()


def get_task_store() -> TaskStore:
    """Return the singleton TaskStore selected by A2A_TASK_STORE."""
    global _store_instance
    with _store_lock:
        if _store_instance is not None:
            return _store_instance

        mode = (os.getenv("A2A_TASK_STORE") or "memory").strip().lower()
        env = (os.getenv("FLASK_ENV") or "production").strip().lower()
        is_prod = env not in {"development", "dev", "test", "testing", "local"}

        if mode == "redis":
            try:
                _store_instance = RedisTaskStore()
                logger.info("A2A TaskStore: redis")
                return _store_instance
            except Exception as err:
                logger.error("RedisTaskStore failed to init: %s — falling back to sqlite", err)
                mode = "sqlite"

        if mode == "sqlite":
            try:
                _store_instance = SqliteTaskStore()
                logger.info("A2A TaskStore: sqlite (PersistentStore)")
                return _store_instance
            except Exception as err:
                logger.error("SqliteTaskStore failed: %s — falling back to memory", err)
                mode = "memory"

        if is_prod and mode == "memory":
            logger.error(
                "A2A task store is in-memory (non-persistent). "
                "Set A2A_TASK_STORE=sqlite (or redis + REDIS_URL) for production. "
                "Tasks will be lost on restart and are not shared across workers."
            )
        _store_instance = MemoryTaskStore()
        logger.info("A2A TaskStore: memory")
        return _store_instance
