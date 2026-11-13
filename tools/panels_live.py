# SPDX-License-Identifier: GPL-3.0-or-later
"""Task panels-live (Karl, 8 Nov 2026: "Live check now"): the right column's panels (bug-1567718618249035777) checked against a
real Forge game. Starts a dev game (card_check.Checker, Slow stops), plays a few turns so the game log has lines, sets up a board
where one spell of mine puts five items on the stack, then drives the table's right column with mouse and pen-style (touch=True)
events: fold the stack and scroll the log, unfold it and read every item, fold / unfold the focus card, drag both borders to their
limits, and a new table on the same settings file (the layout remembered). One window size per run.

    MANTICORE_PORTABLE=1 python3 tools/panels_live.py --size 1360x840 --scale 2.0 --out DIR

Writes DIR/<size>_<scale>/*.png and report.json; exit code 1 when a check fails. Developer tool: not shipped, not run by tests."""
import argparse
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("MANTICORE_NO_TOUR", "1")
os.environ.setdefault("MANTICORE_OFFLINE", "1")
os.environ.setdefault("MANTICORE_NO_UPDATE_CHECK", "1")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pygame  # noqa: E402

DECK = os.path.join(BASE, "sample_decks", "spellslinger_veyran.txt")
ISLANDS = "humanbattlefield=" + ";".join(["Island"] * 7)
TRIGGERS = "aibattlefield=Kambal, Consul of Allocation;Rhystic Study;Mystic Remora;Esper Sentinel;Plains;Plains;Plains"


