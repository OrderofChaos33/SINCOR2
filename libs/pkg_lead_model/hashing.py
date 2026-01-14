import hashlib

def dedupe_key(email: str | None, phone: str | None) -> str:
    data = (email or "").lower().strip() + "|" + (phone or "").strip()
    return hashlib.sha256(data.encode()).hexdigest()