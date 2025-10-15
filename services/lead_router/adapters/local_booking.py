import httpx, os

BOOKING_WEBHOOK = os.getenv("BOOKING_WEBHOOK","http://example.com/book")

async def send_to_booking(lead: dict):
    # Replace with Calendly/Squarespace/your booking API
    async with httpx.AsyncClient(timeout=10) as c:
        # Example payload:
        payload = {"name": lead["attributes"].get("name"), "email": lead["contact"].get("email"), "phone": lead["contact"].get("phone")}
        # Placeholder no-op
        return {"status": "queued", "payload": payload}