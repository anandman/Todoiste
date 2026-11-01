"""Use real Git commits/pushes and a fake GitHub transport to test retries."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from test_store_release import gate
from sparkle_test_support import TestSigner

FAKE_GH = r'''#!/usr/bin/env python3
import hashlib,json,os,sys
from urllib.parse import urlsplit,parse_qs
from pathlib import Path
p=Path(os.environ['FAKE_RELEASE'])
a=sys.argv[1:]
state=json.loads(p.read_text()) if p.exists() else None
method=a[a.index('--method')+1] if '--method' in a else 'GET'
endpoint=next(x for x in a[1:] if x.startswith(('repos/','https://')))
if '/releases/tags/' in endpoint:
    # GitHub's tag endpoint excludes drafts even for an owner.
    if state is None or state['draft']:
        sys.stderr.write('HTTP 404');sys.exit(1)
    print(json.dumps(state));sys.exit()
if endpoint.endswith('/releases?per_page=100'):
    pages=[[state] if state else []]
    if os.environ.get('DUPLICATE_DRAFT') and state:
        pages.append([dict(state,id=124)])
    print(json.dumps(pages));sys.exit()
if method=='POST' and endpoint.endswith('/releases'):
    assert state is None, 'Never create a duplicate draft'
    data=json.load(sys.stdin)
    state=dict(data,id=123,prerelease=False,assets=[])
elif method=='DELETE' and '/releases/assets/' in endpoint:
    assert state['draft'], 'Never replace public assets'
    asset_id=int(endpoint.rsplit('/',1)[1])
    state['assets']=[asset for asset in state['assets'] if asset['id'] != asset_id]
elif method=='POST' and endpoint.startswith('https://uploads.github.com/'):
    assert '/releases/123/assets?' in endpoint and state['draft']
    name=parse_qs(urlsplit(endpoint).query)['name'][0]
    if os.environ.get('FAIL_UPLOAD') == name:
        sys.stderr.write('transport failed');sys.exit(1)
    data=Path(a[a.index('--input')+1]).read_bytes()
    digest='0'*64 if os.environ.get('BAD_DIGEST') in ('1',name) else hashlib.sha256(data).hexdigest()
    asset={'id':456 if name=='Todoiste.dmg' else 457,'name':name,'size':len(data),'digest':'sha256:'+digest}
    state['assets'].append(asset)
    p.write_text(json.dumps(state));print(json.dumps(asset));sys.exit()
elif method=='PATCH' and endpoint.endswith('/releases/123'):
    state.update(json.load(sys.stdin))
elif method=='GET' and endpoint.endswith('/releases/123'):
    if os.environ.get('PUBLISH_DRAFT_ON_RECHECK'):
        state['draft']=False
        p.write_text(json.dumps(state))
    print(json.dumps(state));sys.exit()
else:
    raise AssertionError('Unexpected GitHub call: '+str(a))
p.write_text(json.dumps(state));print(json.dumps(state))
'''

class PublicationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'work'
        self.root.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        self.signer = TestSigner(self.base)
        (self.root / 'upstream.json').write_text(json.dumps({'version':'210','todoiste_version':'1.2.4','todoiste_build':'8'}))
        (self.root / '.gitignore').write_text('build/\n')
        (self.root / 'project.yml').write_text(f'original project\n    macOS: "13.0"\n    SPARKLE_PUBLIC_KEY: "{self.signer.public_key}"\n')
        resources = self.root / gate.RESOURCE_DIR
        resources.mkdir(parents=True)
        for name in ['manifest.json', 'mousetrap.js', 'todoist-shortcuts.js', 'options-page.html']:
            (resources / name).write_text('original script')
        self.git('add', '.')
        self.git('commit', '-m', 'Checked source')
        subprocess.run(['git','init','--bare',str(self.base / 'remote')], check=True, capture_output=True)
        self.git('remote', 'add', 'origin', str(self.base / 'remote'))
        self.git('push', '-u', 'origin', 'main')
        self.state = self.base / 'release.json'
        binpath = self.base / 'bin'
        binpath.mkdir()
        (binpath / 'gh').write_text(FAKE_GH)
        (binpath / 'gh').chmod(0o755)
        self.dmg = self.root / 'build/Build/Products/Release/Todoiste.dmg'
        self.dmg.parent.mkdir(parents=True)
        self.dmg.write_bytes(b'verified DMG placeholder for transport test')
        self.feed = self.dmg.with_name('appcast.xml')
        self.feed.write_bytes(self.signer.appcast(self.dmg.read_bytes()))
        env = {'GITHUB_SHA':self.git('rev-parse','HEAD'), 'FAKE_RELEASE':str(self.state),'PATH':str(binpath)+os.pathsep+os.environ['PATH']}
        self.patcher = patch.dict(os.environ, env)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def git(self, *args):
        return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.DEVNULL,text=True).strip()

    def test_draft_lookup_uses_list_when_tag_endpoint_returns_404(self):
        draft = {'id':123,'tag_name':'v1.2.4','draft':True,'prerelease':False,'assets':[]}
        self.state.write_text(json.dumps(draft))
        self.assertEqual(draft, gate.release_info('v1.2.4'))

    def test_multiple_pending_drafts_stop_before_upload_or_publication(self):
        self.state.write_text(json.dumps({'id':123,'tag_name':'v1.2.4','draft':True,'assets':[]}))
        before = self.state.read_bytes()
        with patch.dict(os.environ, {'DUPLICATE_DRAFT':'1'}), self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertEqual(before, self.state.read_bytes())
        self.assertEqual('',self.git('tag'))

    def test_workflow_fix_after_pending_tag_does_not_require_retagging(self):
        self.git('tag','v1.2.4')
        tagged = self.git('rev-parse','HEAD')
        (self.root / 'workflow.yml').write_text('publication fix')
        self.git('add','workflow.yml')
        self.git('commit','-m','Fix publisher')
        self.git('push','origin','main')
        os.environ['GITHUB_SHA'] = self.git('rev-parse','HEAD')
        gate.publish(self.root)
        self.assertEqual(tagged,self.git('rev-parse','v1.2.4^{commit}'))
        self.assertFalse(json.loads(self.state.read_text())['draft'])

    def test_changed_app_source_cannot_reuse_pending_tag(self):
        (self.root / 'Todoiste/App.swift').write_text('old source')
        self.git('add','Todoiste/App.swift')
        self.git('commit','-m','App')
        self.git('tag','v1.2.4')
        (self.root / 'Todoiste/App.swift').write_text('different source')
        self.git('add','Todoiste/App.swift')
        self.git('commit','-m','Change app')
        self.git('push','origin','main')
        os.environ['GITHUB_SHA'] = self.git('rev-parse','HEAD')
        with self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertFalse(self.state.exists())

    def test_completed_publication_never_replaces_asset(self):
        gate.publish(self.root)
        before = self.state.read_bytes()
        head = self.git('rev-parse','HEAD')
        with self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertEqual(before,self.state.read_bytes())
        self.assertEqual(head,self.git('rev-parse','HEAD'))
        self.assertEqual('v1.2.4', self.git('tag'))
        self.assertFalse(json.loads(before)['draft'])

    def test_upload_digest_failure_can_retry_existing_tag_and_draft(self):
        with patch.dict(os.environ, {'BAD_DIGEST':'1'}), self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertTrue(json.loads(self.state.read_text())['draft'])
        head = self.git('rev-parse','HEAD')
        gate.publish(self.root)
        self.assertFalse(json.loads(self.state.read_text())['draft'])
        self.assertEqual(head,self.git('rev-parse','HEAD'))

    def test_both_assets_are_required_and_have_verified_remote_digests(self):
        gate.publish(self.root)
        assets = {a['name']:a for a in json.loads(self.state.read_text())['assets']}
        self.assertEqual({'Todoiste.dmg', 'appcast.xml'}, set(assets))
        for path in (self.dmg, self.feed):
            self.assertEqual('sha256:' + hashlib.sha256(path.read_bytes()).hexdigest(), assets[path.name]['digest'])

    def test_appcast_digest_mismatch_stays_draft_then_retries_same_id(self):
        with patch.dict(os.environ, {'BAD_DIGEST':'appcast.xml'}), self.assertRaises(ValueError):
            gate.publish(self.root)
        first = json.loads(self.state.read_text())
        self.assertTrue(first['draft'])
        gate.publish(self.root)
        final = json.loads(self.state.read_text())
        self.assertEqual(first['id'], final['id'])
        self.assertFalse(final['draft'])
        self.assertEqual(2, len(final['assets']))

    def test_failed_second_upload_keeps_draft_and_can_retry_pair(self):
        with patch.dict(os.environ, {'FAIL_UPLOAD':'appcast.xml'}), self.assertRaises(RuntimeError):
            gate.publish(self.root)
        first = json.loads(self.state.read_text())
        self.assertTrue(first['draft'])
        self.assertEqual(['Todoiste.dmg'], [a['name'] for a in first['assets']])
        gate.publish(self.root)
        final = json.loads(self.state.read_text())
        self.assertEqual(first['id'], final['id'])
        self.assertFalse(final['draft'])
        self.assertEqual(2, len(final['assets']))

    def test_tampered_feed_or_archive_cannot_create_tag_or_draft(self):
        self.feed.write_bytes(self.feed.read_bytes().replace(b'Todoiste</title>', b'Malware!</title>'))
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            gate.publish(self.root)
        self.assertFalse(self.state.exists())
        self.assertEqual('', self.git('tag'))

    def test_missing_appcast_cannot_create_tag_or_draft(self):
        self.feed.unlink()
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            gate.publish(self.root)
        self.assertFalse(self.state.exists())
        self.assertEqual('', self.git('tag'))

    def test_draft_published_by_someone_else_is_rechecked_before_asset_delete(self):
        original = {'id':123, 'tag_name':'v1.2.4', 'draft':True, 'prerelease':False,
                    'assets':[{'id':456, 'name':'Todoiste.dmg', 'size':100, 'digest':'sha256:'+'a'*64}]}
        self.state.write_text(json.dumps(original))
        with patch.dict(os.environ, {'PUBLISH_DRAFT_ON_RECHECK':'1'}), self.assertRaises(ValueError):
            gate.publish(self.root)
        after = json.loads(self.state.read_text())
        self.assertFalse(after['draft'])
        self.assertEqual(original['assets'], after['assets'])

    def test_incomplete_public_sparkle_release_is_rejected_but_legacy_is_readable(self):
        legacy = {'tag_name':'v1.2.4','draft':False,'prerelease':False,'assets':[
            {'name':'Todoiste.dmg','size':100,'digest':'sha256:'+'a'*64}]}
        self.assertTrue(gate.is_published(legacy))
        with self.assertRaises(ValueError):
            gate.is_published(dict(legacy, tag_name='v1.2.5'))

    def test_concurrent_main_push_stops_before_tag_or_release(self):
        self.git('commit','--allow-empty','-m','Concurrent change')
        self.git('push','origin','main')
        with self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertFalse(self.state.exists())
        self.assertEqual('',self.git('tag'))

    def test_changed_update_is_committed_and_tagged_only_at_publication(self):
        with (self.root / 'project.yml').open('a') as project:
            project.write('updated project\n')
        asset = self.root / gate.RESOURCE_DIR / 'nested/new-option.js'
        asset.parent.mkdir()
        asset.write_text('new source asset')
        gate.publish(self.root)
        self.assertEqual(self.git('rev-parse','HEAD'), self.git('rev-parse','v1.2.4^{commit}'))
        self.assertEqual(self.git('rev-parse','HEAD'), self.git('rev-parse','origin/main'))
        self.assertEqual('',self.git('status','--porcelain'))

    def test_unexpected_untracked_source_cannot_be_published(self):
        (self.root / 'Todoiste/unreviewed.swift').write_text('unexpected source')
        with self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertFalse(self.state.exists())

if __name__ == '__main__':
    unittest.main()
