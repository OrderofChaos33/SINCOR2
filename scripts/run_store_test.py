import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sincor_app import app

app.testing = True
client = app.test_client()

r = client.post('/store/create', json={'product_id': 'book_auto_detailing', 'email': 'court@example.com'})
print('status:', r.status_code)
print('data:', r.get_data(as_text=True))
