# Manticore sound bible

What every sound in `sounds/` is meant to be, and why. Written for patch 49 (AU2, 0.28.53, 8 Oct 2026) from Karl's review of every
AU1 sound (7 Oct: 17 Keep, 36 Redo) and his picks on the "Manticore Sound Candidates" page (7 Oct: 37 picked, 9 "none of these").
The draft this grew from is claude/SOUND_BIBLE_DRAFT_2026-10-07.md in the project; the picks are in
claude/ROUND_AU2_PICKS_ROUND2_2026-10-08.md.

## 1. The one sentence

Dark fantasy in the spirit of Diablo 1 and Dark Souls, from CC0 recordings only: **magic is energy and air, bodies are leather and
metal, harm is flesh, turns are horns and drums, the end is crows or a hollow swell** - and bells only where they already earned their
keep (vs.reveal, boot.splash). AU1's "everything is stone, iron, parchment and bells in a cold church" is what Karl redid ("not every
sound in the game has to sound like church bells").

## 2. Where the sounds come from

- **CC0 only.** Every recording is listed in `tools/sound_sources.json` with its page, author and licence, read on the page itself.
  `sounds/CREDITS.txt` names exactly the recordings a recipe uses (a replaced AU1 source drops out of it). "Like Diablo" and "YOU
  DIED" mean in that spirit: no Blizzard or FromSoftware sample can be in a GPL download.
- AU2's recordings are OpenGameArt pages (keys `oga2-*`, `mus2-*`). Some pages are dual-licensed OGA-BY / CC0; we use them under CC0
  and the record's `note` says so. An AU2 record says `prefer_lossless`: from a page with the same sound as .wav and .ogg the build
  reads the lossless one. AU1's records never say it, so a rebuild of a Keep reads the file it always read.
- `tools/build_sounds.py` turns recordings into cues: cut, trim to the onset, pitch down by resampling, band-limit, mix, a small
  synthetic room, level to a loudness target with a peak limiter. Fixed seeds and a bit-exact Vorbis encoder: the same files every
  run. It is a developer tool (numpy, scipy, ffmpeg); the game never runs it.

## 3. The families

| family | sounds like | cues |
|---|---|---|
| **magic** | energy, shimmer, whoosh, crackle, a short "appear" | spell.cast, spell.resolve, spell.fizzle, ability.trigger, token.create, card.land.enchantment, destroy.enchantment |
| **metal and leather** | clinks, coins, blades, footfalls; Diablo's rings and amulets for artifacts | card.land.artifact, card.land (a creature), damage.creature, combat.block, counter.add, counter.remove, untap |
| **horns and drums** | a war horn, a far drum | phase.turn_mine (a horn), phase.turn_theirs (a drum, far), game.win (horns and a snare) |
| **flesh and crows** | a punch and a grunt, a drain, crows | damage.player, life.loss, game.lose (a crow, a moan, a rumble), destroy (a creature: a hit and a splat) |
| **potion** | Diablo's potion: a vial uncorked, a swallow, a lift | life.gain |
| **cards** | real card stock | card.draw, deck.shuffle (two riffles), flurry (three cards slid at once - not a shuffle), ui.menu_hover (a card swipe) |
| **earth** | a quick sample of the land's own type | land.play.forest (a bird, leaves), land.play.swamp (mud, a bubble) |
| **wood** | small and dry | tap (a wood tick) |
| **UI** | quiet, readable | ui.error (a lowered card-game error); the rest are AU1's Keeps |
| **rooms** | a bed under the mix, no droning wind | amb.citadel (a dungeon with a fire), amb.title (wind under 400 Hz, a dark ambience), amb.ruins (wind low, a tree creak, drops), amb.plain (the dungeon, dulled) |

## 4. Levels and lengths

- The answer sounds come first (`TIGHT` in build_sounds: clicks, the card pickup, dialogs): any slow lead-in is cut.
- **Short where frequent.** A cue on the sfx or ui bus with `cooldown_ms` under 100 is at most 1.2 s (`tests/test_au1.py`, patch 47);
  AU2's picks are built to at most `AU2_CAP_SECONDS` = 1.15 s. Only the stingers carry a tail: the turn sounds, game.win / game.lose,
  rewind, vs.reveal, boot.splash. `LONG_FOR_NOW` in test_au1 lists the long files still waiting for a replacement (land.play, the
  "other land" sound, from round 2; exile, a Keep, long on purpose); the test fails on a stale entry.
- **One room, not a church.** State changes and feedback get the small "room", mixed in at -13 to -20 dB; the bigger rooms (hall,
  crypt) are for the stingers, the end and the ambience.
- **Variation.** 2-4 files for anything that fires more than once a turn (draws, taps, hits, arrivals); the director never plays the
  same file twice in a row.
- **Darkened, not muffled.** Metal and card stock keep their top (up to 9 kHz); AU1 cut everything above 5-6.5 kHz.
- Each recipe's `level(x, lufs, peak)` sets its loudness; the director's buses (ui, sfx, stinger, ambience, music) and the cue's
  `gain_db` sit on top. The budget: 32 MB for `sounds/`, 400 KB per effect file (`tests/test_au1.py`).

## 5. Typed cues (`cue_types.py`)

Karl: "creatures need different sounds based off creature type, artifacts need to sound like rings/amulets from diablo, enchantments
need a magical flair", lands "a quick sample of the type of land they are", destruction "vary depending on what type of permanent".
A typed cue is the parent's name plus a suffix; `cue_types.choose()` plays the parent when the card's type isn't known or the typed
cue isn't in `cues.json`. The parent is the commonest case.

