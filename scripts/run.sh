#!/usr/bin/env bash
set -euo pipefail
uvicorn main:app --reload --host 0.0.0.0 --port 8011
