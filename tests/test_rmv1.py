# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RMV1 (16-17 Oct 2026): three open ROADMAP lines, checked against Forge itself.

All three date from before Forge was the rules engine (the old table_gui.py worked mana out by itself). Each is checked here in the
way the table plays, through the real bridge:

  ROADMAP "Enforce 'spend this mana only on ...' restrictions (Delighted Halfling)"
      LiveManaTests: the Halfling's "any colour, only for a legendary spell" mana taps and floats, but does NOT pay for a
      non-legendary spell (Savannah Lions stays unpaid, Auto stays grey), and DOES pay for a legendary one (Isamaru resolves).
  ROADMAP "Mana Vault / Basalt Monolith: pay to untap (Forge script has the ability; not menu-driven yet)"
      LiveManaTests: a click on a tapped Basalt Monolith goes straight to "Pay Mana Cost: {3}" for its untap; paid, it untaps.
      Mana Vault's upkeep "you may pay {4}" is a payment step (Cancel = don't pay): paid, it untaps and the draw step deals no
      damage; not paid, it stays tapped and deals 1. Neither artifact untaps in my untap step (the Islands beside them do).
      PaymentHintTests: found on the way - the action bar said "Cancel = take the spell back" for Mana Vault's payment too, where
      Cancel means not paying. forge_table.resolving_payment() now tells that payment apart (the top of the stack is resolving
      from the card that asks), and the hint says "Cancel = don't pay."
  ROADMAP "Prompts still answered with Forge's default instead of asking: ... (manipulateCardList)"
      GuardTests: in Forge fb4d809 the only way to manipulateCardList is arrangeForScry -> arrangeForMove, behind
      UI_SELECT_FROM_CARD_DISPLAYS, which the bridge turns off - so scry asks with lists instead (test_round28b_scenarios
      scenario 5 now plays scry 2 twice through them). These tests go red when a Forge update adds another way in.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
import zipfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import forge_client as fc
import tests.live as live

MAIN_JAVA = os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "Main.java")
BRIDGE_SRC = os.path.join(BASE, "java_bridge", "src")
DECK = os.path.join(BASE, "sample_decks", "spellslinger_veyran.txt")
FIXTURES = os.path.join(BASE, "tests", "fixtures", "rmv1")      # three real payment snapshots, recorded with Forge fb4d809 on 16 Oct

# Every class in Forge fb4d809's forge.jar whose constant pool names manipulateCardList (checked 16 Oct 2026). IGuiGame declares it;
# PlayerControllerHuman calls it; CMatchUI / GuiChoose(+$1) are Forge's own desktop window and ProtocolGuiGame / ProtocolMethod its own
# network client - neither is the bridge's IGuiGame (BridgeGui is).
KNOWN_MANIPULATE = {
    "forge/gamemodes/net/ProtocolGuiGame.class",
    "forge/gamemodes/net/ProtocolMethod.class",
    "forge/gui/GuiChoose$1.class",
    "forge/gui/GuiChoose.class",
    "forge/gui/interfaces/IGuiGame.class",
    "forge/player/PlayerControllerHuman.class",
    "forge/screens/match/CMatchUI.class",
}
KNOWN_ARRANGE = {"forge/player/PlayerControllerHuman.class"}


def forge_jar():
    return os.path.join(fc.DEFAULT_RUNTIME, "forge.jar")


def classes_naming(jar_path, name):
    """The .class entries of a jar whose bytes contain `name` (a method name lives in the constant pool as plain UTF-8)."""
    needle = name.encode("utf-8")
    with zipfile.ZipFile(jar_path) as z:
        return {i.filename for i in z.infolist() if i.filename.endswith(".class") and needle in z.read(i)}


def methods_calling(javap_text, needle):
    """Names of the methods in `javap -c -p` output whose code contains `needle`."""
    found, current = set(), None
    for line in javap_text.splitlines():
        m = re.match(r"^  \S.*?\b(\w+)\(.*\);$", line)
        if m:
            current = m.group(1)
        elif current and needle in line:
            found.add(current)
    return found


def method_code(javap_text, method):
    """The bytecode lines of one method in `javap -c -p` output (the first overload with that name)."""
    lines, inside = [], False
    for line in javap_text.splitlines():
        if re.match(r"^  \S.*?\b%s\(.*\);$" % re.escape(method), line):
            inside = True
            continue
        if inside:
            if re.match(r"^  \S", line) or line.strip() == "}":
                break
            lines.append(line)
    return lines


