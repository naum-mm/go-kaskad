"""Release branding must not change existing deployment identities."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseCompatibilityTests(unittest.TestCase):
    def test_version_matches_changelog_and_panel(self):
        version = (ROOT / 'VERSION').read_text().strip()
        self.assertRegex(version, r'^\d+\.\d+\.\d+$')
        changelog = (ROOT / 'CHANGELOG.md').read_text()
        self.assertEqual(re.search(r'^## v([^ ]+) —', changelog, re.M).group(1), version)
        self.assertIn('>v' + version + '</span>', (ROOT / 'web/gui/index.html').read_text())

    def test_deployment_identity_is_preserved_across_rename(self):
        self.assertTrue((ROOT / 'compose.yaml').read_text().startswith('name: gokaskad\n'))
        for path in ('install.sh', 'tools/install_core.sh', 'tools/menu.sh', 'uninstall.sh'):
            self.assertIn('root=/opt/go-kaskad', (ROOT / path).read_text())
        for path in ('tools/lifecycle.py', 'tools/proxy_setup.py'):
            self.assertIn("ROOT = Path('/opt/go-kaskad')", (ROOT / path).read_text())
        self.assertIn('"X-Gokaskad"', (ROOT / 'web/api/server.py').read_text())
        self.assertIn("'X-Gokaskad'", (ROOT / 'web/gui/app.js').read_text())

    def test_installers_download_from_renamed_repository(self):
        for path in ('install.sh', 'tools/install_core.sh'):
            script = (ROOT / path).read_text()
            self.assertIn('https://codeload.github.com/naum-mm/kaskad/tar.gz/refs/heads/main', script)
            self.assertNotIn('naum-mm/go-kaskad', script)
