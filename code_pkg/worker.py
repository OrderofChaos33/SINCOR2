import asyncio, json, signal, sys, time
from loguru import logger
from commons.queue import get_queue
from commons.model_router import generate
from commons.settings import settings

RUNNING = True

def handle_sig(*_):
    global RUNNING
    RUNNING = False
    logger.warning("Shutting down worker...")

async def process(payload: dict):
    # Example: enrich lead with a generated snippet & (optionally) vectorize later
    prompt = f"Create a 1-sentence irresistible detailing offer for: {json.dumps(payload)}"
    blurb = await generate(prompt)
    logger.info(f"LeadGen processed: blurb={blurb!r}")
    # TODO: store to DB or push downstream

async def main():
    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)
    q = get_queue("leadgen:queue")
    logger.info(f"LeadGen worker started. Redis={settings.redis_url}")
    while RUNNING:
        item = q.pop(timeout=1)
        if item is None:
            await asyncio.sleep(0.1)
            continue
        try:
            await process(item)
        except Exception as e:
            logger.exception(f"Failed processing item: {e}")

    logger.info("Worker stopped.")

if __name__ == "__main__":
    asyncio.run(main())
