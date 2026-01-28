"""Demo Email Workflow System stub."""

class EmailWorkflowSystem:
    def __init__(self):
        pass

    def generate_welcome_email(self, customer_name, service_type, business_name):
        return {'subject': f'Welcome, {customer_name}!', 'content': f'Thanks for choosing {business_name} for {service_type}.'}

    def generate_appointment_reminder(self, customer_name, appointment_date, service_type, business_name):
        return {'subject': f'Reminder for {customer_name}', 'content': f'Your {service_type} appointment at {appointment_date}.'}
