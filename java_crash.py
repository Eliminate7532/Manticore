# SPDX-License-Identifier: GPL-3.0-or-later
"""
java_crash.py - Java's own crash reports (patch 46, from soak night 14's game 75).

When the Java virtual machine itself dies - not a Forge exception, the whole process: game 75 was an EXCEPTION_ACCESS_VIOLATION
in JIT-compiled Forge code - it writes hs_err_pid<pid>.log, by default into its working folder. For the bridge that is
forge_runtime/. Three things went wrong with that file:
  * tools/build_installer.py copies forge_runtime/ whole, so the next installer would have carried it to testers - and it holds
    the PC's user name, PATH and temp folder ("Environment Variables") and the bridge's command line;
  * nothing looked at it: not the soak's report, not an F8 report (they take forge_engine.log and crash_log.txt);
  * the table's "Forge stopped" message sent the player to forge_engine.log, which had nothing.

What this module does (forge_client, reporting, forge_table, tools/soak.py, tools/build_installer.py and tools/check_dist.py
use it):
  * jvm_flags(folder): the bridge's Java writes its crash report - and the JIT compiler's replay file, if the compiler itself
    crashed - into `folder` (the folder forge_engine.log is in), and never a core dump / minidump. Checked with OpenJDK 21.0.12:
    the file lands there with the pid filled in; if `folder` doesn't exist Java falls back to its WORKING folder
    (forge_runtime/), not the temp folder - so forge_client creates the folder first, and the sweep and the build check below
    stay as backstops.
  * move_strays(src, dest): crash files found in forge_runtime/ (from before this patch, or a fallback) are moved out at the
    next start, into the log folder.
  * report_for_pid(pid, *folders): the crash report one Java process wrote, if it wrote one.
  * summary(path): one line - what happened, where and on which thread.
  * excerpt(path): what a bug report carries - the "Environment Variables" block and any user/computer-name line left out,
    shortened. reporting.scrub() then hides user-folder paths, as it does for every file in a report.
  * is_crash_file(name): for the installer build and check_dist.
  * stopped_message(engine_log, crash): the table's "Forge stopped" text.
"""
import fnmatch
import os
import re

# hs_err: the crash report. replay_pid: written when the JIT compiler thread itself crashes. *.mdmp: a Windows minidump (off by
# default on client Windows; -XX:-CreateCoredumpOnCrash below keeps it off everywhere).
CRASH_PATTERNS = ("hs_err_pid*.log", "replay_pid*.log", "*.mdmp")

HEAD_LINES = 400            # an excerpt keeps the summary, the crashing thread and the start of the process section ...
SYSTEM_LINES = 30           # ... plus the start of the SYSTEM section (OS, CPU, memory)
_ENV_HEADER = "environment variables:"
# Belt and braces: any line naming the user or the computer goes, wherever it is (the block above should hold them all).
_IDENTITY_LINE = re.compile(r"^\s*(USERNAME|USERDOMAIN|USERDOMAIN_ROAMINGPROFILE|USERPROFILE|COMPUTERNAME|LOGONSERVER|HOMEPATH|"
                            r"HOMEDRIVE|HOME|USER|LOGNAME|ONEDRIVE\w*)\s*=", re.I)


def jvm_flags(folder):
    """The Java options that put this process's crash files in `folder` (patch 46)."""
    return [f"-XX:ErrorFile={os.path.join(folder, 'hs_err_pid%p.log')}",
            f"-XX:ReplayDataFile={os.path.join(folder, 'replay_pid%p.log')}",
            "-XX:-CreateCoredumpOnCrash"]


def is_crash_file(name):
    base = os.path.basename(str(name).replace("\\", "/"))
    return any(fnmatch.fnmatch(base.lower(), p.lower()) for p in CRASH_PATTERNS)


def find(folder):
    """The crash files in `folder` (not its subfolders), newest first. [] when there are none or it can't be read."""
    try:
        names = [n for n in os.listdir(folder) if is_crash_file(n)]
    except (OSError, TypeError):
        return []
    paths = [os.path.join(folder, n) for n in names if os.path.isfile(os.path.join(folder, n))]

    def mtime(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0.0
    return sorted(paths, key=mtime, reverse=True)


def move_strays(src, dest):
    """Move crash files out of `src` (forge_runtime/) into `dest` (the log folder). Never raises; returns the new paths.
    A name already taken in `dest` gets _2, _3, ... (Java reuses pids across a long run)."""
    moved = []
    if not src or not dest:
        return moved
    try:
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dest)):
            return moved
    except (OSError, ValueError, TypeError):
        return moved
    for path in find(src):
        name = os.path.basename(path)
        root, ext = os.path.splitext(name)
        target, n = os.path.join(dest, name), 1
        while os.path.exists(target):
            n += 1
            target = os.path.join(dest, f"{root}_{n}{ext}")
        try:
            os.makedirs(dest, exist_ok=True)
            os.replace(path, target)
            moved.append(target)
        except OSError:
            pass                                   # locked or read-only: the build check still keeps it out of an installer
    return moved


