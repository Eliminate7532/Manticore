# SPDX-License-Identifier: GPL-3.0-or-later

"""
game_state.py - Core game state: zones, life totals, turn structure, casting, combat, mana
"""
import random
from mana_system import ManaPool, parse_mana_cost, get_land_mana_color


class Player:
    def __init__(self, name, decklist, commanders=None):
        self.name = name
        self.life = 40
        self.library = list(decklist)
        random.shuffle(self.library)
        self.hand = []
        self.battlefield = []
        self.graveyard = []
        self.exile = []
        self.command_zone = list(commanders) if commanders else []
        self.commander_names = list(commanders) if commanders else []   # stays fixed when a commander leaves the command zone
        self.commander_casts = {}      # commander name -> times cast from the command zone (for commander tax)
        self.mulligans = 0
        self.mana_pool = ManaPool()
        self.commander_damage_taken = {}
        self.lands_played_this_turn = 0

    def draw(self, n=1):
        drawn = []
        for _ in range(n):
            if self.library:
                card = self.library.pop(0)
                self.hand.append(card)
                drawn.append(card)
        return drawn

    def mulligan_hand(self, size=7):
        self.library.extend(self.hand)
        self.hand = []
        random.shuffle(self.library)
        self.draw(size)

    def reset_lands_played(self):
        self.lands_played_this_turn = 0

    def play_land(self, card_name, card_store):
        if card_name not in self.hand:
            return False, "Card not in hand."
        if self.lands_played_this_turn >= 1:
            return False, "Already played a land this turn."

        card_data = card_store.get_card(card_name) if card_store else None
        if card_data and "Land" not in card_data.get("type_line", ""):
            return False, f"{card_name} is not a land."

        self.hand.remove(card_name)
        self.battlefield.append({
            "name": card_name, "tapped": False, "counters": {}, "summoning_sick": False, "is_land": True
        })
        self.lands_played_this_turn += 1
        return True, None

    def tap_lands_for_mana(self, card_store, land_names=None):
        lands = [
            c for c in self.battlefield
            if c.get("is_land") and not c["tapped"]
            and (land_names is None or c["name"] in land_names)
        ]
        for land in lands:
            colors = get_land_mana_color(card_store.get_card(land["name"])) if card_store else ["C"]
            color = colors[0] if colors else "C"
            self.mana_pool.add(color, 1)
            land["tapped"] = True
        return len(lands)

    def can_afford(self, card_name, card_store):
        card_data = card_store.get_card(card_name) if card_store else None
        if not card_data:
            return True, {"generic": 0}
        cost = parse_mana_cost(card_data.get("mana_cost", ""))
        return self.mana_pool.can_pay(cost), cost

    def cast_from_hand(self, card_name, card_store=None, destination="battlefield", force=False):
        if card_name not in self.hand:
            return False, "Card not in hand."

        if not force and card_store:
            affordable, cost = self.can_afford(card_name, card_store)
            if not affordable:
                return False, f"Cannot afford {card_name} (cost: {cost})."
            self.mana_pool.pay(cost)

        self.hand.remove(card_name)
        if destination == "battlefield":
            self.battlefield.append({
                "name": card_name, "tapped": False, "counters": {}, "summoning_sick": True
            })
        elif destination == "graveyard":
            self.graveyard.append(card_name)
        return True, None

    def cast_commander(self, card_store=None, force=False):
        if not self.command_zone:
            return False, "No commander in command zone."
        card_name = self.command_zone[0]

        if not force and card_store:
            affordable, cost = self.can_afford(card_name, card_store)
            if not affordable:
                return False, f"Cannot afford commander {card_name} (cost: {cost})."
            self.mana_pool.pay(cost)

        self.command_zone.pop(0)
        self.battlefield.append({
            "name": card_name, "tapped": False, "counters": {}, "summoning_sick": True
        })
        return True, None

    def tap_permanent(self, card_name):
        for card in self.battlefield:
            if card["name"] == card_name and not card["tapped"]:
                card["tapped"] = True
                return True
        return False

    def untap_all(self):
        for card in self.battlefield:
            card["tapped"] = False
            card["summoning_sick"] = False
        self.mana_pool.clear()
        self.reset_lands_played()

    def destroy_permanent(self, card_name):
        for card in self.battlefield:
            if card["name"] == card_name:
                self.battlefield.remove(card)
                self.graveyard.append(card_name)
                return True
        return False

    def take_damage(self, amount, from_commander=False, source_player=None):
        self.life -= amount
        if from_commander and source_player:
            self.commander_damage_taken[source_player] = (
                self.commander_damage_taken.get(source_player, 0) + amount
            )

    def board_summary(self):
        return {
            "life": self.life,
            "battlefield": [c["name"] for c in self.battlefield],
            "tapped": [c["name"] for c in self.battlefield if c["tapped"]],
            "mana_pool": dict(self.mana_pool.pool),
            "hand_size": len(self.hand),
            "graveyard": list(self.graveyard),
            "library_size": len(self.library),
            "command_zone": list(self.command_zone),
        }


class GameState:
    def __init__(self, player_a, player_b, card_store=None):
        self.players = [player_a, player_b]
        self.turn_number = 1
        self.active_player_index = 0
        self.first_player_index = 0     # who takes the first turn (matters for Gemstone Caverns, draws)
        self.first_turn_taken = False   # set once the game's first turn has begun (that player skips the draw)
        self.phase = "upkeep"
        self.stack = []
        self.log = []
        self.card_store = card_store

    def start_game(self, opening_hand_size=7):
        for p in self.players:
            p.draw(opening_hand_size)
        self._log("Game started.")

    def active_player(self):
        return self.players[self.active_player_index]

    def opponent_of(self, player):
        return self.players[1] if player is self.players[0] else self.players[0]

    def _log(self, message):
        self.log.append(f"[Turn {self.turn_number}] {message}")

    PHASES = ["upkeep", "draw", "main1", "combat", "main2", "end"]

    def next_phase(self):
        idx = self.PHASES.index(self.phase)
        if idx + 1 < len(self.PHASES):
            self.phase = self.PHASES[idx + 1]
            if self.phase == "draw":
                self.active_player().draw(1)
                self._log(f"{self.active_player().name} draws for the turn.")
        else:
            self.pass_turn()

    def pass_turn(self):
        self.active_player().untap_all()
        self.active_player_index = (self.active_player_index + 1) % len(self.players)
        if self.active_player_index == 0:
            self.turn_number += 1
        self.phase = "upkeep"
        self._log(f"{self.active_player().name}'s turn begins.")

    def add_to_stack(self, card_name, controller_name):
        self.stack.append({"card": card_name, "controller": controller_name})
        self._log(f"{controller_name} puts {card_name} on the stack.")

    def resolve_top_of_stack(self):
        if not self.stack:
            return None
        item = self.stack.pop()
        card_name = item["card"]
        self._log(f"{card_name} resolves.")
        return item

    def deal_combat_damage(self, attacker_player, defender_player, amount, commander_source=False):
        defender_player.take_damage(
            amount, from_commander=commander_source, source_player=attacker_player.name
        )
        self._log(f"{attacker_player.name} deals {amount} combat damage to {defender_player.name}.")
        if commander_source and defender_player.commander_damage_taken.get(attacker_player.name, 0) >= 21:
            self._log(f"{defender_player.name} has taken 21+ commander damage from {attacker_player.name} and loses!")
