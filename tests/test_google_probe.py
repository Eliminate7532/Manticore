# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/google_signin_probe.py against a stand-in Google on 127.0.0.1 (no network, no real account)."""
import base64
import hashlib
import http.server
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

from tools import google_signin_probe as probe

CLIENT_ID = "1234-fake.apps.googleusercontent.com"
SECRET = "GOCSPX-fakeclientsecret00"
ACCESS = "ya29.fake-access-token-0001"
REFRESH = "1//fake-refresh-token-0002"
CODE = "4/fake-auth-code-0003"


class FakeGoogle:
    """The endpoints the probe uses. mode: ok | bad_state | no_drive | no_refresh | denied."""

    def __init__(self, mode="ok"):
        self.mode, self.challenge, self.redirect = mode, None, None
        self.files, self.token_calls, self.revoked, self.scope_asked = {}, 0, [], None
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                b = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _authed(self):
                return self.headers.get("Authorization") == "Bearer " + ACCESS

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                if u.path == "/auth":
                    fake.scope_asked = q.get("scope")
                    ok = (q.get("client_id") == CLIENT_ID and q.get("response_type") == "code"
                          and q.get("code_challenge_method") == "S256" and q.get("redirect_uri", "").startswith("http://127.0.0.1:"))
                    fake.challenge, fake.redirect = q.get("code_challenge"), q.get("redirect_uri")
                    back = {"error": "access_denied"} if fake.mode == "denied" else {"code": CODE}
                    back["state"] = "not-the-state" if fake.mode == "bad_state" else q.get("state")
                    if not ok:
                        back = {"error": "invalid_request", "state": q.get("state")}
                    self.send_response(302)
                    self.send_header("Location", fake.redirect + "/?" + urllib.parse.urlencode(back))
                    self.end_headers()
                elif u.path == "/userinfo":
                    self._json(200, {"email": "tester@example.com"}) if self._authed() else self._json(401, {})
                elif u.path.startswith("/drive/v3/files/") and q.get("alt") == "media":
                    fid = u.path.rsplit("/", 1)[1]
                    if not self._authed() or fid not in fake.files:
                        return self._json(404, {"error": {"message": "File not found"}})
                    b = fake.files[fid]
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                elif u.path == "/drive/v3/files":
                    if q.get("spaces") != "appDataFolder" or not self._authed():
                        return self._json(403, {"error": {"message": "wrong space"}})
                    self._json(200, {"files": [{"id": k, "name": "x"} for k in fake.files]})
                else:
                    self._json(404, {})

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                if u.path == "/token":
                    fake.token_calls += 1
                    f = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
                    want = base64.urlsafe_b64encode(hashlib.sha256(f.get("code_verifier", "").encode()).digest()) \
                        .rstrip(b"=").decode()
                    if (f.get("code") != CODE or f.get("client_secret") != SECRET or want != fake.challenge
                            or f.get("redirect_uri") != fake.redirect):
                        return self._json(400, {"error": "invalid_grant", "error_description": "Bad Request"})
                    scope = "openid https://www.googleapis.com/auth/userinfo.email"
                    if fake.mode != "no_drive":
                        scope += " https://www.googleapis.com/auth/drive.appdata"
                    t = {"access_token": ACCESS, "expires_in": 3599, "scope": scope, "token_type": "Bearer"}
                    if fake.mode != "no_refresh":
                        t["refresh_token"] = REFRESH
                    self._json(200, t)
                elif u.path == "/upload/drive/v3/files":
                    if not self._authed() or b'"appDataFolder"' not in body:
                        return self._json(403, {"error": {"message": "no"}})
                    content = body.split(b"Content-Type: text/plain\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
                    fake.files["f1"] = content
                    self._json(200, {"id": "f1", "name": probe.PROBE_NAME})
                elif u.path == "/revoke":
                    fake.revoked.append(urllib.parse.parse_qs(body.decode()).get("token", [""])[0])
                    self._json(200, {})
                else:
                    self._json(404, {})

            def do_DELETE(self):
                fid = urllib.parse.urlparse(self.path).path.rsplit("/", 1)[1]
                fake.files.pop(fid, None)
                self.send_response(204)
                self.end_headers()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.endpoints = {"auth": base + "/auth", "token": base + "/token", "userinfo": base + "/userinfo",
                          "drive": base, "revoke": base + "/revoke"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def browser(url):
    """Stands in for the user's browser: follows Google's redirect back to the probe."""
    try:
        urllib.request.urlopen(url, timeout=10).read()
    except Exception:
        pass


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = os.path.join(self.tmp.name, "google_oauth.bundled.json")
        with open(self.client, "w") as f:
            json.dump({"installed": {"client_id": CLIENT_ID, "client_secret": SECRET}}, f)
        self.saved = dict(probe.ENDPOINTS)

    def tearDown(self):
        probe.ENDPOINTS.clear()
        probe.ENDPOINTS.update(self.saved)
        self.tmp.cleanup()

    def go(self, mode="ok", path=None):
        g = FakeGoogle(mode)
        probe.ENDPOINTS.update(g.endpoints)
        out = io.StringIO()
        try:
            rc = probe.run(path or self.client, out=out, open_browser=browser, wait=20)
        finally:
            g.close()
        return rc, out.getvalue(), g

    def assertNoSecrets(self, text):
        for s in (ACCESS, REFRESH, CODE, SECRET):
            self.assertNotIn(s, text)

    def test_a_good_sign_in_says_ok_and_leaves_nothing_behind(self):
        rc, out, g = self.go()
        self.assertEqual(rc, 0, out)
        self.assertIn("Signed in as: tester@example.com", out)
        self.assertTrue(out.rstrip().endswith("PROBE RESULT: OK"), out)
        self.assertEqual(g.files, {})                    # the test file was deleted
        self.assertEqual(g.revoked, [REFRESH])          # and the sign-in revoked
        self.assertEqual(g.scope_asked.split(), list(probe.SCOPES))
        self.assertNoSecrets(out)
        url = [l.strip() for l in out.splitlines() if l.strip().startswith("http://127.0.0.1:")][0]
        self.assertNotIn("<hidden>", url)              # the address printed for copying by hand must work as printed

    def test_a_wrong_state_is_refused_before_anything_is_exchanged(self):
        rc, out, g = self.go("bad_state")
        self.assertEqual(rc, 1)
        self.assertIn("wrong 'state'", out)
        self.assertEqual(g.token_calls, 0)
        self.assertNoSecrets(out)

    def test_drive_folder_not_granted_fails_and_still_revokes(self):
        rc, out, g = self.go("no_drive")
        self.assertEqual(rc, 1)
        self.assertIn("didn't grant the Drive app folder", out)
        self.assertEqual(g.revoked, [REFRESH])
        self.assertNoSecrets(out)

    def test_no_refresh_token_fails(self):
        rc, out, _g = self.go("no_refresh")
        self.assertEqual(rc, 1)
        self.assertIn("No refresh token", out)
        self.assertNoSecrets(out)

    def test_cancel_on_googles_screen_is_said_plainly(self):
        rc, out, _g = self.go("denied")
        self.assertEqual(rc, 1)
        self.assertIn("Cancel or Deny", out)

    def test_a_web_client_file_or_no_file_is_explained(self):
        web = os.path.join(self.tmp.name, "web.json")
        with open(web, "w") as f:
            json.dump({"web": {"client_id": "x", "client_secret": "y"}}, f)
        rc, out, _g = self.go(path=web)
        self.assertEqual(rc, 1)
        self.assertIn("Desktop app", out)
        rc, out, _g = self.go(path=os.path.join(self.tmp.name, "missing.json"))
        self.assertEqual(rc, 1)
        self.assertIn("step A7", out)


if __name__ == "__main__":
    unittest.main()
