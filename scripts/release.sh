#!/usr/bin/env bash
#
# Build, sign, notarize, staple, and publish a Todoiste release from this Mac.
#
# Signing and notarizing locally keeps the Developer ID certificate off GitHub
# entirely — no signing secrets in Actions. The result is a DMG that opens with
# no Gatekeeper prompt at all, rather than the "unidentified developer" warning
# an ad-hoc build produces.
#
# Usage:
#   ./scripts/release.sh              # version comes from project.yml
#   ./scripts/release.sh --dry-run    # build, sign, notarize; skip the upload
#
# One-time setup:
#   1. An active Apple Developer Program membership.
#   2. A *Developer ID Application* certificate in the login keychain, with its
#      private key. Xcode > Settings > Accounts > Manage Certificates > "+" >
#      Developer ID Application. An "Apple Development" certificate is NOT
#      sufficient — that one only signs for local development.
#   3. If running remotely: sign from a session attached to the logged-in
#      desktop, or unlock the login keychain first. Preflight tests this
#      before doing several minutes of work.
#   4. A notarytool credential profile:
#        xcrun notarytool store-credentials todoiste \
#          --key /path/to/AuthKey_XXXXXXXXXX.p8 \
#          --key-id XXXXXXXXXX \
#          --issuer <issuer-uuid from App Store Connect > Users and Access > Keys>
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

NOTARY_PROFILE="${NOTARY_PROFILE:-todoiste}"
DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }
step() { printf '\n==> %s\n' "$1"; }

# ---------------------------------------------------------------- preflight

step "Preflight"

command -v xcodegen >/dev/null || die "xcodegen not found. brew install xcodegen"
command -v gh >/dev/null || die "gh not found. brew install gh"

VERSION=$(awk -F'"' '/MARKETING_VERSION:/ {print $2; exit}' project.yml)
[ -n "$VERSION" ] || die "Could not read MARKETING_VERSION from project.yml"
TAG="v$VERSION"
echo "  version:  $VERSION  (tag $TAG)"

if [ -n "$(git status --porcelain)" ]; then
  die "Working tree is dirty. Commit or stash before releasing."
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
  die "Tag $TAG already exists. Bump MARKETING_VERSION in project.yml first."
fi

# A Developer ID Application identity is what makes notarization possible.
IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null \
  | grep "Developer ID Application" | head -1 | sed 's/.*"\(.*\)"/\1/')
if [ -z "$IDENTITY" ]; then
  cat >&2 <<'MSG'

ERROR: No "Developer ID Application" signing identity found in the keychain.

  Check what you do have:
      security find-identity -v -p codesigning

  An "Apple Development" certificate will not work — it signs for local
  development only and cannot be notarized. You need a Developer ID
  Application certificate, which requires an active Apple Developer Program
  membership. Create it in Xcode > Settings > Accounts > Manage Certificates,
  or at developer.apple.com > Certificates, Identifiers & Profiles.

  Which machine: the Developer ID certificate lives on minion, the primary
  build machine. Cut releases there.

  This machine can still build and run the app normally — signing is only
  needed to publish:
      xcodebuild -project Todoiste.xcodeproj -scheme Todoiste \\
        -configuration Release -derivedDataPath build build

  As a fallback, .github/workflows/release.yml can publish an ad-hoc signed
  DMG (Actions > Build and Release > Run workflow). Users then have to
  right-click > Open on first launch.
MSG
  exit 1
fi
echo "  identity: $IDENTITY"

# Team ID is the OU field of the signing certificate.
TEAM_ID=$(security find-certificate -c "$IDENTITY" -p 2>/dev/null \
  | openssl x509 -noout -subject 2>/dev/null | sed -n 's/.*OU=\([^,]*\).*/\1/p')
[ -n "$TEAM_ID" ] || die "Could not determine Team ID from the signing certificate"
echo "  team:     $TEAM_ID"

if ! xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1; then
  die "No notarytool credential profile '$NOTARY_PROFILE'. See the setup notes at the top of this script."
fi
echo "  notary:   profile '$NOTARY_PROFILE' OK"

