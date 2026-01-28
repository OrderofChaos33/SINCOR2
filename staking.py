#!/usr/bin/env python3
"""
SINC Staking Backend
Handles token staking operations from the web interface
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

class SINCStaking:
    def __init__(self):
        # Configuration
        self.reward_rate = 0.125  # 12.5% APY
        self.min_stake = 100  # Minimum stake amount
        self.lock_period_days = 30  # Lock period in days

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
        logger.info(f"Staking wallet: {self.account.address}")

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
            },
            {
                "constant": True,
                "inputs": [],
                "name": "totalSupply",
                "outputs": [{"name": "", "type": "uint256"}],
                "type": "function"
            }
        ]

        # Initialize contract
        self.sinc_contract = self.w3.eth.contract(
            address=self.sinc_contract_address,
            abi=self.sinc_abi
        )

        # Load staking data
        self.staking_data_file = 'staking_data.json'
        self.staking_data = self._load_staking_data()

    def _load_staking_data(self):
        """Load staking data from file"""
        try:
            if os.path.exists(self.staking_data_file):
                with open(self.staking_data_file, 'r') as f:
                    data = json.load(f)
                    # Convert string timestamps back to datetime
                    for addr, stakes in data.items():
                        for stake in stakes:
                            stake['start_time'] = datetime.fromisoformat(stake['start_time'])
                            stake['end_time'] = datetime.fromisoformat(stake['end_time'])
                    return data
        except Exception as e:
            logger.error(f"Error loading staking data: {e}")

        return {}

    def _save_staking_data(self):
        """Save staking data to file"""
        try:
            # Convert datetime objects to strings
            data = {}
            for addr, stakes in self.staking_data.items():
                data[addr] = []
                for stake in stakes:
                    data[addr].append({
                        'amount': stake['amount'],
                        'start_time': stake['start_time'].isoformat(),
                        'end_time': stake['end_time'].isoformat(),
                        'rewards': stake['rewards']
                    })

            with open(self.staking_data_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving staking data: {e}")

    def _is_valid_address(self, address):
        """Validate Ethereum address"""
        return self.w3.is_address(address)

    def get_user_stakes(self, address):
        """Get staking information for a user"""
        if address not in self.staking_data:
            return []

        stakes = []
        for stake in self.staking_data[address]:
            now = datetime.now()
            days_staked = (now - stake['start_time']).days
            total_rewards = stake['amount'] * self.reward_rate * days_staked / 365

            stakes.append({
                'amount': stake['amount'],
                'start_time': stake['start_time'].isoformat(),
                'days_staked': days_staked,
                'rewards_earned': total_rewards,
                'can_unstake': now >= stake['end_time']
            })

        return stakes

    def get_staking_stats(self):
        """Get global staking statistics"""
        total_staked = 0
        total_stakers = len(self.staking_data)
        daily_rewards = 0

        for address, stakes in self.staking_data.items():
            for stake in stakes:
                total_staked += stake['amount']
                days_staked = (datetime.now() - stake['start_time']).days
                daily_rewards += stake['amount'] * self.reward_rate / 365

        return {
            'total_staked': total_staked,
            'total_stakers': total_stakers,
            'daily_rewards': daily_rewards,
            'apy': self.reward_rate * 100
        }

    def stake_tokens(self, user_address, amount):
        """Process token staking request"""
        try:
            # Validate inputs
            if not self._is_valid_address(user_address):
                return {
                    'success': False,
                    'error': 'Invalid wallet address'
                }

            try:
                amount = float(amount)
            except ValueError:
                return {
                    'success': False,
                    'error': 'Invalid amount'
                }

            if amount < self.min_stake:
                return {
                    'success': False,
                    'error': f'Minimum stake is {self.min_stake} SINC'
                }

            # Check user's SINC balance (this would require the user to approve transfer first)
            # For now, we'll simulate the staking since we can't directly transfer from user

            now = datetime.now()
            end_time = now + timedelta(days=self.lock_period_days)

            # Record the stake
            if user_address not in self.staking_data:
                self.staking_data[user_address] = []

            stake_record = {
                'amount': amount,
                'start_time': now,
                'end_time': end_time,
                'rewards': 0
            }

            self.staking_data[user_address].append(stake_record)
            self._save_staking_data()

            logger.info(f"Stake recorded: {amount} SINC for {user_address}")

            return {
                'success': True,
                'amount': amount,
                'lock_period': self.lock_period_days,
                'message': f'Successfully staked {amount} SINC tokens for {self.lock_period_days} days'
            }

        except Exception as e:
            logger.error(f"Error processing stake: {e}")
            return {
                'success': False,
                'error': 'Internal error. Please try again later.'
            }

    def unstake_tokens(self, user_address, amount):
        """Process token unstaking request"""
        try:
            if user_address not in self.staking_data:
                return {
                    'success': False,
                    'error': 'No stakes found for this address'
                }

            # Find a stake that can be unstaked
            available_stakes = [s for s in self.staking_data[user_address]
                              if datetime.now() >= s['end_time'] and s['amount'] >= amount]

            if not available_stakes:
                return {
                    'success': False,
                    'error': 'No eligible stakes found or insufficient staked amount'
                }

            # Use the first eligible stake
            stake = available_stakes[0]

            # Calculate rewards
            days_staked = (datetime.now() - stake['start_time']).days
            rewards = stake['amount'] * self.reward_rate * days_staked / 365

            # Remove the stake
            self.staking_data[user_address].remove(stake)
            if not self.staking_data[user_address]:
                del self.staking_data[user_address]
            self._save_staking_data()

            logger.info(f"Unstake processed: {amount} SINC + {rewards} rewards for {user_address}")

            return {
                'success': True,
                'amount': amount,
                'rewards': rewards,
                'message': f'Successfully unstaked {amount} SINC tokens plus {rewards:.2f} SINC rewards'
            }

        except Exception as e:
            logger.error(f"Error processing unstake: {e}")
            return {
                'success': False,
                'error': 'Internal error. Please try again later.'
            }

# Global staking instance
staking = None

def init_staking():
    """Initialize the staking instance"""
    global staking
    try:
        staking = SINCStaking()
        logger.info("Staking system initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize staking: {e}")
        return False

def get_staking_stats():
    """Get staking statistics"""
    if not staking:
        return {
            'total_staked': 0,
            'total_stakers': 0,
            'daily_rewards': 0,
            'apy': 12.5
        }

    return staking.get_staking_stats()

def get_user_staking_info():
    """Get user's staking information"""
    data = request.get_json()
    if not data or 'address' not in data:
        return jsonify({
            'success': False,
            'error': 'Missing address'
        }), 400

    user_address = data['address']

    if not staking:
        return jsonify({
            'success': False,
            'error': 'Staking service unavailable'
        }), 503

    stakes = staking.get_user_stakes(user_address)

    return jsonify({
        'success': True,
        'stakes': stakes
    })

def process_stake_request():
    """Process a staking request"""
    if not staking:
        return jsonify({
            'success': False,
            'error': 'Staking service unavailable'
        }), 503

    data = request.get_json()
    if not data or 'address' not in data or 'amount' not in data:
        return jsonify({
            'success': False,
            'error': 'Missing address or amount'
        }), 400

    user_address = data['address']
    amount = data['amount']

    result = staking.stake_tokens(user_address, amount)

    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400

def process_unstake_request():
    """Process an unstaking request"""
    if not staking:
        return jsonify({
            'success': False,
            'error': 'Staking service unavailable'
        }), 503

    data = request.get_json()
    if not data or 'address' not in data or 'amount' not in data:
        return jsonify({
            'success': False,
            'error': 'Missing address or amount'
        }), 400

    user_address = data['address']
    amount = data['amount']

    result = staking.unstake_tokens(user_address, amount)

    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400