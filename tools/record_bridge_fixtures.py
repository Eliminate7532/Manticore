# SPDX-License-Identifier: GPL-3.0-or-later
"""Record the bridge fixtures in tests/fixtures/bridge (Round 27d): real games through the bridge, both directions, plus the
engine log. Needs Java 17+ and forge_runtime/ (like the live tests). Overwrites the fixtures it records - only re-record when
the bridge's messages change on purpose, and say so in the round notes.

    python tools/record_bridge_fixtures.py                 (all six)
    python tools/record_bridge_fixtures.py surveil_ok      (just one)
"""
import gzip, json, os, shutil, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import card_check as cc, forge_client as fc
from tests.test_round27c import play_spiteful_to_my_second_draw, prompt, names

OUT = "tests/fixtures/bridge"
KINNAN = "sample_decks/kinnan_nbc_moxfield_export.txt"

class Shim:
    def assertTrue(self, x, msg=None):
        assert x, msg

def recorded(name, faults, body, expected, about):
    rec = os.path.join(tempfile.gettempdir(), f"{name}.jsonl")
    real = fc.ForgeSession
    def make(*a, **k):
        k["record_path"] = rec
        return real(*a, **k)
    cc.fc.ForgeSession = make
    chk = cc.Checker(KINNAN, faults=faults)
    try:
        chk.start()
        body(chk)
        chk.pump(1.0)
        checks = [(c["q"], c["rule"]) for c in chk.s.checks]
        engine = chk.s.stderr_path
        chk.s.close()
        time.sleep(1.0)
        with open(rec, "rb") as f, gzip.open(os.path.join(OUT, name + ".jsonl.gz"), "wb", 9) as g:
            shutil.copyfileobj(f, g)
        shutil.copy(engine, os.path.join(OUT, name + ".engine.log"))
        meta = {"name": name, "about": about, "deck": os.path.basename(KINNAN), "seed": chk.seed, "faults": list(faults),
                "bridge_checks_seen": checks, "expected_fail_rules": expected, "recorded": time.strftime("%Y-%m-%d"),
                "forge": "3a74143", "bridge": "round 27d"}
        with open(os.path.join(OUT, name + ".json"), "w") as f:
            json.dump(meta, f, indent=1)
        print(name, "checks:", checks, "size:", os.path.getsize(os.path.join(OUT, name + ".jsonl.gz")))
    finally:
        cc.fc.ForgeSession = real
        chk.stop()

def spiteful(chk):
    play_spiteful_to_my_second_draw(Shim(), chk)
    print("  hand", len(chk.s.me()["zones"]["hand"]), "life", chk.s.me()["life"])

def pay(chk, tries=6):
    s = chk.s
    for _ in range(tries):
        s.poll(); m = prompt(s).get("message") or ""
        if "Pay Mana" in m: s.ok(); time.sleep(0.6)
        elif m.startswith("Priority") and s.state.get("stack"): s.ok(); time.sleep(0.8)
        else: time.sleep(0.4)

def cmdzone(answer):
    def body(chk):
        s = chk.s
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Swords to Plowshares;Brainstorm", "humanbattlefield=Forest;Island;Plains;Plains;Island",
                 "humanlibrary=Forest;Island;Island;Island;Island;Island", "humancommand=Kinnan, Bonder Prodigy|IsCommander",
                 "aihand=", "ailibrary=Forest;Forest;Forest;Forest", "aibattlefield=Grizzly Bears", "removesummoningsickness=true"]
        assert chk.apply(lines, ["Swords to Plowshares", "Brainstorm"])
        k = next(c for c in s.me()["zones"]["command"] if "Kinnan" in c["name"])
        s.click_card(k["id"]); time.sleep(0.5); pay(chk); chk.pump(1.5)
        k = next(c for c in s.me()["zones"]["battlefield"] if "Kinnan" in c["name"])
        sw = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Swords to Plowshares")
        s.click_card(sw["id"]); time.sleep(0.6); s.poll()
        s.click_card(k["id"]); time.sleep(0.6); s.poll()
        pay(chk, 5)
        end, asked = time.time() + 20, False
        while time.time() < end and not asked:
            s.poll(); asked = "command zone" in (prompt(s).get("message") or "").lower(); time.sleep(0.05)
        print("  asked:", asked)
        if asked:
            (s.ok if answer else s.cancel)()
        chk.pump(2.0)
        # pass priority once so the stream has a following "Priority:" state
        s.poll()
        if (prompt(s).get("message") or "").startswith("Priority"):
            s.ok(); chk.pump(1.5)
        print("  command", names(s, "command"), "exile", names(s, "exile"))
    return body

def surveil(chk):
    s = chk.s
    lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
             "humanhand=Raucous Theater;Brainstorm", "humanbattlefield=Swamp",
             "humanlibrary=Forest;Island;Swamp;Plains;Mountain", "aihand=", "ailibrary=Forest;Forest;Forest", "aibattlefield="]
    assert chk.apply(lines, ["Raucous Theater", "Brainstorm"])
    t = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Raucous Theater")
    s.click_card(t["id"]); time.sleep(0.6)
    end = time.time() + 30
    while time.time() < end:
        s.poll(); p = prompt(s)
        if "graveyard?" in (p.get("message") or ""): break
        if (p.get("message") or "").startswith("Priority") and s.state.get("stack"): s.ok(); time.sleep(0.4)
        time.sleep(0.1)
    print("  source:", (prompt(s).get("source") or {}).get("name"))
    s.cancel(); chk.pump(1.5)          # graveyard
    print("  graveyard", names(s, "graveyard"))

which = sys.argv[1:] or ["spiteful_ok", "spiteful_fault", "cmdzone_yes", "cmdzone_no", "cmdzone_fault", "surveil_ok"]
table = {
 "spiteful_ok": ((), spiteful, [], "Spiteful Visions cast on turn 3, played to my turn-5 main phase; both order questions answered in the order given"),
 "spiteful_fault": (("ORDER_IGNORES_SORTED",), spiteful, ["bridge_check"], "as spiteful_ok with the ORDER_IGNORES_SORTED fault: the reorder on my draw step loses both triggers"),
 "cmdzone_yes": ((), cmdzone(True), [], "Swords to Plowshares on my own Kinnan; Yes to the command zone"),
 "cmdzone_no": ((), cmdzone(False), [], "Swords to Plowshares on my own Kinnan; No (Kinnan stays in exile)"),
 "cmdzone_fault": (("NO_COMMANDER_VARIANT",), cmdzone(True), ["bridge_check", "commander_stranded"], "as cmdzone_yes with the NO_COMMANDER_VARIANT fault: no question, Kinnan stays in exile"),
 "surveil_ok": ((), surveil, [], "Raucous Theater enters, surveil 1 (Forest); answered Graveyard"),
}
for n in which:
    f, body, exp, about = table[n]
    recorded(n, f, body, exp, about)