def report_for_pid(pid, *folders):
    """Path of hs_err_pid<pid>.log in the first of `folders` that has it, else None."""
    if not pid:
        return None
    name = f"hs_err_pid{pid}.log"
    for folder in folders:
        if folder:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    return None


def _read_lines(path, limit=20000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            out = []
            for i, line in enumerate(f):
                if i >= limit:
                    break
                out.append(line.rstrip("\r\n"))
            return out
    except (OSError, TypeError, ValueError):       # never raises: a missing file, or something that isn't a path
        return []


_ERROR_LINE = re.compile(r"^#\s+([A-Z][A-Z0-9_]+)\b")       # "#  EXCEPTION_ACCESS_VIOLATION (0xc0000005) at pc=..." / "#  SIGSEGV (0xb) ..."
_METHOD = re.compile(r"([\w$]+(?:\.[\w$]+)+\.(?:<init>|<clinit>|[\w$]+))\(")
_THREAD = re.compile(r'^Current thread \(.*?\):\s+\w+\s+"([^"]+)"')


def summary(path):
    """'EXCEPTION_ACCESS_VIOLATION in forge.util.collect.FCollection.<init> (thread Game-0)', or a shorter line when the
    report doesn't say as much. Never raises."""
    lines = _read_lines(path, 400)
    what, where, thread = None, None, None
    for i, line in enumerate(lines):
        if what is None and line.startswith("# A fatal error has been detected"):
            for nxt in lines[i + 1:i + 4]:
                m = _ERROR_LINE.match(nxt)
                if m:
                    what = m.group(1)
                    break
        elif what is None and line.startswith("# There is insufficient memory"):
            what = "out of native memory"
        elif where is None and line.startswith("# Problematic frame:") and i + 1 < len(lines):
            frame = lines[i + 1].lstrip("# ").strip()
            m = _METHOD.search(frame)
            if m:
                where = m.group(1)
            else:
                lib = re.search(r"\[([^\]]+)\]", frame)
                where = lib.group(1) if lib else frame[:80]
        elif thread is None:
            m = _THREAD.match(line)
            if m:
                thread = m.group(1)
    if not what and not where:
        return "Java crashed (no details in its report)" if lines else "Java crashed"
    text = what or "Java crashed"
    if where:
        text += f" in {where}"
    if thread:
        text += f" (thread {thread})"
    return text


def excerpt(path, head=HEAD_LINES, system=SYSTEM_LINES):
    """The crash report as a bug report carries it: no environment variables, no user/computer-name lines, shortened."""
    lines = _read_lines(path)
    kept, skipping, dropped = [], False, 0
    for line in lines:
        if line.strip().lower() == _ENV_HEADER:
            skipping = True
            kept.append("Environment Variables: (left out of bug reports - they name the user and the PC's folders)")
            continue
        if skipping:
            if line.strip() == "" or line.startswith("---------------"):
                skipping = False
            else:
                dropped += 1
                continue
        if _IDENTITY_LINE.match(line):
            dropped += 1
            continue
        kept.append(line)
    if len(kept) <= head + system:
        return "\n".join(kept) + "\n"
    sys_at = next((i for i, ln in enumerate(kept) if "S Y S T E M" in ln), None)
    out = kept[:head]
    if sys_at is not None and sys_at >= head:
        out.append(f"... ({sys_at - head} lines left out) ...")
        out += kept[sys_at:sys_at + system]
    else:
        out.append(f"... ({len(kept) - head} lines left out) ...")
    return "\n".join(out) + "\n"


def stopped_message(engine_log, crash):
    """The table's "Forge stopped" text when the bridge didn't say why (it died, or its output ended)."""
    if crash:
        return (f"Java itself crashed, so this game can't go on. Its crash report is {os.path.basename(crash)} in "
                f"{os.path.dirname(crash)}. Press F8 before closing to send a bug report: it includes that crash report.")
    where = os.path.dirname(engine_log) if engine_log else ""
    return ("The Forge engine stopped unexpectedly. See forge_engine.log" + (f" in {where}" if where else "") + " for details.")
