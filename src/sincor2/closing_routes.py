"""
SINCOR Sales Closing Routes
API endpoints for sales conversion pipeline
"""

from flask import Blueprint, request, jsonify
from sincor2.sales_closing_engine import SalesClosingEngine
import logging

closing_bp = Blueprint('closing', __name__)
closing_engine = SalesClosingEngine()

logger = logging.getLogger(__name__)


@closing_bp.route('/api/sales/response', methods=['POST'])
def receive_lead_response():
    """Receive a lead response"""
    try:
        data = request.json
        result = closing_engine.receive_lead_response(
            lead_id=data['lead_id'],
            response_text=data['response_text'],
            channel=data.get('channel', 'email'),
            outreach_agent=data.get('outreach_agent')
        )
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        logger.error(f"Error receiving lead response: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400


@closing_bp.route('/api/sales/opportunity/<opportunity_id>', methods=['GET'])
def get_opportunity(opportunity_id):
    """Get sales opportunity status"""
    try:
        result = closing_engine.get_opportunity_status(opportunity_id)
        if not result:
            return jsonify({'error': 'Opportunity not found'}), 404
        return jsonify({'success': True, 'opportunity': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@closing_bp.route('/api/sales/proposal', methods=['POST'])
def generate_proposal():
    """Generate and send proposal"""
    try:
        data = request.json
        result = closing_engine.generate_proposal(
            opportunity_id=data['opportunity_id'],
            lead_info=data.get('lead_info', {}),
            service_type=data.get('service_type', 'intelligence')
        )
        if not result:
            return jsonify({'error': 'Opportunity not found'}), 404
        return jsonify({'success': True, 'proposal': result})
    except Exception as e:
        logger.error(f"Error generating proposal: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400


@closing_bp.route('/api/sales/objection', methods=['POST'])
def handle_objection():
    """Handle sales objection with rule-based response"""
    try:
        data = request.json
        result = closing_engine.handle_objection(
            opportunity_id=data['opportunity_id'],
            objection_text=data['objection_text']
        )
        return jsonify({'success': True, 'objection_response': result})
    except Exception as e:
        logger.error(f"Error handling objection: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400


@closing_bp.route('/api/sales/close', methods=['POST'])
def close_deal():
    """Close a deal and create order"""
    try:
        data = request.json
        result = closing_engine.close_deal(
            opportunity_id=data['opportunity_id'],
            order_id=data['order_id'],
            deal_amount=data['deal_amount'],
            closing_agent=data.get('closing_agent')
        )
        return jsonify({'success': True, 'deal': result})
    except Exception as e:
        logger.error(f"Error closing deal: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400


@closing_bp.route('/api/agents/<agent_name>/commissions', methods=['GET'])
def get_agent_commissions(agent_name):
    """Get agent commission summary"""
    try:
        result = closing_engine.get_agent_commissions(agent_name)
        return jsonify({'success': True, 'commissions': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@closing_bp.route('/api/sales/pipeline', methods=['GET'])
def get_sales_pipeline():
    """Get sales pipeline summary by status"""
    try:
        import sqlite3
        db_path = 'data/sincor.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get counts by status
        cursor.execute('''
            SELECT status, COUNT(*) as count
            FROM sales_opportunities
            GROUP BY status
        ''')
        status_counts = {row[0]: row[1] for row in cursor.fetchall()}

        # Get total value in pipeline
        cursor.execute('''
            SELECT SUM(final_amount) FROM sales_opportunities
            WHERE status = 'closed_won'
        ''')
        closed_value = cursor.fetchone()[0] or 0

        # Get average deal size
        cursor.execute('''
            SELECT AVG(final_amount) FROM sales_opportunities
            WHERE final_amount > 0
        ''')
        avg_deal = cursor.fetchone()[0] or 0

        conn.close()

        return jsonify({
            'success': True,
            'pipeline': {
                'status_breakdown': status_counts,
                'closed_revenue': closed_value / 100,  # Convert cents to dollars
                'average_deal_size': avg_deal / 100,
                'total_opportunities': sum(status_counts.values())
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# Register blueprint in main app using:
# app.register_blueprint(closing_bp)
