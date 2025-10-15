#!/usr/bin/env python3
"""
Test script to verify promo code functionality
"""

from flask import Flask, request, jsonify
from promo_codes import promo_system

app = Flask(__name__)

@app.route("/api/validate-promo", methods=["POST"])
def validate_promo_code():
    """Validate a promo code."""
    print("POST request received to /api/validate-promo")
    
    data = request.get_json()
    print(f"Request data: {data}")
    
    if not data:
        return jsonify({"valid": False, "error": "No data provided"})
    
    code = data.get("code", "").strip()
    print(f"Testing code: {code}")
    
    if not code:
        return jsonify({"valid": False, "error": "Promo code required"})
    
    result = promo_system.validate_code(code)
    print(f"Validation result: {result}")
    return jsonify(result)

if __name__ == "__main__":
    print("Starting promo code test server...")
    app.run(host="0.0.0.0", port=5002, debug=True)