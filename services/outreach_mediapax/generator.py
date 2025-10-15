import asyncpg
import os
import json

async def generate_pack(lead: dict) -> dict:
    """Generate MediaPax using purchased templates"""
    vertical = lead.get("vertical","")
    state = lead.get("contact",{}).get("state","")
    tenant_id = lead.get("tenant_id")
    
    # Connect to DB to get purchased templates
    db_conn = None
    purchased_templates = []
    
    try:
        if os.getenv("DB_DSN"):
            db_conn = await asyncpg.connect(os.getenv("DB_DSN"))
            
            # Get templates this tenant has purchased
            if tenant_id:
                rows = await db_conn.fetch("""
                    SELECT c.title, c.type, c.category, c.payload_ref, c.tags
                    FROM catalog c
                    JOIN purchases p ON c.id = p.item_id
                    WHERE p.tenant_id = $1 AND p.status = 'completed'
                    AND c.category = $2 AND c.active = true
                """, tenant_id, vertical)
                
                purchased_templates = [dict(row) for row in rows]
    
    except Exception:
        pass  # Fall back to default templates
    finally:
        if db_conn:
            await db_conn.close()
    
    # Use purchased templates if available, otherwise default
    if purchased_templates:
        # Use first matching purchased template
        template = purchased_templates[0]
        return {
            "headline": f"{template['title']} for {state}",
            "template_source": "purchased",
            "template_id": template['payload_ref'],
            "scripts": [
                {"hook":"0-3s", "text":"Premium template - Stop scrolling instant results."},
                {"benefit":"3-9s", "text":"Professional grade, proven conversions."},
                {"proof":"9-15s", "text":"Enterprise-level creative assets."},
                {"cta":"15-20s", "text":"Get started today."}
            ],
            "assets": [template['payload_ref']],
            "purchased_template": template['title']
        }
    else:
        # Default free template
        return {
            "headline": f"{vertical.title()} Growth Pack for {state}",
            "template_source": "default",
            "scripts": [
                {"hook":"0-3s", "text":"Stop scrolling—instant shine in 3 hours."},
                {"benefit":"3-9s", "text":"Less hassle, more bookings this week."},
                {"proof":"9-15s", "text":"Before/After splits + local reviews."},
                {"cta":"15-20s", "text":"Tap to book today."}
            ],
            "assets": [],
            "upsell_message": f"Upgrade to premium {vertical} templates in marketplace"
        }