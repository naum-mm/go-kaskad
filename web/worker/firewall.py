"""JSON CLI and dedicated iptables chains. Never flush host/Docker chains."""
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

STATE = Path(os.environ.get("STATE_DIR", "/var/lib/gokaskad"))
MARK = "0x4b530000/0xffff0000"
CHAINS = {"nat": ("GK_DNAT", "GK_SNAT"), "filter": ("GK_FORWARD",)}
BACKEND = os.environ.get("IPTABLES_BACKEND", "nft")
if BACKEND not in ("nft", "legacy"):
    raise ValueError("IPTABLES_BACKEND must be nft or legacy")
IPTABLES = "iptables-" + BACKEND


class Invalid(ValueError):
    pass


def validate(rule):
    if not isinstance(rule, dict) or set(rule) - {"id", "name", "protocol", "input_port", "target_ip", "target_port", "enabled"}:
        raise Invalid("Unknown rule fields")
    try:
        if not isinstance(rule["id"], str) or not isinstance(rule["target_ip"], str):
            raise Invalid("ID and IPv4 destination must be strings")
        ident = str(uuid.UUID(rule["id"]))
        address = ipaddress.IPv4Address(rule["target_ip"])
        if address.is_unspecified or address.is_multicast or address.is_loopback or str(address) == "255.255.255.255":
            raise Invalid("Use a reachable unicast IPv4 destination")
        ports = (rule["input_port"], rule["target_port"])
        if any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
            raise Invalid("Ports must be integers between 1 and 65535")
        if rule["input_port"] == int(os.environ["MANAGEMENT_PORT"]):
            raise Invalid("The management port is reserved")
        if rule["protocol"] not in ("tcp", "udp") or type(rule.get("enabled", True)) is not bool:
            raise Invalid("Invalid protocol or enabled value")
        name = rule.get("name", "")
        if not isinstance(name, str) or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise Invalid("Name must contain at most 100 printable characters")
        return {"id": ident, "name": name, "protocol": rule["protocol"], "input_port": ports[0],
                "target_ip": str(address), "target_port": ports[1], "enabled": rule.get("enabled", True)}
    except (KeyError, TypeError, ValueError) as exc:
        raise Invalid(str(exc)) from exc


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=20, **kwargs)


def render(rules):
    # Mark connections before DNAT so only our own traffic is accepted/MASQUERADEd.
    lines = ["*nat", "-F GK_DNAT", "-F GK_SNAT"]
    for r in rules:
        if not r["enabled"]:
            continue
        match = f'-p {r["protocol"]} -m addrtype --dst-type LOCAL --dport {r["input_port"]}'
        lines += [f"-A GK_DNAT {match} -j CONNMARK --set-xmark {MARK}",
                  f'-A GK_DNAT {match} -j DNAT --to-destination {r["target_ip"]}:{r["target_port"]}']
    lines += [f"-A GK_SNAT -m connmark --mark {MARK} -j MASQUERADE", "COMMIT", "*filter", "-F GK_FORWARD",
              f"-A GK_FORWARD -m connmark --mark {MARK} -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT", "COMMIT", ""]
    return "\n".join(lines)


def apply(rules):
    for table, chains in CHAINS.items():
        for chain in chains:
            result = subprocess.run([IPTABLES, "-w", "5", "-t", table, "-S", chain], capture_output=True)
            if result.returncode:
                run([IPTABLES, "-w", "5", "-t", table, "-N", chain])
    run([IPTABLES + "-restore", "--wait", "5", "--noflush"], input=render(rules))
    for table, parent, chain in (("nat", "PREROUTING", "GK_DNAT"), ("nat", "POSTROUTING", "GK_SNAT"),
                                  ("filter", "FORWARD", "GK_FORWARD")):
        prefix = [IPTABLES, "-w", "5", "-t", table]
        if subprocess.run(prefix + ["-C", parent, "-j", chain], capture_output=True).returncode:
            run(prefix + ["-I", parent, "1", "-j", chain])


def connect():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    db = sqlite3.connect(STATE / "rules.sqlite3", timeout=30)
    (STATE / "rules.sqlite3").chmod(0o600)
    db.execute("CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    return db


def read_rules(db):
    return [json.loads(row[0]) for row in db.execute("SELECT data FROM rules ORDER BY id")]


def execute(command, payload=None):
    with connect() as db:
        # Also serialize independent CLI calls, not only the worker's RPC loop.
        db.execute("BEGIN IMMEDIATE")
        old = read_rules(db)
        if command == "list":
            return {"rules": old}
        if command == "reconcile":
            apply(old)
            return {"rules": old}
        if command == "put":
            rule = validate(payload)
            new = [r for r in old if r["id"] != rule["id"]] + [rule]
            bindings = [(r["protocol"], r["input_port"]) for r in new if r["enabled"]]
            if len(bindings) != len(set(bindings)):
                raise Invalid("An enabled rule already uses this protocol and input port")
        elif command == "delete":
            ident = str(uuid.UUID(payload["id"]))
            new = [r for r in old if r["id"] != ident]
        else:
            raise Invalid("Unknown command")
        try:
            apply(new)
            db.execute("DELETE FROM rules")
            db.executemany("INSERT INTO rules VALUES (?, ?)", [(r["id"], json.dumps(r)) for r in new])
            db.commit()
        except Exception:
            # iptables-restore commits tables separately: recover the previous configuration.
            apply(old)
            raise
        return {"rules": sorted(new, key=lambda r: r["id"])}


if __name__ == "__main__":
    try:
        command = sys.argv[1]
        payload = json.load(sys.stdin) if command in ("put", "delete") else None
        print(json.dumps(execute(command, payload)))
    except (Invalid, ValueError, KeyError, IndexError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(2)
    except Exception as exc:
        print(json.dumps({"error": "Firewall operation failed; inspect worker logs"}))
        print(str(exc), file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        sys.exit(1)
