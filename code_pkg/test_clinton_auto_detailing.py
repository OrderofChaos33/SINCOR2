"""
Test Script for Clinton Auto Detailing - Square Integration
Tests all integrations with your actual Square credentials
"""

import os
import sys
import json
import asyncio
from datetime import datetime, timedelta

# Add integrations directory to path
sys.path.append('integrations')

# Set environment variables for testing
os.environ['SQUARE_APPLICATION_ID'] = 'sq0idp-VK2jNKBb4xLTcIAxADkY9g'
os.environ['SQUARE_ACCESS_TOKEN'] = 'EAAAl4HXPrCVLFcZd7UOCyV4_e_q201jW2RbZa_uZObQ7IIBXPyuc497vKBBvUEG'
os.environ['SQUARE_ENVIRONMENT'] = 'sandbox'  # Use sandbox for testing

# Business info
os.environ['BUSINESS_NAME'] = 'Clinton Auto Detailing'
os.environ['BUSINESS_EMAIL'] = 'energy@protonmail.com'
os.environ['BUSINESS_PHONE'] = '+16015555555'

# Facebook credentials (you provided earlier)
os.environ['FACEBOOK_PAGE_ID'] = '1304470571464929'
os.environ['FACEBOOK_BUSINESS_PORTFOLIO_ID'] = '2375579872784747'

# Google API keys (you provided earlier) 
os.environ['GOOGLE_PLACES_API_KEY'] = 'AIzaSyBOqhPHr7rA-pxzKdCFgR0zWbwQn1Ykh0I'
os.environ['GOOGLE_API_KEY_2'] = 'AIzaSyBQrbndbuV4Bkfj01_n4HkqdiNS9-fb_fM'

# PayPal credentials (you provided)
os.environ['PAYPAL_REST_API_ID'] = 'Ac0_uwVreyKj-vz0l8n5f2PDNs0-LCIuqahsBdeIMsJ-kMEzxXcEiWYI1kse8Ai0qoGH-bpCtZQgaoPh'
os.environ['PAYPAL_REST_API_SECRET'] = 'ELQFG6YTCH9RxqXWWJQ_peb7Nrt5GYN_qvcYv6vDXUtmI6GtTZRH9fKWLSk67kS4czJuKBykBS335tJc'
os.environ['PAYPAL_SANDBOX'] = 'true'

try:
    from integrations.square_integration import SquareIntegration
    from integrations.crm_integration import ClintonAutoDetailingCRM
    from integrations.email_workflow_system import EmailWorkflowSystem
    from integrations.accounting_integration import AccountingIntegration
    from integrations.square_workflow_optimizer import SquareWorkflowOptimizer
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running this from the SINCOR directory")
    sys.exit(1)

