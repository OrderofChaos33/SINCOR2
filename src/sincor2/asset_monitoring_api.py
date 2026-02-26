"""
Asset Creation Monitoring Dashboard APIs
Real-time visibility into all asset workflows, quality, and value
"""

from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
import logging

logger = logging.getLogger('sincor2.asset_monitoring')

def create_asset_monitoring_routes(app):
    """Register asset monitoring API endpoints"""

    @app.route('/api/assets/dashboard', methods=['GET'])
    def assets_dashboard():
        """Main asset creation dashboard"""
        from sincor2.asset_orchestration_engine import asset_orchestrator

        summary = asset_orchestrator.get_asset_metrics_summary()

        return jsonify({
            'timestamp': datetime.utcnow().isoformat(),
            'status': 'operational',
            'metrics': summary,
            'quality_targets': {
                'standard': {'min': 0.75, 'target': 0.80},
                'premium': {'min': 0.80, 'target': 0.90},
                'enterprise': {'min': 0.90, 'target': 1.00}
            },
            'revenue_targets': {
                'monthly': 500000,
                'quarterly': 1500000,
                'annual': 6000000
            }
        }), 200

    @app.route('/api/assets/by-type', methods=['GET'])
    def assets_by_type():
        """Asset breakdown by type"""
        from sincor2.asset_orchestration_engine import asset_orchestrator

        registry = asset_orchestrator.asset_registry
        by_type = {}

        for asset in registry.values():
            asset_type = asset.asset_type.value
            if asset_type not in by_type:
                by_type[asset_type] = {
                    'count': 0,
                    'total_revenue': 0,
                    'avg_quality': 0,
                    'assets': []
                }

            by_type[asset_type]['count'] += 1
            by_type[asset_type]['total_revenue'] += asset.value_metrics.final_price
            by_type[asset_type]['avg_quality'] = (
                (by_type[asset_type]['avg_quality'] * (by_type[asset_type]['count'] - 1) +
                 asset.quality_metrics.overall_score) / by_type[asset_type]['count']
            )
            by_type[asset_type]['assets'].append({
                'asset_id': asset.asset_id,
                'status': asset.status.value,
                'quality': asset.quality_metrics.overall_score,
                'revenue': asset.value_metrics.final_price
            })

        return jsonify(by_type), 200

    @app.route('/api/assets/<asset_id>/quality', methods=['GET'])
    def asset_quality_details(asset_id: str):
        """Detailed quality metrics for an asset"""
        from sincor2.asset_orchestration_engine import asset_orchestrator

        if asset_id not in asset_orchestrator.asset_registry:
            return jsonify({'error': 'Asset not found'}), 404

        asset = asset_orchestrator.asset_registry[asset_id]
        metrics = asset.quality_metrics

        return jsonify({
            'asset_id': asset_id,
            'overall_score': metrics.overall_score,
            'quality_tier': asset.quality_tier.value,
            'dimensions': {
                'accuracy': metrics.accuracy,
                'completeness': metrics.completeness,
                'relevance': metrics.relevance,
                'timeliness': metrics.timeliness,
                'clarity': metrics.clarity,
                'actionability': metrics.actionability,
                'innovation': metrics.innovation,
                'depth': metrics.depth,
                'credibility': metrics.credibility
            },
            'feedback': asset.quality_feedback,
            'revision_count': asset.revision_count,
            'status': asset.status.value
        }), 200

    @app.route('/api/assets/<asset_id>/value', methods=['GET'])
    def asset_value_details(asset_id: str):
        """Value and revenue metrics for an asset"""
        from sincor2.asset_orchestration_engine import asset_orchestrator

        if asset_id not in asset_orchestrator.asset_registry:
            return jsonify({'error': 'Asset not found'}), 404

        asset = asset_orchestrator.asset_registry[asset_id]
        value = asset.value_metrics

        return jsonify({
            'asset_id': asset_id,
            'asset_type': asset.asset_type.value,
            'base_price': value.base_price,
            'multipliers': value.dynamic_multipliers,
            'final_price': value.final_price,
            'creation_cost': value.creation_cost,
            'gross_margin': value.gross_margin,
            'agent_attribution': value.agent_contribution,
            'client_value_delivered': value.client_value_delivered
        }), 200

    @app.route('/api/assets/quality-trends', methods=['GET'])
    def quality_trends():
        """Quality metrics trends over time"""
        from sincor2.asset_orchestration_engine import asset_orchestrator

        registry = asset_orchestrator.asset_registry
        assets_by_date = {}

        for asset in registry.values():
            date_key = asset.created_at.date().isoformat()
            if date_key not in assets_by_date:
                assets_by_date[date_key] = {
                    'count': 0,
                    'avg_quality': 0,
                    'quality_scores': []
                }

            assets_by_date[date_key]['count'] += 1
            assets_by_date[date_key]['quality_scores'].append(asset.quality_metrics.overall_score)

        # Calculate rolling averages
        for date_key in assets_by_date:
            scores = assets_by_date[date_key]['quality_scores']
            assets_by_date[date_key]['avg_quality'] = sum(scores) / len(scores) if scores else 0
            del assets_by_date[date_key]['quality_scores']

        return jsonify(assets_by_date), 200

    @app.route('/api/assets/revenue-analytics', methods=['GET'])
    def revenue_analytics():
        """Revenue generation analytics"""
        from sincor2.asset_orchestration_engine import asset_orchestrator

        registry = asset_orchestrator.asset_registry
        delivered = [a for a in registry.values() if a.status.value == 'delivered']

        if not delivered:
            return jsonify({
                'total_revenue': 0,
                'assets_delivered': 0,
                'avg_revenue_per_asset': 0,
                'avg_margin': 0
            }), 200

        total_revenue = sum(a.value_metrics.final_price for a in delivered)
        total_cost = sum(a.value_metrics.creation_cost for a in delivered)
        total_margin = total_revenue - total_cost

        return jsonify({
            'total_revenue': total_revenue,
            'assets_delivered': len(delivered),
            'avg_revenue_per_asset': total_revenue / len(delivered),
            'total_creation_cost': total_cost,
            'total_gross_profit': total_margin,
            'gross_margin_percent': (total_margin / total_revenue * 100) if total_revenue > 0 else 0,
            'revenue_by_asset_type': {
                asset.asset_type.value: {
                    'count': len([a for a in delivered if a.asset_type == asset.asset_type]),
                    'total': sum(a.value_metrics.final_price for a in delivered
                                if a.asset_type == asset.asset_type)
                }
                for asset in delivered
            }
        }), 200

    @app.route('/api/assets/agent-contribution', methods=['GET'])
    def agent_contribution():
        """Agent contributions to asset creation"""
        from sincor2.asset_orchestration_engine import asset_orchestrator

        registry = asset_orchestrator.asset_registry
        agent_stats = {}

        for asset in registry.values():
            for agent_id in asset.assigned_agents:
                if agent_id not in agent_stats:
                    agent_stats[agent_id] = {
                        'assets_created': 0,
                        'total_revenue_attributed': 0,
                        'avg_quality_contribution': 0,
                        'quality_scores': []
                    }

                agent_stats[agent_id]['assets_created'] += 1
                revenue_per_agent = asset.value_metrics.final_price / len(asset.assigned_agents)
                agent_stats[agent_id]['total_revenue_attributed'] += revenue_per_agent
                agent_stats[agent_id]['quality_scores'].append(asset.quality_metrics.overall_score)

        # Calculate averages
        for agent_id in agent_stats:
            scores = agent_stats[agent_id]['quality_scores']
            agent_stats[agent_id]['avg_quality_contribution'] = sum(scores) / len(scores) if scores else 0
            del agent_stats[agent_id]['quality_scores']

        return jsonify(agent_stats), 200

    @app.route('/api/assets/quality-gates-status', methods=['GET'])
    def quality_gates_status():
        """Status of quality control gates"""
        from sincor2.asset_orchestration_engine import asset_orchestrator

        registry = asset_orchestrator.asset_registry

        return jsonify({
            'total_assets': len(registry),
            'passed_all_gates': sum(1 for a in registry.values()
                                   if a.status.value in ['approved', 'delivered']),
            'passed_minimum_quality': sum(1 for a in registry.values()
                                         if a.quality_metrics.minimum_passed),
            'revision_needed': sum(1 for a in registry.values()
                                  if a.status.value == 'revision_needed'),
            'avg_revision_count': sum(a.revision_count for a in registry.values()) / len(registry)
                                  if registry else 0,
            'gates': {
                'minimum_quality_threshold': 0.75,
                'premium_threshold': 0.80,
                'enterprise_threshold': 0.90,
                'max_revisions': 3,
                'dimension_thresholds': {
                    'accuracy': 0.60,
                    'completeness': 0.75,
                    'relevance': 0.60
                }
            }
        }), 200

    logger.info("Asset monitoring routes registered")

# Register when app initializes
def init_asset_monitoring(app):
    create_asset_monitoring_routes(app)
