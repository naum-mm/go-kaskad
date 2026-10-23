"""Run against an already started gokaskad-test stack using compose.smoke.yaml.

SMOKE_PASSWORD, SMOKE_CA (certificate file) are required; port defaults to 18443.
All firewall mutations occur inside a checked, isolated worker namespace.
"""
import base64
import json
import os
from pathlib import Path
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import http.client


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


project = "gokaskad-test"
worker = project + "-worker-1"
info = json.loads(docker("inspect", worker))[0]
assert info["HostConfig"]["NetworkMode"] == "bridge", "Refusing to test against a host-network worker"
worker_ip = info["NetworkSettings"]["IPAddress"]
port = os.environ.get("MANAGEMENT_PORT", "18443")
base = "https://localhost:" + port
context = ssl.create_default_context(cafile=os.environ["SMOKE_CA"])
authorization = "Basic " + base64.b64encode(("admin:" + os.environ["SMOKE_PASSWORD"]).encode()).decode()


def request(path="/api/rules", method="GET", data=None, auth=True, origin=None, csrf=True):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = authorization
    if csrf:
        headers["X-Gokaskad"] = "1"
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(base + path, method=method, headers=headers,
                                 data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(req, context=context, timeout=140) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


assert request(auth=False)[0] == 401
assert request('/api/firewall', auth=False)[0] == 401
plain = http.client.HTTPConnection("localhost", int(port), timeout=10)
plain.request("GET", "/?from=http")
redirect = plain.getresponse()
assert redirect.status == 308
assert redirect.getheader("Location") == base + "/?from=http"
assert redirect.getheader("WWW-Authenticate") is None
redirect.read()
plain.close()
assert request("/")[0] == 200
assert request("/api/health")[0] == 200
for name in ("api", "gui", "worker"):
    container = json.loads(docker("inspect", f"{project}-{name}-1"))[0]
    assert not container["HostConfig"]["PortBindings"], f"Unexpected published port on {name}"
print("PASS: HTTP-to-HTTPS redirect preserves port/path, HTTPS, authentication, GUI, private service ports")

# Both common fail2ban implementations, inside the isolated worker namespace.
native_fixture = '''
table inet f2b-gokaskad-smoke {
 set addr-set-sshd {
  type ipv4_addr
  flags interval
  elements = { 203.0.113.18, 198.51.100.0/24 }
 }
 chain f2b-chain {
  type filter hook input priority -1; policy accept;
  ip saddr @addr-set-sshd tcp dport 22 drop
 }
}
'''
try:
    docker("exec", worker, "iptables-nft", "-N", "f2b-gokaskad-smoke")
    docker("exec", worker, "iptables-nft", "-A", "INPUT", "-p", "tcp", "--dport", "2222", "-j", "f2b-gokaskad-smoke")
    docker("exec", worker, "iptables-nft", "-A", "f2b-gokaskad-smoke", "-s", "203.0.113.19", "-j", "REJECT")
    subprocess.run(["docker", "exec", "-i", worker, "nft", "-f", "-"], input=native_fixture, text=True, check=True)
    before = '\n'.join(line for line in docker("exec", worker, "iptables-nft-save").splitlines() if not line.startswith('#'))
    code, body = request('/api/firewall')
    assert code == 200, body
    view = json.loads(body)
    classic = next(c for c in view['chains'] if c['name'] == 'f2b-gokaskad-smoke')
    assert classic['fail2ban'] and classic['rules'][0]['source'] == '203.0.113.19/32'
    native = next(c for c in view['chains'] if c['table'] == 'f2b-gokaskad-smoke' and c['name'] == 'f2b-chain')
    assert native['backend'] == 'nftables' and native['fail2ban']
    assert '203.0.113.18' in native['rules'][0]['source_members']
    assert '198.51.100.0/24' in native['rules'][0]['source_members']
    assert native['rules'][0]['destination_port'] == '22'
    assert native['rules'][0]['action'] == 'DROP'
    assert request('/api/firewall', 'DELETE')[0] == 404
    after = '\n'.join(line for line in docker("exec", worker, "iptables-nft-save").splitlines() if not line.startswith('#'))
    assert before == after, 'Read-only firewall endpoint modified rules'
    print('PASS: live iptables and native nftables fail2ban chains, blocked-address sets, read-only endpoint')
finally:
    for args in (("iptables-nft", "-D", "INPUT", "-p", "tcp", "--dport", "2222", "-j", "f2b-gokaskad-smoke"),
                 ("iptables-nft", "-F", "f2b-gokaskad-smoke"), ("iptables-nft", "-X", "f2b-gokaskad-smoke"),
                 ("nft", "delete", "table", "inet", "f2b-gokaskad-smoke")):
        subprocess.run(['docker', 'exec', worker, *args], capture_output=True, check=False)

target_name = "gokaskad-smoke-target-" + uuid.uuid4().hex[:8]
target_code = '''
import http.server,socket,threading
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'gokaskad-target')
threading.Thread(target=http.server.HTTPServer(('0.0.0.0',9090),Handler).serve_forever,daemon=True).start()
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('0.0.0.0',9090))
while True:
 data,peer=s.recvfrom(1024);s.sendto(b'gokaskad-'+data,peer)
'''
ids = []
try:
    docker("run", "-d", "--name", target_name, "--network", "bridge", "python:3.13-slim-bookworm", "python3", "-u", "-c", target_code)
    target_ip = json.loads(docker("inspect", target_name))[0]["NetworkSettings"]["IPAddress"]
    docker("exec", worker, "iptables", "-N", "GK_SMOKE_SENTINEL")
    for proto, input_port in (("tcp", 18080), ("udp", 18081)):
        ident = str(uuid.uuid4())
        ids.append(ident)
        rule = {"name": "Smoke " + proto, "protocol": proto, "input_port": input_port,
                "target_ip": target_ip, "target_port": 9090, "enabled": True}
        assert request("/api/rules/" + ident, "PUT", rule)[0] == 200
        assert request("/api/rules/" + ident, "DELETE", origin="https://attacker.invalid")[0] == 403
        assert request("/api/rules/" + ident, "DELETE", csrf=False)[0] == 403
    tcp = f"import http.client;c=http.client.HTTPConnection('{worker_ip}',18080,timeout=5);c.request('GET','/');assert c.getresponse().read()==b'gokaskad-target'"
    udp = f"import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(5);s.sendto(b'probe',('{worker_ip}',18081));assert s.recv(1024)==b'gokaskad-probe'"
    for code in (tcp, udp):
        docker("run", "--rm", "--network", "bridge", "python:3.13-slim-bookworm", "python3", "-c", code)
    print("PASS: real TCP and UDP forwarding through isolated worker to separate target")
    docker("exec", worker, "iptables", "-S", "GK_SMOKE_SENTINEL")
    docker("restart", worker)
    for attempt in range(30):
        if request("/api/health")[0] == 200:
            break
        time.sleep(1)
    else:
        raise AssertionError("Worker did not recover")
    stored = json.loads(request()[1])["rules"]
    assert set(ids) <= {r["id"] for r in stored}
    docker("run", "--rm", "--network", "bridge", "python:3.13-slim-bookworm", "python3", "-c", tcp)
    for item in stored:
        if item["id"] in ids:
            assert request("/api/rules/" + item["id"], "PUT", dict(item, enabled=False))[0] == 200
    disabled = f"import socket;s=socket.socket();s.settimeout(2);assert s.connect_ex(('{worker_ip}',18080))!=0"
    docker("run", "--rm", "--network", "bridge", "python:3.13-slim-bookworm", "python3", "-c", disabled)
    print("PASS: restart restores database/firewall, disable blocks new connections, unrelated chain preserved, CSRF rejected")
finally:
    for ident in ids:
        assert request("/api/rules/" + ident, "DELETE")[0] == 200
    subprocess.run(["docker", "exec", worker, "iptables", "-X", "GK_SMOKE_SENTINEL"], check=False, capture_output=True)
    subprocess.run(["docker", "rm", "-f", target_name], check=False, capture_output=True)
