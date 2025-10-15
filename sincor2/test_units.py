#!/usr/bin/env python3
"""
SINCOR Unit Tests
Tests individual modules without requiring server to be running
"""

import sys
import os

# Test results
passed = 0
failed = 0


def test(name, func):
    """Run a single unit test"""
    global passed, failed
    try:
        func()
        print(f"  PASS: {name}")
        passed += 1
        return True
    except Exception as e:
        print(f"  FAIL: {name} - {e}")
        failed += 1
        return False


print("\n" + "="*60)
print("SINCOR UNIT TESTS")
print("="*60 + "\n")

# ==================== TEST IMPORTS ====================

print("MODULE IMPORTS:")


def test_flask_app():
    from app import app
    assert app is not None


def test_auth_system():
    from auth_system import SINCORAuth
    assert SINCORAuth is not None


def test_validation_models():
    from validation_models import WaitlistSignup, PaymentCreateRequest
    assert WaitlistSignup is not None
    assert PaymentCreateRequest is not None


def test_rate_limiter():
    from rate_limiter import SINCORRateLimiter, AUTH_LIMITS
    assert SINCORRateLimiter is not None
    assert AUTH_LIMITS is not None


test("Flask app imports", test_flask_app)
test("Auth system imports", test_auth_system)
test("Validation models import", test_validation_models)
test("Rate limiter imports", test_rate_limiter)


# ==================== TEST VALIDATION ====================

print("\nVALIDATION MODELS:")


def test_valid_waitlist_signup():
    from validation_models import validate_request, WaitlistSignup

    data = {
        'email': 'test@example.com',
        'name': 'John Doe',
        'company': 'Acme Corp'
    }

    validated, error = validate_request(WaitlistSignup, data)
    assert error is None, f"Valid data rejected: {error}"
    assert validated['email'] == 'test@example.com'


def test_invalid_email_rejected():
    from validation_models import validate_request, WaitlistSignup

    data = {
        'email': 'not-an-email',
        'name': 'John Doe'
    }

    validated, error = validate_request(WaitlistSignup, data)
    assert error is not None, "Invalid email should be rejected"
    assert 'email' in error.lower()


def test_valid_payment_request():
    from validation_models import validate_request, PaymentCreateRequest

    data = {
        'amount': 100.50,
        'description': 'Test payment for product',
        'currency': 'USD'
    }

    validated, error = validate_request(PaymentCreateRequest, data)
    assert error is None, f"Valid payment rejected: {error}"
    assert validated['amount'] == 100.50


def test_negative_amount_rejected():
    from validation_models import validate_request, PaymentCreateRequest

    data = {
        'amount': -50,
        'description': 'Test payment'
    }

    validated, error = validate_request(PaymentCreateRequest, data)
    assert error is not None, "Negative amount should be rejected"


def test_excessive_amount_rejected():
    from validation_models import validate_request, PaymentCreateRequest

    data = {
        'amount': 2000000,
        'description': 'Test payment'
    }

    validated, error = validate_request(PaymentCreateRequest, data)
    assert error is not None, "Excessive amount should be rejected"


def test_short_description_rejected():
    from validation_models import validate_request, PaymentCreateRequest

    data = {
        'amount': 100,
        'description': 'hi'
    }

    validated, error = validate_request(PaymentCreateRequest, data)
    assert error is not None, "Short description should be rejected"


def test_string_sanitization():
    from validation_models import sanitize_string

    dangerous = "<script>alert('xss')</script>"
    safe = sanitize_string(dangerous)

    assert '<script>' not in safe, "Script tags should be escaped"
    assert '&lt;' in safe or safe != dangerous, "Tags should be escaped"


def test_email_validation():
    from validation_models import validate_email

    valid, _ = validate_email('test@example.com')
    assert valid, "Valid email should pass"

    valid, _ = validate_email('not-an-email')
    assert not valid, "Invalid email should fail"


def test_url_validation():
    from validation_models import validate_url

    valid, _ = validate_url('https://example.com')
    assert valid, "Valid HTTPS URL should pass"

    valid, _ = validate_url('javascript:alert(1)')
    assert not valid, "JavaScript URL should fail"


test("Valid waitlist signup", test_valid_waitlist_signup)
test("Invalid email rejected", test_invalid_email_rejected)
test("Valid payment request", test_valid_payment_request)
test("Negative amount rejected", test_negative_amount_rejected)
test("Excessive amount rejected", test_excessive_amount_rejected)
test("Short description rejected", test_short_description_rejected)
test("String sanitization", test_string_sanitization)
test("Email validation", test_email_validation)
test("URL validation", test_url_validation)


# ==================== TEST RATE LIMITING CONFIG ====================

print("\nRATE LIMITING:")


def test_rate_limit_configs():
    from rate_limiter import AUTH_LIMITS, PAYMENT_LIMITS, PUBLIC_LIMITS

    assert isinstance(AUTH_LIMITS, str), "AUTH_LIMITS should be string"
    assert 'per minute' in AUTH_LIMITS, "Should contain time window"
    assert ';' in AUTH_LIMITS, "Should use semicolon separator"


def test_rate_limit_config_function():
    from rate_limiter import get_rate_limit_config

    config = get_rate_limit_config()
    assert 'authentication' in config
    assert 'payment' in config
    assert isinstance(config['authentication'], list)


test("Rate limit configs are strings", test_rate_limit_configs)
test("Rate limit config function", test_rate_limit_config_function)


# ==================== TEST AUTH SYSTEM ====================

print("\nAUTHENTICATION:")


def test_auth_system_init():
    from auth_system import SINCORAuth

    auth = SINCORAuth()
    assert auth is not None


def test_valid_credentials():
    from auth_system import SINCORAuth
    from app import app

    auth = SINCORAuth()

    # Set test credentials
    os.environ['ADMIN_USERNAME'] = 'testadmin'
    os.environ['ADMIN_PASSWORD'] = 'testpass123'

    # Run within app context
    with app.app_context():
        result = auth.authenticate_user('testadmin', 'testpass123')
        assert result['success'], "Valid credentials should authenticate"
        assert 'access_token' in result


def test_invalid_credentials():
    from auth_system import SINCORAuth

    auth = SINCORAuth()

    os.environ['ADMIN_USERNAME'] = 'testadmin'
    os.environ['ADMIN_PASSWORD'] = 'testpass123'

    result = auth.authenticate_user('testadmin', 'wrongpassword')
    assert not result['success'], "Invalid credentials should fail"
    assert 'access_token' not in result


test("Auth system initialization", test_auth_system_init)
test("Valid credentials authenticate", test_valid_credentials)
test("Invalid credentials rejected", test_invalid_credentials)


# ==================== SUMMARY ====================

print("\n" + "="*60)
print("SUMMARY")
print("="*60)
print(f"Total:  {passed + failed}")
print(f"Passed: {passed}")
print(f"Failed: {failed}")
print("="*60 + "\n")

if failed > 0:
    print("RESULT: FAILED")
    sys.exit(1)
else:
    print("RESULT: PASSED")
    sys.exit(0)
