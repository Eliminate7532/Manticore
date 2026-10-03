MANTICORE RELAY (Round MP2d)
============================

What it is for
  Playing online normally needs the host's PC to accept an incoming connection (an open router port, or Tailscale). Some
  internet providers make that impossible (CGNAT). A relay is a small program on a server that BOTH players can reach: the
  host's game connects out to it and opens a "room", the friend's game connects out to it too, and the relay joins the two
  connections. The game's own encryption (TLS 1.3) runs straight through it, from the friend's game to the host's engine, and
  the friend's game checks the host's certificate fingerprint from the invite code - so the relay sees only encrypted bytes,
  their sizes and both internet addresses. It never sees the game password, the cards or the moves.

  Nothing in Manticore needs a relay. It is only for the case above. Karl's decision (2026-10-02) is that work stays on his
  own PC, so no relay is deployed anywhere; this folder is ready for whoever wants to run one.

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
  (tests/test_mp2d.py, including a real game through it and a dropped connection coming back through it).
