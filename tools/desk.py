# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/desk.py - Round PAR1 (Karl, 11 Oct 2026: "I really need to be able to run simultaneous sessions and have them aware of
each other"). Every session works at its own DESK, and a BOARD says who is working where.

A desk is a git worktree, commander_sim/.worktrees/<name>, on its own branch round-<name>: a whole copy of the project that
shares the main folder's history. Edits and commits there never touch the main folder (Karl's: he plays from it, the backup
commits it every 30 minutes, merges happen there) or another desk. The board is commander_sim/.sessions/<name>.md, one file per
session, written only by that session. Neither folder is ever committed (.gitignore); backup.py pushes the desks' branches.

    python tools\\desk.py list                       who is working where, what each desk changes, and where two meet
    python tools\\desk.py new <name> --who "Claude (Sonnet, Cowork)" [--base main] [--files a.py,b.py] [--note "..."]
    python tools\\desk.py new <name> --who "Claude (Opus, cloud)" --elsewhere "cloud sandbox" [--files ...]
                                                     a board entry only, for a session that builds somewhere else
    python tools\\desk.py note <name> "what I'm doing now" [--files a.py,b.py]
    python tools\\desk.py done <name>                finished: the desk stays (its branch has the work) until Karl removes it
    python tools\\desk.py remove <name>              Karl: delete a finished desk's folder (its branch stays)
    python tools\\desk.py merging start "<who>" "<what>"   /   python tools\\desk.py merging stop
    python tools\\desk.py check                      like list; exit code 1 when sessions overlap or the main folder has edits

Across the device bridge, Karl's Windows PC and the Linux VM that Cowork sessions use reach the same folder by two different
absolute paths, and the VM's git (2.34) cannot write relative worktree links. So `new` rewrites the desk's .git file to a
RELATIVE link ("gitdir: ../../.git/worktrees/<name>"), which both read, and sets gc.worktreePruneExpire=never: the link back
from .git/worktrees/<name> to the desk is one machine's absolute path, and `git worktree prune` (or gc) on the other machine
would take the desk for a deleted one and drop it. Never run `git worktree prune` or `git worktree repair` in this project.
Board files are written in place (no temporary file renamed over them): the device bridge may refuse deletes in Karl's folder.
"""
import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKS = ".worktrees"
BOARD = ".sessions"
MERGING = "MERGING.md"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
NOT_NAMES = {"merging", "readme", "finished"}
# Changes in the main folder that are not someone editing the program: patches dropped for Karl, the old one-line SESSION.md.
MAIN_OK_PREFIXES = ("docs/incoming/", "docs/SESSION.md")
FIELDS = ("desk", "branch", "base", "who", "where", "started", "updated", "ended", "status", "files", "note")


class DeskError(Exception):
    pass


# ---- git ---------------------------------------------------------------------------------------------------------------

def git(args, cwd, check=True):
    """Runs git. GIT_OPTIONAL_LOCKS=0: a read (status, diff) never takes another session's index lock to refresh it."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        p = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace")
    except OSError as e:
        raise DeskError(f"git could not be started: {e}")
    if check and p.returncode != 0:
        raise DeskError(f"git {' '.join(args)} failed:\n{(p.stderr or p.stdout).strip()}")
    return p.returncode, (p.stdout or "").rstrip()     # rstrip only: `status --porcelain` starts lines with a space


def main_folder(start=HERE):
    """The main folder (the one holding the shared .git), from the main folder or from inside any desk."""
    code, out = git(["rev-parse", "--git-common-dir"], start, check=False)
    out = out.strip()
    if code != 0 or not out:
        return os.path.abspath(start)
    common = os.path.normpath(os.path.join(start, out))
    return os.path.dirname(common)


def make_link_relative(desk):
    """Rewrites desk/.git ("gitdir: <absolute path>") as a path relative to the desk, so the same link works from Windows and
    from the Linux VM. True when it changed something."""
    path = os.path.join(desk, ".git")
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if not text.startswith("gitdir:"):
        raise DeskError(f"{path} is not a worktree link")
    target = text[len("gitdir:"):].strip()
    if not os.path.isabs(target):
        return False
    # realpath on both: Windows may name the same folder by its short 8.3 form (C:\Users\KARLRI~1\...) on one side and its
    # long form on the other, and a relative path between the two forms would climb out and back in.
    rel = os.path.relpath(os.path.realpath(target), os.path.realpath(desk)).replace(os.sep, "/")
    # "r+", then truncate - never "w": Git for Windows marks a new file named .git HIDDEN (core.hideDotFiles=dotGitOnly), and
    # Windows refuses to open an existing hidden file with CREATE_ALWAYS, which is what "w" asks for (PermissionError; Karl's
    # PC, 11 Oct: every test that made a desk errored). Opening it as it is and rewriting it in place works, hidden or not.
    with open(path, "r+", encoding="utf-8", newline="\n") as f:
        f.seek(0)
        f.write(f"gitdir: {rel}\n")
        f.truncate()
    return True


