#!/usr/bin/env python3
"""
SINCOR Comprehensive Test Suite
Tests all Priority 1 fixes and critical functionality
"""

import os
import sys
import json
import time
import requests
from datetime import datetime

# Test configuration
BASE_URL = os.environ.get('SINCOR_TEST_URL', 'http://localhost:5000')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme123')

# Test results tracker
test_results = {
    'total': 0,
    'passed': 0,
    'failed': 0,
    'skipped': 0,
    'errors': []
}


class TestResult:
    """Track test execution results"""

    def __init__(self, name):
        self.name = name
        self.passed = False
        self.error = None
        self.start_time = time.time()
        self.end_time = None

    def success(self):
        self.passed = True
        self.end_time = time.time()
        test_results['passed'] += 1
        print(f"  PASS: {self.name} ({self.duration():.2f}s)")

    def fail(self, error):
        self.passed = False
        self.error = str(error)
        self.end_time = time.time()
        test_results['failed'] += 1
        test_results['errors'].append({
            'test': self.name,
            'error': self.error
        })
        print(f"  FAIL: {self.name} - {self.error}")

    def skip(self, reason):
        self.end_time = time.time()
        test_results['skipped'] += 1
        print(f"  SKIP: {self.name} - {reason}")

    def duration(self):
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time


def run_test(name, test_func):
    """Run a single test with error handling"""
    test_results['total'] += 1
    result = TestResult(name)

    try:
        test_func(result)
        if not result.end_time:
            result.success()
    except Exception as e:
        result.fail(str(e))

    return result


# ==================== HEALTH CHECKS ====================

def test_server_running(result):
    """Test that the server is accessible"""
    response = requests.get(f"{BASE_URL}/health", timeout=5)
    if response.status_code != 200:
        raise Exception(f"Server returned {response.status_code}")


def test_api_version(result):
    """Test API version endpoint"""
    response = requests.get(f"{BASE_URL}/api/version", timeout=5)
    if response.status_code != 200:
        raise Exception(f"Version endpoint returned {response.status_code}")

    data = response.json()
    if 'version' not in data:
        raise Exception("Version data missing")


# ==================== FIX #3: JWT AUTHENTICATION ====================

def test_login_success(result):
    """Test successful login"""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={
            'username': ADMIN_USERNAME,
            'password': ADMIN_PASSWORD
        },
        timeout=5
    )

    if response.status_code != 200:
        raise Exception(f"Login failed: {response.status_code}")

    data = response.json()
    if not data.get('success'):
        raise Exception(f"Login unsuccessful: {data.get('error')}")

    if 'access_token' not in data:
        raise Exception("No access token in response")

    # Store token for later tests
    global ACCESS_TOKEN
    ACCESS_TOKEN = data['access_token']


def test_login_invalid_credentials(result):
    """Test login with invalid credentials"""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={
            'username': 'invalid',
            'password': 'wrongpassword'
        },
        timeout=5
    )

    if response.status_code == 200:
        raise Exception("Login should have failed with invalid credentials")

    if response.status_code != 401:
        raise Exception(f"Expected 401, got {response.status_code}")


def test_protected_endpoint_without_token(result):
    """Test accessing protected endpoint without token"""
    response = requests.get(
        f"{BASE_URL}/api/admin/dashboard",
        timeout=5
    )

    if response.status_code == 200:
        raise Exception("Protected endpoint should require authentication")


def test_protected_endpoint_with_token(result):
    """Test accessing protected endpoint with valid token"""
    if 'ACCESS_TOKEN' not in globals():
        result.skip("No access token available")
        return

    response = requests.get(
        f"{BASE_URL}/api/admin/dashboard",
        headers={'Authorization': f'Bearer {ACCESS_TOKEN}'},
        timeout=5
    )

    if response.status_code != 200:
        raise Exception(f"Protected endpoint failed: {response.status_code}")


# ==================== FIX #4: INPUT VALIDATION ====================

def test_waitlist_valid_input(result):
    """Test waitlist with valid input"""
    response = requests.post(
        f"{BASE_URL}/api/waitlist",
        json={
            'email': 'test@example.com',
            'name': 'Test User',
            'company': 'Test Corp'
        },
        timeout=5
    )

    if response.status_code != 200:
        raise Exception(f"Valid waitlist signup failed: {response.status_code}")


