# SPDX-License-Identifier: GPL-3.0-or-later
"""Task bug-sell-scroll-2 (Karl, 3 Nov 2026: the Adventure's Sell list didn't move with a finger, the pen or the arrow keys on his
Surface Pro 11): a window that shows what the computer really sends Manticore when you touch, write or type.

    python tools\\input_probe.py            (or, from an installed copy: Manticore-cli.exe can't run it - use a source copy)

It lists the last 20 input events, newest at the top: the event's type, where it was, the mouse button, whether SDL says it came
from a touch (touch=True on a mouse event), the finger's id and its 0-1 position for FINGER events, and the window's size. Nothing
is saved or sent anywhere. Esc or the window's close button ends it.

What to look for (Manticore's lists need one of these to work):
  - a finger drag shows MOUSEBUTTONDOWN / MOUSEMOTION ... / MOUSEBUTTONUP with touch=True, each just above a FINGER... line:
    SDL turns the finger into mouse events (Manticore uses those);
  - only FINGER... lines: SDL doesn't (Manticore now turns them into mouse events itself);
  - nothing at all: Windows keeps the touch for itself (a gesture) - the scroll bar and the arrow keys are the way then.
"""
import os
import sys
import time

KEEP = 20


def describe(ev, pygame, size):
    name = pygame.event.event_name(ev.type)
    d = ev.dict
    parts = [f"{time.strftime('%H:%M:%S')}", f"{name:<16}"]
    if "pos" in d:
        parts.append(f"pos={tuple(d['pos'])}")
    if "button" in d:
        parts.append(f"button={d['button']}")
    if "buttons" in d:
        parts.append(f"buttons={tuple(d['buttons'])}")
    if "touch" in d:
        parts.append(f"touch={d['touch']}")
    if "finger_id" in d:
        parts.append(f"finger={d.get('finger_id')} touch_id={d.get('touch_id')} at=({d.get('x', 0):.3f}, {d.get('y', 0):.3f})")
    if "key" in d:
        parts.append(f"key={pygame.key.name(d['key'])}")
    if "y" in d and name == "MouseWheel":
        parts.append(f"wheel x={d.get('x')} y={d.get('y')}")
    if "which" in d and name.startswith("Mouse"):
        parts.append(f"which={d['which']}")
    parts.append(f"window={size}")
    return "  ".join(parts)


def main():
    try:
        import pygame
    except ImportError:
        print("pygame isn't installed here: run this from the folder Manticore runs from (python tools\\input_probe.py).")
        return 1
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()          # as the game does (round 27e), so the sizes match the game's
        except Exception:
            pass
    pygame.init()
    info = pygame.display.Info()
    size = (max(800, int(info.current_w * 0.8)), max(600, int(info.current_h * 0.8))) if info.current_w > 0 else (1280, 800)
    screen = pygame.display.set_mode(size, pygame.RESIZABLE)
    pygame.display.set_caption("Manticore input probe - touch, write and type here; Esc ends it")
    font = pygame.font.SysFont("consolas,dejavusansmono,monospace", max(14, size[1] // 50))
    lines = []
    trail = []
    clock = pygame.time.Clock()
    running = True
    skip = {pygame.ACTIVEEVENT if hasattr(pygame, "ACTIVEEVENT") else -1, pygame.VIDEOEXPOSE}
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
                running = False
                continue
            if ev.type in skip or pygame.event.event_name(ev.type).startswith(("Window", "TextEditing", "AudioDevice")):
                if ev.type == getattr(pygame, "WINDOWRESIZED", -1):
                    size = screen.get_size()
                continue
            lines.insert(0, describe(ev, pygame, screen.get_size()))
            del lines[KEEP:]
            if "pos" in ev.dict:
                trail.append(tuple(ev.pos))
            elif "finger_id" in ev.dict:
                w, h = screen.get_size()
                trail.append((int(ev.x * w), int(ev.y * h)))
            del trail[:-200]
        screen.fill((16, 14, 22))
        y = 10
        hint = "Drag a finger, then the pen, up and down over this window; press Up, Down, PageUp, PageDown. Esc ends it."
        screen.blit(font.render(hint, True, (230, 200, 90)), (10, y))
        y += font.get_height() * 2
        for ln in lines:
            screen.blit(font.render(ln, True, (220, 220, 220)), (10, y))
            y += font.get_height() + 2
        for p in trail:
            pygame.draw.circle(screen, (90, 160, 230), p, 3)
        pygame.display.flip()
        clock.tick(60)
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())
