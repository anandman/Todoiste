import importlib.util
import base64
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from sparkle_test_support import TestSigner

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/sparkle_appcast.py'


class AppcastTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('sparkle_appcast', SCRIPT)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.signer = TestSigner(self.directory)
        self.dmg = self.directory / 'Todoiste.dmg'
        self.dmg.write_bytes(b'final notarized and stapled fixture archive')
        self.feed = self.directory / 'appcast.xml'
        self.feed.write_bytes(self.signer.appcast(self.dmg.read_bytes()))

    def verify(self):
        return self.helper.verify(self.feed, self.dmg, '1.2.4', '8', self.signer.public_key, '13.0')

    def test_accepts_feed_and_archive_signed_by_embedded_public_key(self):
        self.verify()

    def test_rejects_tampered_feed_even_when_xml_still_valid(self):
        self.feed.write_bytes(self.feed.read_bytes().replace(b'<title>Todoiste', b'<title>Attacker'))
        with self.assertRaises(ValueError):
            self.verify()

    def test_rejects_tampered_archive_even_when_length_unchanged(self):
        self.dmg.write_bytes(b'X' * self.dmg.stat().st_size)
        with self.assertRaises(ValueError):
            self.verify()

    def test_rejects_different_public_key(self):
        other = self.directory / 'other'
        other.mkdir()
        with self.assertRaises(ValueError):
            self.helper.verify(self.feed, self.dmg, '1.2.4', '8', TestSigner(other).public_key, '13.0')

    def test_rejects_signed_but_wrong_version_build_url_length_or_os(self):
        for kwargs in ({'version':'1.2.5'}, {'build':'9'}, {'minimum':'12.0'}, {'length':1},
                       {'url':'https://github.com/anandman/Todoiste/releases/latest/download/Todoiste.dmg'},
                       {'url':'https://example.com/Todoiste.dmg'}):
            with self.subTest(kwargs=kwargs):
                self.feed.write_bytes(self.signer.appcast(self.dmg.read_bytes(), **kwargs))
                with self.assertRaises(ValueError):
                    self.verify()

    def test_rejects_missing_or_trailing_unsigned_signature_content(self):
        original = self.feed.read_bytes()
        for data in (original.split(b'<!-- sparkle-signatures:')[0], original + b'<!-- extra -->'):
            with self.subTest(data=data[-20:]):
                self.feed.write_bytes(data)
                with self.assertRaises(ValueError):
                    self.verify()

    def project(self, key=None):
        (self.directory / 'upstream.json').write_text(json.dumps({
            'todoiste_version':'1.2.4', 'todoiste_build':'8'}))
        (self.directory / 'project.yml').write_text(
            f'    macOS: "13.0"\n    SPARKLE_PUBLIC_KEY: "{key or self.signer.public_key}"\n')

    def test_unconfigured_production_public_key_fails_closed(self):
        self.project()
        (self.directory / 'project.yml').write_text('    macOS: "13.0"\n    SPARKLE_PUBLIC_KEY: ""\n')
        with patch.dict('os.environ', {'SPARKLE_PUBLIC_KEY':''}), self.assertRaises(ValueError):
            self.helper.configuration(self.directory)

    def test_generated_app_key_must_match_configured_release_key_before_signing(self):
        self.project()
        app = self.directory / 'Todoiste.app'
        (app / 'Contents').mkdir(parents=True)
        info = {'CFBundleIdentifier':'com.todoiste.app', 'CFBundleShortVersionString':'1.2.4',
                'CFBundleVersion':'8', 'SUPublicEDKey':'A'*43+'=',
                'SUFeedURL':self.helper.FEED_URL, 'LSMinimumSystemVersion':'13.0',
                'SURequireSignedFeed':True, 'SUVerifyUpdateBeforeExtraction':True,
                'SUAllowsAutomaticUpdates':False, 'SUEnableInstallerLauncherService':False,
                'SUEnableDownloaderService':False}
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        output = self.directory / 'generated.xml'
        with self.assertRaisesRegex(ValueError, 'Built app does not match'):
            self.helper.generate(self.directory, self.dmg, app, output)
        self.assertFalse(output.exists())

    def test_generated_app_must_disable_unused_xpc_services_before_signing(self):
        self.project()
        app = self.directory / 'Todoiste.app'
        (app / 'Contents').mkdir(parents=True)
        info = {'CFBundleIdentifier':'com.todoiste.app', 'CFBundleShortVersionString':'1.2.4',
                'CFBundleVersion':'8', 'SUPublicEDKey':self.signer.public_key,
                'SUFeedURL':self.helper.FEED_URL, 'LSMinimumSystemVersion':'13.0',
                'SURequireSignedFeed':True, 'SUVerifyUpdateBeforeExtraction':True,
                'SUAllowsAutomaticUpdates':False, 'SUEnableInstallerLauncherService':False,
                'SUEnableDownloaderService':False}
        for field in ('SUEnableInstallerLauncherService', 'SUEnableDownloaderService'):
            with self.subTest(field=field):
                altered = dict(info, **{field:True})
                (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(altered))
                with self.assertRaisesRegex(ValueError, 'Built app does not match'):
                    self.helper.generate(self.directory, self.dmg, app, self.directory / 'generated.xml')


