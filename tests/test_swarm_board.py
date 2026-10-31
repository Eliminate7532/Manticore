# SPDX-License-Identifier: GPL-3.0-or-later
"""tokens-0027: a ~1,000-token board at the table (Scute Swarm, soak game 24 / 44 of 26 Oct).

The table already stacks identical tokens ("x1023"), but it gave every ATTACKING or BLOCKING token a slot of its own, so an attack
with 1,023 Scute Swarm tokens drew 1,023 cards and 1,023 arrows to the defender's panel: 1.35 s a frame at 1920x1080 (0.7 s for my
own attack, 1.9 s at 4096x1949 @175%; checkpoint/table_2p_combat_*_before.json on branch claude/round-tokens). Now identical tokens
attacking the same defender unblocked, or blocking the same attacker, share a stack (lossless: each would have had the same line
to the same place). A blocked attacker, and a creature that isn't a token, keep a slot of their own, as before.

  StackTests   how many slots and how many drawn cards an attack of 300 tokens makes; what still stays apart
"""
import copy
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.forge_fake import load_state                      # noqa: E402
from tests.test_forge_table import frame, make_gui           # noqa: E402

N = 300


def token(cid, controller, **extra):
    c = {"id": cid, "name": "Scute Swarm", "controller": controller, "owner": controller, "token": True, "isCreature": True,
         "isLand": False, "power": 1, "toughness": 1, "type": "Creature - Insect", "text": "Landfall", "zone": "Battlefield",
         "tapped": False, "sick": False, "damage": 0, "attacking": False, "blocking": False, "selectable": False}
    c.update(extra)
    return c


def board(attackers=N, blocked=0, nontoken=0, my_blockers_same=0):
    """The opponent attacks me with `attackers` Scute Swarm tokens; the first `blocked` of them are blocked, one blocker each, by
    my tokens; `nontoken` of the attackers aren't tokens; `my_blockers_same` more of my tokens all block the last attacker."""
    st = copy.deepcopy(load_state("declare_blockers"))
    me = st["me"]
    opp = next(p for p in st["players"] if p["id"] != me)
    mine = next(p for p in st["players"] if p["id"] == me)
    st["combat"] = []
    for i in range(attackers):
        cid = 50000 + i
        c = token(cid, opp["id"], attacking=True, tapped=True)
        if i < nontoken:
            c["token"] = False
        opp["zones"]["battlefield"].append(c)
        blockers = []
        if i < blocked:
            b = token(60000 + i, me, blocking=True)
            mine["zones"]["battlefield"].append(b)
            blockers = [b["id"]]
        st["combat"].append({"card": cid, "defender": "p%d" % me, "blockers": blockers})
    if my_blockers_same:
        last = st["combat"][-1]
        for k in range(my_blockers_same):
            b = token(70000 + k, me, blocking=True)
            mine["zones"]["battlefield"].append(b)
            last["blockers"].append(b["id"])
    return st, opp, mine


def slots_of(gui, player):
    rows = gui.build_slots(player)
    return [s for row in rows for s in row]


def swarm_slots(gui, player):
    return [s for s in slots_of(gui, player) if s.cards[0]["name"] == "Scute Swarm"]


class StackTests(unittest.TestCase):
    def test_an_attack_of_300_tokens_is_one_stack_and_a_few_drawn_cards(self):
        st, opp, _mine = board()
        gui = make_gui(st, size=(1920, 1080))
        frame(gui)
        slots = swarm_slots(gui, gui.session.state["players"][[p["id"] for p in gui.session.state["players"]].index(opp["id"])])
        self.assertEqual(len(slots), 1, "300 identical tokens attacking me unblocked share one stack")
        self.assertEqual(len(slots[0].cards), N)
        drawn = [cid for cid in gui.card_rects if 50000 <= cid < 50000 + N]
        self.assertLessEqual(len(drawn), 4, "a stack draws at most 4 of its cards (and so at most 4 arrows)")
        self.assertGreaterEqual(len(drawn), 1, "the stack still has a card on screen for its attack arrow")

    def test_blocked_attackers_and_their_blockers_keep_their_own_slots(self):
        st, opp, mine = board(attackers=20, blocked=5)
        gui = make_gui(st, size=(1920, 1080))
        state = gui.session.state
        o = next(p for p in state["players"] if p["id"] == opp["id"])
        m = next(p for p in state["players"] if p["id"] == mine["id"])
        sizes = sorted(len(s.cards) for s in swarm_slots(gui, o))
        self.assertEqual(sizes, [1, 1, 1, 1, 1, 15], "5 blocked attackers alone, the 15 unblocked ones together")
        self.assertEqual(sorted(len(s.cards) for s in swarm_slots(gui, m)), [1, 1, 1, 1, 1],
                         "each blocker blocks a different attacker: no stack")

    def test_tokens_blocking_the_same_attacker_share_a_stack(self):
        st, opp, mine = board(attackers=3, my_blockers_same=6)
        gui = make_gui(st, size=(1920, 1080))
        m = next(p for p in gui.session.state["players"] if p["id"] == mine["id"])
        self.assertEqual([len(s.cards) for s in swarm_slots(gui, m)], [6])

    def test_attackers_that_are_not_tokens_are_never_stacked(self):
        st, opp, _mine = board(attackers=10, nontoken=4)
        gui = make_gui(st, size=(1920, 1080))
        o = next(p for p in gui.session.state["players"] if p["id"] == opp["id"])
        self.assertEqual(sorted(len(s.cards) for s in swarm_slots(gui, o)), [1, 1, 1, 1, 6])

    def test_tokens_attacking_different_defenders_are_apart(self):
        st, opp, mine = board(attackers=10)
        for a in st["combat"][:3]:
            a["defender"] = "p99"
        gui = make_gui(st, size=(1920, 1080))
        o = next(p for p in gui.session.state["players"] if p["id"] == opp["id"])
        self.assertEqual(sorted(len(s.cards) for s in swarm_slots(gui, o)), [3, 7])

    def test_a_click_on_the_stack_still_picks_an_attacker_i_can_block(self):
        st, opp, _mine = board(attackers=12)
        o = next(p for p in st["players"] if p["id"] == opp["id"])
        for c in o["zones"]["battlefield"]:
            if c["id"] >= 50000:
                c["highlight"] = True
        gui = make_gui(st, size=(1920, 1080))
        o = next(p for p in gui.session.state["players"] if p["id"] == opp["id"])
        stack = swarm_slots(gui, o)
        self.assertEqual(len(stack), 1)
        hits = [data for rect, kind, data in gui.hits if kind == "card" and data.get("card", {}).get("id", 0) >= 50000]
        self.assertTrue(hits, "the stack is clickable")


if __name__ == "__main__":
    unittest.main()