def scry_gate_holds(javap_text):
    """True when arrangeForScry reads UI_SELECT_FROM_CARD_DISPLAYS, and an ifeq right after that read jumps past its only call of
    arrangeForMove - i.e. with the setting off, arrangeForMove (and manipulateCardList behind it) is never reached."""
    code = method_code(javap_text, "arrangeForScry")
    offset = lambda ln: int(re.match(r"\s*(\d+):", ln).group(1))
    pref = [i for i, ln in enumerate(code) if "UI_SELECT_FROM_CARD_DISPLAYS" in ln]
    calls = [ln for ln in code if "Method arrangeForMove:" in ln]
    if len(pref) != 1 or len(calls) != 1:
        return False
    after = code[pref[0] + 1:pref[0] + 4]
    jump = next((ln for ln in after if re.search(r"\bifeq\s+(\d+)", ln)), None)
    if not jump or not any("getPrefBoolean" in ln for ln in after):
        return False
    target = int(re.search(r"\bifeq\s+(\d+)", jump).group(1))
    return offset(code[pref[0]]) < offset(calls[0]) < target


def javap_of(jar_path, cls):
    tool = shutil.which("javap")
    if not tool:
        return None
    out = subprocess.run([tool, "-c", "-p", "-cp", jar_path, cls], capture_output=True, text=True, timeout=120)
    return out.stdout if out.returncode == 0 else None


class GuardTests(unittest.TestCase):
    """ROADMAP 'manipulateCardList': unreachable while the bridge keeps Forge's card displays off."""

    def test_the_bridge_turns_forges_card_displays_off(self):
        with open(MAIN_JAVA, encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r"setPref\(FPref\.UI_SELECT_FROM_CARD_DISPLAYS,\s*false\)")
        for folder, _dirs, files in os.walk(BRIDGE_SRC):
            for name in files:
                if name.endswith(".java"):
                    with open(os.path.join(folder, name), encoding="utf-8") as f:
                        text = f.read()
                    self.assertNotRegex(text, r"UI_SELECT_FROM_CARD_DISPLAYS,\s*true", name + " turns the card displays back on")

    def test_only_the_known_forge_classes_name_manipulate_card_list(self):
        if not os.path.isfile(forge_jar()):
            self.skipTest("forge_runtime/forge.jar is not set up here")
        self.assertEqual(classes_naming(forge_jar(), "manipulateCardList"), KNOWN_MANIPULATE,
                         "a Forge update changed who can reach manipulateCardList: check the new callers against BridgeGui's auto-answer")
        self.assertEqual(classes_naming(forge_jar(), "arrangeForMove"), KNOWN_ARRANGE)

    def test_the_only_way_in_is_scry_behind_the_card_display_setting(self):
        if not os.path.isfile(forge_jar()):
            self.skipTest("forge_runtime/forge.jar is not set up here")
        text = javap_of(forge_jar(), "forge.player.PlayerControllerHuman")
        if text is None:
            self.skipTest("javap is not on PATH (it comes with a JDK)")
        self.assertEqual(methods_calling(text, "InterfaceMethod forge/gui/interfaces/IGuiGame.manipulateCardList:"), {"manipulateCardList"})
        self.assertEqual(methods_calling(text, "Method manipulateCardList:"), {"arrangeForMove"})
        self.assertEqual(methods_calling(text, "Method arrangeForMove:"), {"arrangeForScry"})
        self.assertTrue(scry_gate_holds(text), "arrangeForScry no longer reaches arrangeForMove only when UI_SELECT_FROM_CARD_DISPLAYS is on")

    def test_the_gate_reader_tells_a_gated_call_from_an_open_one(self):
        """scry_gate_holds on hand-made javap text: the real shape passes; no ifeq, a jump that lands before the call, or a second
        call outside the branch does not."""
        gated = "\n".join([
            "  public org.Pair arrangeForScry(forge.game.card.CardCollection);",
            "    Code:",
            "      30: getstatic     #612                // Field forge/X$FPref.UI_SELECT_FROM_CARD_DISPLAYS:Lforge/X;",
            "      33: invokevirtual #298                // Method forge/X.getPrefBoolean:(Lforge/Y;)Z",
            "      36: ifeq          110",
            "      83: invokevirtual #1229               // Method arrangeForMove:(Ljava/lang/String;)Lorg/Pair;",
            "     110: aload_1",
            "  public void other();",
        ])
        self.assertTrue(scry_gate_holds(gated))
        self.assertFalse(scry_gate_holds(gated.replace("ifeq          110", "nop")))
        self.assertFalse(scry_gate_holds(gated.replace("ifeq          110", "ifeq          50")))
        self.assertFalse(scry_gate_holds(gated.replace("     110: aload_1", "     110: invokevirtual #1229 // Method arrangeForMove:(X)Y")))


