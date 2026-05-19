#!/usr/bin/env bash
set -euo pipefail
cd /Users/billkim/gym-tracker
set -a
source .env
set +a
exec python3 scripts/gmail_brief.py
