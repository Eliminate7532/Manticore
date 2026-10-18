# SPDX-License-Identifier: GPL-3.0-or-later
"""
rpg_screens.py - Round RPG8: the Adventure's screens (Karl, 13 Oct 2026, 22:30: "You could focus on refining the RPG mode", then
"First playable loop": main menu > Adventure > new game > walk the map > duel an enemy on the real table > gold and cards > the
town's shop > the deck editor > save).

One full-window dialog (as the cube draft's DraftFlow is), opened from the main menu's Adventure (the slot Continue left on 13 Oct
for it). Its stages:
  slots   the save slots (numbers.json save_slots): Continue a game, or start one in an empty slot (a used slot asks first)
  name    your name (rpg.save.check_name's rules)
  pick    three Jumpstart half-decks, one at a time (rpg.newgame: three offered at once, "Show others" for more, Back)
  ready   the 60 cards they make; Begin writes the save and opens the start town
  map     the Zelda-style room view (rpg.explore + rpg_map_view, Rounds RPG5-RPG7), walked with the arrows or WASD; Esc is the
          menu (Back to the map / Your deck / Save and leave). Walking into a foe asks first (who, what a win gives, what a loss
          costs), then Fight plays the duel on the real table (ForgeTable.start_adventure_duel). A town's gate opens the town.
  town    the town: Singles, Packs, Basic lands (Round RPG7: 1 gold each), Sell (rpg.shop); Your deck; Leave town
  pack    the cards of the pack you just opened (the new ones marked)
  deck    the deck editor (rpg.adventure.DeckEditor): your cards on the left (click: one more in the deck; right-click: one
          less), the deck on the right (click a line: one less), colour and type filters and a search (just type)
Small windows over a stage (self.sub): the fight question, a result (after a duel, or a note on loading), the map's menu, and
"replace the game in this slot?".

Everything is saved as it happens: a new game when Begin is pressed, a town when you enter it, every purchase and sale at once,
the deck when you leave the editor, your place on the map when you open the menu, a town or a fight, and when you leave.
The rules and numbers are rpg/'s (no pygame there); this module draws and routes clicks. Every colour is a gfx.py token.
"""
import time

import pygame

import art_loader
import crashlog
import forge_dialogs as dlg
import gfx
import rpg_map_view
from gfx import DIM, GOLD, GREEN, ORANGE, RED, TEXT, clip_text, draw_text, round_rect, wrap_text
from rpg import adventure as adv
from rpg import cardpool
from rpg import duel as rduel
from rpg import explore, newgame, products
from rpg import save as rsave
from rpg import shop as rshop

CARD_ASPECT = 488 / 680
TITLE = "Adventure"
INTRO = ("Walk the land, duel whatever blocks your way, and build your deck from the cards you win and buy. Every duel is a "
         "60-card game at 20 life against the AI, on the same table as your other games.")
PICK_INTRO = ("Three Jumpstart half-decks make your first 60 cards, in at most two colours. Click the one you want; Show others "
              "offers three more.")
MAP_KEYS = ("Arrows or WASD: walk", "Esc: menu")
WELCOME = ("Your 60 cards are your deck. This town's shop sells single cards, packs and basic lands, and buys cards back. "
           "Leave town to explore; walking into a foe asks before a duel starts.")
TABS = (("singles", "Singles"), ("packs", "Packs"), ("lands", "Basic lands"), ("sell", "Sell"))
GROUP_BUTTONS = ("Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Land")
RARITY = {"M": 0, "S": 0, "R": 1, "U": 2, "C": 3}
MAX_DT = 0.1                     # seconds: a frame that took longer walks this far at most (a stall never throws you into a wall)
BASIC_STEPS = [1, 5, 10]          # a list: a 3-number tuple reads as a colour to test_ad1's guard
WHEEL_ROWS = 3                   # rows a wheel notch scrolls a list


def card_picture(gui, name, card, w, h):
    """A card's picture at w x h (the art loader's, fetched in the background); a drawn stand-in until it is here."""
    art = getattr(gui, "art", None)
    radius = max(3, int(w * 0.05))
    key = ("rpg", name, w, h)
    hit = gfx.recall(key)
    if hit is not None:
        return hit
    big = h > art_loader.SOURCE_SIZE[1] + 20
    img = None
    if art is not None:
        try:
            if big:
                pic = art.preview(name, w, h)
                if pic is not None:
                    return gfx.remember(key, gfx.rounded_image(pic, w, h, radius))
            img = art.get(name)
        except Exception:                                    # a picture must never stop the Adventure
            img = None
    if img is not None:
        surf = gfx.rounded_image(img, w, h, radius)
        return surf if big else gfx.remember(key, surf)
    colours = [c for c in (card.colours if card is not None else "") if c in "WUBRG"]
    return gfx.card_face({"name": name, "cost": "", "type": card.type_line if card is not None else "", "text": "",
                          "colors": colours}, w, h, radius)


def face_of(half, pool):
    """The card that stands for a half-deck: its rarest non-basic card, the costliest of those, the first on a tie."""
    best, key = None, None
    for i, (n, _k) in enumerate(half.cards):
        card = pool.get(n)
        if card is None or card.is_basic:
            continue
        k = (RARITY.get(card.rarity, 4), -card.mana_value, i)
        if key is None or k < key:
            best, key = card.name, k
    return best


