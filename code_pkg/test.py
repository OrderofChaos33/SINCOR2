#!/usr/bin/env python3
"""
ABSOLUTE MINIMAL TEST - BYPASS ALL CACHING
"""
print("Content-Type: text/html\n")
print("<h1>SINCOR PAYPAL TEST LIVE!</h1>")

import os
paypal_id = os.getenv('PAYPAL_REST_API_ID')
print(f"<p>PayPal ID Found: {bool(paypal_id)}</p>")
if paypal_id:
    print(f"<p>PayPal ID (first 10 chars): {paypal_id[:10]}...</p>")
print("<p>RAILWAY CACHE BYPASSED!</p>")