def admin_dir(desk):
    """The desk's own folder inside the shared .git (.git/worktrees/<name>), from its .git link."""
    with open(os.path.join(desk, ".git"), encoding="utf-8") as f:
        target = f.read().strip()[len("gitdir:"):].strip()
    return os.path.normpath(os.path.join(desk, target))


def desk_changes(desk, base):
    """Files this desk has changed since `base` - committed or not - plus new files. None when git can't read the desk."""
    code, out = git(["diff", "--name-only", base], desk, check=False)
    if code != 0:
        return None
    files = set(out.splitlines())
    code, out = git(["ls-files", "--others", "--exclude-standard"], desk, check=False)
    if code == 0:
        files |= set(out.splitlines())
    return sorted(f for f in files if f)


def main_changes(main):
    """(branch the main folder is on, [files changed in it and not committed]) - leaving out patch folders under docs/incoming/."""
    _code, branch = git(["rev-parse", "--abbrev-ref", "HEAD"], main, check=False)
    code, out = git(["status", "--porcelain", "--untracked-files=all"], main, check=False)
    files = []
    if code == 0:
        for line in out.splitlines():
            name = line[3:].strip()
            if " -> " in name:
                name = name.split(" -> ", 1)[1]
            name = name.strip('"')
            if name and not name.startswith(MAIN_OK_PREFIXES):
                files.append(name)
    return branch.strip() or "?", sorted(set(files))


# ---- the board -----------------------------------------------------------------------------------------------------------

def now():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")


def check_name(name):
    if not NAME_RE.match(name or "") or name in NOT_NAMES or name.split(".")[0] in RESERVED:
        raise DeskError(f"'{name}' can't be a desk name: lower-case letters, digits, '.', '-' and '_' only, at most 40, not a "
                        f"name Windows reserves (con, nul, com1 ...)")
    return name


def branch_for(name):
    return name if name.startswith("round-") else f"round-{name}"


def board_path(main, name):
    return os.path.join(main, BOARD, f"{name}.md")


def read_entry(path):
    entry = {"notes": []}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if not sep:
            continue
        if key == "note":
            entry["notes"].append(value.strip())
        elif key in FIELDS:
            entry[key] = value.strip()
    entry["name"] = os.path.splitext(os.path.basename(path))[0]
    return entry


