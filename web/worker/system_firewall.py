"""Read-only, structured view of host iptables and native nftables rules."""
from datetime import datetime, timezone
import json
import shlex
import shutil
import subprocess
import sys


def is_fail2ban(*names):
    return any('f2b' in name.lower() or 'fail2ban' in name.lower() for name in names)


def new_rule():
    return dict(protocol='Любой', source='Любой', destination='Любой', source_port='Любой',
                destination_port='Любой', action='Продолжить', conditions=[], source_members=[], destination_members=[])


def parse_iptables(text, backend, family):
    chains = {}
    table = ''
    fields = {'-p': 'protocol', '--protocol': 'protocol', '-s': 'source', '--source': 'source',
              '-d': 'destination', '--destination': 'destination', '--sport': 'source_port',
              '--sports': 'source_port', '--source-port': 'source_port', '--dport': 'destination_port',
              '--dports': 'destination_port', '--destination-port': 'destination_port', '-j': 'action',
              '--jump': 'action', '-g': 'action', '--goto': 'action'}
    labels = {'-i': 'Входящий интерфейс', '-o': 'Исходящий интерфейс', '--ctstate': 'Состояние',
              '-m': 'Модуль', '--mark': 'Метка', '--set-xmark': 'Установить метку',
              '--set-mark': 'Установить метку', '--mask': 'Маска', '--tcp-flags': 'Флаги TCP',
              '--dst-type': 'Тип назначения', '--src-type': 'Тип источника', '--icmp-type': 'Тип ICMP',
              '--limit': 'Лимит', '--limit-burst': 'Начальный лимит',
              '--state': 'Состояние', '--comment': 'Комментарий', '--to-destination': 'Назначение NAT',
              '--to-source': 'Источник NAT', '--reject-with': 'Ответ', '--match-set': 'Набор IP',
              '--in-interface': 'Входящий интерфейс', '--out-interface': 'Исходящий интерфейс'}
    for line in text.splitlines():
        if line.startswith('*'):
            table = line[1:]
        elif line.startswith(':'):
            name, policy, *_ = line[1:].split()
            chains[table, name] = dict(backend=backend, family=family, table=table, name=name,
                                      policy=None if policy == '-' else policy,
                                      fail2ban=is_fail2ban(name, table), rules=[])
        elif line.startswith(('-A ', '[')):
            if line.startswith('['):
                line = line.split('] ', 1)[-1]
            tokens = shlex.split(line)
            if len(tokens) < 2 or tokens[0] != '-A' or (table, tokens[1]) not in chains:
                continue
            r, i, negate = new_rule(), 2, False
            while i < len(tokens):
                key = tokens[i]
                i += 1
                if key == '!':
                    negate = True
                    continue
                values = []
                count = 2 if key in ('--tcp-flags', '--match-set', '-c') else 1
                while len(values) < count and i < len(tokens) and not tokens[i].startswith('-') and tokens[i] != '!':
                    values.append(tokens[i])
                    i += 1
                value = ('не ' if negate else '') + ' '.join(values)
                if key in fields and values:
                    r[fields[key]] = value
                elif key == '-m' and values and values[0] in ('tcp', 'udp', 'conntrack', 'state', 'multiport', 'comment'):
                    pass
                elif key != '-c':
                    r['conditions'].append((labels.get(key, key) + ': ' + value).rstrip(': '))
                negate = False
            chains[table, tokens[1]]['rules'].append(r)
    return list(chains.values())


def describe(value):
    if value is None:
        return ''
    if isinstance(value, list):
        return ', '.join(describe(v) for v in value)
    if isinstance(value, dict):
        if 'prefix' in value:
            p = value['prefix']
            return f"{p['addr']}/{p['len']}"
        if 'range' in value:
            return '–'.join(describe(v) for v in value['range'])
        if 'set' in value:
            return describe(value['set'])
        if 'elem' in value:
            return describe(value['elem'])
        if 'val' in value:
            return describe(value['val'])
        return '; '.join(str(key) + ': ' + describe(item) for key, item in value.items())
    return str(value)


