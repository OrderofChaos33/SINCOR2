"""Demo workflow optimizer stub for Square webhooks."""

class SquareWorkflowOptimizer:
    def __init__(self):
        self._rules = [(1, 'Send Welcome Email', 'on_customer_created'), (2, 'Create Invoice', 'on_payment')] 

    def process_square_webhook(self, event):
        # Simulate processing
        return {'actions': [{'type': 'email', 'to': event.get('data', {}).get('customer_email')}]} 

    def get_active_rules(self):
        return self._rules
