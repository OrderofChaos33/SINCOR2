from __future__ import annotations

from pathlib import Path

from sincor2.persona_engine import PersonaEngine


def test_continuity_recovery_blends_back_toward_constitution(tmp_path) -> None:
    engine = PersonaEngine("E-persona-01", "Scout", persona_dir=str(tmp_path / "personas"))

    engine.current_persona.openness = 0.0
    engine.current_persona.conscientiousness = 0.0
    engine.current_persona.extraversion = 1.0
    engine.current_persona.agreeableness = 0.0
    engine.current_persona.neuroticism = 1.0
    engine.current_persona.risk_tolerance = 0.0
    engine.current_persona.directness = 0.0

    before = engine.calculate_constitutional_drift()
    assert before > 0.2

    engine._trigger_continuity_recovery(current_idx=1.0 - before)

    after = engine.calculate_constitutional_drift()
    assert after <= 0.2
    assert after < before


def test_constitutional_drift_never_clobbers_persona_file(tmp_path) -> None:
    """Regression: calculate_constitutional_drift() used to call the
    archetype builder, which persisted archetype defaults and overwrote
    the live persona file. The drift anchor must be built in memory only."""
    engine = PersonaEngine("E-persona-02", "Scout", persona_dir=str(tmp_path / "personas"))

    # Diverge the live persona from the archetype baseline and persist it.
    engine.current_persona.openness = 0.05
    engine.current_persona.directness = 0.95
    engine._save_persona(engine.current_persona)
    persona_path = Path(engine.persona_file)
    before_bytes = persona_path.read_bytes()

    drift = engine.calculate_constitutional_drift()

    # Drift is real: the diverged persona is far from the Scout anchor.
    assert 0.0 < drift <= 1.0
    # The live persona file is byte-for-byte unchanged ...
    assert persona_path.read_bytes() == before_bytes
    # ... and the in-memory persona was not reset to archetype defaults.
    assert engine.current_persona.openness == 0.05
    assert engine.current_persona.directness == 0.95


def test_constitutional_drift_zero_for_fresh_archetype(tmp_path) -> None:
    """A freshly created persona matches its archetype anchor: drift ~ 0."""
    engine = PersonaEngine("E-persona-03", "Builder", persona_dir=str(tmp_path / "personas"))
    assert engine.calculate_constitutional_drift() == 0.0
