# SPDX-License-Identifier: GPL-3.0-or-later
"""
bridge_rules.py - rule checks over a recorded bridge game (Round 28b).

Round 27d gave the bridge self-checks and "shape" reports (Checks.java, FORGE_REPEAT_BEHAVIOURS.md
section D). Those catch a wrong ANSWER at the moment it is given. This module looks at the whole
recorded stream afterwards and catches what can only be seen from outside: a commander left in
exile with no question ever asked about it, a question nobody ever answered, a prompt that was
supposed to show a card but didn't, the engine dying, or the bridge dropping too many clicks.

No pygame and no Forge in here - just the messages a game already produced (a record file, or a
live session's own history), so it runs anywhere, including the offline Linux VM.

    check_stream(messages, engine_log_lines=()) -> [Finding, ...]
    Finding = {"rule": str, "severity": "fail" | "warn", "at": int, "detail": str}

`messages` is a recorded game in order, both directions (see tests/fixtures/bridge/README.md):
Forge's own messages ("state", "request", "check", "shape", "event", "log", "info"/"message",
"dropped", "game_over", "fatal", "ready", "_exit") and what we sent it ("_sent", holding "cmd").

Don't add a rule here that needs to know what a card does (e.g. "Spiteful Visions should give an
extra draw") - that belongs in a scenario test (tests/test_round28b_scenarios.py). A rule in this
module has to make sense for ANY game.
"""
import gzip
import json
import os
import re

import card_check

FAIL, WARN = "fail", "warn"

# ---------------------------------------------------------------------------------------------
# loading a recorded game
# ---------------------------------------------------------------------------------------------


def load_stream(path):
    """The messages of a recorded game, in order, from a .jsonl or .jsonl.gz file."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_engine_log(path):
    """The lines of an engine log (forge_engine.log / <name>.engine.log), or [] when there is none."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------


def _finding(rule, severity, at, detail):
    return {"rule": rule, "severity": severity, "at": at, "detail": detail}


def _prompt_text(m):
    return ((m.get("prompt") or {}).get("message")) or ""


def _iter_commander_cards(state):
    """(card, zone, owner) for every card in this state's zones that carries "commander": true."""
    for p in state.get("players", []) or []:
        for zone, cards in (p.get("zones") or {}).items():
            for c in cards or []:
                if c.get("commander"):
                    yield c, zone, c.get("owner")


# The header Forge puts on some prompts: "<name> (<id>)" then a blank line (see
# tests/fixtures/bridge/README.md, "Card headers in prompts"). Other prompts that carry a source
# have a different shape (a cost line, a creature's type line, more text on the same line as the
# id), so this only matches the one form actually seen with a card behind it.
CARD_HEADER_RE = re.compile(r"^.+\(\d+\)\s*\r?\n\s*\r?\n", re.M)

# claude/SIM_BUGHUNT_50GAMES_2026-09-25.md Finding 2: a pre-existing Forge engine warning that shows
# up in otherwise-clean games and is not something this project introduced.
ENGINE_LOG_ALLOWLIST = (
    re.compile(r"Did not have activator set in SpellAbility_Condition\.checkConditions\(\)"),
    # Round 29a: the loop guard's own note (Round 28e). It is reported as the ai_loop_guard warning from the bridge's message;
    # as an engine-log line it made soak night 10's games 160 and 240 "engine_error" failures.
    re.compile(r"^bridge: loop guard: "),
)


# ---------------------------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------------------------


def _rule_bridge_check(messages):
    out = []
    for i, m in enumerate(messages):
        if m.get("t") != "check":
            continue
        severity = WARN if m.get("rule") == "auto_answered" else FAIL
        out.append(_finding("bridge_check", severity, i,
                            "%s / %s: %s" % (m.get("q"), m.get("rule"), m.get("detail"))))
    return out