def test_waitlist_invalid_email(result):
    """Test waitlist with invalid email"""
    response = requests.post(
        f"{BASE_URL}/api/waitlist",
        json={
            'email': 'not-an-email',
            'name': 'Test User'
        },
        timeout=5
    )

    if response.status_code == 200:
        raise Exception("Invalid email should be rejected")

    data = response.json()
    if 'error' not in data:
        raise Exception("Error message missing from validation failure")


def test_waitlist_xss_attempt(result):
    """Test waitlist with XSS attempt"""
    response = requests.post(
        f"{BASE_URL}/api/waitlist",
        json={
            'email': 'test@example.com',
            'name': '<script>alert("xss")</script>'
        },
        timeout=5
    )

    # Should either sanitize or reject
    if response.status_code == 200:
        data = response.json()
        # Verify script tags are not present in response
        if '<script>' in str(data):
            raise Exception("XSS not sanitized")


def test_waitlist_sql_injection_attempt(result):
    """Test waitlist with SQL injection attempt"""
    response = requests.post(
        f"{BASE_URL}/api/waitlist",
        json={
            'email': "'; DROP TABLE users; --",
            'name': 'Test User'
        },
        timeout=5
    )

    # Should reject as invalid email
    if response.status_code == 200:
        raise Exception("SQL injection attempt should be rejected")


def test_payment_negative_amount(result):
    """Test payment with negative amount"""
    if 'ACCESS_TOKEN' not in globals():
        result.skip("No access token available")
        return

    response = requests.post(
        f"{BASE_URL}/api/payment/create",
        headers={'Authorization': f'Bearer {ACCESS_TOKEN}'},
        json={
            'amount': -100,
            'description': 'Test payment'
        },
        timeout=5
    )

    if response.status_code == 200:
        raise Exception("Negative amount should be rejected")


def test_payment_amount_too_large(result):
    """Test payment with excessive amount"""
    if 'ACCESS_TOKEN' not in globals():
        result.skip("No access token available")
        return

    response = requests.post(
        f"{BASE_URL}/api/payment/create",
        headers={'Authorization': f'Bearer {ACCESS_TOKEN}'},
        json={
            'amount': 2000000,
            'description': 'Test payment'
        },
        timeout=5
    )

    if response.status_code == 200:
        raise Exception("Excessive amount should be rejected")


def test_payment_invalid_description(result):
    """Test payment with too-short description"""
    if 'ACCESS_TOKEN' not in globals():
        result.skip("No access token available")
        return

    response = requests.post(
        f"{BASE_URL}/api/payment/create",
        headers={'Authorization': f'Bearer {ACCESS_TOKEN}'},
        json={
            'amount': 100,
            'description': 'hi'
        },
        timeout=5
    )

    if response.status_code == 200:
        raise Exception("Short description should be rejected")


# ==================== FIX #5: RATE LIMITING ====================

def test_rate_limit_headers(result):
    """Test that rate limit headers are present"""
    response = requests.post(
        f"{BASE_URL}/api/waitlist",
        json={
            'email': 'ratelimit@example.com',
            'name': 'Rate Limit Test'
        },
        timeout=5
    )

    headers = response.headers
    if 'X-RateLimit-Limit' not in headers:
        raise Exception("Rate limit headers missing")


def test_rate_limit_enforcement(result):
    """Test that rate limiting is enforced"""
    # Make multiple rapid requests to trigger rate limit
    attempts = 25  # More than the 20/min limit for public endpoints

    responses = []
    for i in range(attempts):
        response = requests.post(
            f"{BASE_URL}/api/waitlist",
            json={
                'email': f'test{i}@example.com',
                'name': f'Test User {i}'
            },
            timeout=5
        )
        responses.append(response)

    # Check if any were rate limited (429)
    rate_limited = [r for r in responses if r.status_code == 429]

    if not rate_limited:
        raise Exception("Rate limiting not enforced after 25 requests")

    # Verify 429 response format
    error_data = rate_limited[0].json()
    if 'error' not in error_data or 'rate_limit' not in error_data.get('error', '').lower():
        raise Exception("Invalid rate limit error response")


