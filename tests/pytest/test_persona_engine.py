from __future__ import annotations

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
