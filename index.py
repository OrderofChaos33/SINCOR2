from flask import Flask, jsonify
import os

app = Flask(__name__)

@app.route('/')
def home():
    return '<h1>SINCOR PAYPAL TEST WORKING!</h1><p>PayPal: ' + str(bool(os.getenv('PAYPAL_REST_API_ID'))) + '</p>'

@app.route('/health')
def health():
    return jsonify({'status': 'PAYPAL READY'})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)