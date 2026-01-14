import operator
from typing import Any, Dict, List

OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "in": lambda a,b: a in b,
    "not_in": lambda a,b: a not in b,
}

def get(d: Dict, path: str, default=None):
    cur = d
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur

def evaluate_rules(payload: Dict[str, Any], rules: List[Dict[str, Any]]) -> bool:
    for r in rules:
        # rule: {"left":"lead.attributes.credit_score","op":">=","right":640}
        left = get(payload, r["left"])
        right = r["right"]
        op = OPS[r.get("op","==")]
        if not op(left, right):
            return False
    return True