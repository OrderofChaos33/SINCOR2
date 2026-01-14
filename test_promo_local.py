#!/usr/bin/env python3
"""
Test the promo system locally to verify it works
"""

from flask import Flask
from railway_promo_bypass import add_promo_bypass_routes

app = Flask(__name__)
app.secret_key = "test-secret-key"

# Add promo routes
add_promo_bypass_routes(app)

@app.route("/")
def home():
    return """
    <h1>SINCOR Promo Test</h1>
    <p>Test these promo links:</p>
    <ul>
        <li><a href="/free-trial/FRIENDSTEST">FRIENDSTEST</a></li>
        <li><a href="/free-trial/PROTOTYPE2025">PROTOTYPE2025</a></li>
        <li><a href="/promo-status">Check Status</a></li>
    </ul>
    """

if __name__ == "__main__":
    print("Testing promo system locally...")
    print("Open: http://localhost:5555")
    app.run(host="localhost", port=5555, debug=True)