class Run:
    def __init__(self, size, scale, out, touch_only=False):
        import card_check as cc
        import forge_table as ft
        from tests.forge_fake import StubStore
        self.cc, self.ft, self.Stub = cc, ft, StubStore
        self.size, self.scale, self.out = size, scale, out
        self.checks = []
        self.tmp = tempfile.mkdtemp(prefix="panels_live_")
        self.settings = os.path.join(self.tmp, "settings.json")
        os.makedirs(out, exist_ok=True)

    # ---- helpers ------------------------------------------------------------------------------------------------------------
    def check(self, name, ok, detail=""):
        self.checks.append({"check": name, "ok": bool(ok), "detail": str(detail)})
        print(("OK   " if ok else "FAIL ") + name + ("  -  " + str(detail) if detail else ""), flush=True)
        return ok

    def shot(self, name):
        path = os.path.join(self.out, name + ".png")
        self.frame(2)
        pygame.image.save(self.gui.screen, path)
        return path

    def frame(self, n=1):
        for _ in range(n):
            self.gui.sync()
            self.gui.render()

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.s.poll()
            time.sleep(0.03)

    def ev(self, kind, **kw):
        self.gui.handle_event(pygame.event.Event(kind, **kw))

    def move(self, pos, held=False, touch=False):
        self.ev(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(1 if held else 0, 0, 0), touch=touch)

    def click(self, pos, touch=False):
        self.move(pos, touch=touch)
        self.ev(pygame.MOUSEBUTTONDOWN, pos=pos, button=1, touch=touch)
        self.ev(pygame.MOUSEBUTTONUP, pos=pos, button=1, touch=touch)
        self.frame(2)

    def drag(self, start, end, touch=False):
        self.move(start, touch=touch)
        self.ev(pygame.MOUSEBUTTONDOWN, pos=start, button=1, touch=touch)
        steps = 6
        for i in range(1, steps + 1):
            p = (start[0], start[1] + (end[1] - start[1]) * i // steps)
            # a pen's motion events: SDL reports no button for a touch, only touch=True
            self.move(p, held=not touch, touch=touch)
            self.frame(1)
        self.ev(pygame.MOUSEBUTTONUP, pos=end, button=1, touch=touch)
        self.frame(2)

    def wheel(self, pos, y):
        self.move(pos)
        self.ev(pygame.MOUSEWHEEL, x=0, y=y, flipped=False, precise_x=0.0, precise_y=float(y), touch=False, which=0)
        self.frame(1)

    def button(self, name):
        best = None
        for rect, kind, data in self.gui.hits:
            if kind == "button" and data.get("name") == name and (best is None or rect.w * rect.h < best.w * best.h):
                best = rect
        return best

    def seam(self, name):
        for n, r in getattr(self.gui.L, "seams", ()):
            if n == name:
                return r
        return None

    def sent(self):
        return len(self.s.sent_all)

    def rects(self):
        L = self.gui.L
        return {k: tuple(getattr(L, k)) for k in ("preview", "stack", "log") if getattr(L, k, None) is not None}

    def column_ok(self, label):
        """Every panel inside the right column, no two overlapping (heights > 0)."""
        L = self.gui.L
        R = L.right
        parts = []
        if getattr(L, "panel_on", False):
            parts.append(("focus", L.preview))
        elif "focus" in getattr(L, "heads", {}):
            parts.append(("focus", L.heads["focus"]))
        if L.stack.h > 0:
            parts.append(("stack", L.stack))
        if getattr(L, "log_col", False) and L.log.h > 0:
            parts.append(("log", L.log))
        bad = [n for n, r in parts if not R.contains(r)]
        over = [(a, b) for i, (a, ra) in enumerate(parts) for b, rb in parts[i + 1:] if ra.colliderect(rb)]
        return self.check(f"{label}: panels inside the column, none overlapping", not bad and not over,
                          f"outside={bad} overlap={over} " + str({n: tuple(r) for n, r in parts}))

    # ---- the game -----------------------------------------------------------------------------------------------------------
    def start(self):
        self.chk = self.cc.Checker(DECK, classic_stops=True, say=lambda *a: None, seed=4321)
        self.chk.start()
        self.s = self.chk.s
        # a few turns go by so the game log has lines to scroll (Slow stops: pass at every one)
        end = time.time() + 120
        while time.time() < end:
            self.s.poll()
            st = self.s.state or {}
            mine_main = (st.get("phase") == "MAIN1" and st.get("activePlayer") == st.get("me") and not st.get("stack"))
            if st.get("asking") and st.get("input") == "InputPassPriority":
                if st.get("turn", 0) >= 5 and mine_main:
                    break
                self.s.ok()
                time.sleep(0.15)
            elif st.get("asking") and "discard" in ((st.get("prompt") or {}).get("message") or "").lower():
                hand = self.s.me()["zones"]["hand"]
                if hand:                                  # the cleanup step's discard down to seven
                    self.s.click_card(hand[0]["id"])
                    time.sleep(0.3)
            if self.s.requests:
                req = self.s.requests[0]
                self.s.answer(req, self.cc.request_answer(req, {}))
            time.sleep(0.05)
        n = self.s.setups_done
        self.s.setup(["activeplayer=human", "activephase=MAIN1", "humanlife=40", "ailife=40",
                      "humanlibrary=Island;Island;Island;Island;Island;Island;Island;Island",
                      "ailibrary=Plains;Plains;Plains;Plains;Plains;Plains;Plains;Plains",
                      ISLANDS, "humanhand=Divination;Opt;Stifle", TRIGGERS])
        end = time.time() + 30
        while time.time() < end and self.s.setups_done <= n:
            self.s.poll()
            time.sleep(0.05)
        self.chk.pump(1.5)

    def cast_for_a_full_stack(self):
        s = self.s
        end = time.time() + 20
        while time.time() < end:                       # Main 1, empty stack, asking me
            s.poll()
            st = s.state or {}
            if st.get("asking") and st.get("input") == "InputPassPriority" and st.get("phase") == "MAIN1" \
                    and not st.get("stack") and st.get("activePlayer") == st.get("me"):
                break
            if st.get("asking") and st.get("input") == "InputPassPriority" and st.get("phase") != "MAIN1":
                s.ok()
                time.sleep(0.2)
            if s.requests:
                req = s.requests[0]
                s.answer(req, self.cc.request_answer(req, {}))
            time.sleep(0.05)
        card = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Divination")
        s.click_card(card["id"])
        end = time.time() + 20
        while time.time() < end:
            s.poll()
            st = s.state or {}
            inp = st.get("input") or ""
            if st.get("asking") and inp.startswith("InputPayMana"):
                s.ok()
                time.sleep(0.3)
            if s.requests:
                req = s.requests[0]
                s.answer(req, self.cc.request_answer(req, {}))
            if st.get("asking") and inp == "InputPassPriority" and len(st.get("stack") or []) >= 4:
                return st
            time.sleep(0.05)
        return s.state or {}

    def new_table(self):
        gui = self.ft.ForgeTable(self.s, self.Stub(), settings_path=self.settings, window_size=self.size)
        gui.text_scale = self.scale
        gui.animations = False
        self.gui = gui
        self.frame(3)
        return gui

    # ---- the checks ---------------------------------------------------------------------------------------------------------
    def run(self):
        self.start()
        st = self.cast_for_a_full_stack()
        n_stack = len(st.get("stack") or [])
        self.check("a real stack of 4+ items", n_stack >= 4, [((i.get("card") or {}).get("name")) for i in st.get("stack", [])])
        gui = self.new_table()
        sent0 = self.sent()
        shots = {"start": self.shot("01_start")}
        with open(os.path.join(self.out, "state.json"), "w", encoding="utf-8") as fh:   # for an offline render on the base
            json.dump({"state": self.s.state, "log": list(self.s.log)}, fh)
        self.column_ok("as designed")
        log_lines = len(self.s.log)

        # 1. fold the stack, scroll the log up and down
        fb = self.button("fold_stack")
        self.check("the stack has a fold arrow", fb is not None)
        stack_before = gui.L.stack.h
        log_before = gui.L.log.h
        self.click(fb.center)
        self.check("the stack folds to its heading", gui.panel_fold["stack"] and gui.L.stack.h < stack_before, (stack_before, gui.L.stack.h))
        self.check("the folded stack gives its room to the log", gui.L.log.h > log_before, (log_before, gui.L.log.h))
        self.column_ok("stack folded")
        shots["stack_folded"] = self.shot("02_stack_folded")
        lp = gui.L.log.center
        self.wheel(lp, 3)
        up1 = gui.log_scroll
        self.wheel(lp, 3)
        up2 = gui.log_scroll
        shots["log_scrolled_up"] = self.shot("03_log_scrolled_up")
        self.check("the game log scrolls up (wheel)", up1 > 0, f"log_scroll {up1} then {up2}, overflow={gui.log_overflow}")
        self.wheel(lp, -3)
        down = gui.log_scroll
        self.wheel(lp, -30)
        self.check("the game log scrolls back down to the newest", down < up2 and gui.log_scroll == 0, (up2, down, gui.log_scroll))

        # 2. unfold the stack, every item readable
        self.click(self.button("fold_stack").center)
        self.check("the stack opens again", not gui.panel_fold["stack"] and gui.L.stack.h > gui.L.panel_head)
        seen = set()
        for _ in range(30):
            self.frame(1)
            content_top = gui.L.stack.y + gui.L.panel_head
            for cid, r in gui.stack_rects.items():
                if r.y >= content_top - 1 and r.bottom <= gui.L.stack.bottom + 1:
                    seen.add(cid)
            if not gui.stack_overflow:
                break
            self.wheel(gui.L.stack.center, -1)
        ids = {(it.get("card") or {}).get("id") for it in gui.state.get("stack", [])}
        self.check("every stack item is shown whole (scrolling if needed)", ids <= seen, f"items={sorted(ids)} seen={sorted(seen)}")
        self.wheel(gui.L.stack.center, 30)
        shots["stack_open"] = self.shot("04_stack_open")

        # 3. fold / unfold the focus card
        fr = self.button("fold_focus")
        self.check("the focus card has a fold control", fr is not None)
        if fr is not None:
            self.click(fr.center)
            self.check("the focus card folds", gui.panel_fold["focus"] and "focus" in gui.L.heads)
            self.column_ok("focus folded")
            shots["focus_folded"] = self.shot("05_focus_folded")
            self.click(self.button("fold_focus").center)
            self.check("the focus card opens again", not gui.panel_fold["focus"] and gui.L.panel_on)

        # 4. drag both borders to their limits, with the mouse and with a pen
        R = gui.L.right
        for pen in (False, True):
            tag = "pen" if pen else "mouse"
            for name in ("focus", "stack"):
                gui.panel_size = {"focus": None, "stack": None}     # each border from the designed layout
                self.frame(2)
                sr = self.seam(name)
                if not self.check(f"{tag}: the {name} border can be grabbed", sr is not None):
                    continue
                self.drag(sr.center, (sr.centerx, R.y - 200), touch=pen)
                small = self.rects()
                self.column_ok(f"{tag}: {name} border at its top limit")
                shots[f"{tag}_{name}_min"] = self.shot(f"06_{tag}_{name}_min")
                sr = self.seam(name)
                self.drag(sr.center, (sr.centerx, R.bottom + 200), touch=pen)
                big = self.rects()
                self.column_ok(f"{tag}: {name} border at its bottom limit")
                shots[f"{tag}_{name}_max"] = self.shot(f"07_{tag}_{name}_max")
                key = "preview" if name == "focus" else "stack"
                self.check(f"{tag}: dragging the {name} border changes its height", big[key][3] > small[key][3],
                           (small[key], big[key]))
                self.check(f"{tag}: the {name} size is a share of the column",
                           gui.panel_size[name] is not None and 0.05 <= gui.panel_size[name] <= 0.95, gui.panel_size[name])
                # as designed (test_rightpanels pins it): at a border's bottom limit the log keeps its heading and fold arrow
                self.check(f"{tag}: the log keeps at least its heading", gui.L.log.h >= gui.L.panel_head
                           and self.button("fold_log") is not None, gui.L.log)
            if not pen:           # a middle setting for the remembered layout: the focus a bit smaller, the stack a bit taller
                gui.panel_size = {"focus": None, "stack": None}
                self.frame(2)
                sf = self.seam("focus")
                self.drag(sf.center, (sf.centerx, sf.centery - 60))
                ss = self.seam("stack")
                self.drag(ss.center, (ss.centerx, ss.centery + 40))

        # 5. a fold, then a new table on the same settings: the layout is remembered
        self.click(self.button("fold_log").center if self.button("fold_log") else (0, 0))
        before = (dict(gui.panel_fold), dict(gui.panel_size), self.rects())
        shots["before_restart"] = self.shot("08_before_restart")
        self.check("nothing was sent to Forge by any of it", self.sent() == sent0, (sent0, self.sent()))
        self.new_table()
        after = (dict(self.gui.panel_fold), dict(self.gui.panel_size), self.rects())
        shots["after_restart"] = self.shot("09_after_restart")
        self.check("a new table remembers folds and sizes", before[0] == after[0] and before[1] == after[1], (before[:2], after[:2]))
        self.check("a new table lays the column out the same", before[2] == after[2], (before[2], after[2]))
        self.column_ok("after restart")
        # put it back: unfold the log, right-click both borders
        self.click(self.button("fold_log").center)
        for name in ("focus", "stack"):
            sr = self.seam(name)
            if sr is not None:
                self.move(sr.center)
                self.ev(pygame.MOUSEBUTTONDOWN, pos=sr.center, button=3)
                self.ev(pygame.MOUSEBUTTONUP, pos=sr.center, button=3)
                self.frame(1)
        self.check("right-clicks put both heights back as designed", self.gui.panel_size == {"focus": None, "stack": None},
                   self.gui.panel_size)
        shots["reset"] = self.shot("10_reset")
        return {"size": self.size, "scale": self.scale, "stack_items": n_stack, "log_lines": log_lines,
                "checks": self.checks, "shots": shots}

    def close(self):
        try:
            self.chk.stop()
        except Exception:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="1360x840")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    w, h = (int(x) for x in a.size.split("x"))
    out = os.path.join(a.out, f"{w}x{h}_{int(a.scale * 100)}")
    pygame.init()
    r = Run((w, h), a.scale, out)
    try:
        rep = r.run()
    except Exception as e:  # noqa: BLE001 - the report says what broke
        import traceback
        traceback.print_exc()
        rep = {"size": (w, h), "scale": a.scale, "checks": r.checks + [{"check": "the run itself", "ok": False, "detail": repr(e)}]}
    finally:
        r.close()
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=str)
    bad = [c for c in rep["checks"] if not c["ok"]]
    print(f"{len(rep['checks']) - len(bad)} of {len(rep['checks'])} checks OK", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
