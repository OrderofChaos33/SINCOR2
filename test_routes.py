#!/usr/bin/env python3
"""
Quick test to verify SINCOR Professional routes are working
"""

from sincor_app_professional import app

def test_routes():
    """Test key routes to ensure they're working."""
    
    test_routes = [
        ('/', 'Home page'),
        ('/admin/executive', 'Executive dashboard'),
        ('/login', 'Login page'),
        ('/api/admin/health-check', 'Health check API')
    ]
    
    with app.test_client() as client:
        print("Testing SINCOR Professional Routes:")
        print("=" * 50)
        
        for route, description in test_routes:
            try:
                response = client.get(route)
                status = "✓ OK" if response.status_code == 200 else f"✗ ERROR ({response.status_code})"
                print(f"{status:12} {route:25} - {description}")
                
                if response.status_code != 200:
                    print(f"              Error details: {response.data.decode()[:100]}...")
                    
            except Exception as e:
                print(f"✗ EXCEPTION  {route:25} - {description}: {e}")
        
        print("=" * 50)

if __name__ == "__main__":
    test_routes()