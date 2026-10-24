"""Installer orchestration regression tests. Run only in the disposable CI container.

All host commands/ACME are mocked; real nginx/TLS is checked by proxy_smoke.py.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path('/opt/go-kaskad')
SITE = Path('/opt/website')
BIN = Path('/tmp/install-fake-bin')
LOG = Path('/tmp/install-commands.log')

FAKE = r'''#!/usr/local/bin/python3
import base64,json,os,sys,tarfile,sqlite3
from pathlib import Path
name=Path(sys.argv[0]).name
a=sys.argv[1:]
if name in ('docker','apt-get'):
 sys.stdin.buffer.read()  # Deliberately drain stdin, just like an attached Docker exec.
root=Path('/opt/go-kaskad')
site=Path('/opt/website')
with open('/tmp/install-commands.log','a') as f:f.write(name+' '+' '.join(a)+'\n')
if name=='docker' and a[:2]==['compose','up'] and os.environ.get('FAKE_UPDATE_FAIL') and Path.cwd().name.startswith('update.'):
 sys.exit(1)
if name=='ss':
 if ':443' in ' '.join(a):print('LISTEN 0 128 *:443 *:* users:(("docker-proxy"))')
elif name=='docker':
 if a[:2]==['ps','-q']:print('website-id')
 elif a[:1]==['inspect']:
  print(json.dumps([{'Id':'website-id','Name':'/website-nginx','Mounts':[
   {'Type':'bind','Source':str(site/'nginx.conf'),'Destination':'/etc/nginx/conf.d/default.conf'},
   {'Type':'bind','Source':str(site/'acme'),'Destination':'/var/www/certbot'},
   {'Type':'bind','Source':'/etc/letsencrypt','Destination':'/etc/letsencrypt'}],
   'NetworkSettings':{'Networks':{'website_default':{}},'Ports':{
    '443/tcp':[{'HostPort':'443'}],'80/tcp':[{'HostPort':'80'}]}}}]))
 elif a[:1]==['exec'] and '-T' in a:
  print('include /etc/nginx/conf.d/*.conf;\n# configuration file /etc/nginx/conf.d/default.conf:\n'+(site/'nginx.conf').read_text())
 elif a[:3]==['compose','exec','-T'] and '--machine' in a:
  port=os.environ.get('FAKE_RULE_PORT')
  print(json.dumps({'rules':[{'protocol':'tcp','input_port':int(port),'enabled':True}] if port else []}))
 elif a[:3]==['compose','exec','-T'] and 'api' in a:print('{"ready": true}')
 elif a[:3]==['compose','exec','-T'] and 'worker' in a and 'python3' in a:
  db=sqlite3.connect(':memory:');db.execute('CREATE TABLE rules (id TEXT PRIMARY KEY, data TEXT NOT NULL)');sys.stdout.buffer.write(db.serialize())
 elif a[:2]==['ps','-aq']:print('website-id')
elif name=='apt-get' and 'docker.io' in a:
 target=Path('/tmp/install-fake-bin/docker')
 target.write_text(Path(sys.argv[0]).read_text());target.chmod(0o755)
elif name=='curl':
 if 'codeload.github.com' in ' '.join(a):
  archive=tarfile.open(Path(a[a.index('-o')+1]),'w:gz') if '-o' in a else tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz')
  with archive as tar:
   for f in ('compose.yaml','web/worker/firewall.py','tools/proxy_setup.py','tools/install_core.sh','tools/menu.sh','tools/lifecycle.py','uninstall.sh'):
    tar.add('/src/'+f,arcname='source/'+f)
 elif '/.well-known/acme-challenge/' in a[-1]:
  token=(site/'acme/.well-known/acme-challenge'/a[-1].rsplit('/',1)[-1]).read_text()
  print(token+'\n200',end='')
 else:
  body='{"ready": true}' if '--header' in a else 'Unauthorized'
  status='200' if '--header' in a else '401'
  counter=Path('/tmp/first-tls-check')
  if os.environ.get('FAKE_RELOAD_RACE') and '--resolve' in a and not counter.exists():
   counter.touch();print('\n000',end='');sys.exit(60)
  if '--write-out' in a:
   template=a[a.index('--write-out')+1]
   if template.startswith('\n'):print(body+'\n'+status,end='')
   else:print(status,end='')
  else:print(body)
elif name=='certbot':
 if a[0]=='certonly' and os.environ.get('FAKE_ACME_FAIL'):sys.exit(1)
 domain=a[a.index('--cert-name')+1]
 live=Path('/etc/letsencrypt/live')/domain
 renewal=Path('/etc/letsencrypt/renewal')/(domain+'.conf')
 if a[0] in ('certonly','reconfigure'):
  live.mkdir(parents=True,exist_ok=True);renewal.parent.mkdir(parents=True,exist_ok=True)
  (live/'fullchain.pem').write_text('public certificate')
  (live/'privkey.pem').write_text('private key')
  if '--standalone' in a:renewal.write_text('[renewalparams]\nauthenticator = standalone\n')
  else:
   webroot=a[a.index('--webroot-path')+1]
   renewal.write_text('[[webroot_map]]\n'+domain+' = '+webroot+'\n')
 elif a[0]=='delete':
  renewal.unlink(missing_ok=True)
  import shutil
  shutil.rmtree(live,ignore_errors=True)
elif name=='systemctl':
 if a[:1]==['list-timers']:print('snap.certbot.renew.timer snap.certbot.renew.service')
elif name=='openssl':
 if a[:1]==['base64']:print(base64.b64encode(sys.stdin.buffer.read()).decode(),end='')
 elif a[:1]==['x509'] and '-ext' in a:
  print('DNS:panel.example.com, DNS:localhost' if 'gokaskad-selfsigned' in ' '.join(a) else 'DNS:panel.example.com')
 elif a[:1]==['req']:
  Path(a[a.index('-keyout')+1]).write_text('temporary key')
  Path(a[a.index('-out')+1]).write_text('temporary certificate')
elif name=='htpasswd':
 sys.stdin.read();print('admin:$2y$10$synthetic-test-hash')
elif name=='iptables':print('iptables v1.8.9 (nf_tables)')
elif name=='iptables-nft' and '-C' in a:sys.exit(1)
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ.get('GOKASKAD_INSTALL_TEST_CONTAINER'), '1', 'Use the disposable test container')
        self.assertFalse(Path('/var/run/docker.sock').exists())
        for path in (ROOT, SITE, Path('/etc/letsencrypt')):
            shutil.rmtree(path, ignore_errors=True)
        Path('/tmp/first-tls-check').unlink(missing_ok=True)
        SITE.mkdir(parents=True)
        (SITE/'acme').mkdir()
        Path('/etc/letsencrypt').mkdir(exist_ok=True)
        (SITE/'nginx.conf').write_text('server { listen 80; server_name website.example.com;\nlocation ^~ /.well-known/acme-challenge/ { root /var/www/certbot; try_files $uri =404; }\n}\n')
        LOG.write_text('')
        BIN.mkdir(exist_ok=True)
        for name in ('apt-get','docker','curl','certbot','systemctl','openssl','ss','ip','iptables','iptables-nft','sysctl','htpasswd'):
            path=BIN/name
            path.write_text(FAKE)
            path.chmod(0o755)
        isolated=dict(os.environ)
        for key in ('MANAGEMENT_PORT','MANAGEMENT_BIND','RESERVED_TCP_PORTS','INSTALL_MODE',
                    'GOKASKAD_ACTION','GOKASKAD_CONFIRM','CERT_TYPE','PANEL_DOMAIN',
                    'GOKASKAD_PASSWORD_FILE','GOKASKAD_SOURCE_DIR','COMPOSE_FILE','COMPOSE_PROJECT_NAME'):
            isolated.pop(key,None)
        self.env=dict(isolated, PATH=str(BIN)+':'+os.environ['PATH'],
                      ADMIN_PASSWORD='development-password', PANEL_DOMAIN='panel.example.com')

    def install(self,*args,**env):
        return subprocess.run(['bash','-s','--',*args],input=Path('/src/install.sh').read_text(),env=dict(self.env,**env),capture_output=True,text=True,timeout=40)

    def test_one_command_auto_integrates_existing_site_and_preserves_password(self):
        original=(SITE/'nginx.conf').read_text()
        result=self.install(FAKE_RELOAD_RACE='1')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        env=(ROOT/'current/.env').read_text()
        self.assertIn('MANAGEMENT_BIND=127.0.0.1',env)
        self.assertIn('MANAGEMENT_PORT=10443',env)
        self.assertIn('RESERVED_TCP_PORTS=80,443',env)
        self.assertIn('COMPOSE_FILE=compose.yaml:/opt/go-kaskad/config/proxy.compose.yaml',env)
        self.assertNotIn('development-password',env)
        self.assertTrue((SITE/'nginx.conf').read_text().startswith(original))
        state=json.loads((ROOT/'config/proxy.json').read_text())
        self.assertEqual(state['phase'],'ready')
        self.assertEqual((ROOT/'config/tls/server.crt').read_text(),'public certificate')
        log=LOG.read_text()
        self.assertNotIn('docker run',log)
        self.assertNotIn('systemctl enable --now certbot.timer',log)
        self.assertNotIn('development-password',log)
        # The override is outside the release, so copying .env to a new release preserves it.
        self.assertTrue((ROOT/'config/proxy.compose.yaml').exists())

    def test_acme_failure_can_resume_without_password_or_database_reset(self):
        result=self.install(FAKE_ACME_FAIL='1')
        self.assertNotEqual(result.returncode,0)
        self.assertTrue((ROOT/'current').is_dir())
        self.assertIn('--resume-domain',result.stderr)
        password=(ROOT/'config/admin.htpasswd').read_bytes()
        (ROOT/'config/do-not-remove').write_text('existing state')
        result=self.install('--resume-domain',ADMIN_PASSWORD='',PANEL_DOMAIN='')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual((ROOT/'config/admin.htpasswd').read_bytes(),password)
        self.assertEqual((ROOT/'config/do-not-remove').read_text(),'existing state')
        self.assertEqual(LOG.read_text().count('htpasswd '),1)

    def test_existing_local_panel_can_add_domain_without_resetting_password(self):
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_BIND='127.0.0.1',MANAGEMENT_PORT='10443')
        self.assertEqual(result.returncode,0,result.stderr)
        password=(ROOT/'config/admin.htpasswd').read_bytes()
        result=self.install('--configure-domain',ADMIN_PASSWORD='')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual((ROOT/'config/admin.htpasswd').read_bytes(),password)
        self.assertEqual(LOG.read_text().count('htpasswd '),1)
        self.assertEqual(json.loads((ROOT/'config/proxy.json').read_text())['phase'],'ready')

    def test_old_version_is_rejected_before_dependencies_or_domain_prompt(self):
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_PORT='18443')
        self.assertEqual(result.returncode,0,result.stderr)
        compose=ROOT/'current/compose.yaml'
        compose.write_text(compose.read_text().replace('RESERVED_TCP_PORTS','OLD_VERSION'))
        LOG.write_text('')
        result=self.install('--configure-domain',PANEL_DOMAIN='')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Сначала обновите Gokaskad',result.stderr)
        self.assertEqual(LOG.read_text(),'')
        self.assertNotIn('Домен панели',result.stdout+result.stderr)

    def test_documented_heredoc_update_switches_current_after_docker_exec(self):
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_PORT='18443')
        self.assertEqual(result.returncode,0,result.stderr)
        previous=(ROOT/'current').resolve()
        guide=Path('/src/docs/web.ru.md').read_text()
        code=guide.split("sudo bash <<'UPDATE'\n",1)[1].split('\nUPDATE\n',1)[0]
        result=subprocess.run(['bash','-s'],input=code+'\necho UPDATE_COMPLETED\n',env=self.env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('UPDATE_COMPLETED',result.stdout)
        self.assertNotEqual((ROOT/'current').resolve(),previous)
        self.assertTrue((ROOT/'current/compose.yaml').is_file())

    def test_fresh_host_still_installs_docker_with_stdin_wrapper(self):
        (BIN/'docker').unlink()
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_PORT='18443')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('apt-get install -y --no-install-recommends docker.io',LOG.read_text())
        self.assertTrue((ROOT/'current').is_dir())

    def test_menu_proxy_certbot_and_update_keep_rules_and_mode(self):
        result=self.install(GOKASKAD_ACTION='install',INSTALL_MODE='proxy',CERT_TYPE='certbot')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        original=(ROOT/'config/admin.htpasswd').read_bytes()
        state=json.loads((ROOT/'config/proxy.json').read_text())
        self.assertEqual(state['phase'],'ready')
        before=(ROOT/'current').resolve()
        result=self.install('--update')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertNotEqual((ROOT/'current').resolve(),before)
        self.assertEqual((ROOT/'config/admin.htpasswd').read_bytes(),original)
        self.assertIn('COMPOSE_FILE=',(ROOT/'current/.env').read_text())
        self.assertTrue(list(Path('/root/gokaskad-backups').glob('*/rules.sqlite3')))

    def test_menu_selfsigned_sni_has_no_certbot_and_keeps_verification(self):
        result=self.install(GOKASKAD_ACTION='install',INSTALL_MODE='proxy',CERT_TYPE='selfsigned')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        config=(SITE/'nginx.conf').read_text()
        self.assertIn('gokaskad-selfsigned/panel.example.com/fullchain.pem',config)
        self.assertIn('proxy_ssl_verify on;',config)
        self.assertNotIn('certbot certonly',LOG.read_text())
        self.assertFalse(Path('/etc/letsencrypt/renewal-hooks/deploy/gokaskad-panel').exists())

    def test_menu_changes_legacy_port_to_sni_preserving_password(self):
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_PORT='8443')
        self.assertEqual(result.returncode,0,result.stderr)
        original=(ROOT/'config/admin.htpasswd').read_bytes()
        result=self.install('--change-mode',INSTALL_MODE='proxy',CERT_TYPE='certbot')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual((ROOT/'config/admin.htpasswd').read_bytes(),original)
        self.assertEqual(json.loads((ROOT/'config/installation.json').read_text())['mode'],'proxy')
        self.assertIn('MANAGEMENT_BIND=127.0.0.1',(ROOT/'current/.env').read_text())

    def test_failed_mode_change_rolls_back_original_port_certificate_and_domain(self):
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_PORT='8443')
        self.assertEqual(result.returncode,0,result.stderr)
        oldenv=(ROOT/'current/.env').read_text()
        oldtls=(ROOT/'config/tls/server.crt').read_bytes()
        oldsite=(SITE/'nginx.conf').read_text()
        result=self.install('--change-mode',INSTALL_MODE='proxy',CERT_TYPE='certbot',FAKE_ACME_FAIL='1')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Восстанавливаю прежнюю',result.stderr)
        self.assertEqual((ROOT/'current/.env').read_text(),oldenv)
        self.assertEqual((ROOT/'config/tls/server.crt').read_bytes(),oldtls)
        self.assertEqual((SITE/'nginx.conf').read_text(),oldsite)
        self.assertFalse((ROOT/'config/proxy.json').exists())

    def test_keep_config_and_restore_preserves_password_and_database_volume(self):
        result=self.install(GOKASKAD_ACTION='install',INSTALL_MODE='proxy',CERT_TYPE='selfsigned')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        password=(ROOT/'config/admin.htpasswd').read_bytes()
        result=self.install('--keep-config',GOKASKAD_CONFIRM='YES')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertFalse((ROOT/'current').exists())
        self.assertTrue((ROOT/'saved/rules.sqlite3').exists())
        self.assertNotIn('down --volumes',LOG.read_text())
        result=self.install(GOKASKAD_ACTION='install',INSTALL_MODE='proxy',CERT_TYPE='selfsigned',ADMIN_PASSWORD='')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual((ROOT/'config/admin.htpasswd').read_bytes(),password)
        self.assertEqual(LOG.read_text().count('htpasswd '),1)
        self.assertTrue((ROOT/'current').exists())

    def test_menu_full_removal_keeps_shared_docker_but_removes_project(self):
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_PORT='18443')
        self.assertEqual(result.returncode,0,result.stderr)
        result=self.install('--purge',GOKASKAD_CONFIRM='YES')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertFalse(ROOT.exists())
        self.assertIn('down --volumes',LOG.read_text())

    def test_direct_certbot_uses_host_standalone_and_renewal_hook(self):
        result=self.install(GOKASKAD_ACTION='install',INSTALL_MODE='standalone',CERT_TYPE='certbot',MANAGEMENT_PORT='18443')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('--standalone',LOG.read_text())
        plan=json.loads((ROOT/'config/proxy.json').read_text())
        self.assertEqual(plan['kind'],'direct')
        self.assertTrue(Path('/etc/letsencrypt/renewal-hooks/deploy/gokaskad-panel').exists())

    def test_update_failure_keeps_previous_release_and_configuration(self):
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_PORT='8443')
        self.assertEqual(result.returncode,0,result.stderr)
        before=(ROOT/'current').resolve()
        env=(ROOT/'current/.env').read_text()
        result=self.install('--update',FAKE_UPDATE_FAIL='1')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((ROOT/'current').resolve(),before)
        self.assertEqual((ROOT/'current/.env').read_text(),env)
        self.assertIn('Восстанавливаю предыдущий release',result.stderr)

    def test_default_interactive_menu_detects_services_and_exit_changes_nothing(self):
        import pty,select,time
        pid,terminal=pty.fork()
        if pid==0:
            os.execve('/bin/bash',['bash','-c','cat /src/install.sh | bash'],dict(self.env,ADMIN_PASSWORD=''))
        output=b''
        sent=False
        deadline=time.monotonic()+15
        status=None
        try:
            while time.monotonic()<deadline:
                ready,_,_=select.select([terminal],[],[],0.2)
                if ready:
                    try: chunk=os.read(terminal,65536)
                    except OSError: break
                    if not chunk:break
                    output+=chunk
                    if not sent and 'Выберите действие:'.encode() in output:
                        os.write(terminal,b'0\n');sent=True
                child,status=os.waitpid(pid,os.WNOHANG)
                if child:break
            else:
                os.kill(pid,15)
                self.fail('Interactive menu did not exit')
        finally:
            os.close(terminal)
            try:os.waitpid(pid,0)
            except ChildProcessError:pass
        text=output.decode(errors='replace')
        self.assertIn('Слушающие TCP/UDP-порты',text)
        self.assertIn('4 — Удаление',text)
        self.assertTrue(sent,text)
        self.assertFalse(ROOT.exists())
        self.assertNotIn('curl ',LOG.read_text())
        self.assertNotIn('apt-get ',LOG.read_text())

    def test_proxy_can_move_back_to_direct_selfsigned_and_full_cleanup_removes_retired_certificate(self):
        result=self.install(GOKASKAD_ACTION='install',INSTALL_MODE='proxy',CERT_TYPE='certbot')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        password=(ROOT/'config/admin.htpasswd').read_bytes()
        result=self.install('--change-mode',INSTALL_MODE='standalone',CERT_TYPE='selfsigned',PANEL_DOMAIN='',MANAGEMENT_PORT='18443')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertNotIn('COMPOSE_FILE=',(ROOT/'current/.env').read_text())
        self.assertNotIn('server_name panel.example.com;',(SITE/'nginx.conf').read_text())
        self.assertEqual((ROOT/'config/admin.htpasswd').read_bytes(),password)
        self.assertTrue((ROOT/'config/retired-proxies.json').exists())
        result=self.install('--purge',GOKASKAD_CONFIRM='YES')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertFalse(ROOT.exists())
        self.assertFalse(Path('/etc/letsencrypt/renewal/panel.example.com.conf').exists())

    def test_mode_change_rejects_conflicting_forward_before_proxy_changes(self):
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_PORT='8443')
        self.assertEqual(result.returncode,0,result.stderr)
        site=(SITE/'nginx.conf').read_text()
        result=self.install('--change-mode',INSTALL_MODE='proxy',CERT_TYPE='certbot',FAKE_RULE_PORT='443')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('отключите перенаправления TCP 80/443',result.stderr)
        self.assertEqual((SITE/'nginx.conf').read_text(),site)
        self.assertNotIn('COMPOSE_FILE=',(ROOT/'current/.env').read_text())

    def test_standalone_still_accepts_password_and_port(self):
        result=self.install(INSTALL_MODE='standalone',MANAGEMENT_PORT='18443')
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertFalse((ROOT/'config/proxy.json').exists())
        self.assertNotIn('server_name panel.example.com;', (SITE/'nginx.conf').read_text())
        self.assertNotIn('certbot certonly',LOG.read_text())

    def test_uninstall_removes_only_project_domain_and_keeps_later_site_edits(self):
        result=self.install()
        self.assertEqual(result.returncode,0,result.stderr)
        (SITE/'nginx.conf').write_text((SITE/'nginx.conf').read_text()+'# later site change\n')
        result=subprocess.run(['bash','-s'],input=Path('/src/uninstall.sh').read_text(),env=self.env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        config=(SITE/'nginx.conf').read_text()
        self.assertNotIn('server_name panel.example.com;',config)
        self.assertIn('server_name website.example.com;',config)
        self.assertIn('# later site change',config)
        self.assertFalse(ROOT.exists())
        self.assertFalse(Path('/etc/letsencrypt/renewal/panel.example.com.conf').exists())


if __name__=='__main__':
    unittest.main()
