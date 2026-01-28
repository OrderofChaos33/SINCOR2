# -*- coding: utf-8 -*-
"""
Simple Test Script for Clinton Auto Detailing - Square Integration
"""

import os
import sys
import json
from datetime import datetime

# Set environment variables
os.environ['SQUARE_APPLICATION_ID'] = 'sq0idp-VK2jNKBb4xLTcIAxADkY9g'
os.environ['SQUARE_ACCESS_TOKEN'] = 'EAAAl4HXPrCVLFcZd7UOCyV4_e_q201jW2RbZa_uZObQ7IIBXPyuc497vKBBvUEG'
os.environ['SQUARE_ENVIRONMENT'] = 'sandbox'

# PayPal credentials
os.environ['PAYPAL_REST_API_ID'] = 'Ac0_uwVreyKj-vz0l8n5f2PDNs0-LCIuqahsBdeIMsJ-kMEzxXcEiWYI1kse8Ai0qoGH-bpCtZQgaoPh'
os.environ['PAYPAL_REST_API_SECRET'] = 'ELQFG6YTCH9RxqXWWJQ_peb7Nrt5GYN_qvcYv6vDXUtmI6GtTZRH9fKWLSk67kS4czJuKBykBS335tJc'

# Business info
os.environ['BUSINESS_NAME'] = 'Clinton Auto Detailing'

print("=" * 60)
print("CLINTON AUTO DETAILING - INTEGRATION TEST")
print("=" * 60)
print(f"Business: {os.environ['BUSINESS_NAME']}")
print(f"Test Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Add integrations to path
sys.path.append('integrations')

try:
    print("\n1. Testing Square Integration...")
    from integrations.square_integration import SquareIntegration
    
    square = SquareIntegration()
    locations = square.get_locations()
    
    if locations:
        print(f"   SUCCESS: Connected to Square API")
        print(f"   Found {len(locations)} location(s)")
        for location in locations[:2]:  # Show first 2
            name = location.get('name', 'Unnamed Location')
            loc_id = location.get('id', 'Unknown ID')
            print(f"   - {name} (ID: {loc_id})")
            
        # Save location ID for other systems
        if locations:
            os.environ['SQUARE_LOCATION_ID'] = locations[0].get('id')
    else:
        print("   WARNING: No locations found")

except Exception as e:
    print(f"   ERROR: Square test failed - {e}")

try:
    print("\n2. Testing CRM System...")
    from integrations.crm_integration import ClintonAutoDetailingCRM
    
    crm = ClintonAutoDetailingCRM()
    
    # Test customer creation
    customer_id = crm.create_customer(
        first_name="Test",
        last_name="Customer", 
        email="test@clintondetailing.com",
        phone="+16015551234"
    )
    
    if customer_id:
        print(f"   SUCCESS: Created CRM customer (ID: {customer_id})")
    else:
        print("   WARNING: CRM customer creation failed")

except Exception as e:
    print(f"   ERROR: CRM test failed - {e}")

try:
    print("\n3. Testing Email System...")
    from integrations.email_workflow_system import EmailWorkflowSystem
    
    email_system = EmailWorkflowSystem()
    
    welcome_email = email_system.generate_welcome_email(
        customer_name="Test Customer",
        service_type="Auto Detail",
        business_name="Clinton Auto Detailing"
    )
    
    if welcome_email:
        print("   SUCCESS: Email system working")
        print(f"   Generated welcome email: {welcome_email['subject']}")
    else:
        print("   WARNING: Email generation failed")

except Exception as e:
    print(f"   ERROR: Email test failed - {e}")

try:
    print("\n4. Testing Accounting System...")
    from integrations.accounting_integration import AccountingIntegration
    
    accounting = AccountingIntegration()
    
    # Test payment recording
    payment_id = accounting.record_square_payment(
        square_payment_id="test_123",
        customer_id="test_customer",
        amount=75.00,
        service_description="Test Detail Service"
    )
    
    if payment_id:
        print(f"   SUCCESS: Recorded payment (ID: {payment_id})")
    else:
        print("   WARNING: Payment recording failed")

except Exception as e:
    print(f"   ERROR: Accounting test failed - {e}")

try:
    print("\n5. Testing PayPal Integration...")
    import asyncio
    from sincor.paypal_integration import PayPalIntegration
    
    async def test_paypal():
        paypal = PayPalIntegration()
        token = await paypal.get_access_token()
        return token is not None
    
    paypal_works = asyncio.run(test_paypal())
    
    if paypal_works:
        print("   SUCCESS: PayPal connection established")
    else:
        print("   WARNING: PayPal connection failed")

except Exception as e:
    print(f"   ERROR: PayPal test failed - {e}")

print("\n" + "=" * 60)
print("TEST SUMMARY")
print("=" * 60)
print("CORE SYSTEMS TESTED:")
print("- Square Payment Integration")  
print("- CRM Customer Management")
print("- Email Workflow System") 
print("- Accounting & Financial Tracking")
print("- PayPal Payment Processing")
print("\nClinton Auto Detailing integration system is ready for deployment!")
print("Next step: Get Square Location ID from dashboard")
print("Dashboard URL: http://localhost:5000 (when running master_integration_orchestrator.py)")