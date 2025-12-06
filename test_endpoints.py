#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, 'src')
from sincor2.mvp_app import app

client = app.test_client()

print("Testing MVP Endpoints...\n")

# Test health
resp = client.get('/health')
d = json.loads(resp.data)
print(f'✓ Health: {resp.status_code} - {d["status"]}')

# Test home
resp = client.get('/')
print(f'✓ Home: {resp.status_code}')

# Test login
resp = client.post('/api/auth/login', json={'email': 'test@example.com', 'password': 'demo'})
print(f'✓ Login: {resp.status_code}')
d = json.loads(resp.data)
token = d['access_token']

# Test protected (with token)
resp = client.get('/api/protected', headers={'Authorization': f'Bearer {token}'})
print(f'✓ Protected: {resp.status_code}')

# Test checkout
resp = client.post('/api/checkout', json={'email': 'test@example.com', 'amount': 29.99})
print(f'✓ Checkout: {resp.status_code}')
d = json.loads(resp.data)
order_id = d['order_id']

# Test payment
resp = client.post('/api/process-payment', json={'order_id': order_id, 'payment_method': 'card'})
print(f'✓ Payment: {resp.status_code} - Status: {json.loads(resp.data)["status"]}')

print('\n✅ All core endpoints working!')
