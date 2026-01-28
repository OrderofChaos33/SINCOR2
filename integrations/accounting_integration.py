"""Demo Accounting Integration stub."""

class AccountingIntegration:
    def __init__(self):
        self._payments = []

    def record_square_payment(self, square_payment_id, customer_id, amount, service_description, payment_method='card'):
        pid = f"acct_{len(self._payments)+1}"
        self._payments.append({'id': pid, 'square_payment_id': square_payment_id, 'amount': amount})
        return pid

    def generate_financial_report(self, start_date, end_date):
        return {'revenue': {'total': 0}, 'profit_loss': {'net_income': 0}}
