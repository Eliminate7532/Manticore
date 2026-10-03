# -*- mode: python ; coding: utf-8 -*-
"""
commander_sim.spec - the PyInstaller recipe for the Manticore installer build (Round 29, SONNET_SPEC_R29 section 5).

Run it through tools/build_installer.py (that script writes the icon and the Windows version resource this file points at, then
runs PyInstaller, then adds forge_runtime/ and jre/ to the finished folder - NOT here: 37,000 card scripts through PyInstaller's
analysis would be slow and pointless).

  * onedir, no UPX (UPX packed programs are what Windows Defender likes least);
  * contents_directory='.': the program's own files sit next to the .exe, not in _internal\\. Without this paths.program_dir()
    (the exe's folder) and forge_client's old __file__-based paths disagreed (measured by Opus on 1 Oct);
  * TWO executables from ONE analysis, in one folder:
        Manticore.exe        windowed: the game (no console window; sys.stdout is None - forge_table.ensure_std_streams copes)
        Manticore-cli.exe    console: --version, --soak N and --report-test, whose output the installer's checks read
"""
import os

ROOT = os.path.abspath(SPECPATH)                               # SPECPATH is provided by PyInstaller: this file's folder
ICON = os.environ.get("MANTICORE_BUILD_ICON") or None           # an .ico made by build_installer.py from assets/splash/icon.png
VERSION_FILE = os.environ.get("MANTICORE_BUILD_VERSION_FILE") or None   # a Windows version resource made from version.VERSION


def _data(name, dest=None):
    return (os.path.join(ROOT, name), dest if dest is not None else name)


a = Analysis(
    [os.path.join(ROOT, "forge_table.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        _data("sample_decks"),
        _data("sounds"),
        _data("assets"),
        _data("licenses"),
        _data("LICENSE", "."),
        _data("THIRD_PARTY_NOTICES.txt", "."),
        _data("START_HERE.txt", "."),              # the testers' first page, opened from the installer's last page
        _data("banned_commander.snapshot.json", "."),    # Round BAN1: the banned list for a copy that's never been online
        _data("banned_brawl.snapshot.json", "."),        # Round FMT1: the same for MTG Arena's Brawl
        _data("update_config.json", "."),          # Round 30: where an installed copy looks for updates
    ],
    # Modules only imported lazily or by name: --soak imports tools.soak, which imports the rest inside _load_modules().
    # Opus's Linux probe needed exactly these (SONNET_SPEC_R29 section 1.3); add whatever the first Windows build's warnings
    # file (build\\pyinstaller\\commander_sim\\warn-commander_sim.txt) names as missing.
    hiddenimports=[
        "tools.soak", "soak_bot", "soak_report", "soak_boards", "bridge_rules", "card_check", "deck_loader",
    ],
    hookspath=[],
    runtime_hooks=[],
    # NOT tkinter: forge_menu's clipboard paste/copy falls back to it when pygame.scrap can't (pasting a deck is a main flow).
    excludes=["unittest", "pydoc_data"],
    noarchive=False,
)
pyz = PYZ(a.pure)

_common = dict(
    exclude_binaries=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    icon=ICON,
    version=VERSION_FILE,
    contents_directory=".",
)
exe_game = EXE(pyz, a.scripts, [], name="Manticore", console=False, **_common)
exe_cli = EXE(pyz, a.scripts, [], name="Manticore-cli", console=True, **_common)

coll = COLLECT(
    exe_game,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Manticore",
)
