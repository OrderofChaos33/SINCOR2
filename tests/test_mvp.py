#!/usr/bin/env python3
"""
SINCOR2 MVP - Test Suite
Validates core endpoints, auth, and payment flows.
Run: pytest tests/test_mvp.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import json
import pytest
from sincor2.mvp_app import app


@pytest.fixture
def client():
    """Create a test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


class TestHealth:
    """Test health and status endpoints."""
    
    def test_health_check(self, client):
        """GET /health should return 200 with healthy status."""
        response = client.get('/health')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'healthy'
        assert data['service'] == 'SINCOR2 MVP'
        assert 'version' in data
    
    def test_home_page(self, client):
        """GET / should return 200 with home page HTML."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'SINCOR2 MVP' in response.data


class TestAuthentication:
    """Test JWT authentication endpoints."""
    
    def test_login_success(self, client):
        """POST /api/auth/login with valid email/password should return access token."""
        response = client.post('/api/auth/login',
            data=json.dumps({'email': 'user@example.com', 'password': 'demo'}),
            content_type='application/json')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'access_token' in data
        assert data['user']['email'] == 'user@example.com'
        assert data['expires_in'] == 86400
    
    def test_login_missing_email(self, client):
        """POST /api/auth/login without email should return 400."""
        response = client.post('/api/auth/login',
            data=json.dumps({'password': 'demo'}),
            content_type='application/json')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data
    
    def test_protected_endpoint_without_token(self, client):
        """GET /api/protected without token should return 401."""
        response = client.get('/api/protected')
        assert response.status_code == 401
    
    def test_protected_endpoint_with_token(self, client):
        """GET /api/protected with valid token should return 200."""
        # First, login to get a token
        login_response = client.post('/api/auth/login',
            data=json.dumps({'email': 'user@example.com', 'password': 'demo'}),
            content_type='application/json')
        token = json.loads(login_response.data)['access_token']
        
        # Then, use the token to access protected endpoint
        response = client.get('/api/protected',
            headers={'Authorization': f'Bearer {token}'})
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'user' in data
        assert data['user'] == 'user@example.com'


class TestCheckout:
    """Test checkout and payment endpoints."""
    
    def test_buy_page(self, client):
        """GET /buy should return 200 with buy page HTML."""
        response = client.get('/buy')
        assert response.status_code == 200
        assert b'Checkout' in response.data
        assert b'SINCOR2 MVP Access' in response.data
    
    def test_checkout_success(self, client):
        """POST /api/checkout with valid data should create order."""
        response = client.post('/api/checkout',
            data=json.dumps({'email': 'user@example.com', 'amount': 29.99}),
            content_type='application/json')
        assert response.status_code == 201
        data = json.loads(response.data)
        assert 'order_id' in data
        assert data['status'] == 'pending'
        assert data['amount'] == 29.99
    
    def test_checkout_missing_email(self, client):
        """POST /api/checkout without email should return 400."""
        response = client.post('/api/checkout',
            data=json.dumps({'amount': 29.99}),
            content_type='application/json')
        assert response.status_code == 400
    
    def test_process_payment_success_or_fail(self, client):
        """POST /api/process-payment should process payment (90% success, 10% fail)."""
        # Create an order first
        order_response = client.post('/api/checkout',
            data=json.dumps({'email': 'user@example.com', 'amount': 29.99}),
            content_type='application/json')
        order_id = json.loads(order_response.data)['order_id']
        
        # Process payment
        payment_response = client.post('/api/process-payment',
            data=json.dumps({'order_id': order_id, 'payment_method': 'card'}),
            content_type='application/json')
        
        # Should be either 200 (success) or 402 (failed)
        assert payment_response.status_code in [200, 402]
        data = json.loads(payment_response.data)
        assert data['order_id'] == order_id
        assert 'status' in data
    
    def test_order_confirmation(self, client):
        """GET /api/order-confirmation/<order_id> should return order details."""
        response = client.get('/api/order-confirmation/ORDER-20250101000000')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['order_id'] == 'ORDER-20250101000000'
        assert data['status'] == 'confirmed'


class TestErrorHandling:
    """Test error handlers."""
    
    def test_404_not_found(self, client):
        """GET /nonexistent should return 404."""
        response = client.get('/nonexistent')
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data['status'] == 404


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
