#!/usr/bin/env python3
"""
SINCOR Agent Status Checker

Command-line tool to quickly check agent status and coordination.
"""

import os
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

class AgentStatusChecker:
    """Quick agent status checker for command line use."""
    
    def __init__(self):
        self.root_path = Path(__file__).parent
        self.log_dir = self.root_path / "logs"
        self.data_dir = self.root_path / "data"
    
    def check_daetime_agents(self):
        """Check daetime scheduler agent status."""
        daetime_dir = self.log_dir / "daetime"
        status = {
            'name': 'Daetime Scheduler',
            'status': 'inactive',
            'last_activity': 'never',
            'recent_tasks': [],
            'details': {}
        }
        
        if not daetime_dir.exists():
            status['status'] = 'not_configured'
            return status
        
        # Find most recent log file
        log_files = sorted(daetime_dir.glob("run_*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
        
        if not log_files:
            return status
        
        try:
            latest_log = log_files[0]
            
            # Check file age
            file_age = time.time() - latest_log.stat().st_mtime
            if file_age < 300:  # Less than 5 minutes
                status['status'] = 'active'
            elif file_age < 3600:  # Less than 1 hour
                status['status'] = 'recent'
            else:
                status['status'] = 'stale'
            
            # Read log content
            with open(latest_log, 'r') as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    status['details'] = data
                    status['last_activity'] = data.get('result', {}).get('timestamp', 'unknown')
                    
                    task = data.get('task', {})
                    if task:
                        status['recent_tasks'].append(f"{task.get('type', 'unknown')}: {task}")
                        
        except Exception as e:
            status['status'] = 'error'
            status['details'] = {'error': str(e)}
        
        return status
    
    def check_main_system(self):
        """Check main system agent coordination."""
        status = {
            'name': 'Main System',
            'status': 'unknown',
            'last_activity': 'never',
            'recent_activities': [],
            'agent_mentions': []
        }
        
        main_log = self.log_dir / "run.log"
        if not main_log.exists():
            status['status'] = 'no_logs'
            return status
        
        try:
            # Read recent log entries
            with open(main_log, 'r') as f:
                lines = f.readlines()
            
            if not lines:
                return status
            
            # Check recent activity (last 50 lines)
            recent_lines = lines[-50:]
            agent_activities = []
            
            for line in recent_lines:
                line = line.strip()
                if not line:
                    continue
                    
                # Look for agent-related entries
                if any(keyword in line.lower() for keyword in ['agent', 'started', 'completed', 'executed']):
                    agent_activities.append(line)
            
            status['recent_activities'] = agent_activities[-10:]  # Last 10 activities
            
            # Parse last line for timestamp
            if lines:
                last_line = lines[-1].strip()
                if last_line and '[' in last_line and ']' in last_line:
                    try:
                        timestamp_part = last_line.split('[')[1].split(']')[0]
                        status['last_activity'] = timestamp_part
                        
                        # Check if recent (within last hour)
                        last_time = datetime.fromisoformat(timestamp_part.replace('T', ' '))
                        time_diff = datetime.now() - last_time
                        
                        if time_diff < timedelta(minutes=5):
                            status['status'] = 'active'
                        elif time_diff < timedelta(hours=1):
                            status['status'] = 'recent'
                        else:
                            status['status'] = 'stale'
                            
                    except:
                        status['status'] = 'active' if agent_activities else 'idle'
            
            # Count agent mentions
            agent_keywords = ['SINCOR agents', 'agent started', 'routes added']
            status['agent_mentions'] = [line for line in recent_lines 
                                      for keyword in agent_keywords 
                                      if keyword.lower() in line.lower()]
                                      
        except Exception as e:
            status['status'] = 'error'
            status['details'] = {'error': str(e)}
        
        return status
    
    def check_business_intelligence(self):
        """Check business intelligence agent."""
        status = {
            'name': 'Business Intelligence',
            'status': 'unknown',
            'last_activity': 'never',
            'error_count': 0,
            'api_status': 'unknown'
        }
        
        # Check sincor_engine.log for BI agent activity
        engine_log = self.log_dir / "sincor_engine.log"
        if engine_log.exists():
            try:
                with open(engine_log, 'r') as f:
                    lines = f.readlines()
                
                recent_lines = lines[-20:] if lines else []
                
                # Look for Google API key errors
                api_errors = [line for line in recent_lines if 'Google API key not configured' in line]
                status['error_count'] = len(api_errors)
                
                if api_errors:
                    status['status'] = 'disabled'
                    status['api_status'] = 'not_configured'
                else:
                    status['status'] = 'configured'
                    status['api_status'] = 'ready'
                
                if recent_lines:
                    status['last_activity'] = 'recent'
                    
            except Exception as e:
                status['status'] = 'error'
                status['details'] = {'error': str(e)}
        
        return status
    
    def check_databases(self):
        """Check database connectivity and health."""
        databases = {
            'sincor_main.db': 'Main Database',
            'business_intel.db': 'Business Intelligence',
            'business_intelligence.db': 'BI Data', 
            'compliance.db': 'Compliance',
            'generated_content.db': 'Generated Content'
        }
        
        db_status = {}
        
        for db_file, db_name in databases.items():
            status = {
                'name': db_name,
                'status': 'unknown',
                'size': 0,
                'tables': 0,
                'accessible': False
            }
            
            db_path = self.data_dir / db_file
            
            if not db_path.exists():
                status['status'] = 'missing'
            else:
                try:
                    import sqlite3
                    status['size'] = db_path.stat().st_size
                    
                    conn = sqlite3.connect(str(db_path))
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                    tables = cursor.fetchall()
                    status['tables'] = len(tables)
                    status['accessible'] = True
                    status['status'] = 'healthy'
                    conn.close()
                    
                except Exception as e:
                    status['status'] = 'error'
                    status['error'] = str(e)
            
            db_status[db_file] = status
        
        return db_status
    
    def get_coordination_score(self, agent_statuses):
        """Calculate agent coordination score."""
        active_count = sum(1 for status in agent_statuses.values() 
                          if status.get('status') in ['active', 'recent'])
        
        total_agents = len(agent_statuses)
        base_score = (active_count / total_agents * 100) if total_agents > 0 else 0
        
        # Bonus for system activity
        if any('recent_activities' in status and status['recent_activities'] 
               for status in agent_statuses.values()):
            base_score += 10
        
        # Penalty for errors
        error_count = sum(1 for status in agent_statuses.values() 
                         if status.get('status') == 'error')
        base_score -= error_count * 15
        
        return max(0, min(100, base_score))
    
    def print_status_report(self):
        """Print comprehensive status report."""
        print("\n" + "="*60)
        print("SINCOR AGENT STATUS REPORT")
        print("="*60)
        print(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # Check all agent systems
        agents = {
            'daetime': self.check_daetime_agents(),
            'main_system': self.check_main_system(),
            'business_intel': self.check_business_intelligence()
        }
        
        # Print agent statuses
        print("AGENT STATUS:")
        print("-" * 40)
        
        status_icons = {
            'active': '[ACTIVE]',
            'recent': '[RECENT]', 
            'stale': '[STALE]',
            'inactive': '[INACTIVE]',
            'error': '[ERROR]',
            'disabled': '[DISABLED]',
            'not_configured': '[NOT_CONFIGURED]'
        }
        
        for agent_key, agent_data in agents.items():
            status = agent_data.get('status', 'unknown')
            icon = status_icons.get(status, '[UNKNOWN]')
            
            print(f"{icon} {agent_data['name']}: {status.upper()}")
            
            if agent_data.get('last_activity') != 'never':
                print(f"    Last Activity: {agent_data['last_activity']}")
            
            if agent_data.get('recent_activities'):
                print(f"    Recent Activities: {len(agent_data['recent_activities'])} events")
            
            if agent_data.get('recent_tasks'):
                print(f"    Recent Tasks: {len(agent_data['recent_tasks'])}")
            
            if agent_data.get('error_count', 0) > 0:
                print(f"    Errors: {agent_data['error_count']}")
            
            print()
        
        # Database status
        print("DATABASE STATUS:")
        print("-" * 40)
        
        databases = self.check_databases()
        for db_file, db_data in databases.items():
            status = db_data.get('status', 'unknown')
            icon = '[HEALTHY]' if status == 'healthy' else '[ERROR]' if status == 'error' else '[UNKNOWN]'
            
            print(f"{icon} {db_data['name']}: {status.upper()}")
            
            if db_data.get('accessible'):
                print(f"    Tables: {db_data['tables']}")
                print(f"    Size: {db_data['size']:,} bytes")
            
            if db_data.get('error'):
                print(f"    Error: {db_data['error']}")
            
            print()
        
        # Coordination analysis
        coord_score = self.get_coordination_score(agents)
        print("COORDINATION ANALYSIS:")
        print("-" * 40)
        print(f"Coordination Score: {coord_score:.1f}/100")
        
        if coord_score >= 80:
            print("[EXCELLENT] Agents working cohesively")
        elif coord_score >= 60:
            print("[GOOD] Most agents coordinating well")
        elif coord_score >= 40:
            print("[FAIR] Some coordination issues")
        else:
            print("[POOR] Significant coordination problems")
        
        print()
        
        # Recommendations
        print("RECOMMENDATIONS:")
        print("-" * 40)
        
        active_agents = [name for name, data in agents.items() 
                        if data.get('status') in ['active', 'recent']]
        
        if len(active_agents) == 0:
            print("• No agents currently active - check system startup")
        elif len(active_agents) < len(agents):
            inactive = [name for name, data in agents.items() 
                       if data.get('status') not in ['active', 'recent']]
            print(f"• Restart inactive agents: {', '.join(inactive)}")
        
        if agents.get('business_intel', {}).get('status') == 'disabled':
            print("• Configure Google API key for business intelligence features")
        
        error_agents = [name for name, data in agents.items() 
                       if data.get('status') == 'error']
        if error_agents:
            print(f"• Fix errors in: {', '.join(error_agents)}")
        
        db_issues = [name for name, data in databases.items() 
                    if data.get('status') != 'healthy']
        if db_issues:
            print(f"• Check database connectivity: {', '.join(db_issues)}")
        
        if coord_score > 80:
            print("• System running well - continue monitoring")
        
        print("\n" + "="*60)


if __name__ == "__main__":
    checker = AgentStatusChecker()
    checker.print_status_report()