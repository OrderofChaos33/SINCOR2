#!/usr/bin/env python3
"""Run one launch content cycle — all drafts go to review queue (no auto-publish)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

import yaml

from launch_content_engine import agent_spotlight, onchain_stats, seo_compare_pipeline
from launch_content_engine.review_queue import enqueue, list_drafts

ROTATION = ROOT / "config" / "topic_rotation.yaml"


def _next_pipeline() -> str:
    data = yaml.safe_load(ROTATION.read_text(encoding="utf-8"))
    rotation = data.get("rotation", ["agent_spotlight", "onchain_stats"])
    pending = list_drafts("pending")
    idx = len(pending) % len(rotation)
    return rotation[idx]


def run_once(pipeline: str | None = None) -> list[str]:
    pipeline = pipeline or _next_pipeline()
    ids: list[str] = []

    if pipeline == "agent_spotlight":
        title, body = agent_spotlight.draft_spotlight()
        channel = "twitter"
    elif pipeline == "build_log":
        title, body = agent_spotlight.draft_build_log()
        channel = "blog"
    elif pipeline == "referral_cta":
        title, body = agent_spotlight.draft_referral_cta()
        channel = "twitter"
    elif pipeline == "onchain_stats":
        title, body = "On-chain snapshot", onchain_stats.draft_post()
        channel = "farcaster"
    elif pipeline == "seo_compare":
        title, body = seo_compare_pipeline.draft_comparison()
        channel = "blog"
    elif pipeline == "campaign_kpi":
        from sincor2.launch_campaign import draft_campaign_kpi_post

        title, body = draft_campaign_kpi_post()
        channel = "twitter"
    else:
        title, body = agent_spotlight.draft_spotlight()
        channel = "twitter"

    draft_id = enqueue(pipeline, channel, body, title=title, meta={"auto": True})
    ids.append(draft_id)

    # Always add a Farcaster-sized on-chain draft every cycle
    if pipeline != "onchain_stats":
        fc_body = onchain_stats.draft_post()
        ids.append(enqueue("onchain_stats", "farcaster", fc_body, title="FC stats"))

    return ids


def main() -> None:
    p = argparse.ArgumentParser(description="SINCOR launch content cycle")
    p.add_argument("--pipeline", choices=[
        "agent_spotlight", "onchain_stats", "build_log", "referral_cta", "seo_compare", "campaign_kpi", "all"
    ])
    p.add_argument("--daemon", action="store_true")
    p.add_argument("--interval-hours", type=int, default=24)
    args = p.parse_args()

    if args.pipeline == "all":
        for pipe in ["agent_spotlight", "onchain_stats", "build_log", "referral_cta", "seo_compare"]:
            ids = run_once(pipe)
            print(json.dumps({"pipeline": pipe, "draft_ids": ids}))
        return

    if args.daemon:
        import time
        while True:
            ids = run_once(args.pipeline)
            print(json.dumps({"draft_ids": ids, "pending": len(list_drafts("pending"))}))
            time.sleep(args.interval_hours * 3600)
    else:
        pipe = args.pipeline or None
        ids = run_once(pipe)
        print(json.dumps({"pipeline": pipe or _next_pipeline(), "draft_ids": ids, "review": "/launch/review"}))


if __name__ == "__main__":
    main()