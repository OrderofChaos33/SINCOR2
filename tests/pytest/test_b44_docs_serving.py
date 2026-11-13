"""Base44 launch-check follow-up: /docs/*.md serving must work in production.

PR #626 added a route for /docs/TRUST_STACK.md and it passes the existing
suite, yet the live site 404s it (and /docs/architecture/
AUCTION_GROUND_TRUTH.md, which machine surfaces reference). Two defects:

1. discovery.py resolved the doc from a single import-time constant,
   ``_PROJECT_ROOT`` = exactly four ``dirname()`` steps from ``__file__``.
   That is only right for the repo/test layout; any deployed layout where
   the code sits at a different depth (flattened copy, installed package)
   resolves to a root that has no docs/ tree and the route 404s.
2. The Docker image never contained the docs at all: .dockerignore
   excluded ``docs/`` wholesale, and the Dockerfile's ``COPY . .`` is the
   production tree — so on Railway there was no file for any resolver to
   find. The packaging must re-include exactly the served docs.

These tests pin both halves: HTTP serving in the repo layout, resolution
from a simulated alternate (production-style) root, a real 404 for a
genuinely missing doc, and the .dockerignore rules for the served paths.
"""
from __future__ import annotations

import fnmatch
import os
import re

import pytest

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
    from sincor2.mvp_app import app

    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = None
    return app.test_client()


@pytest.fixture
def discovery_client():
    from flask import Flask

    from sincor2.mvp_blueprints import discovery

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(discovery.bp)
    return app.test_client(), discovery


