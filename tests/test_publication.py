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

FAKE_GH = r'''#!/usr/bin/env python3
import hashlib,json,os,sys
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
    state['assets']=[]
elif method=='POST' and endpoint.startswith('https://uploads.github.com/'):
    assert '/releases/123/assets?' in endpoint and state['draft']
    data=Path(a[a.index('--input')+1]).read_bytes()
    digest='0'*64 if os.environ.get('BAD_DIGEST') else hashlib.sha256(data).hexdigest()
    asset={'id':456,'name':'Todoiste.dmg','size':len(data),'digest':'sha256:'+digest}
    state['assets']=[asset]
    p.write_text(json.dumps(state));print(json.dumps(asset));sys.exit()
elif method=='PATCH' and endpoint.endswith('/releases/123'):
    state.update(json.load(sys.stdin))
elif method=='GET' and endpoint.endswith('/releases/123'):
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
        (self.root / 'upstream.json').write_text(json.dumps({'version':'210','todoiste_version':'1.2.2'}))
        (self.root / '.gitignore').write_text('build/\n')
        (self.root / 'project.yml').write_text('original project')
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
        env = {'GITHUB_SHA':self.git('rev-parse','HEAD'), 'FAKE_RELEASE':str(self.state),'PATH':str(binpath)+os.pathsep+os.environ['PATH']}
        self.patcher = patch.dict(os.environ, env)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def git(self, *args):
        return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.DEVNULL,text=True).strip()

    def test_draft_lookup_uses_list_when_tag_endpoint_returns_404(self):
        draft = {'id':123,'tag_name':'v1.2.2','draft':True,'prerelease':False,'assets':[]}
        self.state.write_text(json.dumps(draft))
        self.assertEqual(draft, gate.release_info('v1.2.2'))

    def test_multiple_pending_drafts_stop_before_upload_or_publication(self):
        self.state.write_text(json.dumps({'id':123,'tag_name':'v1.2.2','draft':True,'assets':[]}))
        before = self.state.read_bytes()
        with patch.dict(os.environ, {'DUPLICATE_DRAFT':'1'}), self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertEqual(before, self.state.read_bytes())
        self.assertEqual('',self.git('tag'))

    def test_workflow_fix_after_pending_tag_does_not_require_retagging(self):
        self.git('tag','v1.2.2')
        tagged = self.git('rev-parse','HEAD')
        (self.root / 'workflow.yml').write_text('publication fix')
        self.git('add','workflow.yml')
        self.git('commit','-m','Fix publisher')
        self.git('push','origin','main')
        os.environ['GITHUB_SHA'] = self.git('rev-parse','HEAD')
        gate.publish(self.root)
        self.assertEqual(tagged,self.git('rev-parse','v1.2.2^{commit}'))
        self.assertFalse(json.loads(self.state.read_text())['draft'])

    def test_changed_app_source_cannot_reuse_pending_tag(self):
        (self.root / 'Todoiste/App.swift').write_text('old source')
        self.git('add','Todoiste/App.swift')
        self.git('commit','-m','App')
        self.git('tag','v1.2.2')
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
        self.assertEqual('v1.2.2', self.git('tag'))
        self.assertFalse(json.loads(before)['draft'])

    def test_upload_digest_failure_can_retry_existing_tag_and_draft(self):
        with patch.dict(os.environ, {'BAD_DIGEST':'1'}), self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertTrue(json.loads(self.state.read_text())['draft'])
        head = self.git('rev-parse','HEAD')
        gate.publish(self.root)
        self.assertFalse(json.loads(self.state.read_text())['draft'])
        self.assertEqual(head,self.git('rev-parse','HEAD'))

    def test_concurrent_main_push_stops_before_tag_or_release(self):
        self.git('commit','--allow-empty','-m','Concurrent change')
        self.git('push','origin','main')
        with self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertFalse(self.state.exists())
        self.assertEqual('',self.git('tag'))

    def test_changed_update_is_committed_and_tagged_only_at_publication(self):
        (self.root / 'project.yml').write_text('updated project')
        asset = self.root / gate.RESOURCE_DIR / 'nested/new-option.js'
        asset.parent.mkdir()
        asset.write_text('new source asset')
        gate.publish(self.root)
        self.assertEqual(self.git('rev-parse','HEAD'), self.git('rev-parse','v1.2.2^{commit}'))
        self.assertEqual(self.git('rev-parse','HEAD'), self.git('rev-parse','origin/main'))
        self.assertEqual('',self.git('status','--porcelain'))

    def test_unexpected_untracked_source_cannot_be_published(self):
        (self.root / 'Todoiste/unreviewed.swift').write_text('unexpected source')
        with self.assertRaises(ValueError):
            gate.publish(self.root)
        self.assertFalse(self.state.exists())

if __name__ == '__main__':
    unittest.main()
