Manticore - Setup Instructions
==============================

WHAT THIS IS
------------
A work-in-progress Python program for playing Magic: The Gathering Commander against
AI opponents. There are now TWO tables in this folder:

  forge_table.py   THE NEW ONE. The rules are run by Forge (the open-source Java Magic
                   engine): real stack and priority, combat with blockers, targeting,
                   triggers, replacement effects, state-based actions, and Forge's own AI
                   for the opponents. This program only draws what Forge says (Arena-style
                   table) and sends your clicks back. Up to 3 AI opponents (a pod).
  table_gui.py     THE OLD ONE. Our own simplified rules engine written in Python. Kept as a
                   fallback until the new table has been played enough. See the last section.

Both use real card data and art from Scryfall (downloaded once, then cached) and the same
deck import (Moxfield/Archidekt text export, or an Archidekt URL).

LOOK AND FEEL
-------------
The table has a dark, warm "Diablo 1" look. Text uses two fonts that ship in the assets/ folder: Alegreya (body text, SIL Open
Font License 1.1) and a display face for titles and buttons. The table sits on a picture: Graveyard, Cathedral, Citadel or Ruins,
or Plain. In the cog menu (top right), DISPLAY, the Table row picks one with < and >; "Rotate" (the default) changes the picture
every game. Keep the assets/ folder next to the program; if it is missing, the program falls back to system fonts and a plain
dark gradient. Credits and licences: cog menu > Licences (the display font and the pictures are marked "not established" until
their licences are confirmed).
The program opens on a short studio splash (a click skips it) and the title screen with the main menu: Play (the deck screen),
Continue (your unfinished game), Settings, Credits and Quit. Starting a game shows the commanders face to face while the rules
engine starts, then your opening hand full-screen (keep, or mulligan and choose the cards to put on the bottom). The end of a game
shows VICTORY or DEFEAT before the usual window. During play, short animations show what the AI just did (its card large in the
top-right panel), damage and life changes, whose turn it is, and attacks. Cog > Animations turns the animations off.

SETUP (new table)
-----------------
1. Python 3.10 or newer, then, from inside this folder:
       python -m pip install -r requirements.txt
   (requirements.txt pins the exact versions the program is tested with: pygame-ce 2.5.8, requests 2.34.2 and what requests needs.
   A newer pygame-ce you already have will be replaced by 2.5.8; that is intended.)
2. Java 17 or newer (Forge is a Java program). In a terminal:
       java -version
   If that says the command is not found, or a version below 17:
       winget install EclipseAdoptium.Temurin.21.JRE
   Then CLOSE the terminal and open a NEW one (so it can find Java).
3. Unpack the Forge engine (one time, about a minute, ~200 MB written to forge_runtime/):
       python setup_forge.py --check
   It checks the pieces in forge_bundle/ against their checksums, unpacks them, finds Java,
   and then starts a real test game to prove it all works. It prints OK or PROBLEM.
   When an update brings a new Forge (a new forge_bundle/, as in round FB1), run it again: it sees that forge_runtime/
   holds the older Forge and unpacks the new one. Until then the game refuses to start and says so.
4. Play:
       python forge_table.py
   This opens the DECK SCREEN (next section): pick your deck, pick the deck the AI plays, choose
   how many opponents (1-3), press Start game. To import a deck, copy its text list and press Ctrl+V.
   The old command-line ways still work and skip the deck screen (New game inside the game brings it back):
       python forge_table.py my_deck.txt                       (both sides play my_deck.txt)
       python forge_table.py my_deck.txt --opp ai_deck.txt --opp another.txt     (a pod of up to three)
   Other options: --name YourName   --seed 7 (repeatable shuffles)   --record file.jsonl (debug)
   The first start also downloads card art in the background (a few seconds), and Forge itself
   takes 10-20 seconds to start a game.

