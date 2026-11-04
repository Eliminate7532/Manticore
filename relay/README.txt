MANTICORE RELAY (Round MP2d)
============================

What it is for
  Playing online normally needs the host's PC to accept an incoming connection (an open router port, or Tailscale). Some
  internet providers make that impossible (CGNAT). A relay is a small program on a server that BOTH players can reach: the
  host's game connects out to it and opens a "room", the friend's game connects out to it too, and the relay joins the two
  connections. The game's own encryption (TLS 1.3) runs straight through it, from the friend's game to the host's engine, and
  the friend's game checks the host's certificate fingerprint from the invite code - so the relay sees only encrypted bytes,
  their sizes and both internet addresses. It never sees the game password, the cards or the moves.

  Nothing in Manticore needs a relay. It is only for the case above. Karl's decision of 2026-10-02 was that work stays on his
  own PC; on 2026-11-02 (multiplayer week) he chose to try a relay on Oracle Cloud's Always Free tier - see "Oracle Always
  Free" below. Until that server exists, no relay is deployed anywhere.

Where it can run
  Any machine both players can reach on one TCP port: a small rented server, or a friend's PC whose router forwards the port.
  It cannot run behind the same CGNAT it is meant to get around.

Running it
  Python 3.9 or newer, nothing else to install (standard library only):
      python3 manticore_relay.py --port 36900 --log relay.log
  Open that TCP port in the server's firewall. Options: --bind (default 0.0.0.0), --max-rooms (200).
  As a service on Linux (systemd), e.g. /etc/systemd/system/manticore-relay.service:
      [Unit]
      Description=Manticore relay
      After=network-online.target
      [Service]
      ExecStart=/usr/bin/python3 /opt/manticore-relay/manticore_relay.py --port 36900 --log /var/log/manticore-relay.log
      Restart=always
      User=nobody
      [Install]
      WantedBy=multi-user.target
  then:  systemctl enable --now manticore-relay

Oracle Always Free (round RLY1, 2026-11-02)
  relay/cloud_init.yaml (written by tools/make_relay_cloud_init.py; never edit it by hand) turns a fresh Ubuntu server into the
  relay with nothing typed on the server: it holds the relay itself, installs it as the systemd service manticore-relay on TCP
  36900 (DynamicUser, restarts by itself, starts at boot), opens the port in the server's own firewall, and rotates its log.
  1. Sign up at cloud.oracle.com (needs a card for identity; Always Free resources are not charged).
  2. Compute > Instances > Create instance. Image: Canonical Ubuntu (22.04 or 24.04). Shape: VM.Standard.E2.1.Micro (Always
     Free, AMD) or VM.Standard.A1.Flex with 1 OCPU / 6 GB (Always Free, Arm) - the relay needs almost nothing.
     Networking: keep "Assign a public IPv4 address" on.
     Show advanced options > Management > "Paste cloud-init script": paste the whole of relay/cloud_init.yaml. Create.
  3. Open the port in Oracle's own firewall: the instance's page > the subnet > its Security List > Add Ingress Rules:
     source 0.0.0.0/0, IP protocol TCP, destination port 36900.
  4. Check from any PC with Python:  python tools/online_soak.py --relay <public IP>:36900 --games 1   (Forge needed), or
     host in the game with "Connect through a relay" and the address <public IP>:36900.
  Oracle's own rules (their documentation, read 2026-11-02): an Always Free instance whose CPU, network and memory all stay
  under 20% for 7 days may be reclaimed as idle - a relay is idle most of the time, so check it is still there before a game
  night; an account unused for 30+ days may be suspended. "Out of host capacity" when creating an A1 instance means try
  another availability domain or the AMD micro shape.

Several games at once
  tools/relay_load.py runs N online soak games through ONE relay at the same time and checks, from each game's own times and
  from the relay's log, that they really overlapped:
      python tools/relay_load.py --relay <address>:36900 --tables 3 --games 2      (or --start-relay to test locally)
  Measured 2026-11-02 on 127.0.0.1: 3 tables x 2 games, 6 of 6 to game over through the relay, 3 games at once.

Using it from the game
  Host: deck screen > Host online > tick "Connect through a relay" > type the relay's address:port > Host. The Host screen says
  "Connected to the relay at ..." and shows an invite code as usual (it carries the relay's address and the room, not the
  host's address). Friends join with that code exactly as before. Reconnecting after a drop works through the relay too.

Limits built in
  200 rooms; 8 friends waiting to be let in per room; 32 connections per internet address; a friend waits at most 15 s for the
  host's game to answer; a joined pair that sends nothing for 10 minutes is closed (a game sends a heartbeat every 5 s).

What the relay keeps
  Nothing on disk except its log: rooms opened and closed, and a count of pairs. No game data passes through it in readable form.

Not tested yet
  On a real server on the internet, and from Windows through one. The tests run the relay on 127.0.0.1
  (tests/test_mp2d.py, including a real game through it and a dropped connection coming back through it; tests/test_rly1.py,
  two games at once and the guest bot joining through it).
