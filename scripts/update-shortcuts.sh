#!/usr/bin/env bash
#
# Pulls the latest mousetrap.js and todoist-shortcuts.js from
# https://github.com/mgsloan/todoist-shortcuts
#
# Usage:
#   ./scripts/update-shortcuts.sh
#
set -euo pipefail

REPO="mgsloan/todoist-shortcuts"
BRANCH="master"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/src"
DEST="$(cd "$(dirname "$0")/../Todoiste/Resources" && pwd)"

echo "Fetching latest scripts from ${REPO}..."

for file in mousetrap.js todoist-shortcuts.js; do
  echo "  Downloading ${file}..."
  curl -sL "${BASE_URL}/${file}" -o "${DEST}/${file}"
done

echo ""
echo "Updated files in ${DEST}:"
ls -la "${DEST}/mousetrap.js" "${DEST}/todoist-shortcuts.js"
echo ""
echo "Done. Review changes with: git diff Todoiste/Resources/"
