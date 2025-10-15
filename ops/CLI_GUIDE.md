# CLI Guide
Minimal:
  python content_engine/gatherer.py --config engine.yaml
  python content_engine/expander.py --config engine.yaml --size 200 --seed 42
  python content_engine/compiler.py --config engine.yaml
  python content_engine/exporter.py --config engine.yaml --formats pdf,md,docx
  python content_engine/qa.py --config engine.yaml --strict

Full:
  python content_engine/expander.py --config engine.yaml --size 2000 --seed 1337
  python content_engine/compiler.py --config engine.yaml --shards 10
