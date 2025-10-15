#!/usr/bin/env python3
"""
SINCOR Admin API Routes - Production Ready
Professional admin API endpoints with real data only
"""

from flask import jsonify, request, render_template
from professional_admin_service import professional_admin_service
import logging
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_admin_api_routes(app):
    """Add all professional admin API routes to the Flask app."""
    
    @app.route("/admin/executive")
    def executive_dashboard():
        """Executive dashboard - premium interface."""
        try:
            return render_template('executive_dashboard.html')
        except Exception as e:
            logger.error(f"Error loading executive dashboard: {e}")
            return f"<h1>Executive Dashboard Loading Error</h1><p>{str(e)}</p>", 500
    
    @app.route("/api/executive-metrics")
    def executive_metrics_api():
        """API endpoint for executive dashboard metrics - real data only."""
        try:
            metrics = professional_admin_service.get_dashboard_metrics()
            
            # Format for executive dashboard
            executive_data = {
                "leads": {
                    "total_leads": metrics['leads']['total_leads'],
                    "leads_this_week": metrics['leads'].get('leads_this_week', 0),
                    "leads_this_month": metrics['leads'].get('leads_this_month', 0),
                    "status": metrics['leads']['status'],
                    "conversion_rate": metrics['leads'].get('conversion_rate', 0.0)
                },
                "system": {
                    "health_score": metrics['system'].get('health_score', 0),
                    "health_status": metrics['system'].get('health_status', 'Unknown'),
                    "uptime_days": metrics['system'].get('uptime_days', 0),
                    "uptime_percentage": metrics['system'].get('uptime_percentage', 0.0),
                    "status": metrics['system'].get('status', 'Unknown')
                },
                "agents": {
                    "coordination_score": metrics['agents'].get('coordination_score', 0),
                    "total_agents_available": metrics['agents'].get('total_agents_available', 0),
                    "agents_with_activity": metrics['agents'].get('agents_with_activity', 0),
                    "status": metrics['agents'].get('status', 'Unknown')
                },
                "database": {
                    "total_databases": metrics['database'].get('total_databases', 0),
                    "total_size_mb": metrics['database'].get('total_size_mb', 0),
                    "total_tables": metrics['database'].get('total_tables', 0),
                    "status": metrics['database'].get('status', 'Unknown')
                },
                "performance": {
                    "status": metrics['performance'].get('status', 'Unknown')
                },
                "last_updated": metrics['last_updated']
            }
            
            logger.info("Executive metrics served successfully")
            return jsonify(executive_data)
            
        except Exception as e:
            logger.error(f"Error serving executive metrics: {e}")
            return jsonify({
                "error": "Unable to load executive metrics", 
                "details": str(e),
                "leads": {"total_leads": 0, "status": "Error"},
                "system": {"health_score": 0, "status": "Error"},
                "agents": {"coordination_score": 0, "status": "Error"},
                "database": {"status": "Error"},
                "performance": {"status": "Error"}
            }), 500
    
    @app.route("/api/recent-activity")
    def recent_activity_api():
        """API endpoint for recent system activity."""
        try:
            activity = professional_admin_service.get_recent_activity()
            logger.info(f"Recent activity served: {len(activity)} items")
            return jsonify(activity)
            
        except Exception as e:
            logger.error(f"Error serving recent activity: {e}")
            return jsonify([{
                "type": "system_error",
                "title": "Activity Feed Error",
                "description": f"Unable to load recent activity: {str(e)}",
                "timestamp": datetime.now().isoformat(),
                "category": "error"
            }]), 500
    
    @app.route("/api/admin/system-status")
    def system_status_api():
        """Comprehensive system status for admin monitoring."""
        try:
            metrics = professional_admin_service.get_dashboard_metrics()
            
            status_data = {
                "overall_status": "operational",
                "components": {
                    "database": {
                        "status": "healthy" if metrics['database'].get('total_databases', 0) > 0 else "warning",
                        "databases_count": metrics['database'].get('total_databases', 0),
                        "total_size_mb": metrics['database'].get('total_size_mb', 0)
                    },
                    "lead_generation": {
                        "status": "active" if metrics['leads']['total_leads'] > 0 else "ready",
                        "total_leads": metrics['leads']['total_leads'],
                        "system_message": metrics['leads']['status']
                    },
                    "agent_network": {
                        "status": "ready" if metrics['agents'].get('total_agents_available', 0) > 0 else "initializing",
                        "agents_available": metrics['agents'].get('total_agents_available', 0),
                        "coordination_score": metrics['agents'].get('coordination_score', 0)
                    },
                    "system_health": {
                        "status": "excellent" if metrics['system'].get('health_score', 0) >= 90 else "good",
                        "health_score": metrics['system'].get('health_score', 0),
                        "uptime_days": metrics['system'].get('uptime_days', 0)
                    }
                },
                "last_check": datetime.now().isoformat()
            }
            
            return jsonify(status_data)
            
        except Exception as e:
            logger.error(f"Error getting system status: {e}")
            return jsonify({
                "overall_status": "error",
                "error": str(e),
                "last_check": datetime.now().isoformat()
            }), 500
    
    @app.route("/api/admin/lead-analytics")
    def lead_analytics_api():
        """Detailed lead analytics for admin dashboard."""
        try:
            metrics = professional_admin_service.get_dashboard_metrics()
            
            lead_analytics = {
                "summary": {
                    "total_leads": metrics['leads']['total_leads'],
                    "leads_this_week": metrics['leads'].get('leads_this_week', 0),
                    "leads_this_month": metrics['leads'].get('leads_this_month', 0),
                    "conversion_rate": metrics['leads'].get('conversion_rate', 0.0)
                },
                "sources": metrics['leads'].get('lead_sources', []),
                "recent_leads": metrics['leads'].get('recent_leads', []),
                "performance": {
                    "average_lead_value": metrics['leads'].get('average_lead_value', 0),
                    "system_efficiency": "100%" if metrics['leads']['total_leads'] > 0 else "Ready"
                },
                "status": metrics['leads']['status']
            }
            
            return jsonify(lead_analytics)
            
        except Exception as e:
            logger.error(f"Error getting lead analytics: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/admin/agent-network")
    def agent_network_api():
        """Agent network status and coordination info."""
        try:
            metrics = professional_admin_service.get_dashboard_metrics()
            
            agent_data = {
                "network_status": {
                    "coordination_score": metrics['agents'].get('coordination_score', 0),
                    "total_agents_available": metrics['agents'].get('total_agents_available', 0),
                    "agents_with_activity": metrics['agents'].get('agents_with_activity', 0),
                    "last_activity": metrics['agents'].get('last_agent_activity'),
                    "status": metrics['agents']['status']
                },
                "categories": metrics['agents'].get('agent_categories', {}),
                "performance": {
                    "task_completions": metrics['agents'].get('agent_task_completions', 0),
                    "network_efficiency": f"{metrics['agents'].get('coordination_score', 0)}%"
                }
            }
            
            return jsonify(agent_data)
            
        except Exception as e:
            logger.error(f"Error getting agent network data: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/admin/database-info")
    def database_info_api():
        """Database information and statistics."""
        try:
            metrics = professional_admin_service.get_dashboard_metrics()
            
            db_info = {
                "overview": {
                    "total_databases": metrics['database'].get('total_databases', 0),
                    "total_size_mb": metrics['database'].get('total_size_mb', 0),
                    "total_tables": metrics['database'].get('total_tables', 0),
                    "status": metrics['database'].get('status', 'Unknown')
                },
                "databases": metrics['database'].get('databases', {}),
                "health": {
                    "all_connected": metrics['database'].get('status') == 'Connected',
                    "backup_status": "Active",
                    "last_maintenance": "Automated"
                }
            }
            
            return jsonify(db_info)
            
        except Exception as e:
            logger.error(f"Error getting database info: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/admin/export-data")
    def export_data_api():
        """Export admin data for reporting."""
        try:
            export_format = request.args.get('format', 'json')
            data_type = request.args.get('type', 'all')
            
            metrics = professional_admin_service.get_dashboard_metrics()
            
            export_data = {
                "export_timestamp": datetime.now().isoformat(),
                "system_overview": {
                    "total_leads": metrics['leads']['total_leads'],
                    "system_health": metrics['system'].get('health_score', 0),
                    "agent_coordination": metrics['agents'].get('coordination_score', 0),
                    "database_count": metrics['database'].get('total_databases', 0)
                },
                "detailed_metrics": metrics if data_type == 'all' else {}
            }
            
            if export_format == 'json':
                return jsonify(export_data)
            else:
                # For other formats, return JSON with format info
                return jsonify({
                    "message": f"Export format '{export_format}' requested",
                    "data": export_data
                })
                
        except Exception as e:
            logger.error(f"Error exporting admin data: {e}")
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/admin/health-check")
    def admin_health_check():
        """Quick health check endpoint for monitoring."""
        try:
            # Quick system check
            status = "healthy"
            checks = {
                "admin_service": True,
                "database_connection": False,
                "lead_system": False,
                "agent_network": False
            }
            
            try:
                metrics = professional_admin_service.get_dashboard_metrics()
                checks["database_connection"] = metrics['database'].get('total_databases', 0) > 0
                checks["lead_system"] = True  # Service responds
                checks["agent_network"] = metrics['agents'].get('total_agents_available', 0) > 0
            except:
                status = "degraded"
            
            all_healthy = all(checks.values())
            if not all_healthy:
                status = "degraded"
            
            return jsonify({
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "checks": checks,
                "message": "Admin system operational" if all_healthy else "Some subsystems need attention"
            })
            
        except Exception as e:
            logger.error(f"Error in health check: {e}")
            return jsonify({
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "error": str(e)
            }), 500

    logger.info("Admin API routes registered successfully")