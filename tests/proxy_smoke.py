"""Real nginx/SNI/TLS integration; isolated Docker networks, no host firewall writes.

ACME issuance and systemd are simulated. All HTTPS checks verify a disposable CA.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('proxy_setup', REPO / 'tools/proxy_setup.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=120, **kwargs)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    os.umask(0o022)
    (REPO / '.local').mkdir(exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix='proxy-smoke-', dir=REPO / '.local'))
    os.chmod(fixture, 0o755)
    root = fixture / 'installation'
    ca = fixture / 'ca.crt'
    key = fixture / 'ca.key'
    le = fixture / 'letsencrypt'
    domain = 'panel.example.test'
    network = 'gokaskad-proxy-smoke-' + str(os.getpid())
    outer = 'gokaskad-outer-smoke-' + str(os.getpid())
    project = 'gokaskad-proxy-smoke-' + str(os.getpid())
    http_port, https_port, local_port = free_port(), free_port(), free_port()
    for path in (root / 'config/tls', root / 'current', fixture / 'acme', fixture / 'site', fixture / 'panel', le / 'renewal'):
        path.mkdir(parents=True, exist_ok=True)
    run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
         '-subj', '/CN=Gokaskad disposable test CA', '-keyout', str(key), '-out', str(ca)])

    # Match cross-signed ACME chains: leaf -> issuing CA -> intermediate -> trusted root.
    intermediate = fixture / 'intermediate.crt'
    intermediate_key = fixture / 'intermediate.key'
    intermediate_csr = fixture / 'intermediate.csr'
    intermediate_ext = fixture / 'intermediate.cnf'
    intermediate_ext.write_text('basicConstraints=critical,CA:TRUE,pathlen:1\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
    run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes',
         '-subj', '/CN=Gokaskad disposable intermediate CA',
         '-keyout', str(intermediate_key), '-out', str(intermediate_csr)])
    run(['openssl', 'x509', '-req', '-in', str(intermediate_csr), '-CA', str(ca),
         '-CAkey', str(key), '-CAcreateserial', '-days', '2', '-extfile',
         str(intermediate_ext), '-out', str(intermediate)])

    issuing = fixture / 'issuing.crt'
    issuing_key = fixture / 'issuing.key'
    issuing_csr = fixture / 'issuing.csr'
    issuing_ext = fixture / 'issuing.cnf'
    issuing_ext.write_text('basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
    run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes',
         '-subj', '/CN=Gokaskad disposable issuing CA',
         '-keyout', str(issuing_key), '-out', str(issuing_csr)])
    run(['openssl', 'x509', '-req', '-in', str(issuing_csr), '-CA', str(intermediate),
         '-CAkey', str(intermediate_key), '-CAcreateserial', '-days', '2', '-extfile',
         str(issuing_ext), '-out', str(issuing)])

    def certificate(name, directory):
        directory.mkdir(parents=True, exist_ok=True)
        ext = fixture / 'ext.cnf'
        ext.write_text('subjectAltName=DNS:' + name + '\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
        csr = fixture / 'request.csr'
        run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=' + name,
             '-keyout', str(directory / 'privkey.pem'), '-out', str(csr)])
        run(['openssl', 'x509', '-req', '-in', str(csr), '-CA', str(issuing), '-CAkey', str(issuing_key),
             '-CAcreateserial', '-days', '2', '-extfile', str(ext), '-out', str(directory / 'fullchain.pem')])

        with (directory / 'fullchain.pem').open('a') as chain:
            chain.write(issuing.read_text() + intermediate.read_text())

    certificate('site.example.test', le / 'live/site.example.test')
    certificate('localhost', fixture / 'initial')
    shutil.copyfile(fixture / 'initial/fullchain.pem', root / 'config/tls/server.crt')
    shutil.copyfile(fixture / 'initial/privkey.pem', root / 'config/tls/server.key')
    auth = run(['openssl', 'passwd', '-apr1', '-stdin'], input='development-password\n').stdout.strip()
    (fixture / 'auth').write_text('admin:' + auth + '\n')
    (fixture / 'auth').chmod(0o644)
    (fixture / 'site/index.html').write_text('existing website')
    (fixture / 'panel/health.json').write_text('{"ready": true}')
    (fixture / 'backend.conf').write_text('''events {}
http {
    server {
        listen 443 ssl;
        ssl_certificate /tls/server.crt;
        ssl_certificate_key /tls/server.key;
        auth_basic "Panel";
        auth_basic_user_file /auth;
        location /api/health { root /data; try_files /health.json =404; }
    }
}
''')
    conf = fixture / 'outer.conf'
    original = '''server {
    listen 80 default_server;
    server_name site.example.test;
    location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; try_files $uri =404; }
    location / { root /data; }
}
server {
    listen 443 ssl default_server;
    server_name site.example.test;
    ssl_certificate /etc/letsencrypt/live/site.example.test/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/site.example.test/privkey.pem;
    location / { root /data; }
}
'''
    conf.write_text(original)
    (root / 'current/compose.yaml').write_text(f'''name: {project}
services:
  nginx:
    image: nginx:1.28-alpine
    entrypoint: [nginx, -g, "daemon off;"]
    ports: ["127.0.0.1:{local_port}:443"]
    volumes:
      - {fixture}/backend.conf:/etc/nginx/nginx.conf:ro
      - {root}/config/tls:/tls:ro
      - {fixture}/auth:/auth:ro
      - {fixture}/panel:/data:ro
    networks: [edge, control]
networks:
  edge: {{}}
  control:
    internal: true
''')
    (root / 'current/.env').write_text(f'MANAGEMENT_PORT={local_port}\nMANAGEMENT_BIND=127.0.0.1\n')
    authfile = fixture / 'auth.header'
    authfile.write_text('Authorization: Basic ' + base64.b64encode(b'admin:development-password').decode() + '\n')
    authfile.chmod(0o600)
    real_run = p.run
    real_which = shutil.which

    def commands(args, **kwargs):
        if args[0] == '/test/certbot':
            if args[1] == 'certonly':
                certificate(domain, le / 'live' / domain)
                (le / 'renewal' / (domain + '.conf')).write_text('[[webroot_map]]\n' + domain + ' = ' + str(fixture / 'acme') + '\n')
            elif args[1] == 'delete':
                (le / 'renewal' / (domain + '.conf')).unlink()
                shutil.rmtree(le / 'live' / domain)
            return subprocess.CompletedProcess(args, 0, '', '')
        if args[:2] == ['systemctl', 'list-timers']:
            return subprocess.CompletedProcess(args, 0, 'snap.certbot.renew.timer', '')
        if args[0] == 'certbot':
            return commands(['/test/certbot', *args[1:]], **kwargs)
        if args[0] == 'curl':
            # Keep production URLs/SNI/Host and TLS verification; map only the socket port.
            args = list(args)
            index = args.index('--resolve')
            name, port, address = args[index + 1].split(':')
            target = https_port if port == '443' else http_port
            args[index:index + 2] = ['--connect-to', f'{name}:{port}:{address}:{target}']
            args[1:1] = ['--cacert', str(ca)]
        return real_run(args, **kwargs)

    try:
        run(['docker', 'network', 'create', network])
        run(['docker', 'run', '-d', '--name', outer, '--network', network,
             '-p', f'127.0.0.1:{http_port}:80', '-p', f'127.0.0.1:{https_port}:443',
             '-v', f'{conf}:/etc/nginx/conf.d/default.conf:ro',
             '-v', f'{le}:/etc/letsencrypt:ro', '-v', f'{fixture}/acme:/var/www/certbot:ro',
             '-v', f'{fixture}/site:/data:ro', '-v', f'{ca}:/etc/ssl/certs/ca-certificates.crt:ro',
             'nginx:1.28-alpine'])
        run(['docker', 'compose', 'up', '-d'], cwd=root / 'current')
        # Verify existing site first, including the correct SNI certificate.
        def site_ok():
            r = run(['curl', '--noproxy', '*', '--silent', '--show-error', '--cacert', str(ca),
                     '--resolve', f'site.example.test:{https_port}:127.0.0.1',
                     f'https://site.example.test:{https_port}/'])
            assert r.stdout == 'existing website', r.stdout
        for attempt in range(20):
            try:
                site_ok()
                break
            except subprocess.CalledProcessError:
                time.sleep(0.2)
        else:
            raise AssertionError('Existing website did not start')
        with patch.object(p, 'ROOT', root), patch.object(p, 'LE', le), patch.object(p, 'run', side_effect=commands), \
             patch.object(p.shutil, 'which', side_effect=lambda name: '/test/certbot' if name == 'certbot' else real_which(name)):
            container = json.loads(run(['docker', 'inspect', outer]).stdout)[0]
            config = run(['docker', 'exec', outer, 'nginx', '-T'])
            plan = p.docker_plan(container, config.stdout + config.stderr)
            p.setup(plan, domain, local_port, authfile)
            site_ok()
            state = json.loads((root / 'config/proxy.json').read_text())
            assert state['phase'] == 'ready'
            assert conf.read_text().startswith(original)
            # A new leaf/key must propagate through both nginx reloads, with verification on.
            certificate(domain, le / 'live' / domain)
            p.copy_certificate(state)
            p.nginx(state, '-s', 'reload')
            p.wait_http(f'https://{domain}/api/health', domain, 200, token='"ready": true', auth_file=authfile)
            # Recreating the panel must retain its external network from .env/COMPOSE_FILE.
            p.compose('up', '-d', '--force-recreate', 'nginx')
            p.wait_http(f'https://{domain}/api/health', domain, 200, token='"ready": true', auth_file=authfile)
            conf.write_text(conf.read_text() + '# website edit after install\n')
            p.remove(state)
            assert conf.read_text() == original + '# website edit after install\n'
            site_ok()
            # Self-signed SNI must still verify the upstream name and pinned CA.
            selfsigned_plan = dict(plan, certificate_type='selfsigned')
            p.setup(selfsigned_plan, domain, local_port, None)
            p.wait_http(f'https://{domain}/api/health', domain, 401, ca_file=root / 'config/tls/server.crt')
            p.wait_http(f'https://{domain}/api/health', domain, 200, token='"ready": true',
                        auth_file=authfile, ca_file=root / 'config/tls/server.crt')
            site_ok()
            p.remove(json.loads((root / 'config/proxy.json').read_text()))
            site_ok()
        print('PASS: real HTTP-01 webroot, SNI sites, verified TLS, authentication, certificate rotation, recreation and domain cleanup')
    except Exception:
        logs = subprocess.run(['docker', 'logs', '--tail', '30', outer], capture_output=True, text=True)
        print(logs.stdout + logs.stderr, flush=True)
        raise
    finally:
        subprocess.run(['docker', 'compose', 'down', '-v'], cwd=root / 'current', capture_output=True)
        subprocess.run(['docker', 'rm', '-f', outer], capture_output=True)
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True)
        shutil.rmtree(fixture, ignore_errors=True)


if __name__ == '__main__':
    main()
