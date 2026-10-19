"""Agent skill registry integrity (Part B).

Proves the advertised AgentCard skill catalogue is honest:
* every registered skill has a non-empty id/name/description/tags/examples
* no duplicate skill ids
* every registered skill resolves to a real implementation reference
  (dispatch map entry, AgencyKernel task type, or the P24 skill module)
* schemas compile for the full catalogue
* vertical pack skills appended at bootstrap resolve to a vertical agent
"""
from __future__ import annotations

import importlib
import os
import re

import pytest

from sincor2.a2a_integration import (
    SINCOR_SKILLS,
    compile_skill_schemas,
    refresh_skill_schemas,
    skill_implementation_ref,
)

# The 17 catalogue entries DEFINED in a2a_integration.py, captured at this
# module's import time.  Import happens at collection — before any test's
# app fixture boots the platform (whose bootstrap permanently extends the
# global SINCOR_SKILLS list).  Tests that need the pristine base catalogue
# filter on these ids, so they stay green regardless of suite order.
_BASE_IDS_AT_IMPORT = [s.id for s in SINCOR_SKILLS]
assert len(_BASE_IDS_AT_IMPORT) == 17, (
    "a2a_integration.SINCOR_SKILLS changed at import — update the count "
    "guards in this file and the module docstring")


@pytest.fixture()
def base_skills():
    """The 17 catalogue entries defined in the module (pre-bootstrap).

    platform_bootstrap appends vertical pack skills at runtime; those are
    covered by the vertical-resolution tests below, not here.
    """
    by_id = {s.id: s for s in SINCOR_SKILLS}
    return [by_id[i] for i in _BASE_IDS_AT_IMPORT if i in by_id]


def test_skill_ids_unique_and_non_empty(base_skills):
    ids = [s.id for s in base_skills]
    assert all(isinstance(i, str) and i.strip() for i in ids)
    assert len(ids) == len(set(ids)), "duplicate skill ids in registry"


def test_descriptions_names_tags_examples_non_empty(base_skills):
    for s in base_skills:
        assert s.name and s.name.strip(), f"{s.id}: empty name"
        # descriptions must say something real, not a placeholder
        assert s.description and len(s.description.strip()) >= 40, (
            f"{s.id}: description too short/empty: {s.description!r}")
        assert not re.search(r"\b(todo|lorem|placeholder)\b", s.description,
                             re.I), f"{s.id}: placeholder text in description"
        assert s.tags and all(t.strip() for t in s.tags), f"{s.id}: empty tags"
        assert s.examples and all(e.strip() for e in s.examples), (
            f"{s.id}: empty examples")


def test_pricing_fields_sane(base_skills):
    for s in base_skills:
        assert s.axm_price_wei >= 0, f"{s.id}: negative AXM price"
        assert s.sinc_price >= 0, f"{s.id}: negative SINC price"


def test_every_skill_has_implementation_ref(base_skills):
    missing = [s.id for s in base_skills if not skill_implementation_ref(s.id)]
    assert not missing, f"skills with no implementation reference: {missing}"


def test_implementation_refs_resolve(base_skills):
    """Each ref must point at importable code with the named symbol."""
    for s in base_skills:
        ref = skill_implementation_ref(s.id)
        module_name, _, qual = ref.partition(":")
        module = importlib.import_module(module_name)
        if "[" in qual:
            # "module:_MAP_NAME[skill_id]" — the map must contain the skill
            map_name = qual.split("[", 1)[0]
            skill_key = qual.split("[", 1)[1].rstrip("]")
            mapping = getattr(module, map_name)
            assert skill_key in mapping, (
                f"{s.id}: {map_name} has no entry for {skill_key}")
        elif qual:
            assert hasattr(module, qual), f"{s.id}: {ref} does not resolve"


def test_p24_skill_ref_matches_implementing_module(base_skills):
    from sincor2.defi.p24 import skill as p24_skill

    (p24,) = [s for s in base_skills if s.id == p24_skill.SKILL_ID]
    assert p24.description == p24_skill.CATALOG_DESCRIPTION
    assert p24.input_schema == dict(p24_skill.CATALOG_INPUT_SCHEMA)


def test_schemas_compile_for_full_catalogue():
    # raises on any invalid draft-07 subset usage
    compiled = compile_skill_schemas(SINCOR_SKILLS)
    assert len(compiled) == len(SINCOR_SKILLS)
    refresh_skill_schemas()  # must not raise either


def test_catalogue_count_matches_docstring():
    """Guards the module docstring's base-skill count against drift.

    Counts the AgentSkill literals DEFINED in the module source — not the
    runtime list, which the platform bootstrap legitimately extends at app
    boot (so a runtime-length assertion would be order-dependent).
    """
    import sincor2.a2a_integration as integ

    src = open(integ.__file__, encoding="utf-8").read()
    literals = re.findall(r"^    AgentSkill\($", src, re.M)
    assert len(literals) == 17, (
        "SINCOR_SKILLS literal count changed — update the module docstring "
        "count too")
    assert len(_BASE_IDS_AT_IMPORT) == 17


