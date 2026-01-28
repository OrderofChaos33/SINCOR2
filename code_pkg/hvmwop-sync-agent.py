import os
import openai
import yaml
from datetime import datetime

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.yml')
    if os.path.exists(config_path):
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)
    return {}

def main():
    print("🔁 HVMWOP Sync Agent starting...")

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        print("❌ Missing OPENAI_API_KEY")
        return

    openai.api_key = openai_api_key
    config = load_config()

    prompt = config.get("prompt", "What's the most lucrative and powerful sync action Court Jansma should take right now?")
    model = config.get("model", "gpt-4")

    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a high-powered sync agent helping Court Jansma maximize leverage, autonomy, and future impact."},
                {"role": "user", "content": prompt}
            ]
        )

        content = response['choices'][0]['message']['content']
        timestamp = datetime.utcnow().isoformat()
        print(f"✅ [{timestamp}] Agent Sync Response:\n{content}\n")

    except Exception as e:
        print(f"💥 Agent error: {e}")

if __name__ == "__main__":
    main()
