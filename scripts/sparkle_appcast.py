#!/usr/bin/env python3
"""Generate and independently verify Todoiste's signed Sparkle release pair.

Generation uses the pinned Sparkle tools; verification uses only the embedded
public key and OpenSSL 3+, so publication needs no private signing material.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
FEED_URL = 'https://github.com/anandman/Todoiste/releases/latest/download/appcast.xml'
SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'
KEYCHAIN_ACCOUNT = 'com.todoiste.app.Sparkle'
SIGNING_BLOCK = re.compile(rb'<!-- sparkle-signatures:\nedSignature: ([A-Za-z0-9+/]{86}==)\nlength: ([0-9]+)\n-->\n\Z')


def decoded(value, size, name):
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError(f'Invalid {name}') from error
    if len(result) != size:
        raise ValueError(f'Invalid {name} length')
    return result


def openssl():
    candidates = [os.environ.get('SPARKLE_OPENSSL'), shutil.which('openssl'),
                  '/opt/homebrew/opt/openssl@3/bin/openssl', '/usr/local/opt/openssl@3/bin/openssl']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            result = subprocess.run([candidate, 'version'], capture_output=True, text=True)
            if result.returncode == 0 and re.match(r'OpenSSL [3-9]\.', result.stdout):
                return candidate
    raise ValueError('OpenSSL 3+ is required for public-key verification (brew install openssl@3)')


def verify_signature(path, signature, public_key):
    signature = decoded(signature, 64, 'Ed25519 signature')
    public_key = decoded(public_key, 32, 'Sparkle public key')
    with tempfile.TemporaryDirectory(prefix='todoiste-public-verify-') as directory:
        directory = Path(directory)
        # RFC 8410 SubjectPublicKeyInfo prefix for a raw Ed25519 public key.
        key_path, signature_path = directory / 'public.der', directory / 'signature'
        key_path.write_bytes(bytes.fromhex('302a300506032b6570032100') + public_key)
        signature_path.write_bytes(signature)
        result = subprocess.run([openssl(), 'pkeyutl', '-verify', '-pubin', '-inkey', str(key_path),
                                 '-keyform', 'DER', '-rawin', '-in', str(path),
                                 '-sigfile', str(signature_path)], capture_output=True)
        if result.returncode:
            raise ValueError(f'Invalid Ed25519 signature for {Path(path).name}')


def verify(feed, dmg, version, build, public_key, minimum_os):
    feed, dmg = Path(feed), Path(dmg)
    if any(path.is_symlink() or not path.is_file() or path.stat().st_size == 0 for path in (feed, dmg)):
        raise ValueError('Missing, empty or symlinked Sparkle release asset')
    data = feed.read_bytes()
    if len(data) > 1_000_000:
        raise ValueError('Appcast exceeds size limit')
    block = SIGNING_BLOCK.search(data)
    if not block or int(block[2]) != block.start() or data.count(b'<!-- sparkle-signatures:') != 1:
        raise ValueError('Missing or invalid signed appcast envelope')
    content = data[:block.start()]
    with tempfile.TemporaryDirectory(prefix='todoiste-feed-verify-') as directory:
        path = Path(directory) / 'appcast-content.xml'
        path.write_bytes(content)
        verify_signature(path, block[1], public_key)
    # Forbid entity declarations before parsing untrusted XML.
    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise ValueError('Unsupported appcast XML declarations')
    try:
        rss = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError('Invalid appcast XML') from error
    items = rss.findall('./channel/item')
    if rss.tag != 'rss' or len(rss.findall('channel')) != 1 or len(items) != 1:
        raise ValueError('Expected exactly one release in the appcast')
    item = items[0]
    for name, expected in [('version', build), ('shortVersionString', version), ('minimumSystemVersion', minimum_os)]:
        fields = item.findall(SPARKLE + name)
        if len(fields) != 1 or fields[0].text != expected:
            raise ValueError(f'Appcast {name} does not match the verified app')
    enclosures = item.findall('enclosure')
    if len(enclosures) != 1 or item.find(SPARKLE + 'deltas') is not None:
        raise ValueError('Expected one full update enclosure')
    enclosure = enclosures[0]
    expected_url = f'https://github.com/anandman/Todoiste/releases/download/v{version}/Todoiste.dmg'
    if (enclosure.get('url') != expected_url or enclosure.get('type') != 'application/octet-stream' or
            enclosure.get('length') != str(dmg.stat().st_size)):
        raise ValueError('Appcast enclosure URL, type or length does not match the final DMG')
    verify_signature(dmg, enclosure.get(SPARKLE + 'edSignature'), public_key)


def configuration(root):
    metadata = json.loads((root / 'upstream.json').read_text())
    project = (root / 'project.yml').read_text()
    keys = re.findall(r'^\s*SPARKLE_PUBLIC_KEY:\s*[\"\']?([A-Za-z0-9+/]{43}=)[\"\']?\s*$', project, re.M)
    if os.environ.get('SPARKLE_PUBLIC_KEY'):
        keys = [os.environ['SPARKLE_PUBLIC_KEY']]
    floors = re.findall(r'^\s*macOS:\s*[\"\']([0-9.]+)[\"\']\s*$', project, re.M)
    if len(keys) != 1 or len(floors) != 1:
        raise ValueError('Expected exactly one Sparkle public key and macOS deployment target in project.yml')
    decoded(keys[0], 32, 'Sparkle public key')
    return metadata['todoiste_version'], str(metadata['todoiste_build']), keys[0], floors[0]


def tools_directory(root):
    candidates = [os.environ.get('SPARKLE_BIN_DIR'),
                  root / 'build/SourcePackages/artifacts/sparkle/Sparkle/bin',
                  root / 'build/sparkle-tools/bin']
    for candidate in candidates:
        if candidate and all((Path(candidate) / name).is_file() for name in ('generate_appcast', 'sign_update')):
            return Path(candidate).resolve()
    raise ValueError('Pinned Sparkle tools are missing; resolve the Sparkle package first')


def generate(root, dmg, app, output):
    version, build, public_key, minimum_os = configuration(root)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    expected = {'CFBundleIdentifier':'com.todoiste.app', 'CFBundleShortVersionString':version,
                'CFBundleVersion':build, 'SUPublicEDKey':public_key, 'SUFeedURL':FEED_URL,
                'LSMinimumSystemVersion':minimum_os, 'SURequireSignedFeed':True,
                'SUVerifyUpdateBeforeExtraction':True, 'SUAllowsAutomaticUpdates':False,
                'SUEnableInstallerLauncherService':False, 'SUEnableDownloaderService':False}
    if any(info.get(key) != value for key, value in expected.items()):
        raise ValueError('Built app does not match the release Sparkle configuration')
    tools = tools_directory(root)
    key_file = os.environ.get('SPARKLE_EDDSA_KEY_FILE')
    if key_file:
        key = Path(key_file)
        if key.is_symlink() or not key.is_file() or key.stat().st_mode & 0o077:
            raise ValueError('Sparkle private key must be a restricted regular file')
        signing = ['--ed-key-file', str(key.resolve())]
    else:
        signing = ['--account', KEYCHAIN_ACCOUNT]
    # Start from an empty directory; no previous feed, notes, deltas or cached
    # assets may accidentally enter this release. Never modify signed XML.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sparkle-appcast-', dir=output.parent) as directory:
        directory = Path(directory)
        staged_dmg = directory / 'Todoiste.dmg'
        shutil.copyfile(dmg, staged_dmg)
        feed = directory / 'appcast.xml'
        command = [str(tools / 'generate_appcast'), *signing, '--download-url-prefix',
                   f'https://github.com/anandman/Todoiste/releases/download/v{version}/',
                   '--versions', build, '--maximum-deltas', '0', '-o', str(feed), str(directory)]
        result = subprocess.run(command, capture_output=True)
        if result.returncode:
            # Tool diagnostics are intentionally withheld: signing failures must
            # never echo secret content from a malformed imported key.
            raise ValueError('Official Sparkle appcast generation failed')
        verify(feed, dmg, version, build, public_key, minimum_os)
        for path, signature in [(feed, None), (dmg, ET.fromstring(feed.read_bytes()).find('./channel/item/enclosure').get(SPARKLE + 'edSignature'))]:
            result = subprocess.run([str(tools / 'sign_update'), '--verify', *signing, str(path),
                                     *([signature] if signature else [])], capture_output=True)
            if result.returncode:
                raise ValueError('Official Sparkle signature verification failed')
        shutil.copyfile(feed, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('generate', 'verify', 'check-configuration'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--dmg', type=Path)
    parser.add_argument('--app', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    directory = root / 'build/Build/Products/Release'
    dmg, output = args.dmg or directory / 'Todoiste.dmg', args.output or directory / 'appcast.xml'
    if args.command == 'check-configuration':
        configuration(root)
        print('Sparkle release public key and deployment target are configured.')
        return
    if args.command == 'generate':
        generate(root, dmg.resolve(), (args.app or directory / 'Todoiste.app').resolve(), output.resolve())
    else:
        verify(output, dmg, *configuration(root))
    print('Verified Sparkle appcast and final DMG signatures and release metadata.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'Sparkle release validation failed: {error}')
