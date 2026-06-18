"""Load prepared outreach emails for approval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from sincor2.data_paths import project_root


def load_outreach_emails() -> dict[str, Any]:
    path = project_root() / "config" / "outreach_emails.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    meta = raw.get("meta", {})
    emails: dict[str, Any] = {}
    for key, item in raw.items():
        if key == "meta" or not isinstance(item, dict):
            continue
        body = (item.get("body") or "").strip()
        for mk, mv in meta.items():
            body = body.replace("{{" + mk + "}}", str(mv))
        subject = item.get("subject", "")
        for mk, mv in meta.items():
            subject = subject.replace("{{" + mk + "}}", str(mv))
        emails[key] = {**item, "body": body, "subject": subject}
    return emails