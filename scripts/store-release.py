#!/usr/bin/env python3
"""Prepare a pinned, Store-published shortcuts update; publish only verified DMGs.

No third-party Python dependencies. HTTP data is bounded and all downloaded JS
is checked before any source file is replaced. No upstream code is executed.
"""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

EXTENSION = 'dehmghpdcahlffompjagejmgbcfahndp'
STORE_URL = f'https://chromewebstore.google.com/detail/todoist-shortcuts-gmail-v/{EXTENSION}?hl=en'
UPSTREAM = 'mgsloan/todoist-shortcuts'
FILES = ('mousetrap.js', 'todoist-shortcuts.js')
OPTIONS = {'mouse-behavior', 'cursor-movement'}
ROOT = Path(__file__).resolve().parents[1]


def fetch(url):
    # Never forward the repository token to the Store or raw-content host.
    headers = {'User-Agent': 'Todoiste-release-monitor', 'Cache-Control': 'no-cache'}
    if url.startswith('https://api.github.com/'):
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if token:
            headers['Authorization'] = f'Bearer {token}'
        headers['Accept'] = 'application/vnd.github+json'
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        data = response.read(5_000_001)
    if len(data) > 5_000_000:
        raise ValueError('Response exceeds size limit')
    return data


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text):
        if not self.hidden and text.strip():
            self.text.append(text.strip())


def store_version(html):
    if EXTENSION not in html:
        raise ValueError('Store response is not the expected extension listing')
    parser = VisibleText()
    parser.feed(html)
    indexes = [i for i, value in enumerate(parser.text) if value == 'Version']
    if len(indexes) != 1 or indexes[0] + 1 == len(parser.text):
        raise ValueError('Missing or ambiguous Chrome Store version label')
    value = parser.text[indexes[0] + 1]
    # Upstream uses integer version tags. A new scheme needs a human review.
    if not re.fullmatch(r'[1-9][0-9]{0,8}', value):
        raise ValueError(f'Unsupported Store version: {value!r}')
    return value


def version_fields(project):
    values = []
    for key, pattern in [('MARKETING_VERSION', r'\d+\.\d+\.\d+'), ('CURRENT_PROJECT_VERSION', r'\d+')]:
        matches = re.findall(rf'^\s*{key}: "({pattern})"\s*$', project, re.M)
        if len(matches) != 1:
            raise ValueError(f'Expected exactly one {key}')
        values.append(matches[0])
    return values


def validate_integration(root, scripts):
    text = scripts['todoist-shortcuts.js'].decode('utf-8')
    option_keys = set(re.findall(r"\boptions\s*\[\s*['\"]([^'\"]+)['\"]\s*\]", text))
    option_keys.update(re.findall(r'\boptions\.([A-Za-z_$][\w$]*)', text))
    if option_keys != OPTIONS:
        raise ValueError(f'Upstream options changed: {sorted(option_keys)}')
    for marker in ['data-todoist-shortcuts-options', 'Configure todoist-shortcuts options']:
        if marker not in text:
            raise ValueError(f'Upstream integration marker changed: {marker}')
    webview = (root / 'Todoiste/WebView.swift').read_text()
    settings = (root / 'Todoiste/SettingsView.swift').read_text()
    if any(key not in webview or key not in settings for key in OPTIONS):
        raise ValueError('Native option keys do not match upstream')
    if 'Configure todoist-shortcuts options' not in (root / 'Todoiste/Resources/bootstrap.js').read_text():
        raise ValueError('Native help-link integration changed')
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in scripts.items():
            path = Path(tmp) / name
            path.write_bytes(data)
            result = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
            if result.returncode:
                raise ValueError(f'{name} has invalid JavaScript: {result.stderr}')
    subprocess.run(['node', '--check', str(root / 'Todoiste/Resources/bootstrap.js')], check=True)


