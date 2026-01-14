# Dispatcher logic for assigning tasks to agents.
import json
import threading
import time
from datetime import datetime

class TaskDispatcher:
    def __init__(self):
        self.workers = {}
        self.task_queue = []
        self.results = {}
        
    def register_worker(self, task_type, worker_func):
        """Register a worker function for a specific task type."""
        self.workers[task_type] = worker_func
        
    def assign_task(self, task):
        """Assign a task to the appropriate worker."""
        task_type = task.get("type")
        if task_type not in self.workers:
            return {"error": f"No worker registered for task type: {task_type}"}
            
        try:
            worker_func = self.workers[task_type]
            result = worker_func(task)
            return {"success": True, "result": result, "timestamp": datetime.now().isoformat()}
        except Exception as e:
            return {"error": str(e), "timestamp": datetime.now().isoformat()}