class LocalReleasePreparationTests(unittest.TestCase):
    def test_generation_keeps_info_plist_and_retained_build_data(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            root, binaries = directory / 'repo', directory / 'bin'
            (root / 'scripts').mkdir(parents=True)
            binaries.mkdir()
            for name in ('release.sh', 'sparkle_appcast.py'):
                shutil.copyfile(SCRIPT.parent / name, root / 'scripts' / name)
            (root / 'project.yml').write_text('''options:
  deploymentTarget:
    macOS: "14.0"
settings:
  MARKETING_VERSION: "1.2.4"
  SPARKLE_PUBLIC_KEY: "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
''')
            (root / 'upstream.json').write_text(json.dumps({
                'todoiste_version':'1.2.4', 'todoiste_build':'8'}))
            (root / '.gitignore').write_text('build/\n')
            for arguments in (('init', '-b', 'main'), ('add', '.'),
                              ('-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                               'commit', '-m', 'Release fixture')):
                subprocess.run(['git', *arguments], cwd=root, check=True, capture_output=True)
            retained = ['SourcePackages/cache', 'local-tests/results', 'retained-preview/Todoiste.app']
            for name in retained + ['Build/stale-product', 'Intermediates.noindex/stale-object']:
                path = root / 'build' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('retained fixture')
            stubs = {
                'gh':'exit 0',
                'security':'''case "$1" in
  find-identity) echo '1) ABCDEF "Developer ID Application: Release Test (TESTTEAM)"' ;;
  find-certificate) echo 'certificate fixture' ;;
  *) exit 1 ;;
esac''',
                'openssl':"cat >/dev/null; echo 'subject=CN=Release Test,OU=TESTTEAM,O=Test'",
                'xcrun':'exit 0',
                'codesign':'exit 0',
                'xattr':'exit 0',
                'xcodegen':'''test "$1" = generate
mkdir -p build
printf 'generated Info.plist' > build/Todoiste-Info.plist''',
                # Exit at the native build boundary. No Xcode/native tests run.
                'xcodebuild':'''test -f build/Todoiste-Info.plist || exit 81
test ! -e build/Build/stale-product || exit 82
test ! -e build/Intermediates.noindex/stale-object || exit 83
test -f build/SourcePackages/cache || exit 84
test -f build/local-tests/results || exit 85
test -f build/retained-preview/Todoiste.app || exit 86
python3 - "$@" <<'PY'
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
root = Path.cwd().resolve()
derived = Path(args[args.index('-derivedDataPath') + 1]).resolve()
Path(os.environ['RELEASE_BUILD_RECORD']).write_text(json.dumps({'workspace':str(derived.parent)}))
assert not derived.is_relative_to(root), 'The app must be built outside FileProvider'
assert Path(args[args.index('-clonedSourcePackagesDirPath') + 1]).resolve() == root / 'build/SourcePackages'
PY
exit 79''',
            }
            for name, body in stubs.items():
                path = binaries / name
                path.write_text('#!/usr/bin/env bash\nset -e\n' + body + '\n')
                path.chmod(0o755)
            record = directory / 'release-build.json'
            env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                       RELEASE_BUILD_RECORD=str(record))
            result = subprocess.run(['bash', 'scripts/release.sh', '--dry-run'],
                                    cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(79, result.returncode, result.stdout + result.stderr)
            self.assertEqual('generated Info.plist', (root / 'build/Todoiste-Info.plist').read_text())
            self.assertFalse(Path(json.loads(record.read_text())['workspace']).exists(),
                             'The private workspace must be cleaned after build failure')
            for name in retained:
                self.assertEqual('retained fixture', (root / 'build' / name).read_text())

    def test_complete_dry_run_keeps_final_outputs_and_cleans_private_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root, tools = directory / 'repo', directory / 'bin'
            (root / 'scripts').mkdir(parents=True)
            tools.mkdir()
            for name in ('release.sh', 'store-release.py'):
                shutil.copyfile(SCRIPT.parent / name, root / 'scripts' / name)
            shutil.copytree(SCRIPT.parents[1] / 'Todoiste/Resources', root / 'Todoiste/Resources')
            shutil.copyfile(SCRIPT.parents[1] / 'upstream.json', root / 'upstream.json')
            (root / 'project.yml').write_text('MARKETING_VERSION: "1.2.4"\n')
            (root / '.gitignore').write_text('build/\n')
            script_bodies = {
                'sparkle_appcast.py':'''import json, os, sys
from pathlib import Path
if sys.argv[1] == 'check-configuration':
    raise SystemExit(0)
assert sys.argv[1] == 'generate'
app = Path(sys.argv[sys.argv.index('--app') + 1])
dmg = Path(sys.argv[sys.argv.index('--dmg') + 1])
record = json.loads(Path(os.environ['RELEASE_BUILD_RECORD']).read_text())
assert app == Path(record['app']) and app.is_dir()
assert dmg.resolve() == Path.cwd() / 'build/Build/Products/Release/Todoiste.dmg'
assert dmg.is_file()
dmg.with_name('appcast.xml').write_bytes(b'<rss>transport fixture</rss>')
''',
                'sign-sparkle.sh':'''import json, os, sys
from pathlib import Path
record = json.loads(Path(os.environ['RELEASE_BUILD_RECORD']).read_text())
assert sys.argv[1:] == [record['app'], 'Developer ID Application: Release Test (TESTTEAM)', 'TESTTEAM']
''',
                'test-settings-host.sh':'''import json, os, sys
from pathlib import Path
app = Path(json.loads(Path(os.environ['RELEASE_BUILD_RECORD']).read_text())['app'])
assert sys.argv[1:] == [str(app / 'Contents/Resources/Shortcuts'), str(app / 'Contents/Resources/settings-storage-adapter.js')]
assert all(Path(path).exists() for path in sys.argv[1:])
''',
                'test-updater.sh':'''import json, os, sys
from pathlib import Path
assert sys.argv[1:] == [json.loads(Path(os.environ['RELEASE_BUILD_RECORD']).read_text())['app']]
''',
            }
            for name, source in script_bodies.items():
                path = root / 'scripts' / name
                path.write_text('#!/usr/bin/env python3\n' + source)
                path.chmod(0o755)
            shell_tools = {
                'gh':'exit 0',
                'security':'''case "$1" in
 find-identity) echo '1) ABCDEF "Developer ID Application: Release Test (TESTTEAM)"' ;;
 find-certificate) echo 'certificate fixture' ;;
 *) exit 1 ;;
esac''',
                'openssl':"cat >/dev/null; echo 'subject=CN=Release Test,OU=TESTTEAM,O=Test'",
                'xcrun':'''if [ "$1" = notarytool ] && [ "$2" = submit ]; then echo 'status: Accepted'; fi
exit 0''',
                'xattr':'exit 0',
                'xcodegen':"mkdir -p build; echo 'generated Info.plist' > build/Todoiste-Info.plist",
                'lipo':'echo "x86_64 arm64"',
                'spctl':'exit 0',
            }
            python_tools = {
                'xcodebuild':'''import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
root = Path.cwd().resolve()
derived = Path(args[args.index('-derivedDataPath') + 1]).resolve()
assert not derived.is_relative_to(root)
assert Path(args[args.index('-clonedSourcePackagesDirPath') + 1]).resolve() == root / 'build/SourcePackages'
app = derived / 'Build/Products/Release/Todoiste.app'
shutil.copytree(root / 'Todoiste/Resources', app / 'Contents/Resources')
(app / 'Contents/_CodeSignature').mkdir()
(app / 'Contents/_CodeSignature/CodeResources').write_text('fixture seal')
(app / 'Contents/MacOS').mkdir()
(app / 'Contents/MacOS/Todoiste').write_text('fixture executable, never launched')
Path(os.environ['RELEASE_BUILD_RECORD']).write_text(json.dumps({'workspace':str(derived.parent), 'app':str(app)}))
''',
                'codesign':'''import sys
if '-dvv' in sys.argv:
    print('Authority=Developer ID Application\\nTeamIdentifier=TESTTEAM\\nflags=0x10000(runtime)')
elif '--entitlements' in sys.argv:
    print('<?xml version="1.0"?><plist version="1.0"><dict><key>com.apple.security.device.audio-input</key><true/></dict></plist>')
''',
                'hdiutil':'''import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
workspace = Path(json.loads(Path(os.environ['RELEASE_BUILD_RECORD']).read_text())['workspace'])
if args[0] == 'create':
    stage = Path(args[args.index('-srcfolder') + 1])
    assert stage == workspace / 'dmg'
    assert (stage / 'Todoiste.app').is_dir()
    assert (stage / 'Applications').is_symlink()
    Path(args[-1]).write_bytes(b'final notarized DMG transport fixture')
elif args[0] == 'attach':
    mount = Path(args[args.index('-mountpoint') + 1])
    assert mount.parent == workspace
    (mount / 'Todoiste.app').mkdir(parents=True)
elif args[0] == 'detach':
    shutil.rmtree(args[1])
else:
    raise AssertionError(args)
''',
            }
            for name, source in shell_tools.items():
                path = tools / name
                path.write_text('#!/bin/bash\nset -e\n' + source + '\n')
                path.chmod(0o755)
            for name, source in python_tools.items():
                path = tools / name
                path.write_text('#!/usr/bin/env python3\n' + source)
                path.chmod(0o755)
            for arguments in (('init', '-b', 'main'), ('add', '.'),
                              ('-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                               'commit', '-m', 'Release fixture')):
                subprocess.run(['git', *arguments], cwd=root, check=True, capture_output=True)
            cache = root / 'build/SourcePackages/cache'
            cache.parent.mkdir(parents=True)
            cache.write_text('retained package cache')
            record = directory / 'release-build.json'
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                       RELEASE_BUILD_RECORD=str(record))
            result = subprocess.run(['/bin/bash', 'scripts/release.sh', '--dry-run'],
                                    cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            outputs = root / 'build/Build/Products/Release'
            for name in ('Todoiste.dmg', 'appcast.xml'):
                path = outputs / name
                self.assertTrue(path.is_file())
                checksum = (root / 'build' / (name + '.sha256')).read_text().split()[0]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)
            self.assertIn('Artifact ready: build/Build/Products/Release/Todoiste.dmg', result.stdout)
            self.assertIn('Signed feed: build/Build/Products/Release/appcast.xml', result.stdout)
            self.assertFalse(Path(json.loads(record.read_text())['workspace']).exists())
            self.assertFalse((outputs / 'Todoiste.app').exists())
            self.assertEqual('retained package cache', cache.read_text())


class WorkflowSigningKeyImportTests(unittest.TestCase):
    def test_import_accepts_official_seed_and_legacy_formats_and_rejects_64_bytes(self):
        workflow = (SCRIPT.parents[1] / '.github/workflows/release.yml').read_text()
        importer = re.search(r"python3 - <<'PY'\n(.*?)^\s*PY$", workflow, re.M | re.S)
        self.assertIsNotNone(importer)
        source = textwrap.dedent(importer[1])
        for size, accepted in [(32, True), (64, False), (96, True)]:
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                key = base64.b64encode(bytes(size)).decode()
                env = dict(os.environ, RUNNER_TEMP=directory,
                           P12_BASE64='AA==', NOTARY_BASE64='AA==', SPARKLE_PRIVATE_KEY=key)
                result = subprocess.run(['python3', '-c', source], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(accepted, result.returncode == 0, result.stderr)
                path = Path(directory) / 'sparkle-eddsa.key'
                self.assertEqual(accepted, path.exists())
                if accepted:
                    self.assertEqual(key + '\n', path.read_text())


class SparklePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.app = self.directory / 'Todoiste.app'
        self.framework = self.app / 'Contents/Frameworks/Sparkle.framework'
        self.version = self.framework / 'Versions/B'
        for name in ('Autoupdate', 'Updater.app/Contents/MacOS/Updater', 'Sparkle',
                     'XPCServices/Downloader.xpc/Contents/MacOS/Downloader',
                     'XPCServices/Installer.xpc/Contents/MacOS/Installer'):
            path = self.version / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('preserved fixture: ' + name)
        (self.framework / 'Versions/Current').symlink_to('B')
        (self.framework / 'XPCServices').symlink_to('Versions/Current/XPCServices')
        self.info = {'CFBundleIdentifier':'com.todoiste.fixture',
                     'SUEnableInstallerLauncherService':False, 'SUEnableDownloaderService':False}
        self.entitlements = {'com.apple.security.device.audio-input':True}
        self.tools = self.directory / 'bin'
        self.tools.mkdir()
        tool = self.tools / 'codesign'
        tool.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
assert sys.argv[1:] == ['-d', '--entitlements', '-', '--xml', os.environ['FIXTURE_APP']]
if os.environ.get('FIXTURE_UNSIGNED'):
    sys.stderr.write('code object is not signed at all');sys.exit(1)
sys.stdout.buffer.write(Path(os.environ['FIXTURE_ENTITLEMENTS']).read_bytes())
''')
        tool.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.tools) + os.pathsep + os.environ['PATH'],
                        FIXTURE_APP=str(self.app.resolve()),
                        FIXTURE_ENTITLEMENTS=str(self.directory / 'entitlements.plist'))

    def prepare(self, unsigned=False):
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps(self.info))
        Path(self.env['FIXTURE_ENTITLEMENTS']).write_bytes(plistlib.dumps(self.entitlements))
        env = dict(self.env)
        if unsigned:
            env['FIXTURE_UNSIGNED'] = '1'
        return subprocess.run(['/bin/bash', str(SCRIPT.parent / 'prepare-sparkle.sh'), str(self.app)],
                              env=env, capture_output=True, text=True)

    def test_trims_only_unused_xpc_services_and_keeps_helpers_and_entitlements(self):
        before = {name:(self.version / name).read_bytes() for name in
                  ('Autoupdate', 'Updater.app/Contents/MacOS/Updater', 'Sparkle')}
        result = self.prepare()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.version / 'XPCServices').exists())
        self.assertFalse((self.framework / 'XPCServices').is_symlink())
        for name, data in before.items():
            self.assertEqual(data, (self.version / name).read_bytes())
        self.assertEqual(self.info, plistlib.loads((self.app / 'Contents/Info.plist').read_bytes()))
        self.assertEqual(self.entitlements, plistlib.loads(Path(self.env['FIXTURE_ENTITLEMENTS']).read_bytes()))
        self.assertEqual(0, self.prepare().returncode, 'Preparation must be idempotent')

    def test_refuses_sandboxed_host_before_removing_services(self):
        self.entitlements['com.apple.security.app-sandbox'] = True
        result = self.prepare()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('sandbox', result.stderr.lower())
        self.assertTrue((self.version / 'XPCServices/Installer.xpc').is_dir())

    def test_refuses_enabled_service_or_other_app_identity(self):
        for field, value in [('SUEnableInstallerLauncherService', True), ('SUEnableDownloaderService', True),
                             ('CFBundleIdentifier', 'org.other.App')]:
            with self.subTest(field=field):
                original = dict(self.info)
                self.info[field] = value
                result = self.prepare()
                self.assertNotEqual(0, result.returncode)
                self.assertIn('refus', result.stderr.lower())
                self.assertTrue((self.version / 'XPCServices/Installer.xpc').is_dir())
                self.info = original

    def test_unsigned_build_can_be_prepared(self):
        result = self.prepare(unsigned=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_refuses_linked_framework_instead_of_modifying_package_cache(self):
        cache = self.directory / 'original-sdk.framework'
        self.framework.rename(cache)
        self.framework.symlink_to(cache)
        result = self.prepare()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('package-cache', result.stderr)
        self.assertTrue((cache / 'Versions/B/XPCServices/Installer.xpc').is_dir())


class SparkleSigningScriptTests(unittest.TestCase):
    def test_system_bash_signs_with_omitted_or_present_optional_keychain(self):
        for explicit_keychain in (False, True):
            with self.subTest(explicit_keychain=explicit_keychain), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                app = directory / 'Todoiste.app'
                framework = app / 'Contents/Frameworks/Sparkle.framework'
                version = framework / 'Versions/B'
                binaries = ['Autoupdate', 'Updater.app/Contents/MacOS/Updater',
                            'XPCServices/Downloader.xpc/Contents/MacOS/Downloader',
                            'XPCServices/Installer.xpc/Contents/MacOS/Installer', 'Sparkle']
                for name in binaries:
                    path = version / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('Mach-O fixture for stubbed lipo/codesign')
                (framework / 'Versions/Current').symlink_to('B')
                (framework / 'XPCServices').symlink_to('Versions/Current/XPCServices')
                (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                    'CFBundleIdentifier':'com.todoiste.fixture', 'SUEnableInstallerLauncherService':False,
                    'SUEnableDownloaderService':False}))
                inspected = []
                if sys.platform == 'darwin':
                    # Real Apple xattrs are unsupported in Linux's xattr
                    # namespace; run this artifact behavior check on macOS.
                    outside = directory / 'outside-app'
                    outside.write_text('external symlink target')
                    (app / 'external-link').symlink_to(outside)
                    def set_attribute(path, name, value):
                        subprocess.run(['/usr/bin/xattr', '-wx', name, value.hex(), str(path)],
                                       check=True, capture_output=True)

                    def attributes(path):
                        names = subprocess.check_output(['/usr/bin/xattr', str(path)], text=True).splitlines()
                        return {name:bytes.fromhex(subprocess.check_output(
                            ['/usr/bin/xattr', '-px', name, str(path)], text=True)) for name in names}

                    for path in (app, version / 'Autoupdate', outside):
                        set_attribute(path, 'com.apple.FinderInfo', b'TEST' + bytes(28))
                    set_attribute(version / 'Autoupdate', 'com.apple.ResourceFork', b'fixture detritus')
                    for path in (app, version / 'Autoupdate'):
                        set_attribute(path, 'com.apple.quarantine', b'0081;00000000;Test;fixture')
                        set_attribute(path, 'com.todoiste.signing-test', b'preserve me')
                        preserved = {name:value for name, value in attributes(path).items()
                                     if name not in ('com.apple.FinderInfo', 'com.apple.ResourceFork')}
                        inspected.append((path, preserved))
                tools = directory / 'bin'
                tools.mkdir()
                (tools / 'lipo').write_text('#!/bin/bash\necho "x86_64 arm64"\n')
                (tools / 'codesign').write_text('''#!/usr/bin/env python3
import json, os, plistlib, sys
from pathlib import Path
args = sys.argv[1:]
records = Path(os.environ['SIGN_RECORDS'])
signed = json.loads(records.read_text()) if records.exists() else {}
target = args[-1]
if '--entitlements' in args:
    sys.stdout.buffer.write(plistlib.dumps({'com.apple.security.device.audio-input':True}));sys.exit()
if '--force' in args:
    assert args[args.index('--sign') + 1] == 'fixture Developer ID'
    assert args[args.index('--options') + 1] == 'runtime'
    assert '--timestamp' in args
    assert args[args.index('--preserve-metadata=identifier,entitlements')] is not None
    keychain = args[args.index('--keychain') + 1] if '--keychain' in args else ''
    assert keychain == os.environ['EXPECTED_KEYCHAIN']
    signed[target] = keychain
    records.write_text(json.dumps(signed))
elif '--verify' in args:
    assert target in signed, 'Every verification must follow signing'
elif '-dvv' in args:
    assert target in signed
    print('TeamIdentifier=FIXTURETEAM\\nflags=0x10000(runtime)')
else:
    raise AssertionError(args)
''')
                for tool in tools.iterdir():
                    tool.chmod(0o755)
                keychain = str(directory / 'fixture.keychain-db') if explicit_keychain else ''
                records = directory / 'signatures.json'
                env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                           SIGN_RECORDS=str(records), EXPECTED_KEYCHAIN=keychain)
                command = ['/bin/bash', str(SCRIPT.parent / 'sign-sparkle.sh'), str(app),
                           'fixture Developer ID', 'FIXTURETEAM']
                if explicit_keychain:
                    command.append(keychain)
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                expected = [version / 'Autoupdate', version / 'Updater.app',
                            framework, app]
                actual = json.loads(records.read_text())
                self.assertEqual([str(path).replace('/Versions/B/', '/Versions/Current/')
                                  for path in expected], list(actual))
                self.assertEqual({keychain}, set(actual.values()))
                for path, preserved in inspected:
                    self.assertEqual(preserved, attributes(path))
                if sys.platform == 'darwin':
                    self.assertEqual(b'TEST' + bytes(28), attributes(outside)['com.apple.FinderInfo'])


if __name__ == '__main__':
    unittest.main()