# ---------------------------------------------------------------------------
# vertical pack skills (appended by platform_bootstrap at runtime)
#
# These tests never mutate the global SINCOR_SKILLS: they exercise the real
# _extend_a2a_skills_from_registry against a FAKE registry (quarantine
# behaviour), and check the REAL registry sources (agent_cards.json and the
# verticals loader) for executor resolvability.  Suite-order independent.
# ---------------------------------------------------------------------------


def _fake_registry(records):
    class _R:
        def __init__(self, rs):
            self._rs = rs

        def list_all(self):
            return self._rs

    return _R(records)


def _with_temp_skills():
    """Context manager: snapshot and restore the global SINCOR_SKILLS."""
    from contextlib import contextmanager

    from sincor2 import a2a_integration as integ

    @contextmanager
    def _cm():
        before = list(integ.SINCOR_SKILLS)
        try:
            yield integ
        finally:
            integ.SINCOR_SKILLS[:] = before
            integ.refresh_skill_schemas()

    return _cm()


def test_bootstrap_extends_with_described_vertical_skills():
    from types import SimpleNamespace

    from sincor2.platform_bootstrap import _extend_a2a_skills_from_registry

    records = [SimpleNamespace(skills=[
        {"id": "real-skill-x", "name": "Real X",
         "description": "A real described vertical skill for the catalogue.",
         "tags": ["vertical"], "examples": ["Do X."]},
    ])]
    with _with_temp_skills() as integ:
        before = {s.id for s in integ.SINCOR_SKILLS}
        _extend_a2a_skills_from_registry(_fake_registry(records))
        appended = [s for s in integ.SINCOR_SKILLS if s.id not in before]
    assert [s.id for s in appended] == ["real-skill-x"]
    assert appended[0].description.strip()


def test_bootstrap_quarantines_description_less_fixture_skills():
    """Test-fixture records (external-agent/testagent) must never reach the
    advertised AgentCard catalogue — the quarantine is by rule (empty
    description), not by hardcoded id."""
    from types import SimpleNamespace

    from sincor2.platform_bootstrap import _extend_a2a_skills_from_registry

    records = [SimpleNamespace(skills=[
        {"id": "ext-skill", "name": "ext", "description": "",
         "tags": [], "examples": []},
        {"id": "s1", "name": "s1", "description": None,
         "tags": [], "examples": []},
    ])]
    with _with_temp_skills() as integ:
        _extend_a2a_skills_from_registry(_fake_registry(records))
        ids = {s.id for s in integ.SINCOR_SKILLS}
    assert "ext-skill" not in ids
    assert "s1" not in ids


def _described_registry_skills():
    """(skill_id, source) for every described skill in the real registry
    sources — the repo's agent_cards.json AND the vertical packs'
    agent_card.json files that platform_bootstrap actually loads."""
    from verticals.loader import load_agent_cards

    out = []
    repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "..")
    from marketplace.registry import AgentCardRegistry

    file_registry = AgentCardRegistry(
        storage_path=os.path.join(repo_root, "marketplace", "agent_cards.json"))
    for record in file_registry.list_all():
        for skill in record.skills:
            if (skill.get("description") or "").strip():
                out.append((skill.get("id"), "marketplace/agent_cards.json"))
    for card in load_agent_cards():
        for skill in card.get("skills", []):
            if (skill.get("description") or "").strip():
                out.append((skill.get("id"), "verticals/*/agent_card.json"))
    return out


def test_advertised_vertical_skills_resolve_to_an_executor():
    """Every described registry skill must be executable: a dispatch-map /
    kernel-map entry (skill_implementation_ref) or a vertical agent via
    SKILL_VERTICAL_MAP.  Skills failing this are advertised but dead."""
    from verticals.loader import SKILL_VERTICAL_MAP

    from sincor2.a2a_integration import skill_implementation_ref as ref

    unresolvable = []
    for sid, source in _described_registry_skills():
        if ref(sid) is None and SKILL_VERTICAL_MAP.get(sid) is None:
            unresolvable.append(f"{sid} ({source})")
    assert not unresolvable, f"unexecutable advertised skills: {unresolvable}"


def test_registry_vertical_skills_have_non_empty_descriptions():
    """Live packs must describe their skills (description-less fixtures are
    quarantined by the bootstrap, asserted above — not fixed here)."""
    from marketplace.registry import AgentCardRegistry

    repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "..")
    registry = AgentCardRegistry(
        storage_path=os.path.join(repo_root, "marketplace", "agent_cards.json"))
    for record in registry.list_all():
        aid = getattr(record, "agent_id", "?")
        for skill in record.skills:
            sid = skill.get("id")
            desc = skill.get("description") or ""
            if not desc.strip():
                # quarantined by the bootstrap — document, don't fail
                assert sid in {"ext-skill", "s1"}, (
                    f"{aid}/{sid}: unexpected description-less live skill")
                continue
            assert len(desc.strip()) >= 20, f"{aid}/{sid}: empty description"