def write_entry(path, entry):
    """In place, no rename: the device bridge may refuse to delete or replace files in Karl's folder."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [f"# Desk board entry - written by tools/desk.py; read by every session (python tools\\desk.py list)"]
    for key in FIELDS:
        if key == "note":
            lines += [f"note: {n}" for n in entry.get("notes", [])]
        elif entry.get(key):
            lines.append(f"{key}: {entry[key]}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def entries(main):
    folder = os.path.join(main, BOARD)
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".md") and n != MERGING)
    except OSError:
        return []
    return [e for e in (read_entry(os.path.join(folder, n)) for n in names) if e]


def split_files(text):
    return sorted({f.strip().replace("\\", "/") for f in (text or "").split(",") if f.strip()})


def merging_state(main):
    """The MERGING.md entry when someone is merging, else None."""
    e = read_entry(os.path.join(main, BOARD, MERGING))
    return e if e and e.get("status") == "merging" else None


def old_session_line(main):
    try:
        with open(os.path.join(main, "docs", "SESSION.md"), encoding="utf-8") as f:
            return " / ".join(ln.strip() for ln in f if ln.strip())
    except OSError:
        return ""


# ---- what list shows -----------------------------------------------------------------------------------------------------

def survey(main):
    """Everything `list` and `check` report, as data: {sessions: [...], main: (branch, files), overlaps: {file: [who...]},
    merging, old_line}."""
    sessions = []
    touch = {}
    for e in entries(main):
        active = e.get("status", "working") not in ("done", "removed")
        desk = os.path.join(main, DESKS, e["name"])
        changed = None
        if os.path.isfile(os.path.join(desk, ".git")):
            base = (e.get("base") or "").split()[-1:] or ["HEAD"]
            changed = desk_changes(desk, base[0])
        expects = split_files(e.get("files"))
        sessions.append(dict(e, active=active, desk_exists=os.path.isdir(desk), changed=changed, expects=expects))
        if active:
            for f in set(changed or []) | set(expects):
                touch.setdefault(f, []).append(e["name"])
    branch, files = main_changes(main)
    for f in files:
        touch.setdefault(f, []).append("main folder")
    overlaps = {f: who for f, who in sorted(touch.items()) if len(who) > 1}
    return {"sessions": sessions, "main": (branch, files), "overlaps": overlaps, "merging": merging_state(main),
            "old_line": old_session_line(main)}


def short_list(files, n=8):
    files = list(files)
    return ", ".join(files[:n]) + (f", +{len(files) - n} more" if len(files) > n else "")


def report(main, s):
    out = [f"Board: {os.path.join(main, BOARD)}"]
    m = s["merging"]
    if m:
        out.append(f"MERGING NOW: {m.get('who', '?')} - {m.get('notes', [''])[0] if m.get('notes') else ''} (since {m.get('started', '?')})")
    branch, files = s["main"]
    if files:
        out.append(f"Main folder: on {branch}; {len(files)} file(s) changed and not committed: {short_list(files)}")
        out.append("  -> someone is editing the main folder itself; desks can't see those edits until they are committed.")
    else:
        out.append(f"Main folder: on {branch}; no program edits waiting.")
    if s["old_line"]:
        out.append(f"Old docs/SESSION.md line (a session that started under the old rule): {s['old_line']}")
    active = [x for x in s["sessions"] if x["active"]]
    out.append("")
    out.append(f"{len(active)} session(s) working" + (f", {len(s['sessions']) - len(active)} finished" if len(s["sessions"]) > len(active) else "") + ":")
    for x in s["sessions"]:
        where = (f"desk {DESKS}/{x['name']}" if x["desk_exists"] else f"elsewhere: {x.get('where') or 'not on this PC'}")
        out.append(f"  {x['name']:<16} {x.get('status', 'working'):<9} {x.get('who', '?')}  [{x.get('branch', '-')}; {where}; "
                   f"since {x.get('started', '?')}]")
        if not x["active"]:
            continue
        if x["changed"] is not None:
            out.append(f"  {'':<16} changes {len(x['changed'])} file(s): {short_list(x['changed']) or '-'}")
        elif x["desk_exists"]:
            out.append(f"  {'':<16} (git can't read this desk from here)")
        if x["expects"]:
            out.append(f"  {'':<16} expects to change: {short_list(x['expects'])}")
        for n in x.get("notes", [])[-2:]:
            out.append(f"  {'':<16} note: {n}")
    if s["overlaps"]:
        out.append("")
        out.append("OVERLAPS - the same file in two places; agree an order with Karl before merging:")
        for f, who in s["overlaps"].items():
            out.append(f"  {f}: {', '.join(who)}")
    return "\n".join(out)


# ---- commands ------------------------------------------------------------------------------------------------------------

def cmd_new(main, a):
    name = check_name(a.name)
    path = board_path(main, name)
    old = read_entry(path)
    if old and old.get("status") not in ("done", "removed"):
        raise DeskError(f"'{name}' is already on the board ({old.get('who', '?')}, {old.get('status', 'working')}). Pick another "
                        f"name, or `desk.py done {name}` first if it's yours.")
    entry = {"who": a.who, "started": now(), "status": "working", "files": ", ".join(split_files(a.files)),
             "notes": [a.note] if a.note else []}
    if a.elsewhere:
        entry.update(where=a.elsewhere, branch=a.branch or "-")
        write_entry(path, entry)
        print(f"On the board: {path} ({a.elsewhere}). No desk made.")
        return 0
    desk = os.path.join(main, DESKS, name)
    if os.path.exists(desk):
        raise DeskError(f"{desk} already exists.")
    branch = a.branch or branch_for(name)
    base = a.base or "main"
    base_sha = git(["rev-parse", "--verify", f"{base}^{{commit}}"], main)[1].strip()
    exists = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], main, check=False)[0] == 0
    git(["config", "gc.worktreePruneExpire", "never"], main)
    os.makedirs(os.path.join(main, DESKS), exist_ok=True)
    git(["worktree", "add", desk, branch] if exists else ["worktree", "add", "-b", branch, desk, base_sha], main)
    make_link_relative(desk)
    fork = git(["merge-base", base_sha, "HEAD"], desk, check=False)[1].strip()
    entry.update(desk=f"{DESKS}/{name}", branch=branch, base=f"{base} {fork or base_sha}", where="this PC")
    write_entry(path, entry)
    print(f"Desk ready: {desk}\n  branch {branch}, from {base} ({(fork or base_sha)[:7]})\n  board: {path}\n"
          f"Work and commit only there (commit at least every 30 minutes: the backup pushes this branch, not uncommitted edits).\n"
          f"Live-Forge tests there need the desk's own engine: python setup_forge.py inside the desk.")
    return 0


def cmd_note(main, a):
    path = board_path(main, check_name(a.name))
    e = read_entry(path)
    if not e:
        raise DeskError(f"'{a.name}' is not on the board.")
    if a.text:
        e["notes"] = (e.get("notes") or []) + [f"{now()}  {a.text}"]
    if a.files is not None:
        e["files"] = ", ".join(split_files(a.files))
    if a.status:
        e["status"] = a.status
    e["updated"] = now()
    write_entry(path, e)
    print(f"Updated {path}")
    return 0


def cmd_done(main, a):
    path = board_path(main, check_name(a.name))
    e = read_entry(path)
    if not e:
        raise DeskError(f"'{a.name}' is not on the board.")
    e.update(status="done", ended=now())
    write_entry(path, e)
    desk = os.path.join(main, DESKS, a.name)
    print(f"Marked done: {path}" + (f"\nThe desk stays until Karl removes it: python tools\\desk.py remove {a.name}"
                                     if os.path.isdir(desk) else ""))
    return 0


def cmd_remove(main, a):
    name = check_name(a.name)
    path = board_path(main, name)
    e = read_entry(path) or {}
    desk = os.path.join(main, DESKS, name)
    if e.get("status") != "done" and not a.force:
        raise DeskError(f"'{name}' isn't marked done (status: {e.get('status', 'not on the board')}). Its session first: "
                        f"desk.py done {name} (or --force).")
    if os.path.isdir(desk):
        left = desk_changes(desk, "HEAD")
        if left is None or left:
            raise DeskError(f"{desk} has work that is not committed ({short_list(left or ['git cannot read it'])}). Nothing removed.")
        admin = admin_dir(desk)
        # Not `git worktree remove`: a desk made on the other side of the device bridge isn't in this git's list by its path.
        shutil.rmtree(desk)
        if os.path.basename(os.path.dirname(admin)) == "worktrees":
            shutil.rmtree(admin, ignore_errors=True)
    if e:
        e.update(status="removed", ended=e.get("ended") or now())
        write_entry(path, e)
    print(f"Removed the desk {desk}; its branch {e.get('branch', branch_for(name))} is kept.")
    return 0


def cmd_merging(main, a):
    path = os.path.join(main, BOARD, MERGING)
    current = merging_state(main)
    if a.action == "start":
        if current:
            raise DeskError(f"{current.get('who', '?')} is merging already (since {current.get('started', '?')}). Wait for "
                            f"`desk.py merging stop`.")
        write_entry(path, {"who": a.who or "?", "status": "merging", "started": now(), "notes": [a.what or ""]})
        print(f"Merging: {a.who} - {a.what}")
    else:
        write_entry(path, {"status": "idle", "ended": now(), "notes": [f"last: {current.get('who', '?')}" if current else ""]})
        print("Merging: nobody.")
    return 0


def main(argv=None, start=HERE):
    ap = argparse.ArgumentParser(prog="desk.py", description="Desks (git worktrees) and the board of who is working where.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    p = sub.add_parser("new")
    p.add_argument("name")
    p.add_argument("--who", required=True)
    p.add_argument("--base", default=None)
    p.add_argument("--branch", default=None)
    p.add_argument("--files", default="")
    p.add_argument("--note", default="")
    p.add_argument("--elsewhere", default=None, help="a board entry only: where this session builds (e.g. 'cloud sandbox')")
    p = sub.add_parser("note")
    p.add_argument("name")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--files", default=None)
    p.add_argument("--status", default=None)
    p = sub.add_parser("done")
    p.add_argument("name")
    p = sub.add_parser("remove")
    p.add_argument("name")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("merging")
    p.add_argument("action", choices=("start", "stop"))
    p.add_argument("who", nargs="?", default="")
    p.add_argument("what", nargs="?", default="")
    a = ap.parse_args(argv)
    root = main_folder(start)
    try:
        if a.cmd in ("list", "check"):
            s = survey(root)
            print(report(root, s))
            return 1 if a.cmd == "check" and (s["overlaps"] or s["main"][1]) else 0
        return {"new": cmd_new, "note": cmd_note, "done": cmd_done, "remove": cmd_remove, "merging": cmd_merging}[a.cmd](root, a)
    except DeskError as e:
        print(f"desk.py: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
