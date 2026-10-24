"""Regression checks for restoring configuration while TLS directories are mounted."""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
try:
    spec = importlib.util.spec_from_file_location('lifecycle', TOOLS / 'lifecycle.py')
    lifecycle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lifecycle)
finally:
    sys.path.pop(0)


class LifecycleTests(unittest.TestCase):
    def test_rollback_preserves_mounted_tls_directory_and_reloads_certificate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'installation'
            tls = root / 'config/tls'
            tls.mkdir(parents=True)
            (root / 'current').mkdir()
            (root / 'current/.env').write_text('MANAGEMENT_PORT=8443\n')
            (tls / 'server.crt').write_text('previous certificate')
            (tls / 'server.key').write_text('previous key')
            saved = Path(temporary) / 'backup'
            shutil.copytree(root / 'config', saved / 'config')
            shutil.copy2(root / 'current/.env', saved / 'env')
            inode = tls.stat().st_ino
            (tls / 'server.crt').write_text('new failed certificate')
            (root / 'config/installation.json').write_text('{"mode":"failed"}')
            with patch.object(lifecycle, 'ROOT', root), patch.object(lifecycle.proxy, 'ROOT', root), \
                 patch.object(lifecycle.proxy, 'LE', Path(temporary) / 'letsencrypt'), \
                 patch.object(lifecycle, 'compose') as compose:
                lifecycle.restore(saved)
            self.assertEqual(tls.stat().st_ino, inode)
            self.assertEqual((tls / 'server.crt').read_text(), 'previous certificate')
            self.assertEqual((tls / 'server.key').read_text(), 'previous key')
            self.assertFalse((root / 'config/installation.json').exists())
            compose.assert_any_call('exec', '-T', 'nginx', 'nginx', '-s', 'reload')


if __name__ == '__main__':
    unittest.main()
