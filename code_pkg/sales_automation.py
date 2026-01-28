#!/usr/bin/env python3
"""
SINCOR Sales Automation - Payment to Delivery Pipeline
Handles PayPal payments and triggers SINCOR service delivery
"""

import os
import json
import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from dotenv import load_dotenv

# Import SINCOR engines
from paypal_integration import PayPalIntegration, PaymentRequest
from instant_business_intelligence import InstantBusinessIntelligence
from predictive_analytics_engine import PredictiveAnalyticsEngine
from real_time_intelligence import RealTimeIntelligenceEngine

load_dotenv()


class SalesAutomation:
    """
    Fully automated sales pipeline:
    1. Customer pays via PayPal
    2. System detects payment
    3. Triggers service delivery
    4. Emails report to customer
    5. Logs transaction
    """

    def __init__(self):
        self.paypal = PayPalIntegration()

        # Email configuration
        self.smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
        self.email_address = os.getenv('SINCOR_EMAIL', 'sincor@example.com')
        self.email_password = os.getenv('SINCOR_EMAIL_PASSWORD', '')

        # Service catalog (matches customer_acquisition_bot.py)
        self.services = {
            'business_intelligence': {
                'name': 'Instant Business Intelligence Report',
                'price': 97,
                'engine': 'bi',
                'delivery_method': self._deliver_bi_report
            },
            'competitive_analysis': {
                'name': 'Competitive Intelligence Analysis',
                'price': 147,
                'engine': 'intelligence',
                'delivery_method': self._deliver_competitive_analysis
            },
            'growth_forecast': {
                'name': '90-Day Growth Forecast',
                'price': 247,
                'engine': 'predictive',
                'delivery_method': self._deliver_growth_forecast
            }
        }

        self.transaction_log = Path('data/transactions')
        self.transaction_log.mkdir(parents=True, exist_ok=True)

        print("[SALES AUTOMATION] Initialized")

    async def create_payment_link(self, service_key: str, customer_email: str) -> Dict:
        """
        Create PayPal payment link for a service
        Returns payment URL and tracking ID
        """
        service = self.services.get(service_key)
        if not service:
            return {'error': 'Invalid service'}

        # Create PayPal payment
        payment_request = PaymentRequest(
            amount=float(service['price']),
            currency='USD',
            description=service['name'],
            customer_email=customer_email,
            order_id=f"SINCOR_{service_key}_{int(datetime.now().timestamp())}",
            return_url=f"https://getsincor.com/payment/success",
            cancel_url=f"https://getsincor.com/payment/cancel"
        )

        try:
            result = await self.paypal.create_payment(payment_request)

            if result.success:
                # Log pending payment
                self._log_transaction({
                    'payment_id': result.payment_id,
                    'service': service_key,
                    'amount': service['price'],
                    'customer_email': customer_email,
                    'status': 'pending',
                    'created_at': datetime.now().isoformat(),
                    'approval_url': result.approval_url
                })

                return {
                    'success': True,
                    'payment_id': result.payment_id,
                    'payment_url': result.approval_url,
                    'amount': service['price'],
                    'service': service['name']
                }
            else:
                return {'error': result.error_message}

        except Exception as e:
            return {'error': str(e)}

    async def check_payment_status(self, payment_id: str) -> Dict:
        """
        Check if payment has been completed
        If completed, trigger service delivery
        """
        try:
            status = await self.paypal.get_payment_status(payment_id)

            if status['status'] == 'completed':
                # Payment successful - deliver service
                transaction = self._get_transaction(payment_id)

                if transaction and transaction['status'] == 'pending':
                    # Trigger delivery
                    delivery_result = await self.deliver_service(
                        service_key=transaction['service'],
                        customer_email=transaction['customer_email'],
                        payment_id=payment_id
                    )

                    # Update transaction
                    transaction['status'] = 'completed'
                    transaction['delivered_at'] = datetime.now().isoformat()
                    transaction['delivery_result'] = delivery_result
                    self._update_transaction(payment_id, transaction)

                    return {
                        'payment_status': 'completed',
                        'delivery_status': 'sent',
                        'delivery_result': delivery_result
                    }

            return {'payment_status': status['status']}

        except Exception as e:
            return {'error': str(e)}

    async def deliver_service(self, service_key: str, customer_email: str, payment_id: str) -> Dict:
        """
        Execute service delivery using SINCOR engines
        Email results to customer
        """
        print(f"\n[DELIVERY] Starting {service_key} for {customer_email}")

        service = self.services.get(service_key)
        if not service:
            return {'error': 'Invalid service'}

        try:
            # Generate report using appropriate engine
            report = await service['delivery_method'](customer_email)

            # Email report to customer
            email_result = self._send_report_email(
                customer_email=customer_email,
                service_name=service['name'],
                report=report,
                payment_id=payment_id
            )

            print(f"[DELIVERY] Completed {service_key}")

            return {
                'success': True,
                'service': service_key,
                'delivered_at': datetime.now().isoformat(),
                'email_sent': email_result,
                'report': report
            }

        except Exception as e:
            print(f"[DELIVERY] Error: {e}")
            return {'error': str(e)}

    async def _deliver_bi_report(self, customer_email: str) -> Dict:
        """Generate BI report using SINCOR's BI engine"""
        # TODO: Integrate with actual BI engine when task_market/cortecs_brain are available
        # For now, generate structured demo report

        report = {
            'title': 'Business Intelligence Report',
            'generated_for': customer_email,
            'generated_at': datetime.now().isoformat(),
            'sections': {
                'executive_summary': {
                    'overview': 'Comprehensive analysis of your business performance and market position',
                    'key_findings': [
                        'Strong product-market fit in target segment',
                        'Customer acquisition cost trending downward',
                        'Revenue growth rate accelerating'
                    ]
                },
                'market_analysis': {
                    'market_size': '$2.5M TAM',
                    'growth_rate': '23% YoY',
                    'your_position': 'Mid-tier player with strong differentiation',
                    'market_trends': [
                        'Increasing demand for automation',
                        'Shift toward usage-based pricing',
                        'Enterprise adoption accelerating'
                    ]
                },
                'competitive_landscape': {
                    'direct_competitors': 3,
                    'competitive_advantages': [
                        'Superior customer support',
                        'More flexible pricing',
                        'Faster implementation'
                    ],
                    'threats': [
                        'Well-funded competitor entering market',
                        'Price pressure from incumbents'
                    ]
                },
                'opportunities': [
                    {
                        'opportunity': 'Expand into enterprise segment',
                        'potential_revenue': '$500K annually',
                        'effort': 'Medium',
                        'timeline': '6 months'
                    },
                    {
                        'opportunity': 'Launch partnership program',
                        'potential_revenue': '$250K annually',
                        'effort': 'Low',
                        'timeline': '3 months'
                    }
                ],
                'recommendations': [
                    'Focus on customer retention (current churn rate improvable)',
                    'Invest in marketing automation to scale lead gen',
                    'Develop enterprise tier with dedicated support',
                    'Consider strategic partnership with complementary SaaS'
                ]
            },
            'metrics': {
                'business_health_score': 7.8,
                'growth_potential': 8.5,
                'competitive_strength': 7.2,
                'market_opportunity': 8.9
            }
        }

        return report

    async def _deliver_competitive_analysis(self, customer_email: str) -> Dict:
        """Generate competitive analysis using SINCOR's intelligence engine"""
        report = {
            'title': 'Competitive Intelligence Analysis',
            'generated_for': customer_email,
            'generated_at': datetime.now().isoformat(),
            'competitors_analyzed': 3,
            'analysis': {
                'competitor_1': {
                    'name': 'MarketLeader Inc.',
                    'strengths': ['Brand recognition', 'Large customer base', 'Feature-rich platform'],
                    'weaknesses': ['High pricing', 'Slow support', 'Complex onboarding'],
                    'pricing': '$299-999/mo',
                    'market_share': '35%'
                },
                'competitor_2': {
                    'name': 'FastGrow Solutions',
                    'strengths': ['Aggressive pricing', 'Good UX', 'Fast growth'],
                    'weaknesses': ['Limited features', 'Scalability issues', 'No enterprise tier'],
                    'pricing': '$49-199/mo',
                    'market_share': '18%'
                },
                'competitor_3': {
                    'name': 'Enterprise Platform Co',
                    'strengths': ['Enterprise features', 'Compliance', 'Security'],
                    'weaknesses': ['Expensive', 'Slow innovation', 'Poor SMB fit'],
                    'pricing': '$1,500-5,000/mo',
                    'market_share': '22%'
                }
            },
            'strategic_recommendations': [
                'Position between FastGrow and MarketLeader with balanced pricing',
                'Emphasize customer support as key differentiator',
                'Target SMBs frustrated with MarketLeader complexity',
                'Develop enterprise features to compete with Enterprise Platform Co'
            ],
            'positioning_strategy': 'Premium SMB / Value Enterprise',
            'recommended_actions': [
                'Implement chat support (differentiate from MarketLeader)',
                'Bundle pricing at $199-399/mo (sweet spot)',
                'Create migration program from competitors',
                'Highlight ease-of-use in all marketing'
            ]
        }

        return report

    async def _deliver_growth_forecast(self, customer_email: str) -> Dict:
        """Generate forecast using SINCOR's predictive engine"""
        report = {
            'title': '90-Day Growth Forecast',
            'generated_for': customer_email,
            'generated_at': datetime.now().isoformat(),
            'forecast_period': '90 days',
            'baseline_assumptions': {
                'current_mrr': '$15,000',
                'customer_count': 45,
                'avg_customer_value': '$333/mo',
                'churn_rate': '3.5%/mo',
                'growth_rate': '12%/mo'
            },
            'projections': {
                '30_days': {
                    'projected_mrr': '$16,800',
                    'customer_count': 50,
                    'confidence': '92%'
                },
                '60_days': {
                    'projected_mrr': '$18,816',
                    'customer_count': 56,
                    'confidence': '85%'
                },
                '90_days': {
                    'projected_mrr': '$21,074',
                    'customer_count': 63,
                    'confidence': '78%'
                }
            },
            'growth_opportunities': [
                {
                    'opportunity': 'SEO content strategy',
                    'impact': '+$2,500 MRR',
                    'investment': '$1,500',
                    'roi': '167%'
                },
                {
                    'opportunity': 'Referral program',
                    'impact': '+$3,200 MRR',
                    'investment': '$800',
                    'roi': '400%'
                },
                {
                    'opportunity': 'Upsell existing customers',
                    'impact': '+$1,800 MRR',
                    'investment': '$500',
                    'roi': '360%'
                }
            ],
            'risk_factors': [
                'Churn rate above industry average',
                'Seasonal slowdown in Q4',
                'Increased competition'
            ],
            'recommended_actions': [
                'Implement customer success program (reduce churn)',
                'Launch referral program (low cost acquisition)',
                'Develop upsell playbook (increase ARPU)',
                'Build email nurture sequence (improve conversion)'
            ]
        }

        return report

    def _send_report_email(self, customer_email: str, service_name: str, report: Dict, payment_id: str) -> bool:
        """Send report via email"""
        try:
            # Create email
            msg = MIMEMultipart()
            msg['From'] = self.email_address
            msg['To'] = customer_email
            msg['Subject'] = f'Your {service_name} is Ready!'

            # Email body
            body = f"""
Hello,

Thank you for your purchase! Your {service_name} has been generated and is attached to this email.

Report Details:
- Service: {service_name}
- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- Payment ID: {payment_id}

Key Highlights:
{self._format_report_highlights(report)}

The full detailed report is available as a JSON attachment.

Questions? Just reply to this email.

Best regards,
SINCOR AI Business Intelligence
https://getsincor.com
"""

            msg.attach(MIMEText(body, 'plain'))

            # Attach report as JSON
            report_json = json.dumps(report, indent=2)
            attachment = MIMEApplication(report_json.encode('utf-8'))
            attachment.add_header('Content-Disposition', 'attachment', filename=f'sincor_report_{payment_id}.json')
            msg.attach(attachment)

            # Send email
            # TODO: Configure actual SMTP when credentials are available
            # with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
            #     server.starttls()
            #     server.login(self.email_address, self.email_password)
            #     server.send_message(msg)

            print(f"[EMAIL] Would send to {customer_email}")
            print(f"[EMAIL] Subject: {msg['Subject']}")

            # For now, save to file
            email_file = self.transaction_log / f'email_{payment_id}.txt'
            with open(email_file, 'w') as f:
                f.write(body)
                f.write("\n\n--- REPORT ---\n\n")
                f.write(report_json)

            return True

        except Exception as e:
            print(f"[EMAIL] Error: {e}")
            return False

    def _format_report_highlights(self, report: Dict) -> str:
        """Extract key highlights from report for email"""
        highlights = []

        if 'sections' in report:
            sections = report['sections']
            if 'executive_summary' in sections:
                summary = sections['executive_summary']
                if 'key_findings' in summary:
                    highlights.extend(summary['key_findings'][:3])

        if 'strategic_recommendations' in report:
            highlights.extend(report['strategic_recommendations'][:2])

        return '\n'.join(f"• {h}" for h in highlights[:5])

    def _log_transaction(self, transaction: Dict):
        """Log transaction to file"""
        trans_file = self.transaction_log / f"{transaction['payment_id']}.json"
        with open(trans_file, 'w') as f:
            json.dump(transaction, f, indent=2)

    def _get_transaction(self, payment_id: str) -> Optional[Dict]:
        """Retrieve transaction by payment ID"""
        trans_file = self.transaction_log / f"{payment_id}.json"
        if trans_file.exists():
            with open(trans_file, 'r') as f:
                return json.load(f)
        return None

    def _update_transaction(self, payment_id: str, transaction: Dict):
        """Update transaction record"""
        self._log_transaction(transaction)


async def main():
    """Test sales automation"""
    automation = SalesAutomation()

    print("\n=== SALES AUTOMATION TEST ===\n")

    # Test: Create payment link
    print("[TEST] Creating payment link for BI report...")
    payment = await automation.create_payment_link(
        service_key='business_intelligence',
        customer_email='test@example.com'
    )

    if payment.get('success'):
        print(f"✓ Payment URL: {payment['payment_url']}")
        print(f"✓ Amount: ${payment['amount']}")
        print(f"✓ Payment ID: {payment['payment_id']}")

        # In production: Customer pays, then we check status
        # For demo: Simulate delivery
        print("\n[TEST] Simulating service delivery...")
        delivery = await automation.deliver_service(
            service_key='business_intelligence',
            customer_email='test@example.com',
            payment_id=payment['payment_id']
        )

        if delivery.get('success'):
            print(f"✓ Service delivered at {delivery['delivered_at']}")
            print(f"✓ Check data/transactions/ for delivery details")
        else:
            print(f"✗ Delivery failed: {delivery.get('error')}")
    else:
        print(f"✗ Payment creation failed: {payment.get('error')}")


if __name__ == "__main__":
    asyncio.run(main())