# Signing smoke test. Over SSH, or with a locked keychain, codesign fails with
# "User interaction is not allowed" only once the real build is done — several
# minutes in. Fail here instead, with the fix.
SMOKE=$(mktemp -d)/probe
cp /bin/echo "$SMOKE"
if ! codesign --force --sign "$IDENTITY" "$SMOKE" >/dev/null 2>"$SMOKE.err"; then
  if grep -qiE 'user interaction is not allowed|errSecInternalComponent' "$SMOKE.err"; then
    cat >&2 <<MSG

ERROR: codesign cannot reach the signing key in this session.

  This is the usual failure when releasing over SSH: the login keychain is
  locked, or the private key's ACL does not permit non-interactive signing.

  Best fix — run this script from a session attached to the logged-in desktop
  (Screen Sharing, or a Claude Code session running in the GUI session) where
  the login keychain is already unlocked.

  Over SSH, unlock it first:
      security unlock-keychain ~/Library/Keychains/login.keychain-db

  If it still fails, the key's ACL needs to allow codesign non-interactively.
  This prompts for the keychain password and only needs doing once:
      security set-key-partition-list -S apple-tool:,apple:,codesign: \\
        -s -k <keychain-password> ~/Library/Keychains/login.keychain-db

  codesign said:
$(sed 's/^/      /' "$SMOKE.err")
MSG
  else
    printf '\nERROR: test signature failed with identity "%s":\n' "$IDENTITY" >&2
    sed 's/^/  /' "$SMOKE.err" >&2
  fi
  rm -rf "$(dirname "$SMOKE")"
  exit 1
fi
rm -rf "$(dirname "$SMOKE")"
echo "  signing:  test signature OK"

# ---------------------------------------------------------------- build

step "Building Release"
xcodegen generate >/dev/null
rm -rf build
mkdir -p build
# The repo lives in Dropbox and is built on more than one Mac. Without this,
# ~100MB of DerivedData syncs between machines and Dropbox writes "conflicted
# copy" files inside it when two builds overlap, which corrupts builds.
xattr -w com.dropbox.ignored 1 build 2>/dev/null || true
xcodebuild -project Todoiste.xcodeproj -scheme Todoiste \
  -configuration Release \
  -derivedDataPath build \
  CODE_SIGN_STYLE=Manual \
  CODE_SIGN_IDENTITY="$IDENTITY" \
  DEVELOPMENT_TEAM="$TEAM_ID" \
  ENABLE_HARDENED_RUNTIME=YES \
  OTHER_CODE_SIGN_FLAGS="--timestamp" \
  build \
  | tail -3

APP="build/Build/Products/Release/Todoiste.app"
[ -d "$APP" ] || die "Build did not produce $APP"

# ---------------------------------------------------------------- verify

step "Verifying signature"
codesign --verify --deep --strict --verbose=2 "$APP"
[ -f "$APP/Contents/_CodeSignature/CodeResources" ] \
  || die "Bundle has no sealed resources — Gatekeeper would call this damaged"
codesign -dvv "$APP" 2>&1 | grep -E 'Authority|TeamIdentifier|flags' | sed 's/^/  /'

# ---------------------------------------------------------------- package

step "Building DMG"
STAGE="build/dmg"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="build/Build/Products/Release/Todoiste.dmg"
rm -f "$DMG"
hdiutil create -volname Todoiste -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
codesign --sign "$IDENTITY" --timestamp "$DMG"
echo "  $DMG ($(du -h "$DMG" | cut -f1))"

# ---------------------------------------------------------------- notarize

step "Notarizing (this can take a few minutes)"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait

step "Stapling"
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"

# The real test: what Gatekeeper says about the app a user would drag out.
step "Gatekeeper check"
hdiutil attach "$DMG" -nobrowse -quiet -mountpoint build/verify-mnt
spctl -a -t exec -vv build/verify-mnt/Todoiste.app 2>&1 | sed 's/^/  /'
hdiutil detach build/verify-mnt -quiet

# ---------------------------------------------------------------- publish

if [ "$DRY_RUN" = true ]; then
  step "Dry run — skipping tag and upload"
  echo "  Artifact ready: $DMG"
  exit 0
fi

step "Publishing $TAG"
git tag -a "$TAG" -m "Todoiste $TAG"
git push origin "$TAG"

# release.yml is workflow_dispatch-only, so the tag push does not trigger a
# competing ad-hoc build. This notarized DMG is the only asset.
gh release create "$TAG" "$DMG" --title "$TAG" --generate-notes
gh release view "$TAG" --json url --jq '.url' | sed 's/^/  Published: /'
