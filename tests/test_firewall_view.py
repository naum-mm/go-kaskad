import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

sf = load('system_firewall', 'web/worker/system_firewall.py')
fw = load('firewall_view_cli', 'web/worker/firewall.py')

IPTABLES = '''*filter
:INPUT DROP [0:0]
:f2b-sshd - [0:0]
:EMPTY - [0:0]
-A INPUT -p tcp -m multiport --dports 22,2222 -j f2b-sshd
[12:240] -A f2b-sshd ! -s 203.0.113.9/32 -p tcp -m comment --comment "test <script>" -j REJECT --reject-with tcp-reset
-A f2b-sshd -j RETURN
COMMIT
'''
NFT = {'nftables': [
    {'chain': {'family': 'ip', 'table': 'filter', 'name': 'INPUT', 'policy': 'drop'}},
    {'set': {'family': 'inet', 'table': 'f2b-table', 'name': 'addr-set-sshd', 'elem': ['203.0.113.18', {'prefix': {'addr': '198.51.100.0', 'len': 24}}]}},
    {'chain': {'family': 'inet', 'table': 'f2b-table', 'name': 'f2b-chain', 'hook': 'input', 'policy': 'accept'}},
    {'rule': {'family': 'inet', 'table': 'f2b-table', 'chain': 'f2b-chain', 'expr': [
        {'match': {'op': '==', 'left': {'meta': {'key': 'l4proto'}}, 'right': 'tcp'}},
        {'match': {'op': '==', 'left': {'payload': {'protocol': 'tcp', 'field': 'dport'}}, 'right': {'set': [22, 2222]}}},
        {'match': {'op': 'in', 'left': {'payload': {'protocol': 'ip', 'field': 'saddr'}}, 'right': '@addr-set-sshd'}},
        {'counter': {'packets': 10, 'bytes': 1234}}, {'reject': {'type': 'tcp reset'}}]}}
]}


class FirewallViewTests(unittest.TestCase):
    def test_iptables_fail2ban_policy_order_ports_negation_and_empty_chain(self):
        chains = sf.parse_iptables(IPTABLES, 'iptables-nft', 'IPv4')
        self.assertEqual(len(chains), 3)
        self.assertEqual(chains[0]['policy'], 'DROP')
        self.assertEqual(chains[0]['rules'][0]['action'], 'f2b-sshd')
        self.assertEqual(chains[0]['rules'][0]['destination_port'], '22,2222')
        self.assertTrue(chains[1]['fail2ban'])
        self.assertEqual(chains[1]['rules'][0]['source'], 'не 203.0.113.9/32')
        self.assertIn('Комментарий: test <script>', chains[1]['rules'][0]['conditions'])
        self.assertEqual(chains[1]['rules'][1]['action'], 'RETURN')
        self.assertEqual(chains[2]['rules'], [])

    def test_native_fail2ban_sets_and_deduplication(self):
        chains = sf.parse_nftables(NFT, {('ip', 'filter', 'INPUT')})
        self.assertEqual(len(chains), 1)
        chain = chains[0]
        self.assertTrue(chain['fail2ban'])
        self.assertEqual(chain['family'], 'IPv4/IPv6')
        self.assertEqual(chain['hook'], 'input')
        self.assertEqual(chain['rules'][0]['source_members'], ['203.0.113.18', '198.51.100.0/24'])
        self.assertEqual(chain['rules'][0]['destination_port'], '22, 2222')
        self.assertEqual(chain['rules'][0]['action'], 'REJECT')

    def test_unrecognized_conditions_are_preserved(self):
        data = {'nftables': [
            {'chain': {'family': 'ip6', 'table': 'custom', 'name': 'input'}},
            {'rule': {'family': 'ip6', 'table': 'custom', 'chain': 'input', 'expr': [
                {'limit': {'rate': 5, 'per': 'second'}}, {'drop': None}]}}
        ]}
        chain = sf.parse_nftables(data)[0]
        self.assertEqual(chain['family'], 'IPv6')
        self.assertIn('limit:', chain['rules'][0]['conditions'][0])
        self.assertEqual(chain['rules'][0]['action'], 'DROP')

    def test_logging_is_preserved_alongside_terminal_verdict(self):
        data = {'nftables': [
            {'chain': {'family': 'ip', 'table': 'filter', 'name': 'INPUT'}},
            {'rule': {'family': 'ip', 'table': 'filter', 'chain': 'INPUT', 'expr': [{'log': None}, {'accept': None}]}}
        ]}
        r = sf.parse_nftables(data)[0]['rules'][0]
        self.assertEqual(r['action'], 'ACCEPT')
        self.assertEqual(r['conditions'], ['Журнал: включён'])

    @patch.object(sf.shutil, 'which', return_value='/usr/sbin/tool')
    def test_snapshot_uses_only_read_commands_and_both_backends(self, which):
        def read(args, **kwargs):
            out = json.dumps(NFT) if args[0] == 'nft' else IPTABLES if args[0] == 'iptables-nft-save' else ''
            return subprocess.CompletedProcess(args, 0, out, '')
        with patch.object(sf.subprocess, 'run', side_effect=read) as run:
            result = sf.snapshot()
        self.assertEqual(result['warnings'], [])
        self.assertEqual(len(result['chains']), 4)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands, [['iptables-nft-save'], ['ip6tables-nft-save'], ['iptables-legacy-save'], ['ip6tables-legacy-save'], ['nft', '-j', 'list', 'ruleset']])

    @patch.object(sf.shutil, 'which', return_value='/usr/sbin/tool')
    def test_partial_read_failure_is_reported(self, which):
        def read(args, **kwargs):
            if args[0] == 'nft':
                return subprocess.CompletedProcess(args, 0, json.dumps(NFT), '')
            raise subprocess.CalledProcessError(1, args, stderr='Permission denied')
        with patch.object(sf.subprocess, 'run', side_effect=read):
            result = sf.snapshot()
        self.assertEqual(len(result['warnings']), 4)
        self.assertEqual(result['sources'], ['nftables'])
        self.assertTrue(any(c['fail2ban'] for c in result['chains']))

    def test_firewall_command_does_not_open_or_modify_database(self):
        fake = types.ModuleType('system_firewall')
        fake.snapshot = lambda: {'chains': []}
        with patch.dict(sys.modules, {'system_firewall': fake}), patch.object(fw, 'connect', side_effect=AssertionError('Must not open DB')):
            self.assertEqual(fw.execute('firewall'), {'chains': []})

if __name__ == '__main__':
    unittest.main()
