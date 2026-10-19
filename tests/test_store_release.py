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
        (self.root / 'upstream.json').write_text(json.dumps(metadata))
        self.requests = []
        self.version = '210'
        self.manifest_version = '210'
        self.scripts = {n: (ROOT / 'Todoiste/Resources' / n).read_bytes() for n in gate.FILES}

    def fetch(self, url):
        self.requests.append(url)
        if url == gate.STORE_URL:
            return f'<link rel="canonical" href="{gate.STORE_URL}"><script>Version 999</script><div>Version</div><div>{self.version}</div>'.encode()
        if url.endswith('/git/ref/tags/v210'):
            return json.dumps({'object': {'type': 'commit', 'sha': SHA}}).encode()
        if f'/{SHA}/src/' in url:
            name = url.rsplit('/', 1)[1]
            return json.dumps({'version': self.manifest_version}).encode() if name == 'manifest.json' else self.scripts[name]
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

    def test_new_option_or_help_contract_or_syntax_aborts_before_writes(self):
        original = self.scripts['todoist-shortcuts.js']
        for script in [original + b"\noptions['new-option'];", original.replace(b'Configure todoist-shortcuts options', b'New settings link'), original + b'\nconst = broken;']:
            with self.subTest(script=script[-50:]):
                self.scripts['todoist-shortcuts.js'] = script
                before = self.snapshot()
                with self.assertRaises((ValueError, RuntimeError)):
                    gate.prepare(self.root, self.fetch)
                self.assertEqual(before, self.snapshot())

    def test_local_vendor_drift_is_not_silently_overwritten(self):
        (self.root / 'Todoiste/Resources/mousetrap.js').write_text('local patch')
        with self.assertRaises(ValueError):
            gate.prepare(self.root, self.fetch)

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
