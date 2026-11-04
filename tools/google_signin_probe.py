# SPDX-License-Identifier: GPL-3.0-or-later
"""Google sign-in probe (3 Nov 2026, claude/GOOGLE_SYNC_OUTLINE_2026-11-03.md step A6): one real sign-in with Manticore's
Google client file, to see exactly what a tester will see BEFORE the sign-in is built into the game.

    python tools\\google_signin_probe.py [path to google_oauth.bundled.json]

Without a path it reads google_oauth.bundled.json next to the program (the commander_sim folder).

What it does, in order:
  1. opens your browser at Google's sign-in (scopes: openid, email, and Drive's hidden app data folder - nothing else);
  2. waits, at most 5 minutes, for Google to send the browser back to this computer (127.0.0.1, a random port; PKCE and a
     random state, as Google asks of desktop apps);
  3. reads the signed-in account's email;
  4. writes one small file to Manticore's app data folder in your Drive, reads it back and deletes it;
  5. revokes the sign-in, so nothing is left behind.
It prints what happened and a last line "PROBE RESULT: OK" or "PROBE RESULT: FAILED - <why>". It never prints a token or the
client secret. Standard library only.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SCOPES = ("openid", "email", "https://www.googleapis.com/auth/drive.appdata")
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.appdata"
WAIT_SECONDS = 300
PROBE_NAME = "manticore_probe.txt"
# Google's endpoints; the tests point these at a stand-in on 127.0.0.1.
ENDPOINTS = {
    "auth": "https://accounts.google.com/o/oauth2/v2/auth",
    "token": "https://oauth2.googleapis.com/token",
    "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
    "drive": "https://www.googleapis.com",
    "revoke": "https://oauth2.googleapis.com/revoke",
}


class ProbeError(Exception):
    """A step that failed; str() is the sentence to print."""


class Printer:
    """print() that blanks out every secret it has been told about (tokens, the client secret, the code)."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.secrets = set()

    def hide(self, *values):
        for v in values:
            if isinstance(v, str) and len(v) >= 6:
                self.secrets.add(v)

    def __call__(self, text):
        for s in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(s, "<hidden>")
        print(text, file=self.out)
        self.out.flush()


