#!/usr/bin/env bash
# Import only the Chrome Store-published version, pinned to its upstream tag.
# Also bumps Todoiste's patch/build versions and records source hashes.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/store-release.py prepare
