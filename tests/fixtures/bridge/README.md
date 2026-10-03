# Recorded bridge games (Round 27d)

These are real games through the Round 27d bridge. They were recorded in the sandbox on 2026-09-26, using Forge 3a74143 and Java 21, with the `card_check.Checker` set-up (deck: `kinnan_nbc_moxfield_export.txt`, seed 7, one "Filler" AI).

They let `bridge_rules.py` (Round 28b) be built and tested **without Forge**. **Don't edit them.** If one looks wrong, write it up.

## The files

For each game there are three files:

| File | What it holds |
|---|---|
| `<name>.jsonl.gz` | every message, both directions, in order (gzip, one JSON object per line) |
| `<name>.engine.log` | the engine's stderr for that game (Forge start-up chatter, `bridge: CHECK FAILED ...` lines) |
| `<name>.json` | what the game was, its faults, the bridge checks it produced, and `expected_fail_rules` for the Round 28b rules |

### Message kinds

* **From Forge:** `ready`, `state` (full snapshot), `event`, `log`, `request`, `shape`, `check`, `info`/`message`, `dropped`, `game_over`, `fatal`.
* **Sent to Forge:** `{"t":"_sent","time":<unix time>,"cmd":{...}}`. `cmd.c` is `ok`, `cancel`, `card`, `reply` (answer to a request: `id`, `value`), `setup` (dev board set-up), `quit`, and so on. Clicks carry `at`, the question number.

## The games

| Name | Fault | What happens | Expected (Round 28b rules) |
|---|---|---|---|
| `spiteful_ok` | – | Spiteful Visions cast on turn 3, played to my turn-5 main phase. Two `order` requests: "Select order for simultaneous abilities" on the AI's draw, "Reorder simultaneous abilities" on mine; both answered `[0, 1]`. Hand 3, life 37 at the end. | nothing |
| `spiteful_fault` | `ORDER_IGNORES_SORTED` | The same. The reorder is never sent as a request: the bridge sees 0 items, answers nothing, and reports `order/wrong_count`. Hand 2, life 39 at the end: my draw lost both triggers. | `bridge_check` |
| `cmdzone_yes` | – | Kinnan cast from the command zone, then Swords to Plowshares on him. | nothing |
| `cmdzone_no` | – | The same, answered **No**: Kinnan stays in exile. | nothing: the question was asked |
| `cmdzone_fault` | `NO_COMMANDER_VARIANT` | The same, but no question comes, and Kinnan stays in exile. A `check` `start/commander_variant_missing` is the second message. | `bridge_check`, `commander_stranded` |
| `surveil_ok` | – | Raucous Theater played, surveil 1. The prompt carries `source` (Forest); answered Graveyard (`cancel`). | nothing |

### What the command-zone question looks like

* It's **not** a `request`: it's the state's `prompt`, with `input: "InputConfirm"`, `ok.label` "Yes" and `cancel.label` "No". It's answered by `_sent` `ok` / `cancel`.
* The message starts `Kinnan, Bonder Prodigy: If a commander is in a graveyard or in exile and that card was put into that zone since the last time state-based actions were checked...`, so look for "command zone" anywhere in the text. It has no `source`.
* **Timing:** the first state with Kinnan in exile can be the **same** state that shows this prompt. A rule must check the prompt on that state too, before deciding the commander was stranded.
* **After "No":** Kinnan stays in exile for the rest of the stream. Remember that a commander was asked about, and only look again once it has left exile or the graveyard.

### Card headers in prompts

Among these games, only the surveil prompt starts with Forge's card header, `Forest (205)` then a blank line, and it has `source`.

Other prompts that carry a `source` have other forms:
* `Spiteful Visions` then a blank line (paying a cost);
* `Kinnan, Bonder Prodigy - Creature 2 / 2` then a blank line;
* `Swords to Plowshares (203) - Exile target creature...` (targeting).

So "header present, `source` missing" is a sensible check, but it has only been seen on this one prompt type.

## How they were made

`tools/record_bridge_fixtures.py` (needs Java 17+ and `forge_runtime/`, like the live tests). It wraps `card_check.Checker` with `ForgeSession(record_path=...)` and copies the engine log.
