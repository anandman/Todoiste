#!/usr/bin/env bash
# Build and validate only. Publication is a separate, final workflow step.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SIGNING_IDENTITY:?Missing signing certificate SHA1}"
: "${SIGNING_KEYCHAIN:?Missing temporary keychain path}"
: "${NOTARY_KEY:?Missing notarization key path}"
: "${NOTARY_KEY_ID:?Missing notarization key ID}"
: "${NOTARY_ISSUER_ID:?Missing notarization issuer ID}"

APP=build/Build/Products/Release/Todoiste.app
DMG=build/Build/Products/Release/Todoiste.dmg
mkdir -p build
cleanup() {
  if mount | grep -Fq "$PWD/build/verify-mnt"; then
    hdiutil detach build/verify-mnt -quiet || true
  fi
}
trap cleanup EXIT
xcodegen generate
xcodebuild -project Todoiste.xcodeproj -scheme Todoiste \
  -configuration Release -destination 'generic/platform=macOS' \
  -derivedDataPath build ARCHS='arm64 x86_64' ONLY_ACTIVE_ARCH=NO \
  CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY="$SIGNING_IDENTITY" \
  DEVELOPMENT_TEAM=UX8BPNLPX5 ENABLE_HARDENED_RUNTIME=YES \
  CODE_SIGN_INJECT_BASE_ENTITLEMENTS=NO \
  OTHER_CODE_SIGN_FLAGS="--timestamp --keychain $SIGNING_KEYCHAIN" build > build/xcodebuild.log 2>&1 || {
    tail -100 build/xcodebuild.log
    exit 1
  }
codesign --verify --deep --strict --verbose=2 "$APP"
test -s "$APP/Contents/_CodeSignature/CodeResources"
ARCHS_BUILT=$(lipo -archs "$APP/Contents/MacOS/Todoiste")
[[ "$ARCHS_BUILT" == *arm64* && "$ARCHS_BUILT" == *x86_64* ]]
codesign -dvv "$APP" 2> build/signature.txt
grep -q 'flags=.*runtime' build/signature.txt
grep -q 'TeamIdentifier=UX8BPNLPX5' build/signature.txt
# Extract the actual embedded leaf and entitlements, rather than trusting the
# requested build settings. Preserve microphone access and reject debug signing.
codesign -d --extract-certificates=build/leaf "$APP"
ACTUAL=$(shasum -a 1 build/leaf0 | cut -d ' ' -f1 | tr '[:lower:]' '[:upper:]')
[[ "$ACTUAL" == "$SIGNING_IDENTITY" ]]
codesign -d --entitlements build/entitlements.txt "$APP"
python3 - <<'PY'
import json, plistlib
from pathlib import Path
data = Path('build/entitlements.txt').read_bytes()
if data.startswith(b'[Dict]'):
    # New codesign displays DER-only entitlements in this abstract form.
    # Fail closed on any unexpected entitlement/value until explicitly reviewed.
    assert data.decode().split() == ['[Dict]', '[Key]', 'com.apple.security.device.audio-input', '[Value]', '[Bool]', 'true'], 'Unexpected entitlements'
    ent = {'com.apple.security.device.audio-input': True}
else:
    start = data.find(b'<?xml')
    assert 0 <= start <= 8, 'Unrecognized entitlement format'
    ent = plistlib.loads(data[start:])
assert not ent.get('com.apple.security.get-task-allow'), 'Debugger entitlement present'
assert ent.get('com.apple.security.device.audio-input') is True, 'Microphone entitlement missing'
meta = json.loads(Path('upstream.json').read_text())
app = Path('build/Build/Products/Release/Todoiste.app')
info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
assert info['CFBundleShortVersionString'] == meta['todoiste_version']
assert str(info['CFBundleVersion']) == meta['todoiste_build']
for name in ['mousetrap.js', 'todoist-shortcuts.js', 'bootstrap.js']:
    assert (app / 'Contents/Resources' / name).read_bytes() == (Path('Todoiste/Resources') / name).read_bytes(), name
PY

# Check startup without accessing a user's Todoist account. Hosted runners have
# no saved session; this is a launch smoke test, not an authenticated UI test.
"$APP/Contents/MacOS/Todoiste" > build/launch.log 2>&1 &
APP_PID=$!
sleep 8
kill -0 "$APP_PID"
kill "$APP_PID"
wait "$APP_PID" || true

mkdir -p build/dmg
ditto "$APP" build/dmg/Todoiste.app
ln -s /Applications build/dmg/Applications
hdiutil create -volname Todoiste -srcfolder build/dmg -ov -format UDZO "$DMG"
codesign --sign "$SIGNING_IDENTITY" --keychain "$SIGNING_KEYCHAIN" --timestamp "$DMG"
codesign --verify --strict "$DMG"
NOTARY_ARGS=(--key "$NOTARY_KEY" --key-id "$NOTARY_KEY_ID" --issuer "$NOTARY_ISSUER_ID")
xcrun notarytool submit "$DMG" "${NOTARY_ARGS[@]}" --wait --timeout 20m --output-format json > build/notarization.json
if ! python3 -c 'import json; assert json.load(open("build/notarization.json"))["status"] == "Accepted"'; then
  ID=$(python3 -c 'import json; print(json.load(open("build/notarization.json"))["id"])')
  xcrun notarytool log "$ID" "${NOTARY_ARGS[@]}" || true
  exit 1
fi
./scripts/retry-command.sh xcrun stapler staple "$DMG"
./scripts/retry-command.sh xcrun stapler validate "$DMG"
hdiutil attach "$DMG" -nobrowse -quiet -mountpoint build/verify-mnt
spctl --assess --type execute --verbose=2 build/verify-mnt/Todoiste.app
hdiutil detach build/verify-mnt -quiet
shasum -a 256 "$DMG" > build/Todoiste.dmg.sha256
cat build/Todoiste.dmg.sha256