def parse_nftables(data, covered=()):
    objects = data.get('nftables', [])
    chains, sets = {}, {}
    for obj in objects:
        if 'set' in obj:
            s = obj['set']
            sets[s['family'], s['table'], s['name']] = [describe(v) for v in s.get('elem', [])]
        if 'element' in obj:
            s = obj['element']
            sets.setdefault((s['family'], s['table'], s['name']), []).extend(describe(v) for v in s.get('elem', []))
        if 'chain' in obj:
            c = obj['chain']
            key = c['family'], c['table'], c['name']
            if key in covered:
                continue
            chains[key] = dict(backend='nftables', family={'ip': 'IPv4', 'ip6': 'IPv6', 'inet': 'IPv4/IPv6'}.get(c['family'], c['family']),
                               table=c['table'], name=c['name'], policy=c.get('policy', '').upper() or None,
                               fail2ban=is_fail2ban(c['name'], c['table']), rules=[])
            if c.get('hook'):
                chains[key]['hook'] = c['hook']
    for obj in objects:
        if 'rule' not in obj:
            continue
        node = obj['rule']
        key = node['family'], node['table'], node['chain']
        if key not in chains:
            continue
        r = new_rule()
        if node.get('comment'):
            r['conditions'].append('Комментарий: ' + node['comment'])
        for expression in node.get('expr', []):
            if 'match' in expression:
                match = expression['match']
                left, right = match.get('left', {}), match.get('right')
                op = match.get('op', '==')
                value = ('не ' if op == '!=' else '' if op in ('==', 'in') else op + ' ') + describe(right)
                field = None
                if isinstance(left, dict) and 'payload' in left:
                    payload = left['payload']
                    field = {'saddr': 'source', 'daddr': 'destination', 'sport': 'source_port',
                             'dport': 'destination_port', 'protocol': 'protocol', 'nexthdr': 'protocol'}.get(payload.get('field'))
                    if payload.get('protocol') in ('tcp', 'udp', 'icmp', 'icmpv6'):
                        r['protocol'] = payload['protocol']
                elif isinstance(left, dict) and left.get('meta', {}).get('key') == 'l4proto':
                    field = 'protocol'
                if field:
                    r[field] = value
                    if field in ('source', 'destination') and isinstance(right, str) and right.startswith('@'):
                        r[field + '_members'] = sets.get((node['family'], node['table'], right[1:]), [])
                else:
                    label = describe(left)
                    if isinstance(left, dict):
                        meta = left.get('meta', {}).get('key')
                        ct = left.get('ct', {}).get('key')
                        label = {'iifname': 'Входящий интерфейс', 'oifname': 'Исходящий интерфейс',
                                 'iif': 'Входящий интерфейс', 'oif': 'Исходящий интерфейс',
                                 'nfproto': 'Семейство', 'mark': 'Метка пакета'}.get(meta, label)
                        label = {'state': 'Состояние', 'mark': 'Метка соединения', 'status': 'Статус соединения'}.get(ct, label)
                    r['conditions'].append(label + ' ' + op + ' ' + describe(right))
            else:
                for kind, value in expression.items():
                    if kind in ('accept', 'drop', 'return', 'reject', 'masquerade', 'dnat', 'snat', 'queue'):
                        r['action'] = kind.upper()
                        if value:
                            r['conditions'].append(kind.upper() + ': ' + describe(value))
                    elif kind in ('jump', 'goto'):
                        r['action'] = value['target']
                    elif kind == 'log':
                        r['conditions'].append('Журнал: ' + (describe(value) or 'включён'))
                    elif kind != 'counter':
                        r['conditions'].append(kind + ': ' + describe(value))
                        if r['action'] == 'Продолжить':
                            r['action'] = 'См. условия'
        chains[key]['rules'].append(r)
    return list(chains.values())


def snapshot():
    chains, warnings, covered, sources = [], [], set(), []
    # Read both backends: fail2ban and Docker may use different ones.
    for backend in ('nft', 'legacy'):
        for family, binary, nft_family in (('IPv4', 'iptables', 'ip'), ('IPv6', 'ip6tables', 'ip6')):
            command = f'{binary}-{backend}-save'
            if not shutil.which(command):
                warnings.append(f'{family} / {backend}: средство чтения недоступно')
                continue
            try:
                output = subprocess.run([command], check=True, text=True, capture_output=True, timeout=12)
                parsed = parse_iptables(output.stdout, 'iptables-' + backend, family)
                chains.extend(parsed)
                sources.append(f'{family} / {backend}')
                if backend == 'nft':
                    covered.update((nft_family, c['table'], c['name']) for c in parsed)
            except (subprocess.SubprocessError, OSError, ValueError) as exc:
                warnings.append(f'{family} / {backend}: не удалось прочитать правила')
                # Diagnostic output stays in worker logs, never replaces the table.
                print(f'{command}: {exc}', file=sys.stderr)
    if shutil.which('nft'):
        try:
            output = subprocess.run(['nft', '-j', 'list', 'ruleset'], check=True, text=True, capture_output=True, timeout=12)
            chains.extend(parse_nftables(json.loads(output.stdout), covered))
            sources.append('nftables')
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
            warnings.append('nftables: не удалось прочитать правила')
            print(f'nftables: {exc}', file=sys.stderr)
    else:
        warnings.append('nftables: средство чтения недоступно; отдельные цепочки fail2ban могут быть не видны')
    return dict(chains=chains, warnings=warnings, sources=sources,
                updated_at=datetime.now(timezone.utc).isoformat())