def test_login_rate_limit(result):
    """Test login rate limiting (5 per minute)"""
    # Attempt 7 logins rapidly (should hit 5/min limit)
    attempts = 7

    responses = []
    for i in range(attempts):
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={
                'username': 'testuser',
                'password': f'wrongpassword{i}'
            },
            timeout=5
        )
        responses.append(response)

    # Check if any were rate limited
    rate_limited = [r for r in responses if r.status_code == 429]

    if not rate_limited:
        raise Exception("Login rate limiting not enforced after 7 attempts")


# ==================== INTEGRATION TESTS ====================

def test_full_waitlist_flow(result):
    """Test complete waitlist signup flow"""
    timestamp = int(time.time())

    response = requests.post(
        f"{BASE_URL}/api/waitlist",
        json={
            'email': f'integration{timestamp}@example.com',
            'name': 'Integration Test User',
            'company': 'Test Company',
            'phone': '+1-555-123-4567',
            'message': 'This is an integration test'
        },
        timeout=5
    )

    if response.status_code != 200:
        raise Exception(f"Waitlist flow failed: {response.status_code}")

    data = response.json()
    if not data.get('success'):
        raise Exception(f"Waitlist signup unsuccessful: {data}")


def test_full_auth_flow(result):
    """Test complete authentication flow"""
    # Login
    login_response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={
            'username': ADMIN_USERNAME,
            'password': ADMIN_PASSWORD
        },
        timeout=5
    )

    if login_response.status_code != 200:
        raise Exception(f"Login failed: {login_response.status_code}")

    token = login_response.json().get('access_token')
    if not token:
        raise Exception("No access token received")

    # Access protected endpoint
    protected_response = requests.get(
        f"{BASE_URL}/api/admin/dashboard",
        headers={'Authorization': f'Bearer {token}'},
        timeout=5
    )

    if protected_response.status_code != 200:
        raise Exception(f"Protected endpoint access failed: {protected_response.status_code}")


# ==================== TEST RUNNER ====================

def run_all_tests():
    """Run all test suites"""

    print("\n" + "="*60)
    print("SINCOR COMPREHENSIVE TEST SUITE")
    print("="*60)
    print(f"Base URL: {BASE_URL}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60 + "\n")

    # Health checks
    print("HEALTH CHECKS:")
    run_test("Server is running", test_server_running)
    run_test("API version endpoint", test_api_version)

    # Fix #3: JWT Authentication
    print("\nFIX #3: JWT AUTHENTICATION:")
    run_test("Login with valid credentials", test_login_success)
    run_test("Login with invalid credentials", test_login_invalid_credentials)
    run_test("Protected endpoint without token", test_protected_endpoint_without_token)
    run_test("Protected endpoint with token", test_protected_endpoint_with_token)

    # Fix #4: Input Validation
    print("\nFIX #4: INPUT VALIDATION:")
    run_test("Waitlist with valid input", test_waitlist_valid_input)
    run_test("Waitlist with invalid email", test_waitlist_invalid_email)
    run_test("Waitlist XSS attempt", test_waitlist_xss_attempt)
    run_test("Waitlist SQL injection attempt", test_waitlist_sql_injection_attempt)
    run_test("Payment with negative amount", test_payment_negative_amount)
    run_test("Payment with excessive amount", test_payment_amount_too_large)
    run_test("Payment with short description", test_payment_invalid_description)

    # Fix #5: Rate Limiting
    print("\nFIX #5: RATE LIMITING:")
    run_test("Rate limit headers present", test_rate_limit_headers)
    run_test("Rate limit enforcement", test_rate_limit_enforcement)
    run_test("Login rate limiting", test_login_rate_limit)

    # Integration tests
    print("\nINTEGRATION TESTS:")
    run_test("Full waitlist flow", test_full_waitlist_flow)
    run_test("Full authentication flow", test_full_auth_flow)

    # Print summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Total tests:  {test_results['total']}")
    print(f"Passed:       {test_results['passed']} ({test_results['passed']/test_results['total']*100:.1f}%)")
    print(f"Failed:       {test_results['failed']}")
    print(f"Skipped:      {test_results['skipped']}")

    if test_results['errors']:
        print("\nFAILURES:")
        for error in test_results['errors']:
            print(f"  - {error['test']}: {error['error']}")

    print("="*60 + "\n")

    # Exit with appropriate code
    if test_results['failed'] > 0:
        print("RESULT: FAILED")
        sys.exit(1)
    else:
        print("RESULT: PASSED")
        sys.exit(0)


if __name__ == "__main__":
    try:
        run_all_tests()
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        sys.exit(1)
