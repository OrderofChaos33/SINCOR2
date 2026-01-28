"""Demo CRM integration stub."""

class ClintonAutoDetailingCRM:
    def __init__(self):
        self._customers = {}

    def create_customer(self, first_name, last_name, email, phone, source=None, vehicle_info=None, preferred_services=None):
        cid = f"crm_{len(self._customers)+1}"
        self._customers[cid] = {'id': cid, 'first_name': first_name, 'last_name': last_name, 'email': email}
        return cid

    def get_customer_360_view(self, customer_id):
        customer = self._customers.get(customer_id, {'first_name': 'Unknown', 'last_name': ''})
        return {'customer': customer, 'summary': {'total_spent': 0}}
