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

FAKE_GH = '''#!/usr/bin/env python3
import hashlib,json,os,sys
from pathlib import Path
p=Path(os.environ['FAKE_RELEASE'])
a=sys.argv[1:]
state=json.loads(p.read_text()) if p.exists() else None
if a[0]=='api':
    if state is None:
        sys.stderr.write('HTTP 404');sys.exit(1)
    print(json.dumps(state));sys.exit()
assert a[:2] in [['release','create'],['release','upload'],['release','edit']]
if a[1] in ['create','upload']:
    dmg=Path(a[3])
    assert state is None or state['draft'], 'Never replace public assets'
    digest='0'*64 if os.environ.get('BAD_DIGEST') else hashlib.sha256(dmg.read_bytes()).hexdigest()
    state={'draft':True,'prerelease':False,'assets':[{'name':'Todoiste.dmg','size':dmg.stat().st_size,'digest':'sha256:'+digest}]}
else:
    assert '--draft=false' in a
    state['draft']=False
p.write_text(json.dumps(state))
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
        resources = self.root / 'Todoiste/Resources'
        resources.mkdir(parents=True)
        for name in gate.FILES:
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
        gate.publish(self.root)
        self.assertEqual(self.git('rev-parse','HEAD'), self.git('rev-parse','v1.2.2^{commit}'))
        self.assertEqual(self.git('rev-parse','HEAD'), self.git('rev-parse','origin/main'))
        self.assertEqual('',self.git('status','--porcelain'))

if __name__ == '__main__':
    unittest.main()
