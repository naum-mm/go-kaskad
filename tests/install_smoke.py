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
import base64,json,os,sys,tarfile
from pathlib import Path
name=Path(sys.argv[0]).name
a=sys.argv[1:]
root=Path('/opt/go-kaskad')
site=Path('/opt/website')
with open('/tmp/install-commands.log','a') as f:f.write(name+' '+' '.join(a)+'\n')
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
 elif a[:3]==['compose','exec','-T'] and '--machine' in a:print('{"rules": []}')
 elif a[:2]==['ps','-aq']:print('website-id')
elif name=='curl':
 if '-o' in a:
  out=Path(a[a.index('-o')+1])
  with tarfile.open(out,'w:gz') as tar:
   for f in ('compose.yaml','web/worker/firewall.py','tools/proxy_setup.py'):
    tar.add('/src/'+f,arcname='source/'+f)
 elif '/.well-known/acme-challenge/' in a[-1]:
  token=(site/'acme/.well-known/acme-challenge'/a[-1].rsplit('/',1)[-1]).read_text()
  print(token+'\n200',end='')
 elif '--cacert' in a:
  if '--header' in a:print('{"ready": true}')
  else:print('401',end='')
 else:
  counter=Path('/tmp/first-tls-check')
  if os.environ.get('FAKE_RELOAD_RACE') and not counter.exists():
   counter.touch();print('\n000',end='');sys.exit(60)
  if '--header' in a:print('{"ready": true}\n200',end='')
  else:print('Unauthorized\n401',end='')
elif name=='certbot':
 if a[0]=='certonly' and os.environ.get('FAKE_ACME_FAIL'):sys.exit(1)
 domain=a[a.index('--cert-name')+1]
 live=Path('/etc/letsencrypt/live')/domain
 renewal=Path('/etc/letsencrypt/renewal')/(domain+'.conf')
 if a[0] in ('certonly','reconfigure'):
  live.mkdir(parents=True,exist_ok=True);renewal.parent.mkdir(parents=True,exist_ok=True)
  (live/'fullchain.pem').write_text('public certificate')
  (live/'privkey.pem').write_text('private key')
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
        self.env=dict(os.environ, PATH=str(BIN)+':'+os.environ['PATH'],
                      ADMIN_PASSWORD='development-password', PANEL_DOMAIN='panel.example.com')

    def install(self,*args,**env):
        return subprocess.run(['bash','/src/install.sh',*args],env=dict(self.env,**env),capture_output=True,text=True,timeout=40)

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
        result=subprocess.run(['bash','/src/uninstall.sh'],env=self.env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        config=(SITE/'nginx.conf').read_text()
        self.assertNotIn('server_name panel.example.com;',config)
        self.assertIn('server_name website.example.com;',config)
        self.assertIn('# later site change',config)
        self.assertFalse(ROOT.exists())
        self.assertFalse(Path('/etc/letsencrypt/renewal/panel.example.com.conf').exists())


if __name__=='__main__':
    unittest.main()
