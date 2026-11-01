#!/usr/bin/env bash
# Re-sign the pinned Sparkle helper layout inside-out before sealing the host.
set -euo pipefail
APP="${1:?App bundle required}"
IDENTITY="${2:?Developer ID signing identity required}"
TEAM="${3:?Expected Team ID required}"
SIGN_COMMAND=(codesign --force --sign "$IDENTITY" --options runtime
  --timestamp --preserve-metadata=identifier,entitlements)
if [ -n "${4:-}" ]; then SIGN_COMMAND+=(--keychain "$4"); fi
"$(dirname "$0")/prepare-sparkle.sh" "$APP"
# Dropbox/Finder may attach metadata which codesign refuses. Remove only these
# two attributes from this build artifact; retain quarantine/provenance and do
# not follow links (including Sparkle's Versions/Current framework link).
python3 - "$APP" <<'PY'
import os
from pathlib import Path
import stat
import subprocess
import sys

if sys.platform != 'darwin':
    raise SystemExit(0)
app = Path(sys.argv[1])
if not stat.S_ISDIR(app.lstat().st_mode):
    raise ValueError('Signing requires a regular app directory')

def failed_walk(error):
    raise error

for directory, _, files in os.walk(app, followlinks=False, onerror=failed_walk):
    for path in [Path(directory), *(Path(directory) / name for name in files)]:
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            continue
        names = subprocess.check_output(['/usr/bin/xattr', str(path)], text=True).splitlines()
        for name in ('com.apple.FinderInfo', 'com.apple.ResourceFork'):
            if name in names:
                subprocess.run(['/usr/bin/xattr', '-d', name, str(path)], check=True)
PY
FRAMEWORK="$APP/Contents/Frameworks/Sparkle.framework"
VERSION="$FRAMEWORK/Versions/Current"
TARGETS=(
  "$VERSION/Autoupdate"
  "$VERSION/Updater.app"
  "$FRAMEWORK"
  "$APP"
)
BINARIES=(
  "$VERSION/Autoupdate"
  "$VERSION/Updater.app/Contents/MacOS/Updater"
  "$VERSION/Sparkle"
)
for binary in "${BINARIES[@]}"; do
  test -f "$binary"
  ARCHITECTURES=$(lipo -archs "$binary")
  [[ "$ARCHITECTURES" == *arm64* && "$ARCHITECTURES" == *x86_64* ]]
done
for target in "${TARGETS[@]}"; do
  "${SIGN_COMMAND[@]}" "$target"
  codesign --verify --strict --verbose=2 "$target"
  SIGNATURE=$(codesign -dvv "$target" 2>&1)
  grep -Fqx "TeamIdentifier=$TEAM" <<< "$SIGNATURE"
  grep -q 'flags=.*runtime' <<< "$SIGNATURE"
done
codesign --verify --deep --strict --verbose=2 "$APP"
