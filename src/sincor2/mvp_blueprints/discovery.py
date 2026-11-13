"""Agent-presence discovery files: /llms.txt, /ai.txt, /robots.txt, /.well-known/security.txt.

Machine-readable agent-discoverability surface for outside AI agents.
Content lives in static/ (text files) so it is editable without code
changes; these routes serve them at their canonical root paths with
correct Content-Type headers.
"""
from __future__ import annotations

import os

from flask import Blueprint, Response, current_app

bp = Blueprint("discovery", __name__)

# discovery.py sits at src/sincor2/mvp_blueprints/discovery.py — four levels up
# is the repo root. The legacy sincor2.app factory has no static dir of its
# own (its static_folder defaults to src/sincor2/static, which does not
# exist), while mvp_app points static_folder at the repo-root static/ tree.
# Resolve against the app first and fall back to the repo tree so these
# routes serve on both factories.
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_REPO_STATIC = os.path.join(_PROJECT_ROOT, "static")


def _repo_doc_candidates(relpath: str):
    """Candidate paths for a repo doc (``docs/<relpath>``) across layouts.

    The repo/test layout puts docs/ at the repo root, four dirname steps
    above this module (``_PROJECT_ROOT``). Deployed layouts break that
    fixed depth: the Docker image flattens the tree under /app, and an
    installed package can sit in site-packages while the process working
    directory is the app root. ``_PROJECT_ROOT`` alone resolved to a root
    with no docs/ tree in production and the doc routes 404'd. Walk every
    ancestor of this module, then the import-time project root, then the
    working directory, and serve the first hit. ``relpath`` is only ever
    a hardcoded literal from the routes below, never user input.
    """
    parts = relpath.split("/")
    roots = []
    cur = os.path.dirname(os.path.abspath(__file__))
    while True:
        roots.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    roots.append(_PROJECT_ROOT)
    roots.append(os.getcwd())
    seen = set()
    for root in roots:
        if root not in seen:
            seen.add(root)
            yield os.path.join(root, "docs", *parts)


def _serve_repo_doc(relpath: str) -> Response:
    for path in _repo_doc_candidates(relpath):
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                return Response(fh.read(), mimetype="text/markdown")
    from werkzeug.exceptions import NotFound

    raise NotFound(f"repo doc not found: {relpath}")


def _serve_static_text(relpath: str, mimetype: str) -> Response:
    candidates = [
        os.path.join(current_app.static_folder, relpath),
        os.path.join(_REPO_STATIC, relpath),
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                body = fh.read()
            return Response(body, mimetype=mimetype)
    from werkzeug.exceptions import NotFound

    raise NotFound(f"discovery file not found: {relpath}")


def _serve_static_binary(relpath: str, mimetype: str) -> Response:
    candidates = [
        os.path.join(current_app.static_folder, relpath),
        os.path.join(_REPO_STATIC, relpath),
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                body = fh.read()
            resp = Response(body, mimetype=mimetype)
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp
    from werkzeug.exceptions import NotFound

    raise NotFound(f"discovery file not found: {relpath}")


@bp.route("/favicon.ico", methods=["GET"])
def favicon_ico():
    # Browsers request /favicon.ico at the root regardless of the <link>
    # tags; serve the hornet brand asset so the request never 404s.
    return _serve_static_binary("sincor_hornet_logo.jpg", "image/jpeg")


@bp.route("/llms.txt", methods=["GET"])
def llms_txt():
    return _serve_static_text("llms.txt", "text/plain")


@bp.route("/ai.txt", methods=["GET"])
def ai_txt():
    return _serve_static_text("ai.txt", "text/plain")


@bp.route("/robots.txt", methods=["GET"])
def robots_txt():
    return _serve_static_text("robots.txt", "text/plain")


@bp.route("/.well-known/security.txt", methods=["GET"])
def security_txt():
    return _serve_static_text(".well-known/security.txt", "text/plain")


@bp.route("/docs/TRUST_STACK.md", methods=["GET"])
def trust_stack_doc():
    # static/.well-known/trust-stack.json names this exact URL as its
    # "doc"; the content lives in the repo at docs/TRUST_STACK.md but no
    # route served it, so the manifest pointed at a 404. Serve the file.
    return _serve_repo_doc("TRUST_STACK.md")


@bp.route("/docs/architecture/AUCTION_GROUND_TRUTH.md", methods=["GET"])
def auction_ground_truth_doc():
    # Machine surfaces name this spec as the canonical auction invariants
    # (a2a_inbound_market.py, mcp_server.py, auction_bridge.py, and the
    # A2A API's "invariants" field all cite
    # docs/architecture/AUCTION_GROUND_TRUTH.md), but no route served it.
    return _serve_repo_doc("architecture/AUCTION_GROUND_TRUTH.md")