def _rule_commander_stranded(messages):
    """A commander of mine left stranded in exile or the graveyard, with no question ever asked
    about the command zone before my next Priority prompt. See tests/fixtures/bridge/README.md."""
    out = []
    limbo = {}     # card id -> True while it sits in exile/graveyard and hasn't been asked about yet
    asked = {}     # card id -> True once a "command zone" prompt/request was seen since it entered limbo
    mine = {}      # card id -> was it mine (owner == me) when it entered limbo
    since = {}     # card id -> the question number (inputSeq) BEFORE it entered limbo (round 28ba, see below)
    prev_seq = None
    reported = set()

    def note_text(cid, text):
        if limbo.get(cid) and "command zone" in (text or "").lower():
            asked[cid] = True

    for i, m in enumerate(messages):
        t = m.get("t")
        if t == "request":
            text = json.dumps(m)
            for cid in list(limbo):
                note_text(cid, text)
            continue
        if t != "state":
            continue
        me = m.get("me")
        prompt_msg = _prompt_text(m)
        seen_this_state = set()
        for card, zone, owner in _iter_commander_cards(m):
            cid = card.get("id")
            seen_this_state.add(cid)
            in_limbo_zone = zone.lower() in ("exile", "graveyard")
            if in_limbo_zone and not limbo.get(cid):
                limbo[cid] = True
                asked[cid] = False
                mine[cid] = owner == me
                since[cid] = prev_seq
            elif not in_limbo_zone and limbo.get(cid):
                limbo[cid] = False
                asked[cid] = False
            if limbo.get(cid) and not asked.get(cid):
                note_text(cid, prompt_msg)
        if prompt_msg.startswith("Priority:") and m.get("asking") is not False:
            # Round 29b: a snapshot with "asking": false is Forge between two questions; its prompt text is the old one (the
            # bridge keeps the last question's text). Scenario 7's remaining flake (about 1 run in 4): the commander already in
            # exile, a NEW question number, input "" and asking false, the stale "Priority" text - and the command-zone question
            # came in the very next snapshot. Only a priority question Forge is really asking counts. (Recordings from before
            # round 28bb have no "asking" field and are judged as before.)
            for cid in list(limbo):
                # Round 28ba: only a priority question asked AFTER the commander left counts. A snapshot can show the
                # commander already in exile while its prompt is still the old "Priority ... Stack: 1 to Resolve" - the same
                # question number as the last state before it left: the zones and the prompt text are updated separately.
                # Found live: scenario 7 failed about 1 run in 4 on exactly that.
                seq, entered = m.get("inputSeq"), since.get(cid)
                if seq is not None and entered is not None and seq <= entered:
                    continue
                if not mine.get(cid):
                    # Round 28bb: an AI's commander is not reported at all. The AI decides about the command zone without
                    # asking anyone, so no question ever comes and every AI commander that died was "stranded": 17 games
                    # of pure noise in soak night 1 (27 Sept). Checked: game 4's warning was Light-Paws, an AI's.
                    continue
                if limbo[cid] and not asked.get(cid) and cid not in reported:
                    severity = FAIL
                    out.append(_finding("commander_stranded", severity, i,
                                        "commander %s stranded with no command-zone question before this Priority prompt" % cid))
                    reported.add(cid)
                    asked[cid] = True     # don't repeat the same finding at every later Priority prompt
        if m.get("inputSeq") is not None:
            prev_seq = m.get("inputSeq")
    return out


def _rule_prompt_card_missing(messages):
    """warn at first: a card-header prompt with no prompt.source behind it."""
    for i, m in enumerate(messages):
        if m.get("t") != "state":
            continue
        prompt = m.get("prompt") or {}
        msg = prompt.get("message") or ""
        if CARD_HEADER_RE.match(msg) and not prompt.get("source"):
            return [_finding("prompt_card_missing", WARN, i,
                             "prompt has a card header but no source: %r" % msg.splitlines()[0])]
    return []


def _rule_unanswered_request(messages):
    pending = {}     # request id -> message index
    last_request_at = None
    out = []
    for i, m in enumerate(messages):
        t = m.get("t")
        if t == "request":
            pending[m.get("id")] = i
            last_request_at = i
        elif t == "_sent":
            cmd = m.get("cmd") or {}
            if cmd.get("c") == "reply":
                pending.pop(cmd.get("id"), None)
        elif t == "game_over":
            for rid, at in sorted(pending.items(), key=lambda kv: kv[1]):
                out.append(_finding("unanswered_request", FAIL, at,
                                    "request %s never answered before game_over" % rid))
            pending = {}
    for rid, at in sorted(pending.items(), key=lambda kv: kv[1]):
        if at == last_request_at:
            continue          # the stream simply ends (a stopped test): the last request gets no blame
        out.append(_finding("unanswered_request", FAIL, at,
                            "request %s never answered before the stream ends" % rid))
    return out


