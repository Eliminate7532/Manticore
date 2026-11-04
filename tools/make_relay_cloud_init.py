# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/make_relay_cloud_init.py - Round RLY1: writes relay/cloud_init.yaml, the "initialization script" that turns a fresh Ubuntu
server (Oracle Always Free, or any cloud that takes cloud-init) into Manticore's relay, with nothing typed on the server.

    python tools/make_relay_cloud_init.py [--port 36900] [--check]

The relay itself (relay/manticore_relay.py, standard library only) goes into the file whole (base64), so the server needs no
download and no git. The file installs it as a systemd service that starts at boot and restarts if it stops, opens the port in
the server's own firewall (Oracle's Ubuntu images refuse everything but SSH in iptables; Oracle Linux uses firewalld), and
rotates the relay's log weekly.

The cloud's OWN firewall still needs the port opened once: on Oracle, the instance's subnet > security list > an ingress rule
for TCP 36900 from 0.0.0.0/0 (relay/README.txt, "Oracle Always Free").

--check: exit 1 if relay/cloud_init.yaml doesn't hold the current relay/manticore_relay.py (tests/test_rly1.py runs it).
"""
import argparse
import base64
import hashlib
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY = os.path.join(BASE_DIR, "relay", "manticore_relay.py")
OUT = os.path.join(BASE_DIR, "relay", "cloud_init.yaml")

TEMPLATE = """#cloud-config
# relay/cloud_init.yaml - written by tools/make_relay_cloud_init.py (Round RLY1). Do not edit by hand: change the tool or the relay
# and run the tool again. Paste this whole file as the server's "initialization script" (Oracle: Create instance > Show advanced
# options > Management > Paste cloud-init script). It installs Manticore's relay (relay/manticore_relay.py, sha256 {sha}) as a
# service on TCP port {port} and opens that port in the server's own firewall. The cloud's firewall (Oracle: the subnet's security
# list) must allow TCP {port} too - see relay/README.txt.
write_files:
  - path: /opt/manticore-relay/manticore_relay.py
    permissions: '0644'
    encoding: b64
    content: {b64}
  - path: /etc/systemd/system/manticore-relay.service
    permissions: '0644'
    content: |
      [Unit]
      Description=Manticore relay (meeting point for online games)
      After=network-online.target
      Wants=network-online.target

      [Service]
      ExecStart=/usr/bin/python3 /opt/manticore-relay/manticore_relay.py --bind 0.0.0.0 --port {port} --max-rooms 200 --log /var/log/manticore-relay/relay.log
      Restart=always
      RestartSec=3
      DynamicUser=yes
      LogsDirectory=manticore-relay
      NoNewPrivileges=yes
      ProtectSystem=strict
      ProtectHome=yes
      PrivateTmp=yes

      [Install]
      WantedBy=multi-user.target
  - path: /etc/logrotate.d/manticore-relay
    permissions: '0644'
    content: |
      /var/log/manticore-relay/relay.log {{
        weekly
        rotate 8
        compress
        missingok
        notifempty
        copytruncate
      }}
runcmd:
  - [sh, -c, "if command -v firewall-cmd >/dev/null 2>&1; then firewall-cmd --permanent --add-port={port}/tcp && firewall-cmd --reload; fi"]
  - [sh, -c, "if command -v iptables >/dev/null 2>&1; then iptables -C INPUT -p tcp --dport {port} -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport {port} -j ACCEPT; fi"]
  - [sh, -c, "if command -v netfilter-persistent >/dev/null 2>&1; then netfilter-persistent save; fi"]
  - [systemctl, daemon-reload]
  - [systemctl, enable, --now, manticore-relay]
"""


def build(port=36900, relay_path=RELAY):
    with open(relay_path, "rb") as f:
        data = f.read()
    return TEMPLATE.format(sha=hashlib.sha256(data).hexdigest(), port=int(port), b64=base64.b64encode(data).decode("ascii"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write relay/cloud_init.yaml (round RLY1).")
    ap.add_argument("--port", type=int, default=36900)
    ap.add_argument("--check", action="store_true", help="exit 1 if relay/cloud_init.yaml is out of date")
    args = ap.parse_args(argv)
    text = build(args.port)
    if args.check:
        try:
            with open(OUT, encoding="utf-8") as f:
                current = f.read()
        except OSError:
            current = None
        if current != text:
            print("relay/cloud_init.yaml is out of date: run  python tools/make_relay_cloud_init.py")
            return 1
        print("relay/cloud_init.yaml holds the current relay.")
        return 0
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("wrote %s (%d bytes)" % (OUT, len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
