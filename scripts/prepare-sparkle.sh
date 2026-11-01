#!/usr/bin/env bash
# Sparkle supports omitting both XPC services from an unsandboxed application.
# Operate only on the embedded framework in a built Todoiste app copy.
set -euo pipefail
python3 - "${1:?Built app bundle required}" <<'PY'
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

def refuse(message):
    raise SystemExit('Refusing Sparkle preparation: ' + message)

app = Path(sys.argv[1]).absolute()
if app.is_symlink() or not app.is_dir():
    refuse('expected a regular built app directory')
app = app.resolve()
info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
identifier = info.get('CFBundleIdentifier', '')
if not isinstance(identifier, str) or not identifier.startswith('com.todoiste.') or identifier == 'com.todoiste.':
    refuse('expected a Todoiste app identity')
for key in ('SUEnableInstallerLauncherService', 'SUEnableDownloaderService'):
    if info.get(key, False) is not False:
        refuse(key + ' must be disabled for unsandboxed packaging')
inspection = subprocess.run(['codesign', '-d', '--entitlements', '-', '--xml', str(app)],
                            capture_output=True)
if inspection.returncode:
    if b'code object is not signed at all' not in inspection.stderr:
        refuse('could not inspect the host signing entitlements')
    entitlements = {}
else:
    entitlements = plistlib.loads(inspection.stdout) if inspection.stdout.strip() else {}
if entitlements.get('com.apple.security.app-sandbox', False) is not False:
    refuse('sandboxed hosts require the installer service')
framework = app / 'Contents/Frameworks/Sparkle.framework'
if framework.is_symlink() or not framework.is_dir():
    refuse('expected an embedded framework copy, not a package-cache link')
framework = framework.resolve()
try:
    framework.relative_to(app)
    versions = (framework / 'Versions').resolve(strict=True)
    versions.relative_to(framework)
    version = (framework / 'Versions/Current').resolve(strict=True)
    version.relative_to(framework)
except (ValueError, FileNotFoundError):
    refuse('framework layout escapes the built app copy')
if version.parent != versions or not version.is_dir():
    refuse('unexpected Sparkle framework version layout')
for name in ('Autoupdate', 'Updater.app/Contents/MacOS/Updater', 'Sparkle'):
    if not (version / name).is_file():
        refuse('required Sparkle helper is missing: ' + name)
services = version / 'XPCServices'
alias = framework / 'XPCServices'
if services.is_symlink() or (services.exists() and not services.is_dir()):
    refuse('expected a regular embedded XPCServices directory')
if alias.is_symlink():
    if os.readlink(alias) != 'Versions/Current/XPCServices':
        refuse('unexpected XPCServices alias target')
elif alias.exists():
    refuse('expected the framework XPCServices alias')
# All host/layout checks precede these only two mutations. Helpers, framework
# cache, host Info.plist and host signing entitlements remain untouched.
if alias.is_symlink():
    alias.unlink()
if services.exists():
    shutil.rmtree(services)
print('Prepared Sparkle helpers for an unsandboxed Todoiste app.')
PY
