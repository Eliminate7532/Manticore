# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in for the Java bridge: speaks the same JSON lines so ForgeSession can be tested without Java."""
import json
import sys


def out(**m):
    sys.stdout.write(json.dumps(m) + "\n")
    sys.stdout.flush()


out(t="ready")
out(t="state", turn=1, phase="MAIN1", me=0, activePlayer=0, players=[
    {"id": 0, "name": "Me", "life": 40, "zones": {"hand": [{"id": 5, "name": "Forest"}], "battlefield": []}},
    {"id": 1, "name": "AI", "life": 40, "zones": {"battlefield": [{"id": 9, "name": "Island"}]}}],
    prompt={"message": "Priority", "ok": {"label": "OK", "enabled": True}, "cancel": {"label": "End Turn", "enabled": True}})
out(t="request", id=1, kind="confirm", title="Keep?", default=True, options=["Keep", "Mulligan"])
for line in sys.stdin:
    cmd = json.loads(line)
    if cmd["c"] == "quit":
        break
    out(t="log", entries=[{"type": "TEST", "text": "got " + json.dumps(cmd, sort_keys=True)}])
    if cmd["c"] == "die":
        out(t="fatal", text="boom")
        break
