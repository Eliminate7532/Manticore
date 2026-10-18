# SPDX-License-Identifier: GPL-3.0-or-later
"""
counter_names.py - what a person calls a counter whose name comes from Forge (Round CTR1).

The bridge sends a counter as Forge's CounterType.toString() (Snapshot.java's "counters", EventForwarder's "counters" event):
  - one of Forge's CounterEnumType constants by its Java name: "P1P1", "LOYALTY", "M0M1", "ACQUIREDTASTE" (237 at fb4d809; a
    script's "Stun" becomes STUN - CounterEnumType.getType upper-cases);
  - a keyword counter (rule 122.1b) by the keyword as the card's script writes it: "Flying", "First Strike" (seen live: "Flying");
  - any other counter a card script names (CounterCustomType) as the script writes it.
Forge's own name for an enum constant (CounterEnumType.getName) is the constant with only its first letter capital ("Loyalty"),
except for 15 that carry a name of their own: "+1/+1", "-1/-1", the other power/toughness counters ("-0/-1", "+2/+2" ...),
"acquired taste" and "tower". The table used to show "M0M1" and "Acquiredtaste" for those (str.title() of the Java name). Forge's
desktop draws short abbreviations on a card ("ATASTE", "ARWHD": getCounterOnCardDisplayName); the table keeps the words.
No pygame in here, so it is easy to test.
"""
import re

# Forge's getName() where it isn't just the constant capitalised (CounterEnumType.java at fb4d809, its three-string constructors;
# "tower" is the capitalised constant anyway). The +N/+N ones are read by PT below.
NAMED = {"ACQUIREDTASTE": "Acquired taste"}

_PT = re.compile(r"^([PM])(\d+)([PM])(\d+)$")        # P1P1 = +1/+1, M0M1 = -0/-1 ... (CounterEnumType.getType reads them back)


def counter_name(raw):
    """The words for a counter Forge names `raw`: "+1/+1", "-0/-1", "Loyalty", "Acquired taste", "First Strike", "Hexproof"."""
    raw = str(raw or "").strip()
    if raw in NAMED:
        return NAMED[raw]
    m = _PT.match(raw)
    if m:
        def sign(c):
            return "+" if c == "P" else "-"
        return f"{sign(m.group(1))}{m.group(2)}/{sign(m.group(3))}{m.group(4)}"
    head = raw.split(":", 1)[0]                     # a guard: no script at fb4d809 gives a counter a ":" parameter
    if not head:
        return ""
    if head.isupper():                              # a Forge constant: its first letter only, as Forge's getName() does
        return head[0] + head[1:].lower()
    return head[0].upper() + head[1:]               # a keyword or a script's own name: as written


def is_pt(raw):
    """True for a power/toughness counter (+1/+1, -0/-1 ...): its words are already a number, so no lower-casing."""
    return bool(_PT.match(str(raw or "").strip()))


def float_words(raw):
    """The word after a floating "+2" on the table (anim.py): "+1/+1", "loyalty", "acquired taste", "flying"."""
    name = counter_name(raw)
    return name if is_pt(raw) else name.lower()
