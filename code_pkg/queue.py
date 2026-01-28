import json, time, redis
from .settings import settings

class RedisQueue:
    def __init__(self, name: str):
        self.name = name
        self.r = redis.from_url(settings.redis_url)

    def push(self, item: dict):
        self.r.lpush(self.name, json.dumps(item))

    def pop(self, timeout=1):
        res = self.r.brpop(self.name, timeout=timeout)
        if not res:
            return None
        _, payload = res
        return json.loads(payload)

# Simple factory for named queues
def get_queue(name: str) -> RedisQueue:
    return RedisQueue(name)
