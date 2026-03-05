"""
Stripe Checkout Routes for SINCOR
Handles payment creation and webhooks
"""

import os
import json
import logging
from flask import Blueprint, request, jsonify, current_app
from functools import wraps

logger = logging.getLogger('sincor.stripe_routes')

stripe_bp = Blueprint('stripe', __name__, url_prefix='/api/stripe')


def init_stripe_routes(app, stripe_processor):
    """Initialize Stripe routes with Flask app"""
    @stripe_bp.route('/checkout', methods=['POST'])
    def create_checkout():
        """Create a Stripe checkout session"""
        try:
            data = request.get_json()
            plan_id = data.get('plan_id')
            plan_name = data.get('plan_name')
            price_cents = data.get('price_cents')
            is_subscription = data.get('is_subscription', True)

            if not all([plan_id, plan_name, price_cents]):
                return jsonify({'success': False, 'error': 'Missing required fields'}), 400

            # Create Stripe checkout session
            result = stripe_processor.create_checkout_session(
                product_name=plan_name,
                price_cents=price_cents,
                is_subscription=is_subscription,
                interval='month'
            )

            if result.get('success'):
                logger.info(f"[STRIPE] Checkout session created: {plan_id}")
                return jsonify({
                    'success': True,
                    'session_id': result['session_id'],
                    'checkout_url': result['checkout_url']
                })
            else:
                logger.error(f"[STRIPE] Checkout creation failed: {result.get('error')}")
                return jsonify({
                    'success': False,
                    'error': result.get('error', 'Failed to create checkout')
                }), 400

        except Exception as e:
            logger.error(f"[STRIPE] Checkout endpoint error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @stripe_bp.route('/webhook', methods=['POST'])
    def webhook():
        """Handle Stripe webhooks"""
        try:
            payload = request.get_data()
            sig_header = request.headers.get('Stripe-Signature')

            # Verify and process webhook
            success, event_data = stripe_processor.verify_webhook(payload, sig_header)

            if success:
                logger.info(f"[STRIPE] Webhook processed: {event_data.get('event', 'unknown')}")

                # Process the event (fulfill order, send emails, etc.)
                _process_payment_event(event_data)

                return jsonify({'success': True}), 200
            else:
                logger.warning("[STRIPE] Webhook verification failed")
                return jsonify({'success': False, 'error': 'Verification failed'}), 400

        except Exception as e:
            logger.error(f"[STRIPE] Webhook error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @stripe_bp.route('/session/<session_id>', methods=['GET'])
    def get_session_details(session_id):
        """Get checkout session details"""
        try:
            session = stripe_processor.get_session(session_id)
            if session:
                return jsonify({
                    'success': True,
                    'session': session
                })
            else:
                return jsonify({'success': False, 'error': 'Session not found'}), 404
        except Exception as e:
            logger.error(f"[STRIPE] Session retrieval error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @stripe_bp.route('/cancel/<session_id>', methods=['POST'])
    def cancel_subscription_route(session_id):
        """Cancel a subscription"""
        try:
            session = stripe_processor.get_session(session_id)
            if not session or not session.get('subscription_id'):
                return jsonify({'success': False, 'error': 'Subscription not found'}), 404

            success, message = stripe_processor.cancel_subscription(session['subscription_id'])

            if success:
                logger.info(f"[STRIPE] Subscription cancelled by user: {session_id}")
                return jsonify({'success': True, 'message': message})
            else:
                return jsonify({'success': False, 'error': message}), 400

        except Exception as e:
            logger.error(f"[STRIPE] Cancellation error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    app.register_blueprint(stripe_bp)


def _process_payment_event(event_data):
    """Process payment events and trigger fulfillment"""
    event_type = event_data.get('event')

    if event_type == 'payment_completed':
        # Handle successful payment
        session_id = event_data.get('session_id')
        customer_email = event_data.get('customer_email')
        amount = event_data.get('amount_total')

        logger.info(f"[FULFILLMENT] Processing payment: {session_id} from {customer_email}")

        # TODO: Integrate with your fulfillment system
        # - Create user account
        # - Send welcome email
        # - Grant access to product
        # - Send training materials
        # - Record transaction in database

    elif event_type == 'subscription_updated':
        # Handle subscription changes
        subscription_id = event_data.get('subscription_id')
        logger.info(f"[FULFILLMENT] Subscription updated: {subscription_id}")
        # TODO: Update user subscription status in database

    elif event_type == 'payment_succeeded':
        # Handle one-time payments
        payment_id = event_data.get('payment_id')
        logger.info(f"[FULFILLMENT] Payment succeeded: {payment_id}")
        # TODO: Handle one-time service delivery
