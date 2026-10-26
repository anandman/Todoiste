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
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import sys
import tempfile
import unicodedata
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

EXTENSION = 'dehmghpdcahlffompjagejmgbcfahndp'
STORE_URL = f'https://chromewebstore.google.com/detail/todoist-shortcuts-gmail-v/{EXTENSION}?hl=en'
UPSTREAM = 'mgsloan/todoist-shortcuts'
RESOURCE_DIR = Path('Todoiste/Resources/Shortcuts')
MAX_ASSETS = 128
MAX_ASSET_BYTES = 2_000_000
MAX_SOURCE_BYTES = 5_000_000
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


def asset_path(value):
    """Use one portable, case-sensitive relative namespace on Git and macOS."""
    if (not isinstance(value, str) or not value or len(value) > 256 or
            any(c in value for c in '\\:%?#') or
            any(ord(c) < 32 for c in value) or value.startswith('/') or
            any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError(f'Unsafe asset path: {value!r}')
    return value


def check_asset_names(names):
    names = list(names)
    for name in names:
        asset_path(name)
    portable = lambda name: unicodedata.normalize('NFD', name).casefold()
    namespace = set(names)
    namespace.update(parent.as_posix() for name in names for parent in PurePosixPath(name).parents
                     if parent.as_posix() != '.')
    if (len(names) > MAX_ASSETS or len(set(names)) != len(names) or
            len({portable(n) for n in namespace}) != len(namespace)):
        raise ValueError('Too many assets or ambiguous asset paths')
    folded = {portable(name) for name in names}
    if any(portable(parent.as_posix()) in folded for name in names
           for parent in PurePosixPath(name).parents if parent.as_posix() != '.'):
        raise ValueError('An asset file collides with a parent directory')


def local_assets(root, hashes):
    check_asset_names(hashes)
    if not hashes or any(not re.fullmatch(r'[a-f0-9]{64}', value) for value in hashes.values()):
        raise ValueError('Invalid vendored asset hashes')
    directory = root / RESOURCE_DIR
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Missing or symlinked vendored asset directory')
    paths = list(directory.rglob('*'))
    if any(path.is_symlink() or not (path.is_dir() or path.is_file()) for path in paths):
        raise ValueError('Vendored assets must be regular files')
    files = {path.relative_to(directory).as_posix(): path for path in paths if path.is_file()}
    if set(files) != set(hashes):
        raise ValueError('Vendored asset inventory differs from recorded hashes')
    if any(path.stat().st_size > MAX_ASSET_BYTES for path in files.values()):
        raise ValueError('Vendored asset exceeds size limit')
    assets = {name: path.read_bytes() for name, path in files.items()}
    if sum(map(len, assets.values())) > MAX_SOURCE_BYTES:
        raise ValueError('Vendored source exceeds size limit')
    if any(hashlib.sha256(data).hexdigest() != hashes[name] for name, data in assets.items()):
        raise ValueError('Vendored assets differ from recorded hashes; review local changes')
    return assets


def download_assets(commit, download):
    """Discover only src, never extract an archive or follow links from upstream."""
    api = f'https://api.github.com/repos/{UPSTREAM}/git/trees'
    root = json.loads(download(f'{api}/{commit}'))
    if root.get('truncated') or len(root['tree']) > 512:
        raise ValueError('Incomplete or excessive upstream root tree')
    matches = [entry for entry in root['tree'] if entry['path'] == 'src']
    if len(matches) != 1 or matches[0]['type'] != 'tree' or matches[0]['mode'] != '040000':
        raise ValueError('Upstream src must be a directory')
    source_sha = matches[0]['sha']
    if not re.fullmatch(r'[a-f0-9]{40}', source_sha):
        raise ValueError('Invalid upstream source tree SHA')
    source = json.loads(download(f'{api}/{source_sha}?recursive=1'))
    if source.get('truncated') or len(source['tree']) > MAX_ASSETS * 2:
        raise ValueError('Incomplete or excessive upstream source tree')
    entries = source['tree']
    # Directory entries are checked separately; only file names may not be
    # ancestors of each other (normal Git trees include parent directories).
    for entry in entries:
        asset_path(entry['path'])
    check_asset_names(entry['path'] for entry in entries if entry['type'] != 'tree')
    if len({unicodedata.normalize('NFD', entry['path']).casefold() for entry in entries}) != len(entries):
        raise ValueError('Ambiguous upstream source paths')
    files = []
    for entry in entries:
        if entry['type'] == 'tree' and entry['mode'] == '040000':
            continue
        if (entry['type'] != 'blob' or entry['mode'] != '100644' or
                not re.fullmatch(r'[a-f0-9]{40}', entry['sha']) or
                not isinstance(entry.get('size'), int) or not 0 <= entry['size'] <= MAX_ASSET_BYTES):
            raise ValueError('Unsupported upstream asset type or size')
        files.append(entry)
    if not files or len(files) > MAX_ASSETS or sum(e['size'] for e in files) > MAX_SOURCE_BYTES:
        raise ValueError('Upstream assets exceed size limit')
    assets = {}
    for entry in files:
        data = download(f'https://raw.githubusercontent.com/{UPSTREAM}/{commit}/src/{quote(entry["path"], safe="/")}')
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if len(data) != entry['size'] or blob != entry['sha']:
            raise ValueError(f'Upstream asset differs from pinned tree: {entry["path"]}')
        assets[entry['path']] = data
    return assets


def javascript_tokens(text):
    """Small lexical scanner, including expressions inside template literals.

    This is deliberately a conservative contract check, not a JavaScript security
    parser. Node checks syntax separately without evaluating the source.
    """
    tokens = []
    index = 0

    def code(stop=False):
        nonlocal index
        depth = 0
        while index < len(text):
            char = text[index]
            if char.isspace():
                index += 1
            elif text.startswith('//', index):
                end = text.find('\n', index)
                index = len(text) if end < 0 else end
            elif text.startswith('/*', index):
                end = text.find('*/', index + 2)
                index = len(text) if end < 0 else end + 2
            elif char == '/' and (not tokens or tokens[-1][1] in
                    ('(', '[', '{', '=', ',', ':', ';', '!', '?', '&', '|', '>',
                     'return', 'case', 'throw', 'yield', 'await')):
                # Skip regex literals so quotes/comment-like characters inside
                # a pattern cannot hide API access later in the script.
                index += 1
                character_class = False
                while index < len(text):
                    current = text[index]
                    if current == '\\':
                        index += 2
                        continue
                    if current == '[':
                        character_class = True
                    elif current == ']':
                        character_class = False
                    index += 1
                    if current == '/' and not character_class:
                        break
                while index < len(text) and text[index].isalpha():
                    index += 1
                tokens.append(('regex', 'literal'))
            elif char in ('"', "'"):
                quote, start = char, index
                index += 1
                while index < len(text) and text[index] != quote:
                    index += 2 if text[index] == '\\' else 1
                index += 1
                tokens.append(('string', text[start + 1:index - 1]))
            elif char == '`':
                index += 1
                while index < len(text) and text[index] != '`':
                    if text[index] == '\\':
                        index += 2
                    elif text.startswith('${', index):
                        index += 2
                        code(True)
                    else:
                        index += 1
                index += 1
            elif char.isalpha() or char in '_$':
                start = index
                index += 1
                while index < len(text) and (text[index].isalnum() or text[index] in '_$'):
                    index += 1
                tokens.append(('identifier', text[start:index]))
            else:
                if char == '\\':
                    raise ValueError('Escaped JavaScript identifiers need integration review')
                index += 1
                if char == '{':
                    depth += 1
                elif char == '}':
                    if stop and depth == 0:
                        return
                    depth -= 1
                tokens.append(('punctuation', char))
    code()
    return tokens


def validate_extension_apis(name, text, assets):
    tokens = javascript_tokens(text)
    allowed = {('storage', 'sync', method) for method in ('get', 'set', 'remove', 'clear', 'getBytesInUse')}
    allowed.update({('runtime', 'getURL'), ('runtime', 'lastError'), ('runtime', 'lastError', 'message')})
    for i, token in enumerate(tokens):
        computed_root = (token[0] == 'string' and i > 0 and tokens[i - 1][1] == '[' and
                         i + 1 < len(tokens) and tokens[i + 1][1] == ']')
        if (token[0] != 'identifier' and not computed_root) or token[1] not in ('chrome', 'browser'):
            continue
        path, j = [], i + (2 if computed_root else 1)
        while j < len(tokens):
            if tokens[j][1] == '.' and j + 1 < len(tokens) and tokens[j + 1][0] == 'identifier':
                path.append(tokens[j + 1][1])
                j += 2
            elif (tokens[j][1] == '[' and j + 2 < len(tokens) and
                    tokens[j + 1][0] == 'string' and tokens[j + 2][1] == ']'):
                path.append(tokens[j + 1][1])
                j += 3
            else:
                break
        guard = (tuple(path) in {('runtime',), ('storage',), ('storage', 'sync')} and
                 [value for _, value in tokens[j:j + 2]] == ['&', '&'])
        if tuple(path) not in allowed and not guard:
            raise ValueError(f'{name} requires unsupported extension API: {token[1]}.{".".join(path)}')
        if tuple(path) == ('runtime', 'getURL') and j + 1 < len(tokens) and tokens[j][1] == '(':
            argument = tokens[j + 1]
            if argument[0] == 'string':
                asset_reference(assets, 'manifest.json', argument[1])
    # The host loads a closed local asset set. New loading mechanisms require a
    # native review, even when an upstream spelling might ultimately be safe.
    forbidden = {'fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'Worker',
                 'SharedWorker', 'importScripts', 'import', 'eval', 'Function', 'WebAssembly'}
    if any(kind == 'identifier' and value in forbidden for kind, value in tokens):
        raise ValueError(f'{name} introduces unsupported dynamic code/network loading')
    if name != 'inject.js' and re.search(r'createElement\s*\(\s*[\'\"]script[\'\"]', text, re.I):
        raise ValueError(f'{name} introduces unsupported dynamic script loading')


def asset_reference(assets, owner, reference):
    """Resolve a URL reference, separately from the strict inventory namespace."""
    if (not isinstance(reference, str) or '\\' in reference or
            any(ord(char) < 32 or ord(char) == 127 for char in reference)):
        raise ValueError(f'{owner} has an unsafe resource reference')
    url = urlsplit(reference)
    if url.scheme or url.netloc or reference.startswith('/'):
        raise ValueError(f'{owner} references a nonlocal resource: {reference}')
    path = unquote(url.path, errors='strict')
    if path.startswith('/') or '\\' in path:
        raise ValueError(f'{owner} references an absolute resource path: {reference}')
    target = posixpath.normpath(posixpath.join(posixpath.dirname(owner), path)) if path else owner
    asset_path(target)  # Reject normalized paths which still escape the root.
    if target not in assets:
        raise ValueError(f'{owner} references missing asset: {reference}')
    return target


CSS_NAME = re.compile(r'(?:[-_a-zA-Z0-9]|\\(?:[0-9a-fA-F]{1,6}[ \t\r\n\f]?|[^\r\n\f]))+')
CSS_ESCAPE = re.compile(r'\\(?:([0-9a-fA-F]{1,6})[ \t\r\n\f]?|(\r\n|[\n\r\f])|(.))', re.S)


def css_unescape(value):
    def replace(match):
        if match[1]:
            codepoint = int(match[1], 16)
            if not codepoint or codepoint > 0x10ffff or 0xd800 <= codepoint <= 0xdfff:
                raise ValueError('Invalid CSS resource escape')
            return chr(codepoint)
        return '' if match[2] else match[3]
    return CSS_ESCAPE.sub(replace, value)


def validate_css(owner, text, assets):
    """Check resource URLs/imports without mistaking CSS strings for loaders."""
    length = len(text)

    def whitespace(index):
        while index < length:
            if text[index].isspace():
                index += 1
            elif text.startswith('/*', index):
                end = text.find('*/', index + 2)
                if end < 0:
                    raise ValueError(f'{owner} has an unterminated CSS comment')
                index = end + 2
            else:
                break
        return index

    def string(index):
        quote, start = text[index], index + 1
        index += 1
        while index < length:
            if text[index] == quote:
                return css_unescape(text[start:index]), index + 1
            index += 2 if text[index] == '\\' else 1
        raise ValueError(f'{owner} has an unterminated CSS string')

    def url_value(index):
        index = whitespace(index)
        if index >= length or text[index] != '(':
            raise ValueError(f'{owner} has an unsupported CSS URL')
        index = whitespace(index + 1)
        if index < length and text[index] in ('"', "'"):
            value, index = string(index)
            index = whitespace(index)
        else:
            start = index
            while index < length and text[index] != ')':
                index += 2 if text[index] == '\\' else 1
            value = css_unescape(text[start:index].strip())
        if index >= length or text[index] != ')':
            raise ValueError(f'{owner} has an unterminated CSS URL')
        return value, index + 1

    index = 0
    while index < length:
        index = whitespace(index)
        if index >= length:
            break
        if text[index] in ('"', "'"):
            _, index = string(index)
            continue
        if text[index] == '@':
            match = CSS_NAME.match(text, index + 1)
            if match and css_unescape(match[0]).lower() == 'import':
                index = whitespace(match.end())
                if index < length and text[index] in ('"', "'"):
                    value, index = string(index)
                else:
                    name = CSS_NAME.match(text, index)
                    if not name or css_unescape(name[0]).lower() != 'url':
                        raise ValueError(f'{owner} has an unsupported CSS import')
                    value, index = url_value(name.end())
                if not asset_reference(assets, owner, value).endswith('.css'):
                    raise ValueError(f'{owner} imports an unsupported stylesheet type')
                continue
        match = CSS_NAME.match(text, index)
        if match:
            end = whitespace(match.end())
            if css_unescape(match[0]).lower() == 'url' and end < length and text[end] == '(':
                value, index = url_value(end)
                asset_reference(assets, owner, value)
            else:
                index = match.end()
        else:
            index += 1


class AssetHTML(HTMLParser):
    def __init__(self, name, assets):
        super().__init__()
        self.name, self.assets, self.script = name, assets, False
        self.style = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if (tag in ('base', 'iframe', 'object', 'embed') or
                any(k.startswith('on') for k in attrs) or
                (tag == 'meta' and attrs.get('http-equiv', '').lower() == 'refresh')):
            raise ValueError(f'{self.name} uses unsupported active HTML')
        if tag == 'script':
            if not attrs.get('src') or attrs.get('type', '') not in ('', 'text/javascript', 'application/javascript'):
                raise ValueError(f'{self.name} must use local classic script assets')
            if not asset_reference(self.assets, self.name, attrs['src']).endswith('.js'):
                raise ValueError(f'{self.name} requires a script with an unsupported extension')
            self.script = True
        for key in ('src', 'poster'):
            if attrs.get(key):
                asset_reference(self.assets, self.name, attrs[key])
        if tag == 'link' and attrs.get('href'):
            asset_reference(self.assets, self.name, attrs['href'])
        if attrs.get('href', '').lower().startswith(('javascript:', 'data:')):
            raise ValueError(f'{self.name} uses an unsupported active link')
        if 'srcset' in attrs:
            raise ValueError(f'{self.name} has an unsupported inline resource declaration')
        if tag == 'style':
            if attrs.get('type', '') not in ('', 'text/css'):
                raise ValueError(f'{self.name} has an unsupported stylesheet type')
            self.style = []
        if 'style' in attrs:
            validate_css(self.name, attrs['style'] or '', self.assets)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.script = False
        if tag == 'style' and self.style is not None:
            validate_css(self.name, ''.join(self.style), self.assets)
            self.style = None

    def handle_data(self, text):
        if self.script and text.strip():
            raise ValueError(f'{self.name} has an unsupported inline script')
        if self.style is not None:
            self.style.append(text)

    def close(self):
        super().close()
        if self.style is not None:
            validate_css(self.name, ''.join(self.style), self.assets)
            self.style = None


def validate_integration(root, assets):
    check_asset_names(assets)
    if not {'manifest.json', 'mousetrap.js', 'todoist-shortcuts.js'} <= set(assets):
        raise ValueError('Upstream native runtime assets are missing')
    manifest = json.loads(assets['manifest.json'])
    if (manifest.get('manifest_version') != 3 or manifest.get('permissions') != ['storage'] or
            any(manifest.get(key) for key in ('optional_permissions', 'host_permissions',
                'optional_host_permissions', 'background', 'sandbox', 'devtools_page'))):
        raise ValueError('Upstream manifest needs unsupported permissions or extension services')
    options = manifest['options_ui']['page']
    # Native manifest discovery appends this exact path; URL normalization is
    # only appropriate for dependencies resolved by WebKit from that page.
    asset_path(options)
    asset_reference(assets, 'manifest.json', options)
    if not options.endswith('.html'):
        raise ValueError('Upstream options entrypoint must be local HTML')
    # Check every manifest-declared resource, including content-script loaders
    # which are bundled for provenance but not executed by the native host.
    for content in manifest.get('content_scripts', []):
        for name in content.get('js', []) + content.get('css', []):
            asset_reference(assets, 'manifest.json', name)
    for group in manifest.get('web_accessible_resources', []):
        for name in group.get('resources', []):
            asset_reference(assets, 'manifest.json', name)
    for name in manifest.get('icons', {}).values():
        asset_reference(assets, 'manifest.json', name)
    for key in ('default_icon', 'default_popup'):
        value = manifest.get('action', {}).get(key, {})
        for name in value.values() if isinstance(value, dict) else [value]:
            asset_reference(assets, 'manifest.json', name)
    csp = json.dumps(manifest.get('content_security_policy', {}))
    if re.search(r'unsafe-|https?:|data:|blob:', csp, re.I):
        raise ValueError('Upstream CSP enables unsupported script sources')
    text = assets['todoist-shortcuts.js'].decode('utf-8')
    for marker in ('data-todoist-shortcuts-options', 'data-todoist-shortcuts-options-url'):
        if not re.search(rf"(['\"]){re.escape(marker)}\1", text):
            raise ValueError(f'Upstream DOM attribute integration changed: {marker}')
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in assets.items():
            if name.endswith('.html'):
                parser = AssetHTML(name, assets)
                parser.feed(data.decode('utf-8'))
                parser.close()
            if name.endswith('.css'):
                validate_css(name, data.decode('utf-8'), assets)
            if not name.endswith(('.js', '.mjs', '.cjs')):
                continue
            validate_extension_apis(name, data.decode('utf-8'), assets)
            path = Path(tmp) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            result = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
            if result.returncode:
                raise ValueError(f'{name} has invalid JavaScript: {result.stderr}')
    for name in ('bootstrap.js', 'settings-storage-adapter.js'):
        path = root / 'Todoiste/Resources' / name
        if path.exists():
            subprocess.run(['node', '--check', str(path)], check=True, capture_output=True)


def prepare(root=ROOT, download=fetch, expected=None):
    metadata = json.loads((root / 'upstream.json').read_text())
    project = (root / 'project.yml').read_text()
    version, build = version_fields(project)
    if [version, build] != [metadata['todoiste_version'], metadata['todoiste_build']]:
        raise ValueError('project.yml and upstream.json versions differ; update both for native releases')
    scripts = local_assets(root, metadata['sha256'])
    if json.loads(scripts['manifest.json'])['version'] != metadata['version']:
        raise ValueError('Vendored manifest and upstream.json versions differ')
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
        scripts = download_assets(commit, download)
        if json.loads(scripts['manifest.json'])['version'] != published:
            raise ValueError('Upstream manifest does not match the published Store version')
        validate_integration(root, scripts)
        parts = version.split('.')
        version = '.'.join(parts[:2] + [str(int(parts[2]) + 1)])
        build = str(int(build) + 1)
        project = re.sub(r'(MARKETING_VERSION: ")[^"]+', lambda m: m[1] + version, project)
        project = re.sub(r'(CURRENT_PROJECT_VERSION: ")[^"]+', lambda m: m[1] + build, project)
        metadata = dict(version=published, commit=commit, todoiste_version=version, todoiste_build=build,
                        sha256={name: hashlib.sha256(data).hexdigest() for name, data in scripts.items()})
        # All network/integration/syntax checks have succeeded before these writes.
        directory = root / RESOURCE_DIR
        # Materialize first so file/directory shape changes never leave stale
        # assets or partially copied resources after a validation failure.
        with tempfile.TemporaryDirectory(prefix='.shortcuts-update-', dir=directory.parent) as tmp:
            candidate, previous = Path(tmp) / 'candidate', Path(tmp) / 'previous'
            candidate.mkdir()
            for name, data in scripts.items():
                path = candidate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            directory.rename(previous)
            try:
                candidate.rename(directory)
            except OSError:
                previous.rename(directory)
                raise
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
    allowed = {'project.yml', 'upstream.json'}
    dirty = subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], cwd=root, text=True).splitlines()
    dirty += subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'],
                                    cwd=root, text=True).splitlines()
    if any(name not in allowed and not name.startswith(RESOURCE_DIR.as_posix() + '/') for name in dirty):
        raise ValueError('Unexpected source changes during build')
    run('git', 'config', 'user.name', 'github-actions[bot]')
    run('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    if dirty:
        run('git', 'add', '--', *sorted(allowed), RESOURCE_DIR.as_posix())
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
