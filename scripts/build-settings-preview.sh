#!/usr/bin/env bash
# Build an isolated local app; never change the pinned production asset tree.
set -euo pipefail
cd -P "$(dirname "$0")/.."
PREVIEW_SOURCE="$PWD/extension-previews/prefix-and-remapping-pr297-pr298"
LAUNCH=false
while (($#)); do
  case "$1" in
    --source) PREVIEW_SOURCE="$2"; shift 2 ;;
    --launch) LAUNCH=true; shift ;;
    *) echo "Usage: $0 [--source preview-directory] [--launch]" >&2; exit 2 ;;
  esac
done
mkdir -p build
xattr -w com.dropbox.ignored 1 build 2>/dev/null || true
python3 - "$PREVIEW_SOURCE" <<'PY'
import hashlib, json, sys
from pathlib import Path, PurePosixPath
root = Path(sys.argv[1])
meta = json.loads((root / 'SOURCE.json').read_text())
files = meta['files_sha256']
actual = {p.relative_to(root / 'extension').as_posix() for p in (root / 'extension').rglob('*') if p.is_file()}
assert actual == set(files), 'Preview inventory differs from its frozen source record'
for name, digest in files.items():
    path = PurePosixPath(name)
    assert not path.is_absolute() and '..' not in path.parts and '\\' not in name
    candidate = root / 'extension' / name
    assert not candidate.is_symlink() and hashlib.sha256(candidate.read_bytes()).hexdigest() == digest, name
print('Verified combined preview source:', meta['commit'])
PY
xcodegen generate
xcodebuild -project Todoiste.xcodeproj -scheme Todoiste -configuration Debug \
  -destination 'platform=macOS' -derivedDataPath build/settings-preview-derived \
  PRODUCT_BUNDLE_IDENTIFIER=com.todoiste.settings-preview CODE_SIGNING_ALLOWED=NO \
  build > build/settings-preview-build.log 2>&1 || {
    tail -100 build/settings-preview-build.log
    exit 1
  }
APP="$PWD/build/local-tests/Todoiste Settings Preview.app"
python3 - "$PREVIEW_SOURCE" "$APP" <<'PY'
import hashlib, json, plistlib, shutil, sys
from pathlib import Path
source, app = map(Path, sys.argv[1:])
assert app.parent == Path.cwd() / 'build/local-tests'
if app.exists(): shutil.rmtree(app)
app.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree('build/settings-preview-derived/Build/Products/Debug/Todoiste.app', app)
vendor = app / 'Contents/Resources/Shortcuts'
if vendor.exists(): shutil.rmtree(vendor)
shutil.copytree(source / 'extension', vendor)
info_path = app / 'Contents/Info.plist'
info = plistlib.loads(info_path.read_bytes())
assert info['CFBundleIdentifier'] == 'com.todoiste.settings-preview'
meta = json.loads((source / 'SOURCE.json').read_text())
info.update(CFBundleDisplayName='Todoiste Settings Preview', CFBundleName='Todoiste Settings Preview',
            TodoistePreviewSource=meta['commit'])
info_path.write_bytes(plistlib.dumps(info))
(app / 'Contents/Resources/PreviewSource.json').write_text(json.dumps(meta, indent=2)+'\n')
for name, digest in meta['files_sha256'].items():
    assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == digest, name
print('Packaged exact preview assets:', meta['commit'])
PY
codesign --force --sign - --entitlements Todoiste/Todoiste.entitlements "$APP"
codesign --verify --deep --strict "$APP"
./scripts/test-settings-host.sh "$APP/Contents/Resources/Shortcuts" "$APP/Contents/Resources/settings-storage-adapter.js"
printf '\nLocal preview ready: %s\n' "$APP"
if "$LAUNCH"; then open -n "$APP" --args --settings-only; fi
