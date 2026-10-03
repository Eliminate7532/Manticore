# SPDX-License-Identifier: GPL-3.0-or-later

"""
pod_game_state.py - Four-player free-for-all extension: turn order via d20, N players, AI agents
Now with real power/toughness pulled from Scryfall card data for combat math.
"""
import random
from game_state import Player, GameState


class PodGameState(GameState):
    def __init__(self, players, card_store=None):
        assert len(players) >= 2, "Need at least 2 players for a pod"
        self.players = list(players)
        self.turn_number = 1
        self.active_player_index = 0
        self.phase = "upkeep"
        self.stack = []
        self.log = []
        self.turn_order_rolls = {}
        self.card_store = card_store  # CardDataStore instance, optional but needed for real combat math

    def roll_for_turn_order(self):
        contenders = list(self.players)
        rolls = {}
        while True:
            round_rolls = {p.name: random.randint(1, 20) for p in contenders}
            rolls.update(round_rolls)
            self._log("Turn order rolls: " + ", ".join(f"{n}: {r}" for n, r in round_rolls.items()))
            max_roll = max(round_rolls.values())
            tied = [p for p in contenders if round_rolls[p.name] == max_roll]
            if len(tied) == 1:
                winner = tied[0]
                break
            else:
                self._log(f"Tie between {', '.join(p.name for p in tied)} — re-rolling.")
                contenders = tied
        self.turn_order_rolls = rolls
        winner_index = self.players.index(winner)
        self.players = self.players[winner_index:] + self.players[:winner_index]
        self.active_player_index = 0
        self._log(f"{winner.name} wins the roll and goes first.")
        return winner

    def alive_players(self):
        return [p for p in self.players if p.life > 0]

    def next_active_player_index(self, from_index):
        n = len(self.players)
        idx = from_index
        for _ in range(n):
            idx = (idx + 1) % n
            if self.players[idx].life > 0:
                return idx
        return from_index

    def pass_turn(self):
        self.active_player().untap_all()
        new_index = self.next_active_player_index(self.active_player_index)
        if new_index <= self.active_player_index:
            self.turn_number += 1
        self.active_player_index = new_index
        self.phase = "upkeep"
        self._log(f"{self.active_player().name}'s turn begins.")

    def check_eliminations(self):
        eliminated = []
        for p in self.players:
            if p.life <= 0 and p not in eliminated:
                eliminated.append(p)
                self._log(f"{p.name} has been eliminated (life at {p.life}).")
        return eliminated

    def game_over(self):
        return len(self.alive_players()) <= 1

    def get_power_toughness(self, card_name):
        """Look up real power/toughness from Scryfall data via card_store. Falls back to (2,2)."""
        if not self.card_store:
            return (2, 2)
        card = self.card_store.get_card(card_name)
        if not card:
            return (2, 2)
        try:
            power = int(card.get("power", 0))
            toughness = int(card.get("toughness", 0))
            return (power, toughness)
        except (ValueError, TypeError):
            # non-creature or */* power like some tokens - fall back
            return (2, 2)

    def is_creature(self, card_name):
        if not self.card_store:
            return True  # assume yes if we can't check
        card = self.card_store.get_card(card_name)
        if not card:
            return True
        type_line = card.get("type_line", "")
        return "Creature" in type_line


class SimpleAIAgent:
    def __init__(self, player, personality="balanced"):
        self.player = player
        self.personality = personality

    def choose_attack_target(self, game_state):
        others = [p for p in game_state.alive_players() if p is not self.player]
        if not others:
            return None
        if self.personality == "aggressive":
            return max(others, key=lambda p: p.life)
        elif self.personality == "controlling":
            return min(others, key=lambda p: p.life)
        else:
            return random.choice(others)

    def choose_card_to_play(self):
        if self.player.hand:
            return self.player.hand[0]
        return None

    def take_main_phase_action(self, game_state):
        card = self.choose_card_to_play()
        if card:
            self.player.cast_from_hand(card)
            game_state._log(f"{self.player.name} (AI) plays {card}.")
        return card

    def take_combat_action(self, game_state):
        target = self.choose_attack_target(game_state)
        attackers = [
            c for c in self.player.battlefield
            if not c["tapped"] and not c.get("summoning_sick")
            and game_state.is_creature(c["name"])
        ]
        if not target or not attackers:
            return target

        total_power = 0
        for creature in attackers:
            power, toughness = game_state.get_power_toughness(creature["name"])
            total_power += power
            creature["tapped"] = True

        if total_power > 0:
            game_state.deal_combat_damage(self.player, target, total_power)
        return target
