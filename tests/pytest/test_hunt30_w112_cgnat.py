"""W-112 regression tests: SSRF validator allowed CGNAT 100.64.0.0/10.

Hunt 30 found that ``site_check._ip_is_forbidden`` relied solely on
the ``ipaddress`` classification flags (private / loopback /
link-local / multicast / reserved / unspecified). For the RFC 6598
shared address space (CGNAT, 100.64.0.0/10) every one of those flags
is False — the range is neither private nor global — so a target
like ``http://100.64.0.1/`` passed validation and would be fetched.
On container/cloud networks where 100.64/10 carries internal
services, that is a live SSRF target class the validator claimed to
exclude. Its IPv6-mapped form (``::ffff:100.64.0.1``) was already
blocked incidentally via ``is_reserved``; the plain IPv4 form was
not.

These tests pin:

- every address inside 100.64.0.0/10 is forbidden, boundaries
  included (100.64.0.0 and 100.127.255.255);
- the addresses just outside the range (100.63.255.255,
  100.128.0.0) and ordinary public addresses (8.8.8.8) stay
  allowed — the fix must not over-block;
- the IPv4-mapped IPv6 form of a CGNAT address is forbidden;
- ``_target_error`` / ``_resolve_target`` refuse CGNAT literal
  targets with ``invalid_target_host`` and an empty validated set,
  while a just-outside literal still validates;
- the serve-level entry point rejects a CGNAT URL with a 400
  ``invalid_target_host`` before any fetch is attempted;
- every previously blocked class (loopback, RFC 1918, link-local /
  cloud metadata, IPv6 loopback) stays blocked.

Hermetic: literal IPs only — ``_resolve_target`` performs no DNS
for IP literals, and the serve test never reaches the fetch seam.
"""
from __future__ import annotations

import ipaddress

import pytest

import sincor2.site_check as sc
from sincor2.site_check import (
    _ip_is_forbidden,
    _resolve_target,
    _target_error,
    serve_site_check,
    validate_payload,
)

CGNAT_FORBIDDEN = [
    "100.64.0.0",        # network address of 100.64.0.0/10
    "100.64.0.1",        # the hunt-30 PoC address
    "100.100.50.25",     # mid-range
    "100.127.255.255",   # broadcast address of 100.64.0.0/10
]

JUST_OUTSIDE_ALLOWED = [
    "100.63.255.255",    # one below the range
    "100.128.0.0",       # one above the range
]


@pytest.mark.parametrize("addr", CGNAT_FORBIDDEN)
def test_cgnat_addresses_are_forbidden(addr):
    assert _ip_is_forbidden(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize("addr", JUST_OUTSIDE_ALLOWED)
def test_addresses_just_outside_cgnat_stay_allowed(addr):
    assert _ip_is_forbidden(ipaddress.ip_address(addr)) is False


def test_public_address_stays_allowed():
    assert _ip_is_forbidden(ipaddress.ip_address("8.8.8.8")) is False


def test_ipv4_mapped_cgnat_is_forbidden():
    assert _ip_is_forbidden(ipaddress.ip_address("::ffff:100.64.0.1")) \
        is True


@pytest.mark.parametrize("addr", [
    "127.0.0.1",         # loopback
    "10.0.0.5",          # RFC 1918
    "172.16.0.5",        # RFC 1918
    "192.168.1.1",       # RFC 1918
    "169.254.169.254",   # link-local / cloud metadata
    "::1",               # IPv6 loopback
    "0.0.0.0",           # unspecified
])
def test_previously_blocked_classes_stay_blocked(addr):
    assert _ip_is_forbidden(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize("url", [
    "http://100.64.0.1/",
    "http://100.127.0.9/",
    "https://100.100.50.25/",
])
def test_target_error_rejects_cgnat_literals(url):
    assert _target_error(url) == "invalid_target_host"


def test_target_error_allows_just_outside_cgnat():
    assert _target_error("http://100.128.0.1/") is None
    assert _target_error("http://100.63.255.255/") is None


def test_target_error_rejects_ipv4_mapped_cgnat_literal():
    assert _target_error("http://[::ffff:100.64.0.1]/") \
        == "invalid_target_host"


def test_resolve_target_cgnat_yields_no_validated_ips():
    error, validated_ips, host = _resolve_target("http://100.64.0.1/")
    assert error == "invalid_target_host"
    assert validated_ips == frozenset()
    assert host == "100.64.0.1"


def test_resolve_target_just_outside_cgnat_validates():
    error, validated_ips, host = _resolve_target("http://100.128.0.1/")
    assert error is None
    assert validated_ips == frozenset(
        {ipaddress.ip_address("100.128.0.1")})
    assert host == "100.128.0.1"


def test_validate_payload_rejects_cgnat_url():
    parsed, error = validate_payload({"url": "http://100.64.0.1/"})
    assert parsed is None
    assert error is not None
    status, body = error
    assert status == 400
    assert body["error"] == "invalid_target_host"


def test_serve_rejects_cgnat_before_any_fetch():
    def _exploding_fetch(url):  # pragma: no cover - must never run
        raise AssertionError(f"fetch attempted for forbidden {url}")

    status, body = sc.serve_site_check(
        {"url": "http://100.64.0.1/"}, fetch=_exploding_fetch)
    assert status == 400
    assert body["error"] == "invalid_target_host"