_STACK_LINE = re.compile(r"^\s+(at |\.\.\. \d+ more)")
# Round 28ba: Forge gives its AI a time limit to pick a spell; when it runs out Forge logs a TimeoutException from
# forge.ai.AiController and plays on. Seen on the first real night (game 86): the game carried on normally. A warning,
# so it stays visible, but not a failure.
_AI_THINK_TIMEOUT = re.compile(r"TimeoutException")
# Round 28d: an exception thrown inside a Guava EventBus subscriber (Forge's GameLogFormatter) is logged by java.util.logging
# as two header lines - "<date> com.google.common.eventbus.EventBus$LoggingHandler handleException" and "SEVERE: Exception
# thrown by subscriber method ..." - and only then the exception with its trace. The headers have no trace of their own, so
# each was reported as a separate engine_error FAIL even when the exception under them was Forge-internal in a finished game
# (soak night 5, game 123). They belong to the exception below them, which is judged by itself.
_LOG_HEADER = re.compile(r"EventBus\$LoggingHandler handleException|^SEVERE: Exception thrown by subscriber")


# Round 28e: when Forge stops an AI that has thought past its time limit, it interrupts that thread. If the thread was just
# handing an event to Swing at that moment, the JDK prints "AWT blocker activation interrupted:" and an InterruptedException
# from sun.awt.AWTAutoShutdown, and carries on. Soak night 9, game 144 (three of them, in a game with 20 AI timeouts). Part of
# the AI time limit, like ai_think_timeout: a warning, whether or not the game finished.
_INTERRUPTED = re.compile(r"java\.lang\.InterruptedException")
_AWT_SHUTDOWN = "sun.awt.AWTAutoShutdown"


def _rule_engine_error(messages, engine_log_lines):
    """One finding per problem, not per line: a Java stack trace's "at ..." lines belong to the exception line above them
    (round 28ba: one AI timeout used to be reported as 20 engine errors)."""
    out = []
    lines = list(engine_log_lines)
    finished = any(m.get("t") == "game_over" for m in messages)
    for n, line in enumerate(lines):
        if _STACK_LINE.match(line) or _CAUSED_BY.match(line):
            continue                      # part of the exception above it (round 28bc: "Caused by:" too)
        if not card_check.PROBLEM_LINE.search(line):
            continue
        if "CHECK FAILED" in line:
            continue                      # that's bridge_check's job
        if _LOG_HEADER.search(line):
            continue                      # round 28d: a logging header; the exception it announces follows
        if any(rx.search(line) for rx in ENGINE_LOG_ALLOWLIST):
            continue
        following = " ".join(l.strip() for l in lines[n + 1:n + 4] if _STACK_LINE.match(l))
        if _AI_THINK_TIMEOUT.search(line) and "forge.ai." in following:
            out.append(_finding("ai_think_timeout", WARN, -1, "engine log line %d: %s %s" % (n, line.strip(), following[:120])))
            continue
        block = _exception_block(lines, n)
        if _INTERRUPTED.search(line) and any(_AWT_SHUTDOWN in b for b in block):
            out.append(_finding("ai_interrupted", WARN, -1, "engine log line %d: %s (an AI thread stopped at its time limit)" % (
                n, line.strip())))
            continue
        if finished and block and not _bridge_code_in(block) and "bridge:" not in line:
            # Round 28bc: an exception inside Forge's own code - no bridge frame anywhere in its trace - in a game that still
            # reached game over. Soak night 2 had two (a ConcurrentModificationException in Tracker.unfreeze, a
            # StackOverflowError in the AI's think thread); the game went on both times. Shown as a warning, not hidden:
            # it's Forge's bug, not ours, and it didn't stop the game. Anything that stops the game, or has a bridge frame,
            # stays a failure.
            out.append(_finding("forge_internal_error", WARN, -1, "engine log line %d: %s | %s" % (
                n, line.strip(), " ".join(l.strip() for l in block[:3])[:160])))
            continue
        out.append(_finding("engine_error", FAIL, -1, "engine log line %d: %s" % (n, line)))
    return out


_CAUSED_BY = re.compile(r"^\s*Caused by: ")

# Patch 37 (soak night 12, game 131; night 10's game 59 too): since Round 28e every AI choice goes through the loop guard,
# forge.bridge.LoopGuard$Controller.chooseSpellAbilityToPlay, which only counts and hands the call on to Forge's own
# PlayerControllerAi. So every exception from the AI's think thread carries that one bridge frame, and a Forge AI
# StackOverflowError in a finished game was a FAIL where before 28e it was a forge_internal_error warning. A LoopGuard frame
# whose callee (the frame printed just above it) is outside the bridge is that hand-on, not bridge code. One where the
# exception starts (the first frame of a trace or of a "Caused by:") is the guard's own, and still counts.
_LOOP_GUARD_FRAME = re.compile(r"^\s+at forge\.bridge\.LoopGuard[$.]")


