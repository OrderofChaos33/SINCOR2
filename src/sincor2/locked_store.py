"""Shared locked JSON persistence primitive (persistence research 2026-11-23).

One small primitive for the lost-update family (W-192..W-215): every store
in that family loaded its state once, mutated an in-memory snapshot, and
rewrote the whole file unlocked, so a second instance's save silently
erased the first instance's writes. The cure — already proven in this repo
by ``FileSealRegistry`` (hierarchical_cnp) and
``EscalationStore._write_transaction`` (escalation) — is a discipline, not
a backend: sidecar lock file + ``fcntl.flock`` exclusive + reload state
from disk under the lock + mutate via callback + pid-unique tmp file +
flush/fsync + ``os.replace`` + directory fsync.

Usage::

    store = LockedJsonStore(path, default={}, fail_closed=True)
    store.mutate(lambda state: state.update({"key": value}))
    current = store.read()

``fail_closed=True`` (money/safety stores): any lock or parse failure
raises :class:`LockedStoreError`. ``fail_closed=False`` (observability
stores): a corrupt file degrades to the default and a missing ``fcntl``
degrades loudly (warning log) to the per-instance lock only — mirroring
``EscalationStore`` on non-POSIX platforms. Where ``fcntl`` exists, a lock
file that cannot be opened always raises: there is no safe unlocked path.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Union

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

PathLike = Union[str, Path]
Mutator = Callable[[Any], Optional[Any]]


class LockedStoreError(RuntimeError):
    """A locked store could not lock, read, or persist its state."""


def _serialize(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class LockedJsonStore:
    """A JSON file guarded by flock + reload-under-lock + atomic replace."""

    def __init__(
        self,
        path: PathLike,
        *,
        default: Any = None,
        fail_closed: bool = False,
        serializer: Optional[Callable[[Any], str]] = None,
    ) -> None:
        self._path = Path(path)
        self._lock_path = str(self._path) + ".lock"
        self._default = default
        self._fail_closed = fail_closed
        self._serialize = serializer or _serialize
        self._instance_lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    # -- locking -----------------------------------------------------------

    @contextmanager
    def _locked(self, exclusive: bool = True) -> Iterator[None]:
        with self._instance_lock:
            if fcntl is None:
                if self._fail_closed:
                    raise LockedStoreError(
                        f"fcntl unavailable: cannot lock {self._path} "
                        "(fail-closed store)"
                    )
                log.warning(
                    "fcntl unavailable: %s is running WITHOUT cross-process "
                    "locking (instance lock only)",
                    self._path,
                )
                yield
                return
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            except OSError as exc:
                raise LockedStoreError(
                    f"cannot open lock file {self._lock_path!r}: {exc}"
                ) from exc
            try:
                fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    # -- disk primitives (caller holds the lock) ----------------------------

    def _read_unlocked(self) -> Any:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return copy.deepcopy(self._default)
        except OSError as exc:
            if self._fail_closed:
                raise LockedStoreError(
                    f"cannot read {self._path}: {exc}"
                ) from exc
            log.warning("cannot read %s (%s); using default", self._path, exc)
            return copy.deepcopy(self._default)
        try:
            return json.loads(raw)
        except ValueError as exc:
            if self._fail_closed:
                raise LockedStoreError(
                    f"{self._path} is corrupt (invalid JSON)"
                ) from exc
            log.warning("%s is corrupt (%s); using default", self._path, exc)
            return copy.deepcopy(self._default)

    def _write_unlocked(self, payload: Any) -> None:
        text = self._serialize(payload)
        tmp_path = f"{self._path}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._path)
            dir_fd = os.open(
                os.path.dirname(os.path.abspath(self._path)), os.O_RDONLY
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            raise LockedStoreError(
                f"cannot persist {self._path}: {exc}"
            ) from exc
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass  # already replaced (or never created)

    # -- public API ----------------------------------------------------------

    def read(self) -> Any:
        """Return the current on-disk state (shared lock), or the default."""
        with self._locked(exclusive=False):
            return self._read_unlocked()

    def write(self, payload: Any) -> None:
        """Atomically replace the whole state under the exclusive lock."""
        with self._locked(exclusive=True):
            self._write_unlocked(payload)

    def mutate(self, fn: Mutator) -> Any:
        """Reload under the exclusive lock, apply ``fn``, persist, return state.

        ``fn`` receives the freshly reloaded state. It may mutate it in
        place and return ``None``, or return the replacement state. If
        ``fn`` raises, nothing is written.
        """
        with self._locked(exclusive=True):
            state = self._read_unlocked()
            result = fn(state)
            new_state = state if result is None else result
            self._write_unlocked(new_state)
            return new_state
