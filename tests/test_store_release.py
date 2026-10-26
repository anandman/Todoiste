"""Exercise the release gate with HTTP fixtures; never touch the real worktree."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import re
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('store_release', Path(__file__).resolve().parents[1] / 'scripts/store-release.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40

class StoreReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ['project.yml', 'upstream.json', 'Todoiste']:
            src, dest = ROOT / name, self.root / name
            shutil.copytree(src, dest) if src.is_dir() else shutil.copy(src, dest)
        # A stable release baseline independent of the repository's next version.
        project = (self.root / 'project.yml').read_text()
        project = re.sub(r'(MARKETING_VERSION: ")[^"]+', r'\g<1>1.2.1', project)
        project = re.sub(r'(CURRENT_PROJECT_VERSION: ")[^"]+', r'\g<1>5', project)
        (self.root / 'project.yml').write_text(project)
        metadata = json.loads((self.root / 'upstream.json').read_text())
        metadata.update(version='209', todoiste_version='1.2.1', todoiste_build='5')
        manifest_path = self.root / gate.RESOURCE_DIR / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['version'] = '209'
        manifest_path.write_text(json.dumps(manifest))
        metadata['sha256']['manifest.json'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        (self.root / 'upstream.json').write_text(json.dumps(metadata))
        self.requests = []
        self.version = '210'
        self.manifest_version = '210'
        self.scripts = {p.relative_to(ROOT / gate.RESOURCE_DIR).as_posix(): p.read_bytes()
                        for p in (ROOT / gate.RESOURCE_DIR).rglob('*') if p.is_file()}
        self.tree_transform = lambda tree: tree
        self.raw_transform = lambda name, data: data

    def candidate_assets(self):
        assets = dict(self.scripts)
        manifest = json.loads(assets['manifest.json'])
        manifest['version'] = self.manifest_version
        assets['manifest.json'] = json.dumps(manifest).encode()
        return assets

    def candidate_manifest(self, **changes):
        manifest = json.loads(self.scripts['manifest.json'])
        manifest.update(changes)
        self.scripts['manifest.json'] = json.dumps(manifest).encode()

    def fetch(self, url):
        self.requests.append(url)
        if url == gate.STORE_URL:
            return f'<link rel="canonical" href="{gate.STORE_URL}"><script>Version 999</script><div>Version</div><div>{self.version}</div>'.encode()
        if url.endswith('/git/ref/tags/v210'):
            return json.dumps({'object': {'type': 'commit', 'sha': SHA}}).encode()
        if url.endswith('/git/trees/' + SHA):
            return json.dumps({'truncated': False, 'tree': [
                {'path': 'src', 'type': 'tree', 'mode': '040000', 'sha': 'c' * 40}]}).encode()
        if url.endswith('/git/trees/' + 'c' * 40 + '?recursive=1'):
            tree = {'truncated': False, 'tree': [dict(path=name, type='blob', mode='100644',
                size=len(data), sha=hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest())
                for name, data in self.candidate_assets().items()]}
            return json.dumps(self.tree_transform(tree)).encode()
        if f'/{SHA}/src/' in url:
            name = url.split('/src/', 1)[1]
            return self.raw_transform(name, self.candidate_assets()[name])
        raise AssertionError('Unexpected network request: ' + url)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_new_store_version_pins_sources_and_bumps_once(self):
        result = gate.prepare(self.root, self.fetch)
        self.assertTrue(result['changed'])
        self.assertEqual(result['tag'], 'v1.2.2')
        self.assertIn('CURRENT_PROJECT_VERSION: "6"', (self.root / 'project.yml').read_text())
        metadata = json.loads((self.root / 'upstream.json').read_text())
        self.assertEqual(metadata['version'], '210')
        self.assertEqual(metadata['commit'], SHA)
        self.assertEqual(set(metadata['sha256']), set(self.scripts))
        before = self.snapshot()
        self.assertFalse(gate.prepare(self.root, self.fetch)['changed'])
        self.assertEqual(before, self.snapshot())
        self.assertFalse(any('/master/' in u for u in self.requests))

    def test_same_or_older_store_does_not_bump(self):
        for version in ['209', '208']:
            with self.subTest(version=version):
                self.version = version
                before = self.snapshot()
                self.assertFalse(gate.prepare(self.root, self.fetch)['changed'])
                self.assertEqual(before, self.snapshot())

    def test_ambiguous_or_missing_version_fails_closed(self):
        for html in ['<div>Version</div><div>210</div><div>Version</div><div>211</div>', '<div>Version</div><div>not a version</div>', '<script>Version 210</script>']:
            with self.subTest(html=html), self.assertRaises(ValueError):
                gate.store_version(f'<link href="{gate.STORE_URL}">' + html)

    def test_wrong_extension_is_rejected(self):
        with self.assertRaises(ValueError):
            gate.store_version('<div>Version</div><div>210</div>')

    def test_manifest_mismatch_leaves_all_files_unchanged(self):
        self.manifest_version = '211'
        before = self.snapshot()
        with self.assertRaises(ValueError):
            gate.prepare(self.root, self.fetch)
        self.assertEqual(before, self.snapshot())

    def test_new_option_and_help_text_are_accepted(self):
        original = self.scripts['todoist-shortcuts.js']
        self.scripts['todoist-shortcuts.js'] = original.replace(
            b'Configure todoist-shortcuts options', b'New settings link') + b"\noptions['future-setting'];"
        self.assertTrue(gate.prepare(self.root, self.fetch)['changed'])

    def test_changed_upstream_options_ui_text_is_accepted(self):
        self.scripts['options-page.html'] = self.scripts['options-page.html'].replace(
            b'Mouse hover behavior:', b'Future settings labels:')
        self.assertTrue(gate.prepare(self.root, self.fetch)['changed'])

    def test_inline_css_presentation_changes_are_accepted(self):
        self.scripts['options-page.html'] = self.scripts['options-page.html'].replace(b'</head>',
            b'<style>.pane { color: red; background: url("icon128.png?cache=2#preview"); }</style></head>')
        self.scripts['options-page.html'] = self.scripts['options-page.html'].replace(b'<body>',
            b'<body style="color: blue; background-image: url(icon128.png?v=1#preview)">')
        self.assertTrue(gate.prepare(self.root, self.fetch)['changed'])

    def test_nested_options_page_can_use_safe_parent_cache_and_sprite_references(self):
        page = self.scripts['options-page.html']
        for name in [b'common.js', b'options-page.js', b'options-page.css']:
            page = page.replace(name, b'../' + name + b'?cache=1#asset')
        self.scripts['settings/options.html'] = page
        self.candidate_manifest(options_ui={'page': 'settings/options.html'})
        self.assertTrue(gate.prepare(self.root, self.fetch)['changed'])

    def test_local_css_imports_share_relative_reference_rules(self):
        self.scripts['theme/base.css'] = b'body { background: url("../icon128.png?v=1#sprite"); }'
        self.scripts['options-page.css'] += b'\n@import "theme/base.css?cache=1" screen;'
        self.scripts['options-page.html'] = self.scripts['options-page.html'].replace(b'</head>',
            b'<style>@import url("theme/base.css#theme");</style></head>')
        self.assertTrue(gate.prepare(self.root, self.fetch)['changed'])

    def test_reference_resolution_allows_local_urls_without_relaxing_inventory_paths(self):
        assets = {'pages/options.html': b'', 'common.js': b'', 'pages/icons.svg': b''}
        for value, target in [('../common.js?v=2#cache', 'common.js'),
                              ('./icons.svg#sprite', 'pages/icons.svg'),
                              ('?cache=3', 'pages/options.html'),
                              ('#section', 'pages/options.html'),
                              ('../pages/../common.js', 'common.js'),
                              ('%2e%2e/common.js', 'common.js')]:
            with self.subTest(value=value):
                self.assertEqual(target, gate.asset_reference(assets, 'pages/options.html', value))
        for value in ['../../outside.js', '%2e%2e/%2e%2e/outside.js',
                      'https://example.com/common.js', '//example.com/common.js',
                      'data:text/javascript,alert(1)', '/common.js', '//?cache=1', '%2fcommon.js',
                      '../missing.js?cache=1', '..\\common.js', '../common.js\n']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.asset_reference(assets, 'pages/options.html', value)
        with self.assertRaises(ValueError):
            gate.asset_path('../common.js')

    def test_css_literals_and_escapes_do_not_hide_dependencies(self):
        assets = {'options.html': b'', 'icon.png': b'', 'theme.css': b''}
        gate.validate_css('options.html', r'''
            p::before { content: "url(https://example.com/plain-text)"; }
            body { background: u\72l("icon.png?cache=1#sprite"); }
            @\69mport "theme.css";
        ''', assets)
        for css in [r'body { background: u\72l("https://example.com/image.png"); }',
                    r'body { background: url("\68ttps://example.com/image.png"); }',
                    r'@\69mport "https://example.com/theme.css";']:
            with self.subTest(css=css), self.assertRaises(ValueError):
                gate.validate_css('options.html', css, assets)

    def test_inline_css_rejects_remote_missing_or_escaping_dependencies(self):
        original = self.scripts['options-page.html']
        for css in [b'body { background: url(https://example.com/image.png) }',
                    b'body { background: url(../outside.png) }',
                    b'body { background: url(missing.png?cache=1) }',
                    b'@import "https://example.com/theme.css";',
                    b'@import "missing.css";']:
            for kind in ('block', 'attribute'):
                with self.subTest(css=css, kind=kind):
                    if kind == 'block':
                        page = original.replace(b'</head>', b'<style>' + css + b'</style></head>')
                    else:
                        page = original.replace(b'<body>', b"<body style='" + css + b"'>")
                    self.scripts['options-page.html'] = page
                    self.assert_rejected_unchanged()

    def test_supported_runtime_last_error_guard_is_accepted(self):
        self.scripts['common.js'] += b'\nconst failure = chrome.runtime && chrome.runtime.lastError;'
        self.assertTrue(gate.prepare(self.root, self.fetch)['changed'])

    def test_nested_new_assets_are_preserved_and_hashed(self):
        self.scripts['future/editor.js'] = b'browser.storage.sync.get(null).then(console.log);'
        self.scripts['future/editor.css'] = b'body { color: blue; }'
        self.scripts['options-page.html'] = self.scripts['options-page.html'].replace(b'</head>',
            b'<script src="future/editor.js"></script><link rel="stylesheet" href="future/editor.css"></head>')
        gate.prepare(self.root, self.fetch)
        metadata = json.loads((self.root / 'upstream.json').read_text())
        for name, data in self.candidate_assets().items():
            self.assertEqual(data, (self.root / gate.RESOURCE_DIR / name).read_bytes())
            self.assertEqual(hashlib.sha256(data).hexdigest(), metadata['sha256'][name])

    def assert_rejected_unchanged(self):
        before = self.snapshot()
        with self.assertRaises((ValueError, KeyError, RuntimeError)):
            gate.prepare(self.root, self.fetch)
        self.assertEqual(before, self.snapshot())

    def test_unsupported_storage_api_aborts_before_writes(self):
        original = self.scripts['common.js']
        for expression in [b'chrome.storage.local.get(null);', b'browser.storage.sync.getKeys();',
                           b'const alias = chrome.storage;', b'chrome.storage.sync[method]();',
                           b'chrome.runtime.sendMessage({});', b'`${browser.tabs.query({})}`;']:
            with self.subTest(expression=expression):
                self.scripts['common.js'] = original + b'\n' + expression
                self.assert_rejected_unchanged()

    def test_missing_settings_asset_aborts_before_writes(self):
        del self.scripts['options-page.css']
        self.assert_rejected_unchanged()

    def test_missing_runtime_options_attribute_aborts_before_writes(self):
        self.scripts['todoist-shortcuts.js'] = self.scripts['todoist-shortcuts.js'].replace(
            b"'data-todoist-shortcuts-options'", b"'a-new-transport'")
        self.assert_rejected_unchanged()

    def test_changed_or_removed_settings_url_attribute_aborts_before_writes(self):
        original = self.scripts['todoist-shortcuts.js']
        for replacement in [b'data-upstream-settings-url', b'']:
            with self.subTest(replacement=replacement):
                self.scripts['todoist-shortcuts.js'] = original.replace(
                    b'data-todoist-shortcuts-options-url', replacement)
                self.assert_rejected_unchanged()

    def test_new_permission_aborts_before_writes(self):
        self.candidate_manifest(permissions=['storage', 'tabs'])
        self.assert_rejected_unchanged()

    def test_external_and_unsafe_dependencies_abort_before_writes(self):
        original = self.scripts['options-page.html']
        for reference in [b'https://example.com/editor.js', b'//example.com/editor.js', b'../editor.js',
                          b'%2e%2e/editor.js', b'file:///tmp/editor.js']:
            with self.subTest(reference=reference):
                self.scripts['options-page.html'] = original.replace(b'common.js', reference)
                self.assert_rejected_unchanged()

    def test_manifest_options_path_cannot_escape_assets(self):
        self.candidate_manifest(options_ui={'page': '../options-page.html'})
        self.assert_rejected_unchanged()

    def test_manifest_entrypoint_must_be_an_exact_native_inventory_path(self):
        for page in ['nested/../options-page.html', 'options%2dpage.html',
                     './options-page.html', 'options-page.html?cache=1',
                     'options-page.html#section']:
            with self.subTest(page=page):
                self.candidate_manifest(options_ui={'page': page})
                self.assert_rejected_unchanged()

    def test_dynamic_script_loading_aborts_before_writes(self):
        self.scripts['common.js'] += b'\nimport("https://example.com/editor.js");'
        self.assert_rejected_unchanged()

    def test_dynamic_dom_script_loading_aborts_before_writes(self):
        self.scripts['common.js'] += b'\nconst script = document.createElement("script"); script.src = "https://example.com/editor.js";'
        self.assert_rejected_unchanged()

    def test_geturl_must_reference_existing_local_asset(self):
        self.scripts['common.js'] += b'\nchrome.runtime.getURL("missing.js");'
        self.assert_rejected_unchanged()

    def test_unsupported_api_after_regex_is_detected(self):
        self.scripts['common.js'] += b'\nconst pattern = /["\\/]/; chrome.storage.local.get(null);'
        self.assert_rejected_unchanged()

    def test_css_external_dependency_aborts_before_writes(self):
        self.scripts['options-page.css'] += b'\nbody { background: url(https://example.com/image.png) }'
        self.assert_rejected_unchanged()

    def test_unsafe_source_tree_aborts_before_any_asset_download(self):
        for alteration in [dict(path='../escape.js'), dict(path='/escape.js'),
                           dict(mode='120000'), dict(mode='160000', type='commit')]:
            with self.subTest(alteration=alteration):
                def mutate(tree):
                    tree['tree'][0].update(alteration)
                    return tree
                self.tree_transform = mutate
                self.requests.clear()
                self.assert_rejected_unchanged()
                self.assertFalse(any('raw.githubusercontent.com' in url for url in self.requests))

    def test_truncated_or_excessive_tree_is_rejected(self):
        for field, value in [('truncated', True), ('tree', [])]:
            with self.subTest(field=field):
                self.tree_transform = lambda tree: dict(tree, **{field: value})
                self.assert_rejected_unchanged()

    def test_asset_limits_and_aliases_are_rejected_before_download(self):
        for extra in [
            [dict(path='oversized.png', size=gate.MAX_ASSET_BYTES + 1)],
            [dict(path=f'extra-{i}.png', size=0) for i in range(gate.MAX_ASSETS)],
            [dict(path='OPTIONS-PAGE.JS', size=0)],
            [dict(path='one/a.js', size=0), dict(path='One/b.js', size=0)],
            [dict(path='collision', size=0), dict(path='collision/child.js', size=0)],
        ]:
            with self.subTest(extra=extra[:2]):
                def mutate(tree):
                    tree['tree'] += [dict(type='blob', mode='100644', sha='d' * 40, **entry) for entry in extra]
                    return tree
                self.tree_transform = mutate
                self.requests.clear()
                self.assert_rejected_unchanged()
                self.assertFalse(any('raw.githubusercontent.com' in url for url in self.requests))

    def test_raw_file_must_match_pinned_tree_blob(self):
        self.raw_transform = lambda name, data: data + b' changed'
        self.assert_rejected_unchanged()

    def test_javascript_is_checked_without_execution(self):
        self.scripts['common.js'] += b'\nthrow new Error("Never execute downloaded code in the gate");'
        self.assertTrue(gate.prepare(self.root, self.fetch)['changed'])

    def test_syntax_aborts_before_writes(self):
        original = self.scripts['todoist-shortcuts.js']
        for script in [original + b'\nconst = broken;']:
            with self.subTest(script=script[-50:]):
                self.scripts['todoist-shortcuts.js'] = script
                before = self.snapshot()
                with self.assertRaises((ValueError, RuntimeError)):
                    gate.prepare(self.root, self.fetch)
                self.assertEqual(before, self.snapshot())

    def test_local_vendor_drift_is_not_silently_overwritten(self):
        (self.root / gate.RESOURCE_DIR / 'options-page.css').write_text('local patch')
        self.assert_rejected_unchanged()

    def test_unrecorded_local_asset_is_rejected(self):
        (self.root / gate.RESOURCE_DIR / 'unrecorded.js').write_text('local patch')
        self.assert_rejected_unchanged()

    def test_symlinked_local_asset_is_rejected(self):
        path = self.root / gate.RESOURCE_DIR / 'common.js'
        path.unlink()
        path.symlink_to('options-page.js')
        self.assert_rejected_unchanged()

    def test_local_manifest_version_must_match_metadata(self):
        path = self.root / gate.RESOURCE_DIR / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['version'] = '111'
        path.write_text(json.dumps(manifest))
        metadata_path = self.root / 'upstream.json'
        metadata = json.loads(metadata_path.read_text())
        metadata['sha256']['manifest.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
        metadata_path.write_text(json.dumps(metadata))
        self.assert_rejected_unchanged()

    def test_missing_tag_is_not_replaced_with_master(self):
        def missing(url):
            if '/git/ref/' in url:
                raise RuntimeError('404')
            return self.fetch(url)
        before = self.snapshot()
        with self.assertRaises(RuntimeError):
            gate.prepare(self.root, missing)
        self.assertEqual(before, self.snapshot())

    def test_changed_store_between_jobs_aborts_without_writing(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            gate.prepare(self.root, self.fetch, expected='209')
        self.assertEqual(before, self.snapshot())

    def test_annotated_tag_resolves_to_commit(self):
        def annotated(url):
            if url.endswith('/git/ref/tags/v210'):
                return json.dumps({'object': {'type': 'tag', 'sha': 'b' * 40}}).encode()
            if url.endswith('/git/tags/' + 'b' * 40):
                return json.dumps({'object': {'type': 'commit', 'sha': SHA}}).encode()
            return self.fetch(url)
        gate.prepare(self.root, annotated)
        self.assertEqual(json.loads((self.root / 'upstream.json').read_text())['commit'], SHA)

    def test_only_complete_public_release_counts_as_published(self):
        complete = {'draft': False, 'prerelease': False, 'assets': [{'name': 'Todoiste.dmg', 'size': 100, 'digest': 'sha256:' + 'a' * 64}]}
        self.assertTrue(gate.is_published(complete))
        self.assertFalse(gate.is_published(dict(complete, draft=True)))
        with self.assertRaises(ValueError):
            gate.is_published(dict(complete, assets=[]))

if __name__ == '__main__':
    unittest.main()
