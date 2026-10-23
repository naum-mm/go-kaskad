"""Run only in the disposable container described in the CI workflow."""
from pathlib import Path
import os
import shutil
import subprocess
import unittest

ROOT = Path('/opt/go-kaskad')
FAKE = Path('/tmp/gokaskad-fake-bin')
LOG = Path('/tmp/gokaskad-uninstall.log')

class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ.get('GOKASKAD_UNINSTALL_TEST_CONTAINER'), '1', 'Use an isolated test container')
        self.assertFalse(Path('/var/run/docker.sock').exists())
        shutil.rmtree(ROOT, ignore_errors=True)
        release = ROOT / 'releases/test'
        release.mkdir(parents=True)
        (release / 'compose.yaml').write_text('name: gokaskad\n')
        (release / '.env').write_text('IPTABLES_BACKEND=nft\n')
        (ROOT / 'current').symlink_to(release)
        state = ROOT / 'install-state'
        state.mkdir()
        (state / 'new-packages').write_text('new-package\n')
        (state / 'ip-forward.before').write_text('0\n')
        (state / 'sysctl.existed').touch()
        (state / 'sysctl.previous').write_text('# previous config\n')
        Path('/etc/sysctl.d/90-gokaskad.conf').write_text('net.ipv4.ip_forward=1\n')
        FAKE.mkdir(exist_ok=True)
        LOG.write_text('')
        code = '''#!/usr/local/bin/python3
import os,sys
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
with open('/tmp/gokaskad-uninstall.log','a') as log: log.write(name+' '+' '.join(args)+'\\n')
if name=='iptables-nft' and '-C' in args: sys.exit(1)
if name=='docker' and args==['ps','-aq'] and os.environ.get('FAKE_SHARED'): print('other-container')
if name=='apt-get' and args[:2]==['-s','purge']:
 print('Purg new-package')
 if os.environ.get('FAKE_DEPENDENT'): print('Remv unrelated-app')
'''
        for name in ('docker', 'iptables-nft', 'apt-get', 'sysctl'):
            target = FAKE / name
            target.write_text(code)
            target.chmod(0o755)
        self.env = dict(os.environ, PATH=str(FAKE) + ':' + os.environ['PATH'])

    def run_uninstall(self, *args, **env):
        return subprocess.run(['bash', '/src/uninstall.sh', *args], env=dict(self.env, **env), text=True, capture_output=True)

    def test_remove_project_and_restore_previous_setting(self):
        result = self.run_uninstall()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(ROOT.exists())
        self.assertEqual(Path('/etc/sysctl.d/90-gokaskad.conf').read_text(), '# previous config\n')
        log = LOG.read_text()
        self.assertIn('sysctl -w net.ipv4.ip_forward=0', log)
        self.assertIn('down --volumes --rmi local --remove-orphans', log)
        self.assertNotIn('apt-get', log)
        self.assertNotIn('-F FORWARD', log)
        self.assertNotIn('-F PREROUTING', log)

    def test_remove_only_recorded_packages(self):
        result = self.run_uninstall('--remove-packages')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('apt-get purge -y new-package', LOG.read_text())
        self.assertFalse(ROOT.exists())

    def test_native_architecture_package_name_is_recognized(self):
        arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
        (ROOT / 'install-state/new-packages').write_text('new-package:' + arch + '\n')
        result = self.run_uninstall('--remove-packages')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('apt-get purge -y new-package:' + arch, LOG.read_text())

    def test_regular_uninstall_preserves_forwarding_for_other_containers(self):
        result = self.run_uninstall(FAKE_SHARED='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(ROOT.exists())
        self.assertNotIn('sysctl -w', LOG.read_text())

    def test_shared_docker_blocks_package_removal(self):
        result = self.run_uninstall('--remove-packages', FAKE_SHARED='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((ROOT / 'install-state/new-packages').exists())
        self.assertNotIn('apt-get purge', LOG.read_text())

    def test_unrelated_dependency_blocks_package_removal(self):
        result = self.run_uninstall('--remove-packages', FAKE_DEPENDENT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('apt-get purge', LOG.read_text())

    def test_legacy_installation_does_not_guess_packages(self):
        (ROOT / 'install-state/new-packages').unlink()
        result = self.run_uninstall('--remove-packages')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(LOG.read_text(), '')

if __name__ == '__main__':
    unittest.main()
