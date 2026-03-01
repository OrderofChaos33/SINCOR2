"""
SINCOR Agent Commission API Routes
Endpoints for agents to view commissions and for admin to manage payouts
"""

from flask import Blueprint, request, jsonify
from sincor2.agent_commission_engine import AgentCommissionEngine
import logging

commission_bp = Blueprint('commissions', __name__)
commission_engine = AgentCommissionEngine()

logger = logging.getLogger(__name__)


@commission_bp.route('/api/agents/<agent_name>/balance', methods=['GET'])
def get_agent_balance(agent_name):
    """Get agent's current commission balance"""
    try:
        balance = commission_engine.get_agent_balance(agent_name)
        if not balance:
            return jsonify({'error': 'Agent not found'}), 404
        return jsonify({'success': True, 'balance': balance})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@commission_bp.route('/api/agents/<agent_name>/commissions', methods=['GET'])
def get_agent_commissions(agent_name):
    """Get agent's pending and paid commissions"""
    try:
        balance = commission_engine.get_agent_balance(agent_name)
        pending = commission_engine.get_agent_commissions_pending(agent_name)
        activity = commission_engine.record_commission_activity(agent_name)

        return jsonify({
            'success': True,
            'agent_name': agent_name,
            'balance': balance,
            'pending_count': len(pending),
            'total_activity': len(activity),
            'recent_commissions': activity[:20]
        })
    except Exception as e:
        logger.error(f"Error getting agent commissions: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400


@commission_bp.route('/api/agents/<agent_name>/history', methods=['GET'])
def get_commission_history(agent_name):
    """Get agent's commission history by month"""
    try:
        months = request.args.get('months', 3, type=int)
        history = commission_engine.get_commission_history(agent_name, months)

        return jsonify({
            'success': True,
            'agent_name': agent_name,
            'months': months,
            'monthly_history': history
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@commission_bp.route('/api/commissions/top-agents', methods=['GET'])
def get_top_agents():
    """Get top earning agents (admin endpoint)"""
    try:
        limit = request.args.get('limit', 10, type=int)
        agents = commission_engine.get_top_earning_agents(limit)

        return jsonify({
            'success': True,
            'top_agents': agents,
            'agent_count': len(agents)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@commission_bp.route('/api/commissions/dashboard', methods=['GET'])
def get_commission_dashboard():
    """Get commission dashboard (can be agent-specific or system-wide)"""
    try:
        agent_name = request.args.get('agent')

        dashboard = commission_engine.get_commission_dashboard(agent_name)

        return jsonify({
            'success': True,
            'dashboard': dashboard
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@commission_bp.route('/api/commissions/pending', methods=['GET'])
def get_pending_commissions():
    """Get all pending (unpaid) commissions (admin endpoint)"""
    try:
        agent_name = request.args.get('agent')
        limit = request.args.get('limit', 50, type=int)

        pending = commission_engine.get_agent_commissions_pending(agent_name, limit)

        return jsonify({
            'success': True,
            'pending_count': len(pending),
            'pending_commissions': [
                {
                    'id': p[0],
                    'agent': p[1],
                    'lead_id': p[2],
                    'touchpoint': p[3],
                    'amount': float(p[4]),
                    'earned_at': p[5]
                }
                for p in pending
            ]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@commission_bp.route('/api/commissions/pay', methods=['POST'])
def mark_commission_paid():
    """Mark a specific commission as paid (admin endpoint)"""
    try:
        data = request.json
        commission_id = data.get('commission_id')

        if not commission_id:
            return jsonify({'error': 'commission_id required'}), 400

        success = commission_engine.mark_commission_paid(commission_id)

        if success:
            return jsonify({
                'success': True,
                'message': f'Commission {commission_id} marked as paid'
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to mark commission paid'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@commission_bp.route('/api/commissions/payout', methods=['POST'])
def process_payout():
    """Process payout for pending commissions (admin endpoint)"""
    try:
        data = request.json or {}
        agent_name = data.get('agent')  # Optional - if not provided, pays all agents

        result = commission_engine.batch_payout(agent_name)

        return jsonify({
            'success': result['status'] == 'success' or result['status'] == 'no_pending',
            'payout_result': result
        })
    except Exception as e:
        logger.error(f"Error processing payout: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400


# Register blueprint in main app using:
# app.register_blueprint(commission_bp)
