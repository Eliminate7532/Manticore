# SPDX-License-Identifier: GPL-3.0-or-later
"""Copies the Adventure's pixel art into assets/rpg/ from the packs' own zip files, unchanged (Round RPG10, 23 Oct 2026).

Karl's choice on the "Adventure Art Survey" page (23 Oct): look A - DawnLike 16x16 v1.81 with Simple Battles. Both are CC-BY
(see assets/rpg/README.txt and licenses/NOTICES.json). Only the parts the vertical slice uses are copied; every file is copied
byte for byte, and assets/rpg/MANIFEST.sha256 records each one's SHA-256 so tests/test_rpg10.py can prove nothing was changed.

    python tools/rpg_import_art.py --dawnlike DawnLike_5.zip --simple-battles simple_battles.zip

The zips (downloaded 23 Oct 2026; their SHA-256 are below, and the script refuses another file unless --any-version):
    https://opengameart.org/sites/default/files/DawnLike_5.zip
    https://opengameart.org/sites/default/files/simple_battles.zip
"""
import argparse
import hashlib
import os
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEST = os.path.join(ROOT, "assets", "rpg")
PACKS = {
    "dawnlike": {
        "sha256": "3ef8819e38ccef1b543a3d74b4bbf31022a95e22fafd8115e89694d8ccf2c179",
        "keep": lambda n: (n.startswith(("Characters/", "Objects/")) and n.endswith(".png"))
        or n in ("Commissions/Warrior.png", "Commissions/Paladin.png", "Commissions/Rogue.png", "Commissions/Mage.png",
                 "Commissions/Engineer.png", "GUI/GUI0.png", "GUI/GUI1.png", "GUI/SDS_8x8.ttf", "GUI/SDS_6x6.ttf",
                 "README.txt"),
    },
    "simple_battles": {
        "sha256": "95cb940450783eb01277ede26b29ee274b5f0c7f972545f1732bf5ece728c6f1",
        "keep": lambda n: n.endswith(".png") and "/" not in n,
    },
}
MANIFEST = os.path.join(DEST, "MANIFEST.sha256")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def copy_pack(name, zpath, any_version=False):
    with open(zpath, "rb") as f:
        whole = f.read()
    if sha256(whole) != PACKS[name]["sha256"] and not any_version:
        raise SystemExit(f"{zpath}: not the {name} zip this was made from (SHA-256 {sha256(whole)}); --any-version to use it anyway")
    out = []
    with zipfile.ZipFile(zpath) as z:
        for n in sorted(z.namelist()):
            if n.endswith("/") or not PACKS[name]["keep"](n):
                continue
            data = z.read(n)
            rel = f"{name}/{n}"
            target = os.path.join(DEST, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(data)
            out.append((sha256(data), rel))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dawnlike", required=True)
    ap.add_argument("--simple-battles", required=True)
    ap.add_argument("--any-version", action="store_true")
    a = ap.parse_args(argv)
    rows = copy_pack("dawnlike", a.dawnlike, a.any_version) + copy_pack("simple_battles", a.simple_battles, a.any_version)
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        f.write("# SHA-256 of every file in assets/rpg/ as it is in its pack's zip (tools/rpg_import_art.py); tests/test_rpg10.py\n")
        for digest, rel in rows:
            f.write(f"{digest}  {rel}\n")
    print(f"{len(rows)} files copied into {DEST}; manifest {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
