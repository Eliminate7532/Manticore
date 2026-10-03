# SPDX-License-Identifier: GPL-3.0-or-later

"""
forge_bridge.py - Sketch of a Python-to-Forge bridge.

Converts our internal decklists (already produced by deck_importer.py) into
Forge's .dck file format, and provides a wrapper to launch Forge in headless
"sim" mode as a subprocess, then parse its results back into Python.

STATUS: This is a design sketch, not yet tested against a real Forge
installation. Needs a real forge.jar and a real "sim" invocation to verify
the exact CLI args and log output format before this is production-ready.
"""
import subprocess
import re
from pathlib import Path


def deck_to_dck_format(deck_name, commanders, deck_list):
    """
    Convert a decklist (commanders + list of card names with duplicates
    expanded, as produced by deck_importer.import_deck) into Forge's
    plain-text .dck format.

    Forge .dck files are simple: a [metadata] section, then a [Main] section
    listing "N CardName" per line. Commander decks also need a [Commander]
    or [Avatar]-style section - exact tag needs confirming against a real
    Forge install, sketched here as [Commander].
    """
    lines = []
    lines.append("[metadata]")
    lines.append(f"Name={deck_name}")
    lines.append("")

    if commanders:
        lines.append("[Commander]")
        for c in commanders:
            lines.append(f"1 {c}")
        lines.append("")

    lines.append("[Main]")
    # collapse duplicates into "N CardName" lines, matching typical .dck style
    counts = {}
    for card in deck_list:
        counts[card] = counts.get(card, 0) + 1
    for card, qty in counts.items():
        lines.append(f"{qty} {card}")

    return "\n".join(lines)


def write_dck_file(deck_name, commanders, deck_list, output_dir="forge_decks"):
    Path(output_dir).mkdir(exist_ok=True)
    content = deck_to_dck_format(deck_name, commanders, deck_list)
    safe_name = "".join(c if c.isalnum() else "_" for c in deck_name)
    path = Path(output_dir) / f"{safe_name}.dck"
    path.write_text(content)
    return str(path)


class ForgeSimResult:
    def __init__(self, raw_output):
        self.raw_output = raw_output
        self.winner = None
        self.game_log = []
        self._parse()

    def _parse(self):
        # PLACEHOLDER parsing logic - real Forge sim output format needs to be
        # captured from an actual run and this regex/logic adjusted to match.
        for line in self.raw_output.splitlines():
            self.game_log.append(line)
            match = re.search(r"(.+) has won", line)
            if match:
                self.winner = match.group(1).strip()


def run_forge_headless_match(forge_jar_path, dck_paths, num_games=1, timeout_seconds=300):
    """
    Launch Forge in headless simulation mode against the given .dck files.

    forge_jar_path: path to the built forge.jar (or launch script)
    dck_paths: list of .dck file paths, one per AI seat
    num_games: how many games to simulate in this batch

    Returns a ForgeSimResult. This wraps the documented `sim` CLI mode:
        sim -d <deck1> <deck2> ... -n <N>
    Exact flags (-D for deck directory, -f for format, -p for player count,
    etc.) need to be confirmed against the installed Forge version's
    wiki/help output before this is relied on.
    """
    cmd = ["java", "-jar", forge_jar_path, "sim", "-d"] + dck_paths + ["-n", str(num_games)]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Forge simulation timed out after {timeout_seconds}s") from e
    except FileNotFoundError as e:
        raise RuntimeError(
            "Could not launch Forge - check that Java is installed and "
            "forge_jar_path points to a real forge.jar"
        ) from e

    if proc.returncode != 0:
        raise RuntimeError(f"Forge exited with error:\n{proc.stderr}")

    return ForgeSimResult(proc.stdout)


# --- Example usage sketch (not run automatically) ---
if __name__ == "__main__":
    from deck_importer import import_deck

    # Example: convert one of Karl's decks into a .dck file
    commanders, deck_list = import_deck("PASTE_MOXFIELD_URL_HERE")
    dck_path = write_dck_file("Trazyn NBC", commanders, deck_list)
    print(f"Wrote Forge deck file to: {dck_path}")

    # Example: run it against itself as a mirror match (replace with real jar path)
    # result = run_forge_headless_match("forge.jar", [dck_path, dck_path], num_games=5)
    # print("Winner:", result.winner)