def read_client(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ProbeError(f"No client file at {path}. Do step A7 first (rename the downloaded JSON to google_oauth.bundled.json "
                         "and put it in the commander_sim folder), or give its path.")
    except (OSError, ValueError) as e:
        raise ProbeError(f"The client file {path} can't be read as JSON ({type(e).__name__}).")
    if "web" in data and "installed" not in data:
        raise ProbeError("This client file is for a 'Web application' client. Make a 'Desktop app' client (step A5.6).")
    c = data.get("installed")
    if not isinstance(c, dict) or not c.get("client_id") or not c.get("client_secret"):
        raise ProbeError("The client file has no 'installed' client_id and client_secret - is it the file Google gave you?")
    return c["client_id"], c["client_secret"]


def pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _Catcher(http.server.BaseHTTPRequestHandler):
    result = None            # set on the server object: {"query": {...}}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in q and "error" not in q:
            self.send_response(404)
            self.end_headers()
            return
        self.server.result = {k: v[0] for k, v in q.items()}
        body = ("<html><body style='font-family:sans-serif'><h2>Manticore sign-in probe</h2>"
                "<p>Done - you can close this tab and go back to the probe's window.</p></body></html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):      # nothing on the console: the query holds the code
        pass


def _post(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode("ascii"),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _send(req)


def _send(req):
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
            err = detail.get("error")
            msg = err.get("message") if isinstance(err, dict) else (detail.get("error_description") or err)
        except (ValueError, AttributeError, UnicodeDecodeError):
            msg = None
        raise ProbeError(f"{req.get_method()} {req.full_url.split('?')[0]} answered {e.code}" + (f": {msg}" if msg else ""))
    except urllib.error.URLError as e:
        raise ProbeError(f"Couldn't reach {req.full_url.split('?')[0]} ({e.reason}). Is this computer online?")


def _bearer(url, token, method="GET", data=None, headers=None):
    h = {"Authorization": "Bearer " + token}
    h.update(headers or {})
    return _send(urllib.request.Request(url, data=data, method=method, headers=h))


def _raw_bearer(url, token):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise ProbeError(f"GET {url.split('?')[0]} answered {e.code}")
    except urllib.error.URLError as e:
        raise ProbeError(f"Couldn't reach {url.split('?')[0]} ({e.reason}).")


def sign_in(client_id, client_secret, say, open_browser=webbrowser.open, wait=WAIT_SECONDS):
    """The browser sign-in. Returns Google's token answer (a dict)."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    say.hide(verifier)              # not the state: it is in the address printed below, for copying by hand
    server = http.server.HTTPServer(("127.0.0.1", 0), _Catcher)
    server.result = None
    redirect = f"http://127.0.0.1:{server.server_address[1]}"
    url = ENDPOINTS["auth"] + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent"})
    say("1. Opening your browser at Google's sign-in. Pick your account and look at what the screens say.")
    say("   (If no browser opens, copy this address into one:)")
    say("   " + url)
    server.timeout = 1
    t = threading.Thread(target=lambda: open_browser(url), daemon=True)
    t.start()
    left = wait
    while server.result is None and left > 0:
        server.handle_request()
        left -= 1
    server.server_close()
    q = server.result
    if q is None:
        raise ProbeError(f"No answer from the browser within {wait // 60} minutes.")
    if q.get("state") != state:
        raise ProbeError("The browser came back with the wrong 'state' - the answer was not for this sign-in; nothing used.")
    if "error" in q:
        raise ProbeError(f"Google said: {q['error']}" + (" (you clicked Cancel or Deny?)" if q["error"] == "access_denied" else ""))
    say.hide(q["code"])
    say("2. The browser came back. Exchanging the answer for a sign-in (PKCE).")
    tok = _post(ENDPOINTS["token"], {"code": q["code"], "client_id": client_id, "client_secret": client_secret,
                                     "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    say.hide(tok.get("access_token"), tok.get("refresh_token"), tok.get("id_token"))
    if not tok.get("access_token"):
        raise ProbeError("Google's answer had no access token.")
    return tok


def check_drive(token, say):
    """Write, read back, list and delete one small file in the app data folder. Returns the number of files seen there."""
    base = ENDPOINTS["drive"]
    content = b"Manticore sign-in probe - safe to delete.\n"
    boundary = "manticore" + secrets.token_hex(8)
    meta = json.dumps({"name": PROBE_NAME, "parents": ["appDataFolder"]}).encode("utf-8")
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n").encode("ascii") + meta + \
           (f"\r\n--{boundary}\r\nContent-Type: text/plain\r\n\r\n").encode("ascii") + content + \
           (f"\r\n--{boundary}--\r\n").encode("ascii")
    made = _bearer(base + "/upload/drive/v3/files?uploadType=multipart&fields=id,name", token, "POST", body,
                   {"Content-Type": f"multipart/related; boundary={boundary}"})
    fid = made.get("id")
    if not fid:
        raise ProbeError("Drive accepted the upload but gave no file id.")
    try:
        back = _raw_bearer(base + f"/drive/v3/files/{urllib.parse.quote(fid)}?alt=media", token)
        if back != content:
            raise ProbeError("The file read back from Drive is not the file written.")
        listed = _bearer(base + "/drive/v3/files?" + urllib.parse.urlencode(
            {"spaces": "appDataFolder", "fields": "files(id,name)", "pageSize": "100"}), token)
        files = listed.get("files", [])
        if fid not in {f.get("id") for f in files}:
            raise ProbeError("The file written is not in the app data folder's list.")
    finally:
        try:
            _bearer(base + f"/drive/v3/files/{urllib.parse.quote(fid)}", token, "DELETE")
        except ProbeError as e:
            say(f"   (couldn't delete the probe's file: {e})")
    return len(files)


def run(path, out=None, open_browser=webbrowser.open, wait=WAIT_SECONDS):
    say = Printer(out)
    tok, failed = None, None
    try:
        client_id, client_secret = read_client(path)
        say.hide(client_secret)
        say(f"Client file: {path} (a Desktop app client).")
        tok = sign_in(client_id, client_secret, say, open_browser, wait)
        granted = set((tok.get("scope") or "").split())
        say("   Google granted: " + (", ".join(sorted(granted)) or "(no scope list in the answer)"))
        say("   A refresh token (stays signed in): " + ("yes" if tok.get("refresh_token") else "NO"))
        if granted and DRIVE_SCOPE not in granted:
            raise ProbeError("Google didn't grant the Drive app folder (was its box unticked on the consent screen?).")
        me = _bearer(ENDPOINTS["userinfo"], tok["access_token"])
        say(f"3. Signed in as: {me.get('email', '(no email in the answer)')}")
        n = check_drive(tok["access_token"], say)
        say(f"4. Drive app data folder: wrote, read back, listed and deleted a test file - OK ({n} file(s) there, the test file included).")
        if not tok.get("refresh_token"):
            raise ProbeError("No refresh token: testers would have to sign in again every hour.")
    except ProbeError as e:
        failed = str(e)
    if tok:
        try:
            _post(ENDPOINTS["revoke"], {"token": tok.get("refresh_token") or tok["access_token"]})
            say("5. Signed out again (the probe's access was revoked).")
        except ProbeError as e:
            say(f"5. Couldn't revoke the probe's access ({e}); remove 'Manticore' at "
                "https://myaccount.google.com/connections if it is listed.")
    say("PROBE RESULT: " + (f"FAILED - {failed}" if failed else "OK"))
    return 1 if failed else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = argv[0] if argv else os.path.join(here, "google_oauth.bundled.json")
    return run(path)


if __name__ == "__main__":
    sys.exit(main())
