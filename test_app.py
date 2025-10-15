#!/usr/bin/env python3
"""
Minimal test app to verify Railway deployment
"""
import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return "<h1>SINCOR Test - Working!</h1><p>Port: " + os.environ.get("PORT", "5000") + "</p>"

@app.route("/health")
def health():
    return jsonify({"status": "ok", "port": os.environ.get("PORT", "5000")})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting test app on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)