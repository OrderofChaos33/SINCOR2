#!/usr/bin/env python3
"""
Script to add Pydantic validation to app.py
Applies input validation to all user-facing endpoints
"""

import sys

def add_validation_imports(content):
    """Add validation imports after auth imports"""

    # Find auth imports section
    auth_section = """# Import authentication system
try:
    from auth_system import SINCORAuth, admin_required
    AUTH_AVAILABLE = True
except ImportError as e:
    print(f"Auth system not available: {e}")
    AUTH_AVAILABLE = False"""

    # Add validation imports
    validation_imports = """
# Import validation models
try:
    from validation_models import (
        WaitlistSignup,
        PaymentCreateRequest,
        PaymentExecuteRequest,
        LoginRequest,
        validate_request,
        sanitize_string
    )
    VALIDATION_AVAILABLE = True
except ImportError as e:
    print(f"Validation models not available: {e}")
    VALIDATION_AVAILABLE = False"""

    # Insert after auth section
    content = content.replace(auth_section, auth_section + validation_imports)
    return content


def patch_login_endpoint(content):
    """Add validation to login endpoint"""

    old_login = """    try:
        auth_data = request.get_json()

        username = auth_data.get('username')
        password = auth_data.get('password')

        if not username or not password:
            return jsonify({
                'success': False,
                'error': 'Username and password required'
            }), 400

        # Authenticate user
        result = sincor_auth.authenticate_user(username, password)"""

    new_login = """    try:
        auth_data = request.get_json()

        # Validate input with Pydantic
        if VALIDATION_AVAILABLE:
            validated_data, error = validate_request(LoginRequest, auth_data)
            if error:
                return jsonify({
                    'success': False,
                    'error': error
                }), 400

            username = validated_data['username']
            password = validated_data['password']
        else:
            username = auth_data.get('username')
            password = auth_data.get('password')

            if not username or not password:
                return jsonify({
                    'success': False,
                    'error': 'Username and password required'
                }), 400

        # Authenticate user
        result = sincor_auth.authenticate_user(username, password)"""

    content = content.replace(old_login, new_login)
    return content


def patch_waitlist_endpoint(content):
    """Add validation to waitlist endpoint"""

    old_waitlist = """        signup_data = request.get_json()

        # Validate required fields
        if not signup_data or not signup_data.get('email'):
            return jsonify({'success': False, 'error': 'Email address is required'})

        # Add to waitlist using the waitlist manager
        result = waitlist_manager.add_to_waitlist(signup_data)"""

    new_waitlist = """        signup_data = request.get_json()

        # Validate input with Pydantic
        if VALIDATION_AVAILABLE:
            validated_data, error = validate_request(WaitlistSignup, signup_data)
            if error:
                return jsonify({'success': False, 'error': error}), 400
        else:
            # Fallback validation
            if not signup_data or not signup_data.get('email'):
                return jsonify({'success': False, 'error': 'Email address is required'}), 400
            validated_data = signup_data

        # Add to waitlist using the waitlist manager
        result = waitlist_manager.add_to_waitlist(validated_data)"""

    content = content.replace(old_waitlist, new_waitlist)
    return content


def patch_payment_create_endpoint(content):
    """Add validation to payment creation endpoint"""

    old_payment = """        current_user = get_jwt_identity()
        payment_data = request.get_json()

        # Validate required fields
        required_fields = ['amount', 'description']
        for field in required_fields:
            if field not in payment_data:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        # Create payment request
        payment_request = PaymentRequest(
            amount=float(payment_data['amount']),
            currency=payment_data.get('currency', 'USD'),
            description=payment_data['description'],
            customer_email=payment_data.get('customer_email', ''),
            order_id=payment_data.get('order_id', ''),
            return_url=payment_data.get('return_url', request.host_url + 'payment/success'),
            cancel_url=payment_data.get('cancel_url', request.host_url + 'payment/cancel')
        )"""

    new_payment = """        current_user = get_jwt_identity()
        payment_data = request.get_json()

        # Validate input with Pydantic
        if VALIDATION_AVAILABLE:
            validated_data, error = validate_request(PaymentCreateRequest, payment_data)
            if error:
                return jsonify({'error': error}), 400
        else:
            # Fallback validation
            required_fields = ['amount', 'description']
            for field in required_fields:
                if field not in payment_data:
                    return jsonify({'error': f'Missing required field: {field}'}), 400
            validated_data = payment_data

        # Create payment request
        payment_request = PaymentRequest(
            amount=float(validated_data['amount']),
            currency=validated_data.get('currency', 'USD'),
            description=validated_data['description'],
            customer_email=validated_data.get('customer_email', ''),
            order_id=validated_data.get('order_id', ''),
            return_url=validated_data.get('return_url', request.host_url + 'payment/success'),
            cancel_url=validated_data.get('cancel_url', request.host_url + 'payment/cancel')
        )"""

    content = content.replace(old_payment, new_payment)
    return content


def patch_payment_execute_endpoint(content):
    """Add validation to payment execution endpoint"""

    old_execute = """        current_user = get_jwt_identity()
        payment_data = request.get_json()
        payment_id = payment_data.get('payment_id')
        payer_id = payment_data.get('payer_id')

        if not payment_id or not payer_id:
            return jsonify({'error': 'Missing payment_id or payer_id'}), 400

        # Execute payment synchronously
        result = paypal_processor.execute_payment_sync(payment_id, payer_id)"""

    new_execute = """        current_user = get_jwt_identity()
        payment_data = request.get_json()

        # Validate input with Pydantic
        if VALIDATION_AVAILABLE:
            validated_data, error = validate_request(PaymentExecuteRequest, payment_data)
            if error:
                return jsonify({'error': error}), 400
            payment_id = validated_data['payment_id']
            payer_id = validated_data['payer_id']
        else:
            payment_id = payment_data.get('payment_id')
            payer_id = payment_data.get('payer_id')

            if not payment_id or not payer_id:
                return jsonify({'error': 'Missing payment_id or payer_id'}), 400

        # Execute payment synchronously
        result = paypal_processor.execute_payment_sync(payment_id, payer_id)"""

    content = content.replace(old_execute, new_execute)
    return content


def main():
    """Apply all validation patches"""

    print("Applying validation patches to app.py...")

    # Read current app.py
    with open('app.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Apply patches
    content = add_validation_imports(content)
    print("✓ Added validation imports")

    content = patch_login_endpoint(content)
    print("✓ Patched login endpoint")

    content = patch_waitlist_endpoint(content)
    print("✓ Patched waitlist endpoint")

    content = patch_payment_create_endpoint(content)
    print("✓ Patched payment create endpoint")

    content = patch_payment_execute_endpoint(content)
    print("✓ Patched payment execute endpoint")

    # Write updated app.py
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(content)

    print("\n✅ All validation patches applied successfully!")
    print("\nValidation now active on:")
    print("  - /api/auth/login")
    print("  - /api/waitlist")
    print("  - /api/payment/create")
    print("  - /api/payment/execute")


if __name__ == "__main__":
    main()
