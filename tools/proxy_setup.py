#!/usr/bin/env python3
"""Install a domain in an existing nginx. Only this project's marked block is owned.

Run on the host, including Certbot. Never mount the Docker socket in a container.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path('/opt/go-kaskad')
LE = Path('/etc/letsencrypt')
BEGIN = '# BEGIN GOKASKAD MANAGED DOMAIN\n'
END = '# END GOKASKAD MANAGED DOMAIN\n'
ALIAS = 'gokaskad-proxy-upstream'


class SetupError(RuntimeError):
    pass


def run(args, *, check=True, timeout=60, **kwargs):
    if 'input' not in kwargs:
        kwargs.setdefault('stdin', subprocess.DEVNULL)
    return subprocess.run(args, check=check, text=True, capture_output=True,
                          timeout=timeout, **kwargs)


def domain_name(value):
    value = value.strip().lower().rstrip('.')
    labels = value.split('.')
    if (len(value) > 253 or len(labels) < 2 or not (re.fullmatch(r'[a-z]{2,63}', labels[-1]) or labels[-1].startswith('xn--'))
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in labels)):
        raise SetupError('Укажите DNS-имя без https://, порта и пути (IDN — в punycode).')
    return value


def mapped_path(path, mounts, *, directory=False):
    candidates = []
    for mount in mounts:
        if mount.get('Type') != 'bind':
            continue
        target = Path(mount['Destination'])
        source = Path(mount['Source'])
        try:
            relative = Path(path).relative_to(target)
        except ValueError:
            continue
        if relative.parts and not source.is_dir():
            continue
        candidate = source / relative
        if directory and not candidate.is_dir():
            continue
        candidates.append((len(target.parts), candidate))
    if not candidates:
        raise SetupError(f'Нет bind mount для {path}; автоматическое подключение этого nginx не поддерживается.')
    return max(candidates, key=lambda item: item[0])[1]


def docker_plan(container, config):
    mounts = container.get('Mounts', [])
    conf_dir = '/etc/nginx/conf.d'
    if not re.search(r'include\s+/etc/nginx/conf\.d/\*\.conf\s*;', config):
        raise SetupError('Для Docker nginx требуется include /etc/nginx/conf.d/*.conf.')
    try:
        directory = mapped_path(conf_dir, mounts, directory=True)
        conf = directory / 'gokaskad.conf'
    except SetupError:
        files = []
        for mount in mounts:
            target = mount.get('Destination', '')
            if (mount.get('Type') == 'bind' and str(Path(target).parent) == conf_dir
                    and target.endswith('.conf') and f'# configuration file {target}:' in config):
                files.append(mount['Source'])
        if len(files) != 1:
            raise SetupError('Не удалось однозначно найти bind mount конфигурации nginx.')
        conf = Path(files[0])
    acme_roots = re.findall(r'location\s+[^{}]*\.well-known/acme-challenge/[^{}]*\{[^{}]*?root\s+([^;\s]+)\s*;', config)
    if not acme_roots:
        raise SetupError('В Docker nginx нужен существующий webroot /.well-known/acme-challenge/.')
    webroot = mapped_path(acme_roots[0], mounts, directory=True)
    cert_dir = mapped_path('/etc/letsencrypt', mounts, directory=True)
    if cert_dir.resolve() != LE.resolve():
        raise SetupError('Для Docker nginx требуется bind mount /etc/letsencrypt:/etc/letsencrypt.')
    networks = sorted(name for name in container['NetworkSettings']['Networks'] if name not in ('host', 'none', 'bridge'))
    if not networks:
        raise SetupError('У nginx не найдена пользовательская Docker-сеть.')
    return dict(kind='docker', container=container['Name'].lstrip('/'), conf=str(conf),
                webroot=str(webroot), nginx_webroot=acme_roots[0], network=networks[0],
                backend=f'{ALIAS}:443', ipv6=bool(re.search(r'listen\s+\[::\]:', config)))


def discover():
    containers = []
    if shutil.which('docker'):
        ids = run(['docker', 'ps', '-q']).stdout.split()
        if ids:
            containers = json.loads(run(['docker', 'inspect', *ids]).stdout)
    owners = [c for c in containers if any(
        p.get('HostPort') == '443' for p in (c.get('NetworkSettings', {}).get('Ports', {}).get('443/tcp') or []))]
    listeners = run(['ss', '-H', '-ltn', 'sport = :443']).stdout.strip()
    if not owners and not listeners:
        return dict(kind='standalone')
    if len(owners) > 1:
        raise SetupError('Порт 443 опубликован несколькими контейнерами; автоматический выбор nginx невозможен.')
    if owners:
        c = owners[0]
        result = run(['docker', 'exec', c['Id'], 'nginx', '-T'], check=False)
        if result.returncode:
            raise SetupError('Порт 443 занят контейнером, который не поддерживает nginx -T. Используйте INSTALL_MODE=standalone для отдельного порта.')
        # Confirm port 80 also reaches this nginx; needed for HTTP-01.
        if not any(p.get('HostPort') == '80' for p in (c['NetworkSettings']['Ports'].get('80/tcp') or [])):
            raise SetupError('Для автоматического сертификата этот nginx должен публиковать также TCP 80.')
        config = result.stdout + result.stderr
        plan = docker_plan(c, config)
        run(['docker', 'exec', c['Id'], 'test', '-r', '/etc/ssl/certs/ca-certificates.crt'])
        return plan
    if not shutil.which('nginx'):
        raise SetupError('TCP 443 занят нераспознанным сервисом. Его настройки не изменены. Для отдельного порта: INSTALL_MODE=standalone.')
    result = run(['nginx', '-T'])
    config = result.stdout + result.stderr
    # Do not edit an inactive host nginx while another program owns 443.
    process = run(['ss', '-H', '-ltnp', 'sport = :443']).stdout
    if 'nginx' not in process:
        raise SetupError('TCP 443 занят другим сервисом, а не nginx хоста.')
    if not re.search(r'include\s+/etc/nginx/conf\.d/\*\.conf\s*;', config):
        raise SetupError('Для nginx хоста требуется include /etc/nginx/conf.d/*.conf.')
    if not run(['ss', '-H', '-ltnp', 'sport = :80']).stdout.count('nginx'):
        raise SetupError('Для сертификата nginx хоста должен слушать TCP 80.')
    return dict(kind='host', conf='/etc/nginx/conf.d/gokaskad.conf', webroot='/var/lib/gokaskad-acme',
                nginx_webroot='/var/lib/gokaskad-acme', backend='127.0.0.1:{port}',
                ipv6=bool(re.search(r'listen\s+\[::\]:', config)))


def validate_paths(plan):
    # Values are interpolated into nginx, YAML or Compose dotenv, never shell code.
    for key in ('conf', 'webroot', 'nginx_webroot'):
        if not re.fullmatch(r'/[a-zA-Z0-9_./-]+', plan[key]):
            raise SetupError(f'Неподдерживаемый путь nginx: {key}.')
    for key in ('container', 'network'):
        if key in plan and not re.fullmatch(r'[a-zA-Z0-9_.-]+', plan[key]):
            raise SetupError(f'Неподдерживаемое имя Docker: {key}.')
    conf = Path(plan['conf'])
    if conf.is_symlink():
        raise SetupError('Не изменяю симлинк конфигурации nginx.')


def nginx(plan, *args):
    command = ['docker', 'exec', plan['container'], 'nginx'] if plan['kind'] == 'docker' else ['nginx']
    return run(command + list(args))


def marked(content):
    if BEGIN in content or END in content:
        if content.count(BEGIN) != 1 or content.count(END) != 1:
            raise SetupError('Повреждены маркеры конфигурации Gokaskad.')
        before, rest = content.split(BEGIN)
        block, after = rest.split(END)
        return before, block, after
    return content, '', ''


def write_block(plan, body):
    path = Path(plan['conf'])
    original = path.read_text() if path.exists() else ''
    before, _, after = marked(original)
    # Write in place: replacing a file would leave a Docker single-file bind on the old inode.
    existed = path.exists()
    updated = before + ('' if not before or before.endswith('\n') else '\n') + BEGIN + body + END + after
    path.write_text(updated)
    if not existed:
        path.chmod(0o644)
    try:
        nginx(plan, '-t')
        nginx(plan, '-s', 'reload')
    except (subprocess.SubprocessError, OSError):
        path.write_text(original)
        if not original:
            path.unlink()
        nginx(plan, '-t')
        nginx(plan, '-s', 'reload')
        raise
    return body


def set_block(plan, body):
    plan['pending_block'] = body
    save_state(plan)
    try:
        write_block(plan, body)
    finally:
        # Reflect a rollback too, so cleanup/resume works after a failed nginx -t.
        path = Path(plan['conf'])
        _, actual, _ = marked(path.read_text() if path.exists() else '')
        plan['block'] = actual
        plan.pop('pending_block', None)
        save_state(plan)


def http_config(plan):
    ipv6 = 'listen [::]:80;' if plan.get('ipv6') else ''
    return f'''server {{
    listen 80;
    {ipv6}
    server_name {plan['domain']};
    location ^~ /.well-known/acme-challenge/ {{
        root {plan['nginx_webroot']};
        try_files $uri =404;
    }}
    location / {{ return 301 https://{plan['domain']}$request_uri; }}
}}
'''


def https_config(plan):
    domain = plan['domain']
    resolver = 'resolver 127.0.0.11 valid=5s ipv6=off;' if plan['kind'] == 'docker' else ''
    backend = plan['backend'].format(port=plan['port'])
    ipv6 = 'listen [::]:443 ssl;' if plan.get('ipv6') else ''
    return http_config(plan) + f'''server {{
    listen 443 ssl;
    {ipv6}
    server_name {domain};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    server_tokens off;
    client_max_body_size 16k;
    {resolver}
    location / {{
        set $gokaskad_upstream {backend};
        proxy_pass https://$gokaskad_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 130s;
        proxy_ssl_server_name on;
        proxy_ssl_name {domain};
        proxy_ssl_verify on;
        proxy_ssl_verify_depth 3;
        proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
    }}
}}
'''


def wait_http(url, domain, expected, *, token=None, auth_file=None, attempts=30):
    port = 443 if url.startswith('https:') else 80
    args = ['curl', '--silent', '--show-error', '--connect-timeout', '2', '--max-time', '5',
            '--noproxy', '*', '--resolve', f'{domain}:{port}:127.0.0.1']
    if auth_file:
        args += ['--header', '@' + str(auth_file)]
    # stderr can contain transient certificate mismatch immediately after asynchronous reload.
    last = ''
    for attempt in range(attempts):
        result = run(args + ['--write-out', '\n%{http_code}', url], check=False, timeout=10)
        body, _, status = result.stdout.rpartition('\n')
        last = f'curl={result.returncode}, HTTP={status}'
        if result.returncode == 0 and status == str(expected) and (token is None or token in body):
            return
        if attempt + 1 < attempts:
            time.sleep(1)
    hint = ('Nginx вернул 502: проверьте соединение с upstream и проверку его TLS-цепочки в error log nginx.'
            if status == '502' else 'Проверьте DNS, доступность TCP 80/443 и nginx.')
    raise SetupError(f'Не прошла проверка {domain}: {last}. {hint}')


def save_state(plan):
    path = ROOT / 'config/proxy.json'
    path.write_text(json.dumps(plan, indent=2) + '\n')
    path.chmod(0o600)


def compose(*args):
    return run(['docker', 'compose', *args], cwd=ROOT / 'current', timeout=240)


def copy_certificate(plan):
    source = (LE / 'live') / plan['domain']
    destination = ROOT / 'config/tls'
    # Directory mount makes new certificate inodes visible to the panel container.
    for name, target in (('fullchain.pem', 'server.crt'), ('privkey.pem', 'server.key')):
        path = destination / target
        staging = destination / (target + '.new')
        shutil.copyfile(source / name, staging)
        staging.chmod(0o600)
        staging.replace(path)
    compose('exec', '-T', 'nginx', 'nginx', '-t')
    compose('exec', '-T', 'nginx', 'nginx', '-s', 'reload')


def ensure_directory(path):
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir()
        directory.chmod(0o755)


def setup(plan, domain, port, auth_file):
    plan = dict(plan, domain=domain_name(domain), port=port)
    validate_paths(plan)
    if plan['kind'] == 'docker':
        text = (ROOT / 'current/.env').read_text()
        entry = 'compose.yaml:' + str(ROOT / 'config/proxy.compose.yaml')
        existing = re.search(r'^COMPOSE_FILE=(.*)$', text, re.M)
        if existing and existing[1] != entry:
            raise SetupError('COMPOSE_FILE уже настроен вручную; существующее подключение не изменено.')
    state_file = ROOT / 'config/proxy.json'
    if state_file.exists():
        raise SetupError('Подключение домена уже начато. Продолжите установщик с --resume-domain; настройки сохранены в config/proxy.json.')
    conf = Path(plan['conf'])
    original = conf.read_text() if conf.exists() else ''
    if BEGIN in original or END in original:
        raise SetupError('В nginx уже есть блок Gokaskad; автоматическая замена остановлена.')
    dump = nginx(plan, '-T')
    # Reject exact, wildcard and regex server names that may already handle this domain.
    for names in re.findall(r'\bserver_name\s+([^;]+);', dump.stdout + dump.stderr):
        for name in shlex.split(names):
            if (name == plan['domain'] or name.startswith('~')
                    or (name.startswith('*.') and plan['domain'].endswith(name[1:]))
                    or (name.endswith('.*') and plan['domain'].startswith(name[:-1]))
                    or (name.startswith('.') and (plan['domain'] == name[1:] or plan['domain'].endswith(name)))):
                raise SetupError('Домен уже занят или nginx использует regex server_name; автоматическое подключение остановлено.')
    certificate = LE / 'live' / plan['domain'] / 'fullchain.pem'
    if certificate.exists():
        names = run(['openssl', 'x509', '-in', str(certificate), '-noout', '-ext', 'subjectAltName']).stdout
        if set(re.findall(r'DNS:([^,\s]+)', names)) != {plan['domain']}:
            raise SetupError('Существующий сертификат содержит другие домены; он не будет заменён.')
    plan['conf_existed'] = conf.exists()
    plan['certificate_owned'] = not ((LE / 'renewal') / (plan['domain'] + '.conf')).exists()
    backup = ROOT / 'config/proxy-nginx.before'
    backup.write_text(original)
    backup.chmod(0o600)
    plan['webroot_owned'] = plan['kind'] == 'host' and not Path(plan['webroot']).exists()
    ensure_directory(Path(plan['webroot']))
    plan['phase'] = 'prepared'
    save_state(plan)
    finish_setup(plan, auth_file)


def renewal_webroot(path, domain):
    mapping = re.search(r'^\s*' + re.escape(domain) + r'\s*=\s*(.*?)\s*$', path.read_text(), re.M)
    return mapping[1].rstrip(',') if mapping else None


def certbot_binary():
    binary = shutil.which('certbot')
    if not binary and os.access('/snap/bin/certbot', os.X_OK):
        binary = '/snap/bin/certbot'
    if not binary:
        raise SetupError('Нужен Certbot на хосте (apt-get install certbot).')
    return binary


def finish_setup(plan, auth_file):
    # Save the exact owned text before each write so interrupted setup remains removable.
    body = http_config(plan) if plan['phase'] != 'ready' else https_config(plan)
    set_block(plan, body)
    token = secrets.token_hex(16)
    challenge_dir = Path(plan['webroot']) / '.well-known/acme-challenge'
    ensure_directory(challenge_dir)
    challenge = challenge_dir / ('gokaskad-' + token)
    challenge.write_text(token)
    challenge.chmod(0o644)
    try:
        wait_http(f"http://{plan['domain']}/.well-known/acme-challenge/{challenge.name}", plan['domain'], 200, token=token)
    finally:
        challenge.unlink(missing_ok=True)
    certbot = certbot_binary()
    # Existing deploy hooks must run in the host, not a disposable Certbot container.
    run([certbot, 'certonly', '--non-interactive', '--agree-tos', '--register-unsafely-without-email',
         '--webroot', '--webroot-path', plan['webroot'], '--cert-name', plan['domain'],
         '--domain', plan['domain'], '--keep-until-expiring'], timeout=240)
    renewal = (LE / 'renewal') / (plan['domain'] + '.conf')
    # An older certificate may have been issued in Docker with a container-only webroot.
    # Certbot writes nested [[webroot_map]] sections; read the domain mapping explicitly.
    if renewal_webroot(renewal, plan['domain']) != plan['webroot']:
        run([certbot, 'reconfigure', '--non-interactive', '--cert-name', plan['domain'],
             '--webroot', '--webroot-path', plan['webroot']], timeout=240)
        if renewal_webroot(renewal, plan['domain']) != plan['webroot']:
            raise SetupError('Certbot не сохранил webroot для продления. Проверьте renewal-конфигурацию и повторите --resume-domain.')
    if plan['kind'] == 'docker':
        override = ROOT / 'config/proxy.compose.yaml'
        override.write_text('services:\n  nginx:\n    networks:\n      existing-proxy:\n        aliases: [' + ALIAS + ']\nnetworks:\n  existing-proxy:\n    external: true\n    name: ' + plan['network'] + '\n')
        env = ROOT / 'current/.env'
        text = env.read_text()
        entry = 'COMPOSE_FILE=compose.yaml:' + str(override)
        existing = re.search(r'^COMPOSE_FILE=(.*)$', text, re.M)
        if existing and existing[1] != entry.split('=', 1)[1]:
            raise SetupError('COMPOSE_FILE уже содержит другие настройки; они не изменены.')
        if not existing:
            env.write_text(text.rstrip('\n') + '\n' + entry + '\n')
        compose('config', '--quiet')
        compose('up', '-d', '--no-deps', 'nginx')
    copy_certificate(plan)
    hook = (LE / 'renewal-hooks/deploy/gokaskad-panel')
    hook.parent.mkdir(parents=True, exist_ok=True)
    expected = '#!/bin/sh\nset -eu\nif [ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/' + plan['domain'] + '" ]; then\n    /usr/bin/python3 /opt/go-kaskad/config/proxy_setup.py renew\nfi\n'
    if hook.exists() and hook.read_text() != expected:
        raise SetupError('Deploy hook gokaskad-panel уже существует и отличается; он не заменён.')
    hook.write_text(expected)
    hook.chmod(0o755)
    timers = run(['systemctl', 'list-timers', '--all', '--no-legend']).stdout
    if not re.search(r'\b(?:snap\.)?certbot[.\w-]*\.timer\b', timers):
        run(['systemctl', 'enable', '--now', 'certbot.timer'])
    body = https_config(plan)
    set_block(plan, body)
    wait_http(f"https://{plan['domain']}/api/health", plan['domain'], 401)
    if auth_file:
        wait_http(f"https://{plan['domain']}/api/health", plan['domain'], 200,
                  token='"ready": true', auth_file=auth_file)
    plan['phase'] = 'ready'
    save_state(plan)
    print(f"Готово: https://{plan['domain']}, пользователь admin. / Ready.")


def remove(plan):
    validate_paths(plan)
    conf = Path(plan['conf'])
    original = conf.read_text() if conf.exists() else ''
    before, block, after = marked(original)
    if block and block not in (plan.get('block'), plan.get('pending_block')):
        raise SetupError('Блок nginx изменён вручную. Удаление остановлено; проверьте config/proxy.json.')
    # Keep all unrelated edits, rather than restoring an old full-file backup.
    if block:
        conf.write_text(before + after)
        try:
            nginx(plan, '-t')
            nginx(plan, '-s', 'reload')
        except (subprocess.SubprocessError, OSError):
            conf.write_text(original)
            raise
        if not plan['conf_existed'] and not conf.read_text().strip():
            conf.unlink()
    hook = (LE / 'renewal-hooks/deploy/gokaskad-panel')
    if hook.exists() and '/opt/go-kaskad/config/proxy_setup.py renew' in hook.read_text():
        hook.unlink()
    if plan.get('certificate_owned') and ((LE / 'renewal') / (plan['domain'] + '.conf')).exists():
        dump = nginx(plan, '-T')
        if f"/live/{plan['domain']}/" not in dump.stdout + dump.stderr:
            run([certbot_binary(), 'delete', '--non-interactive', '--cert-name', plan['domain']])
        else:
            print('Сертификат используется другим блоком nginx и сохранён.')
    if plan.get('webroot_owned'):
        # Do not remove someone else's later files in the webroot.
        for path in (Path(plan['webroot']) / '.well-known/acme-challenge', Path(plan['webroot']) / '.well-known', Path(plan['webroot'])):
            try:
                path.rmdir()
            except OSError:
                pass
    (ROOT / 'config/proxy.json').unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['discover', 'setup', 'resume', 'renew', 'remove', 'domain'])
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--domain')
    parser.add_argument('--port', type=int)
    parser.add_argument('--auth-file', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'domain':
            print(domain_name(args.domain))
        elif args.command == 'discover':
            plan = discover()
            if plan['kind'] != 'standalone':
                validate_paths(plan)
            if args.plan:
                args.plan.write_text(json.dumps(plan))
            print(plan['kind'])
        elif args.command == 'setup':
            setup(json.loads(args.plan.read_text()), args.domain, args.port, args.auth_file)
        else:
            plan = json.loads((ROOT / 'config/proxy.json').read_text())
            if args.command == 'resume':
                finish_setup(plan, args.auth_file)
            elif args.command == 'renew':
                copy_certificate(plan)
                nginx(plan, '-t')
                nginx(plan, '-s', 'reload')
            else:
                remove(plan)
    except (SetupError, OSError, ValueError, subprocess.SubprocessError) as exc:
        # Command output may include configuration secrets. Do not dump nginx -T or certificate keys.
        print(f'Gokaskad: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
