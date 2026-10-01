# A2A Quote Integration

Wire `build_quote_response` into the existing quote route in `src/sincor2/a2a_integration.py`.

```python
from sincor2.a2a_quote_hardening import build_quote_response

@bp.route("/api/a2a/quote", methods=["POST"])
def quote():
    from flask import jsonify, request
    body = request.get_json(force=True, silent=True) or {}
    skill_id = body.get("skill_id", "")
    skill = next((s for s in SINCOR_SKILLS if s.id == skill_id), None)
    if not skill:
        return jsonify(_err(f"Unknown skill: {skill_id}", code=-32602)), 400
    return jsonify(build_quote_response(skill_id, skill.name))
```

This adds the machine-readable `fee_split` block for external agents without changing settlement logic.
