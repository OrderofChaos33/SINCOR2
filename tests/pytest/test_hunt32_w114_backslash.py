"""W-114 regression tests: backslash parser-differential SSRF bypass.

The W-108 guard validates a target with ``urllib.parse.urlparse``,
but the requests/urllib3 transport parses the URL differently: to
urllib3 a backslash is a path separator, while urlparse treats
everything before the last ``@`` as userinfo. So for

    http://127.0.0.1\\@93.184.216.34/

urlparse reports hostname ``93.184.216.34`` (public -> validator
ALLOWS) while the transport dials ``127.0.0.1`` (private). The same
shape works with ``169.254.169.254`` (cloud metadata) and
``localhost`` in the dial position. Second hole: the connect-time
guard (``_guarded_resolution``) only pinned resolutions of the
VALIDATED host — a resolution for any other host passed through
unchecked, which is exactly how the transport's divergent host
escaped.

The fix has two layers, and both are pinned here:

1. Any backslash in the URL authority is rejected as
   ``invalid_target_host`` before validation/fetch, so the
   validator and the transport can never diverge on host parsing.
   (A backslash in the PATH stays legal — it is not authority.)
2. The connect-time guard fails closed on ANY resolution whose
   host is not the validated host, instead of passing it through.

Hermetic: DNS is stubbed per test and transports are fakes in the
existing W-108 test style; the fake transport below extracts the
dial host with ``urllib3.util.parse_url`` — the parser the real
transport actually uses — so the divergence is reproduced
faithfully without any network.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import pytest
from urllib3.util import parse_url as urllib3_parse_url

import sincor2.site_check as sc

PUBLIC_IP = "93.184.216.34"
PUBLIC_IP_2 = "93.184.216.35"
LOOPBACK_IP = "127.0.0.1"
METADATA_IP = "169.254.169.254"

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
    family = (socket.AF_INET6 if ipaddress.ip_address(ip).version == 6
              else socket.AF_INET)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
             (ip, port or 0))]


def _literal_dns(host, privates=None):
    """Hermetic resolver factory: IP literals resolve to themselves;
    the names in ``privates`` resolve to the mapped (private) IP;
    every other name fails. ``privates`` models what an attacker or
    a divergent transport would get for a name — the guard must not
    let it through for a non-validated host regardless."""
    privates = privates or {}

    def _resolve(h, port, *args, **kwargs):
        if isinstance(h, bytes):
            h = h.decode("utf-8")
        h = h.rstrip(".")
        if h in privates:
            return _addrinfo(privates[h], port)
        try:
            ipaddress.ip_address(h)
        except ValueError:
            raise socket.gaierror(f"hermetic DNS: no record for {h!r}")
        return _addrinfo(h, port)

    return _resolve


def _urllib3_transport(routes, connected):
    """requests.get stand-in that determines the dial host the way
    the REAL transport does — via urllib3's own URL parser — then
    resolves that host through (guarded) socket.getaddrinfo at call
    time and records the peer it would connect to."""

    def _fake_get(url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        host = urllib3_parse_url(url).host
        port = urllib3_parse_url(url).port or 80
        infos = socket.getaddrinfo(host, port,
                                   proto=socket.IPPROTO_TCP)
        connected.append((host, infos[0][4][0]))
        return routes(url)

    return _fake_get


def _exploding_fetch(url):
    raise AssertionError("fetch must never run for a rejected target")


#: Backslash-authority URLs whose validator-visible host is public
#: (or absent) while the transport-dialed host is private — plus the
#: mirror divergence (validator sees private, transport dials
#: public), which must also be refused: divergence itself is the bug.
BACKSLASH_AUTHORITY_URLS = [
    # PoC §C shapes: private dial target behind a public decoy.
    "http://127.0.0.1\\@93.184.216.34/",
    "http://169.254.169.254\\@93.184.216.34/latest/meta-data/",
    "http://169.254.169.254\\@93.184.216.34/",
    "http://10.0.0.5\\@93.184.216.34/",
    "http://localhost\\@93.184.216.34/",
    # Userinfo variants: credentials in front change nothing — the
    # first backslash still ends the authority for the transport.
    "http://user@127.0.0.1\\@93.184.216.34/",
    "http://user:pass@169.254.169.254\\@93.184.216.34/",
    "http://user:pass@localhost\\@93.184.216.34/",
    # Mirror divergence: private in the validator's seat, public in
    # the transport's — refused too (authority backslash, period).
    "http://93.184.216.34\\@127.0.0.1/",
    "http://93.184.216.34\\@169.254.169.254/",
]


class TestBackslashAuthorityRejected:
    @pytest.mark.parametrize("url", BACKSLASH_AUTHORITY_URLS)
    def test_target_error_rejects(self, url):
        assert sc._target_error(url) == "invalid_target_host"

    @pytest.mark.parametrize("url", BACKSLASH_AUTHORITY_URLS)
    def test_validate_payload_rejects(self, url):
        parsed, error = sc.validate_payload(
            {"url": url, "business_name": "x"})
        assert parsed is None
        status, body = error
        assert status == 400
        assert body["error"] == "invalid_target_host"

    @pytest.mark.parametrize("url", BACKSLASH_AUTHORITY_URLS)
    def test_serve_rejects_and_fetch_never_runs(self, url):
        status, body = sc.serve_site_check(
            {"url": url, "business_name": "x"}, fetch=_exploding_fetch)
        assert status == 400
        assert body["error"] == "invalid_target_host"

    @pytest.mark.parametrize("url", BACKSLASH_AUTHORITY_URLS)
    def test_fetch_raises_before_any_transport(self, url, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _literal_dns(
            "unused", privates={"localhost": LOOPBACK_IP}))
        connected = []
        monkeypatch.setattr(
            "requests.get",
            _urllib3_transport(
                lambda u: _FakeResponse(200, u, text=GOOD_HTML),
                connected))

        with pytest.raises(sc.InvalidTargetHostError):
            sc.fetch_site_page(url)
        # The transport never ran, so no peer — private or
        # otherwise — was ever dialed.
        assert connected == []


class TestBackslashRedirectHopRejected:
    def test_redirect_to_backslash_authority_refused(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _literal_dns(
            "unused"))
        connected = []

        def _routes(url):
            if url == "http://93.184.216.34/":
                return _FakeResponse(302, url, headers={
                    "Location": "http://127.0.0.1\\@93.184.216.34/"})
            return _FakeResponse(200, url, text=GOOD_HTML)

        monkeypatch.setattr(
            "requests.get", _urllib3_transport(_routes, connected))

        with pytest.raises(sc.InvalidTargetHostError):
            sc.fetch_site_page("http://93.184.216.34/")
        # Only the legitimate first hop connected; the backslash
        # hop was refused at validation, before its transport ran.
        assert connected == [(PUBLIC_IP, PUBLIC_IP)]


class TestGuardFailsClosedOnForeignHost:
    """Layer 2: inside a guarded fetch for one validated host, a
    resolution request for ANY other host must fail closed."""

    def _enter_guard(self, monkeypatch, privates=None):
        monkeypatch.setattr(socket, "getaddrinfo",
                            _literal_dns("unused", privates=privates))
        return sc._guarded_resolution(
            "93.184.216.34",
            frozenset({ipaddress.ip_address(PUBLIC_IP)}))

    def test_validated_host_still_resolves(self, monkeypatch):
        with self._enter_guard(monkeypatch):
            infos = socket.getaddrinfo(PUBLIC_IP, 80,
                                       proto=socket.IPPROTO_TCP)
        assert infos[0][4][0] == PUBLIC_IP

    def test_validated_host_trailing_dot_and_bytes_ok(self, monkeypatch):
        with self._enter_guard(monkeypatch):
            infos = socket.getaddrinfo("93.184.216.34.", 80,
                                       proto=socket.IPPROTO_TCP)
            assert infos[0][4][0] == PUBLIC_IP
            infos = socket.getaddrinfo(b"93.184.216.34", 80,
                                       proto=socket.IPPROTO_TCP)
            assert infos[0][4][0] == PUBLIC_IP

    @pytest.mark.parametrize("foreign", [
        LOOPBACK_IP, METADATA_IP, "10.0.0.5", "192.168.1.1",
        PUBLIC_IP_2,  # public but NOT the validated host
    ])
    def test_foreign_ip_literal_fails_closed(self, monkeypatch,
                                              foreign):
        with self._enter_guard(monkeypatch):
            with pytest.raises(socket.gaierror):
                socket.getaddrinfo(foreign, 80,
                                   proto=socket.IPPROTO_TCP)

    def test_foreign_name_fails_closed_even_when_resolvable(
            self, monkeypatch):
        # localhost WOULD resolve (to loopback) at the stub resolver;
        # the guard must refuse the resolution itself, not pass it
        # through and judge the answer.
        with self._enter_guard(monkeypatch,
                               privates={"localhost": LOOPBACK_IP}):
            with pytest.raises(socket.gaierror):
                socket.getaddrinfo("localhost", 80,
                                   proto=socket.IPPROTO_TCP)

    def test_nonstring_host_fails_closed(self, monkeypatch):
        with self._enter_guard(monkeypatch):
            with pytest.raises(socket.gaierror):
                socket.getaddrinfo(None, 80, proto=socket.IPPROTO_TCP)

    def test_validated_host_wrong_answer_still_refused(
            self, monkeypatch):
        # The pre-existing pin is unchanged: the validated host
        # resolving OUTSIDE its validated set fails closed too.
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda h, p, *a, **k: _addrinfo(PUBLIC_IP_2, p))
        with sc._guarded_resolution(
                "93.184.216.34",
                frozenset({ipaddress.ip_address(PUBLIC_IP)})):
            with pytest.raises(socket.gaierror):
                socket.getaddrinfo(PUBLIC_IP, 80,
                                   proto=socket.IPPROTO_TCP)

    def test_transport_resolving_foreign_host_mid_fetch_fails(
            self, monkeypatch):
        # End-to-end layer 2: the URL itself is a clean public
        # target, but the transport attempts to resolve a private
        # host mid-fetch (the W-114 divergence shape). The fetch
        # must fail closed and connect to nothing.
        monkeypatch.setattr(socket, "getaddrinfo", _literal_dns(
            "unused", privates={"localhost": LOOPBACK_IP}))
        connected = []

        def _divergent_transport(url, **kwargs):
            assert kwargs.get("allow_redirects") is False
            infos = socket.getaddrinfo(LOOPBACK_IP, 80,
                                       proto=socket.IPPROTO_TCP)
            connected.append(infos[0][4][0])
            return _FakeResponse(200, url, text=GOOD_HTML)

        monkeypatch.setattr("requests.get", _divergent_transport)

        page = sc.fetch_site_page("http://93.184.216.34/")
        assert page is None
        assert connected == []


class TestNoOverblock:
    def test_backslash_in_path_is_not_authority(self):
        # A backslash AFTER the authority is path content: WHATWG
        # transports treat it as a path separator on the SAME host,
        # so there is no divergence and no reason to refuse.
        url = "http://93.184.216.34/foo\\bar"
        assert sc._target_error(url) is None
        parsed, error = sc.validate_payload(
            {"url": url, "business_name": "x"})
        assert error is None
        assert parsed["url"] == url

    def test_public_userinfo_url_still_fetches(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _literal_dns(
            "unused"))
        connected = []
        monkeypatch.setattr(
            "requests.get",
            _urllib3_transport(
                lambda u: _FakeResponse(200, u, text=GOOD_HTML),
                connected))

        page = sc.fetch_site_page("http://user@93.184.216.34/")
        assert page is not None
        assert page.status_code == 200
        assert connected == [(PUBLIC_IP, PUBLIC_IP)]
