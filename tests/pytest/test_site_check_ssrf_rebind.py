"""W-108 regression tests: SSRF DNS-rebinding TOCTOU in site_check.

The static validator resolves a hostname and checks the answers, but
the transport (requests/urllib3) resolves the hostname AGAIN at
connect time. With attacker-controlled DNS (TTL 0) the validation
answer can be public while the connect-time answer is loopback.

These tests rig resolution to flip public -> loopback between the
validation resolution and the connect-time resolution, and prove —
through a fake transport written in the existing test style (it
performs the connect-time ``socket.getaddrinfo`` itself, exactly as
urllib3 does, and only "connects" to the address that resolution
returns) — that:

- the direct fetch fails closed and never connects to loopback;
- the full serve path fails closed and never delivers loopback
  content;
- a peer mismatch on a REDIRECT hop is likewise refused;
- legitimate public fetches (direct and via redirect) still succeed,
  with certificate verification never disabled;
- an unresolvable-at-validation name fails closed with no transport
  call at all (there is no validated address set to connect against).

Hermetic: no network, DNS and transport are stubbed per test.
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

import pytest

import sincor2.site_check as sc

PUBLIC_IP = "93.184.216.34"
PUBLIC_IP_2 = "93.184.216.35"
LOOPBACK_IP = "127.0.0.1"

GOOD_HTML = """<!doctype html><html><head>
<title>Acme Dental</title>
<meta name="description" content="Family dental care.">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head><body><h1>Acme Dental</h1>
<a href="tel:+15551234567">Call us</a>
</body></html>"""


class _FakeResponse:
    def __init__(self, status_code, url, text="", headers=None):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.headers = headers or {}


def _addrinfo(ip, port):
    return [(socket.AF_INET, socket.SOCK_STREAM,
             socket.IPPROTO_TCP, "", (ip, port or 0))]


def _flip_dns(host, public_ip, private_ip, flip_after):
    """getaddrinfo rig for one host: the first ``flip_after``
    resolutions return ``public_ip``; every later resolution returns
    ``private_ip`` — the TTL-0 rebinding flip. Other hosts fail."""
    calls = {"n": 0}

    def _resolve(h, port, *args, **kwargs):
        if h != host:
            raise socket.gaierror(f"rigged DNS: unexpected host {h}")
        calls["n"] += 1
        ip = public_ip if calls["n"] <= flip_after else private_ip
        return _addrinfo(ip, port)

    _resolve.calls = calls
    return _resolve


def _per_host_flip_dns(plan):
    """getaddrinfo rig keyed by host. ``plan`` maps host -> list of
    IPs; the n-th resolution of a host returns plan[min(n, len-1)]
    (0-based), so ``[public, loopback]`` flips on the second —
    connect-time — resolution of that host."""
    counts = {}

    def _resolve(h, port, *args, **kwargs):
        if h not in plan:
            raise socket.gaierror(f"rigged DNS: unexpected host {h}")
        n = counts.get(h, 0)
        counts[h] = n + 1
        seq = plan[h]
        return _addrinfo(seq[min(n, len(seq) - 1)], port)

    return _resolve


def _connecting_transport(routes, connected, requests_seen=None):
    """A requests.get stand-in in the existing test style, except it
    simulates urllib3's connect-time resolution: it resolves the
    URL's host via socket.getaddrinfo AT CALL TIME and records the
    address it would connect to before returning the routed page.
    If resolution raises (the SSRF guard failing it closed), no
    connection is recorded and the error propagates, exactly as a
    requests ConnectionError would surface from the real stack."""

    def _fake_get(url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        if requests_seen is not None:
            requests_seen.append((url, dict(kwargs)))
        host = urlparse(url).hostname
        port = urlparse(url).port or (
            443 if urlparse(url).scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port,
                                   proto=socket.IPPROTO_TCP)
        connected.append((url, infos[0][4][0]))
        return routes(url)

    return _fake_get


class TestDNSRebindingDirectFetch:
    def test_rebind_flip_at_connect_fails_closed(self, monkeypatch):
        # Validation resolution (#1) sees the public IP; the
        # connect-time resolution (#2) flips to loopback.
        rig = _flip_dns("rebind.example.com", PUBLIC_IP, LOOPBACK_IP,
                        flip_after=1)
        monkeypatch.setattr(socket, "getaddrinfo", rig)
        connected = []
        monkeypatch.setattr(
            "requests.get",
            _connecting_transport(
                lambda url: _FakeResponse(200, url, text=GOOD_HTML),
                connected))

        page = sc.fetch_site_page("http://rebind.example.com/")

        # Fails closed: no page, and the loopback peer was never
        # connected to (resolution was refused before any connect).
        assert page is None
        assert connected == []
        assert all(ip != LOOPBACK_IP for _url, ip in connected)
        # The resolution really did flip — the guard, not luck,
        # stopped the fetch.
        assert rig.calls["n"] >= 2
        # The guard restored the (rigged) resolver on exit.
        assert socket.getaddrinfo is rig

    def test_rebind_via_full_serve_path_fails_closed(self, monkeypatch):
        # validate_payload resolves (#1, public), the fetcher's
        # re-validation resolves (#2, public), the connect-time
        # resolution (#3) flips to loopback.
        rig = _flip_dns("rebind.example.com", PUBLIC_IP, LOOPBACK_IP,
                        flip_after=2)
        monkeypatch.setattr(socket, "getaddrinfo", rig)
        connected = []
        monkeypatch.setattr(
            "requests.get",
            _connecting_transport(
                lambda url: _FakeResponse(200, url, text=GOOD_HTML),
                connected))

        status, body = sc.serve_site_check(
            {"url": "http://rebind.example.com/"})

        # No loopback content is delivered as a verdict: the fetch
        # failed closed, so the honest verdict is "unknown".
        assert status == 200, body
        assert body["verdict"] == "unknown"
        assert body["reason"] == "site_unreachable"
        assert connected == []
        assert rig.calls["n"] >= 3


class TestDNSRebindingRedirectHop:
    def test_peer_mismatch_on_redirect_hop_refused(self, monkeypatch):
        plan = {
            # Initial host: stable public resolution.
            "start.example.com": [PUBLIC_IP],
            # Redirect target: validation resolves public, the
            # connect-time resolution flips to loopback.
            "hop.example.com": [PUBLIC_IP_2, LOOPBACK_IP],
        }
        monkeypatch.setattr(socket, "getaddrinfo",
                            _per_host_flip_dns(plan))
        connected = []

        def _routes(url):
            if url == "http://start.example.com/":
                return _FakeResponse(
                    302, url, headers={
                        "Location": "http://hop.example.com/landing"})
            return _FakeResponse(200, url, text=GOOD_HTML)

        monkeypatch.setattr(
            "requests.get", _connecting_transport(_routes, connected))

        page = sc.fetch_site_page("http://start.example.com/")

        assert page is None
        # The first (public, validated) hop connected fine; the
        # rebound redirect hop never connected at all.
        assert connected == [("http://start.example.com/", PUBLIC_IP)]


class TestLegitimateFetchStillWorks:
    def test_consistent_public_resolution_succeeds(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo",
            _per_host_flip_dns({"shop.example.com": [PUBLIC_IP]}))
        connected = []
        seen = []
        monkeypatch.setattr(
            "requests.get",
            _connecting_transport(
                lambda url: _FakeResponse(200, url, text=GOOD_HTML),
                connected, seen))

        page = sc.fetch_site_page("https://shop.example.com/")

        assert page is not None
        assert page.status_code == 200
        assert "Acme Dental" in page.html
        assert connected == [("https://shop.example.com/", PUBLIC_IP)]
        # Certificate verification must never be the shortcut: the
        # transport call must not disable it.
        assert seen and seen[0][1].get("verify", True) is not False
        assert seen[0][1].get("allow_redirects") is False

    def test_public_redirect_with_consistent_dns_succeeds(
            self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo",
            _per_host_flip_dns({"shop.example.com": [PUBLIC_IP]}))
        connected = []

        def _routes(url):
            if url == "https://shop.example.com/":
                return _FakeResponse(
                    302, url, headers={"Location": "/landing"})
            return _FakeResponse(200, url, text=GOOD_HTML)

        monkeypatch.setattr(
            "requests.get", _connecting_transport(_routes, connected))

        status, body = sc.serve_site_check(
            {"url": "https://shop.example.com/"})

        assert status == 200, body
        assert body["verdict"] == "no_build_needed"
        assert body["observations"]["final_url"] == \
            "https://shop.example.com/landing"
        assert connected == [
            ("https://shop.example.com/", PUBLIC_IP),
            ("https://shop.example.com/landing", PUBLIC_IP),
        ]


class TestUnresolvableFailsClosed:
    def test_unresolvable_name_never_reaches_transport(
            self, monkeypatch):
        def _no_dns(h, port, *args, **kwargs):
            raise socket.gaierror("rigged DNS: name does not resolve")

        monkeypatch.setattr(socket, "getaddrinfo", _no_dns)

        def _fake_get(url, **kwargs):
            raise AssertionError(
                "transport must not run without a validated "
                "address set to connect against")

        monkeypatch.setattr("requests.get", _fake_get)
        assert sc.fetch_site_page("http://gone.example.com/") is None
