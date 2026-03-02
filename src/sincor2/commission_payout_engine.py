"""
SINCOR Commission Payout Engine
Handles agent commission payments via PayPal + Crypto
"""

import os
import logging
import json
import requests
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PayPalPayoutEngine:
    """Handles PayPal commission payouts to agents"""

    def __init__(self):
        self.client_id = os.getenv('PAYPAL_CLIENT_ID')
        self.client_secret = os.getenv('PAYPAL_CLIENT_SECRET')
        self.api_base = 'https://api.paypal.com'  # Production
        # For sandbox: 'https://api.sandbox.paypal.com'

        if not self.client_id or not self.client_secret:
            logger.warning("PayPal credentials missing: Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")

    def get_access_token(self):
        """Get OAuth2 access token from PayPal"""
        try:
            auth = (self.client_id, self.client_secret)
            headers = {'Accept': 'application/json', 'Accept-Language': 'en_US'}
            data = {'grant_type': 'client_credentials'}

            response = requests.post(
                f'{self.api_base}/v1/oauth2/token',
                auth=auth,
                headers=headers,
                data=data,
                timeout=10
            )

            if response.status_code == 200:
                token = response.json().get('access_token')
                logger.info("PayPal access token obtained")
                return token
            else:
                logger.error(f"Failed to get PayPal token: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Error getting PayPal token: {e}")
            return None

    def send_payout(self, recipient_email: str, amount: float, description: str = "Commission Payout"):
        """
        Send commission payout to agent via PayPal

        Args:
            recipient_email: Agent's PayPal email address
            amount: Dollar amount to send
            description: Reason for payment

        Returns:
            Payout batch ID and status
        """
        try:
            token = self.get_access_token()
            if not token:
                return {'status': 'error', 'message': 'Could not obtain PayPal token'}

            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            }

            # Create payout batch
            payout_data = {
                'sender_batch_header': {
                    'sender_batch_id': f"commission_{int(datetime.utcnow().timestamp())}",
                    'email_subject': "SINCOR Agent Commission Payout",
                    'email_message': f"You've earned a commission for your agent work. {description}"
                },
                'items': [
                    {
                        'recipient_type': 'EMAIL',
                        'amount': {
                            'value': str(amount),
                            'currency': 'USD'
                        },
                        'description': description,
                        'receiver': recipient_email,
                        'note': f"Commission earned on {datetime.utcnow().strftime('%Y-%m-%d')}"
                    }
                ]
            }

            response = requests.post(
                f'{self.api_base}/v1/payments/payouts',
                headers=headers,
                json=payout_data,
                timeout=10
            )

            if response.status_code == 201:
                payout_id = response.json().get('batch_header', {}).get('payout_batch_id')
                logger.info(f"PayPal payout sent to {recipient_email}: ${amount} (ID: {payout_id})")
                return {
                    'status': 'success',
                    'payout_id': payout_id,
                    'recipient': recipient_email,
                    'amount': amount,
                    'timestamp': datetime.utcnow().isoformat()
                }
            else:
                error_msg = response.json().get('message', 'Unknown error')
                logger.error(f"PayPal payout failed: {error_msg}")
                return {
                    'status': 'error',
                    'message': error_msg,
                    'recipient': recipient_email,
                    'amount': amount
                }

        except Exception as e:
            logger.error(f"Error sending PayPal payout: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'recipient': recipient_email
            }

    def get_payout_status(self, payout_batch_id: str):
        """Check status of a payout batch"""
        try:
            token = self.get_access_token()
            if not token:
                return {'status': 'error', 'message': 'Could not obtain PayPal token'}

            headers = {'Authorization': f'Bearer {token}'}

            response = requests.get(
                f'{self.api_base}/v1/payments/payouts/{payout_batch_id}',
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get payout status: {response.status_code}")
                return {'status': 'error'}

        except Exception as e:
            logger.error(f"Error getting payout status: {e}")
            return {'status': 'error', 'error': str(e)}


class CryptoPayoutEngine:
    """
    Handles crypto commission payouts (Bitcoin, Ethereum, etc.)
    Integrates with blockchain APIs
    """

    def __init__(self):
        # These would come from your crypto provider (Coinbase, Kraken, BlockEra, etc.)
        self.bitcoin_api = os.getenv('BITCOIN_API_KEY')
        self.ethereum_api = os.getenv('ETHEREUM_API_KEY')
        self.crypto_provider = os.getenv('CRYPTO_PROVIDER', 'coinbase')  # coinbase, kraken, blockera

    def send_crypto_payout(self, agent_crypto_address: str, amount: float,
                          crypto_type: str = 'BTC', description: str = "Commission"):
        """
        Send commission via cryptocurrency

        Args:
            agent_crypto_address: Agent's wallet address (BTC, ETH, etc.)
            amount: Amount in USD equivalent
            crypto_type: 'BTC', 'ETH', 'USDC', etc.
            description: Reason for payment

        Returns:
            Transaction hash and status
        """
        try:
            logger.info(f"Sending {crypto_type} payout to {agent_crypto_address}: ${amount}")

            if self.crypto_provider == 'coinbase':
                return self._coinbase_send(agent_crypto_address, amount, crypto_type, description)
            elif self.crypto_provider == 'kraken':
                return self._kraken_send(agent_crypto_address, amount, crypto_type, description)
            else:
                return {
                    'status': 'pending',
                    'message': 'Crypto provider not configured',
                    'crypto_type': crypto_type,
                    'address': agent_crypto_address,
                    'amount': amount
                }

        except Exception as e:
            logger.error(f"Error sending crypto payout: {e}")
            return {'status': 'error', 'message': str(e)}

    def _coinbase_send(self, address: str, amount: float, crypto_type: str, description: str):
        """Send via Coinbase Commerce or Coinbase API"""
        # Placeholder - would integrate with actual Coinbase API
        logger.info(f"Would send {amount} USD worth of {crypto_type} to {address} via Coinbase")
        return {
            'status': 'pending_integration',
            'provider': 'coinbase',
            'crypto_type': crypto_type,
            'address': address,
            'amount_usd': amount,
            'description': description
        }

    def _kraken_send(self, address: str, amount: float, crypto_type: str, description: str):
        """Send via Kraken API"""
        # Placeholder - would integrate with actual Kraken API
        logger.info(f"Would send {amount} USD worth of {crypto_type} to {address} via Kraken")
        return {
            'status': 'pending_integration',
            'provider': 'kraken',
            'crypto_type': crypto_type,
            'address': address,
            'amount_usd': amount,
            'description': description
        }

    def convert_usd_to_crypto(self, usd_amount: float, crypto_type: str = 'BTC'):
        """Convert USD amount to crypto equivalent"""
        try:
            # Would use real price API
            # For now, hardcoded rates (update periodically)
            rates = {
                'BTC': 43500,    # $43,500 per BTC
                'ETH': 2300,     # $2,300 per ETH
                'USDC': 1.0      # $1 per USDC
            }

            rate = rates.get(crypto_type, 1.0)
            amount_crypto = usd_amount / rate
            return amount_crypto

        except Exception as e:
            logger.error(f"Error converting to crypto: {e}")
            return 0


class UnifiedPayoutEngine:
    """
    Routes payments to agents via their preferred method:
    - PayPal email address
    - Crypto wallet address
    - Bank transfer (future)
    """

    def __init__(self):
        self.paypal = PayPalPayoutEngine()
        self.crypto = CryptoPayoutEngine()

    def payout_to_agent(self, agent_info: dict, amount: float, commission_id: str):
        """
        Send payout to agent based on their preferred payment method

        agent_info should contain:
        {
            'name': 'Agent Name',
            'payment_method': 'paypal' or 'crypto',
            'paypal_email': 'agent@example.com' (if paypal),
            'crypto_address': '0x...' (if crypto),
            'crypto_type': 'BTC', 'ETH', 'USDC' (if crypto)
        }
        """
        try:
            payment_method = agent_info.get('payment_method', 'paypal').lower()
            agent_name = agent_info.get('name', 'Unknown Agent')

            if payment_method == 'paypal':
                paypal_email = agent_info.get('paypal_email')
                if not paypal_email:
                    return {
                        'status': 'error',
                        'message': 'PayPal email not provided',
                        'agent': agent_name
                    }

                result = self.paypal.send_payout(
                    recipient_email=paypal_email,
                    amount=amount,
                    description=f"SINCOR Commission (ID: {commission_id})"
                )

            elif payment_method == 'crypto':
                crypto_address = agent_info.get('crypto_address')
                crypto_type = agent_info.get('crypto_type', 'BTC')

                if not crypto_address:
                    return {
                        'status': 'error',
                        'message': 'Crypto address not provided',
                        'agent': agent_name
                    }

                result = self.crypto.send_crypto_payout(
                    agent_crypto_address=crypto_address,
                    amount=amount,
                    crypto_type=crypto_type,
                    description=f"SINCOR Commission (ID: {commission_id})"
                )

            else:
                return {
                    'status': 'error',
                    'message': f'Unknown payment method: {payment_method}',
                    'agent': agent_name
                }

            # Add agent name to result
            result['agent'] = agent_name
            result['commission_id'] = commission_id
            return result

        except Exception as e:
            logger.error(f"Error in unified payout: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'agent': agent_info.get('name', 'Unknown'),
                'commission_id': commission_id
            }

    def batch_payout(self, payouts: list):
        """
        Process multiple payouts

        payouts = [
            {'agent_info': {...}, 'amount': 100, 'commission_id': 'xxx'},
            {'agent_info': {...}, 'amount': 250, 'commission_id': 'yyy'},
            ...
        ]
        """
        results = []
        total_processed = 0
        total_amount = 0

        for payout in payouts:
            try:
                result = self.payout_to_agent(
                    agent_info=payout['agent_info'],
                    amount=payout['amount'],
                    commission_id=payout['commission_id']
                )
                results.append(result)

                if result['status'] == 'success':
                    total_processed += 1
                    total_amount += payout['amount']

            except Exception as e:
                logger.error(f"Error processing payout: {e}")
                results.append({
                    'status': 'error',
                    'message': str(e),
                    'agent': payout.get('agent_info', {}).get('name', 'Unknown')
                })

        return {
            'status': 'complete',
            'total_payouts': len(payouts),
            'successful': total_processed,
            'failed': len(payouts) - total_processed,
            'total_amount': total_amount,
            'results': results,
            'timestamp': datetime.utcnow().isoformat()
        }
