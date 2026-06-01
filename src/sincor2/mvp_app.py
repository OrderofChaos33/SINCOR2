"""Canonical WSGI entrypoint for SINCOR2."""

from __future__ import annotations

from sincor2.app import app, create_app

__all__ = ["app", "create_app"]
