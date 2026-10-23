import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import http.client
import http.server
import threading

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("firewall", ROOT / "web/worker/firewall.py")
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)
api_spec = importlib.util.spec_from_file_location("api", ROOT / "web/api/server.py")
api = importlib.util.module_from_spec(api_spec)
api_spec.loader.exec_module(api)


def rule(**changes):
    return dict(id=str(uuid.uuid4()), name="VPN", protocol="tcp", input_port=8080,
                target_ip="192.0.2.10", target_port=80, enabled=True, **changes)


class FirewallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        state = patch.object(fw, "STATE", Path(self.temp.name))
        state.start()
        self.addCleanup(state.stop)
        env = patch.dict(os.environ, {"MANAGEMENT_PORT": "8443"})
        env.start()
        self.addCleanup(env.stop)

    def test_validation_rejects_reserved_port_types_and_injection(self):
        for field, value in (("input_port", 8443), ("input_port", True), ("target_port", 0),
                             ("target_ip", "127.0.0.1"), ("target_ip", "1.2.3.4; reboot"),
                             ("protocol", "tcp -j ACCEPT"), ("enabled", "false")):
            candidate = rule()
            candidate[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(fw.Invalid):
                fw.validate(candidate)

    @patch.object(fw, "apply")
    def test_crud_persistence_and_collision(self, apply):
        first = rule()
        fw.execute("put", first)
        self.assertEqual(fw.execute("list")["rules"], [first])
        with self.assertRaises(fw.Invalid):
            fw.execute("put", rule())
        first["enabled"] = False
        fw.execute("put", first)
        fw.execute("put", rule())
        self.assertEqual(len(fw.execute("list")["rules"]), 2)
        fw.execute("delete", {"id": first["id"]})
        self.assertEqual(len(fw.execute("list")["rules"]), 1)
        self.assertTrue(apply.called)

    @patch.object(fw, "apply")
    def test_failed_firewall_change_restores_previous_database_and_rules(self, apply):
        first = rule()
        fw.execute("put", first)
        apply.reset_mock()
        apply.side_effect = [RuntimeError("iptables failure"), None]
        changed = dict(first, target_port=81)
        with self.assertRaises(RuntimeError):
            fw.execute("put", changed)
        self.assertEqual(fw.execute("list")["rules"], [first])
        self.assertEqual(apply.call_args_list[-1].args[0], [first])

    @patch.object(fw, "apply")
    def test_reconcile_restores_persisted_state(self, apply):
        first = rule()
        fw.execute("put", first)
        apply.reset_mock()
        fw.execute("reconcile")
        apply.assert_called_once_with([first])

    def test_render_touches_only_owned_chains_and_marks_traffic(self):
        active = rule()
        disabled = dict(rule(), enabled=False, input_port=9090)
        text = fw.render([active, disabled])
        self.assertIn("--to-destination 192.0.2.10:80", text)
        self.assertIn("--set-xmark", text)
        self.assertIn("--dst-type LOCAL", text)
        self.assertNotIn("9090", text)
        self.assertNotIn("-F FORWARD", text)
        self.assertNotIn("-F PREROUTING", text)


class APITests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.rpc = patch.object(api, "rpc", return_value=(200, {"rules": []})).start()
        self.addCleanup(patch.stopall)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_csrf_requests_never_reach_worker(self):
        path = "/api/rules/" + str(uuid.uuid4())
        for headers in ({}, {"X-Gokaskad": "1", "Origin": "https://attacker.invalid"}):
            self.assertEqual(self.request("DELETE", path, headers=headers)[0], 403)
        self.rpc.assert_not_called()

    def test_system_firewall_is_read_only(self):
        self.rpc.return_value = (200, {"chains": [], "warnings": [], "sources": []})
        self.assertEqual(self.request("GET", "/api/firewall")[0], 200)
        self.rpc.assert_called_once_with("firewall")
        self.rpc.reset_mock()
        self.assertEqual(self.request("DELETE", "/api/firewall", headers={"X-Gokaskad": "1"})[0], 404)
        self.rpc.assert_not_called()

    def test_invalid_json_and_ids_never_reach_worker(self):
        headers = {"X-Gokaskad": "1", "Content-Type": "application/json"}
        self.assertEqual(self.request("PUT", "/api/rules/" + str(uuid.uuid4()), "[]", headers)[0], 400)
        self.assertEqual(self.request("DELETE", "/api/rules/not-a-uuid", headers=headers)[0], 400)
        self.rpc.assert_not_called()

    def test_api_forwards_validated_id_and_reports_worker_failure(self):
        candidate = rule()
        headers = {"X-Gokaskad": "1", "Content-Type": "application/json"}
        status, _ = self.request("PUT", "/api/rules/" + candidate["id"], json.dumps(candidate), headers)
        self.assertEqual(status, 200)
        self.rpc.assert_called_once_with("put", candidate)
        self.rpc.side_effect = OSError("unavailable")
        self.assertEqual(self.request("GET", "/api/rules")[0], 503)


if __name__ == "__main__":
    unittest.main()
