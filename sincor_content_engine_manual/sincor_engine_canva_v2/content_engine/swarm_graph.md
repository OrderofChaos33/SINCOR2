# Swarm Graph (content_engine/swarm_graph.md)
Nodes:
- Ingestor → Planner → Drafters[N] → Scorer → Repairer → Reducer → Packager → Exporter → Syndicator → Telemetry

State passed between nodes:
- run_meta: run_id, seed, config_hash
- content_units: list of {id, family, tone, text, assets[], score}
- assets: normalized file refs (hero, logo, b-roll)
- canva_queue: [{template_key, slot_map, export_formats}]
- publish_queue: [{channel, payload_ref}]

Determinism:
- Fix seeds per shard.
- Hash configs; store in outputs/runlogs.
