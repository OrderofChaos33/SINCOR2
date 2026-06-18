#!/usr/bin/env python3
"""Launch partner / KOL outreach CLI — July 7 pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sincor2.partner_outreach import (
    due_outreach,
    export_batch_markdown,
    list_partners,
    mark_contacted,
    pipeline_summary,
    sync_targets,
    update_status,
    write_batch_file,
)


def main() -> int:
    p = argparse.ArgumentParser(description="SINCOR launch partner outreach")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync", help="Sync yaml targets into CRM DB")
    sub.add_parser("summary", help="Pipeline counts")
    sub.add_parser("list", help="All partners + status")

    due_p = sub.add_parser("due", help="Today's outreach queue")
    due_p.add_argument("--limit", type=int, default=12)
    due_p.add_argument("--json", action="store_true")

    batch_p = sub.add_parser("batch", help="Write copy-paste outreach file")
    batch_p.add_argument("--limit", type=int, default=10)

    mark_p = sub.add_parser("mark", help="Update partner after outreach")
    mark_p.add_argument("partner_id")
    mark_p.add_argument("status", choices=[
        "contacted", "followup_1", "followup_2", "interested",
        "partnered", "declined", "no_response",
    ])
    mark_p.add_argument("--notes", default="")
    mark_p.add_argument("--channel", default="")

    args = p.parse_args()

    if args.cmd == "sync":
        n = sync_targets()
        print(f"Synced {n} new partner(s)")
        return 0

    if args.cmd == "summary":
        print(json.dumps(pipeline_summary(), indent=2))
        return 0

    if args.cmd == "list":
        for row in list_partners():
            print(
                f"{row['tier']:3} {row['status']:12} {row['id']:22} "
                f"{row.get('name', '')[:40]}"
            )
        return 0

    if args.cmd == "due":
        items = due_outreach(limit=args.limit)
        if args.json:
            slim = [
                {k: v for k, v in i.items() if k != "message"}
                for i in items
            ]
            print(json.dumps(slim, indent=2))
        else:
            for i in items:
                print(
                    f"\n=== {i['name']} ({i['id']}) "
                    f"[{i['tier']}] phase={i['phase']} ==="
                )
                print(f"Contact: {i.get('contact_method')} → {i.get('contact_url')}")
                print(i["message"])
        return 0

    if args.cmd == "batch":
        path = write_batch_file(limit=args.limit)
        print(f"Wrote {path}")
        print(export_batch_markdown(limit=args.limit)[:2000])
        if len(export_batch_markdown(limit=args.limit)) > 2000:
            print("\n... (see full file)")
        return 0

    if args.cmd == "mark":
        ok = mark_contacted(
            args.partner_id,
            status=args.status,
            notes=args.notes,
            channel=args.channel,
        )
        if not ok:
            print(f"Unknown partner: {args.partner_id}", file=sys.stderr)
            return 1
        print(f"Updated {args.partner_id} → {args.status}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())