"""Demo Square integration stub."""

class SquareIntegration:
    def __init__(self):
        # Demo data
        self._locations = [{'id': 'loc_1', 'name': 'Demo Location'}]
        self._customers = {}
        self._catalog = [{'id': 'item_1', 'item_data': {'name': 'Full Detail'}}]

    def get_locations(self):
        return self._locations

    def create_customer(self, given_name, family_name, email_address, phone_number, note=None):
        customer_id = f"cust_{len(self._customers)+1}"
        customer = {'id': customer_id, 'given_name': given_name, 'family_name': family_name, 'email_address': email_address}
        self._customers[customer_id] = customer
        return customer

    def get_customer(self, customer_id):
        return self._customers.get(customer_id)

    def get_catalog_items(self):
        return self._catalog
