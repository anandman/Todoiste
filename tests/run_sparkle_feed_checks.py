"""Exercise the real Sparkle client against isolated, locally signed fixtures."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from threading import Thread
import uuid

from sparkle_test_support import TestSigner


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def main():
    app, binary = map(lambda p: Path(p).resolve(), sys.argv[1:])
    with tempfile.TemporaryDirectory(prefix='sparkle-client-fixture-') as temporary:
        root = Path(temporary)
        signer = TestSigner(root)
        server = ThreadingHTTPServer(('127.0.0.1', 0),
            lambda *args, **kwargs: QuietHandler(*args, directory=str(root), **kwargs))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            fixture = root / 'Todoiste.app'
            subprocess.run(['ditto', str(app), str(fixture)], check=True)
            original = plistlib.loads((fixture / 'Contents/Info.plist').read_bytes())
            newer = signer.appcast(b'unused archive fixture', version='1.2.5', build='9', minimum='14.0')
            cases = [('newer', newer, 'newer'),
                     ('current', signer.appcast(b'unused archive fixture', build='8', minimum='14.0'), 'current'),
                     ('tampered', newer.replace(b'<title>Todoiste', b'<title>Changed!'), 'error'),
                     ('missing', None, 'error')]
            for name, feed, expected in cases:
                if feed is not None:
                    (root / f'{name}.xml').write_bytes(feed)
                info = dict(original)
                info.update(CFBundleIdentifier='com.todoiste.updater-test.' + uuid.uuid4().hex,
                            CFBundleVersion='8', CFBundleShortVersionString='1.2.4',
                            SUFeedURL=f'http://127.0.0.1:{server.server_port}/{name}.xml',
                            SUPublicEDKey=signer.public_key, SURequireSignedFeed=True,
                            SUVerifyUpdateBeforeExtraction=True, SUEnableAutomaticChecks=False,
                            SUAllowsAutomaticUpdates=False, SUAutomaticallyUpdate=False,
                            NSAppTransportSecurity={'NSAllowsLocalNetworking': True})
                (fixture / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
                subprocess.run(['codesign', '--force', '--sign', '-', str(fixture)],
                               check=True, capture_output=True)
                subprocess.run([str(binary), str(fixture), expected], check=True, timeout=30)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
