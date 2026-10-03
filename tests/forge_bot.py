# SPDX-License-Identifier: GPL-3.0-or-later
"""
A scripted 'human' for testing: keeps hands with 2-5 lands, plays a land each turn, casts what Forge says is
actionable, pays by clicking the highlighted sources, and passes otherwise. It drives a ForgeSession exactly
the way the GUI does (clicks and OK), so it tests the whole path: client -> bridge -> Forge engine.
"""
import re
import time


def cmc(cost):
    total = 0
    for tok in re.findall(r"\{([^}]*)\}", cost or ""):
        total += int(tok) if tok.isdigit() else 1
    return total


class Bot:
    def __init__(self, session, cast=True):
        self.s = session
        self.cast = cast
        self.tried = set()           # (turn, card id) we already tried to cast
        self.pay_stuck = 0
        self.last_pay = None
        self.last_sig = None
        self.same = 0
        self.actions = []            # human-readable list of what it did

    def note(self, text):
        self.actions.append(text)

    def answer_requests(self):
        did = False
        while self.s.requests:
            req = self.s.requests[0]
            if req["kind"] == "confirm":
                self.s.answer(req, True)
            else:
                n = max(req.get("min", 1), 1 if req["kind"] == "choose" else 0)
                self.s.answer(req, list(range(n)))
            self.note(f"answered {req['kind']}: {req.get('title', '')[:50]}")
            did = True
        return did

    def step(self):
        """Do at most one thing. Returns True if it acted."""
        s = self.s
        if self.answer_requests():
            return True
        st = s.state
        if not st:
            return False
        pr = st.get("prompt", {})
        msg = pr.get("message", "")
        if not pr.get("ok", {}).get("enabled") and not pr.get("cancel", {}).get("enabled"):
            return False
        me = s.me()
        hand = me["zones"].get("hand", [])
        battlefield = me["zones"]["battlefield"]
        sig = (st["turn"], msg, len(hand), len(battlefield), pr.get("selecting"))
        if sig == self.last_sig:
            self.same += 1
            if self.same < 10:
                return False            # give the engine a moment to react to our last click
        else:
            self.same = 0
        self.last_sig = sig
        if s.is_mulligan_prompt():
            lands = sum(1 for c in hand if c.get("isLand"))
            if 2 <= lands <= 5:
                self.note(f"keep {lands} lands")
                s.ok()
            else:
                self.note(f"mulligan {lands} lands")
                s.cancel()
            return True
        if "Pay Mana Cost" in msg or pr.get("selecting"):
            sel = [c for c in battlefield if (c.get("selectable") or c.get("weak")) and not c.get("tapped")]
            self.pay_stuck = self.pay_stuck + 1 if msg == self.last_pay else 0
            self.last_pay = msg
            if self.pay_stuck >= 4:
                self.note("gave up paying")
                s.cancel()
                self.pay_stuck = 0
            elif sel:
                s.click_card(sel[0]["id"])
            else:
                s.ok()
            return True
        mine = st.get("activePlayer") == st["me"]
        main_open = "Main phase" in msg and "Stack: Empty" in msg
        if mine and main_open:
            lands = [c for c in hand if c.get("isLand")]
            if lands and me["landsPlayed"] < me["maxLandPlay"]:
                self.note(f"T{st['turn']} land {lands[0]['name']}")
                s.click_card(lands[0]["id"])
                return True
            if self.cast:
                cands = sorted((c for c in hand if not c.get("isLand") and c.get("weak") and cmc(c["cost"]) > 0
                                and (st["turn"], c["id"]) not in self.tried), key=lambda c: cmc(c["cost"]))
                if cands:
                    c = cands[0]
                    self.tried.add((st["turn"], c["id"]))
                    self.note(f"T{st['turn']} cast {c['name']}")
                    s.click_card(c["id"])
                    return True
        s.ok()
        return True

    def run(self, seconds, until=None):
        t0 = time.time()
        while time.time() - t0 < seconds and not self.s.exited and not self.s.game_over:
            self.s.poll()
            if until and until(self.s):
                return True
            if not self.step():
                time.sleep(0.03)
        return bool(until and until(self.s))
