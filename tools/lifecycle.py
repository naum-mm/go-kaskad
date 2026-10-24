#!/usr/bin/env python3
"""Release updates, consistent backups and reversible installation-mode changes."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import proxy_setup as proxy

ROOT = Path('/opt/go-kaskad')
BACKUPS = Path('/root/gokaskad-backups')


def run(args, **kwargs):
    kwargs.setdefault('stdin', subprocess.DEVNULL)
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=600, **kwargs)


def compose(*args, directory=None):
    return run(['docker', 'compose', *args], cwd=directory or ROOT / 'current')


def env_values(path=None):
    path = path or ROOT / 'current/.env'
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if '=' in line and not line.startswith('#'))


def write_env(values):
    path = ROOT / 'current/.env'
    path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    path.chmod(0o600)


def read_plan():
    path = ROOT / 'config/proxy.json'
    return json.loads(path.read_text()) if path.exists() else None


def backup():
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(tempfile.mkdtemp(prefix=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=BACKUPS))
    shutil.copytree(ROOT / 'config', directory / 'config')
    shutil.copy2(ROOT / 'current/.env', directory / 'env')
    (directory / 'previous-release').write_text(str((ROOT / 'current').resolve()))
    # Online SQLite backup is consistent even if a rule is committed concurrently.
    code = '''import sqlite3,sys
source=sqlite3.connect("file:/var/lib/gokaskad/rules.sqlite3?mode=ro",uri=True)
snapshot=sqlite3.connect(":memory:")
source.backup(snapshot)
sys.stdout.buffer.write(snapshot.serialize())
'''
    with (directory / 'rules.sqlite3').open('wb') as output:
        subprocess.run(['docker', 'compose', 'exec', '-T', 'worker', 'python3', '-c', code],
                       cwd=ROOT / 'current', stdin=subprocess.DEVNULL, stdout=output, check=True, timeout=60)
    plan = read_plan()
    if plan and proxy.certificate_source(plan).is_dir():
        shutil.copytree(proxy.certificate_source(plan), directory / 'certificate-source')
    hook = proxy.LE / 'renewal-hooks/deploy/gokaskad-panel'
    if hook.exists():
        shutil.copy2(hook, directory / 'renewal-hook')
    print(f'Резервная копия: {directory}', flush=True)
    return directory


def switch_current(directory):
    link = ROOT / 'current.next'
    link.unlink(missing_ok=True)
    link.symlink_to(directory)
    link.replace(ROOT / 'current')


def update(source, *, reinstall=False):
    saved = backup()
    previous = (ROOT / 'current').resolve()
    release = Path(tempfile.mkdtemp(prefix='update.', dir=ROOT / 'releases'))
    shutil.copytree(source, release, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.git', '.local', '__pycache__'))
    shutil.copy2(saved / 'env', release / '.env')
    try:
        compose('config', '--quiet', directory=release)
        arguments = ['up', '-d', '--build', '--wait', '--wait-timeout', '180']
        if reinstall:
            arguments.append('--force-recreate')
        compose(*arguments, directory=release)
        compose('exec', '-T', 'nginx', 'nginx', '-t', directory=release)
        staging = ROOT / 'config/proxy_setup.py.new'
        shutil.copy2(source / 'tools/proxy_setup.py', staging)
        staging.chmod(0o600)
        staging.replace(ROOT / 'config/proxy_setup.py')
        switch_current(release)
    except Exception:
        print('Обновление не завершено. Восстанавливаю предыдущий release.', file=sys.stderr)
        compose('up', '-d', '--build', '--wait', '--wait-timeout', '180', directory=previous)
        switch_current(previous)
        shutil.copy2(saved / 'config/proxy_setup.py', ROOT / 'config/proxy_setup.py')
        raise
    print('Обновление завершено. Пароль, режим и правила сохранены.')


def certificate_dependency():
    try:
        return proxy.certbot_binary()
    except proxy.SetupError:
        # Track newly introduced packages for the existing package-removal manifest.
        before = set(run(['dpkg-query', '-W', '-f=${binary:Package}\t${db:Status-Status}\n']).stdout.splitlines())
        env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
        run(['apt-get', 'update'], env=env)
        run(['apt-get', 'install', '-y', '--no-install-recommends', 'certbot'], env=env)
        after = set(run(['dpkg-query', '-W', '-f=${binary:Package}\t${db:Status-Status}\n']).stdout.splitlines())
        path = ROOT / 'install-state/new-packages'
        existing = set(path.read_text().splitlines()) if path.exists() else set()
        introduced = {row.split('\t')[0] for row in after - before if row.endswith('\tinstalled')}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(sorted(existing | introduced)) + '\n')
        return proxy.certbot_binary()


def check_port(port, previous_port=None):
    if port != previous_port and run(['ss', '-H', '-ltn', f'sport = :{port}']).stdout.strip():
        raise proxy.SetupError(f'TCP {port} уже занят. Выберите другой порт или режим SNI.')


def direct_certificate(domain, certificate_type, port, previous):
    if certificate_type == 'selfsigned':
        certificate = ROOT / 'config/tls/server.crt'
        if certificate.exists():
            checked = proxy.run(['openssl', 'x509', '-in', str(certificate), '-noout', '-subject', '-issuer', '-checkend', '2592000'], check=False)
            names = dict(line.split('=', 1) for line in checked.stdout.splitlines() if line.startswith(('subject=', 'issuer=')))
            same_name = (not domain or proxy.run(['openssl', 'x509', '-in', str(certificate), '-noout', '-checkhost', domain], check=False).returncode == 0)
            if checked.returncode == 0 and names.get('subject') and names.get('subject') == names.get('issuer') and same_name and (not previous or previous['kind'] == 'direct'):
                return
        # Generate a matching self-signed certificate when changing type/domain or expiring.
        san = 'DNS:localhost,IP:127.0.0.1'
        if domain:
            san += ',DNS:' + domain
        for line in run(['ip', '-o', '-4', 'addr', 'show', 'scope', 'global']).stdout.splitlines():
            words = line.split()
            if 'inet' in words:
                san += ',IP:' + words[words.index('inet') + 1].split('/')[0]
        proxy.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-sha256', '-nodes', '-days', '365',
                   '-subj', '/CN=Gokaskad', '-addext', 'subjectAltName=' + san,
                   '-keyout', str(ROOT / 'config/tls/server.key'), '-out', str(ROOT / 'config/tls/server.crt')])
        compose('exec', '-T', 'nginx', 'nginx', '-t')
        compose('exec', '-T', 'nginx', 'nginx', '-s', 'reload')
        return
    certbot = certificate_dependency()
    if run(['ss', '-H', '-ltn', 'sport = :80']).stdout.strip():
        raise proxy.SetupError('Certbot standalone требует свободный TCP 80. Для существующего nginx выберите SNI.')
    plan = dict(kind='direct', domain=domain, port=port, certificate_type='certbot', phase='prepared',
                certificate_owned=not (proxy.LE / 'live' / domain / 'fullchain.pem').exists())
    if previous and previous['domain'] == domain and previous.get('certificate_type', 'certbot') == 'certbot':
        plan['certificate_owned'] = previous.get('certificate_owned', False)
    certificate = proxy.certificate_source(plan) / 'fullchain.pem'
    if certificate.exists():
        import re
        names = run(['openssl', 'x509', '-in', str(certificate), '-noout', '-ext', 'subjectAltName']).stdout
        if set(re.findall(r'DNS:([^,\s]+)', names)) != {domain}:
            raise proxy.SetupError('Существующий сертификат содержит другие домены; он не заменён.')
    proxy.save_state(plan)
    proxy.run([certbot, 'certonly' , '--standalone', '--non-interactive', '--agree-tos',
               '--register-unsafely-without-email', '--cert-name', domain, '--domain', domain,
               '--keep-until-expiring'], timeout=240)
    # Fix a certificate previously issued using a webroot plugin, before timer renewal.
    renewal = proxy.LE / 'renewal' / (domain + '.conf')
    if 'authenticator = standalone' not in renewal.read_text():
        proxy.run([certbot, 'reconfigure', '--non-interactive', '--cert-name', domain, '--standalone'], timeout=240)
        if 'authenticator = standalone' not in renewal.read_text():
            raise proxy.SetupError('Certbot не сохранил standalone для продления.')
    proxy.copy_certificate(plan)
    proxy.install_renewal_hook(plan)
    plan['phase'] = 'ready'
    proxy.save_state(plan)


def current_settings():
    env = env_values()
    plan = read_plan()
    settings = ROOT / 'config/installation.json'
    if settings.exists():
        return json.loads(settings.read_text())
    return dict(mode='proxy' if plan and plan['kind'] != 'direct' else 'standalone',
                certificate=plan.get('certificate_type', 'certbot') if plan else 'selfsigned',
                domain=plan['domain'] if plan else '', port=int(env['MANAGEMENT_PORT']))


def restore(saved):
    failed = read_plan()
    if failed:
        proxy.remove(failed)
    current_config = ROOT / 'config'
    # Preserve directory inodes: TLS is bind-mounted in the running nginx.
    for path in sorted(current_config.rglob('*'), key=lambda item: len(item.parts), reverse=True):
        relative = path.relative_to(current_config)
        if not (saved / 'config' / relative).exists():
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
    shutil.copytree(saved / 'config', current_config, dirs_exist_ok=True)
    shutil.copy2(saved / 'env', ROOT / 'current/.env')
    previous = read_plan()
    if previous and (saved / 'certificate-source').exists():
        shutil.copytree(saved / 'certificate-source', proxy.certificate_source(previous), dirs_exist_ok=True)
    compose('up', '-d', '--wait', '--wait-timeout', '180')
    compose('exec', '-T', 'nginx', 'nginx', '-t')
    compose('exec', '-T', 'nginx', 'nginx', '-s', 'reload')
    if previous and previous['kind'] != 'direct':
        proxy.set_block(previous, previous['block'])
    hook = proxy.LE / 'renewal-hooks/deploy/gokaskad-panel'
    if (saved / 'renewal-hook').exists():
        shutil.copy2(saved / 'renewal-hook', hook)
    # New state files must not survive a rollback to a mode that never had them.
    for name in ('proxy.json', 'installation.json'):
        if not (saved / 'config' / name).exists():
            (current_config / name).unlink(missing_ok=True)


def retire(plan):
    path = ROOT / 'config/retired-proxies.json'
    plans = json.loads(path.read_text()) if path.exists() else []
    if not any(p['domain'] == plan['domain'] and p.get('certificate_type') == plan.get('certificate_type') for p in plans):
        plans.append(plan)
    path.write_text(json.dumps(plans, indent=2))


def change(mode, certificate_type, domain, port):
    if mode not in ('standalone', 'proxy') or certificate_type not in ('selfsigned', 'certbot'):
        raise proxy.SetupError('Некорректный режим или тип сертификата.')
    if domain:
        domain = proxy.domain_name(domain)
    if (mode == 'proxy' or certificate_type == 'certbot') and not domain:
        raise proxy.SetupError('Для SNI / Certbot нужен домен.')
    if not 1 <= port <= 65535:
        raise proxy.SetupError('Порт должен быть от 1 до 65535.')
    env = env_values()
    previous = read_plan()
    saved_plan = ROOT / 'saved/proxy.json'
    ownership = previous or (json.loads(saved_plan.read_text()) if saved_plan.exists() else None)
    # Reject legacy manual integration before touching someone else's virtual host.
    if env.get('COMPOSE_FILE') and not previous:
        raise proxy.SetupError('Обнаружено ручное подключение proxy. Автоматическая смена требует манифеста; обычное обновление доступно.')
    check_port(port, int(env['MANAGEMENT_PORT']))
    rules = json.loads(compose('exec', '-T', 'worker', 'bash', '/app/gokaskad.sh', '--machine', 'list').stdout)['rules']
    if any(r['input_port'] == port and r['enabled'] for r in rules):
        raise proxy.SetupError('Выбранный порт используется правилом перенаправления. Сначала отключите это правило.')
    if mode == 'proxy':
        if any(r['protocol'] == 'tcp' and r['input_port'] in (80, 443) and r['enabled'] for r in rules):
            raise proxy.SetupError('Сначала отключите перенаправления TCP 80/443 в панели.')
        plan = proxy.discover()
        if plan['kind'] == 'standalone':
            raise proxy.SetupError('Не найден существующий nginx на 80/443 для SNI.')
    elif certificate_type == 'certbot' and run(['ss', '-H', '-ltn', 'sport = :80']).stdout.strip():
        raise proxy.SetupError('Для Certbot standalone нужен свободный TCP 80. Используйте режим SNI.')
    if certificate_type == 'certbot':
        certificate_dependency()
    saved = backup()
    try:
        if previous:
            proxy.remove(previous, preserve_certificate=True)
        env.pop('COMPOSE_FILE', None)
        env.update(MANAGEMENT_PORT=str(port), MANAGEMENT_BIND='127.0.0.1' if mode == 'proxy' else '')
        if mode == 'proxy':
            env['RESERVED_TCP_PORTS'] = ','.join(sorted((set(env.get('RESERVED_TCP_PORTS', '').split(',')) | {'80', '443'}) - {''}))
        write_env(env)
        compose('up', '-d', '--wait', '--wait-timeout', '180')
        if mode == 'proxy':
            plan['certificate_type'] = certificate_type
            if ownership and ownership['domain'] == domain and ownership.get('certificate_type', 'certbot') == certificate_type:
                plan['certificate_owned'] = ownership.get('certificate_owned', False)
            proxy.setup(plan, domain, port, None)
        else:
            direct_certificate(domain, certificate_type, port, ownership)
        target = domain or 'localhost'
        args = ['curl', '--noproxy', '*', '--silent', '--show-error', '--connect-timeout', '3', '--max-time', '8',
                '--resolve', f'{target}:{port}:127.0.0.1', '--output', '/dev/null', '--write-out', '%{http_code}']
        if certificate_type == 'selfsigned':
            args += ['--cacert', str(ROOT / 'config/tls/server.crt')]
        for attempt in range(30):
            response = proxy.run(args + [f'https://{target}:{port}/api/health'], check=False)
            if response.returncode == 0 and response.stdout == '401':
                break
            proxy.time.sleep(1)
        else:
            raise proxy.SetupError('Не прошла проверка HTTPS после смены режима.')
        (ROOT / 'config/installation.json').write_text(json.dumps(dict(mode=mode, certificate=certificate_type, domain=domain, port=port), indent=2))
        if ownership and (not read_plan() or ownership['domain'] != domain or ownership.get('certificate_type', 'certbot') != certificate_type):
            retire(ownership)
        (ROOT / 'saved/proxy.json').unlink(missing_ok=True)
    except Exception:
        print('Новый режим не настроен. Восстанавливаю прежнюю конфигурацию.', file=sys.stderr)
        restore(saved)
        raise
    print('Режим изменён. Пароль и правила сохранены.')
    if certificate_type == 'selfsigned':
        print(run(['openssl', 'x509', '-in', str(ROOT / 'config/tls/server.crt'), '-noout', '-fingerprint', '-sha256']).stdout)
    print(f"Панель: https://{domain or '<IP-сервера>'}" + ('' if mode == 'proxy' or port == 443 else ':' + str(port)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['update', 'reinstall', 'change', 'status'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--mode', choices=['standalone', 'proxy'])
    parser.add_argument('--certificate', choices=['selfsigned', 'certbot'], default='selfsigned')
    parser.add_argument('--domain', default='')
    parser.add_argument('--port', type=int, default=8443)
    args = parser.parse_args()
    os.umask(0o077)
    proxy.ROOT = ROOT
    try:
        with (ROOT / 'install.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action in ('update', 'reinstall'):
                update(args.source, reinstall=args.action == 'reinstall')
            elif args.action == 'status':
                print(json.dumps(current_settings(), ensure_ascii=False))
            else:
                change(args.mode, args.certificate, args.domain, args.port)
    except (OSError, ValueError, proxy.SetupError, subprocess.SubprocessError) as exc:
        print(f'Gokaskad: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
