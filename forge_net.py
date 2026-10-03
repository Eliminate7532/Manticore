# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_net.py - Round MP1: the parts of online play that need no window: game passwords, invite codes, the host's
certificate, the encrypted connection, and the plain-words messages for everything that can go wrong.

How a 1v1 online game works (claude/SONNET_SPEC_MP1_2026-09-25.md, Karl's choices of 2026-09-25):
  * The host's PC runs the one Forge engine (java_bridge NetHost) with two human seats. The host's table talks to it as in an
    ordinary game; the friend's table connects to it directly over the internet.
  * Every connection is TLS 1.3 with the host's own self-signed certificate. There's no certificate authority: the invite
    code carries the certificate's SHA-256 fingerprint, and the friend's table checks it BEFORE sending anything, so the
    password only ever goes to the PC that made the invite code.
  * The invite code is one string: "MANT1-" + base64url(JSON {"a": address, "p": port, "f": fingerprint, "w": password}),
    without padding. It contains the password, so it goes only to the friend.
  * Nothing here talks to a third-party "what is my IP" site: the host's internet address comes from the router (UPnP), or
    the host types it.
Round MP2 (protocol 2): the host's welcome carries a rejoin token, so a guest whose connection drops can come back
(rejoin_line) within the host's grace period; heartbeats ({"t":"hb"} / {"c":"hb"}) notice a dead link; and up to four
people can watch a game (watch_line) with every hidden card face down. The token is a secret like the password: it is never
written to a log, a recording or a report.
"""
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess

DEFAULT_PORT = 36800                 # Forge's own network play uses 36743; stay clear of it
DEFAULT_RELAY_PORT = 36900           # round MP2d: relay/manticore_relay.py's default
INVITE_PREFIX = "MANT1-"
PROTOCOL_V = 2                       # round MP2: rejoin, watch, heartbeats, leave (NetHost.PROTOCOL_V)
DECK_MAX_BYTES = 200 * 1024          # the host refuses bigger decks (NetHost.DECK_MAX)
REPLY_MAX_BYTES = 64 * 1024          # the host's answer to the hello is one short line
CONNECT_SECONDS = 10
WELCOME_SECONDS = 15
NAME_MAX = 24
SILENCE_SECONDS = 30                 # round MP2: the host sends a heartbeat every 5 s; this long without a line = a dead link
RECONNECT_EXTRA = 5                  # round MP2: keep trying a little past the host's grace period (clocks, a slow handshake)
RECONNECT_WAITS = (1, 2, 3, 5)       # round MP2: seconds between reconnect attempts (the last one repeats)

# Short, common, easy to say over voice chat and hard to mishear. 3 of these + 2 digits = 200**3 * 100 = 800 million passwords;
# with 5 wrong tries a minute allowed per address, guessing is not a way in.
WORDS = (
    "acorn amber anchor apple arrow aspen autumn badger bamboo banjo barley basil beacon beaver berry birch bison blossom "
    "bramble breeze brook bubble cabin cactus camel candle canyon carrot castle cedar cello cherry chess cider cinder clover "
    "cobalt comet copper coral cotton cougar crane crater cricket crystal daisy dawn delta desert dingo dolphin dragon drum "
    "eagle ember falcon fern fiddle fig finch flint forest fossil fox frost garden garnet gecko ginger glacier glade goose "
    "granite grape gravel harbor hazel heron hickory honey hornet island ivory jade jasmine jelly juniper kayak kettle kiwi "
    "koala lagoon lantern lark lava lemon lilac lily lime linen lizard llama lobster lotus lynx mango maple marble marsh "
    "meadow melon meteor mint mist moose moss moth mountain nectar nickel nutmeg oak oasis ocean olive onyx orchid otter owl "
    "panda papaya parrot peach pebble pecan pepper pine plum pond poppy prairie puffin quail quartz quill rabbit radish "
    "raven reef river robin rocket rose ruby saffron sage salmon sandal satin scarab shell sierra silver sparrow spruce "
    "squid star stone storm sugar summit sunset swan thistle thunder tiger timber topaz tulip tundra turtle valley velvet "
    "violet walnut walrus willow wolf wren yarrow zebra zephyr"
).split()

JOIN_MESSAGES = {
    "refused_connection": "Nobody is hosting at that address and port. Check both, and that your friend pressed Host.",
    "no_answer": "No answer from that address. Your friend's router port may not be open: they should check the status "
                 "line on their Host screen.",
    "fingerprint": "This isn't your friend's PC, or their game was reinstalled. Ask for a new invite code.",
    "password": "Wrong password.",
    "full": "That game already has two players.",
    "deck": "The host could not use your deck: {text}",
    "version": "Your friend's copy of the game is too different from yours to play together ({text}). Update both.",
    "tls": "The encrypted connection could not be set up ({text}).",
    "closed": "Your friend's game closed the connection before the game started.",
    "rejoin": "Your friend's game doesn't know this seat any more.",
    "gone": "That game has ended.",
    "not_started": "{text}",
    "watch_full": "{text}",
    "lost": "The connection to your friend's game was lost and could not be made again in time.",
    # round MP2d: through a relay
    "relay_no_room": "Your friend's game isn't at the relay (it may have closed, or the invite code is old).",
    "relay_no_answer": "The relay found your friend's game, but their game didn't answer. Try again in a moment.",
    "relay_busy": "Too many people are joining your friend's game at once. Try again in a moment.",
    "relay_version": "The relay is a different version from this game ({text}).",
    "relay_refused": "The relay turned the connection away ({text}).",
    "unknown": "Could not join: {text}",
}

UPNP_LINES = {
    "waiting": ("Asking your router to open port {port}...", "dim"),
    "ok": ("Router port opened automatically.", "green"),
    "refused": ("Your router refused to open port {port}: open it by hand (README: Playing a friend online, part B).",
                "orange"),
    "no_router": ("Open port {port} on your router (README: Playing a friend online, part B). No router answered the "
                  "automatic request.", "orange"),
    "cgnat": ("Your internet provider blocks incoming connections (CGNAT), so a direct connection can't work from here. "
              "Use Tailscale (README: part C).", "red"),
    "no_network": ("This PC has no network connection.", "red"),
    "error": ("Couldn't ask the router to open the port: open it by hand (README: part B).", "orange"),
    "off": ("Automatic port opening is off: make sure port {port} is open on your router (README: part B).", "dim"),
}


NOT_REACHABLE_V4 = ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
                    "192.168.0.0/16")


class InviteError(ValueError):
    """An invite code that can't be read; the message says why in plain words."""


class NetRefused(Exception):
    """The host turned us away (or never answered). `reason` is a JOIN_MESSAGES key; str() is the plain-words message."""

    def __init__(self, reason, text=""):
        self.reason, self.text = reason, text or ""
        super().__init__(join_message(reason, text))


def join_message(reason, text=""):
    template = JOIN_MESSAGES.get(reason) or JOIN_MESSAGES["unknown"]
    return template.format(text=(text or reason or "").strip()[:300])


def upnp_line(result, port):
    """(text, colour name) for the Host screen's status line. result = the bridge's {"t":"upnp"} message, "waiting" or "off"."""
    if result in ("waiting", "off"):
        key = result
    elif result.get("ok"):
        key = "ok"
    else:
        key = result.get("reason") if result.get("reason") in UPNP_LINES else "error"
    text, colour = UPNP_LINES[key]
    return text.format(port=port), colour


# ---- passwords, names, addresses -----------------------------------------------------------------------------------
def make_password(rng=None):
    """Three words and two digits, e.g. 'otter-lamp-river-42'. Uses the system's secure random source."""
    rng = rng or secrets.SystemRandom()
    return "-".join([rng.choice(WORDS) for _ in range(3)] + ["%02d" % rng.randrange(100)])


def clean_name(text):
    """A player name as the host will show it: no control characters, at most NAME_MAX characters."""
    s = "".join(" " if ch.isspace() else ch for ch in (text or ""))
    s = "".join(ch for ch in s if ch.isprintable() and ch not in "<>")
    s = " ".join(s.split())[:NAME_MAX].strip()
    return s


_HOST_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def validate_address(text):
    """None when `text` is something to connect to (an IPv4/IPv6 address or a host name such as my-pc.tailnet.ts.net),
    else a short message saying what's wrong."""
    s = (text or "").strip()
    if not s:
        return "Type your friend's address."
    if any(ch.isspace() for ch in s):
        return "An address has no spaces in it."
    if "://" in s or "/" in s:
        return "Just the address, without http:// or slashes."
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    try:
        ipaddress.ip_address(s)
        return None
    except ValueError:
        pass
    if re.fullmatch(r"[0-9.]+", s):
        return "That isn't a complete address (four numbers, like 203.0.113.7)."
    labels = s.rstrip(".").split(".")
    if len(s) > 253 or not all(_HOST_LABEL.match(lb) for lb in labels):
        return "That doesn't look like an address."
    return None


def validate_port(text):
    """(port, None) or (None, message)."""
    try:
        p = int(str(text).strip())
    except (TypeError, ValueError):
        return None, "The port is a number, like %d." % DEFAULT_PORT
    if not 1024 <= p <= 65535:
        return None, "Use a port between 1024 and 65535."
    return p, None


def behind_another_nat(ip):
    """True for an address nobody on the internet can connect to: carrier-grade NAT (100.64.0.0/10), the private ranges,
    link-local, loopback. A router reporting one of these as its own internet address sits behind another NAT (CGNAT)."""
    try:
        a = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    if a.version != 4:
        return a.is_loopback or a.is_link_local or a.is_unspecified or a in ipaddress.ip_network("fc00::/7")
    # Named ranges only, the same as Upnp.java: ipaddress's is_private also calls the documentation ranges (203.0.113.0/24 ...)
    # private, and those stand in for real addresses in the tests and the README.
    return any(a in ipaddress.ip_network(n) for n in NOT_REACHABLE_V4)


def tailscale_address():
    """This PC's Tailscale address (100.x.y.z) when the Tailscale program is installed and signed in, else None. Only used
    to fill in the address for the CGNAT fallback (README part C); nothing is sent anywhere."""
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        guess = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe")
        exe = guess if os.path.isfile(guess) else None
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=3,
                             creationflags=0x08000000 if os.name == "nt" else 0).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.split():
        if line.startswith("100."):
            return line.strip()
    return None


