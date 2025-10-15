import httpx
import json
import asyncio
from libs.pkg_guard.pii import safe_log

async def deliver_to_auction_winner(lead: Dict, auction_result: Dict) -> Dict:
    """Deliver lead to auction winning buyer via webhook"""
    
    if not auction_result or not auction_result.get('webhook'):
        return {"status": "failed", "error": "no_winning_buyer"}
    
    # Prepare lead payload (strip sensitive PII)
    payload = {
        "lead_id": lead.get("lead_id"),
        "vertical": lead.get("vertical"),
        "contact": {
            "email": lead.get("contact", {}).get("email"),
            "phone": lead.get("contact", {}).get("phone"),
            "state": lead.get("contact", {}).get("state")
        },
        "attributes": lead.get("attributes", {}),
        "score": lead.get("score", 0),
        "auction": {
            "winning_bid": auction_result["winning_bid"],
            "buyer_id": auction_result["buyer_id"]
        }
    }
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                auction_result["webhook"],
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Source": "sincor-auction",
                    "X-Buyer-ID": auction_result["buyer_id"]
                }
            )
            
            return {
                "status": "delivered" if response.status_code == 200 else "failed",
                "buyer_id": auction_result["buyer_id"],
                "winning_bid": auction_result["winning_bid"],
                "response_code": response.status_code,
                "auction_metadata": auction_result.get("auction_metadata", {})
            }
            
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
            "buyer_id": auction_result["buyer_id"],
            "winning_bid": auction_result["winning_bid"]
        }