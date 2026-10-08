# SPDX-License-Identifier: GPL-3.0-or-later
"""
reporting.py - bug reports for the Forge table (no pygame in here, so it is easy to test).

One report = ONE zip file in the bug_reports folder next to the program, holding what is needed to see the problem again:
  report.txt        what the player wrote, when, which copy of the program (version.describe()), the computer, the game in one line
  screenshot.jpg    the table exactly as it looked (the single most useful thing)
  state.json        the board as the game sent it (Forge never sends the opponent's hidden cards)
  game_log.txt      the newest lines of the game log
  commands.json     every click since the game began (with the seed and the decks: what replay.py needs)
  decks/            the deck files the game was started with
  forge_engine.log.txt / crash_log.txt   the newest lines of both, when they exist
  java_crash_hs_err_pid<N>.txt   patch 46: when Java itself crashed in this game, its crash report - without the
                    "Environment Variables" block (user name, PATH, temp folder) and shortened (java_crash.excerpt)
Paths are cleaned: your Windows user folder becomes "~". The zip is kept small (MAX_ZIP_BYTES) by trimming logs and shrinking the picture.

If bug_report_config.json next to the program holds a Discord webhook address, the zip can be posted straight to that Discord
channel. A webhook can only post to its channel (it can't read anything), and it is checked to be a discord.com address.
Whatever goes wrong (offline, file refused, webhook deleted) the zip stays in bug_reports/ and the player is told to send that file.

Round FR1: the same window has a second tab, "Suggest a feature". An idea is ONE text message (no zip, no logs; one picture of
the screen only when the player ticks it), posted to the same webhook - or to "ideas_webhook" when bug_report_config.json has a
valid one - and always saved as bug_reports/idea_YYYYMMDD_HHMMSS.txt. Bugs and ideas share the one-a-minute limit.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile

import crashlog
import java_crash
import paths
import version

# Round 28: BASE_DIR (the program folder) is still what a caller that passes an explicit `folder` means -
# tests build a single scratch folder standing in for "everything lives together", exactly like a portable
# copy. REPORT_DIR/DECK_DIR are the production defaults, used only when a function's `folder` is left None;
# they move to the per-user local_dir() in an installed copy (see paths.py), same as before this round in
# portable mode (local_dir() is the program folder there).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = paths.bug_reports_dir()
CONFIG_NAME = "bug_report_config.json"
BUNDLED_CONFIG_NAME = "bug_report_config.bundled.json"      # Round 29 (§4.5): the alpha webhook the installer build copies in; see load_config()
DECK_DIR = paths.forge_decks_dir()
MAX_ZIP_BYTES = 5 * 1024 * 1024              # small enough for any chat service's upload limit
MIN_SECONDS_BETWEEN_SENDS = 60               # a friend can't flood the channel by pressing the button over and over
LOG_LINES, ENGINE_LINES, COMMAND_LINES = 400, 200, 5000      # commands: a whole game's worth, so a report can be replayed
FIELD_LIMIT = 1500                           # characters per text box in the form
SHOT_WIDTHS = (1600, 1100, 800, 560)         # screenshot widths to try, biggest first
WEBHOOK_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+/?$")
BOT_NAME = "Manticore bug report"
IDEA_BOT_NAME = "Manticore idea"             # Round FR1
IDEA_AREAS = ("Table", "Deck screen", "Cards & rules", "AI", "Online", "Look & sound", "Other")      # "Where in the game?" (optional)
DISCORD_CAP = 1900                           # characters in one Discord message (its limit is 2000)

CONTENTS = ["a screenshot of the table", "the board and the game log", "your last clicks", "the decks in the game",
            "the version of the program and of Forge", "your Windows / Python / Java versions", "the error logs, if any"]

_last_send = [0.0]


# ---- settings ------------------------------------------------------------------------------------------------------------------

def config_path(folder=None):
    return os.path.join(folder, CONFIG_NAME) if folder else paths.bug_config_file()


def bundled_config_path():
    """The alpha webhook file inside an installed build's program folder. Its different name is deliberate:
    paths.migrate_from_program_dir() copies only bug_report_config.json into a tester's folder, so a later build with a
    rotated webhook takes effect instead of being shadowed by an old copy."""
    return os.path.join(paths.program_dir(), BUNDLED_CONFIG_NAME)


def _read_config(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_config(folder=None):
    """The settings from bug_report_config.json as a dict ({} when the file is missing or unreadable).

    Round 29: with no explicit `folder` (the real program, not a test's scratch folder) the order is
      1. the user's own bug_report_config.json, when it holds a valid webhook;
      2. otherwise the bundled alpha webhook file in the program folder (installed builds only; the other keys of the
         user's own file, such as owner_name, still win).
    An explicit `folder` means exactly that folder's file and nothing else."""
    own = _read_config(config_path(folder))
    if folder is not None or webhook_problem(own.get("discord_webhook")) is None:
        return own
    bundled = _read_config(bundled_config_path())
    if webhook_problem(bundled.get("discord_webhook")) is not None:
        return own
    merged = dict(bundled)
    merged.update({k: v for k, v in own.items() if k != "discord_webhook"})
    return merged


def config_file_problem(folder=None):
    """Round 28d: why bug_report_config.json exists but can't be read, or None (it's fine, or simply not there). Karl's first
    alpha webhook was pasted without quotes around the address: the file was not valid JSON, load_config gave {}, and F8
    said "Sending to Discord is not set up on this copy" - true, but it hid the real problem. Never includes the address."""
    path = config_path(folder)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as e:
        return f"{CONFIG_NAME} could not be read ({type(e).__name__})"
    except ValueError as e:
        where = f" (line {e.lineno})" if getattr(e, "lineno", None) else ""
        return (f"{CONFIG_NAME} isn't valid{where} - check that the address has straight double quotes \"...\" around it "
                f"and a comma after it")
    if not isinstance(data, dict):
        return f"{CONFIG_NAME} isn't valid - it should hold {{ \"discord_webhook\": \"...\", \"owner_name\": \"...\" }}"
    return None


def webhook_problem(url):
    """Why this is not a usable webhook address, or None when it is fine."""
    if not url:
        return "no webhook address"
    if not isinstance(url, str) or not WEBHOOK_RE.match(url.strip()):
        return "the address in bug_report_config.json is not a Discord webhook address (it should start with https://discord.com/api/webhooks/)"
    return None


def webhook_url(folder=None):
    """The Discord webhook address to post to, or None (not set up, or not a valid Discord address)."""
    url = load_config(folder).get("discord_webhook")
    return url.strip() if isinstance(url, str) and webhook_problem(url) is None else None


def ideas_webhook_url(folder=None):
    """Round FR1: where ideas go - "ideas_webhook" when it is a valid Discord webhook (Karl may want ideas in their own
    channel), otherwise the bug reports' own address (webhook_url, with its bundled fallback), or None."""
    url = load_config(folder).get("ideas_webhook")
    if isinstance(url, str) and webhook_problem(url) is None:
        return url.strip()
    return webhook_url(folder)


def owner_name(folder=None):
    """Who receives the reports, as the player sees it ("Karl"; change it with "owner_name" in bug_report_config.json)."""
    name = load_config(folder).get("owner_name")
    return name.strip()[:30] if isinstance(name, str) and name.strip() else "Karl"


def config_status(folder=None):
    """(url or None, message for the player/owner about the setting)."""
    broken = config_file_problem(folder)
    if broken and not (folder is None and webhook_problem(_read_config(bundled_config_path()).get("discord_webhook")) is None):
        return None, broken + f", so this saves a report file for you to send to {owner_name(folder)}."
    raw = load_config(folder).get("discord_webhook")
    if not raw or "PASTE" in str(raw).upper():
        return None, f"Sending to Discord is not set up on this copy, so this saves a report file for you to send to {owner_name(folder)}."
    problem = webhook_problem(raw)
    if problem:
        return None, problem[0].upper() + problem[1:] + "."
    return raw.strip(), f"This sends the report to {owner_name(folder)}'s Discord channel (and saves a copy of the file)."


def idea_config_status(folder=None):
    """Round FR1: (url or None, message) for the "Suggest a feature" tab - the bug reports' status in an idea's words, unless a
    valid "ideas_webhook" is set (then that address, whatever state discord_webhook is in)."""
    own = load_config(folder).get("ideas_webhook")
    if isinstance(own, str) and webhook_problem(own) is None:
        return own.strip(), f"This sends your idea to {owner_name(folder)}'s Discord channel (and saves a copy)."
    url, why = config_status(folder)
    if url:
        return url, f"This sends your idea to {owner_name(folder)}'s Discord channel (and saves a copy)."
    return None, why.replace("saves a report file", "saves an idea file")


# ---- cleaning text -------------------------------------------------------------------------------------------------------------

def scrub(text, home=None):
    """Your Windows user folder (C:\\Users\\Name...) becomes ~, so a log does not give away who's PC it came from."""
    text = text or ""
    home = os.path.expanduser("~") if home is None else home
    if home and home != "~" and len(home) > 3:
        for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            text = re.sub(re.escape(variant), "~", text, flags=re.I)
    return re.sub(r"(?i)(?:[A-Z]:)?[\\/]+Users[\\/]+[^\\/\s\"']+", "~", text)


def clean_field(text, limit=FIELD_LIMIT):
    """Text typed or pasted into the form: no control characters, one \\n for line breaks, at most `limit` characters."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    text = "".join(ch for ch in text if ch == "\n" or ch.isprintable())
    return text[:limit]


def slug(text, limit=24):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return s[:limit] or "player"


# ---- facts about the computer and the game -----------------------------------------------------------------------------------

def java_line():
    try:
        import forge_client as fc
        java = fc.find_java()
        if not java:
            return "Java: not found"
        return f"Java {fc.java_major(java)} ({java})"
    except Exception as e:
        return f"Java: unknown ({type(e).__name__})"


def environment_lines(folder=None):
    # Round 28: pass folder through as-is (don't default it to BASE_DIR here) - crashlog.environment(None)
    # already resolves its own current folder (crashlog.install()'s _cfg["folder"], which itself defaults
    # through paths.log_dir()), so forcing BASE_DIR here would silently ignore that in an installed copy.
    lines = list(crashlog.environment(folder))
    lines.append(java_line())
    return lines


def game_line(state):
    """The game in one line: 'turn 4, Main phase, precombat; Karl 40, AI 1 (Kinnan) 37; prompt: ...'."""
    if not state:
        return "no game running (deck screen or engine not started)"
    parts = []
    turn, phase = state.get("turn"), state.get("phase")
    if turn:
        parts.append(f"turn {turn}" + (f", {phase}" if phase else ""))
    people = [f"{p.get('name')} {p.get('life')}" for p in state.get("players", []) if isinstance(p, dict)]
    if people:
        parts.append(", ".join(people))
    prompt = (state.get("prompt") or {}).get("message", "")
    if prompt:
        parts.append("prompt: " + " ".join(prompt.split())[:160])
    return "; ".join(parts) or "game state present but empty"


def _lines(seq, count):
    out = [str(x) for x in (seq or [])]
    return out[-count:]


# ---- the zip -----------------------------------------------------------------------------------------------------------------------

def report_text(info, state, env, when):
    who = (info.get("name") or "").strip() or "(no name given)"
    fmt = (info.get("format") or "commander").strip().lower()
    game = ([f"Format:   {fmt.title()}"] if fmt != "commander" else []) + [f"Game:     {game_line(state)}"]     # round FMT1
    out = ["Manticore bug report", "=" * 20, f"Sent by:  {who}", f"When:     {when} (this computer's clock)",
           f"Program:  {version.describe()}", f"Seed:     {info.get('seed')}"] + game + ["",
           "WHAT HAPPENED", "-------------", (info.get("happened") or "").strip() or "(nothing written)", "",
           "WHAT I EXPECTED", "---------------", (info.get("expected") or "").strip() or "(nothing written)", "",
           "COMPUTER", "--------"] + env
    return "\n".join(out) + "\n"


def _tail_file(path, lines):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [ln.rstrip("\n") for ln in f.readlines()][-lines:]
    except OSError:
        return []


def java_crash_files(path):
    """Patch 46: [(name, text)] for build_report's extra_files - Java's own crash report as a bug report carries it (no
    environment variables, shortened; build_report scrubs it like everything else), or [] when there is none."""
    if not isinstance(path, str) or not os.path.isfile(path):
        return []
    try:
        text = java_crash.excerpt(path)
    except Exception as e:                                     # a report must never fail because of this file
        text = f"(Java's crash report {os.path.basename(path)} could not be read: {type(e).__name__})\n"
    return [("java_crash_" + os.path.splitext(os.path.basename(path))[0] + ".txt", text)]


def _deck_files_in(deck_dir):
    found = []
    try:
        for name in sorted(os.listdir(deck_dir)):
            path = os.path.join(deck_dir, name)
            if name.lower().endswith(".dck") and os.path.isfile(path) and os.path.getsize(path) < 200_000:
                found.append((name, path))
    except OSError:
        pass
    return found


def _zip_bytes(files):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()


def build_report(info, state=None, log_lines=None, commands=None, screenshot=None, folder=None, dest_dir=None,
                 max_bytes=MAX_ZIP_BYTES, now=None, extra_env=None, extra_files=None):
    """Write the report zip and return its path.
    info: {"name", "happened", "expected", "seed"};  state: the latest snapshot (dict) or None;  log_lines: game log text lines;
    commands: [(seconds, command dict)];  screenshot: a function(max_width) -> (bytes, "jpg"/"png") or None.
    Round 28: an explicit `folder` still means "everything lives together in this one folder" (unchanged,
    for tests and anything else that passes it); leaving it out (the production default) instead pulls each
    piece from its own paths.py location, since an installed copy no longer keeps them all in one place."""
    if folder:
        dest_dir = dest_dir or os.path.join(folder, "bug_reports")
        deck_dir = os.path.join(folder, "forge_decks")
        crash_path = os.path.join(folder, crashlog.LOG_NAME)
    else:
        dest_dir = dest_dir or paths.bug_reports_dir()
        deck_dir = paths.forge_decks_dir()
        crash_path = crashlog.log_path()
    now = now or datetime.datetime.now()
    when = now.strftime("%Y-%m-%d %H:%M:%S")
    env = [scrub(x) for x in environment_lines(folder)] + [scrub(str(x)) for x in (extra_env or [])]      # extra_env: e.g. the frame-time line
    text_log = _lines(log_lines, LOG_LINES)
    engine = _lines(crashlog.engine_tail(folder, ENGINE_LINES), ENGINE_LINES)
    crash = _tail_file(crash_path, 250)
    complete = len(commands or []) <= COMMAND_LINES
    cmds = [{"t": round(t, 2), **c} if isinstance(c, dict) else {"t": round(t, 2), "cmd": c} for t, c in (commands or [])][-COMMAND_LINES:]
    decks = [(n, _tail_file(p, 400)) for n, p in _deck_files_in(deck_dir)]

    def assemble(shot_width, log_n, engine_n, crash_n, with_state, with_commands):
        files = [("report.txt", scrub(report_text(info, state, env, when)))]
        if screenshot and shot_width:
            try:
                shot = screenshot(shot_width)
            except Exception as e:                                  # a failed picture must not lose the report
                files.append(("screenshot_failed.txt", f"{type(e).__name__}: {e}\n"))
                shot = None
            if shot:
                files.append((f"screenshot.{shot[1]}", shot[0]))
        if with_state and state is not None:
            files.append(("state.json", scrub(json.dumps(state, indent=1, default=str))))
        files.append(("game_log.txt", scrub("\n".join(text_log[-log_n:])) + "\n"))
        if with_commands:
            files.append(("commands.json", json.dumps({"seed": info.get("seed"), "complete": complete, "commands": cmds}, indent=1, default=str)))
        for name, rows in decks:
            files.append((f"decks/{name}", "\n".join(rows) + "\n"))
        if engine:
            files.append(("forge_engine.log.txt", scrub("\n".join(engine[-engine_n:])) + "\n"))
        if crash and crash_n:
            files.append(("crash_log.txt", scrub("\n".join(crash[-crash_n:])) + "\n"))
        for name, text in extra_files or []:                  # round 28d: e.g. a crashed run's Forge log (last_session.py)
            files.append((name, scrub(text)))
        return files

    # try the full report, then smaller and smaller until it fits
    attempts = [(SHOT_WIDTHS[0], LOG_LINES, ENGINE_LINES, 250, True, True), (SHOT_WIDTHS[1], LOG_LINES, ENGINE_LINES, 250, True, True),
                (SHOT_WIDTHS[2], 200, 100, 120, True, True), (SHOT_WIDTHS[3], 100, 60, 60, True, True),
                (SHOT_WIDTHS[3], 60, 30, 30, False, False), (0, 40, 20, 20, False, False)]
    data = b""
    for args in attempts:
        data = _zip_bytes(assemble(*args))
        if len(data) <= max_bytes:
            break
    os.makedirs(dest_dir, exist_ok=True)
    name = f"bugreport_{now.strftime('%Y%m%d_%H%M%S')}_{slug(info.get('name'))}.zip"
    path = os.path.join(dest_dir, name)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(dest_dir, name[:-4] + f"_{n}.zip")
    with open(path, "wb") as f:
        f.write(data)
    return path


def summary_text(info, when=None):
    """The message that goes with the zip in Discord (at most 1900 characters; @mentions are switched off when sending)."""
    who = (info.get("name") or "").strip() or "someone"
    happened = (info.get("happened") or "").strip() or "(nothing written)"
    expected = (info.get("expected") or "").strip()
    text = f"**Bug report from {who}**  |  {version.short()}\n**What happened:** {happened}"
    if expected:
        text += f"\n**Expected:** {expected}"
    if when:
        text += f"\n*{when}*"
    return text if len(text) <= DISCORD_CAP else text[:DISCORD_CAP - 3] + "..."


# ---- ideas (Round FR1) ------------------------------------------------------------------------------------------------------------

def idea_text(info, when):
    """The local copy of an idea (idea_YYYYMMDD_HHMMSS.txt): who, when, which copy, where in the game, the idea and why."""
    who = (info.get("name") or "").strip() or "(no name given)"
    area = (info.get("area") or "").strip() or "(not given)"
    out = ["Manticore feature idea", "=" * 22, f"Sent by:  {who}", f"When:     {when} (this computer's clock)",
           f"Program:  {version.describe()}", f"Area:     {area}", "",
           "IDEA", "----", (info.get("idea") or "").strip() or "(nothing written)", "",
           "WHAT IT WOULD HELP WITH", "-----------------------", (info.get("why") or "").strip() or "(nothing written)"]
    if info.get("picture"):
        out += ["", f"Picture:  {info['picture']}"]
    return scrub("\n".join(out) + "\n")


def idea_summary(info, when=None):
    """The Discord message for an idea: 'Feature idea from Karl | Manticore 0.28.x | Area: Table', then Idea and Why (at most
    DISCORD_CAP characters; @mentions are switched off when sending)."""
    who = (info.get("name") or "").strip() or "someone"
    head = f"**Feature idea from {who}**  |  {version.short()}"
    area = (info.get("area") or "").strip()
    if area:
        head += f"  |  Area: {area}"
    text = head + f"\n**Idea:** {(info.get('idea') or '').strip() or '(nothing written)'}"
    why = (info.get("why") or "").strip()
    if why:
        text += f"\n**Why:** {why}"
    if when:
        text += f"\n*{when}*"
    return scrub(text if len(text) <= DISCORD_CAP else text[:DISCORD_CAP - 3] + "...")


def save_idea(info, folder=None, dest_dir=None, now=None, screenshot=None):
    """Write the idea's text file (and, when `screenshot` - a function(max_width) -> (bytes, ext) - is given, its picture beside it).
    Returns (text path, picture path or None). An explicit `folder` means "everything lives together" (tests), as in build_report."""
    dest_dir = dest_dir or (os.path.join(folder, "bug_reports") if folder else paths.bug_reports_dir())
    now = now or datetime.datetime.now()
    os.makedirs(dest_dir, exist_ok=True)
    stem = f"idea_{now.strftime('%Y%m%d_%H%M%S')}"
    n = 1
    while os.path.exists(os.path.join(dest_dir, stem + ".txt")):
        n += 1
        stem = f"idea_{now.strftime('%Y%m%d_%H%M%S')}_{n}"
    picture = None
    if screenshot:
        try:
            data, ext = screenshot(SHOT_WIDTHS[0])
            picture = os.path.join(dest_dir, f"{stem}_screen.{ext}")
            with open(picture, "wb") as f:
                f.write(data)
        except Exception:                                   # a failed picture must not lose the idea
            picture = None
    info = dict(info, picture=os.path.basename(picture) if picture else None)
    path = os.path.join(dest_dir, stem + ".txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(idea_text(info, now.strftime("%Y-%m-%d %H:%M:%S")))
    return path, picture


# ---- sending -----------------------------------------------------------------------------------------------------------------------

def seconds_until_send_allowed(now=None):
    """0 when a report may be sent now, else how many seconds to wait."""
    now = time.time() if now is None else now
    return max(0, int(MIN_SECONDS_BETWEEN_SENDS - (now - _last_send[0]) + 0.999)) if _last_send[0] else 0


def _interpret(status, body_text=""):
    """(ok, kind, message for the player) for an HTTP status from Discord."""
    if status in (200, 201, 204):
        return True, "sent", "Sent! Your bug report has been submitted."
    if status == 413:
        return False, "too_big", "Discord refused the file because it is too big."
    if status == 429:
        return False, "rate", "Discord says to slow down; try again in a minute."
    if status in (401, 403, 404):
        return False, "bad_webhook", "Discord no longer accepts this webhook address (it may have been deleted)."
    return False, "error", f"Discord answered with an error (code {status})."


FILE_TYPES = {".zip": "application/zip", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def post_to_discord(url, text, zip_path=None, timeout=(6, 40), session=None, username=BOT_NAME):
    """Post `text` (and the file: a report's zip, or an idea's picture) to a Discord webhook. Returns (ok, kind, message); never
    raises. Round FR1: `username` is the name the message shows ("Manticore idea" for ideas)."""
    problem = webhook_problem(url)
    if problem:
        return False, "bad_webhook", problem[0].upper() + problem[1:] + "."
    try:
        import requests
    except ImportError:
        return False, "error", "The 'requests' package is not installed."
    payload = {"content": text[:DISCORD_CAP], "username": username, "allowed_mentions": {"parse": []}}
    try:
        http = session or requests
        target = url.strip() + ("&" if "?" in url else "?") + "wait=true"
        if zip_path:
            with open(zip_path, "rb") as f:
                r = http.post(target, data={"payload_json": json.dumps(payload)},
                              files={"files[0]": (os.path.basename(zip_path), f,
                                                  FILE_TYPES.get(os.path.splitext(zip_path)[1].lower(), "application/octet-stream"))},
                              timeout=timeout)
        else:
            r = http.post(target, json=payload, timeout=timeout)
        return _interpret(r.status_code, getattr(r, "text", ""))
    except requests.exceptions.Timeout:
        return False, "offline", "Discord did not answer in time (slow or no connection)."
    except requests.exceptions.ConnectionError:
        return False, "offline", "Could not reach Discord (no internet connection?)."
    except OSError as e:
        return False, "error", f"Could not read the report file ({e})."
    except Exception as e:
        return False, "error", f"Sending failed ({type(e).__name__})."


class SendJob(threading.Thread):
    """Posts a report in the background; the game window keeps drawing. Check .finished and read .result."""

    def __init__(self, url, text, zip_path, username=BOT_NAME):
        super().__init__(daemon=True)
        self.url, self.text, self.zip_path, self.username = url, text, zip_path, username
        self.result = None                              # (ok, kind, message)
        self.finished = False

    def run(self):
        try:
            self.result = post_to_discord(self.url, self.text, self.zip_path, username=self.username)
        except Exception as e:                          # post_to_discord should never raise; belt and braces
            self.result = (False, "error", f"Sending failed ({type(e).__name__}).")
        if self.result[0]:
            _last_send[0] = time.time()
        self.finished = True


def send_test(folder=None, name=None):
    """--report-test: post one short test message. Returns (ok, message) for the terminal."""
    url, why = config_status(folder)
    if not url:
        return False, (why + f" To set it up: copy bug_report_config.example.json to {CONFIG_NAME} and paste your Discord webhook "
                       "address into it (README.txt, BUG REPORTS).")
    text = f"Test message from {name or 'a Manticore install'}  |  {version.describe(folder or BASE_DIR)}"
    ok, _kind, message = post_to_discord(url, text)
    return ok, message


# ---- helpers for the window --------------------------------------------------------------------------------------------------

def open_folder(path):
    """Show a folder in the file manager. Returns True if it was started."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                                                     # noqa: only exists on Windows
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def describe_size(path):
    try:
        n = os.path.getsize(path)
    except OSError:
        return ""
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"
