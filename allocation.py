# SPDX-License-Identifier: GPL-3.0-or-later
"""
allocation.py - the arithmetic behind "assign combat damage" and "divide N among these".

No pygame and no engine in here, so it is easy to test. The rules are the same ones the Java bridge checks (Assign.java):

  damage   rows are the blockers in damage order and, when the attacker may hit past them, the defender last.
           order=True   every earlier row needs lethal before a later row may get any.
           free=True    (an attacker that divides its damage as it likes) any split at all.
           The defender row always needs every blocker lethal first, unless free.
  divide   every row has a maximum, the total must be exactly spent, and with at_least_one every row gets 1 or more.

The window (forge_dialogs.AssignDialog) shows an Allocation and lets the player change it; answer() is what goes back to Forge.
"""


class Allocation:
    def __init__(self, mode, total, rows, order=False, free=False, at_least_one=False, may_skip=False):
        self.mode = mode                                   # "damage" or "divide"
        self.total = int(total)
        self.rows = list(rows)
        self.order = bool(order)
        self.free = bool(free)
        self.at_least_one = bool(at_least_one)
        self.may_skip = bool(may_skip)
        self.amounts = [0] * len(self.rows)
        self.reset()

    @classmethod
    def from_request(cls, request):
        return cls(request.get("mode", "divide"), request.get("total", 0), request.get("rows") or [], request.get("order", False),
                   request.get("free", False), request.get("at_least_one", False), request.get("may_skip", False))

    # ---- what each row allows -----------------------------------------------------------

    def lethal(self, i):
        return max(0, int(self.rows[i].get("lethal", 0) or 0)) if self.mode == "damage" else 0

    def maximum(self, i):
        if self.mode == "damage":
            return self.total
        m = self.rows[i].get("max")
        return self.total if m is None else max(0, int(m))

    def is_defender(self, i):
        return bool(self.rows[i].get("defender"))

    def minimum(self, i):
        return 1 if (self.mode == "divide" and self.at_least_one) else 0

    # ---- state ----------------------------------------------------------------------------

    @property
    def spent(self):
        return sum(self.amounts)

    @property
    def remaining(self):
        return self.total - self.spent

    def reset(self):
        """Damage starts as the usual split (lethal to each in order, the rest to the last); a divide starts empty (1 each if required)."""
        if self.mode == "damage":
            self.auto()
        else:
            self.amounts = [self.minimum(i) for i in range(len(self.rows))]

    def auto(self):
        n = len(self.rows)
        if n == 0:
            self.amounts = []
            return
        left = self.total
        if self.mode == "damage":
            out = [0] * n
            for i in range(n):
                if left <= 0:
                    break
                give = min(left, self.lethal(i))
                out[i] = give
                left -= give
            out[-1] += left
        else:
            out = [0] * n
            if self.at_least_one:
                for i in range(n):
                    if left <= 0:
                        break
                    out[i] = 1
                    left -= 1
            for i in range(n):
                if left <= 0:
                    break
                give = min(left, max(0, self.maximum(i) - out[i]))
                out[i] += give
                left -= give
            if left > 0:
                out[-1] += left
        self.amounts = out

    # ---- editing --------------------------------------------------------------------------

    def change(self, i, delta):
        """Add delta (may be negative) to row i as far as the row's limits and the points still free allow. Returns what was applied."""
        if not 0 <= i < len(self.rows):
            return 0
        cur = self.amounts[i]
        want = min(cur + delta, self.maximum(i), cur + max(0, self.remaining))
        want = max(want, self.minimum(i), 0)
        if want == cur:
            return 0
        self.amounts[i] = want
        return want - cur

    def to_lethal(self, i):
        """Give row i exactly its lethal damage when it has less (or a divide's maximum); with more, bring it back to lethal."""
        target = self.lethal(i) if self.mode == "damage" else self.maximum(i)
        return self.change(i, target - self.amounts[i])

    def to_rest(self, i):
        """Everything not yet assigned goes to row i (as far as its limits allow)."""
        return self.change(i, self.remaining)

    def clear(self, i):
        return self.change(i, -self.amounts[i])

    # ---- checking -------------------------------------------------------------------------

    def problem(self):
        """None when the split can be sent, otherwise a short sentence saying what to fix."""
        if self.remaining > 0:
            return f"{self.remaining} left to assign"
        if self.remaining < 0:
            return f"{-self.remaining} too many assigned"
        if self.mode == "divide":
            for i, a in enumerate(self.amounts):
                if a > self.maximum(i):
                    return f"{self.name(i)} can take at most {self.maximum(i)}"
                if self.at_least_one and a < 1:
                    return f"{self.name(i)} needs at least 1"
            return None
        if self.free:
            return None
        short = None                                        # the first row that has less than lethal
        for i, a in enumerate(self.amounts):
            if a > 0 and short is not None and (self.order or self.is_defender(i)):
                return f"{self.name(short)} needs lethal damage ({self.lethal(short)}) before {self.name(i)} gets any"
            if short is None and a < self.lethal(i):
                short = i
        return None

    @property
    def valid(self):
        return self.problem() is None

    def name(self, i):
        row = self.rows[i]
        card = row.get("card")
        if card:
            return card.get("name", "?")
        return row.get("name") or row.get("symbol") or "?"

    def answer(self):
        return list(self.amounts)

    def summary(self):
        """One line for logs and reports: 'Grizzly Bears 2, Player 3'."""
        return ", ".join(f"{self.name(i)} {a}" for i, a in enumerate(self.amounts))
