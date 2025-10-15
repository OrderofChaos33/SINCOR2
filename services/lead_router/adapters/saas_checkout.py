async def send_to_saas(lead: dict):
    # Hit your checkout/session creation, e.g., Stripe Checkout
    return {"status": "started_checkout_session"}