# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PEN1 (24 Oct 2026; Karl, on his Surface with a stylus: "Scroll bars like this are almost unusable with a stylus",
"It was impossible for me to scroll down ... with a stylus"): drag a list with the pen (or a finger, or the mouse) to scroll it.

A pen has no wheel, and a press on a list used to pick the row under it at once - so trying to drag the list picked a row
instead (Karl's "Choose a creature type" screenshot ended with "Selected: Incarnation"). Now a press on a list that scrolls
waits: if the pointer moves more than PAN_START it becomes a drag that moves the list and picks nothing; if it doesn't, the
click happens when the button goes up, at the place it went down. Lists that fit on screen, and every other place, still
act on the press, as before.

A view that can be dragged answers `pan_view(gui, pos)` with a PanView, or None when `pos` isn't on a list that scrolls now:
  PanView(get=..., put=...)      exact: put(get() - drag) in pixels (the list follows the pen)
  PanView(notch=..., step=px)    for views that only know the wheel: one wheel notch per `step` pixels of drag
"""

PAN_START = 12          # px at 100% text: a press that moves this far is a drag, not a click (a pen tap wobbles a little)


class PanView:
    def __init__(self, get=None, put=None, notch=None, step=40):
        self.get, self.put, self.notch, self.step = get, put, notch, max(1, int(step))


def threshold(fs):
    return int(PAN_START * max(1.0, fs))


class Panner:
    """The press being held over a list: decides between a click and a drag. Owned by the table (ForgeTable.pan)."""

    def __init__(self):
        self.press = None

    @property
    def holding(self):
        return self.press is not None

    @property
    def dragging(self):
        return self.press is not None and self.press["moved"]

    def begin(self, view, pos, fs):
        """A left press at `pos` over `view` (a PanView): nothing happens until it moves or is released."""
        self.press = {"view": view, "pos": tuple(pos), "start": view.get() if view.get else 0, "sent": 0,
                      "moved": False, "limit": threshold(fs)}

    def motion(self, pos):
        """The pointer moved with the button held. True when this press is (now) a drag."""
        p = self.press
        if p is None:
            return False
        dx, dy = pos[0] - p["pos"][0], pos[1] - p["pos"][1]
        if not p["moved"] and dx * dx + dy * dy >= p["limit"] * p["limit"]:
            p["moved"] = True
        if p["moved"]:
            v = p["view"]
            if v.put is not None:
                v.put(p["start"] - dy)                       # the list follows the pen: drag down = see what is above
            elif v.notch is not None:
                want = int(dy / v.step)                      # a wheel notch up (+1) shows what is above, like dragging down
                if want != p["sent"]:
                    v.notch(want - p["sent"])
                    p["sent"] = want
        return p["moved"]

    def release(self):
        """The button went up: the press position when it was a click (the caller clicks there now), else None."""
        p, self.press = self.press, None
        if p is None or p["moved"]:
            return None
        return p["pos"]

    def cancel(self):
        """The press ended where we couldn't see it (outside the window): no click, no drag."""
        self.press = None