# ---- invite codes ----------------------------------------------------------------------------------------------------
def make_invite(address, port, fingerprint, password, room=None):
    """Round MP2d: with `room`, address and port are the relay's (relay/manticore_relay.py) and room is the host's room there."""
    data = {"a": address, "p": int(port), "f": fingerprint, "w": password}
    if room:
        data["m"] = room
    body = json.dumps(data, separators=(",", ":"))
    return INVITE_PREFIX + base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")


def read_invite(code):
    """{"address", "port", "fingerprint", "password"} from an invite code, or InviteError saying what's wrong with it."""
    s = "".join((code or "").split())                     # chat programs break long lines; spaces and newlines don't matter
    if not s:
        raise InviteError("Paste the invite code your friend sent you.")
    i = s.find(INVITE_PREFIX)
    if i < 0:
        raise InviteError("That isn't an invite code. It starts with %s" % INVITE_PREFIX)
    body = s[i + len(INVITE_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise InviteError("The invite code is damaged or cut short. Copy it again, all of it.") from None
    if not isinstance(data, dict):
        raise InviteError("The invite code is damaged. Copy it again, all of it.")
    address, port, fp, pw = data.get("a"), data.get("p"), data.get("f"), data.get("w")
    if not isinstance(address, str) or validate_address(address):
        raise InviteError("The invite code has no usable address. Ask your friend to type their address on the Host screen.")
    port, problem = validate_port(port)
    if problem:
        raise InviteError("The invite code's port is wrong: " + problem)
    if not isinstance(fp, str) or not re.fullmatch(r"[0-9a-f]{64}", fp):
        raise InviteError("The invite code is damaged (its fingerprint is wrong). Copy it again, all of it.")
    if not isinstance(pw, str) or not pw:
        raise InviteError("The invite code has no password in it.")
    room = data.get("m")
    if room is not None and (not isinstance(room, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,40}", room)):
        raise InviteError("The invite code is damaged (its relay room is wrong). Copy it again, all of it.")
    inv = {"address": address.strip(), "port": port, "fingerprint": fp, "password": pw}
    if room:
        inv["room"] = room                                  # round MP2d: through a relay
    return inv


# ---- the host's certificate --------------------------------------------------------------------------------------------
class HostCert:
    def __init__(self, path, store_pass, fingerprint):
        self.path, self.store_pass, self.fingerprint = path, store_pass, fingerprint

    def __repr__(self):                                    # never print the keystore password
        return "HostCert(%r, fingerprint=%s...)" % (self.path, self.fingerprint[:12])


class CertError(Exception):
    pass


def fingerprint(der):
    return hashlib.sha256(der).hexdigest()


def keytool_for(java):
    """keytool next to this java (it ships with the java.base module, so a jlinked runtime has it too)."""
    if not java:
        return None
    exe = os.path.join(os.path.dirname(java), "keytool.exe" if os.name == "nt" else "keytool")
    return exe if os.path.isfile(exe) else shutil.which("keytool")


def ensure_host_cert(folder, java):
    """The host's certificate: made once per install (an EC P-256 key, 10 years) and kept in <folder>/net/, so the fingerprint
    in an old invite code still identifies this PC. Its keystore password is random and kept beside it: it protects nothing
    a person types, it only satisfies the PKCS12 format. Returns a HostCert; raises CertError with a plain message."""
    net = os.path.join(folder, "net")
    p12, pass_file, der_file = (os.path.join(net, n) for n in ("host.p12", "host_pass.txt", "host_cert.der"))
    if os.path.isfile(p12) and os.path.isfile(pass_file) and os.path.isfile(der_file):
        try:
            with open(pass_file, "r", encoding="utf-8") as f:
                store_pass = f.read().strip()
            with open(der_file, "rb") as f:
                der = f.read()
            if store_pass and der:
                return HostCert(p12, store_pass, fingerprint(der))
        except OSError:
            pass
    keytool = keytool_for(java)
    if not keytool:
        raise CertError("keytool was not found next to Java, so the encrypted connection can't be set up.")
    os.makedirs(net, exist_ok=True)
    for p in (p12, der_file):
        try:
            os.remove(p)
        except OSError:
            pass
    store_pass = secrets.token_urlsafe(18)
    flags = 0x08000000 if os.name == "nt" else 0
    env = dict(os.environ)
    env.pop("JAVA_TOOL_OPTIONS", None)
    base = [keytool, "-noprompt", "-alias", "manticore", "-keystore", p12, "-storetype", "PKCS12", "-storepass", store_pass]
    attempts = (["-keyalg", "EC", "-groupname", "secp256r1", "-sigalg", "SHA256withECDSA"],
                ["-keyalg", "RSA", "-keysize", "3072", "-sigalg", "SHA256withRSA"])    # if a trimmed Java lacks EC
    err = ""
    for alg in attempts:
        r = subprocess.run(base + ["-genkeypair", "-keypass", store_pass, "-validity", "3650", "-dname", "CN=Manticore host"]
                           + alg, capture_output=True, text=True, timeout=60, creationflags=flags, env=env)
        if r.returncode == 0:
            break
        err = (r.stderr or r.stdout or "").strip()[-300:]
        try:
            os.remove(p12)
        except OSError:
            pass
    else:
        raise CertError("Could not make the host's certificate: " + err)
    r = subprocess.run(base + ["-exportcert", "-file", der_file], capture_output=True, text=True, timeout=60,
                       creationflags=flags, env=env)
    if r.returncode != 0:
        raise CertError("Could not read the host's certificate: " + (r.stderr or r.stdout or "").strip()[-300:])
    with open(pass_file, "w", encoding="utf-8") as f:
        f.write(store_pass)
    with open(der_file, "rb") as f:
        der = f.read()
    return HostCert(p12, store_pass, fingerprint(der))


# ---- the guest's side of the connection --------------------------------------------------------------------------------
def client_context():
    """TLS 1.3 only, no certificate authority: the certificate is checked by its fingerprint instead (connect_tls)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    return ctx


def relay_join(raw, room, timeout=CONNECT_SECONDS):
    """Round MP2d: ask the relay for the host's room on a plain TCP socket; when it answers "paired", the socket is a pipe to the
    host's engine and TLS starts on it as on a direct connection. NetRefused("relay_...") otherwise."""
    import relay_agent
    raw.settimeout(timeout)
    try:
        relay_agent.send_line(raw, {"c": "join", "v": relay_agent.RELAY_V, "room": room})
        reply = relay_agent.read_line(raw)
    except socket.timeout:
        raise NetRefused("no_answer") from None
    except OSError as e:
        raise NetRefused("unknown", str(e)) from None
    if reply is None:
        raise NetRefused("closed")
    if reply.get("t") == "refused":
        reason = str(reply.get("reason") or "")
        key = "relay_" + reason
        raise NetRefused(key if key in JOIN_MESSAGES else "relay_refused", reason)
    if reply.get("t") != "paired":
        raise NetRefused("relay_refused", "an answer this game doesn't understand")


def connect_tls(address, port, expected_fp=None, timeout=CONNECT_SECONDS, room=None):
    """A TLS socket to the host. With expected_fp, the host's certificate must have exactly that SHA-256 fingerprint, or the
    connection is closed before a single byte of ours is sent (NetRefused("fingerprint")). Raises NetRefused for the other
    connection problems too, so the Join screen can show a plain message. Round MP2d: with `room`, (address, port) is a relay
    and the TLS connection runs through it (relay_join) - the relay only passes the encrypted bytes on."""
    try:
        raw = socket.create_connection((address, int(port)), timeout=timeout)
    except ConnectionRefusedError:
        raise NetRefused("refused_connection") from None
    except socket.timeout:
        raise NetRefused("no_answer") from None
    except socket.gaierror:
        raise NetRefused("unknown", "the address %s could not be found" % address) from None
    except OSError as e:
        if getattr(e, "errno", None) in (101, 113, 10051, 10065):          # network / host unreachable
            raise NetRefused("no_answer") from None
        raise NetRefused("unknown", str(e)) from None
    if room:
        try:
            relay_join(raw, room, timeout)
        except NetRefused:
            raw.close()
            raise
    try:
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock = client_context().wrap_socket(raw, server_hostname=None, do_handshake_on_connect=False)
        sock.settimeout(timeout)
        sock.do_handshake()
    except socket.timeout:
        raw.close()
        raise NetRefused("no_answer") from None
    except (ssl.SSLError, OSError) as e:
        raw.close()
        raise NetRefused("tls", str(e)) from None
    if expected_fp is not None:
        got = fingerprint(sock.getpeercert(binary_form=True) or b"")
        if got != expected_fp.lower():
            close_socket(sock)
            raise NetRefused("fingerprint")
    return sock


def close_socket(sock):
    """shutdown first: close() alone leaves the connection open while makefile() objects are still alive (found in the spike)."""
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def hello_line(name, password, code, deck_name, dck_text):
    return json.dumps({"c": "hello", "v": PROTOCOL_V, "name": clean_name(name) or "Guest", "password": password,
                       "code": code or "", "deck": {"name": deck_name or "Deck", "dck": dck_text or ""}}) + "\n"


def rejoin_line(token):
    """Round MP2: the guest's table coming back after its connection dropped (the token from the host's welcome)."""
    return json.dumps({"c": "rejoin", "v": PROTOCOL_V, "token": token or ""}) + "\n"


def watch_line(name, password, code=""):
    """Round MP2: someone who wants to watch the game (no deck, no seat)."""
    return json.dumps({"c": "watch", "v": PROTOCOL_V, "name": clean_name(name) or "Spectator", "password": password,
                       "code": code or ""}) + "\n"


def reconnect_wait(attempt):
    """Seconds to wait before reconnect attempt number `attempt` (1, 2, ...)."""
    return RECONNECT_WAITS[min(max(attempt, 1), len(RECONNECT_WAITS)) - 1]
