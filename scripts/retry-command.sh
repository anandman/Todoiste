#!/usr/bin/env bash
# Retry transient service failures without weakening the final verification.
set -u
for attempt in 1 2 3; do
  if "$@"; then
    exit 0
  else
    status=$?
  fi
  if [ "$attempt" -eq 3 ]; then
    exit "$status"
  fi
  printf 'Command failed (exit %s); retrying attempt %s of 3.\n' "$status" "$((attempt + 1))" >&2
  sleep "$((attempt * 5))"
done
