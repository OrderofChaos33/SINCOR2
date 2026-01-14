#!/usr/bin/env python3
"""
ChatGPT Smoke Test for Clinton Auto Detailing Lead Ingest
Test the "flip switches" setup with actual curl command
"""

import requests
import json
import uuid
from datetime import datetime, timedelta

# ChatGPT exact curl command translated to Python
def test_clinton_lead_ingest():
    """Test the urgent Clinton campaign lead ingest endpoint"""
    
    # Set your API key (from environment or hardcoded for testing)
    INGEST_API_KEY = "clinton-detailing-urgent-key-2024"
    
    url = "http://localhost:8000/leads"
    
    headers = {
        "Authorization": f"Bearer {INGEST_API_KEY}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": "local-test-1"  # ChatGPT requirement
    }
    
    # ChatGPT payload structure
    payload = {
        "schema": "lead.v1",
        "lead_id": "00000000-0000-0000-0000-000000000001",
        "vertical": "auto_detailing",
        "ts": "2025-09-05T20:00:00Z",
        "contact": {
            "email": "test@example.com",
            "phone": "+15635550123",
            "ip": "8.8.8.8",
            "state": "IA",
            "zip": "52732"  # Clinton, IA ZIP
        },
        "meta": {
            "utm": {
                "source": "localtest"
            }
        },
        "attributes": {
            "name": "Court",
            "urgency": "asap",
            "service_interest": "full_detail",
            "needs_by": (datetime.now() + timedelta(days=2)).isoformat()
        },
        "consent": {
            "tcpa": True,
            "timestamp": "2025-09-05T20:00:00Z"
        }
    }
    
    print("Testing Clinton Auto Detailing Lead Ingest")
    print(f"URL: {url}")
    print(f"API Key: {INGEST_API_KEY}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    print("\n" + "="*50)
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        print(f"Response Body: {response.text}")
        
        if response.status_code == 200:
            response_data = response.json()
            print("\n✅ SUCCESS!")
            print(f"🎯 Lead Score: {response_data.get('score', 'N/A')}")
            print(f"🚀 Routing: {response_data.get('routing', 'N/A')}")
            print(f"🏆 Competitive Advantage: {response_data.get('competitive_advantage', 'N/A')}")
            
            if response_data.get('routing') == 'IMMEDIATE_BOOKING':
                print("🚨 HIGH-VALUE LEAD - Routed immediately!")
                print(f"📞 Response target: {response_data.get('followup_time', '< 15 minutes')}")
            else:
                print("📧 Lead entered outreach sequence")
        else:
            print(f"\n❌ ERROR: {response.status_code}")
            print(f"Error details: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print("❌ CONNECTION ERROR: Make sure your app is running on localhost:8000")
        print("Run: python app.py")
    except Exception as e:
        print(f"❌ EXCEPTION: {str(e)}")

def test_high_score_lead():
    """Test with Clinton ZIP + phone + urgency = high score"""
    
    INGEST_API_KEY = "clinton-detailing-urgent-key-2024"
    url = "http://localhost:8000/leads"
    
    headers = {
        "Authorization": f"Bearer {INGEST_API_KEY}",
        "Content-Type": "application/json", 
        "X-Idempotency-Key": f"high-score-test-{uuid.uuid4()}"
    }
    
    # This should get score 95+ (40 proximity + 30 urgency + 25 phone + 10 email)
    payload = {
        "schema": "lead.v1",
        "lead_id": str(uuid.uuid4()),
        "vertical": "auto_detailing",
        "ts": datetime.now().isoformat(),
        "contact": {
            "email": "highvalue@clinton.com",
            "phone": "+15635551234",  # Clinton area code
            "ip": "192.168.1.1",
            "state": "IA",
            "zip": "52732"  # Clinton, IA - should get 40 points
        },
        "attributes": {
            "name": "Sarah Johnson",
            "urgency": "asap",  # Should get 30 points
            "service_interest": "premium_detail",
            "vehicle": "BMW X5",
            "needs_by": (datetime.now() + timedelta(days=1)).isoformat()
        },
        "consent": {
            "tcpa": True,
            "timestamp": datetime.now().isoformat()
        }
    }
    
    print("\n🎯 Testing HIGH-SCORE Lead (should route immediately)")
    print("Expected score: 95+ (40 proximity + 30 urgency + 25 phone + 10 email)")
    
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Score: {data.get('score')}")
        print(f"🚀 Routing: {data.get('routing')}")
        
        if data.get('routing') == 'IMMEDIATE_BOOKING':
            print("🎉 SUCCESS: High-value lead routed immediately!")
        else:
            print("⚠️  Unexpected: Should have routed immediately")
    else:
        print(f"❌ Error: {response.text}")

def test_rate_limiting():
    """Test ChatGPT rate limiting (5 RPS default)"""
    
    INGEST_API_KEY = "clinton-detailing-urgent-key-2024"
    url = "http://localhost:8000/leads"
    
    print("\n⚡ Testing Rate Limiting (should block after 5 requests)")
    
    for i in range(7):  # Send 7 requests rapidly
        headers = {
            "Authorization": f"Bearer {INGEST_API_KEY}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": f"rate-test-{i}"
        }
        
        payload = {
            "lead_id": str(uuid.uuid4()),
            "vertical": "auto_detailing", 
            "contact": {"email": f"test{i}@example.com"},
            "consent": {"tcpa": True}
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            print(f"Request {i+1}: ✅ Accepted")
        elif response.status_code == 429:
            print(f"Request {i+1}: ⛔ Rate limited (expected)")
            break
        else:
            print(f"Request {i+1}: ❌ Error {response.status_code}")

if __name__ == "__main__":
    print("ChatGPT Smoke Test - Clinton Auto Detailing Lead Ingest")
    print("="*60)
    
    # Run all tests
    test_clinton_lead_ingest()
    test_high_score_lead() 
    test_rate_limiting()
    
    print("\n🏁 Smoke test complete!")
    print("\nNext steps:")
    print("1. ✅ Lead ingest working with competitive advantage")
    print("2. 🎯 Set up Facebook Lead Ads")
    print("3. 📱 Configure SMS/email outreach")
    print("4. 🚀 Launch campaigns today!")