class AdventureFlow(dlg.Dialog):
    """The Adventure window. folder: where the saves are (tests); slot: open that game at once (coming back from a duel), with
    the duel's outcome (rpg.duel.finish's dict) or an error to say; pool: a card pool already loaded (tests)."""

    def __init__(self, gui, folder=None, slot=None, outcome=None, error=None, pool=None, retry=None):
        super().__init__()
        import forge_settings as fset
        self.folder, self.pool = folder, pool
        self.stage = "slots"
        self.sub = None                  # None | "fight" | "result" | "pause" | "confirm"
        self.message = None              # (text, colour) under the stage's content
        self.game = None                 # adv.Game once a slot is open
        self.flow = None                 # newgame.NewGameFlow while a new game is being made
        self.slot = None
        self.slots = []
        self.rows = []                   # (rect, kind, data) drawn this frame, for the mouse
        self.scroll = {}                 # list key -> first row shown
        self.hover = None                # (name, card) under the mouse - shown big
        self.explorer = None
        self.views = {}
        self.camera, self._cam_key = None, None
        self.last = None                 # the last update's clock (walking)
        self.tab = "singles"
        self.shop = None
        self.town = None
        self.editor = None
        self.deck_back = "map"
        self.opened = None               # (set name, [card dicts]) - the pack just opened
        self.fight = None                # the rpg.explore.Trigger walked into
        self.result = None               # (title, [lines], colour)
        self.confirm = None              # (title, [lines], yes label, action)
        self.field = fset.TextField("Name", "", multiline=False, limit=rsave.NAME_MAX, lines=1, hint="Your name")
        self._prefetched = set()
        self.retry = None                # a duel's result the table couldn't write: tried again (open_slot, write)
        if slot is not None:
            self.open_slot(gui, slot, outcome=outcome, error=error, retry=retry)
        else:
            self.refresh_slots()

    # ---- moving between stages -------------------------------------------------------------------------------------------
    def say(self, text, colour=DIM):
        self.message = (text, colour)

    def refresh_slots(self):
        try:
            self.slots = adv.slot_rows(self.folder)
        except Exception as e:                               # numbers.json unreadable: say so, never crash the menu
            self.slots = []
            self.say(f"The Adventure's data couldn't be read: {e}", RED)

    def set_stage(self, stage):
        self.stage = stage
        self.message = None
        self.hover = None
        self.last = None

    def open_slot(self, gui, slot, outcome=None, error=None, retry=None):
        if retry:                                            # the table couldn't write the duel's result: once more, now
            try:
                outcome, error = adv.finish_duel(slot, retry, self.folder, pool=self.pool), None
                if outcome is None:
                    error = "The save had no duel going on, so nothing was won or lost."
                retry = None
            except Exception as e:
                error = (f"The duel's result ({retry}) couldn't be saved yet: {e}. It is tried again each time the game is "
                         "saved; it counts once it is written.")
        try:
            loader = adv.Game.load_raw if retry else adv.Game.load      # an unsaved result keeps its duel marked
            game, notes = loader(slot, self.folder, pool=self.pool)
        except FileNotFoundError:
            self.refresh_slots()
            self.say(f"Slot {slot} is empty.", ORANGE)
            return False
        except (rsave.SaveError, ValueError, OSError) as e:
            self.refresh_slots()
            self.say(str(e), RED)
            return False
        self.game, self.slot, self.pool = game, slot, game.pool
        self.retry = retry
        self.explorer = None
        w = game.save.world
        inside = w.get("in_town") if w.get("in_town") in game.world.towns else None
        if inside or (w.get("map") in game.world.towns and w.get("x") is None):
            self.enter_town(gui, inside or w["map"], announce=False)
        else:
            self.enter_map(gui)
        if error:
            self.result = ("The duel", [error], RED)
            self.sub = "result"
        elif outcome:
            won = outcome.get("result") == "won"
            title = {"won": "Victory", "lost": "Defeat", "conceded": "Defeat", "draw": "A draw"}.get(outcome.get("result"), "")
            self.result = (title, adv.outcome_lines(outcome, game.world), GREEN if won else (RED if title == "Defeat" else GOLD))
            self.sub = "result"
        elif notes:
            self.result = ("Welcome back", notes, ORANGE)
            self.sub = "result"
        return True

    def enter_map(self, gui):
        g = self.game
        if self.explorer is None:
            try:
                self.explorer = explore.Explorer(g.save, start_town=g.world.start_town)
            except Exception as e:                           # a damaged map, PyTMX missing (TmxError), ...: said, never raised
                crashlog.note(f"Adventure: the map couldn't be opened: {type(e).__name__}: {e}")
                self.set_stage("town" if self.town else "slots")
                if self.stage == "slots":
                    self.refresh_slots()
                self.say(f"The map couldn't be opened: {e}", RED)
                return
        self.set_stage("map")
        self.town = None
        self.shop = None

    def enter_town(self, gui, town_id, announce=True):
        g = self.game
        try:
            rduel.visit_town(g.save, town_id, g.world)
        except KeyError:
            self.say(f"There is no town {town_id!r} in the Adventure's data.", RED)
            return
        g.save.world.update(map=town_id, x=None, y=None) if self.explorer is None else self.explorer.store()
        g.save.world["in_town"] = town_id                    # a game saved in a town opens there again
        self.town = town_id
        self.shop = rshop.Shop(g.save, town_id, g.world, g.numbers, g.pool, g.tiers(), g.lists())
        self.write()
        self.set_stage("town")
        self.tab = "singles"
        if announce:
            gui.play_cue("ui.dialog_open")

    def leave_town(self, gui):
        g = self.game
        try:
            if self.explorer is None:
                self.explorer = explore.Explorer(g.save, start_town=g.world.start_town)
            self.explorer.leave_town(self.town)
        except Exception as e:                               # a damaged map, PyTMX missing (TmxError), ...: said, never raised
            crashlog.note(f"Adventure: the map couldn't be opened: {type(e).__name__}: {e}")
            self.say(f"The map couldn't be opened: {e}  (Main menu, top right, saves and leaves.)", RED)
            return
        g.save.world.pop("in_town", None)
        self.write()
        self.enter_map(gui)

    def write(self):
        if self.game is None:
            return True
        if self.retry and self.game.save.pending_duel is not None:    # the duel's result first, now that the disk may take it
            try:
                out = adv.finish_loaded(self.game, self.retry)
                self.retry = None
                self.result = ("The duel", ["Your last duel's result is saved now."] + adv.outcome_lines(out, self.game.world), GOLD)
                self.sub = self.sub or "result"
            except Exception as e:
                crashlog.note(f"Adventure: the duel's result still couldn't be saved: {type(e).__name__}: {e}")
        try:
            self.game.write()
            return True
        except OSError as e:
            crashlog.note(f"Adventure: the save couldn't be written: {type(e).__name__}: {e}")
            self.say(f"Your game couldn't be saved: {e}", RED)
            return False

    def adventure_close(self):
        """The program is closing with this window open: where you stand is saved."""
        if self.stage == "map" and self.explorer is not None:
            self.explorer.store()
        self.write()

    def leave(self, gui):
        """Back to the main menu (the game is saved first)."""
        if self.stage == "map" and self.explorer is not None:
            self.explorer.store()
        self.write()
        self.done = True
        if hasattr(gui, "open_title"):
            gui.open_title()
            if getattr(gui, "boot", None) is not None:
                gui.boot.to_menu()

    # ---- a new game ----------------------------------------------------------------------------------------------------------
    def new_game(self, gui, slot, sure=False):
        row = next((r for r in self.slots if r["slot"] == slot), None)
        if row and row["used"] and not sure:
            who = row.get("name") or "the game"
            self.confirm = (f"Replace slot {slot}?", [f"{who}'s game in slot {slot} will be replaced by a new one. This can't be undone."],
                            "Replace it", ("new", slot))
            self.sub = "confirm"
            return
        try:
            self.flow = newgame.NewGameFlow(pool=self.pool)
        except Exception as e:
            self.say(f"A new game can't be started: {e}", RED)
            return
        self.slot = slot
        self.field.text = ""
        self.set_stage("name")

    def name_next(self, gui):
        why = self.flow.set_name(self.field.text)
        if why:
            self.say(why, ORANGE)
            gui.play_cue("ui.error")
            return
        try:
            self.flow.offer()
        except Exception as e:                               # Forge's Jumpstart files missing
            self.flow.undo()
            self.say(f"The starting decks can't be read: {e}", RED)
            return
        self.set_stage("pick")

    def pick(self, gui, code):
        try:
            self.flow.pick(code)
        except ValueError as e:
            self.say(str(e), RED)
            return
        gui.play_cue("card.pickup")
        self.set_stage("ready" if self.flow.step == "done" else "pick")

    def back_new(self, gui):
        if self.stage == "name":
            self.flow = None
            self.refresh_slots()
            self.set_stage("slots")
            return
        self.flow.undo()
        self.set_stage("name" if self.flow.step == "name" else "pick")
        if self.stage == "name":
            self.field.text = ""

    def begin(self, gui):
        try:
            import version
            forge = version.forge_build() or ""
        except Exception:
            forge = ""
        try:
            self.game = adv.Game.create(self.flow, self.slot, self.folder, forge=forge)
        except (rsave.SaveError, OSError, ValueError) as e:
            self.say(f"The new game couldn't be saved: {e}", RED)
            return
        self.pool = self.game.pool
        self.flow = None
        self.explorer = None
        gui.play_cue("ui.menu_select")
        self.enter_town(gui, self.game.world.start_town, announce=False)
        self.result = (f"Welcome, {self.game.save.name}", [WELCOME], GOLD)
        self.sub = "result"

    # ---- walking ---------------------------------------------------------------------------------------------------------------
    def soundscape(self):
        """(ambience cue, music mode) while this window is open (ForgeTable.soundscape asks): the title's on the slots and the new
        game's screens; on the map the region's ambience and the game's playlist (a dungeon is the graveyard's); a town and the
        deck editor the deck screen's track."""
        if self.stage == "map" and self.explorer is not None:
            return ("amb.graveyard" if self.explorer.area.dungeon else "amb.plain"), "game"
        if self.stage in ("town", "deck", "pack"):
            return "amb.cathedral", "deck"
        return "amb.title", "title"

    def walk_tick(self, gui, now, pressed=None):
        """Every tick of the table's loop: walk on the map. pressed: (dx, dy) instead of the keyboard (tests)."""
        if self.stage != "map" or self.sub is not None or self.explorer is None or getattr(gui, "overlay", None) is not None:
            self.last = None                                 # nothing walks behind a window (F8's bug report, the cog)
            return
        dt = 0.0 if self.last is None else min(MAX_DT, max(0.0, now - self.last))
        self.last = now
        if pressed is None:
            try:
                k = pygame.key.get_pressed()
                dx = (k[pygame.K_RIGHT] or k[pygame.K_d]) - (k[pygame.K_LEFT] or k[pygame.K_a])
                dy = (k[pygame.K_DOWN] or k[pygame.K_s]) - (k[pygame.K_UP] or k[pygame.K_w])
            except pygame.error:
                dx = dy = 0
        else:
            dx, dy = pressed
        if not dt:
            return
        event = self.explorer.step(dx, dy, dt)
        if event is None:
            return
        kind, what = event
        if kind == "town":
            self.enter_town(gui, what)
        elif kind == "encounter":
            self.explorer.store()
            self.write()
            self.fight = what
            self.sub = "fight"
            gui.play_cue("ui.dialog_open")
        elif kind == "map":
            self.say(f"{self.explorer.area.title}", GOLD)
            self.write()

    def fight_go(self, gui):
        trigger, self.fight = self.fight, None
        self.sub = None
        try:
            duel = adv.start_duel(self.game, trigger)
        except rduel.DuelError as e:
            self.result = ("No duel", [str(e)], ORANGE)
            self.sub = "result"
            gui.play_cue("ui.error")
            return
        except OSError as e:
            self.game.save.pending_duel = None               # never left marked in memory (every later Fight would refuse)
            self.result = ("No duel", [f"Your game couldn't be saved, so the duel didn't start: {e}"], RED)
            self.sub = "result"
            return
        start = getattr(gui, "start_adventure_duel", None)
        problem = start(self, duel) if start else "This copy can't start duels."
        if problem:
            adv.cancel_duel(self.game)
            self.result = ("No duel", [problem], RED)
            self.sub = "result"

    # ---- the deck editor ---------------------------------------------------------------------------------------------------------
    def open_deck(self, gui, back):
        if self.stage == "map" and self.explorer is not None:
            self.explorer.store()
        self.editor = adv.DeckEditor(self.game)
        self.deck_back = back
        self.sub = None
        self.set_stage("deck")

    def deck_done(self, gui, keep=True):
        e = self.editor
        if keep and e is not None and e.changed:
            try:
                left = e.apply()
            except OSError as err:
                self.say(f"Your deck couldn't be saved: {err}", RED)
                return
        else:
            left = self.game.deck_problems()
        self.editor = None
        back = self.deck_back
        if back == "town" and self.town:
            self.stage = "town"
            self.message = None
        else:
            self.enter_map(gui)
        if left:
            self.say("Your deck can't be played yet: " + left[0].text, ORANGE)

    # ---- the shop -----------------------------------------------------------------------------------------------------------------
    def shop_do(self, gui, what, *args):
        s = self.shop
        try:
            if what == "buy":
                o = s.buy_single(args[0])
                self.say(f"Bought {o.name} for {o.price} gold.", GREEN)
            elif what == "basic":
                cost = s.buy_basic(args[0], int(args[1]))
                self.say(f"Bought {args[1]} {args[0]} for {cost} gold.", GREEN)
            elif what == "sell":
                got = s.sell(args[0], 1)
                self.say(f"Sold one {args[0]} for {got} gold.", GREEN)
            elif what == "pack":
                cards = s.buy_pack(args[0])
                name = next((n for c, n, _p in s.packs() if c == args[0]), args[0])
                self.opened = (name, cards)
                self.set_stage("pack")
        except rshop.ShopError as e:
            self.say(str(e), ORANGE)
            gui.play_cue("ui.error")
            return
        self.write()
        gui.play_cue("card.pickup")

    # ---- input --------------------------------------------------------------------------------------------------------------------
    def press(self, gui, name):
        gui.play_cue("ui.click")
        head, _sep, rest = name.partition(":")
        if self.sub is not None:
            self.press_sub(gui, name)
            return
        if head == "slot":
            self.open_slot(gui, int(rest))
        elif head == "new":
            self.new_game(gui, int(rest))
        elif name == "menu":
            self.leave(gui)
        elif name == "next":
            self.name_next(gui)
        elif name == "back":
            self.back_new(gui)
        elif name == "reroll":
            self.flow.reroll()
        elif head == "pick":
            self.pick(gui, rest)
        elif name == "begin":
            self.begin(gui)
        elif name == "pause":
            self.explorer.store()
            self.write()
            self.sub = "pause"
        elif head == "tab":
            self.tab = rest
            self.message = None
        elif name == "leave_town":
            self.leave_town(gui)
        elif name == "town_deck":
            self.open_deck(gui, "town")
        elif head == "buy":
            self.shop_do(gui, "buy", rest)
        elif head == "pack":
            self.shop_do(gui, "pack", rest)
        elif head == "basic":
            land, _s, k = rest.rpartition(":")
            self.shop_do(gui, "basic", land, k)
        elif head == "sell":
            self.shop_do(gui, "sell", rest)
        elif name == "pack_done":
            self.opened = None
            self.stage = "town"
            self.message = None
        elif name == "deck_done":
            self.deck_done(gui, True)
        elif name == "deck_cancel":
            self.deck_done(gui, False)
        elif head == "colour":
            self.editor.toggle_colour(rest)
            self.scroll["deckpool"] = 0
        elif head == "group":
            self.editor.set_group(rest)
            self.scroll["deckpool"] = 0
        elif name == "clear_search":
            self.editor.search = ""
            self.scroll["deckpool"] = 0

    def press_sub(self, gui, name):
        sub = self.sub
        if sub == "fight":
            if name == "fight":
                self.fight_go(gui)
            else:
                self.fight = None
                self.sub = None
        elif sub == "result":
            self.result = None
            self.sub = None
        elif sub == "pause":
            self.sub = None
            if name == "deck":
                self.open_deck(gui, "map")
            elif name == "leave":
                self.leave(gui)
        elif sub == "confirm":
            action, self.confirm = (self.confirm or (None, None, None, None))[3], None
            self.sub = None
            if name == "yes" and action and action[0] == "new":
                self.new_game(gui, action[1], sure=True)

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name:
            self.press(gui, name)
            return
        if self.sub is not None:
            return
        for rect, kind, data in reversed(self.rows):
            if not rect.collidepoint(pos):
                continue
            if kind == "pick" and button == 1:
                self.pick(gui, data)
            elif kind == "pool":
                e = self.editor
                if button == 3:
                    if e.remove(data):
                        gui.play_cue("card.pickup")
                elif e.add(data):
                    self.message = None
                    gui.play_cue("card.pickup")
                else:
                    self.say(e.why_not(data) or "That card can't go in.", ORANGE)
                    gui.play_cue("ui.error")
            elif kind == "deckline":
                if self.editor.remove(data):
                    gui.play_cue("card.pickup")
            return

    def key(self, gui, ev):
        k = ev.key
        enter = k in (pygame.K_RETURN, pygame.K_KP_ENTER)
        esc = k == pygame.K_ESCAPE
        if self.sub is not None:
            if esc:
                self.press_sub(gui, "no")
            elif enter:
                self.press_sub(gui, {"fight": "fight", "confirm": "yes"}.get(self.sub, "ok"))
            return
        st = self.stage
        if st == "map":
            if esc:
                self.press(gui, "pause")
            return
        if st == "slots":
            if esc:
                self.leave(gui)
            return
        if st == "name":
            if esc:
                self.back_new(gui)
            elif enter:
                self.name_next(gui)
            else:
                import forge_menu as fmenu
                self.field.key(ev, fmenu.read_clipboard)
            return
        if st in ("pick", "ready"):
            if esc:
                self.back_new(gui)
            elif enter and st == "ready":
                self.begin(gui)
            return
        if st == "town":
            if esc:
                self.leave_town(gui)
            return
        if st == "pack":
            if esc or enter:
                self.press(gui, "pack_done")
            return
        if st == "deck":
            e = self.editor
            if esc:
                if e.search:
                    e.search = ""
                else:
                    self.deck_done(gui, True)
            elif enter:
                self.deck_done(gui, True)
            elif k == pygame.K_BACKSPACE:
                e.search = e.search[:-1]
                self.scroll["deckpool"] = 0
            elif ev.unicode and ev.unicode.isprintable() and not (getattr(ev, "mod", 0) & pygame.KMOD_CTRL):
                e.search = (e.search + ev.unicode)[:40]
                self.scroll["deckpool"] = 0

    def wheel(self, gui, dy):
        key = {"town": "town:" + self.tab, "deck": "deckpool", "pick": None, "ready": None}.get(self.stage)
        if self.stage == "deck" and getattr(self, "decklist_rect", None) is not None and self.decklist_rect.collidepoint(gui.mouse):
            key = "decklist"
        if key:
            self.scroll[key] = max(0, self.scroll.get(key, 0) - dy * WHEEL_ROWS)

    # ---- drawing --------------------------------------------------------------------------------------------------------------
    def draw(self, gui):
        L = gui.L
        self.rows = []
        self.hover = None
        self.buttons = []
        fs = max(1.0, L.fs)
        self.fs = fs
        self.pad = int(16 * fs)
        self.bh = int(max(36, 42 * fs))
        self.gap = int(10 * fs)
        if self.stage == "map" and self.explorer is not None:
            self.draw_map(gui)
        else:
            gui.screen.fill(gfx.BACKDROP)
            rect = self.panel(gui, L.W - 40, L.H - 40, real=True)
            getattr(self, "draw_" + self.stage)(gui, rect)
            if self.hover is not None and self.sub is None:
                self.draw_big(gui, rect)
        if self.sub is not None:
            self.draw_sub(gui)

    def prefetch(self, gui, names):
        art = getattr(gui, "art", None)
        new = {n for n in names if n} - self._prefetched
        if art is None or not new:
            return
        self._prefetched |= new
        try:
            art.add_names(new)
        except Exception:
            pass

    def header(self, gui, rect, title, sub=None):
        scr, pad = gui.screen, self.pad
        tf, small = gui.font("title", True), gui.font("small")
        draw_text(scr, clip_text(title, tf, rect.w - 2 * pad), rect.x + pad, rect.y + pad, tf, GOLD)
        y = rect.y + pad + tf.get_height() + int(4 * self.fs)
        for line in ([sub] if isinstance(sub, str) else (sub or [])):
            for ln in wrap_text(line, small, rect.w - 2 * pad)[:3]:
                draw_text(scr, ln, rect.x + pad, y, small, DIM)
                y += small.get_height()
        return y + int(8 * self.fs)

    def footer(self, gui, rect, left=(), right=()):
        """Buttons along the bottom: `left` from the left edge, `right` from the right edge ((label, name, enabled, primary)).
        Returns the footer's top."""
        pad, bh = self.pad, self.bh
        y = rect.bottom - pad - bh
        x = rect.x + pad
        for label, name, on, primary in left:
            w = gui.button_width(label)
            self.button(gui, pygame.Rect(x, y, w, bh), label, name, on, primary)
            x += w + self.gap
        x = rect.right - pad
        for label, name, on, primary in right:
            w = gui.button_width(label)
            x -= w
            self.button(gui, pygame.Rect(x, y, w, bh), label, name, on, primary)
            x -= self.gap
        return y

    def draw_message(self, gui, x, y, w):
        if self.message:
            small = gui.font("small", True)
            text, colour = self.message
            for ln in wrap_text(text, small, w)[:2]:
                draw_text(gui.screen, ln, x, y, small, colour)
                y += small.get_height()

    def draw_big(self, gui, rect):
        """The card under the mouse, big, on the side of the window away from it."""
        name, card = self.hover
        h = int(rect.h * 0.55)
        w = int(h * CARD_ASPECT)
        x = rect.x + self.pad if gui.mouse[0] > rect.centerx else rect.right - self.pad - w
        r = pygame.Rect(x, rect.y + int(rect.h * 0.2), w, h)
        gfx.shadowed(gui.screen, r, radius=max(4, int(w * 0.05)))
        gui.screen.blit(card_picture(gui, name, card, w, h), r)

    def list_rows(self, gui, area, key, count, row_h):
        """The rows of a scrolling list that fit `area`: [(index, rect)]; the scroll is kept in whole rows."""
        fit = max(1, area.h // row_h)
        top = max(0, min(self.scroll.get(key, 0), max(0, count - fit)))
        self.scroll[key] = top
        out = []
        for j, i in enumerate(range(top, min(count, top + fit))):
            out.append((i, pygame.Rect(area.x, area.y + j * row_h, area.w, row_h)))
        if count > fit:
            tiny = gui.font("tiny")
            draw_text(gui.screen, f"{top + 1}-{min(count, top + fit)} of {count}  (wheel to scroll)", area.right, area.y - 2,
                      tiny, DIM, "bottomright")
        return out

    # slots
    def draw_slots(self, gui, rect):
        scr, pad, fs = gui.screen, self.pad, self.fs
        y = self.header(gui, rect, TITLE, INTRO)
        bold, small = gui.font("body", True), gui.font("small")
        bottom = rect.bottom - pad - self.bh - self.gap
        n = max(1, len(self.slots))
        box_h = max(int(64 * fs), min(int(110 * fs), (bottom - y - self.gap * (n - 1) - 2 * small.get_height()) // n))
        for row in self.slots:
            box = pygame.Rect(rect.x + pad, y, rect.w - 2 * pad, box_h)
            round_rect(scr, box, gfx.PANEL, 8, 1, gfx.PANEL_EDGE)
            lines_h = bold.get_height() + small.get_height() * (2 if row.get("problem") else 1) if row["used"] else bold.get_height()
            tx, ty = box.x + pad, box.y + max(4, (box.h - lines_h) // 2)
            bw = max(gui.button_width("Continue"), gui.button_width("New game"))
            bh = min(self.bh, box.h - 8)
            btn = pygame.Rect(box.right - pad - bw, box.centery - bh // 2, bw, bh)
            room = btn.x - tx - pad
            if row["used"] and row.get("name"):
                when = time.strftime("%d %b %H:%M", time.localtime(row["updated"])) if row.get("updated") else ""
                draw_text(scr, clip_text(f"Slot {row['slot']}  -  {row['name']}", bold, room), tx, ty, bold, GOLD)
                wins = row.get("wins") or 0
                line = f"{row['gold']} gold   ·   {wins} duel{'s' if wins != 1 else ''} won   ·   last played {when}"
                draw_text(scr, clip_text(line, small, room), tx, ty + bold.get_height(), small, DIM)
                if row.get("problem"):
                    draw_text(scr, clip_text(row["problem"], small, room), tx, ty + bold.get_height() + small.get_height(),
                              small, ORANGE)
                self.button(gui, btn, "Continue", f"slot:{row['slot']}", True, True)
                nb = gui.button_width("New game")
                if btn.x - nb - self.gap > tx + room // 2:
                    self.button(gui, pygame.Rect(btn.x - nb - self.gap, btn.y, nb, bh), "New game", f"new:{row['slot']}")
            elif row["used"]:
                draw_text(scr, clip_text(f"Slot {row['slot']}  -  can't be read", bold, room), tx, ty, bold, RED)
                draw_text(scr, clip_text(row.get("problem") or "", small, room), tx, ty + bold.get_height(), small, ORANGE)
                self.button(gui, btn, "New game", f"new:{row['slot']}")
            else:
                draw_text(scr, clip_text(f"Slot {row['slot']}  -  empty", bold, room), tx, ty, bold, DIM)
                self.button(gui, btn, "New game", f"new:{row['slot']}", True, True)
            y += box_h + self.gap
        self.draw_message(gui, rect.x + pad, y, rect.w - 2 * pad)
        self.footer(gui, rect, left=[("Main menu", "menu", True, False)])

    # name
    def draw_name(self, gui, rect):
        pad, fs = self.pad, self.fs
        y = self.header(gui, rect, f"A new adventure  -  slot {self.slot}", "What should the game call you? (Up to "
                        f"{rsave.NAME_MAX} letters; only shown, never a file name.)")
        body = gui.font("body")
        fw = min(rect.w - 2 * pad, int(520 * fs))
        box = pygame.Rect(rect.x + pad, y + self.gap, fw, body.get_height() + 14)
        self.field.draw(gui, box, True)
        self.draw_message(gui, rect.x + pad, box.bottom + self.gap, rect.w - 2 * pad)
        self.footer(gui, rect, left=[("Back", "back", True, False)], right=[("Next", "next", True, True)])

    # pick
    def draw_pick(self, gui, rect):
        scr, pad, fs = gui.screen, self.pad, self.fs
        f = self.flow
        y = self.header(gui, rect, f"Your starting deck  -  half-deck {len(f.picks) + 1} of {newgame.PICKS}", PICK_INTRO)
        foot = self.footer(gui, rect, left=[("Back", "back", True, False)], right=[("Show others", "reroll", True, False)])
        side_w = int(rect.w * 0.2)
        area = pygame.Rect(rect.x + pad, y, rect.w - 2 * pad - side_w - self.gap, foot - self.gap - y)
        offer = f.offer()
        pool = f._pool()
        self.prefetch(gui, [face_of(h, pool) for h in offer])
        n = max(1, len(offer))
        cw = (area.w - self.gap * (n - 1)) // n
        small, tiny, bold = gui.font("small"), gui.font("tiny"), gui.font("body", True)
        for i, h in enumerate(offer):
            col = pygame.Rect(area.x + i * (cw + self.gap), area.y, cw, area.h)
            hot = col.collidepoint(gui.mouse)
            round_rect(scr, col, gfx.PANEL, 8, 2 if hot else 1, GOLD if hot else gfx.PANEL_EDGE)
            self.rows.append((col, "pick", h.code))
            inner = col.inflate(-2 * int(8 * fs), -2 * int(8 * fs))
            ph = min(int(inner.h * 0.42), int(inner.w / CARD_ASPECT))
            pw = int(ph * CARD_ASPECT)
            face = face_of(h, pool)
            yy = inner.y
            if face:
                pr = pygame.Rect(inner.centerx - pw // 2, yy, pw, ph)
                scr.blit(card_picture(gui, face, pool.get(face), pw, ph), pr)
                if pr.collidepoint(gui.mouse):
                    self.hover = (face, pool.get(face))
                yy = pr.bottom + int(6 * fs)
            draw_text(scr, clip_text(h.theme, bold, inner.w), inner.x, yy, bold, GOLD)
            yy += bold.get_height()
            draw_text(scr, clip_text(products.SET_NAMES.get(h.set, h.set) + (f"  ·  {h.number}" if h.number else "")
                                     + "  ·  " + products.colour_words(products.colours(h, pool)), tiny,   # Round RPG9
                                     inner.w), inner.x, yy, tiny, DIM)
            yy += tiny.get_height() + int(4 * fs)
            basics = 0
            lines = []
            for name, k in h.cards:
                card = pool.get(name)
                if card is not None and card.is_basic:
                    basics += k
                else:
                    lines.append(f"{k}  {name}" if k > 1 else name)
            if basics:
                lines.append(f"+ {basics} basic land{'s' if basics != 1 else ''}")
            fit = max(1, (inner.bottom - yy) // small.get_height())
            if len(lines) > fit:
                lines = lines[:fit - 1] + [f"... and {len(lines) - fit + 1} more"]
            for ln in lines:
                draw_text(scr, clip_text(ln, small, inner.w), inner.x, yy, small, TEXT)
                yy += small.get_height()
        side = pygame.Rect(area.right + self.gap, area.y, side_w, area.h)
        draw_text(scr, "Picked so far", side.x, side.y, gui.font("small", True), GOLD)
        yy = side.y + small.get_height() + 4
        for p in f.picks:
            draw_text(scr, clip_text(p.theme, small, side.w), side.x, yy, small, TEXT)
            yy += small.get_height()
        if not f.picks:
            draw_text(scr, "nothing yet", side.x, yy, small, DIM)
            yy += small.get_height()
        yy += int(6 * fs)
        for ln in wrap_text(f.colour_note(), small, side.w):         # Round RPG9: the colour rule as it stands now
            if yy + small.get_height() > side.bottom:
                break
            draw_text(scr, ln, side.x, yy, small, DIM)
            yy += small.get_height()
        self.draw_message(gui, rect.x + pad, foot - self.gap - 2 * small.get_height(), rect.w - 2 * pad)

    def draw_ready(self, gui, rect):
        pad = self.pad
        f = self.flow
        deck = f.preview()
        total = sum(k for _n, k in deck)
        sub = (f"{total} cards from " + ", ".join(h.theme for h in f.picks) + ". Begin saves the game in slot "
               f"{self.slot} and opens the town.")
        y = self.header(gui, rect, f"{f.name}'s starting deck", sub)
        foot = self.footer(gui, rect, left=[("Back", "back", True, False)],
                           right=[("Begin the adventure", "begin", True, True)])
        self.draw_deck_text(gui, pygame.Rect(rect.x + pad, y, rect.w - 2 * pad, foot - self.gap - y), deck, f._pool())
        self.draw_message(gui, rect.x + pad, foot - self.gap - 2 * gui.font("small").get_height(), rect.w - 2 * pad)

    def draw_deck_text(self, gui, area, deck, pool):
        """A deck as text in columns by type ("Creature (14)", then "2  Llanowar Elves" lines); the mouse shows a card big."""
        scr, small, bold = gui.screen, gui.font("small"), gui.font("small", True)
        groups = {}
        for name, k in deck:
            card = pool.get(name)
            groups.setdefault(adv.type_group(card), []).append((name, k, card))
        blocks = []
        for g in adv.TYPE_ORDER:
            if g in groups:
                items = sorted(groups[g], key=lambda e: (e[2].mana_value if e[2] else 99, e[0].lower()))
                blocks.append((f"{g} ({sum(k for _n, k, _c in items)})", items))
        lh = small.get_height()
        cols = max(1, min(4, area.w // int(260 * self.fs)))
        cw = (area.w - self.gap * (cols - 1)) // cols
        x, y, c = area.x, area.y, 0
        for head, items in blocks:
            need = lh * (len(items) + 1)
            if y + min(need, 3 * lh) > area.bottom and c < cols - 1:
                c += 1
                x, y = area.x + c * (cw + self.gap), area.y
            draw_text(scr, clip_text(head, bold, cw), x, y, bold, GOLD)
            y += lh
            for name, k, card in items:
                if y + lh > area.bottom:
                    if c < cols - 1:
                        c += 1
                        x, y = area.x + c * (cw + self.gap), area.y
                    else:
                        break
                r = pygame.Rect(x, y, cw, lh)
                draw_text(scr, clip_text(f"{k}  {name}", small, cw), x, y, small, TEXT)
                if r.collidepoint(gui.mouse):
                    self.hover = (name, card)
                y += lh
            y += lh // 2

    # map
    def draw_map(self, gui):
        scr = gui.screen
        W, H = scr.get_size()
        ex, g = self.explorer, self.game
        scr.fill(gfx.BACKDROP)
        key = (W, H, ex.area.tw, ex.area.th)
        if self.camera is None or self._cam_key != key:
            self.camera, self._cam_key = explore.RoomCamera(W, H, ex.area.tw, ex.area.th), key
        view = self.views.get(ex.area.name)
        if view is None:
            view = self.views[ex.area.name] = rpg_map_view.MapView(ex.area)
        self.camera.look_at(ex.view_origin())
        view.draw(scr, self.camera, ex.walker, ex.encounters(), time.monotonic())
        out = rpg_map_view.draw_panels(scr, self.camera, ex.area, ex.room, g.save.world.get("seen", ()), g.panel_info())
        small = gui.font("small")
        left = out.get("left")
        bw = gui.button_width("Menu")
        if left is not None and left.w >= bw + 2 * int(8 * self.fs):
            bx, by = left.x + int(8 * self.fs), left.bottom - int(8 * self.fs) - self.bh
            keys = [w for ln in MAP_KEYS for w in wrap_text(ln, small, left.w - int(16 * self.fs))]   # a narrow panel wraps them
            y = by - self.gap - len(keys) * small.get_height()
            for ln in keys:
                draw_text(scr, clip_text(ln, small, left.w - int(16 * self.fs)), bx, y, small, DIM)
                y += small.get_height()
            self.button(gui, pygame.Rect(bx, by, bw, self.bh), "Menu", "pause")
            mx, my, mw = left.x + int(8 * self.fs), by - self.gap - (len(keys) + 3) * small.get_height(), left.w - int(16 * self.fs)
        else:
            self.button(gui, pygame.Rect(W - bw - 8, H - self.bh - 8, bw, self.bh), "Menu", "pause")
            draw_text(scr, "   ".join(MAP_KEYS), 8, H - 8, small, DIM, "bottomleft", shadow=True)
            mx, my, mw = 8, H - 8 - 4 * small.get_height(), W // 2
        if self.message:
            self.draw_message(gui, mx, my, mw)

    # town
    def draw_town(self, gui, rect):
        scr, pad = gui.screen, self.pad
        g = self.game
        d = g.deck()
        wrong = g.deck_problems()
        sub = f"{g.save.gold} gold   ·   Your deck: {d['name'] if d else 'none'} ({g.deck_size()} cards)" + \
              ("  -  can't be played yet" if wrong else "")
        mw = gui.button_width("Main menu", "small")
        mh = gui.font("small").get_height() + int(12 * self.fs)
        self.button(gui, pygame.Rect(rect.right - pad - mw, rect.y + pad, mw, mh), "Main menu", "menu", fkey="small")
        y = self.header(gui, pygame.Rect(rect.x, rect.y, rect.w - mw - self.gap, rect.h), g.town_name(self.town), sub)
        # tabs and the town's own buttons
        x = rect.x + pad
        tab_h = self.bh
        for key, label in TABS:
            w = gui.button_width(label)
            self.button(gui, pygame.Rect(x, y, w, tab_h), label, f"tab:{key}", True, key == self.tab)
            x += w + self.gap
        right = rect.right - pad
        for label, name in (("Leave town", "leave_town"), ("Your deck", "town_deck")):
            w = gui.button_width(label)
            if right - w > x + self.gap:
                right -= w
                self.button(gui, pygame.Rect(right, y, w, tab_h), label, name, True, name == "leave_town")
                right -= self.gap
        y += tab_h + self.gap
        small = gui.font("small")
        msg_h = 2 * small.get_height() + self.gap
        area = pygame.Rect(rect.x + pad, y + small.get_height(), int((rect.w - 2 * pad) * 0.66), rect.bottom - pad - msg_h - y - small.get_height())
        self.draw_message(gui, rect.x + pad, rect.bottom - pad - msg_h + self.gap, rect.w - 2 * pad)
        getattr(self, "draw_tab_" + self.tab)(gui, area)
        if self.hover is None and self.tab != "packs":
            side_x = area.right + 2 * self.gap
            draw_text(scr, clip_text("Point at a card to see it here.", small, rect.right - pad - side_x), side_x, area.y, small, DIM)
        if self.hover is not None:                           # the card under the mouse, big, in the room at the right
            name, card = self.hover
            side = pygame.Rect(area.right + 2 * self.gap, area.y, rect.right - pad - area.right - 2 * self.gap, area.h)
            h = min(side.h, int(side.w / CARD_ASPECT))
            w = int(h * CARD_ASPECT)
            if w > 40:
                r = pygame.Rect(side.x + (side.w - w) // 2, side.y, w, h)
                gfx.shadowed(scr, r, radius=max(4, int(w * 0.05)))
                scr.blit(card_picture(gui, name, card, w, h), r)
            self.hover = None                                # shown here, not again by draw()

    def row_h(self, gui):
        return max(self.bh, gui.font("body").get_height() + int(14 * self.fs)) + int(4 * self.fs)

    def draw_row(self, gui, r, text, sub, buttons, name=None):
        """One shop line: the text (and a dim second part), buttons at the right ((label, name, enabled, primary))."""
        scr, fs = gui.screen, self.fs
        body, small = gui.font("body"), gui.font("small")
        box = r.inflate(0, -int(4 * fs))
        hot = box.collidepoint(gui.mouse)
        round_rect(scr, box, gfx.PANEL_DEEP if not hot else gfx.PANEL, 6, 1, gfx.PANEL_EDGE)
        x = box.right - int(8 * fs)
        bh = min(self.bh, box.h - 6)
        for label, bname, on, primary in reversed(buttons):
            w = gui.button_width(label)
            x -= w
            self.button(gui, pygame.Rect(x, box.centery - bh // 2, w, bh), label, bname, on, primary)
            x -= int(6 * fs)
        room = x - box.x - int(16 * fs)
        tw = min(body.size(text)[0], int(room * 0.55)) if sub else room
        draw_text(scr, clip_text(text, body, tw), box.x + int(10 * fs), box.centery, body, TEXT, "midleft")
        if sub:
            draw_text(scr, clip_text(sub, small, max(10, room - tw - int(12 * fs))), box.x + int(22 * fs) + tw, box.centery,
                      small, DIM, "midleft")
        if hot and name:
            self.hover = (name, self.game.pool.get(name))

    def draw_tab_singles(self, gui, area):
        s, g = self.shop, self.game
        offers = s.singles()
        self.prefetch(gui, [o.name for o in offers])
        draw_text(gui.screen, f"Singles  -  restocked every {g.numbers.restock_after_duels} duels", area.x, area.y - 2,
                  gui.font("tiny"), DIM, "bottomleft")
        if not offers:
            draw_text(gui.screen, "Nothing for sale.", area.x, area.y, gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:singles", len(offers), self.row_h(gui)):
            o = offers[i]
            owned = g.save.collection.count(o.name) or 0
            sub = f"tier {o.tier}   ·   {o.price} gold   ·   {o.left} left" + (f"   ·   you own {owned}" if owned else "")
            ok = o.left > 0 and g.save.gold >= o.price
            self.draw_row(gui, r, o.name, sub, [("Buy", f"buy:{o.slot}", ok, True)], name=o.name)

    def draw_tab_packs(self, gui, area):
        packs = self.shop.packs()
        g = self.game
        draw_text(gui.screen, "Booster packs  -  every card goes into your collection", area.x, area.y - 2, gui.font("tiny"),
                  DIM, "bottomleft")
        if not packs:
            draw_text(gui.screen, "This town sells no packs.", area.x, area.y, gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:packs", len(packs), self.row_h(gui)):
            code, name, price = packs[i]
            self.draw_row(gui, r, name, f"{code}   ·   {price} gold", [("Buy and open", f"pack:{code}", g.save.gold >= price, True)])

    def draw_tab_lands(self, gui, area):
        basics = self.shop.basics()
        g = self.game
        draw_text(gui.screen, "Basic lands  -  as many as you like", area.x, area.y - 2, gui.font("tiny"), DIM, "bottomleft")
        if not basics:
            draw_text(gui.screen, "Basic lands are free in this game: there is nothing to buy.", area.x, area.y,
                      gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:lands", len(basics), self.row_h(gui)):
            name, price = basics[i]
            owned = g.save.collection.count(name) or 0
            buttons = [(f"+{k}", f"basic:{name}:{k}", g.save.gold >= price * k, k == 1) for k in BASIC_STEPS]
            self.draw_row(gui, r, name, f"{price} gold each   ·   you own {owned}", buttons, name=name)

    def sell_list(self):
        g, s = self.game, self.shop
        uses = adv.deck_uses(g)
        out = []
        for name in g.save.collection.names():
            if g.save.collection.is_free(name):
                continue
            have = g.save.collection.count(name)
            each = s.sell_price(name)
            used = uses.get(cardpool.normal_name(name), 0)
            out.append((name, have, each, used))
        return out

    def draw_tab_sell(self, gui, area):
        rows = self.sell_list()
        draw_text(gui.screen, "Sell  -  a card your deck still needs can't be sold", area.x, area.y - 2, gui.font("tiny"), DIM,
                  "bottomleft")
        if not rows:
            draw_text(gui.screen, "You have nothing to sell.", area.x, area.y, gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:sell", len(rows), self.row_h(gui)):
            name, have, each, used = rows[i]
            sub = f"you own {have}   ·   {each} gold each" + (f"   ·   your deck uses {used}" if used else "")
            ok = each >= 1 and have - 1 >= used
            self.draw_row(gui, r, name, sub, [("Sell 1", f"sell:{name}", ok, False)], name=name)

    # pack
    def draw_pack(self, gui, rect):
        scr, pad = gui.screen, self.pad
        name, cards = self.opened or ("", [])
        new = sum(1 for c in cards if c.get("new"))
        y = self.header(gui, rect, f"{name}: your pack", f"{len(cards)} cards, {new} new to your collection. "
                        f"You have {self.game.save.gold} gold.")
        foot = self.footer(gui, rect, right=[("Back to the shop", "pack_done", True, True)])
        area = pygame.Rect(rect.x + pad, y, rect.w - 2 * pad, foot - self.gap - y)
        self.prefetch(gui, [c["name"] for c in cards])
        n = max(1, len(cards))
        per_row = max(1, min(8, n))
        rows = -(-n // per_row)
        while True:
            w = (area.w - self.gap * (per_row - 1)) // per_row
            h = int(w / CARD_ASPECT)
            if rows * h + (rows - 1) * self.gap <= area.h or per_row >= n:
                break
            per_row += 1
            rows = -(-n // per_row)
        h = min(h, (area.h - (rows - 1) * self.gap) // max(1, rows))
        w = int(h * CARD_ASPECT)
        tiny = gui.font("tiny", True)
        for i, c in enumerate(cards):
            r = pygame.Rect(area.x + (i % per_row) * (w + self.gap), area.y + (i // per_row) * (h + self.gap), w, h)
            card = self.game.pool.get(c["name"])
            scr.blit(card_picture(gui, c["name"], card, w, h), r)
            if c.get("new"):
                tag = pygame.Rect(r.x + 4, r.y + 4, tiny.size("NEW")[0] + 10, tiny.get_height() + 4)
                round_rect(scr, tag, gfx.BTN_GOLD_BOTTOM, 4, 1, gfx.BTN_GOLD_EDGE)
                draw_text(scr, "NEW", tag.centerx, tag.centery, tiny, gfx.BTN_GOLD_FG, "center")
            if r.collidepoint(gui.mouse):
                self.hover = (c["name"], card)

    # the deck editor
    def draw_deck(self, gui, rect):
        scr, pad, fs = gui.screen, self.pad, self.fs
        e = self.editor
        total = e.total()
        sub = (f"{total} cards (at least 60)   ·   click a card: one more in the deck;  right-click it or click a line on "
               "the right: one less;  type to search")
        y = self.header(gui, rect, f"Your deck  -  {e.name}", sub)
        foot = self.footer(gui, rect, left=[("Cancel changes", "deck_cancel", e.changed, False)],
                           right=[("Done", "deck_done", True, True)])
        left = pygame.Rect(rect.x + pad, y, int((rect.w - 2 * pad) * 0.64), foot - self.gap - y)
        right = pygame.Rect(left.right + 2 * self.gap, y, rect.right - pad - left.right - 2 * self.gap, left.h)
        # filters: colours, types, the search
        tiny, small = gui.font("tiny", True), gui.font("small")
        fh = max(int(28 * fs), tiny.get_height() + 10)
        x = left.x
        for c in adv.COLOURS:
            w = max(fh, tiny.size(c)[0] + 14)
            self.button(gui, pygame.Rect(x, y, w, fh), c, f"colour:{c}", True, c in e.colours, fkey="tiny")
            x += w + 4
        x += self.gap
        for gname in GROUP_BUTTONS:                          # every type button, on a second row when the first is full
            label = gname if gname != "Planeswalker" else "Walker"
            w = gui.button_width(label, "tiny")
            if x + w > left.right and x > left.x:
                x, y = left.x, y + fh + 4
            self.button(gui, pygame.Rect(x, y, w, fh), label, f"group:{gname}", True, e.group == gname, fkey="tiny")
            x += w + 4
        y2 = y + fh + 6
        stext = f"Search: {e.search}" if e.search else "Type to search by name or type"
        draw_text(scr, clip_text(stext, small, left.w - 120), left.x, y2, small, TEXT if e.search else DIM)
        if e.search:
            w = gui.button_width("Clear", "tiny")
            self.button(gui, pygame.Rect(left.right - w, y2, w, small.get_height()), "Clear", "clear_search", fkey="tiny")
        y2 += small.get_height() + 6
        pool_area = pygame.Rect(left.x, y2, left.w, left.bottom - y2)
        self.draw_pool(gui, pool_area)
        self.draw_deck_list(gui, right)
        self.draw_message(gui, rect.x + pad, foot - self.gap - 2 * small.get_height(), int(rect.w * 0.6))

    def draw_pool(self, gui, area):
        scr, fs, e = gui.screen, self.fs, self.editor
        rows = e.collection_rows()
        self.prefetch(gui, [r.name for r in rows[:120]])
        per_row = max(3, area.w // int(118 * fs))
        w = max(30, (area.w - (per_row - 1) * int(6 * fs)) // per_row)
        h = int(w / CARD_ASPECT)
        gap = int(6 * fs)
        fit_rows = max(1, (area.h + gap) // (h + gap))
        total_rows = -(-len(rows) // per_row) if rows else 0
        top = max(0, min(self.scroll.get("deckpool", 0), max(0, total_rows - fit_rows)))
        self.scroll["deckpool"] = top
        tiny = gui.font("tiny", True)
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill(gfx.BACKDROP + (160,))
        for k, row in enumerate(rows[top * per_row:(top + fit_rows) * per_row]):
            r = pygame.Rect(area.x + (k % per_row) * (w + gap), area.y + (k // per_row) * (h + gap), w, h)
            scr.blit(card_picture(gui, row.name, row.card, w, h), r)
            owned = "-" if row.owned is None else str(row.owned)
            if e.why_not(row.name):
                scr.blit(shade, r)
            label = f"{row.used}/{owned}"
            tag = pygame.Rect(r.right - tiny.size(label)[0] - 12, r.bottom - tiny.get_height() - 8, tiny.size(label)[0] + 8,
                              tiny.get_height() + 4)
            round_rect(scr, tag, gfx.BTN_GOLD_BOTTOM if row.used else gfx.PANEL_DEEP, 4, 1, gfx.BTN_GOLD_EDGE if row.used
                       else gfx.PANEL_EDGE)
            draw_text(scr, label, tag.centerx, tag.centery, tiny, gfx.BTN_GOLD_FG if row.used else TEXT, "center")
            self.rows.append((r, "pool", row.name))
            if r.collidepoint(gui.mouse):
                self.hover = (row.name, row.card)
        if not rows:
            draw_text(scr, "No card of yours matches." if (e.colours or e.group or e.search) else "You own no cards.",
                      area.centerx, area.y + 20, gui.font("small"), DIM, "midtop")
        elif total_rows > fit_rows:
            draw_text(scr, f"rows {top + 1}-{min(total_rows, top + fit_rows)} of {total_rows}  (wheel to scroll)", area.right,
                      area.y - 2, gui.font("tiny"), DIM, "bottomright")

    def draw_deck_list(self, gui, area):
        """The deck on the right, by type; it scrolls (the wheel over it) when it is longer than the panel. The rules' problems,
        or "ready to duel", stay at the bottom."""
        scr, e = gui.screen, self.editor
        small, bold = gui.font("small"), gui.font("small", True)
        self.decklist_rect = pygame.Rect(area)
        round_rect(scr, area, gfx.PANEL_DEEP, 8, 1, gfx.PANEL_EDGE)
        inner = area.inflate(-int(16 * self.fs), -int(12 * self.fs))
        lh = small.get_height()
        problems = e.problems()
        foot = []
        for p in problems[:3]:
            foot += [(ln, ORANGE) for ln in wrap_text(p.text, small, inner.w)[:3]]
        if not problems:
            foot = [(f"{e.total()} cards - ready to duel.", GREEN)]
        foot_top = inner.bottom - len(foot) * lh
        for i, (ln, colour) in enumerate(foot):
            draw_text(scr, ln, inner.x, foot_top + i * lh, small, colour)
        lines = []
        for g, items in e.deck_rows():
            lines.append(("head", f"{g} ({sum(k for _n, k, _c in items)})", None))
            lines += [("card", name, (k, card)) for name, k, card in items]
        if not lines:
            draw_text(scr, "Your deck is empty: click cards on the left.", inner.x, inner.y, small, DIM)
            return
        tiny = gui.font("tiny")
        room = max(lh, foot_top - lh // 2 - inner.y)
        fit = max(1, room // lh)
        if len(lines) > fit:                                 # the "lines 1-14 of 45" note goes inside the panel, above the status
            fit = max(1, (room - tiny.get_height()) // lh)
        top = max(0, min(self.scroll.get("decklist", 0), max(0, len(lines) - fit)))
        self.scroll["decklist"] = top
        y = inner.y
        for kind, text, data in lines[top:top + fit]:
            if kind == "head":
                draw_text(scr, clip_text(text, bold, inner.w), inner.x, y, bold, GOLD)
            else:
                k, card = data
                r = pygame.Rect(inner.x, y, inner.w, lh)
                hot = r.collidepoint(gui.mouse)
                draw_text(scr, clip_text(f"{k}  {text}", small, inner.w), inner.x, y, small, GOLD if hot else TEXT)
                self.rows.append((r, "deckline", text))
                if hot:
                    self.hover = (text, card)
            y += lh
        if len(lines) > fit:
            note = clip_text(f"lines {top + 1}-{min(len(lines), top + fit)} of {len(lines)}  (wheel)", tiny, inner.w)
            draw_text(scr, note, inner.right, foot_top - lh // 4, tiny, DIM, "bottomright")

    # the small windows over a stage
    def draw_sub(self, gui):
        L, scr = gui.L, gui.screen
        layer = pygame.Surface(scr.get_size(), pygame.SRCALPHA)
        layer.fill(gfx.BACKDROP + (150,))
        scr.blit(layer, (0, 0))
        self.buttons = []
        fs = self.fs
        tf, body = gui.font("title", True), gui.font("body")
        if self.sub == "fight":
            title, lines = adv.fight_text(self.game, self.fight)
            colour = GOLD
            buttons = [("Not now", "no", True, False), ("Fight", "fight", True, True)]
        elif self.sub == "result":
            title, lines, colour = self.result or ("", [], GOLD)
            buttons = [("OK", "ok", True, True)]
        elif self.sub == "pause":
            title, lines, colour = "Menu", [f"{self.game.save.name}  ·  {self.game.save.gold} gold  ·  slot {self.slot}. "
                                            "Your game is saved."], GOLD
            buttons = [("Save and leave", "leave", True, False), ("Your deck", "deck", True, False),
                       ("Back to the map", "resume", True, True)]
        else:
            title, lines, _yes, _a = self.confirm or ("", [], "Yes", None)
            colour = ORANGE
            buttons = [("Keep it", "no", True, False), (self.confirm[2] if self.confirm else "Yes", "yes", True, True)]
        width = min(L.W - 40, int(640 * fs))
        btn_w = sum(gui.button_width(b[0]) for b in buttons) + self.gap * (len(buttons) - 1)
        width = max(width, min(L.W - 40, btn_w + 2 * self.pad))
        wrapped = []
        for ln in lines:
            wrapped += wrap_text(ln, body, width - 2 * self.pad)
        h = self.pad * 2 + tf.get_height() + self.gap + len(wrapped) * body.get_height() + self.gap + self.bh + self.pad
        box = pygame.Rect(0, 0, width, min(L.H - 40, h))
        box.center = (L.W // 2, L.H // 2)
        gfx.shadowed(scr, box, 16, 6, 120)
        round_rect(scr, box, gfx.DIALOG_BG, 16, 2, gfx.DIALOG_EDGE)
        self.rect = box
        y = box.y + self.pad
        draw_text(scr, clip_text(title, tf, box.w - 2 * self.pad), box.x + self.pad, y, tf, colour)
        y += tf.get_height() + self.gap
        for ln in wrapped:
            if y + body.get_height() > box.bottom - self.pad - self.bh - self.gap:
                break
            draw_text(scr, ln, box.x + self.pad, y, body, TEXT)
            y += body.get_height()
        x = box.right - self.pad
        by = box.bottom - self.pad - self.bh
        for label, name, on, primary in reversed(buttons):
            w = gui.button_width(label)
            x -= w
            self.button(gui, pygame.Rect(x, by, w, self.bh), label, name, on, primary)
            x -= self.gap