def _bridge_code_in(block):
    """True when a stack trace has a frame of the bridge's own code (the loop guard handing a call on doesn't count)."""
    for i, line in enumerate(block):
        if "forge.bridge." not in line:
            continue
        if _LOOP_GUARD_FRAME.match(line) and i > 0:
            callee = block[i - 1]
            if _STACK_LINE.match(callee) and "forge.bridge." not in callee and callee.strip().startswith("at "):
                continue
        return True
    return False


def _exception_block(lines, n):
    """The stack trace under an exception line: its "at ..." lines and any "Caused by:" parts with their own lines."""
    block = []
    for line in lines[n + 1:]:
        if _STACK_LINE.match(line) or _CAUSED_BY.match(line):
            block.append(line)
        else:
            break
    return block


def _rule_fatal(messages):
    out = []
    saw_game_over = False
    saw_quit = False
    for i, m in enumerate(messages):
        t = m.get("t")
        if t == "fatal":
            out.append(_finding("fatal", FAIL, i, m.get("text") or "Forge stopped"))
        elif t == "game_over":
            saw_game_over = True
        elif t == "_sent" and (m.get("cmd") or {}).get("c") == "quit":
            saw_quit = True
        elif t == "_exit" and not saw_game_over and not saw_quit:
            out.append(_finding("fatal", FAIL, i, "the stream ended (_exit) without game_over and without a quit command"))
    return out


def _rule_dropped_clicks(messages):
    idxs = [i for i, m in enumerate(messages) if m.get("t") == "dropped"]
    if len(idxs) > 5:
        return [_finding("dropped_clicks", WARN, idxs[5], "%d dropped commands in this game" % len(idxs))]
    return []


# Round 28e: the same ability used over and over in one turn. Soak nights 7 and 9: the Kinnan AI untapped two Grim Monoliths
# with each other 6,530 and 7,134 times, gaining nothing, until the 30-minute limit; the state kept changing, so no stall rule
# saw it, and the game was reported with no finding. The bridge now stops an AI after LoopGuard's limit (10 per ability per
# turn, see LoopGuard.java), so this firing means the guard did not work - or a player (the soak bot) is looping.
AI_LOOP_LIMIT = 30
_ACTIVATED = re.compile(r"^(?P<who>.+?) activated (?P<card>.+)$")


def _rule_ai_loop(messages):
    out = []
    turn = None
    counts = {}
    reported = set()
    for i, m in enumerate(messages):
        t = m.get("t")
        if t == "state":
            if m.get("turn") != turn:
                turn = m.get("turn")
                counts = {}
        elif t == "log":
            for e in m.get("entries") or []:
                if e.get("type") != "STACK_ADD":
                    continue
                hit = _ACTIVATED.match(e.get("text") or "")
                if not hit:
                    continue
                key = (hit.group("who"), hit.group("card"))
                counts[key] = counts.get(key, 0) + 1
                if counts[key] > AI_LOOP_LIMIT and (turn, key) not in reported:
                    reported.add((turn, key))
                    out.append(_finding("ai_loop", FAIL, i, "%s activated %s more than %d times in turn %s" % (
                        key[0], key[1], AI_LOOP_LIMIT, turn)))
    return out


def _rule_loop_guard(messages):
    """Round 28e: the bridge stopped an AI repeating an ability. Working as intended, but worth seeing: a warning."""
    hits = [(i, m) for i, m in enumerate(messages) if m.get("t") == "ai_loop_guard"]
    if not hits:
        return []
    i, m = hits[0]
    cards = sorted({h.get("card") or "?" for _j, h in hits})
    return [_finding("ai_loop_guard", WARN, i, "the AI loop guard stopped %d repeat(s): %s" % (len(hits), ", ".join(cards)))]


# ---------------------------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------------------------

RULES = (
    _rule_bridge_check,
    _rule_commander_stranded,
    _rule_prompt_card_missing,
    _rule_unanswered_request,
    _rule_fatal,
    _rule_dropped_clicks,
    _rule_ai_loop,
    _rule_loop_guard,
)


def check_stream(messages, engine_log_lines=()):
    """Every Finding a recorded game trips, in message order within each rule."""
    out = []
    for rule in RULES:
        out.extend(rule(messages))
    out.extend(_rule_engine_error(messages, list(engine_log_lines)))
    return out


def has_fail(findings):
    return any(f["severity"] == FAIL for f in findings)
