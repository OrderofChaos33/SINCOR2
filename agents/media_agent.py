from typing import Any, Dict

class MediaAgent:
    async def handle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Placeholder for creating media packs
        brand = payload.get("brand", "Clinton Auto Detailing")
        deliverables = ["flyer.pdf", "storyboard.md", "ad_copy.txt"]
        return {"brand": brand, "generated": deliverables}
