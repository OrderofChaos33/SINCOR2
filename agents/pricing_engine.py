from typing import Any, Dict

class PricingEngine:
    async def handle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        base_price = float(payload.get("base_price", 199.0))
        demand = float(payload.get("demand", 1.0))  # 0.5-1.5
        urgency = float(payload.get("urgency", 1.0))  # 0.8-1.3
        price = round(base_price * demand * urgency, 2)
        return {"base_price": base_price, "demand": demand, "urgency": urgency, "price": price}
