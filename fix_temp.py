@app.route("/test-integrations", methods=["POST"])
def test_integrations():
    """Test all available integrations."""
    from real_integrations import test_all_integrations
    
    try:
        results = test_all_integrations()
        return jsonify(results)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/integration-status")
def integration_status():
    """Get current integration status."""
    integrations = session.get('integrations', {})
    
    return jsonify({
        "success": True,
        "connected_services": list(integrations.keys()),
        "total_connected": len(integrations),
        "available_integrations": ["calendar", "payments", "email", "sms", "google_my_business"],
        "integrations": integrations
    })

@app.get("/cortex/chat")
def cortex_chat():
    """CORTEX chat interface for Railway deployment."""
    return '''<!DOCTYPE html>
<html><head><title>SINCOR CORTEX Chat</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-gray-800 p-8 rounded-lg max-w-lg text-center">
<h1 class="text-3xl font-bold mb-6">🧠 CORTEX Chat Interface</h1>
<div class="bg-yellow-900 p-4 rounded-lg mb-6">
<p class="text-yellow-300">⚠️ CORTEX is running locally during development.</p>
<p class="text-sm text-yellow-400 mt-2">For full deployment, CORTEX needs to be deployed as a separate Railway service.</p>
</div>
<div class="space-y-4">
<a href="/" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
← Back to Dashboard
</a>
</div>
<p class="text-sm text-gray-400 mt-6">
Current Status: CORTEX backend development mode
</p>
</div></body></html>'''

if __name__=="__main__":
    port=int(os.environ.get("PORT","5000"))
    host="0.0.0.0"
    log(f"Starting SINCOR STANDALONE on {host}:{port}")
    log("Promo routes: /free-trial/FRIENDSTEST, /free-trial/PROTOTYPE2025, /free-trial/COURTTESTER")
    app.run(host=host, port=port, debug=False)