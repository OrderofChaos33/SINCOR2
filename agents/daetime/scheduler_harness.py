import time
from agents.taskpool.taskpool_dispatcher import TaskDispatcher
from agents.taskpool_agents.taskpool.content_worker import format_blog_post
from agents.taskpool_agents.taskpool.image_worker import resize_banner
import datetime
import os
import json

LOG_DIR = "./logs/daetime"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# Register worker agents
dispatcher = TaskDispatcher()
dispatcher.register_worker("format_blog_post", format_blog_post)
dispatcher.register_worker("resize_banner", resize_banner)

# Sample tasks to simulate a live loop
sample_tasks = [
    {"type": "format_blog_post", "content": "New SEO Strategy\nBoost your rank with agent-powered posts."},
    {"type": "resize_banner", "image": "header.jpg", "size": "1280x720"}
]

def log_result(result):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOG_DIR, f"run_{timestamp}.log")
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

def main():
    print("Starting SINCOR scheduler harness...")
    for task in sample_tasks:
        result = dispatcher.assign_task(task)
        print("Task Result:", result)
        log_result({"task": task, "result": result})
        time.sleep(2)

if __name__ == "__main__":
    main()