class ClintonAutoDetailingTester:
    def __init__(self):
        self.business_name = "Clinton Auto Detailing"
        self.location = "Clinton, MS"
        
    def print_header(self, title):
        print(f"\n{'='*60}")
        print(f"🚗 {title}")
        print(f"{'='*60}")
        
    def print_step(self, step, description):
        print(f"\n{step}. {description}")
        print("-" * 40)
        
    async def test_square_integration(self):
        """Test Square API connection and basic functions"""
        self.print_header("TESTING SQUARE INTEGRATION")
        
        try:
            square = SquareIntegration()
            
            self.print_step(1, "Testing Square API Connection")
            
            # Test getting locations
            locations = square.get_locations()
            if locations:
                print(f"✅ Connected to Square API")
                print(f"📍 Found {len(locations)} location(s):")
                for location in locations:
                    print(f"   - {location.get('name', 'Unnamed')} (ID: {location.get('id', 'Unknown')})")
                    
                # Save first location ID for other tests
                if locations:
                    location_id = locations[0].get('id')
                    os.environ['SQUARE_LOCATION_ID'] = location_id
                    print(f"🔧 Set SQUARE_LOCATION_ID = {location_id}")
            else:
                print("❌ No locations found")
                return False
                
            self.print_step(2, "Testing Customer Management")
            
            # Test customer creation
            test_customer = square.create_customer(
                given_name="John",
                family_name="Smith", 
                email_address="john.smith@example.com",
                phone_number="+16015551234",
                note="Test customer for Clinton Auto Detailing"
            )
            
            if test_customer:
                print(f"✅ Created test customer: {test_customer.get('given_name')} {test_customer.get('family_name')}")
                customer_id = test_customer.get('id')
                
                # Test customer retrieval
                retrieved_customer = square.get_customer(customer_id)
                if retrieved_customer:
                    print(f"✅ Retrieved customer: {retrieved_customer.get('given_name')} {retrieved_customer.get('family_name')}")
                
            self.print_step(3, "Testing Catalog/Services")
            
            # Get catalog items (services)
            catalog = square.get_catalog_items()
            if catalog:
                print(f"✅ Found {len(catalog)} catalog items")
                for item in catalog[:3]:  # Show first 3
                    name = item.get('item_data', {}).get('name', 'Unnamed Service')
                    print(f"   - {name}")
            else:
                print("ℹ️  No catalog items found (you can add services in Square Dashboard)")
                
            return True
            
        except Exception as e:
            print(f"❌ Square integration test failed: {e}")
            return False
    
    async def test_crm_integration(self):
        """Test CRM system"""
        self.print_header("TESTING CRM INTEGRATION") 
        
        try:
            crm = ClintonAutoDetailingCRM()
            
            self.print_step(1, "Testing CRM Database Setup")
            
            # Test customer creation in CRM
            customer_data = {
                'first_name': 'Sarah',
                'last_name': 'Johnson',
                'email': 'sarah.johnson@example.com',
                'phone': '+16015551235',
                'source': 'Website',
                'vehicle_info': '2020 Honda Accord - White',
                'preferred_services': 'Full Detail, Interior Clean'
            }
            
            customer_id = crm.create_customer(**customer_data)
            if customer_id:
                print(f"✅ Created CRM customer ID: {customer_id}")
                
                # Test 360-degree view
                customer_360 = crm.get_customer_360_view(customer_id)
                if customer_360:
                    print(f"✅ Generated 360-degree customer view")
                    print(f"   Customer: {customer_360['customer']['first_name']} {customer_360['customer']['last_name']}")
                    print(f"   Total Value: ${customer_360['summary']['total_spent']}")
                    
            return True
            
        except Exception as e:
            print(f"❌ CRM integration test failed: {e}")
            return False
    
    async def test_email_system(self):
        """Test email workflow system"""
        self.print_header("TESTING EMAIL WORKFLOW SYSTEM")
        
        try:
            email_system = EmailWorkflowSystem()
            
            self.print_step(1, "Testing Email System Setup")
            
            # Test email template generation
            welcome_email = email_system.generate_welcome_email(
                customer_name="Mike Wilson",
                service_type="Full Auto Detail",
                business_name=self.business_name
            )
            
            if welcome_email:
                print(f"✅ Generated welcome email template")
                print(f"   Subject: {welcome_email['subject']}")
                print(f"   Content preview: {welcome_email['content'][:100]}...")
            
            # Test reminder email
            reminder_email = email_system.generate_appointment_reminder(
                customer_name="Mike Wilson",
                appointment_date=datetime.now() + timedelta(days=1),
                service_type="Interior Detail",
                business_name=self.business_name
            )
            
            if reminder_email:
                print(f"✅ Generated appointment reminder email")
                print(f"   Subject: {reminder_email['subject']}")
                
            return True
            
        except Exception as e:
            print(f"❌ Email system test failed: {e}")
            return False
    
    async def test_accounting_system(self):
        """Test accounting integration"""
        self.print_header("TESTING ACCOUNTING SYSTEM")
        
        try:
            accounting = AccountingIntegration()
            
            self.print_step(1, "Testing Accounting Database Setup")
            
            # Test payment recording
            payment_id = accounting.record_square_payment(
                square_payment_id="test_payment_123",
                customer_id="test_customer_456",
                amount=85.00,
                service_description="Full Detail - 2019 Toyota Camry",
                payment_method="card"
            )
            
            if payment_id:
                print(f"✅ Recorded payment in accounting system")
                print(f"   Payment ID: {payment_id}")
                
                # Test financial report
                report = accounting.generate_financial_report(
                    start_date=datetime.now().date() - timedelta(days=30),
                    end_date=datetime.now().date()
                )
                
                if report:
                    print(f"✅ Generated financial report")
                    print(f"   Total Revenue: ${report['revenue']['total']}")
                    print(f"   Net Income: ${report['profit_loss']['net_income']}")
                    
            return True
            
        except Exception as e:
            print(f"❌ Accounting system test failed: {e}")
            return False
    
    async def test_workflow_automation(self):
        """Test workflow automation engine"""
        self.print_header("TESTING WORKFLOW AUTOMATION")
        
        try:
            workflow = SquareWorkflowOptimizer()
            
            self.print_step(1, "Testing Workflow Engine Setup")
            
            # Test workflow rule processing
            test_event = {
                'type': 'payment.created',
                'data': {
                    'payment_id': 'test_payment_789',
                    'customer_id': 'test_customer_789',
                    'amount': 120.00,
                    'customer_email': 'test@example.com',
                    'customer_phone': '+16015559999'
                }
            }
            
            result = workflow.process_square_webhook(test_event)
            if result:
                print(f"✅ Processed workflow event")
                print(f"   Triggered {len(result.get('actions', []))} automated actions")
                
            # Show active workflow rules
            rules = workflow.get_active_rules()
            print(f"✅ Found {len(rules)} active workflow rules:")
            for rule in rules[:3]:  # Show first 3
                print(f"   - {rule[1]}: {rule[2]}")
                
            return True
            
        except Exception as e:
            print(f"❌ Workflow automation test failed: {e}")
            return False
    
    async def run_comprehensive_test(self):
        """Run comprehensive test of all systems"""
        self.print_header(f"CLINTON AUTO DETAILING - INTEGRATION TEST SUITE")
        print(f"🏢 Business: {self.business_name}")
        print(f"📍 Location: {self.location}")
        print(f"🕐 Test Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        test_results = {}
        
        # Test each system
        test_results['square'] = await self.test_square_integration()
        test_results['crm'] = await self.test_crm_integration() 
        test_results['email'] = await self.test_email_system()
        test_results['accounting'] = await self.test_accounting_system()
        test_results['workflow'] = await self.test_workflow_automation()
        
        # Summary report
        self.print_header("TEST RESULTS SUMMARY")
        
        passed_tests = sum(1 for result in test_results.values() if result)
        total_tests = len(test_results)
        
        print(f"✅ Passed: {passed_tests}/{total_tests} integration tests")
        print(f"📊 Success Rate: {(passed_tests/total_tests)*100:.1f}%")
        
        print("\nDetailed Results:")
        for system, passed in test_results.items():
            status = "✅ PASSED" if passed else "❌ FAILED"
            print(f"   {system.upper()}: {status}")
        
        if passed_tests == total_tests:
            print(f"\n🎉 ALL TESTS PASSED!")
            print(f"🚀 Clinton Auto Detailing integration system is ready!")
            print(f"🌐 Launch the system with: python master_integration_orchestrator.py")
        else:
            print(f"\n⚠️  Some tests failed. Check error messages above.")
            print(f"💡 Most common issues:")
            print(f"   - Missing API credentials")
            print(f"   - Network connectivity")
            print(f"   - Database permissions")
            
        return test_results

async def main():
    """Main test execution"""
    print("🔧 Setting up Clinton Auto Detailing test environment...")
    
    tester = ClintonAutoDetailingTester()
    results = await tester.run_comprehensive_test()
    
    return results

if __name__ == "__main__":
    try:
        results = asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️  Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Test suite failed: {e}")
        print("Make sure you're running from the SINCOR directory")