def prepare(root=ROOT, download=fetch, expected=None):
    metadata = json.loads((root / 'upstream.json').read_text())
    project = (root / 'project.yml').read_text()
    version, build = version_fields(project)
    if [version, build] != [metadata['todoiste_version'], metadata['todoiste_build']]:
        raise ValueError('project.yml and upstream.json versions differ; update both for native releases')
    scripts = {name: (root / 'Todoiste/Resources' / name).read_bytes() for name in FILES}
    if any(hashlib.sha256(data).hexdigest() != metadata['sha256'][name] for name, data in scripts.items()):
        raise ValueError('Vendored scripts differ from recorded hashes; review local changes')
    validate_integration(root, scripts)
    published = store_version(download(STORE_URL).decode('utf-8'))
    if expected is not None and published != expected:
        raise ValueError('Store changed during this run; retry against the new published version')
    changed = int(published) > int(metadata['version'])
    if changed:
        ref = json.loads(download(f'https://api.github.com/repos/{UPSTREAM}/git/ref/tags/v{published}'))['object']
        # Support both lightweight and annotated tags; stop cycles/abnormal nesting.
        for _ in range(5):
            if ref['type'] == 'commit':
                break
            if ref['type'] != 'tag' or not re.fullmatch(r'[a-f0-9]{40}', ref['sha']):
                raise ValueError('Invalid upstream tag target')
            ref = json.loads(download(f'https://api.github.com/repos/{UPSTREAM}/git/tags/{ref["sha"]}'))['object']
        commit = ref['sha']
        if ref['type'] != 'commit' or not re.fullmatch(r'[a-f0-9]{40}', commit):
            raise ValueError('Upstream tag did not resolve to an immutable commit')
        base = f'https://raw.githubusercontent.com/{UPSTREAM}/{commit}/src'
        if json.loads(download(f'{base}/manifest.json'))['version'] != published:
            raise ValueError('Upstream manifest does not match the published Store version')
        scripts = {name: download(f'{base}/{name}') for name in FILES}
        validate_integration(root, scripts)
        parts = version.split('.')
        version = '.'.join(parts[:2] + [str(int(parts[2]) + 1)])
        build = str(int(build) + 1)
        project = re.sub(r'(MARKETING_VERSION: ")[^"]+', lambda m: m[1] + version, project)
        project = re.sub(r'(CURRENT_PROJECT_VERSION: ")[^"]+', lambda m: m[1] + build, project)
        metadata = dict(version=published, commit=commit, todoiste_version=version, todoiste_build=build,
                        sha256={name: hashlib.sha256(data).hexdigest() for name, data in scripts.items()})
        # All network/integration/syntax checks have succeeded before these writes.
        for name, data in scripts.items():
            (root / 'Todoiste/Resources' / name).write_bytes(data)
        (root / 'project.yml').write_text(project)
        (root / 'upstream.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return dict(changed=changed, store_version=published, tag=f'v{version}', upstream_version=metadata['version'],
                source_digest=hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest())


def is_published(release):
    if release.get('draft'):
        return False
    assets = [a for a in release.get('assets', []) if a['name'] == 'Todoiste.dmg']
    if release.get('prerelease') or len(assets) != 1 or assets[0]['size'] <= 0 or not re.fullmatch(r'sha256:[a-f0-9]{64}', assets[0].get('digest') or ''):
        raise ValueError('Existing public release is incomplete; refusing to replace it')
    return True


