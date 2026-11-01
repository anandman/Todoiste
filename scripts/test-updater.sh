#!/usr/bin/env bash
# Real Sparkle checks use isolated localhost fixtures and never install an app.
set -euo pipefail
cd -P "$(dirname "$0")/.."
APP="${1:-$PWD/build/Build/Products/Release/Todoiste.app}"
TEST_BUILD="$PWD/build/updater-tests"
FRAMEWORKS="$APP/Contents/Frameworks"
MODULE_FRAMEWORKS="$(dirname "$APP")"
if [ ! -d "$MODULE_FRAMEWORKS/Sparkle.framework/Modules" ]; then
  MODULE_FRAMEWORKS="$PWD/build/SourcePackages/artifacts/sparkle/Sparkle/Sparkle.xcframework/macos-arm64_x86_64"
fi
mkdir -p "$TEST_BUILD"
xcrun swiftc -module-cache-path "$TEST_BUILD/ModuleCache" \
  Todoiste/UpdaterConfiguration.swift tests/UpdaterConfigurationTests.swift \
  -o "$TEST_BUILD/config-tests"
"$TEST_BUILD/config-tests"
xcrun swiftc -parse-as-library -module-cache-path "$TEST_BUILD/ModuleCache" -F "$MODULE_FRAMEWORKS" \
  -framework Sparkle -Xlinker -rpath -Xlinker "$FRAMEWORKS" \
  tests/SparkleFeedTests.swift -o "$TEST_BUILD/feed-tests"
python3 tests/run_sparkle_feed_checks.py "$APP" "$TEST_BUILD/feed-tests"
