import hashlib, json, asyncpg, os

async def store_consent(conn, lead_id, ts, ip, referrer, form_html):
    digest = hashlib.sha256(form_html.encode()).hexdigest()
    await conn.execute(
        "INSERT INTO consent_artifacts (id, lead_id, ts, ip, referrer, form_html, sha256_hex) VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6)",
        lead_id, ts, ip, referrer, form_html, digest
    )
    return digest