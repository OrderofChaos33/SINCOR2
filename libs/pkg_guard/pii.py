import re
import json
from typing import Any, Dict

EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
PHONE_PATTERN = re.compile(r'\+?1?[-.\s]?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}')

def redact_email(text: str) -> str:
    """Replace emails with ***@***.*** format"""
    if not text:
        return text
    return EMAIL_PATTERN.sub(lambda m: f"***@{m.group().split('@')[1].split('.')[0]}***", text)

def redact_phone(text: str) -> str:
    """Replace phone numbers with ***-***-XXXX format"""
    if not text:
        return text
    return PHONE_PATTERN.sub(lambda m: f"***-***-{m.group()[-4:]}", text)

def redact_pii(text: str) -> str:
    """Remove both emails and phones from text"""
    if not text:
        return text
    text = redact_email(text)
    text = redact_phone(text)
    return text

def safe_log(data: Dict[str, Any]) -> str:
    """Safe logging that strips PII from dict values"""
    safe_data = {}
    for key, value in data.items():
        if isinstance(value, str):
            safe_data[key] = redact_pii(value)
        elif isinstance(value, dict):
            safe_data[key] = {k: redact_pii(v) if isinstance(v, str) else v for k, v in value.items()}
        else:
            safe_data[key] = value
    return json.dumps(safe_data, indent=2)