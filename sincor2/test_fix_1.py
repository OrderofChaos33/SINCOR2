#!/usr/bin/env python3
"""
Test script for Fix #1: Async/Sync Payment Endpoints
Verifies that the payment endpoints now work synchronously with Flask
"""

import sys

def test_imports():
    """Test that all imports work"""
    print("Testing imports...")

    try:
        from app import app
        print("✅ app.py imports successfully")
    except Exception as e:
        print(f"❌ app.py import failed: {e}")
        return False

    try:
        from paypal_integration_sync import PayPalIntegrationSync, SINCORPaymentProcessorSync
        print("✅ paypal_integration_sync.py imports successfully")
    except Exception as e:
        print(f"❌ paypal_integration_sync.py import failed: {e}")
        return False

    try:
        from paypal_integration import PaymentRequest, PaymentStatus
        print("✅ paypal_integration.py imports successfully")
    except Exception as e:
        print(f"❌ paypal_integration.py import failed: {e}")
        return False

    return True

def test_sync_methods():
    """Test that sync methods exist"""
    print("\nTesting sync methods...")

    try:
        from paypal_integration_sync import PayPalIntegrationSync

        sync_processor = PayPalIntegrationSync()

        # Check that sync methods exist
        assert hasattr(sync_processor, 'get_access_token_sync'), "Missing get_access_token_sync"
        assert hasattr(sync_processor, 'create_payment_sync'), "Missing create_payment_sync"
        assert hasattr(sync_processor, 'execute_payment_sync'), "Missing execute_payment_sync"

        print("✅ All sync wrapper methods exist")
        return True

    except Exception as e:
        print(f"❌ Sync methods test failed: {e}")
        return False

def test_app_routes():
    """Test that Flask routes are defined correctly"""
    print("\nTesting Flask routes...")

    try:
        from app import app

        # Get all routes
        routes = [rule.rule for rule in app.url_map.iter_rules()]

        required_routes = [
            '/api/payment/create',
            '/api/payment/execute',
            '/api/monetization/start',
            '/health'
        ]

        for route in required_routes:
            if route in routes:
                print(f"✅ Route {route} exists")
            else:
                print(f"❌ Route {route} missing")
                return False

        return True

    except Exception as e:
        print(f"❌ Route test failed: {e}")
        return False

def test_no_async_decorators():
    """Verify that payment routes are not async"""
    print("\nTesting that payment routes are synchronous...")

    try:
        import inspect
        from app import create_payment, execute_payment, start_monetization

        # Check that functions are NOT coroutines
        if inspect.iscoroutinefunction(create_payment):
            print("❌ create_payment is still async!")
            return False
        else:
            print("✅ create_payment is synchronous")

        if inspect.iscoroutinefunction(execute_payment):
            print("❌ execute_payment is still async!")
            return False
        else:
            print("✅ execute_payment is synchronous")

        if inspect.iscoroutinefunction(start_monetization):
            print("❌ start_monetization is still async!")
            return False
        else:
            print("✅ start_monetization is synchronous")

        return True

    except Exception as e:
        print(f"❌ Async check failed: {e}")
        return False

def main():
    """Run all tests"""
    print("=" * 60)
    print("FIX #1 VERIFICATION: Async/Sync Payment Endpoints")
    print("=" * 60)

    all_pass = True

    all_pass = test_imports() and all_pass
    all_pass = test_sync_methods() and all_pass
    all_pass = test_app_routes() and all_pass
    all_pass = test_no_async_decorators() and all_pass

    print("\n" + "=" * 60)
    if all_pass:
        print("✅ ALL TESTS PASSED - Fix #1 successfully implemented!")
        print("=" * 60)
        return 0
    else:
        print("❌ SOME TESTS FAILED - Review errors above")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
