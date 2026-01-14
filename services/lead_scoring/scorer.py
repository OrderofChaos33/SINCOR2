def score_lead(lead: dict) -> int:
    s = 50
    v = lead.get("vertical","")
    c = lead.get("contact", {})
    meta = lead.get("meta",{})
    attrs = lead.get("attributes",{})

    # cheap heuristics to start (can replace with ML)
    if c.get("email"): s += 5
    if c.get("phone"): s += 5
    if lead.get("validation",{}).get("ok"): s += 10
    if attrs.get("credit_score",0) >= 640: s += 10
    if meta.get("utm",{}).get("source") in ("google","bing","ads"): s += 5
    return max(0, min(100, s))