def fixture(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


class PaymentHintTests(unittest.TestCase):
    """The bar's words for a payment, from real snapshots: Mana Vault's upkeep cost is paid while its trigger resolves."""

    def setUp(self):
        import forge_table as ft
        self.ft = ft

    def test_only_the_payment_asked_by_a_resolving_trigger_counts(self):
        self.assertTrue(self.ft.resolving_payment(fixture("mana_vault_upkeep_pay")))
        self.assertFalse(self.ft.resolving_payment(fixture("basalt_monolith_untap_pay")))      # an ability being activated
        self.assertFalse(self.ft.resolving_payment(fixture("savannah_lions_cast_pay")))        # a spell being cast
        self.assertFalse(self.ft.resolving_payment({}))

    def test_a_payment_while_another_cards_trigger_waits_is_not_resolving(self):
        st = copy.deepcopy(fixture("mana_vault_upkeep_pay"))
        st["stack"][0]["card"]["id"] = 999                               # say I cast something in answer to some other trigger
        self.assertFalse(self.ft.resolving_payment(st))
        st = copy.deepcopy(fixture("mana_vault_upkeep_pay"))
        st["prompt"]["message"] = st["prompt"]["message"].replace("Pay Mana Cost", "Pay Life")
        self.assertFalse(self.ft.resolving_payment(st))

    def test_the_words(self):
        msg = fixture("mana_vault_upkeep_pay")["prompt"]["message"]
        head, hint, _ = self.ft.prompt_view(msg, "Checker", kind="pay", resolving=True)
        self.assertEqual(head, "Pay {4}?  -  Mana Vault")
        self.assertTrue(hint.startswith("Cancel = don't pay."), hint)       # first: a long hint line is cut at its end
        self.assertNotIn("spell back", hint)
        for kw in ({"kind": None}, {"kind": "pay", "floating": True},
                   {"kind": "pay", "trouble": "You need {4}; nothing untapped can pay it."}):
            _head, hint, _ = self.ft.prompt_view(msg, "Checker", resolving=True, **kw)
            self.assertTrue(hint.startswith("Cancel = don't pay."), (kw, hint))
            self.assertNotIn("spell back", hint)
        head, hint, _ = self.ft.prompt_view(msg, "Checker", kind="pay")                       # unchanged without it
        self.assertEqual(head, "Pay {4}  -  Mana Vault")
        self.assertTrue(hint.endswith("Cancel = take the spell back."), hint)

    def drawn_hint(self, name, size, scale=1.0):
        import gfx
        from tests.forge_fake import FakeSession, StubStore, load_log
        from tests.test_forge_table import frame
        gui = self.ft.ForgeTable(FakeSession(fixture(name), load_log()), StubStore(), settings_path=None, window_size=size)
        gui.text_scale = scale
        gui.animations = False
        lines, draw_line = [], gui.draw_line

        def record(text, x, y, font, colour):
            if gui.L.bar.collidepoint(x, y) and colour == gfx.HINT_LINE_TEXT:
                lines.append(text)
            return draw_line(text, x, y, font, colour)
        gui.draw_line = record
        frame(gui, 2)
        lines.clear()
        frame(gui, 1)
        return " ".join(lines)

    def test_the_table_draws_them_where_they_can_be_read(self):
        for size, scale in (((1920, 1080), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.5)):
            vault = self.drawn_hint("mana_vault_upkeep_pay", size, scale)
            if vault:                                                    # a bar too short for the hint shows the headline only
                self.assertIn("Cancel = don't pay", vault, (size, scale))
            for other in ("savannah_lions_cast_pay", "basalt_monolith_untap_pay"):
                self.assertNotIn("don't pay", self.drawn_hint(other, size, scale), (other, size, scale))
        self.assertIn("Cancel = don't pay", self.drawn_hint("mana_vault_upkeep_pay", (1920, 1080)))


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
class LiveManaTests(unittest.TestCase):

    def start(self, lines):
        import card_check as cc
        self.cc = cc
        chk = cc.Checker(DECK, say=lambda *a: None, classic_stops=True)
        chk.start()
        self.addCleanup(chk.stop)
        self.chk, s = chk, chk.s
        n = s.setups_done
        s.setup(["humanlife=40", "ailife=40", "removesummoningsickness=true", "ailibrary=Forest;Forest;Forest",
                 "humanlibrary=Island;Island;Island;Island"] + lines)
        self.assertTrue(self.wait(lambda st: s.setups_done > n, 60), "the set-up never finished")
        chk.pump(1.0)
        return s

    def wait(self, pred, secs=20):
        s = self.chk.s
        end = time.time() + secs
        while time.time() < end:
            s.poll()
            if pred(s.state or {}):
                return True
            time.sleep(0.02)
        return False

    def card(self, zone, name):
        return next((c for c in self.chk.s.me()["zones"].get(zone, []) if c["name"] == name), None)

    def engine_problems(self):
        return [ln for ln in self.chk.new_engine_lines() if re.search(r"Exception|Report a crash", ln)]

    def paying(self, st):
        return (st.get("input") or "").startswith("InputPayMana")

    # ---- Delighted Halfling ---------------------------------------------------------------------------------------------------

    def tap_halfling_for(self, spell):
        """Cast `spell` with a Delighted Halfling as the only mana source, and tap it for its restricted 'any colour' mana."""
        s = self.start(["activeplayer=human", "activephase=MAIN1", "humanbattlefield=Delighted Halfling", "humanhand=" + spell])
        s.click_card(self.card("hand", spell)["id"])
        self.assertTrue(self.wait(self.paying), "no payment step for " + spell)
        s.click_card(self.card("battlefield", "Delighted Halfling")["id"])
        self.assertTrue(self.wait(lambda st: bool(s.requests), 10), "the Halfling's two abilities weren't offered")
        req = s.requests[0]
        labels = [it.get("label") or "" for it in req.get("items") or []]
        restricted = next(i for i, lb in enumerate(labels) if "legendary spell" in lb)
        s.answer(req, [restricted])
        self.assertTrue(self.wait(lambda st: self.card("battlefield", "Delighted Halfling")["tapped"], 10), "the Halfling didn't tap")
        time.sleep(1.0)
        s.poll()
        return s

    def test_halflings_restricted_mana_does_not_pay_for_a_non_legendary_spell(self):
        s = self.tap_halfling_for("Savannah Lions")
        st = s.state
        self.assertTrue(self.paying(st), "the payment step went away: the restricted mana paid for Savannah Lions")
        self.assertIn("Pay Mana Cost: {W}", (st.get("prompt") or {}).get("message", ""))
        self.assertEqual(s.me().get("manaPool"), {"W": 1}, "the white mana should float, unspent")
        self.assertFalse(((st.get("prompt") or {}).get("ok") or {}).get("enabled"), "Auto should be grey: nothing can pay")
        self.assertIsNone(self.card("battlefield", "Savannah Lions"))
        self.assertFalse(st.get("stack"))
        self.assertEqual(s.checks, [])
        self.assertEqual(self.engine_problems(), [])

    def test_halflings_restricted_mana_pays_for_a_legendary_spell(self):
        s = self.tap_halfling_for("Isamaru, Hound of Konda")
        self.assertTrue(self.wait(lambda st: not self.paying(st) and bool(st.get("stack")), 10), "Isamaru wasn't paid for")
        s.ok()                                                           # pass priority: it resolves
        self.assertTrue(self.wait(lambda st: self.card("battlefield", "Isamaru, Hound of Konda") is not None, 10))
        self.assertEqual(s.me().get("manaPool") or {}, {})
        self.assertEqual(s.checks, [])
        self.assertEqual(self.engine_problems(), [])

    # ---- Basalt Monolith ------------------------------------------------------------------------------------------------------

    def test_a_click_on_a_tapped_basalt_monolith_pays_three_and_untaps_it(self):
        s = self.start(["activeplayer=human", "activephase=MAIN1", "humanbattlefield=Basalt Monolith|Tapped;Wastes;Wastes;Wastes",
                        "humanhand=Island"])
        self.assertTrue(self.card("battlefield", "Basalt Monolith")["tapped"])
        s.click_card(self.card("battlefield", "Basalt Monolith")["id"])
        self.assertTrue(self.wait(self.paying), "no payment step for the Monolith's untap")
        msg = (s.state.get("prompt") or {}).get("message", "")
        self.assertIn("Untap this artifact", msg)
        self.assertIn("Pay Mana Cost: {3}", msg)
        self.assertFalse(s.requests, "a tapped Monolith has one ability to offer, not a choice")
        for land in [c for c in s.me()["zones"]["battlefield"] if c["name"] == "Wastes"]:
            s.click_card(land["id"])
            time.sleep(0.4)
        self.assertTrue(self.wait(lambda st: not self.paying(st) and bool(st.get("stack")), 10), "the untap wasn't paid for")
        s.ok()
        self.assertTrue(self.wait(lambda st: self.card("battlefield", "Basalt Monolith")["tapped"] is False, 10), "the Monolith is still tapped")
        self.assertTrue(all(c["tapped"] for c in s.me()["zones"]["battlefield"] if c["name"] == "Wastes"))
        self.assertEqual(s.checks, [])
        self.assertEqual(self.engine_problems(), [])

    # ---- Mana Vault -----------------------------------------------------------------------------------------------------------

    def through_my_upkeep(self, pay):
        """From the AI's end step to my next Main 1, with Mana Vault and Basalt Monolith tapped and four tapped Islands. Mana Vault's
        upkeep trigger asks to pay {4}: Islands one by one when `pay`, else Cancel. Returns the payment prompts seen."""
        s = self.start(["activeplayer=ai", "activephase=END_OF_TURN", "humanhand=Plains",
                        "humanbattlefield=Mana Vault|Tapped;Basalt Monolith|Tapped;Island|Tapped;Island|Tapped;Island|Tapped;Island|Tapped"])
        payments, end = [], time.time() + 90
        while time.time() < end:
            s.poll()
            st = s.state or {}
            if s.requests:
                req = s.requests[0]
                s.answer(req, self.cc.request_answer(req, {}))
                time.sleep(0.5)
                continue
            mine = st.get("activePlayer") == st.get("me")
            if self.paying(st):
                msg = (st.get("prompt") or {}).get("message", "")
                if msg not in payments:
                    payments.append(msg)
                if not pay:
                    s.cancel()
                    time.sleep(0.6)
                    continue
                isle = next((c for c in s.me()["zones"]["battlefield"] if c["name"] == "Island" and not c["tapped"]), None)
                if isle:
                    s.click_card(isle["id"])
                    time.sleep(0.5)
                    continue
            if mine and st.get("phase") == "MAIN1" and not st.get("stack") and st.get("asking"):
                return s, payments
            if st.get("asking") and ((st.get("prompt") or {}).get("ok") or {}).get("enabled"):
                s.ok()
                time.sleep(0.5)
                continue
            time.sleep(0.05)
        self.fail("never reached my Main 1")

    def assert_untap_step_left_them(self, s):
        self.assertTrue(self.card("battlefield", "Basalt Monolith")["tapped"], "the Monolith untapped in my untap step")

    def test_mana_vault_paid_in_my_upkeep_untaps_and_the_draw_step_does_no_damage(self):
        s, payments = self.through_my_upkeep(pay=True)
        self.assertTrue(payments and all("Mana Vault" in p for p in payments), payments)
        self.assertEqual([p for p in payments if "{4}" in p], payments[:1], "one payment of {4}, counting down as the Islands pay")
        self.assertFalse(self.card("battlefield", "Mana Vault")["tapped"], "Mana Vault should be untapped once paid for")
        self.assertEqual(s.me()["life"], 40)
        self.assertTrue(all(c["tapped"] for c in s.me()["zones"]["battlefield"] if c["name"] == "Island"), "the four Islands paid")
        self.assert_untap_step_left_them(s)
        self.assertEqual(s.checks, [])
        self.assertEqual(self.engine_problems(), [])

    def test_mana_vault_not_paid_stays_tapped_and_deals_one_damage(self):
        s, payments = self.through_my_upkeep(pay=False)
        self.assertEqual(len(payments), 1, payments)
        self.assertIn("Pay Mana Cost: {4}", payments[0])
        self.assertTrue(self.card("battlefield", "Mana Vault")["tapped"])
        self.assertEqual(s.me()["life"], 39, "the draw step's 1 damage")
        self.assertFalse(any(c["tapped"] for c in s.me()["zones"]["battlefield"] if c["name"] == "Island"),
                         "the Islands untapped in my untap step and paid nothing")
        self.assert_untap_step_left_them(s)
        self.assertEqual(s.checks, [])
        self.assertEqual(self.engine_problems(), [])


if __name__ == "__main__":
    unittest.main()
