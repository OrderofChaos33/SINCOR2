"""
SINCOR Checkout Routes
Add to: src/sincor2/mvp_app.py or sincor_app.py
"""

from flask import Blueprint, request, jsonify, render_template
from src.sincor2.checkout_engine import CheckoutEngine
import os

checkout_bp = Blueprint('checkout', __name__)
checkout_engine = CheckoutEngine()

@checkout_bp.route('/checkout', methods=['GET'])
def checkout_page():
    """Render checkout page"""
    return render_template('checkout.html', stripe_key=os.environ.get('STRIPE_PUBLIC_KEY'))

@checkout_bp.route('/api/checkout', methods=['POST'])
def process_checkout():
    """Process payment and create order"""

    try:
        data = request.json

        # Validate required fields
        if not all(k in data for k in ['paymentMethodId', 'amount', 'orderData', 'billingData']):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        # Process payment through Stripe
        result = checkout_engine.process_payment(
            payment_method_id=data['paymentMethodId'],
            amount_cents=data['amount'],
            order_data=data['orderData'],
            billing_data=data['billingData']
        )

        return jsonify(result)

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@checkout_bp.route('/api/orders', methods=['GET'])
def list_orders():
    """List all paid orders (admin only)"""

    # TODO: Add authentication check
    status = request.args.get('status', 'paid')
    orders = checkout_engine.list_orders(status=status)

    return jsonify({
        'orders': [
            {
                'id': o[0],
                'customer': o[2],
                'company': o[3],
                'service': o[5],
                'amount': o[10] / 100,  # Convert cents to dollars
                'status': o[11],
                'created_at': o[15]
            }
            for o in orders
        ]
    })

@checkout_bp.route('/api/orders/<order_id>', methods=['GET'])
def get_order(order_id):
    """Get order details"""

    order = checkout_engine.get_order(order_id)
    if not order:
        return jsonify({'error': 'Order not found'}), 404

    return jsonify({
        'id': order[0],
        'customer': order[2],
        'company': order[3],
        'service': order[5],
        'tier': order[7],
        'amount': order[10] / 100,
        'status': order[11],
        'paid_at': order[12],
        'project_status': order[13]
    })

@checkout_bp.route('/api/revenue', methods=['GET'])
def revenue_summary():
    """Get revenue metrics dashboard"""

    summary = checkout_engine.get_revenue_summary()

    return jsonify({
        'total_revenue': summary['total_revenue'],
        'order_count': summary['order_count'],
        'average_order_value': summary['average_order_value'],
        'monthly_run_rate': summary['monthly_rate']
    })

@checkout_bp.route('/checkout/success', methods=['GET'])
def checkout_success():
    """Post-payment success page"""
    return render_template('checkout_success.html')

# Register blueprint in main app
# app.register_blueprint(checkout_bp)
