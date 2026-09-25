"""Repo hygiene: agent configs, integrity metadata, and token constants.

Turns documentation claims into tests so the repo cannot silently drift:
- every agents/*.yaml carries a real, current constitution_sha256
- no fake `hash_E-...` placeholder values remain
- token constants match TOKEN_CANON.json (single source of truth)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sincor2.onchain.constants import AXIOM_TOKEN, SINC_TOKEN  # noqa: E402

AGENTS = ROOT / "agents"
REQUIRED_FIELDS = {"name", "id", "archetype", "constitution_refs", "constitution_sha256"}
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def agent_files() -> list[Path]:
    return sorted(AGENTS.glob("*.yaml"))


def test_agent_dir_not_empty():
    assert agent_files(), "agents/ has no agent configs"


def test_agent_configs_have_required_fields():
    missing = []
    for path in agent_files():
        data = yaml.safe_load(path.read_text()) or {}
        absent = REQUIRED_FIELDS - set(data.keys())
        if absent:
            missing.append(f"{path.name}: missing {sorted(absent)}")
    assert not missing, "agent configs missing required fields:\n" + "\n".join(missing)


def test_constitution_hashes_are_real_and_current(tmp_path=None):
    import hashlib

    stale, malformed = [], []
    for path in agent_files():
        data = yaml.safe_load(path.read_text()) or {}
        field = str(data.get("constitution_sha256", ""))
        if not SHA_RE.match(field):
            malformed.append(f"{path.name}: {field[:40]}")
            continue
        h = hashlib.sha256()
        for ref in data["constitution_refs"]:
            h.update((ROOT / ref).read_bytes())
        if field != f"sha256:{h.hexdigest()}":
            stale.append(path.name)
    assert not malformed, "non-sha256 constitution hashes:\n" + "\n".join(malformed)
    assert not stale, "stale constitution hashes (docs changed?):\n" + "\n".join(stale)


def test_no_fake_hash_placeholders_remain():
    hits = []
    for path in agent_files():
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if "hash_E-" in line:
                hits.append(f"{path.name}:{i}")
    assert not hits, "fake hash_E- placeholders remain:\n" + "\n".join(hits)


def test_token_constants_match_canon():
    canon = json.loads((ROOT / "TOKEN_CANON.json").read_text())
    # TOKEN_CANON.json is the human/machine spec; runtime constants must agree.
    for key, runtime in (("sinc", SINC_TOKEN), ("axiom", AXIOM_TOKEN)):
        expected = canon.get(key, {}).get("address")
        assert expected, f"TOKEN_CANON.json missing {key}.address"
        assert runtime.lower() == expected.lower(), f"{key} constant {runtime} != canon {expected}"
