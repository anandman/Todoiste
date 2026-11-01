"""Ephemeral test signing keys; never uses a developer's Sparkle Keychain."""
import base64
from pathlib import Path
import subprocess


class TestSigner:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.key = self.directory / 'test-only-ed25519.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.key)],
                       check=True, capture_output=True)
        self.key.chmod(0o600)
        public = subprocess.check_output(['openssl', 'pkey', '-in', str(self.key),
                                          '-pubout', '-outform', 'DER'])
        self.public_key = base64.b64encode(public[-32:]).decode()

    def sign(self, content):
        message = self.directory / 'message'
        signature = self.directory / 'signature'
        message.write_bytes(content)
        subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key),
                        '-rawin', '-in', str(message), '-out', str(signature)],
                       check=True, capture_output=True)
        return base64.b64encode(signature.read_bytes()).decode()

    def appcast(self, dmg, version='1.2.4', build='8', url=None, minimum='13.0', length=None):
        url = url or f'https://github.com/anandman/Todoiste/releases/download/v{version}/Todoiste.dmg'
        content = f'''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><title>Todoiste</title><item>
<sparkle:version>{build}</sparkle:version><sparkle:shortVersionString>{version}</sparkle:shortVersionString>
<sparkle:minimumSystemVersion>{minimum}</sparkle:minimumSystemVersion>
<enclosure url="{url}" length="{len(dmg) if length is None else length}" type="application/octet-stream" sparkle:edSignature="{self.sign(dmg)}"/>
</item></channel></rss>
'''.encode()
        return content + f'<!-- sparkle-signatures:\nedSignature: {self.sign(content)}\nlength: {len(content)}\n-->\n'.encode()