def github_api(endpoint, method='GET', payload=None):
    args = ['gh', 'api', '--method', method, endpoint]
    if payload is not None:
        args += ['--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('GitHub release API failed: ' + result.stderr)
    return json.loads(result.stdout) if result.stdout.strip() else None


def release_info(tag):
    repo = os.environ.get('GITHUB_REPOSITORY', 'anandman/Todoiste')
    # The by-tag endpoint returns only published releases. List pages to find
    # drafts as well, and never guess if an earlier retry created duplicates.
    result = subprocess.run(['gh', 'api', f'repos/{repo}/releases?per_page=100',
                             '--paginate', '--slurp'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Could not inspect GitHub releases: ' + result.stderr)
    matches = [r for page in json.loads(result.stdout) for r in page if r['tag_name'] == tag]
    published = [r for r in matches if not r['draft']]
    if len(published) == 1:
        return published[0]
    if len(published) > 1 or len(matches) > 1:
        raise ValueError(f'Multiple releases for {tag}; review draft IDs: {[r["id"] for r in matches]}')
    return matches[0] if matches else None


def publish(root=ROOT):
    """Called only after the signed DMG has passed notarization and Gatekeeper."""
    metadata = json.loads((root / 'upstream.json').read_text())
    tag = 'v' + metadata['todoiste_version']
    existing = release_info(tag)
    if existing and is_published(existing):
        raise ValueError('Release already published; refusing to replace its artifact')
    run = lambda *args: subprocess.run(args, cwd=root, check=True)
    # A concurrent human push must stop this run instead of being incorporated
    # into a tag whose source differs from the verified build.
    run('git', 'fetch', 'origin', 'main', '--tags')
    remote = subprocess.check_output(['git', 'rev-parse', 'origin/main'], cwd=root, text=True).strip()
    if remote != os.environ['GITHUB_SHA']:
        raise ValueError('main advanced during the build; rerun on its latest commit')
    allowed = {'project.yml', 'upstream.json', *(f'Todoiste/Resources/{n}' for n in FILES)}
    dirty = subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], cwd=root, text=True).splitlines()
    if set(dirty) - allowed:
        raise ValueError('Unexpected source changes during build')
    run('git', 'config', 'user.name', 'github-actions[bot]')
    run('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    if dirty:
        run('git', 'add', '--', *sorted(allowed))
        run('git', 'commit', '-m', f'Release {tag} with Store shortcuts {metadata["version"]}')
        run('git', 'push', 'origin', 'HEAD:main')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    tagged = subprocess.run(['git', 'rev-parse', '--verify', f'refs/tags/{tag}^{{commit}}'], cwd=root, capture_output=True, text=True)
    if tagged.returncode == 0:
        if tagged.stdout.strip() != head:
            # A publisher/workflow fix can advance main after the tag was pushed.
            # Preserve that tag, but require the actual app/build inputs to match.
            same_inputs = subprocess.run(['git', 'diff', '--quiet', f'refs/tags/{tag}',
                'HEAD', '--', 'Todoiste', 'project.yml', 'upstream.json'], cwd=root)
            if same_inputs.returncode:
                raise ValueError('Pending release tag points to different app source; review manually')
    else:
        run('git', 'tag', '-a', tag, '-m', f'Todoiste {tag}')
        run('git', 'push', 'origin', tag)
    dmg = root / 'build/Build/Products/Release/Todoiste.dmg'
    repo = os.environ.get('GITHUB_REPOSITORY', 'anandman/Todoiste')
    draft = existing or github_api(f'repos/{repo}/releases', 'POST', {
        'tag_name': tag, 'name': tag, 'draft': True, 'generate_release_notes': True})
    release_id = draft['id']
    if draft['tag_name'] != tag or not draft['draft'] or draft.get('prerelease'):
        raise ValueError('Expected a non-prerelease draft before uploading')
    # Address the exact draft ID throughout. Tag lookup cannot identify drafts.
    for asset in draft['assets']:
        if asset['name'] == 'Todoiste.dmg':
            github_api(f'repos/{repo}/releases/assets/{asset["id"]}', 'DELETE')
    upload = subprocess.run(['gh', 'api', '--method', 'POST',
        f'https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name=Todoiste.dmg',
        '-H', 'Content-Type: application/octet-stream', '--input', str(dmg)],
        capture_output=True, text=True)
    if upload.returncode:
        raise RuntimeError('Release asset upload failed: ' + upload.stderr)
    draft = github_api(f'repos/{repo}/releases/{release_id}')
    assets = [a for a in draft['assets'] if a['name'] == 'Todoiste.dmg']
    expected_digest = 'sha256:' + hashlib.sha256(dmg.read_bytes()).hexdigest()
    if len(assets) != 1 or assets[0].get('digest') != expected_digest:
        raise ValueError('Uploaded asset digest does not match the verified local DMG')
    github_api(f'repos/{repo}/releases/{release_id}', 'PATCH', {'draft': False, 'make_latest': 'true'})
    if not is_published(github_api(f'repos/{repo}/releases/{release_id}')):
        raise ValueError('Release did not become public')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'publish'])
    parser.add_argument('--expected-store-version')
    parser.add_argument('--output')
    parser.add_argument('--check-release', action='store_true')
    args = parser.parse_args()
    if args.command == 'publish':
        publish()
        return
    result = prepare(expected=args.expected_store_version)
    if args.check_release:
        release = release_info(result['tag'])
        result['pending'] = not (release and is_published(release))
        if result['pending'] and int(result['store_version']) < int(result['upstream_version']):
            raise ValueError('Pending version is newer than the current Store publication; review before releasing')
    print(json.dumps(result))
    if args.output:
        with open(args.output, 'a') as out:
            for key, value in result.items():
                out.write(f'{key}={str(value).lower() if isinstance(value, bool) else value}\n')

if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, HTTPError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'Release gate failed: {error}')
