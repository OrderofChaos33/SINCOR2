"""Discovery presence surface on the legacy factory app.

Regression guard: sincor2.app.create_app() (the factory behind the standard
tests/pytest conftest fixtures) must serve /llms.txt, /ai.txt, /robots.txt
and /.well-known/security.txt. Registering the blueprint only on mvp_app
left them 404ing on the factory client, so builder "all checks pass" claims
only held on the mvp_app client.
"""


DISCOVERY_ROUTES = [
    "/llms.txt",
    "/ai.txt",
    "/robots.txt",
    "/.well-known/security.txt",
]


def test_discovery_routes_200_on_factory_client(client):
    for route in DISCOVERY_ROUTES:
        resp = client.get(route)
        assert resp.status_code == 200, f"{route} returned {resp.status_code}"


def test_discovery_routes_serve_text_plain(client):
    for route in DISCOVERY_ROUTES:
        resp = client.get(route)
        content_type = resp.headers.get("Content-Type", "")
        assert content_type.startswith("text/plain"), (
            f"{route} served as {content_type!r}, expected text/plain"
        )
        # Single charset declaration, not the doubled
        # "text/plain; charset=utf-8; charset=utf-8" bug.
        assert content_type.count("charset=") <= 1, (
            f"{route} has doubled charset: {content_type!r}"
        )


def test_discovery_bodies_nonempty(client):
    for route in DISCOVERY_ROUTES:
        body = client.get(route).get_data(as_text=True)
        assert body.strip(), f"{route} returned an empty body"