THE DECK SCREEN (start screen, and the "New game" button)
---------------------------------------------------------
Opens when you start with no deck on the command line, and any time you choose New game in the settings cog
(top right; or Ctrl+N; the game-over window has a New game button too).
  FORMAT (round FMT1), top right: Commander or Brawl (MTG Arena's 100-card Brawl). The screen shows that format's decks
        only, remembers its own picks for each format, and Start plays that format. Brawl: a legendary creature or
        planeswalker commander, 25 life (30 with 2 or 3 opponents), no commander damage, Arena's cards and banned list.
        Online games are Commander only for now.
  Left: the DECK LIBRARY - your imported decks first (A-Z), then the bundled decks. Click a deck to
        choose it. Each row shows the commander and the card count; YOU / AI tags show what is picked
        (with 2 or 3 opponents the tag says which: "AI 1,3").
        SEARCH (round 27b): just type - the box above the list filters it by deck name, commander or style
        (Typal, Voltron, ...); every word must match. Esc or the x clears it. Backspace deletes a letter.
  Right: YOUR DECK, then one box per AI opponent (AI 1, AI 2, AI 3). The gold-outlined box is the one the next
        click in the list fills: your deck first, then AI 1, then AI 2 ... by itself; click a box to change it.
        Each AI box has "Random" (a playable deck from your library is picked when you press Start - one nobody
        else at the table plays, if there is one; a message says which) and "Same as mine".
        An AI you haven't set plays the same deck as you. "Opponents" - / + sets 1, 2 or 3 of them.
  Start game (Enter). If a game is running it asks before ending it; "Back to the game" (Esc) leaves.
        If the four commanders at the table are the combination known to freeze Forge before turn 1
        (Valgavoth + Light-Paws + Kinnan + Ojer Axonil, found in the 2026-09-25 bug hunt), it asks first.
  Keys: Up/Down move through the (filtered) list for the box you are on, Tab / Shift+Tab walk through the
        boxes, Left/Right change the number of opponents, Ctrl+V imports the deck on your clipboard.
IMPORT A DECK: press "Import a deck" (or Ctrl+V on the deck screen). Copy the deck's TEXT list first -
  Moxfield: open the deck > Export > copy the text.  Archidekt: Export > Text.  MTG Arena: open the deck >
  Export.  A web link is not enough; the window says so. Format (top right of the window): Commander or Brawl;
  it starts on the deck screen's format, and the deck file gets a "# format: brawl" line (no line = Commander). Press "Paste from clipboard" (or Ctrl+V). The window shows the list, the commander it
  found, and warnings (not 100 cards; cards Forge does not know - they would be left out of the game).
  Type a name (it suggests the commander's name) and press "Save to library" (or Enter). The deck is
  saved as my_decks/<name>.txt and selected for you. If the clipboard cannot be read the window says so; you
  can also drag a .txt deck file onto the window.
REMOVE: select a deck and press Remove. Nothing is deleted: the file moves to my_decks/_removed/ (move it back
  into my_decks/ to get it back). The bundled sample can't be removed. You can also just drop your own .txt
  files into my_decks/ - they appear the next time the deck screen opens.
CARD ART (round ALT1): select a deck, press "Card art". Click a card to see every printing of it, then click the one you want
  ("Default" is Scryfall's usual picture). The choice is written into the deck file the way Moxfield writes it ("1 Sol Ring (C21)
  263"), so a deck exported from Moxfield already shows its printings. A deck that came with the program isn't changed: the first
  pick offers to save a copy in My decks. Your own pictures: put them in the my_art folder (next to my_decks) named after the card -
  "Sol Ring.png" (every printing) or "Sol Ring__C21_263.png" (one printing); png or jpg, at most 4096 px on a side; press "Reload
  my art" in the Card art window. They are backed up with your decks, but never published or (later) sent to other players, and
  the preview says "custom art". One printing per card name per deck: 15 Swamps can't have 3 different pictures yet.
MPC AUTOFILL ART (round ALT2): in the Card art window, click a card, then the "MPC Autofill" tab: that card's community-made
  renders from MPC Autofill (mpcfill.com), as small pictures (hover one for who made it and its DPI). Click one to use it: only that
  picture is downloaded (about 1000 pixels high, 1400 for the big preview), its print bleed cut off, into your card cache. The
  deck file gets one line at the top, "# art: Sol Ring = mpc:<id>"; the card's own line stays as Moxfield wrote it. Default, or
  any printing, takes it out again. Nothing is asked of mpcfill.com until you open that tab; a card's search is kept for a week.
  The pictures belong to the people who made them: they are never bundled, published or sent to other players.
  Patch 38: a picked MPC picture is downloaded at once (1400 pixels high) into mpc_art/ beside my_art/, not the card cache, so it
  stays if the cache is cleared, you're offline, or its maker takes it down. mpc_art/ is in the local backup zips but not pushed
  to GitHub. Card size: the "Card size" - and + buttons at the top of the Card art window (or the - and + keys, or Ctrl+wheel)
  make the pictures smaller or bigger, in every part of that window; the size is remembered.
PICTURES FOR ONE DECK (patch 40): in the Card art window, "Import pictures" (or drag a .zip, a folder of pictures, or one picture
  onto that window) puts pictures into THAT deck only - other decks don't change. Made for Proxxied's Export Card Images > ZIP
  Archive, but any .zip or folder of .png/.jpg pictures named after the cards works ("001 - Sol Ring.png", "Sol Ring.jpg").
  Matched by file name, without case or punctuation; a double-faced card's back ("Searstep Pathway") is matched through
  Scryfall's card data and shown when that face is up; a card back ("Default") is left out. The pictures are kept in deck_art/
  beside my_art/ (the card alone, bleed cut off, at most 1400 pixels high; about 25 MB for 100 cards), and the deck file gets
  "# art: <card> = image:<id>" lines at the top - its card lines stay as Moxfield wrote them. A report then lists what matched,
  which pictures aren't cards of the deck and which cards have no picture, with Undo. One picture dropped on a card's page is
  that card's picture, whatever the file is called. "Remove imported" takes them all out again (a printing or an MPC pick
  replaces one). deck_art/ is in the local backup zips but not pushed to GitHub, and never in the public source copy.
Your last choice (your deck, each AI's deck or Random, and the number of opponents) is remembered in settings.json.

PLAYING (press H in the game for the same list)
-----------------------------------------------
Forge asks the questions, the bar above your hand says what it wants. The two buttons at the
right of that bar are Forge's own OK and Cancel. When Cancel says "End Turn" or "Full Send"
that is what it does.

  Click a glowing card ........ play it / cast it / activate it (glow = Forge says you can)
  Click a card when asked ..... choose it (targets, discards, sacrifices, ...)
  Space or Enter .............. OK (pass priority / confirm / next step); not on the play/draw window
  E ........................... End Turn (when that is the Cancel button)
  A ........................... Full Send: attack with everything (when offered)
  Esc ......................... Cancel - but only when the button really says Cancel/Undo, so a
                                slip can't end your turn
  U or Ctrl+Z ................. undo - takes back a mana tap that is still unspent (see UNDO below)
  M ............................ sound on/off (round 24) - same switch as the cog's SOUND group
  S ........................... Skip window: let the stack resolve, skip to your next turn, auto-pass, "always pass" (see SKIPPING AHEAD)
  F11 ......................... fullscreen     + / - .... bigger / smaller text (100%-200%)
                                 Window and text size are remembered for each screen size (round 27e): your monitor and a
                                 Remote Desktop session each keep their own, and the window never opens bigger than the screen.
  H ........................... help           Ctrl+0 ... text size back to 100%
  F8 .......................... report a bug, or suggest an idea (see BUG REPORTS AND IDEAS)
  F3 .......................... speed figures: how fast the table draws, how quickly Forge answers, and (new bridge) event gaps and
                                clicks Forge dropped. Press again to hide. Each session also adds one line to perf_log.txt.
Window and text size, and your last deck choice, are remembered in settings.json (delete that file to reset).

RESUME LAST GAME (round 21)
----------------------------
Every game is written down as you play it (saves\current_game.jsonl: the seed, the decks and every click). If the program closes or
crashes in the middle of a game, the deck screen offers "Resume last game (N actions, when)". It starts Forge with the same seed and
decks and repeats your clicks, with a progress bar (Esc stops it). A finished game moves to saves\finished\ (the newest 10 are kept).
With the round-22 bridge every click also says which of Forge's questions it answered, so a click Forge ignored the first time is
ignored again on resume and the game comes back exactly. If a resume can't follow the old game, it says so in orange and you keep
playing from the last point it could reach.

FORGE HAS NOT ANSWERED (round 22)
----------------------------------
If you click and Forge says nothing back for 5 seconds (not while an AI is thinking), a thin orange bar appears under the top bar.
Usually Forge is just busy; the bar goes away as soon as it answers. If it stays, press F8 to report it: crash_log.txt already has a
line about it.

MOTION, SOUND AND EFFECTS (rounds 23-25)
-----------------------------------------
Hand cards lift a little when you hover them, dip when you click one, and glide (not snap) onto the battlefield. A card that leaves
the battlefield or your hand (dies, is exiled, bounced, discarded) leaves behind a brief fading "ghost" of itself gliding to the pile
it went to. Small bursts of sparks/motes mark a card landing, a life change, or a counter added, and taking a lot of damage jitters
that player's panel; losing 10 or more life at once (or losing the game) gives the whole table a brief shake. In a game with more than
one opponent, an attack shows a red line to the player being attacked, not just to a blocked/blocking creature. Cog > Animations off
turns ALL of this off (motion, ghosts, particles, shakes) and shows the same end states instantly, exactly as it always has for rings.
Sound plays for clicks, draws, casts, damage, life changes, combat and more; M or the cog's SOUND group turns it off, and
Volume/Hover tick are there too. Round AU1 replaced the synthesised beeps with real recordings in the game's dark-fantasy style
(stone, iron, leather, parchment, bells; all CC0, credited in sounds/CREDITS.txt): a quiet ambience loop under each table picture
(wind and crows in the graveyard, a far-off choir in the cathedral, a torch in the citadel, rain in the ruins), music on the title
screen and, in a game, a shuffled set of dark tracks with long silences between them. Cog > Music and Cog > Ambience turn those two
off. tools/build_sounds.py rebuilds every sound from tools/sound_sources.json (a developer tool: needs numpy, scipy and ffmpeg).

THE SETTINGS COG (top right)
----------------------------
The row of little buttons that used to sit at the top right is now ONE cog. Click it and a small panel drops down, in three groups
(round 9c: before this it was nine equal buttons, several of which did nearly the same thing):
  DISPLAY   Text size (A- / A+ and the percentage); Full screen, Animations and Compact board cards (round 26) are on/off
            SWITCHES (green ON, grey OFF). Clicking a switch flips it and the panel stays open, so you see it move. Compact board
            cards shrinks a small battlefield card down to an art-crop picture with a dark name strip and a type letter (C/A/E/L/P)
            instead of the plain shrunk card - all its counters/keywords/P&T badges still show. Default OFF.
  SOUND     (round 24) Sound on/off (same as the M key), Volume - / +  (10% steps), and a Hover tick switch that silences the
            little tick when you hover a hand card (everything else still plays). Round AU1 adds Music and Ambience switches.
  GAME      New game...   asks what you mean: "Same decks, new shuffle" (ends the game if one is running and starts another with
                exactly the decks you and the AI are playing now) or "Choose decks..." (the deck screen, same as Ctrl+N), or
                "Keep playing". Without a game to repeat (nothing started from the deck screen yet) it goes straight to the deck screen.
            Concede...    (red outline; greyed out when no game is running) asks: "Concede and stay on the table" (Forge counts it
                as a loss; in a game with 2 or more AIs the others play on and you get "You are out" with New game / Look at the
                board) or "Concede and close the program" (gives up AND quits in one step), or "Keep playing" (highlighted).
  HELP      Controls and help; Report a bug.
Click anywhere else, or press Esc, to close the panel. In the two windows above, Enter does nothing (so a slip cannot end your game),
Esc is the "never mind" button, and the keys 1 and 2 press the first and second choice. The shortcuts still work without opening the
panel: F11 full screen, + and - text size, Ctrl+N deck screen, H help, F8 report a bug.

WHERE YOUR DATA IS (Round 28)
------------------------------
Your own working copy (this git checkout) is "portable": settings, your decks, saves, the card cache and logs all
stay right here in the game folder, exactly as before - nothing below changes anything for you.
Any OTHER copy of the game (an installed/unpacked build for a friend, or anything without a portable.txt file next
to the .exe/forge_table.py and without a .git folder) is "installed": to survive an update replacing the program
files, settings.json, bug_report_config.json, my_decks/ and saves/ move to a folder that belongs to that Windows
user (%APPDATA%\Manticore), and the bigger, rebuildable stuff (the card/image cache, forge_decks/, bug_reports/,
logs) moves to %LOCALAPPDATA%\Manticore. The program's own files (sample_decks/, sounds/, licenses/, the Forge
engine) never move. The first time an installed copy starts, it COPIES (never moves) any settings/decks/saves it
finds sitting in the program folder into that per-user folder once, and says so on screen.
Cog (top right) > "My data folder" opens the folder your copy is actually using; the Help screen (H) also shows it
on a "Where your data is" line. To make any copy behave like your own working folder instead (keep it all in the
program folder), drop an empty file named portable.txt next to forge_table.py/the .exe.

BUG REPORTS AND IDEAS (F8, or the cog > Bug or idea)
----------------------------------------------------
For anyone playing a copy of the game (you and your friends). It works at any moment, even while the game is
asking you a question, and it takes a picture of the table BEFORE the form appears.
  1. Press F8. Type your name (remembered next time), what happened, and what you expected (optional).
     Tab moves between the boxes; Ctrl+V pastes.
  2. Press "Submit" (or "Save report file" if Discord is not set up on that copy). If sending fails, the zip is
     kept anyway and the game tells you where it is.
  3. The game packs ONE zip file into the bug_reports folder (see "WHERE YOUR DATA IS" above - inside the game
     folder for your own working copy, in the per-user data folder for an installed copy). It holds: a screenshot of the
     table, the board as the game saw it, the game log, every click since the game began and the shuffle seed, the decks in the
     game, the version stamp (the "code xxxxxxxx" fingerprint), your Windows / Python / Java versions, and the
     newest lines of forge_engine.log and crash_log.txt. Your Windows user name is taken out of file paths.
     Nothing else leaves the computer. The zip stays under about 5 MB (logs are trimmed and the picture shrunk).
  4. If Discord is set up the zip is posted to the channel by itself. If not, or if that fails (no internet, Discord
     refuses the file), the window says so and shows the file: press "Open folder" and drag it into Discord.
     A second automatic send has to wait a minute.
Setting up Discord (once, by Karl; friends need nothing except the config file below):
  1. In Discord make a private text channel (only you see it: reports contain deck lists and logs).
  2. Channel settings (the gear next to its name) > Integrations > Webhooks > New Webhook > Copy Webhook URL.
  3. In the game folder copy bug_report_config.example.json to bug_report_config.json and paste the address
     over the words in it (keep the quotes). Do not put that address anywhere else (not in chat, not in GitHub).
  4. Test it:   python forge_table.py --report-test     It says OK and a test message appears in the channel, or
     it says what is wrong (missing file, not a Discord address, Discord refused it).
  5. To let friends' copies send reports, put your bug_report_config.json in THEIR game folder too. It is left
     out of GitHub and out of the backup zips on purpose, because anyone who has the address can post to your
     channel (it cannot read anything). If it ever leaks or gets abused: delete the webhook in Discord (channel
     settings > Integrations) and make a new one.
  "owner_name" in that file is the name the messages use ("This sends the report to Karl's Discord channel", "Please send this file to Karl").
Reading a report: unzip it. report.txt first (who, what, which version), then screenshot.jpg, then game_log.txt.
Only https://discord.com (and discordapp.com) webhook addresses are accepted, so the file cannot be pointed elsewhere.
Ideas (Round FR1): the same window has a second tab, "Suggest a feature" - "What would you like?", "What would it help
you do?" (optional), "Where in the game?" (optional: Table, Deck screen, Cards & rules, AI, Online, Look & sound, Other)
and "Add a picture of the screen" (off unless ticked). F8 opens on it on the deck screen and the title screen, and on
the bug tab at the table; Ctrl+Tab switches tabs, and what you typed in either tab stays there.
  - An idea is ONE Discord message from "Manticore idea": "Feature idea from <name> | Manticore 0.28.x | Area: Table",
    then Idea: and Why:. No zip and no logs; the picture only when ticked.
  - A copy is always saved as bug_reports\idea_YYYYMMDD_HHMMSS.txt (and idea_..._screen.jpg with the picture). With no
    webhook the button says "Save idea file": send that file instead.
  - Ideas go to the same channel as bug reports. To give them their own channel, make a second webhook and add it to
    bug_report_config.json as "ideas_webhook": "https://discord.com/api/webhooks/..." (a missing or wrong one means
    the bug reports' channel). Bug reports and ideas share the one-a-minute limit.

BACKING UP THE CODE - TWO PLACES (do this first, and after every change)
-------------------------------------------------------------------------
One command, python backup.py, keeps the program safe in two places:
  LOCAL COPY: a dated zip of the whole project in a folder on your computer. Made first, needs nothing but Python.
  GITHUB: every version kept in a private online repository; any older version can be brought back.
Do the local copy right now (no Git or GitHub needed):
         cd C:\Users\Karl\Documents\commander_sim
         python backup.py --local-only
   The zips go in  C:\Users\Karl\Documents\commander_sim_backups\snapshots  (next to the project, never inside it).
   That protects against mistakes, but NOT against the disk dying. Better: point it at another drive, a USB stick or a
   OneDrive folder (zips sync to OneDrive safely). Once, with your own folder:
         python backup.py --local-dir D:\Backups\commander_sim
         python backup.py --local-dir C:\Users\Karl\OneDrive\commander_sim_backups
   It remembers the folder (backup_settings.json). If that drive is unplugged, the run says so and still does GitHub.
   A zip is made only when something changed. All zips from the last 3 days are kept, then one per day; zips older than
   90 days are removed. Forge's packed engine (forge_bundle, 35 MB) is not in each zip: one exact copy sits beside them
   in  ...\commander_sim_backups\forge_bundle. Left out of the zips: forge_runtime/, cache/ (both rebuilt), logs, and
   files that look like passwords. Your settings.json and my_decks/ ARE in the zips.
GitHub setup (one time):
  1. Make a free account at github.com (skip if you have one). Click New repository. Name it commander_sim.
     Choose PRIVATE. Do NOT tick "Add a README", ".gitignore" or "license" - it must start empty.
     Copy its address, which looks like  https://github.com/YOUR-NAME/commander_sim.git
     Keep it private: forge_bundle/ contains Forge itself (GPL-3.0), and a public repository would be
     publishing Forge; your decks are in there too.
  2. Install Git (once), in a terminal:
         winget install --id Git.Git -e
     then CLOSE the terminal and open a NEW one. Check:  git --version
  3. Connect the folder (one time; put YOUR address in):
         cd C:\Users\Karl\Documents\commander_sim
         python backup.py --setup https://github.com/YOUR-NAME/commander_sim.git
     It asks for your name and email once (GitHub > Settings > Emails has a private "noreply" address to use).
     A browser window opens to sign in to GitHub - sign in there. Never type a password or token into the
     terminal or into chat. When it says "Backed up", the first copy is online.
After that, after EVERY change (each time Claude gives you new files):
         python backup.py "what changed"
   or just double-click backup.bat in the folder (it uses an automatic note). This makes the local zip AND uploads to GitHub.
   If one of the two fails the other still happens, and the message after PROBLEM says which (LOCAL COPY or GITHUB).
   python backup.py --status tells you whether both places are up to date.
If the internet is down the change is still saved on this computer and the next run uploads it.
What GitHub leaves out on purpose (listed in .gitignore): forge_runtime/, cache/, forge_engine.log, crash_log.txt, forge_decks/,
settings.json, backup_settings.json, and passwords/keys. Your decks in my_decks/ ARE backed up there.
Automatic option (optional, do it after the manual way has worked once): back up every 30 minutes without thinking about it:
         schtasks /create /tn "CommanderSimBackup" /tr "cmd /c cd /d C:\Users\Karl\Documents\commander_sim && python backup.py --auto" /sc minute /mo 30
   Results go to backup.log in the folder. To turn it off:  schtasks /delete /tn "CommanderSimBackup" /f
GETTING AN OLD VERSION BACK
   From the local copy: open ...\commander_sim_backups\snapshots in File Explorer, right-click the zip you want > Extract All
   (the file names hold the date and time). Copy the file you need out of it. For a full restore into a fresh folder:
   extract the newest zip, copy the forge_bundle folder from ...\commander_sim_backups into the extracted commander_sim folder,
   then run  python setup_forge.py --check.
   From GitHub: in the terminal run  git log --oneline  (each line is a backup; the first word is its id), then
   git checkout ID -- forge_table.py   restores that one file as it was then (then run backup.py again to keep it).
   On a new computer: install Git, run  git clone https://github.com/YOUR-NAME/commander_sim.git  then  python setup_forge.py --check.
backup.py stops the GitHub part (and saves nothing there) if a file is over 100 MB (GitHub refuses those) or looks like a password/key/token.

PLAYING A FRIEND ONLINE (Round MP1: one friend, 1v1, over the internet)
  One of you HOSTS: the game runs on the host's PC. The other JOINS from their own copy. Each of you plays the deck chosen as
  "your deck" on the deck screen, and each sees only what their own player may see (the other hand and both libraries stay
  hidden). The connection is encrypted (TLS 1.3) and needs the game password.
  A. The normal way (a direct connection):
     1. Host: deck screen > choose your deck > Host online. Type your name. Leave "Try to open my router port automatically"
        ticked. Press Host.
     2. Read the line under "Hosting a game":
        - "Router port opened automatically": press Copy invite code and send the code to your friend (Discord, a text...).
          Send it ONLY to them: it contains the password.
        - "Open port 36800 on your router": do part B, then host again (or type your address and send the code anyway).
        - "Your internet provider blocks incoming connections (CGNAT)": a direct connection can't work from this internet
          connection. Use part C.
     3. Friend: deck screen > choose your deck > Join online > Paste (the invite code) > type your name > Join.
     4. The first time you host, Windows Firewall may ask whether Java may communicate on networks. Allow it, or your friend
        can't connect. (Installed copies: the Java is Manticore's own, in its program folder.)
     If the friend's game says "This isn't your friend's PC, or their game was reinstalled", ask for a new invite code.
  B. Opening the port by hand (only when the automatic way didn't work):
     In your router's settings page, forward TCP port 36800 to your PC. Every router's menus differ: look for "Port
     forwarding", "Virtual server" or "NAT". Your PC's own address is the "IPv4 Address" line of  ipconfig  in a terminal.
     Your internet address (for the Host screen's address box) is what a web search for "what is my IP" shows.
     Remove the forward when you stop playing online.
  C. Tailscale (only when the Host screen said CGNAT):
     1. Both of you install Tailscale (tailscale.com) and sign in (the free Personal plan is enough).
     2. The host shares their PC with the friend: Tailscale admin console > Machines > the PC > Share > send the link.
     3. Host as in part A. The Host screen fills in your Tailscale address (100.x.y.z) when Tailscale is running; otherwise
        type it into the address box (Tailscale's icon > your address). Send the invite code as usual.
  Three or four players (Round MP2c): in the Host dialog, Friends sets how many friends will join (1-3) and AI players how many
  Forge AIs fill the other seats (four players at most; the AIs play the decks chosen for AI 1 and AI 2 on the deck screen).
  Every friend uses the same invite code. The game starts when the last friend has joined; until then the Host screen counts
  them, and a friend who closes their game before the start frees their seat again. In a game of three or four, a player who
  leaves (or doesn't come back after a dropped connection) is out of the game and the others play on.
  Through a relay (Round MP2d, only when part A and part C are both impossible): a relay is a small program on a server that
  both of you can reach (relay\README.txt). Host online > tick "Connect through a relay" > type its address:port. The invite
  code then points your friends at the relay; the game stays encrypted end to end. No relay is set up anywhere yet.
  If the connection drops (Wi-Fi gone for a moment, a router restart), the friend's game reconnects by itself: both windows
  show an orange line with a countdown, and the game carries on where it was, with any question still waiting asked again.
  The host keeps the friend's seat for 60 seconds; after that the friend has lost the game. (Round MP2a.)
  When either of you closes the game, the other sees "Your friend left" / "The host left" and the game ends at once.
  Watching: someone with the invite code can press Watch (instead of Join) in Join online once the game has started; they see
  the board, both life totals and every card in play, but never a hand or a library, and they can't act. Up to four people
  can watch. The host's Ctrl+I copies the invite code again.
  Continuing a game another day (Round MP2b): the host's PC keeps every online game it hosts until it ends. If the host closes
  the game (or the PC restarts) in the middle, the next Host online shows "Saved game: turn 12, with Sam ..." - press
  Continue it, then Continue. The game is played back from the start to where it was (a progress line counts the actions),
  then your friends join with the new invite code; typing the same name as before gets each of them their own seat back. It
  needs the same version of Forge and the bridge as the game was played with (after a bridge update it can only be
  discarded). Only the host can continue a game; "Resume last game" on the deck screen is for games against the AIs.
  Not checked yet on real routers: the exact Windows Firewall prompt, whether Java needs "Public networks" ticked, and UPnP
  on Karl's router. Please report what you see (F8).

THE TURN BAR (top of the window)
  Left: the turn number and whose turn it is (gold tag = yours, orange = an opponent's).
  Then ten pills, one per step: Upkeep, Draw, Main 1, Begin combat, Attackers, Blockers,
  Damage, End combat, Main 2, End step. The lit pill is where the game is now; pills already
  passed this turn are dimmed.
  Under each pill are two dots (the word "stops:" labels them). A filled dot means the game
  stops and gives you priority there: blue = on YOUR turn, orange = on an OPPONENT's turn.
  Click a dot to switch it; clicking the pill itself toggles the blue one. Hover for a hint.

THE PILES AND THE COMMAND ZONE
  Each player's panel (left) shows Library (face-down pile with the card count), Graveyard and
  Exile (top card and count). Click a graveyard or exile pile to look inside. Your library can't
  be browsed - clicking it just explains - but when an effect lets you search it, a window opens.
  The gold COMMAND ZONE frame beside your hand (and beside each opponent's panel) holds the
  commander; click yours to cast it. "Tax +2" appears on the card after casts. If the commander
  is somewhere else the frame says where ("in play", "in graveyard", ...). When the commander is on the
  battlefield the frame disappears and its room goes to your hand (an opponent's frame too); it comes back
  when the commander returns to the command zone.

CRACKING A FETCHLAND (and every other "search your library")
  1. Click the fetchland on your battlefield.  2. "Do you want to pay 1 life?" - Yes (OK).
  3. The ability is now on the stack: press OK once so it resolves.
  4. A window titled "Select a card from your library" lists the legal cards, one tile per name
     with a count (x28). Click the one you want, then press "Take card" (or double-click it).
     "Find nothing" declines - and only that button does (round 27b): "Take card", Enter, Space and Esc do
     nothing until a card is picked, so a Space meant to pass priority can no longer throw the land away.

THE START OF THE GAME: PLAY OR DRAW, MULLIGANS, GEMSTONE CAVERNS
-----------------------------------------------------------------
1. Who goes first. When you win the coin toss (or lose the last game) a window asks "Play first"
   (key P) or "Draw first / go second" (key D). Enter does not answer it, so you can't skip it by
   accident. If your deck has Gemstone Caverns the window says so, because that decides it:
   Gemstone Caverns can only begin the game on the battlefield when you go SECOND (that is
   its real rule, and Forge enforces it).
2. Keep or mulligan. The bar says "You go first - keep this hand?" (or second). Your FIRST
   mulligan is FREE: you get a new seven and put nothing back (the Mulligan button says
   "Mulligan (free)" until you have used it). After that you draw seven each time and put one more card
   on the bottom of your library per extra mulligan (the bar says how many). A strip above the bar
   repeats this and tells you, for any opening-hand card such as Gemstone Caverns, whether you can
   use it given who goes first. This is a Commander house rule that Forge only applies in pods of 3+ players,
   so java_bridge/ carries a one-line patched copy of Forge's MulliganService (see FILES).
3. Opening-hand cards. After you keep, a window "Before turn 1: begin the game with this on the
   battlefield?" lists them, already switched on. "Begin with it" does it, "No, keep in hand" skips it.
   Gemstone Caverns then enters with its luck counter and Forge asks which card to exile from your hand.
   If you never see this window with Caverns in your opening hand, you were on the play.
   If you were on the draw and it still didn't come up, please send me forge_engine.log.

READING THE GAME (log, stack, prompts, the opponent's turn)
-----------------------------------------------------------
Prompt bar (above your hand): a big headline saying what the game wants right now ("Your turn -
main phase", "Pay {G} - Llanowar Elves", "Choose blockers ...") and a small hint line under it
("Play a land or cast a spell (glowing cards), or Pass priority."). Forge's OK button is
relabelled "Pass priority" when that is what it does.
Game log (right column): a dark bar for each turn ("Turn 6 - AI 1"), a small label for each step
(Main 1, Attackers, Blockers, Damage ...), and one plain-English line per event: "You cast Sol Ring",
"AI 1 attacks you with Wall of Blossoms", "Your life: 40 to 37 (-3)". You are in blue, opponents in
orange, card names in bold; hover a card name in the log to see the card. Debug tails and the
"picked ..." lines are hidden. Mana-tapping noise is hidden, except mana that comes from a card leaving your hand
(Elvish Spirit Guide, Simian Spirit Guide): "Elvish Spirit Guide exiled from hand for {G}" stays, because nothing on the table shows it.
Chrome Mox (new): the card on the table wears a small gold-edged badge with the colours it can make (the colours of the card exiled
with it), or "no mana" while nothing is imprinted. Hover it and a strip under the big picture says "Imprinted: <card> (<colour>)".
Any other card with something imprinted on it (Isochron Scepter) gets the strip too.
Keywords a card has right now (round 13): a permanent that has GAINED a keyword (Flying from Jump, Haste from an Equipment, ...) wears a small violet
chip with the keyword, and the strip under the big picture lists it when you hover the card. Keywords printed on the card are not repeated. The bridge
sends Forge's current keyword list for every permanent; the table leaves out the ones the card's own text already mentions (so a card that grants
itself a keyword in a roundabout way may show no chip, but a chip is never wrong the other way round).
"Why can't I pay?" (round 13): while Forge asks you to pay a mana cost, the second line of the prompt bar explains when what you have untapped cannot
pay it: "Can't pay {G}: nothing you have untapped makes green mana. Untapped: Otawara, Soaring City (blue), Chrome Mox (only blue, from its imprint)."
(mana_hint.py reads the mana abilities out of each permanent's text; it knows Chrome Mox's imprint, summoning-sick creatures, hybrid costs and your
floating mana.) It says nothing, and the usual hint stays, whenever the payment can work, whenever a mana doubler (Kinnan, Mana Reflection) is in play,
and whenever it meets a cost symbol or an ability it cannot read (X, Phyrexian mana, snow ...). It is a helper's guess from the card text, not Forge's
verdict: if it ever says you cannot pay and the payment goes through, tell me.
Stack panel: each item says who it is from and what it is ("AI 1 - spell", "You - trigger"), the name,
"Targets: ...", and what it does; the top item (the one that resolves first) is listed first.
The FULL text is shown: a trigger's or ability's whole description, and for a spell its rules text (up to 10 lines, then
"..."). Each item is as tall as its text needs. When the items do not all fit, the panel takes at most 44% of the right
column and scrolls with the mouse wheel (the header then says "wheel: more").
The panel border flashes when a card you can see in the log/feed is on the stack.
Hover any card in the stack panel (or a card name in the log) and the card appears in the preview at the
top right, like every other card.
Floating mana: when you have mana in your pool (you tapped a land and have not spent it yet) a green
"Mana pool" pill with big mana symbols and counts ("G x2  C x3") appears just above the prompt bar. The
same symbols show next to the "Hand N" chip on each player's panel, so you can see the opponent's too.
It disappears when the pool is empty (mana empties at the end of every step).
While Forge is asking you to PAY for something, the pill turns bold yellow and says "Click to pay": click a
mana symbol in it to spend one of that mana on the payment (e.g. the C from Basalt Monolith / Kinnan to pay
for "{3}: Untap"). The Auto button spends floating mana first, then taps lands. Clicking the pill when nothing
is being paid only shows a hint.
Yellow means "you can click this": every card you can play, choose or activate has a bold yellow frame (red =
attacking, blue = blocking). In a search window the card under the mouse is shown big in the preview panel
at the top right, like everywhere else.
Choosing a target: the bar says "Choose the target for <card>" and what may be chosen. The card whose ability
you are using wears an orange frame and a SOURCE tag (Forge lets it target itself, and it glows like the real
targets). Clicking it asks "Target <card> itself?" (Enter means No). After you pick, Forge asks for the mana;
Cancel still takes the whole thing back.
All pop-up windows (coin toss, choices, yes/no, messages) grow with the text size, and long choices
wrap onto several lines instead of being cut off with "...". Card numbers such as "(28)" are hidden.
Copies: a token that is a copy of a real card (a second Consecrated Sphinx made by a copy effect) is drawn with
that card's picture. Real tokens (Treasure, Goblin ...) still get a drawn card.
Opponent action feed: while an opponent acts, their last few plays appear as toasts over the
battlefield for about seven seconds ("AI 1 cast Chrome Mox", "AI 1 attacks you with ..."), and the
card involved glows on the table while it's fresh.

STATUS BADGES (monarch, initiative, the Ring, emblems) AND THE LIFE FLASH
-----------------------------------------------------------------------
Badges: when a player is the monarch, has the initiative, has been tempted by the Ring, or has an emblem or another
effect that Forge keeps in the command zone, a small chip appears on their panel under the "Hand" chip: a gold crown
+ "Monarch", "Initiative", "The Ring", the emblem's name, or "Effects 3" when there are several. Hover a chip for what it
means; clicking one while Forge asks you to choose a player still chooses that player. On short panels (a pod) the chips
shrink to "Mon" / "Ini" / "Ring" and sit at the right of the Hand chip. They come and go by themselves as the game changes
who holds them. (Before this, the monarch existed in the game but nothing on screen said who it was.)
Life flash: when someone's life changes, the number turns red (loss) or green (gain) for two seconds and a small "-3" or
"+2" rises beside it; several hits in a row add up. Everyone's panel does this, so an AI's damage or lifegain is visible
at the moment it happens.
Into your hand: Forge writes nothing in its log when you draw, and nothing on screen changes except a number, so a
card arriving in your hand (a draw step, The One Ring, a tutor ...) now adds a cyan "Into your hand: <card>" line to the log
and, outside your draw step, a short toast. The opening hand and mulligans are not announced.
The One Ring: tapping it does draw (checked live with the real engine: tap, press OK once so the ability resolves, hand 6 -> 7 and
one burden counter; by the card's own text you put the counter on first, then draw a card per counter, so the first tap draws 1).
Two things made it look as if nothing happened: the draw is only visible as the Hand number changing, and Forge's stack text for
the ability says "draws zero cards" (it works that number out BEFORE the counter is added; it is not what really happens). The
"Into your hand" line above now shows the draw. Remember the ability sits on the stack until you press OK.
UNDO: Forge's undo is narrow. Tested live: it takes back a land you tapped for mana while that mana is still unspent and it is
still the same phase. It does NOT take back a land drop, a spell you have cast (even with it still on the stack), or anything
that already resolved - Forge has no rewind. Forge also says nothing when it can't undo, so the table now shows "Nothing to
undo. Forge only lets you take back a mana tap that is still unspent." about a second after you press Undo if nothing changed.
The Controls window (H) now fits its whole list, and scrolls with the mouse wheel when the window is too small (before, its last lines could run under the Close button).
The "Starting the Forge rules engine" screen was redone: the lines are spaced by their real height (the big title used to sit on top of the small text under it), the dots no longer make the title shake, a bar slides to and fro while it works, and long messages wrap instead of running off the window.
Verified in the sandbox with the real engine: Court of Cunning made "The Monarch" appear in the command zone and its badge
on screen; a Seasoned Dungeoneer made "The Initiative" appear; Restart (now New game > Same decks) ended the old engine and started a new game; Concede
ended a 2-player game with "Defeat" and, in a 3-player game, left the AIs playing (now with the "You are out" window).
Rings (round 9b): a permanent that just entered the battlefield, yours or theirs, gets a pale blue-white ring for about 2.6 seconds
(1.5 for a land, and quieter), with a quick white flash and an expanding ring on top. While a trigger or an activated ability is on
the stack, the permanent it comes from gets a purple (trigger) or green (ability) ring that pulses, plus a small number that is
the same as the number on its row in the stack panel (1 resolves first; only the first 8 stack items mark their source). Spells
get a number on their row but no ring, because the spell itself is on the stack. Cog > Animations: off keeps the rings but stops
the flash, the expanding ring and the pulsing. The orange glow on a card the opponent just cast now marks only the copy that
arrived (before, every permanent with the same name glowed). How it works: Forge never says "this card entered", so the table
compares the card ids on the battlefield from one snapshot to the next; the first snapshot of a game is history, not news.
Tested in the sandbox with the real card art (preview GIF made from the real table code); NOT yet seen in a real game. Unknown:
what a blinked/flickered card does (it may keep its id and so get no ring), and whether many rings at once (a token burst) is too busy.
SKIPPING AHEAD (round 10; this uses Forge's own "yield" features, the ones its desktop window has)
  Skip... (the button left of Undo, or the S key) opens a window with:
    Let the stack resolve ........ Forge passes priority for you until the stack is empty. An opponent casting something new stops it.
    Skip to my next turn ......... passes everything (the opponents' turns, your own combat and end step) until your next turn's upkeep.
                                   It stops early if an opponent casts a spell or attacks you. The turn-bar stops you set are respected again
                                   once you get there. (This is the answer to "three AIs take nine stops before my turn comes back".)
    Auto-pass when I can't do anything: ON / OFF .... Forge passes by itself whenever it finds nothing you could do; an opponent's spell or an attack
                                   on you still stops it. Off by default, remembered in settings.json, and the button then reads "Skip (auto)".
                                   Forge decides what counts as "can do", so if it ever passes when you wanted to act, turn this off and tell me.
    Always pass on <card>'s trigger / ability ... when that ability is on top of the stack it resolves without asking you every time (per ability,
                                   for the rest of the game). Use it for the things you never respond to (City of Brass damage, a mana rock's trigger).
    Always use / Never use <card>'s trigger ... for a "you may" trigger of yours: answers the "use it?" question for you. Ask me again undoes it.
    Stop always passing: ... / Forget all my 'always' choices ... takes those rules back (an "always pass" ability never shows on the stack
                                   again, so this is the only place to undo it).
  While a skip is running the bar says "Yielding until ..." and Esc (or the Cancel button) ends it. The Skip window then offers "Stop skipping" too.
  Verified in the sandbox with the real engine: skip to my next turn (two opponents' turns passed in about a second, stopped when an AI cast a
  Llanowar Elves), always pass on a Mishra's Bauble ability (the second one resolved without asking; "stop" removed the rule), auto-pass
  (passes through combat and stops where you still have a play). NOT verified: on your screen; what auto-pass does with your real deck (Forge's
  "can I do anything" check is its own code); (the attack interrupt WAS tested in round 13: an AI attacked and the skip ended.) Auto-pass rarely fires with a competitive deck: free spells and instants count as things you
  could do, so Forge does not pass. It is still worth leaving on if you like.

Card wording under a trigger or ability on the stack (round 10): Forge's stack text can be wrong about numbers that depend on the ability's own first step
(The One Ring's tap says "draws zero cards" because it counts the burden counters before the new one is added). When the card's own rules text has a
paragraph for that kind of ability and Forge's stack text says something noticeably different, the stack row now shows both: Forge's line, then
"Card says: {T}: Put a burden counter ..., then draw a card for each burden counter ...". Nothing extra is shown when they agree, and nothing when
the choice between two abilities of the same kind is not clear.

Arrows and gliding cards (round 10): a dashed arrow runs from every trigger, ability or spell on the stack (first 8) to what it targets: from the
source permanent for a trigger/ability, from the stack row for a spell; to the permanent, or to the player's panel. The arrowhead carries the same
number as the stack row. A permanent that arrives now glides in from where it came from (its place in your hand, its row on the stack, the
controller's panel; not tokens) in about 0.45 s, and its ring flash starts when it lands. Cog > Animations off: the arrows stand still and cards
appear in place (rings stay). Seen only in a rendered preview (GIF), not in a real game; unknown: how it looks with many arrows at once.

Round 9 (from playing round 8): the stack text, the "Into your hand" line and the Undo note were tested in the sandbox
(live engine for the Ring and for Undo) but NOT yet seen on your real screen. Emblems and the "Effects N" chip are only checked
with made-up data (the names Forge gives them were read from its source, not seen live).

REPLAYING A REPORT (round 13; for me, not for you)
--------------------------------------------------
Forge's shuffles are decided by a seed, and the game gives every game one (the deck screen's --seed still works). A bug report zip holds the seed,
the decks and every click since the game began (commands.json; "complete": true when nothing was cut off), so the same game can be played again in the
sandbox with:   python replay.py bugreport_....zip   (options: --upto N sends only the first N clicks, --log prints the game log, --json FILE saves the
board it ended on). It ends by comparing the board it reached with the board in the report and says REPRODUCED or lists the differences. Verified:
the same seed and clicks gave an identical AI game (165 log lines over 14 turns), and a game of 19 turns and 94 clicks with real casting replayed
to the same board. NOT verified: reports from your PC, which is a different machine (the shuffle should be the same; Forge is the same jar).
A report from a game started before this update has no seed, and the replay says so instead of guessing.

CHECKING A DECK CARD BY CARD (round 14)
---------------------------------------
    python card_check.py sample_decks\stompy_goreclaw.txt --out card_check_report.txt
plays every card of a deck through the real Forge engine, one at a time, the way the table does it: Forge's own developer "Setup Game State"
puts a board in place (twelve basic lands, a small library for each player, an opponent with a creature, an artifact, an enchantment and a nonbasic
land), a scripted player casts the card (plays it, for a land), and answers every question by taking the first legal answer; permanents are then
put on the battlefield and each ability is clicked once; counterspells get an opposing Lightning Bolt to answer. The report lists, per card, OK / WARN /
FAIL / SKIP and what it did ("asked which ability", "floating mana afterwards", "the bridge answered for you"). A whole deck takes 10 to 30 minutes.
Other options: --card "Force of Will" (only these, repeatable), --json FILE, --verbose, --seed N.
It tests that the whole path works (table -> bridge -> Forge) and that nothing stalls or gets answered behind your back. It does NOT test that a card
does what its text says: the scripted player takes the first legal option, so a card whose result depends on a clever choice is only shown to
run. The game itself never starts the bridge in developer mode (Forge's setup command is refused without it); only card_check.py, the tests and a
replay of a report that used it do.

SAVING A REPLAYED GAME AS A TEST (round 14)
-------------------------------------------
    python replay.py bugreport_....zip --save-as arc_lightning_divide --note "what this game guards against"
replays the report and, if it played out completely, keeps it in tests/reports/arc_lightning_divide/ (the zip and expected.json: the board it ended on and
the last 40 log lines). From then on the normal test run (tests/test_report_regressions.py) plays every saved game again and fails, with the
differences, when a change makes one end differently. Options: --as-recorded keeps what the REPORT saw instead of what the replay produced (refused unless the
two match). A report holding only the newest clicks, or a replay that diverged, is refused. Check by hand:  python replay.py --check-saved [NAME].
The saved games are tracked by git, so the backup carries them. None is saved yet: the first useful one is a report of a game where something went wrong
and was then fixed.

THE DAMAGE AND DIVIDE WINDOW (round 14)
---------------------------------------
Two kinds of question used to be answered for you with Forge's automatic split; now a window asks.
  - Combat damage: when your attacker is blocked by several creatures (or tramples over one), one row per blocker and, when the attacker may hit past
    them, one for the defending player or planeswalker. Blockers must be given lethal damage in order before the next row gets any (Forge's rule), and the player row
    needs every blocker at lethal first; an attacker that divides its damage as it likes has no such limit. The window starts on the usual split
    (lethal to each, the rest to the last row) and says in words what to fix when a split is illegal; OK stays greyed out until it is legal.
  - "Divide N among these" (Arc Lightning, a mana of any combination, counters): one row per target or colour, with a maximum and, when the card says so,
    at least 1 each.
Buttons: - and + per row, Lethal (Max for a divide), Rest, Auto, Reset, OK, and "Decide later" when Forge allows skipping. Keys: Up / Down pick a row,
Left / Right change it, L = lethal or max, R or Space = the rest, A = auto, 0 = clear, Enter = OK, Esc = decide later; the mouse wheel scrolls a long list.
Whatever you send is checked again inside the bridge; an illegal answer is refused and the automatic split is used instead (engine log: "bridge: assign ... reply refused").
This also fixes a bug: with trample, the automatic split used to give the excess to the LAST BLOCKER instead of the player.
Verified live in the sandbox: a trampler blocked by two Grizzly Bears (6 damage: 2 + 2 + 2 to the player), a split of your own choosing, an illegal split refused, and
Arc Lightning's 3 divided over two targets. NOT seen on your screen. The window's picture was checked at several window sizes and text scales.

TESTS ON GITHUB (round 14; optional)
------------------------------------
ci/tests.yml is a GitHub Actions recipe that runs the offline tests (no Forge needed) on Windows with Python 3.14 after every upload and shows a green tick or a red cross next to
the commit on github.com. It lives in ci/ on purpose: GitHub refuses an upload that changes .github/workflows/ unless your sign-in has the "workflow" permission, and a
refused upload would stop the 30-minute backups. So nothing happens until you run, once:
    python backup.py --enable-tests-on-github
It does a normal backup first, then copies the recipe to .github/workflows/tests.yml and uploads it as its own commit. If GitHub refuses (missing "workflow" permission), it takes the
file out again so the normal backups keep working, and says what to do: gh auth refresh -s workflow, or delete the stored github.com sign-in in Windows Credential Manager and sign in again.
Do NOT create the file on the github.com website: the online copy would then have a change your PC lacks and the backups would stop.
Emulated on Linux with a fake GitHub refusal; the real Windows run has NOT happened, and the first run may fail on some Windows-only difference (then it shows a red cross, nothing else breaks).

WHAT IS AND ISN'T VERIFIED
--------------------------
Verified in the development sandbox (Linux, Java 21, real Forge engine): full games driven
through this table's own code, including Gemstone Caverns, casting, mana payment, choices,
discarding to hand size, attackers/blockers prompts, the stack, and phase stops. 910 automated
tests (20 of them start real Forge games; they skip themselves if Java or forge_runtime/ is missing).
This update (free mulligan, Caverns, log/stack/prompt readability) was also run live: mulligan hand
sizes 7,7,6,5 with 0,1,2 cards put back; Gemstone Caverns offered after a mulligan when going second;
the play/draw window, mulligan, opening-hand window and the exile step driven through the real GUI code;
real Forge log lines fed through the new log formatter; screenshots at 1360x840, 1024x640, 1920x1080
and your 4096x2019 at 175% text.

The deck screen (this update) was run live in the sandbox with the real engine: start screen -> Start with the
bundled deck and 1 opponent -> a real game arrived; Ctrl+N -> import a deck through the clipboard path (a stand-in
clipboard) -> pick it -> 2 opponents -> "End it and start" -> a second real game arrived with the imported commander
and 3 players (the coin-toss question in a pod is answered by clicking your portrait, and that worked); the
command-line start with a deck still works; the check for cards Forge does not know was compared against Forge's
real card scripts (the sample deck: all 100 known). Screenshots at 4096x2019 @175%, 1920x1080 and 1024x640.

Confirmed on your PC: it starts and plays (you cracked a fetchland). That test found a bug - the
library search had no list to pick from - which this update fixes; the new search window and the
new turn bar / piles / command zone have only been checked in the sandbox so far. Your
forge_engine.log also showed that three two-faced cards of the Kinnan list (Barkchannel Pathway,
Sink into Stupor, Invasion of Ikoria) were missing from the game because Forge knows them by their
front name only; that is fixed too (the deck file now uses the front name).

NOT verified yet: backup.py on your Windows PC (the local zips and the GitHub part are tested here on Linux, GitHub only against a stand-in
repository: the browser sign-in window, Git Credential Manager, an unplugged USB drive, Windows path/locking details and the scheduled
task are untested; if a step fails, copy the PROBLEM text to Claude);
on your PC, reading the CLIPBOARD (Ctrl+V / Paste button; it tries pygame, then Windows' own
Tkinter clipboard) and dragging a .txt file onto the window - both are only tested with stand-ins; if Paste says it
can't read the clipboard, tell me (or save the list as a .txt into my_decks/); how the deck screen looks on your real
screen; how it looks on your real screen with real card art (the sandbox used stand-in art),
whether you were on the play in the game where Gemstone Caverns did not come up, pods of 3 opponents
(they may look cramped; the play/draw question in a pod is still Forge's plain "Who would you like to
start?" prompt), very long ability text in the stack panel (clipped), the Breeding Pool warning in
forge_engine.log (cause unknown), and every kind of Forge
question (targeting and a few rarer prompts use Forge's default answer
instead of asking you; combat damage and "divide" prompts ask you since round 14), and how well Forge's AI handles combo decks like Kinnan. Please report
anything that looks wrong. If the game does not start, look at forge_engine.log in this folder.

WHEN SOMETHING GOES WRONG - crash_log.txt
-----------------------------------------
The program writes a report by itself, into crash_log.txt in this folder, when:
  ERROR        one frame of the game hit a mistake. The game carries on (a red note says so); the same mistake is written once.
  CRASH        the program had to close. A message window names the file, and the report has the full error.
  THREAD       a helper (card art, Forge's reader) stopped. The game carries on but that part is dead.
  FROZEN       the window did not respond for 30 seconds: the report lists what each part was doing, and a second entry says
               when it came back. Holding the window's title bar also freezes it; that is not a bug.
  HARD CRASH   the whole program died (a fault in a library). Python leaves crash_native.txt, and the NEXT start copies it into
               crash_log.txt.
Every entry names the exact copy of the program (version, backup commit, and a 'code' fingerprint of the .py files), your Python /
Windows / pygame versions, what the table was doing, and the last lines of forge_engine.log. To report a problem, send me the
newest entry (the bottom of the file) as copied text. It contains folder names such as C:\Users\Karl\..., nothing else personal.
The files are not backed up to GitHub or zipped; crash_log.txt is moved to crash_log.old.txt when it passes 1 MB.
  python forge_table.py --version    prints which copy this is (the window title shows it too).
Tested on Linux with real processes (an uncaught error, a segmentation fault and the next start, a frozen loop). NOT verified
on Windows: the message window that appears on a CRASH, and the hard-crash file.

LICENCE AND CREDITS (round 27)
------------------------------
Manticore is free software, licensed under the GNU General Public License v3 or later (the same
licence Forge itself uses). The full licence text is in LICENSE at the top of the project, and
Cog > Licenses and credits (or Shift+F1), in the program itself, shows: the licence, where to get the
source, what Forge and every library it uses are licensed under, and the required Wizards of the
Coast / Scryfall / card-art / deck-import credits. THIRD_PARTY_NOTICES.txt is the same information as
a plain text file, for anyone who would rather read that than open the program.
Giving this program to someone else, or publishing it, means the GPL applies to the whole combined
program: they are owed the complete source code under the same licence (that is what
tools/export_public.py prepares, and what --version's second line and the licences window both point
at). The single source of truth for all of this is licenses/NOTICES.json; tools/build_notices.py turns
it into THIRD_PARTY_NOTICES.txt and checks it is complete (--check, --release).

FORGE AND THE LICENSE
---------------------
forge_bundle/ contains Forge itself (forge.jar and its card scripts), built from
github.com/Card-Forge/forge (commit fb4d809, version 2.0.16-SNAPSHOT, 2 Oct 2026). Forge is free software
under the GNU GPL v3 (forge_bundle/FORGE_LICENSE.txt, also LICENSE at the project root).
The small forge_bridge.jar in java_bridge/ is this project's own code that connects Forge to
Python (source in java_bridge/src). One file in it, MulliganService.java, is Forge's own source with a
single changed line (free first mulligan); it carries the GPL notice and is a derivative of Forge.

PUBLISHING THE SOURCE AND RELEASES (round PUB1)
-----------------------------------------------
DONE 3 Oct 2026: the public repository is github.com/Eliminate7532/Manticore (one commit, "Manticore 0.28.38", made from
Documents\manticore_public - later exports go over that folder, below). Patch 35 put it in update_config.json and in
licenses/NOTICES.json "source_url". Still to do with the first release: the installer, the gh release, Forge's source archive.
In PowerShell, write $env:USERPROFILE for %USERPROFILE%, and run the &&-joined commands below one at a time.
Friends' installed copies look for updates in a PUBLIC GitHub repository's Releases (update_config.json), and the GPL owes
them the source of what they run. Both live in one public repository, separate from this private one: GitHub makes a whole
repository public or private, never one branch, and making this one public would publish its entire history (round notes,
bug reports, every old commit). The public one holds only what tools/export_public.py copies, with no history.
What the export leaves out: my_decks, saves, settings, bug reports, logs, caches, soak runs, Forge's unpacked engine, the
AvQest font, docs/, CLAUDE.md, EVENING_CHECKLIST.txt, the backup scripts, and any other .txt at the top. It refuses, naming
the file, if anything it would copy holds a Discord webhook address or this PC's home folder path.
  Once, on github.com: New repository, Public, EMPTY (no README, licence or .gitignore), e.g. <owner>/manticore.
  Once, here: update_config.json  "repo": "<owner>/manticore"  (it ships inside the installer; commit it).
  For each version you send out (on main, tests passed):
    python tools\export_public.py --check                          "N files would be exported." and no "Would refuse"
    python tools\export_public.py %USERPROFILE%\Documents\manticore_export_<version>      (a new, empty folder)
  The first time, turn that folder into the public repository:
    cd %USERPROFILE%\Documents\manticore_export_<version>
    git init -b main  &&  git add -A  &&  git commit -m "Manticore <version>"
    git remote add origin https://github.com/<owner>/manticore.git  &&  git push -u origin main
  Later times, copy the new export over that folder, keeping its .git (robocopy's exit codes 1-7 mean success):
    robocopy %USERPROFILE%\Documents\manticore_export_<version> <the public folder> /MIR /XD .git
    then, in the public folder: git add -A  &&  git commit -m "Manticore <version>"  &&  git push
  Then build the installer (INSTALLER, below) and publish the release from the public repository:
    python tools\make_update_feed.py installer_out\Manticore-<version>-setup.exe --notes "What's new."
    and run the gh line it prints (NOT a pre-release, NOT a draft). It tags the public repository's newest commit, so each
    release's tag is exactly that version's source.
  Forge's own source: with the first release, also attach Forge's source archive for commit fb4d809 (Licenses and credits
  names it); keep your own copy rather than only linking to Forge's site.
  licenses/NOTICES.json "source_url" names the public repository (patch 35), so the program's Licenses and credits window
  points at it.

TESTS
-----
    python -m unittest discover -s tests -t .
Keep the "-t .": without it the tests start before tests\__init__.py has pointed the program at a temporary data folder, so
some of them write into your real saves\ and crash log (round FB1b found test online games in saves\online that way).
910 tests (20 need Java and forge_runtime/). Real-engine tests only:  python -m unittest tests.test_forge_live -v
Set GUI_SHOTS=some_folder first to save screenshots from the older table's GUI tests.

Quick set (round 27a), before handing a session back - skips the real-engine tests, under 3 minutes,
and fails loudly if it touched any of the project's own real log/save files:
    python tools\quick_tests.py

IF THE PROGRAM CLOSED UNEXPECTEDLY (round 28d)
----------------------------------------------
When Manticore didn't close properly last time (a crash, ended in Task Manager, the PC switched off), the deck
screen says so once, with three buttons:
    Send report   sends a report about it to Karl's Discord (or saves it in bug_reports\ when Discord isn't set up)
    Look first    makes the report and opens its folder, so you can see what's in it before sending
    Not now       forgets it
The report has the Forge log of the run that stopped (kept as forge_engine.crashed.log), the crash log and the saved
game's last moves. Nothing is ever sent without the button.

A saved game made by a different Forge build or bridge can't be played back correctly: the deck screen then says
"Saved game ... is from an older version and can't be resumed - Discard" (it is kept in saves\finished\).

--version and every bug report now also say which Forge build is running (e.g. "Forge 2.0.16-SNAPSHOT | commit fb4d809").
A deck card this Forge doesn't know is called "not in this version of Forge yet" on the deck screen, and named again as
the game starts - Forge leaves such cards out of the game.

On a PC with less than 8 GB of memory Forge gets 2 GB instead of 3 and the deck screen says 4-player games may be slow.

NIGHTLY TEST RUN (round 28c)
----------------------------
One command for a whole night: the canary, then the card-check sweep (every card of one or two decks put into play in
turn, with the bridge's self-checks on), then soak games for the rest of the night - half of them starting from a
mid-game board (turn 4-6, 5-6 lands and a creature or two already in play, from each player's own deck).

    python tools\nightly.py --hours 8          start now, stop 8 hours later
    python tools\nightly.py --until 07:00      stop at 07:00

The sweep takes the 6 alpha decks in turn (Lathril, Adeline, Teysa, Light-Paws, Veyran, Goreclaw; patch 38 put Goreclaw in
the alpha instead of the Kinnan sample, which is now only a test deck in tests\fixtures\decks\), then the other samples
(--decks mine: then your own decks, read-only). It spends up to --card-hours (default 2) on it, about one deck; an
unfinished deck carries on from the same card next night. Progress: soak_runs\nightly_state.json.

Each night gets soak_runs\night_<date>_<time>\ (card_check_<deck>.txt reports, the soak games under soak\), and the
latest summary is soak_runs\nightly_summary.txt. Its first line is NIGHT: VALID or INVALID:
    VALID   the canary was caught, the soak games are VALID, and no card check broke a bridge self-check
Card-check FAILs that are not bridge self-checks are listed to look at, but don't count against the night: the checker
can't set every card up (a card that needs a creature to sacrifice, a graveyard to exile from, ...).
Round 28d: each soak summary also has a MEMORY section (the most memory a game needed, per number of players) and a
"Forge:" line; a game whose Forge thread died right after an AI think was cut off at its time limit is reported as
ai_timeout_race (a known Forge problem on Java 21, see claude/SOAK_NIGHT4_2026-09-29.md).

Disk space (round 28d fix 1): each game's recording is kept gzipped, and every night starts by removing the per-game
folders of all but the newest 3 runs (summaries and bug-report zips stay). To catch up by hand:
    python tools\soak_prune.py --dry-run        say what would go
    python tools\soak_prune.py                  keep the newest 3 runs' games, remove the rest

To run it every night on its own (like the backup), once, in a Command Prompt - change 00:30 / 07:00 to suit:
    schtasks /create /tn "CommanderSimNightly" /tr "cmd /c cd /d \"%USERPROFILE%\Documents\commander_sim\" && python tools\nightly.py --until 07:00" /sc daily /st 00:30
The PC has to be awake then (Settings > System > Power: sleep "Never", or at least not before 07:00). To stop it:
    schtasks /delete /tn "CommanderSimNightly" /f

SOAK TEST (round 28b)
----------------------
An overnight run of whole games through the real bridge, with a bot in your seat, to shake out bugs
that only show up in play - the checks tools\quick_tests.py and the unit tests can't reach on their
own. It won't play well; it exists to try lots of kinds of question, not to win.

    python tools\soak.py --hours 8

--hours alone plays as many games as fit (round 28bb; before, it stopped after 1 game unless --games was
also given). --games N alone plays N games; both together stop at whichever comes first.
Common options: --players 2|3|4 (default: rotates), --boards 0.5 (round 28c: this share of games starts from a
mid-game board), --decks sample|mine (mine also reads
my_decks\, read-only), --seed N, --out DIR (default: soak_runs\ next to the program; an installed copy uses soak\ in its local data folder). Stops
cleanly on Ctrl+C - it finishes closing the game in progress and still writes what happened so far.

Afterwards (round 28ba) each run has its own folder, soak_runs\run_<date>_<time>\, and a copy of the
latest summary sits at soak_runs\soak_summary.txt. Read its FIRST LINE:
    SOAK RUN: VALID      the bot really played (lands, spells, several kinds of question) and the canary
                         game's deliberate fault was caught - so "0 problems" means something
    SOAK RUN: INVALID    the reasons are listed underneath; its "0 problems" does NOT mean the game is fine
Then PROBLEMS (grouped: the same problem in 12 games is one line; NEW marks what the last run didn't have),
WARNINGS, and one line per game with what the bot did. Each run folder also has:
    soak_shapes.json     which kinds of question came up, and how often
    soak_<date>_<game>_<rule>.zip   one bug-report zip per game with a failing finding
Warnings are worth reading but don't make a game fail: ai_think_timeout (Forge's AI ran out of thinking time and
played on), forge_internal_error (an error inside Forge's own code, in a game that still finished), dropped_clicks.
ai_stall (a failure) means an AI thought for over 5 minutes while nobody was asked anything.
The first game of every run is the "canary": it switches on a deliberate bridge fault that must be caught.
--no-canary skips it. Default turn cap: 60 (all players' turns together).

Then, to see which kinds of question this run (or several) never exercised at all:
    python tools\coverage_report.py soak_runs\*\soak_shapes.json

Send Claude soak_summary.txt and any .zip files it made - never posted anywhere on its own.
soak_runs\ is in .gitignore and left out of your backups (it's your own test output, and it can
get large).

FOR DEVELOPERS (round 27a)
--------------------------
Read docs\WORKING_AGREEMENT.md first - it has the full rules (branching, never pushing, the reserved
names, one session at a time). Short version:
- One git branch per round: round-<id> (e.g. round-27a). Merge into main only after a clean Windows
  test run; then an annotated tag, r<id> (git tag -a r27a -m "Round 27a").
- version.py's VERSION follows the round (0.27.1 = round 27a); --version also shows the tag when HEAD
  is exactly on one, and always shows the git commit and a code fingerprint of the .py files.
- Never run backup.py yourself, and never git push - the scheduled task (:06 and :36 past the hour)
  does both. A stray file named con, prn, aux, nul, com1-com9 or lpt1-lpt9 (with or without an
  extension) will break the backup; quick_tests.py checks for these.
- A restore drill, run by hand when you want to be sure the backups actually work:
    python tools\restore_drill.py
  Clones the GitHub remote and unzips the newest local snapshot into a temp folder, then compares both
  against the project folder (every tracked file's hash, and the --version code). Uses your own git
  sign-in (Windows' credential manager) - never asks for or stores a password.
- If backups stop working, the deck screen shows an orange line saying since when and why (reads
  backup_status.json, written by every backup.py run); an "Open backup.log" button is right there.

FILES - new table and engine
----------------------------
- forge_table.py       - the Forge-driven table: drawing, mouse/keyboard, launcher
- forge_dialogs.py     - choice / confirm / number / help / zone-list / play-or-draw / opening-hand / yes-no / game-over windows
- forge_menu.py        - the deck screen and the paste-a-deck window
- backup.py / backup.bat - one-command backup: local zips + GitHub (see BACKING UP THE CODE); .gitignore and .gitattributes say what GitHub leaves out / keeps exact
- forge_settings.py    - the settings cog's pop-up and the bug report form (windows that sit above everything)
- forge_fx.py          - the rings, gliding cards and target arrows: which permanents just entered, which permanent a trigger/ability on the stack comes from, what each stack item targets (logic + drawing; tested without a window)
- forge_status.py      - turns the effect cards in a command zone into the status badges (monarch, initiative, Ring, emblems); no pygame
- reporting.py         - builds the bug report zip and posts it to Discord (no pygame; tested on its own)
- replay.py            - plays a bug report's game again in the sandbox: python replay.py bugreport_....zip (same seed, same clicks; see REPLAYING A REPORT)
- card_check.py        - plays every card of a deck through the real engine and reports which go wrong (see CHECKING A DECK CARD BY CARD)
- allocation.py        - the arithmetic of the damage / divide window (no pygame; the same rules as java_bridge/src/forge/bridge/Assign.java)
- ci/tests.yml         - the recipe GitHub runs the tests with; only copied to .github/workflows/ by backup.py --enable-tests-on-github
- tests/reports/       - saved games (replay.py --save-as) that the tests play again
- mana_hint.py         - the "Why can't I pay?" sentence: which untapped permanents make which mana, and why a cost cannot be paid (no pygame)
- bug_report_config.example.json - copy to bug_report_config.json and add the Discord webhook address (not backed up, on purpose)
- bug_reports/         - created when a report is made: the zips (not backed up)
- backup_settings.json - created by backup.py --local-dir: where the local zips go (not uploaded)
- ../commander_sim_backups/ - created next to the project (or wherever you chose): snapshots/ (dated zips), forge_bundle/ (one copy)
- deck_library.py      - the decks you can pick from: reads my_decks/ and sample_decks/, saves imports, removes (no pygame)
- my_decks/            - created when you import: your decks as <name>.txt; my_decks/_removed/ holds removed ones
- sample_decks/        - the decks that come with the program: the 6 alpha Commander decks and 4 Brawl decks (patch 38: Goreclaw in,
                         Kinnan out - its list is a test deck now, tests/fixtures/decks/)
- forge_log.py         - turns Forge's game log into readable rows (no pygame; tested on its own)
- gfx.py               - colours, fonts, mana symbols, card rendering helpers
- art_loader.py        - card art loaded in a background thread
- forge_client.py      - starts Forge (Java) and talks to it: JSON lines over stdin/stdout
- java_bridge/         - forge_bridge.jar and its Java source (the bridge into Forge's GUI interface); a newer jar
                         here is copied into forge_runtime/ automatically when the game starts. The jar also holds
                         a patched copy of Forge's MulliganService (src/forge/game/mulligan/) that makes the first
                         mulligan free; the game puts the bridge jar FIRST on the Java class path so it replaces
                         Forge's own copy. Changed a .java file? Rebuild the jar (python setup_forge.py, needs a JDK)
                         and commit it with the change: the jar carries a stamp of the sources it was built from, and
                         tests/test_round27c.py fails when the two differ (round 27c: an old jar once shipped that way).
                         Round 27d: the bridge checks its own answers (Checks.java); a broken rule writes "Bridge
                         self-check failed" to crash_log.txt - include it when you send a bug report
- forge_bundle/        - Forge packed in checksummed pieces; setup_forge.py unpacks it to forge_runtime/
- setup_forge.py       - installer / checker (python setup_forge.py --check)
- deck_loader.py       - deck files / URLs -> commander + 99 cards
- forge_engine.log     - created when playing: Forge's own messages (look here if something breaks)
- crash_log.txt        - created only when something goes wrong: the reports described above (crash_native.txt: hard crashes)
- crashlog.py / version.py - the report writer and the version stamp (no game code; tested on their own)
- requirements.txt     - the exact package versions (pinned)
- forge_decks/         - created when playing: the .dck files handed to Forge

FILES - shared
--------------
- card_data.py         - Scryfall fetching (batch + single), local data and image cache, Forge script cache
- deck_importer.py     - Moxfield / Archidekt / pasted-text deck import
- settings.json        - text size, fullscreen, window size (shared by both tables); the forge table also keeps the last deck choice
- tests/               - unit and headless GUI tests, saved Scryfall data, saved Forge scripts and
                         saved Forge game states (tests/fixtures/forge_states)
- bug_report.py        - the OLD table's bug report (description, log, board) as JSON; the Forge table uses reporting.py

OLDER TABLE (table_gui.py) - our own simplified rules
-----------------------------------------------------
    python table_gui.py [your_deck] [ai_deck]
Still works and is still tested. It automates turn order, mulligans, land drops, mana abilities
and payment, commander tax, fetch/shock lands, opening-hand Gemstone Caverns (K keep / M mulligan;
T flips who goes first while you decide; after you keep, a step before turn 1 lets you use
Gemstone Caverns if you go second, then ENTER starts the game), and undo. It does NOT do the
stack, targeting, combat or spell effects, and its AI only untaps and draws. Controls: click a
hand card to play/cast, click a permanent to tap it, right-click for menus, SPACE end turn,
D draw, Z undo, H help. Files used only by it: table_gui.py, game_actions.py, game_state.py,
mana_system.py, forge_scripts.py, forge_rules.py (reads Forge card scripts for how lands and
mana rocks work), rules_report.py (python rules_report.py my_deck.txt shows what it understands),
pod_game_state.py and forge_bridge.py (older unrelated sketches, not used).

KNOWN GAPS / NEXT STEPS
------------------------
See ROADMAP.txt.

INSTALLER (Round 29; first built on Windows 3 Oct 2026)
------------------------------------------------------
Manticore is also built as a one-file Windows installer for alpha testers: no Python, no Java, no admin rights, installed for the
current user only. Karl builds it with  python tools\build_installer.py --test  (see EVENING_CHECKLIST.txt). Uninstalling removes
the program and keeps your decks, settings and saved games unless you answer Yes to the question (or pass /PURGE to a silent
uninstall). Forge's own folder is never touched.
It is an x64 build. Patch 37: it also installs on Windows 11 on Arm (a Snapdragon Surface, say), which runs x64 programs through
its own emulation; before that the installer said "This program does not support the version of Windows your computer is
running". How well it plays there isn't known yet: Karl's Surface Pro 11 is the first test. Windows 10 on Arm can't run it.
Unsigned: SmartScreen says "Windows protected your PC" (Run anyway; on some PCs it's behind "More info"). A PC with Smart App
Control on blocks it with no way past, which only code signing fixes.

UPDATES (Round 30: an installed copy updates itself)
----------------------------------------------------
An installed copy (the Round 29 installer) looks for a newer version once each time it starts, on the title screen and never during
a game. When there is one, a card in the top-right corner says so: Update now / Later / Skip this version. Update now downloads the
new installer (its size and SHA-256 checksum must match what the release says, or it is thrown away), closes Manticore, installs over
the top without questions, and starts the new version. Decks, settings and saved games live in the per-user folders, so they stay.
Cog > Help > Updates turns the check off. `Manticore-cli.exe --version` says whether this copy looks, and where.

Your own git copy never updates itself (it updates through git), and the tests never look.

Where it looks: update_config.json next to the program - {"repo": "owner/name"} (the GitHub repository whose Releases hold the
installer and latest.json; it must be PUBLIC) or {"feed_url": "https://..."}. Both empty = updates off. Patch 35 (3 Oct 2026) set
"repo": "Eliminate7532/Manticore".
To publish an update (Karl only):
  1. raise version.VERSION, build:          python tools\build_installer.py --release
  2. write the feed:                        python tools\make_update_feed.py installer_out\Manticore-<version>-setup.exe --notes "..."
  3. run the "gh release create" line it prints. NOT a pre-release and NOT a draft: installed copies ask GitHub for the
     latest release, which skips both.
A copy built before the repo was set never looks; the first build with update_config.json filled in must reach testers by hand.
