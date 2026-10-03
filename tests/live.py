# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/live.py - one place that decides whether the "live" tests (the ones that start the real Forge
engine) are allowed to run. Round 27a: before this, five test files each grew their own copy of the
same check (three read fc.runtime_problem(), two rebuilt it by hand as `LIVE = os.path.isfile(...) and
fc.find_java() is not None`), so `MANTICORE_SKIP_LIVE` needed teaching to every copy separately.

    import tests.live as live
    PROBLEM = live.live_problem()
    @unittest.skipIf(PROBLEM, f"Forge is not ready here: {PROBLEM}")
    ...
    @unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
"""
import os

import forge_client as fc


def live_problem(runtime=None):
    """None when the live tests are allowed to run, else a plain-language sentence saying why not - either
    that Forge itself is not set up (see forge_client.runtime_problem), or that MANTICORE_SKIP_LIVE asked
    for them to be skipped (tools/quick_tests.py sets this so the fast set never starts the Java engine)."""
    if os.environ.get("MANTICORE_SKIP_LIVE"):
        return "MANTICORE_SKIP_LIVE is set"
    return fc.runtime_problem(runtime)


def live_enabled(runtime=None):
    """True when it is safe to start the real Forge engine here."""
    return live_problem(runtime) is None
