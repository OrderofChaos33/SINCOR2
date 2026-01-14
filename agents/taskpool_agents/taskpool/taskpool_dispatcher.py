class TaskDispatcher:
    def __init__(self):
        self.workers = {}

    def register_worker(self, task_type, worker_fn):
        self.workers[task_type] = worker_fn

    def assign_task(self, task):
        task_type = task.get("type")
        if task_type in self.workers:
            return self.workers[task_type](task)
        else:
            return {"error": f"No worker registered for task type: {task_type}"}
