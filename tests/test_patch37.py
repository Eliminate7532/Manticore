# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 37 (4 Oct 2026).

1. The installer on Windows 11 on Arm. Karl's Surface Pro 11 (Snapdragon X) said "This program does not support the version of
   Windows your computer is running": Inno Setup's message for a processor the script doesn't allow. ArchitecturesAllowed=x64
   (x64os) only allows x64 Windows; x64compatible adds Windows 11 on Arm, which runs the x64 build through emulation.
2. The loop guard's frame in the soak's verdict. Since Round 28e every AI choice passes through
   forge.bridge.LoopGuard$Controller.chooseSpellAbilityToPlay, so a Forge AI StackOverflowError in a game that finished was a FAIL
   ("a bridge frame") instead of a forge_internal_error warning: soak night 12 game 131 and night 10 game 59. Both logs are
   fixtures, with the repeating middle of the StackOverflowError's trace cut to 60 frames.
3. Bug and crash reports say "AMD64 on ARM64 (emulated)" when the x64 build runs on an Arm processor (crashlog.machine_text)."""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge_rules as br

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(HERE, "tests", "fixtures", "soak")


def read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


def rules(messages, lines):
    return [(f["rule"], f["severity"]) for f in br.check_stream(messages, lines)]


class InstallerArchitectureTests(unittest.TestCase):
    def setting(self, key):
        m = re.search(r"^%s=(.*)$" % key, read("installer", "commander_sim.iss"), re.M)
        self.assertIsNotNone(m, key)
        return m.group(1).strip()

    def test_windows_11_on_arm_is_allowed(self):
        self.assertEqual(self.setting("ArchitecturesAllowed"), "x64compatible")
        self.assertEqual(self.setting("ArchitecturesInstallIn64BitMode"), "x64compatible")

    def test_start_here_says_which_pcs(self):
        text = read("START_HERE.txt")
        self.assertIn("Windows 11 on an Arm PC", text)


class ReportsSayArmTests(unittest.TestCase):
    """An x64 build under Windows 11 on Arm sees AMD64 in platform.machine(); IsWow64Process2's native machine says ARM64."""

    def test_an_emulated_run_is_named(self):
        """Patch 38: on the Surface, platform.machine() itself said ARM64 for the x64 build; the build's own architecture comes
        from sysconfig.get_platform()."""
        import crashlog
        import sysconfig
        with mock.patch.object(sysconfig, "get_platform", return_value="win-amd64"), \
                mock.patch.object(crashlog.platform, "machine", return_value="ARM64"), \
                mock.patch.object(crashlog, "native_machine", return_value="ARM64"):
            self.assertEqual(crashlog.machine_text(), "AMD64 on ARM64 (emulated)")
            self.assertTrue(any("AMD64 on ARM64 (emulated)" in l for l in crashlog.environment(HERE)))

    def test_a_native_run_is_unchanged(self):
        import crashlog
        import sysconfig
        for native in ("AMD64", None):
            with mock.patch.object(sysconfig, "get_platform", return_value="win-amd64"), \
                    mock.patch.object(crashlog.platform, "machine", return_value="AMD64"), \
                    mock.patch.object(crashlog, "native_machine", return_value=native):
                self.assertEqual(crashlog.machine_text(), "AMD64")
        with mock.patch.object(sysconfig, "get_platform", return_value="win-arm64"), \
                mock.patch.object(crashlog, "native_machine", return_value="ARM64"):
            self.assertEqual(crashlog.machine_text(), "ARM64")                   # a native Arm build some day

    def test_off_windows_there_is_no_native_machine(self):
        import crashlog
        if sys.platform == "win32":
            self.assertIn(crashlog.native_machine(), ("AMD64", "ARM64", "x86", None))
        else:
            self.assertIsNone(crashlog.native_machine())


class LoopGuardFrameTests(unittest.TestCase):
    def lines(self, name):
        return read("tests", "fixtures", "soak", name).splitlines()

    def test_the_two_ai_stack_overflows_are_warnings_in_a_finished_game(self):
        for name in ("night12_game131_engine.log", "night10_game059_engine.log"):
            lines = self.lines(name)
            self.assertTrue(any("forge.bridge.LoopGuard$Controller.chooseSpellAbilityToPlay" in l for l in lines), name)
            self.assertEqual(rules([{"t": "game_over"}], lines), [("forge_internal_error", "warn")], name)

    def test_they_are_still_failures_when_the_game_did_not_finish(self):
        for name in ("night12_game131_engine.log", "night10_game059_engine.log"):
            self.assertEqual(rules([], self.lines(name)), [("engine_error", "fail")], name)

    def test_an_exception_thrown_in_the_loop_guard_itself_is_a_failure(self):
        lines = ["java.lang.NullPointerException: Cannot invoke \"java.util.Map.merge\"",
                 "\tat forge.bridge.LoopGuard$Controller.chooseSpellAbilityToPlay(LoopGuard.java:95)",
                 "\tat forge.game.phase.PhaseHandler.mainLoopStep(PhaseHandler.java:1056)"]
        self.assertEqual(rules([{"t": "game_over"}], lines), [("engine_error", "fail")])

    def test_a_caused_by_that_starts_in_the_loop_guard_is_a_failure(self):
        lines = ["java.util.concurrent.ExecutionException: java.lang.IllegalStateException",
                 "\tat forge.ai.AiController.chooseSpellAbilityToPlayFromList(AiController.java:1685)",
                 "Caused by: java.lang.IllegalStateException",
                 "\tat forge.bridge.LoopGuard$Controller.count(LoopGuard.java:120)",
                 "\tat forge.bridge.LoopGuard$Controller.chooseSpellAbilityToPlay(LoopGuard.java:90)"]
        self.assertEqual(rules([{"t": "game_over"}], lines), [("engine_error", "fail")])

    def test_another_bridge_frame_beside_the_loop_guard_is_a_failure(self):
        lines = ["java.lang.IllegalStateException",
                 "\tat forge.ai.PlayerControllerAi.chooseSpellAbilityToPlay(PlayerControllerAi.java:850)",
                 "\tat forge.bridge.LoopGuard$Controller.chooseSpellAbilityToPlay(LoopGuard.java:88)",
                 "\tat forge.bridge.Snapshot.card(Snapshot.java:127)"]
        self.assertEqual(rules([{"t": "game_over"}], lines), [("engine_error", "fail")])

    def test_the_check_is_on_whole_frames_only(self):
        self.assertFalse(br._bridge_code_in(["\tat forge.ai.AiController.x(AiController.java:1)",
                                             "\tat forge.bridge.LoopGuard$Controller.chooseSpellAbilityToPlay(LoopGuard.java:88)"]))
        self.assertTrue(br._bridge_code_in(["\tat forge.bridge.LoopGuard$Controller.chooseSpellAbilityToPlay(LoopGuard.java:88)"]))
        self.assertTrue(br._bridge_code_in(["\tat forge.bridge.Main.handle(Main.java:1)"]))


if __name__ == "__main__":
    unittest.main()