def _repo_doc(relpath: str) -> str:
    with open(os.path.join(_REPO_ROOT, "docs", *relpath.split("/")),
              encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Repo layout: both docs serve over HTTP with the repo file's exact content
# ---------------------------------------------------------------------------

def test_trust_stack_doc_serves_repo_content(mvp_client):
    r = mvp_client.get("/docs/TRUST_STACK.md")
    assert r.status_code == 200
    assert r.get_data(as_text=True) == _repo_doc("TRUST_STACK.md")


def test_auction_ground_truth_doc_serves_repo_content(mvp_client):
    r = mvp_client.get("/docs/architecture/AUCTION_GROUND_TRUTH.md")
    assert r.status_code == 200
    assert r.get_data(as_text=True) == _repo_doc(
        "architecture/AUCTION_GROUND_TRUTH.md")


# ---------------------------------------------------------------------------
# Production-style layout: code at a different depth than the docs root.
# The import-time _PROJECT_ROOT constant points at an empty tree (what a
# wrong-depth deploy computes); resolution must still find the docs by
# walking from the module's actual location.
# ---------------------------------------------------------------------------

def test_docs_resolve_from_alternate_root(discovery_client, tmp_path,
                                          monkeypatch):
    client, discovery = discovery_client
    deploy_root = tmp_path / "srv" / "app"
    (deploy_root / "docs" / "architecture").mkdir(parents=True)
    (deploy_root / "docs" / "TRUST_STACK.md").write_text(
        "SENTINEL TRUST STACK\n", encoding="utf-8")
    (deploy_root / "docs" / "architecture" / "AUCTION_GROUND_TRUTH.md"
     ).write_text("SENTINEL AUCTION GROUND TRUTH\n", encoding="utf-8")
    # Module flattened to <root>/pkg/discovery.py — a depth the old fixed
    # four-dirname computation overshoots (it lands above the root).
    fake_module = deploy_root / "pkg" / "discovery.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("# deployed copy\n", encoding="utf-8")
    monkeypatch.setattr(discovery, "__file__", str(fake_module))
    empty = tmp_path / "empty-root"
    empty.mkdir()
    monkeypatch.setattr(discovery, "_PROJECT_ROOT", str(empty))

    r = client.get("/docs/TRUST_STACK.md")
    assert r.status_code == 200
    assert r.get_data(as_text=True) == "SENTINEL TRUST STACK\n"

    r = client.get("/docs/architecture/AUCTION_GROUND_TRUTH.md")
    assert r.status_code == 200
    assert r.get_data(as_text=True) == "SENTINEL AUCTION GROUND TRUTH\n"


def test_missing_doc_still_404s_when_no_root_has_it(discovery_client,
                                                    tmp_path, monkeypatch):
    client, discovery = discovery_client
    bare = tmp_path / "bare"
    (bare / "pkg").mkdir(parents=True)
    fake_module = bare / "pkg" / "discovery.py"
    fake_module.write_text("# deployed copy\n", encoding="utf-8")
    monkeypatch.setattr(discovery, "__file__", str(fake_module))
    monkeypatch.setattr(discovery, "_PROJECT_ROOT", str(bare))
    monkeypatch.chdir(bare)

    # The resolver consults more roots than the two patched attributes:
    # _repo_doc_candidates walks EVERY ancestor of the module's location
    # (here: bare/pkg, bare, then tmp_path, the pytest basetemp chain,
    # $TMPDIR, the home directory, up to /), then _PROJECT_ROOT, then the
    # cwd. The patched attributes and the chdir control the last two, and
    # the in-tree ancestors are empty by construction — but the walk's
    # out-of-tree roots are shared filesystem this test cannot clean. A
    # docs/TRUST_STACK.md dropped into any of them (by another test, a
    # parallel run, or leftover state) makes the "missing" doc resolve,
    # and this test failed in full-suite context only (200, not 404)
    # while passing standalone. Close those roots: enumerate the
    # resolver's own candidates under the patched state and deny
    # existence for exactly the candidates outside the controlled tree.
    # In-tree candidates and every other path still go through the real
    # isfile, the resolver's walk/order/NotFound logic runs unchanged,
    # and the 404 below is asserted at full strength.
    controlled = os.path.normpath(str(bare)) + os.sep
    outside = {
        os.path.normpath(candidate)
        for candidate in discovery._repo_doc_candidates("TRUST_STACK.md")
        if not os.path.normpath(candidate).startswith(controlled)
    }
    real_isfile = os.path.isfile

    def _guarded_isfile(path):
        try:
            key = os.path.normpath(os.fspath(path))
        except TypeError:
            return real_isfile(path)
        if key in outside:
            return False
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", _guarded_isfile)

    r = client.get("/docs/TRUST_STACK.md")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Packaging: the production image (Dockerfile COPY . . under .dockerignore)
# must actually contain the served docs.
# ---------------------------------------------------------------------------

def _pattern_matches(pattern: str, path: str) -> bool:
    """Path-aware fnmatch ('*' does not cross '/'), dockerignore-style."""
    regex = ""
    for ch in pattern:
        if ch == "*":
            regex += "[^/]*"
        elif ch == "?":
            regex += "[^/]"
        else:
            regex += re.escape(ch)
    return re.match("^" + regex + "$", path) is not None


def _dockerignore_excludes(relpath: str) -> bool:
    """Last matching pattern wins; a pattern matching any ancestor
    directory of the path applies to the path itself."""
    with open(os.path.join(_REPO_ROOT, ".dockerignore"),
              encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh]
    parts = relpath.split("/")
    ancestors = ["/".join(parts[:i]) for i in range(1, len(parts))]
    excluded = False
    for line in lines:
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:] if negate else line
        pattern = pattern.rstrip("/")
        if _pattern_matches(pattern, relpath) or any(
                _pattern_matches(pattern, a) for a in ancestors):
            excluded = not negate
    return excluded


def test_dockerignore_does_not_exclude_served_docs():
    assert not _dockerignore_excludes("docs/TRUST_STACK.md")
    assert not _dockerignore_excludes(
        "docs/architecture/AUCTION_GROUND_TRUTH.md")
    # The point of the docs/ exclusion stands: unserved docs stay out.
    assert _dockerignore_excludes("docs/architecture/MONEY_FLOW.md")
