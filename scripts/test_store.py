from sincor_app import store_products

resp = store_products()
print('Response type:', type(resp))
print('JSON:', resp.get_json())
