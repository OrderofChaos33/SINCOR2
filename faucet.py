#!/usr/bin/env python3
"""
SINC Faucet Backend
Handles token distribution requests from the web interface
"""

import os
import time
import json
from datetime import datetime, timedelta
from flask import request, jsonify
from web3 import Web3
from web3.middleware import geth_poa_middleware
from eth_account import Account
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SINCFaucet:
    def __init__(self):
        # Configuration
        self.claim_amount = 100  # SINC tokens per claim
        self.cooldown_hours = 24  # Hours between claims
        self.max_daily_claims = 100  # Max claims per day total

        # Blockchain setup
        self.rpc_url = os.getenv('BASE_RPC_URL', 'https://mainnet.base.org')
        self.private_key = os.getenv('PRIVATE_KEY')
        self.sinc_contract_address = '0xd10D86D09ee4316CdD3585fd6486537b7119A073'

        if not self.private_key:
            raise ValueError("PRIVATE_KEY environment variable not set")

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        if not self.w3.is_connected():
            raise ConnectionError("Cannot connect to Base network")

        # Setup account
        self.account = Account.from_key(self.private_key)
        logger.info(f"Faucet wallet: {self.account.address}")

        # SINC Token ABI (minimal)
        self.sinc_abi = [
            {
                "constant": True,
                "inputs": [{"name": "owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}],
                "type": "function"
            },
            {
                "constant": False,
                "inputs": [
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"}
                ],
                "name": "transfer",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function"
            }
        ]

        # Initialize contract
        self.sinc_contract = self.w3.eth.contract(
            address=self.sinc_contract_address,
            abi=self.sinc_abi
        )

        # Load claim history
        self.claim_history_file = 'faucet_claims.json'
        self.claim_history = self._load_claim_history()

    def _load_claim_history(self):
        """Load claim history from file"""
        try:
            if os.path.exists(self.claim_history_file):
                with open(self.claim_history_file, 'r') as f:
                    data = json.load(f)
                    # Convert string timestamps back to datetime
                    for addr, claims in data.items():
                        data[addr] = [datetime.fromisoformat(ts) for ts in claims]
                    return data
        except Exception as e:
            logger.error(f"Error loading claim history: {e}")

        return {}

    def _save_claim_history(self):
        """Save claim history to file"""
        try:
            # Convert datetime objects to strings
            data = {}
            for addr, claims in self.claim_history.items():
                data[addr] = [ts.isoformat() for ts in claims]

            with open(self.claim_history_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving claim history: {e}")

    def _is_valid_address(self, address):
        """Validate Ethereum address"""
        return self.w3.is_address(address)

    def _can_claim(self, address):
        """Check if address can claim tokens"""
        now = datetime.now()
        cooldown_period = timedelta(hours=self.cooldown_hours)

        if address not in self.claim_history:
            return True, "Ready to claim"

        # Check cooldown
        last_claim = max(self.claim_history[address])
        if now - last_claim < cooldown_period:
            remaining = cooldown_period - (now - last_claim)
            hours_left = int(remaining.total_seconds() / 3600)
            return False, f"Cooldown active. Try again in {hours_left} hours"

        return True, "Ready to claim"

    def _get_daily_claims_today(self):
        """Get number of claims made today"""
        today = datetime.now().date()
        count = 0

        for claims in self.claim_history.values():
            for claim_time in claims:
                if claim_time.date() == today:
                    count += 1

        return count

    def get_faucet_balance(self):
        """Get current faucet balance"""
        try:
            balance = self.sinc_contract.functions.balanceOf(self.account.address).call()
            return self.w3.from_wei(balance, 'ether')
        except Exception as e:
            logger.error(f"Error getting faucet balance: {e}")
            return 0

    def claim_tokens(self, recipient_address):
        """Process token claim request"""
        try:
            # Validate address
            if not self._is_valid_address(recipient_address):
                return {
                    'success': False,
                    'error': 'Invalid wallet address'
                }

            # Check claim eligibility
            can_claim, reason = self._can_claim(recipient_address)
            if not can_claim:
                return {
                    'success': False,
                    'error': reason
                }

            # Check daily limit
            daily_claims = self._get_daily_claims_today()
            if daily_claims >= self.max_daily_claims:
                return {
                    'success': False,
                    'error': 'Daily claim limit reached. Try again tomorrow.'
                }

            # Check faucet balance
            faucet_balance = self.get_faucet_balance()
            if faucet_balance < self.claim_amount:
                return {
                    'success': False,
                    'error': 'Faucet is empty. Please try again later.'
                }

            # Get nonce
            nonce = self.w3.eth.get_transaction_count(self.account.address)

            # Build transaction
            amount_wei = self.w3.to_wei(self.claim_amount, 'ether')

            tx = self.sinc_contract.functions.transfer(
                recipient_address,
                amount_wei
            ).build_transaction({
                'chainId': 8453,  # Base mainnet
                'gas': 100000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': nonce,
            })

            # Sign and send transaction
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt.status == 1:
                # Record successful claim
                now = datetime.now()
                if recipient_address not in self.claim_history:
                    self.claim_history[recipient_address] = []
                self.claim_history[recipient_address].append(now)
                self._save_claim_history()

                logger.info(f"Claim successful: {self.claim_amount} SINC to {recipient_address} (TX: {tx_hash.hex()})")

                return {
                    'success': True,
                    'tx_hash': tx_hash.hex(),
                    'amount': self.claim_amount,
                    'recipient': recipient_address
                }
            else:
                return {
                    'success': False,
                    'error': 'Transaction failed'
                }

        except Exception as e:
            logger.error(f"Error processing claim: {e}")
            return {
                'success': False,
                'error': 'Internal error. Please try again later.'
            }

# Global faucet instance
faucet = None

def init_faucet():
    """Initialize the faucet instance"""
    global faucet
    try:
        faucet = SINCFaucet()
        logger.info("Faucet initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize faucet: {e}")
        return False

def get_faucet_status():
    """Get faucet status for frontend"""
    if not faucet:
        return {
            'balance': 0,
            'claim_amount': 100,
            'cooldown_hours': 24,
            'daily_claims': 0,
            'max_daily_claims': 100
        }

    return {
        'balance': faucet.get_faucet_balance(),
        'claim_amount': faucet.claim_amount,
        'cooldown_hours': faucet.cooldown_hours,
        'daily_claims': faucet._get_daily_claims_today(),
        'max_daily_claims': faucet.max_daily_claims
    }

def process_claim_request():
    """Process a claim request from the frontend"""
    if not faucet:
        return jsonify({
            'success': False,
            'error': 'Faucet not available'
        }), 503

    data = request.get_json()
    if not data or 'address' not in data:
        return jsonify({
            'success': False,
            'error': 'Missing address'
        }), 400

    recipient_address = data['address']
    result = faucet.claim_tokens(recipient_address)

    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400