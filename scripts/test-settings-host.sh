#!/usr/bin/env bash
# Test the storage contract and real WebKit host without a Todoist account.
set -euo pipefail
cd -P "$(dirname "$0")/.."
RESOURCE_DIR="${1:-$PWD/Todoiste/Resources/Shortcuts}"
ADAPTER="${2:-$PWD/Todoiste/Resources/settings-storage-adapter.js}"
TEST_BUILD=build/native-settings-tests
mkdir -p "$TEST_BUILD"
xattr -w com.dropbox.ignored 1 build 2>/dev/null || true
node --test tests/settings-adapter.test.js
COMMON=( -module-cache-path "$TEST_BUILD/ModuleCache" Todoiste/ShortcutSettingsStore.swift Todoiste/ShortcutSettingsOptionsNormalizer.swift Todoiste/ShortcutSettingsReloadState.swift )
xcrun swiftc "${COMMON[@]}" tests/SettingsStoreTests.swift -o "$TEST_BUILD/store-tests" > "$TEST_BUILD/store-build.log" 2>&1 || {
  tail -80 "$TEST_BUILD/store-build.log"
  exit 1
}
"$TEST_BUILD/store-tests" "$RESOURCE_DIR/common.js"
xcrun swiftc "${COMMON[@]}" Todoiste/SettingsWindowCloseObserver.swift tests/SettingsWindowCloseTests.swift \
  -o "$TEST_BUILD/window-close-tests" > "$TEST_BUILD/window-close-build.log" 2>&1 || {
  tail -80 "$TEST_BUILD/window-close-build.log"
  exit 1
}
"$TEST_BUILD/window-close-tests"
xcrun swiftc "${COMMON[@]}" Todoiste/ShortcutResources.swift Todoiste/ShortcutSettingsDialogs.swift Todoiste/ShortcutSettingsWebView.swift \
  tests/SettingsHostTests.swift -o "$TEST_BUILD/host-tests" > "$TEST_BUILD/host-build.log" 2>&1 || {
  tail -80 "$TEST_BUILD/host-build.log"
  exit 1
}
"$TEST_BUILD/host-tests" "$RESOURCE_DIR" "$ADAPTER"
