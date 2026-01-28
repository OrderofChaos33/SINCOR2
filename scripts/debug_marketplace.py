import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from marketplace import SINCMarketplace

m = SINCMarketplace()
print('blockchain_enabled=', getattr(m,'blockchain_enabled',None))
print('has w3:', hasattr(m, 'w3'))
try:
    res = m.generate_payment_request('content-writer', '0xDEMOUSER', 2)
    print('RESULT:', res)
except Exception as e:
    import traceback
    traceback.print_exc()
