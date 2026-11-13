# SPDX-License-Identifier: GPL-3.0-or-later
"""Time the sparring yard's practice duels on this computer (Round SPAR1). Developer tool; it never writes a save.

    python tools/rpg_spar.py                      10 duels of the Adventure save in slot 1 (else a new game's starting deck)
    python tools/rpg_spar.py --duels 40 --slot 2
    python tools/rpg_spar.py --fresh              a new game's starting deck, whatever saves there are

It plays exactly what Yardmaster Corin plays (rpg.spar.Sparring: your deck, level and gear against this region's regular foes,
Forge's AI on both seats, one Java start), on this thread, and prints each duel as it ends, the time Forge took to start, and the
practice duels per minute - so the yard's default number of duels (rpg/data/progression.json sparring.duels) can be set to what
takes a few minutes on this computer.
"""
import argparse
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)


def load_save(slot, fresh, seed):
    from rpg import cardpool
    from rpg import save as sv
    if not fresh:
        try:
            s, _note = sv.read(slot, pool=cardpool.load())
            return s, f"save slot {slot} ({s.name})"
        except (FileNotFoundError, sv.SaveError):
            pass
    from rpg import adventure, newgame
    f = newgame.NewGameFlow(seed=seed)
    f.set_name("Timing")
    for _ in range(newgame.PICKS):
        offer = f.offer()
        f.pick(next((h for h in offer if h.size == 20), offer[0]).code)
    game = adventure.Game.create(f, 1, tempfile.mkdtemp(prefix="manticore_spar_time_"))
    return game.save, "a new game's starting deck"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--duels", type=int, default=10)
    ap.add_argument("--slot", type=int, default=1)
    ap.add_argument("--fresh", action="store_true", help="a new game's starting deck instead of a save")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)
    from rpg import spar
    why = spar.tool_problem()
    if why:
        print(spar.NO_TOOL.format(what=why))
        return 2
    save, what = load_save(args.slot, args.fresh, args.seed)
    print(f"Sparring {args.duels} duels: {what}, deck '{save.deck()['name']}'"
          + (f", start effects {spar.start_spec(save)}" if spar.start_spec(save) else "") + ".")
    t0 = time.monotonic()
    marks = []

    class Timed(spar.ForgeEngine):
        def play(self, mine, theirs, seed, spec=""):
            res = super().play(mine, theirs, seed, spec)
            marks.append(time.monotonic() - t0)
            if res is not None:
                who = {"A": "won", "B": "lost"}.get(res.get("winner"), res.get("winner") or res.get("error"))
                print(f"  duel {len(marks):>3}: {who:<5} {res.get('turns', '?'):>3} turns  {(res.get('ms') or 0) / 1000:5.1f} s"
                      f"   (at {marks[-1]:6.1f} s)", flush=True)
            return res

    sp = spar.Sparring(save, n=args.duels, seed=args.seed, engine_factory=lambda folder, timeout=None: Timed(folder, timeout))
    sp.run_here()
    total = time.monotonic() - t0
    r = sp.result()
    print("\n".join(spar.result_lines(r)))
    if not marks:
        return 1
    ms = [m for m in r["ms"] if m]
    start = marks[0] - (ms[0] / 1000 if ms else 0)
    games = total - start
    print(f"Forge started in {start:.1f} s; {len(marks)} duels in {games:.1f} s after that"
          f" = {60 * len(marks) / games:.1f} duels a minute; {total / 60:.1f} minutes in all.")
    if games > 0:
        per = games / len(marks)
        for minutes in (2, 3, 5):
            print(f"  about {max(1, int((60 * minutes - start) / per))} duels fit in {minutes} minutes here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
