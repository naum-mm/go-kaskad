import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('proxy_setup', Path(__file__).resolve().parents[1] / 'tools/proxy_setup.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def result(stdout='', code=0):
    return subprocess.CompletedProcess([], code, stdout, '')


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('config/tls', 'current', 'le/live/panel.example.com', 'le/renewal'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.patch('ROOT', self.root)
        self.patch('LE', self.root / 'le')
        self.plan = dict(kind='host', conf=str(self.root / 'nginx.conf'),
                         webroot=str(self.root / 'acme'), nginx_webroot=str(self.root / 'acme'),
                         backend='127.0.0.1:{port}', domain='panel.example.com', port=10443,
                         conf_existed=True, certificate_owned=False, phase='prepared')
        self.conf = Path(self.plan['conf'])
        self.conf.write_text('# Existing website\n')
        (self.root / 'current/.env').write_text('MANAGEMENT_PORT=10443\n')

    def patch(self, name, *args, **kwargs):
        mock = patch.object(p, name, *args, **kwargs)
        self.addCleanup(mock.stop)
        return mock.start()

    def test_domain_validation_blocks_config_and_shell_injection(self):
        self.assertEqual(p.domain_name('Kaskad2.Box4one.ru.'), 'kaskad2.box4one.ru')
        for value in ('https://a.example.com', 'a.example.com:443', '*.example.com', 'a;include x;.com',
                      'a.example.com\nfoo', '-x.example.com', 'localhost', '1.2.3.4', 'a..com'):
            with self.subTest(value=value), self.assertRaises(p.SetupError):
                p.domain_name(value)

    def test_single_file_mount_maps_acme_subdirectory_and_preserves_configuration(self):
        for name in ('acme', 'le'):
            (self.root / name).mkdir(exist_ok=True)
        mounts = [dict(Type='bind', Destination='/etc/nginx/conf.d/default.conf', Source=str(self.conf)),
                  dict(Type='bind', Destination='/var/www', Source=str(self.root)),
                  dict(Type='bind', Destination='/etc/letsencrypt', Source=str(self.root / 'acme'))]
        container = dict(Name='/website-nginx', Mounts=mounts, NetworkSettings=dict(Networks={'website_network': {}}))
        config = '''include /etc/nginx/conf.d/*.conf;
# configuration file /etc/nginx/conf.d/default.conf:
location ^~ /.well-known/acme-challenge/ { root /var/www/acme; try_files $uri =404; }
'''
        # Docker's certificate mount has to use the canonical host location.
        with self.assertRaises(p.SetupError):
            p.docker_plan(container, config)
        mounts[-1]['Source'] = str(self.root / 'le')
        plan = p.docker_plan(container, config)
        self.assertEqual(plan['webroot'], str(self.root / 'acme'))
        self.assertEqual(plan['conf'], str(self.conf))
        self.assertEqual(plan['network'], 'website_network')

    def test_conflicting_names_are_checked_before_changes(self):
        for name in ('"panel.example.com"', '*.example.com', 'panel.*', '~^panel'):
            with self.subTest(name=name):
                self.patch('nginx', return_value=result('server_name ' + name + ';'))
                with self.assertRaises(p.SetupError):
                    p.setup(self.plan, 'panel.example.com', 10443, None)
                self.assertEqual(self.conf.read_text(), '# Existing website\n')

    def test_ipv6_listeners_follow_existing_proxy(self):
        config = p.https_config(dict(self.plan, ipv6=True))
        self.assertIn('listen [::]:80;', config)
        self.assertIn('listen [::]:443 ssl;', config)

    def test_snap_certbot_is_reused_when_missing_from_path(self):
        self.patch('shutil', wraps=p.shutil).which = lambda _: None
        self.patch('os', wraps=p.os).access = lambda path, mode: path == '/snap/bin/certbot'
        self.assertEqual(p.certbot_binary(), '/snap/bin/certbot')

    def test_free_server_and_unknown_listener_detection(self):
        self.patch('shutil', wraps=p.shutil).which = lambda _: None
        self.patch('run', return_value=result(''))
        self.assertEqual(p.discover(), {'kind': 'standalone'})
        self.patch('run', return_value=result('LISTEN 0 128 *:443 *:*'))
        with self.assertRaises(p.SetupError):
            p.discover()

    def test_certificate_shared_with_other_domains_is_not_changed(self):
        (self.root / 'le/live/panel.example.com/fullchain.pem').write_text('existing certificate')
        self.patch('nginx', return_value=result())
        self.patch('run', return_value=result('DNS:panel.example.com, DNS:other.example.com'))
        with self.assertRaises(p.SetupError):
            p.setup(self.plan, 'panel.example.com', 10443, None)
        self.assertEqual(self.conf.read_text(), '# Existing website\n')
        self.assertFalse((self.root / 'config/proxy.json').exists())

    def test_longest_mount_wins(self):
        (self.root / 'nested').mkdir()
        mounts = [dict(Type='bind', Destination='/var/www', Source=str(self.root)),
                  dict(Type='bind', Destination='/var/www/acme', Source=str(self.root / 'nested'))]
        self.assertEqual(p.mapped_path('/var/www/acme', mounts, directory=True), self.root / 'nested')

    def test_unsupported_or_unsafe_paths_are_rejected(self):
        with self.assertRaises(p.SetupError):
            p.mapped_path('/etc/nginx/conf.d', [])
        with self.assertRaises(p.SetupError):
            p.validate_paths(dict(self.plan, conf='/tmp/a;include'))

    def test_existing_file_inode_permissions_and_other_edits_survive(self):
        nginx = self.patch('nginx', return_value=result())
        inode = self.conf.stat().st_ino
        self.conf.chmod(0o600)
        p.set_block(self.plan, 'server { listen 80; }\n')
        self.assertEqual(self.conf.stat().st_ino, inode)
        self.assertEqual(self.conf.stat().st_mode & 0o777, 0o600)
        self.conf.write_text(self.conf.read_text() + '# Later website edit\n')
        p.remove(self.plan)
        self.assertEqual(self.conf.read_text(), '# Existing website\n# Later website edit\n')
        self.assertEqual(nginx.call_count, 4)

    def test_nginx_validation_failure_rolls_back_in_place_and_state_is_removable(self):
        nginx = self.patch('nginx', side_effect=[subprocess.CalledProcessError(1, ['nginx', '-t']), result(), result()])
        original = self.conf.read_text()
        with self.assertRaises(subprocess.CalledProcessError):
            p.set_block(self.plan, 'broken\n')
        self.assertEqual(self.conf.read_text(), original)
        self.assertEqual(json.loads((self.root / 'config/proxy.json').read_text())['block'], '')
        self.assertEqual(nginx.call_count, 3)

    def test_manually_changed_owned_block_blocks_removal(self):
        self.patch('nginx', return_value=result())
        p.set_block(self.plan, 'server {}\n')
        self.conf.write_text(self.conf.read_text().replace('server {}', 'server { listen 9999; }'))
        with self.assertRaises(p.SetupError):
            p.remove(self.plan)
        self.assertIn('9999', self.conf.read_text())

    def test_pending_block_is_removable_after_interruption(self):
        self.patch('nginx', return_value=result())
        self.conf.write_text('# Existing website\n' + p.BEGIN + 'server {}\n' + p.END)
        p.remove(dict(self.plan, pending_block='server {}\n'))
        self.assertEqual(self.conf.read_text(), '# Existing website\n')

    def test_retry_after_reload_checks_certificate_and_authentication(self):
        run = self.patch('run', side_effect=[result('\n000', 60), result('wrong site\n200'), result('auth required\n401')])
        sleep = self.patch('time', wraps=p.time)
        sleep.sleep = lambda _: None
        p.wait_http('https://panel.example.com/api/health', 'panel.example.com', 401)
        self.assertEqual(run.call_count, 3)
        args = run.call_args.args[0]
        self.assertNotIn('--insecure', args)
        self.assertIn('panel.example.com:443:127.0.0.1', args)

    def test_probe_failure_is_bounded_and_no_certificate_bypass(self):
        self.patch('run', return_value=result('\n000', 60))
        with self.assertRaises(p.SetupError):
            p.wait_http('https://panel.example.com/api/health', 'panel.example.com', 401, attempts=1)

    def test_setup_conflicting_domain_does_not_write_config(self):
        self.patch('nginx', return_value=result('server_name panel.example.com;'))
        before = self.conf.read_text()
        with self.assertRaises(p.SetupError):
            p.setup(self.plan, 'panel.example.com', 10443, None)
        self.assertEqual(self.conf.read_text(), before)
        self.assertFalse((self.root / 'config/proxy.json').exists())

    def test_directory_creation_is_readable_under_installer_umask(self):
        import os
        old = os.umask(0o077)
        try:
            path = self.root / 'acme/.well-known/acme-challenge'
            p.ensure_directory(path)
        finally:
            os.umask(old)
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o755)

    def test_container_renewal_path_is_reconfigured_on_host(self):
        self.patch('nginx', return_value=result())
        self.patch('wait_http')
        self.patch('copy_certificate')
        self.patch('shutil', wraps=p.shutil).which = lambda _: '/usr/bin/certbot'
        calls = []
        renewal = self.root / 'le/renewal/panel.example.com.conf'
        renewal.write_text('[[webroot_map]]\npanel.example.com = /var/www/certbot\n')
        def command(args, **kwargs):
            calls.append(args)
            if args[:2] == ['/usr/bin/certbot', 'reconfigure']:
                renewal.write_text('[[webroot_map]]\npanel.example.com = ' + self.plan['webroot'] + '\n')
            if args[:2] == ['systemctl', 'list-timers']:
                return result('snap.certbot.renew.timer snap.certbot.renew.service')
            return result()
        self.patch('run', side_effect=command)
        p.finish_setup(self.plan, self.root / 'auth.header')
        certcalls = [c for c in calls if c[0] == '/usr/bin/certbot']
        self.assertEqual([c[1] for c in certcalls], ['certonly', 'reconfigure'])
        self.assertIn(self.plan['webroot'], certcalls[1])
        self.assertFalse(any(c[:3] == ['systemctl', 'enable', '--now'] for c in calls))
        self.assertEqual(json.loads((self.root / 'config/proxy.json').read_text())['phase'], 'ready')
        hook = self.root / 'le/renewal-hooks/deploy/gokaskad-panel'
        self.assertIn('/usr/bin/python3 /opt/go-kaskad/config/proxy_setup.py renew', hook.read_text())

    def test_success_exit_without_saving_renewal_webroot_is_not_accepted(self):
        self.patch('nginx', return_value=result())
        self.patch('wait_http')
        self.patch('shutil', wraps=p.shutil).which = lambda _: '/usr/bin/certbot'
        (self.root / 'le/renewal/panel.example.com.conf').write_text('[[webroot_map]]\npanel.example.com = /container-only/path\n')
        self.patch('run', return_value=result())
        with self.assertRaisesRegex(p.SetupError, 'не сохранил webroot'):
            p.finish_setup(self.plan, None)
        self.assertNotEqual(json.loads((self.root / 'config/proxy.json').read_text())['phase'], 'ready')

    def test_preexisting_certificate_is_kept_on_uninstall(self):
        self.patch('nginx', return_value=result())
        run = self.patch('run', return_value=result())
        (self.root / 'le/renewal/panel.example.com.conf').write_text('existing renewal config')
        p.set_block(self.plan, 'server {}\n')
        p.remove(self.plan)
        self.assertFalse(run.called)
        self.assertTrue((self.root / 'le/renewal/panel.example.com.conf').exists())

    def test_new_host_renewal_configuration_does_not_need_reconfigure(self):
        self.patch('nginx', return_value=result())
        self.patch('wait_http')
        self.patch('copy_certificate')
        self.patch('shutil', wraps=p.shutil).which = lambda _: '/usr/bin/certbot'
        (self.root / 'le/renewal/panel.example.com.conf').write_text('[[webroot_map]]\npanel.example.com = ' + self.plan['webroot'] + '\n')
        run = self.patch('run', return_value=result('certbot.timer'))
        p.finish_setup(self.plan, None)
        self.assertFalse(any(c.args[0][1:2] == ['reconfigure'] for c in run.call_args_list))

    def test_https_uses_verified_domain_tls_and_forwards_auth(self):
        config = p.https_config(dict(self.plan, kind='docker', backend=p.ALIAS + ':443'))
        for part in ('proxy_ssl_verify on;', 'proxy_ssl_name panel.example.com;', 'resolver 127.0.0.11',
                     'proxy_set_header Authorization $http_authorization;', 'proxy_read_timeout 130s;'):
            self.assertIn(part, config)

    def test_renewal_updates_panel_files_without_changing_credentials(self):
        source = self.root / 'le/live/panel.example.com'
        (source / 'fullchain.pem').write_text('public certificate')
        (source / 'privkey.pem').write_text('private key')
        compose = self.patch('compose', return_value=result())
        p.copy_certificate(self.plan)
        self.assertEqual((self.root / 'config/tls/server.crt').read_text(), 'public certificate')
        self.assertEqual((self.root / 'config/tls/server.key').stat().st_mode & 0o777, 0o600)
        self.assertEqual(compose.call_count, 2)


if __name__ == '__main__':
    unittest.main()