| parent | plays for | typed cues |
|---|---|---|
| card.land | a creature (humanoids and every creature outside a family) | card.land.artifact, card.land.enchantment (planeswalkers too), card.land.dragon / demon / undead / spirit / elemental / vermin / beast (when built) |
| land.play | any land without a picked sound of its own | land.play.forest, land.play.swamp (island, mountain, plains and "other" when built) |
| destroy | a creature's death (destroy and sacrifice) | destroy.artifact, destroy.enchantment (planeswalkers too), destroy.land (when built) |

The kind of permanent comes from `anim.arrival_kind()` (patch 48), the one function that reads a card's type line and flags; a creature's
family from its subtypes (`FAMILY_TYPES`, with the judgement calls written beside it). A typed cue plays by its parent's rules: the same
bus, cooldown, voices and gain.

## 6. Music

| slot | track | when |
|---|---|---|
| music.title | AU1's title track (Keep) | the title and main menu |
| music.deck | Echoes of the Past (AU1's source) | the deck screen |
| music.settings | Bleeding Out (AU1's source) | the cog's settings and Licenses and credits outside a game |
| music.game1-5 | AU1's five (Keep) | the game playlist, each once before any repeats, 25-80 s of silence between |
| music.game6 | Cold Silence (AU1's source) | in the game playlist from now on |
| music.attack | Lament of the War, its first 45 s | an interlude on a big attack: 10 or more total power, or enough to kill a defending player; at most once in 2 minutes |
| music.victory | AU1's track until round 2 ("wizards in a magical duel", none of the three candidates was picked) | after VICTORY |
| music.defeat | Bittersweet (Emma_MA) | after DEFEAT ("a 'YOU DIED' feel": short sad strings, then silence) |

## 7. Karl's picks (7 Oct)

| cue | pick | what it is |
|---|---|---|
| spell.cast | A | arcane gather-and-release (JaggedStone's magic spells, a swish under it) |
| spell.resolve | A | a bright burst that settles |
| spell.fizzle | C | a spell played backwards and cut dead, a metal clunk |
| ability.trigger | C | a crackle of an electric spell |
| token.create | C | cloth and a low magic swell |
| card.land | A | a creature: a leather footfall and a grunt |
| card.land.artifact | A | a ring clink |
| card.land.enchantment | C | a flight / spell tone |
| land.play.forest | C | a bird and leaves |
| land.play.swamp | A | mud and a bubble |
| destroy | C | a creature: a hit and a splat |
| destroy.artifact | A | metal falling and glass |
| destroy.enchantment | A | a spell torn (reversed) and paper |
| damage.creature | C | sword-knife clashes, a body hit under |
| damage.player | B | a punch and a grunt (qubodup, EmoPreben); louder for a bigger hit (patch 47) |
| life.loss | A | a drain and a heartbeat skip |
| life.gain | B | a vial uncorked, a swallow, a soft shimmer - the potion |
| counter.add | C | a coin struck on an anvil |
| counter.remove | A | the same chink, lower, falling away |
| combat.block | C | wood and a sheet of iron |
| phase.turn_mine | A | one war-horn call (Eldritch Grim) |
| phase.turn_theirs | C | a war drum, far (William Hector) |
| game.win | B | war horns and a snare |
| game.lose | C | a crow, an undead moan and a rumble |
| rewind | C | two whooshes backwards into a click |
| tap | C | a small wood tick |
| untap | A | a cloth step |
| flurry | C | three cards slid at once |
| card.draw | C | a card-game draw with a slide under it |
| deck.shuffle | C | two riffles back to back |
| ui.error | B | a card-game error, lowered |
| ui.menu_hover | C | a card swipe, very short |
| amb.citadel, amb.title, amb.ruins, amb.plain | C, C, C, C | the rooms in section 3 |
| music.defeat | C | Bittersweet |

**Keep** (Karl's 7 Oct review; byte for byte as AU1 built them, `tests/test_au2.py` holds their checksums): ui.click, ui.hover,
ui.dialog_close, ui.menu_select, boot.splash, vs.reveal, card.pickup, combat.attack, exile, amb.graveyard, amb.cathedral, music.title,
music.game1-5.

**Round 2** (a second candidates page, 8 Oct): the nine groups where none of the three was right - card.discard ("These all sound the
same"), card.play ("The magical flair needs a dark fantasy tone"), destroy.land, land.play island / mountain / plains / other ("These
all sound the same"), music.victory, ui.dialog_open ("Not attention getting") - and the seven creature families. Until then card.discard,
card.play, land.play, ui.dialog_open and music.victory play AU1's files, a land's destruction plays the creature's, and every creature
plays card.land.

## 8. Rebuilding

`python tools/build_sounds.py` downloads into its cache and rebuilds everything; `--only <cue> ...` rebuilds some. A Keep that uses a
Freesound recording needs a Freesound key in the cache to rebuild; its file is in the repository, so there is no need to. After a
rebuild: `tests/test_au1.py` and `tests/test_au2.py` (the Keeps' checksums, the credits, every cue's files, the length guard).

## NOT verified

Anything by ear on Claude's side: Claude cannot hear. Every sound was judged by its recording's description, spectrogram, loudness and
onset, and Karl's pick on the candidates page is the real check. The page played MP3 copies of the candidate OGG files; the
patch's files are byte for byte those OGG files.
