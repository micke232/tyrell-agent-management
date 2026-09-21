import hashlib
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from scripts.build_homebrew import build


class HomebrewPackageTests(unittest.TestCase):
    def fixture(self, root):
        path = root/'agent_hub_management-0.2.0a2-py3-none-any.whl'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('agent_hub_management-0.2.0a2.dist-info/METADATA','Name: agent-hub-management\nVersion: 0.2.0a2\n')
        return path

    def test_archive_formula_checksum_and_url_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);wheel=self.fixture(root)
            archive,formula=build(wheel,'example/agent-hub',root/'out')
            text=formula.read_text()
            self.assertIn(hashlib.sha256(archive.read_bytes()).hexdigest(),text)
            self.assertIn('https://github.com/example/agent-hub/releases/download/v0.2.0a2/'+archive.name,text)
            self.assertNotIn('@@',text)
            with tarfile.open(archive) as tar:
                self.assertEqual([Path(p).name for p in tar.getnames()],[wheel.name,'INSTALL.md'])
            original=archive.read_bytes()
            build(wheel,'example/agent-hub',root/'out')
            self.assertEqual(archive.read_bytes(),original)

    def test_repository_cannot_inject_ruby_or_urls(self):
        for repository in ('owner/repo"; bad', 'https://github.com/owner/repo', '../repo', 'owner/repo/extra'):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                build('/missing',repository,'/unused')

    def test_mismatched_wheel_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);wheel=self.fixture(root)
            renamed=wheel.with_name(wheel.name.replace('0.2.0a2','0.3.0'));wheel.rename(renamed)
            with self.assertRaises((KeyError,ValueError)):
                build(renamed,'example/agent-hub',root/'out')
