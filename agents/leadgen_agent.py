from typing import Any, Dict

class LeadGenAgent:
    async def handle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        niche = payload.get("niche", "auto_detailing")
        city = payload.get("city", "Clinton, IA")
        # Placeholder: insert real scraping/API logic
        leads = [
            {"name": "Sample Prospect 1", "contact": "prospect1@example.com", "city": city},
            {"name": "Sample Prospect 2", "contact": "prospect2@example.com", "city": city},
        ]
        return {"niche": niche, "count": len(leads), "leads": leads}
