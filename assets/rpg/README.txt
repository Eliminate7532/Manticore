The Adventure's pixel art (Round RPG10, 23 Oct 2026)
=====================================================

Karl chose this look on the "Adventure Art Survey" page (23 Oct 2026): DawnLike's 16 x 16 tiles and people, with Simple Battles'
backdrops and foes for the encounter screen. Both packs are free to share and change under Creative Commons Attribution licences.
Every file here is exactly as its pack ships it: tools/rpg_import_art.py copied it out of the pack's zip, and MANIFEST.sha256
lists each file's SHA-256 (tests/test_rpg10.py checks them). On screen the game may scale the pictures and draw a darkness
layer with light over them; the files themselves are not changed.

dawnlike/
    DawnLike 16x16 Universal Rogue-like tileset v1.81, by DragonDePlatino, on the DawnBringer 16-colour palette by DawnBringer.
    Licence: Creative Commons Attribution 4.0 (CC-BY 4.0), licenses/texts/CC-BY-4.0.txt.
    Page: https://opengameart.org/content/dawnlike-16x16-universal-rogue-like-tileset-v181
    Zip: https://opengameart.org/sites/default/files/DawnLike_5.zip
         SHA-256 3ef8819e38ccef1b543a3d74b4bbf31022a95e22fafd8115e89694d8ccf2c179 (downloaded 23 Oct 2026)
    Copied: Characters/*.png, Objects/*.png, the five class sheets in Commissions/ (Warrior, Paladin, Rogue, Mage, Engineer),
            GUI/GUI0.png and GUI1.png, the SDS 6x6 and 8x8 fonts, and the pack's own README.txt.
    Not copied: Items/, Examples/, the .ase sources and the Clothes overlays (not used by the slice).
    The pack's README asks every game that uses it to hide its "Platino" sprite (Characters/Reptile0.png and Reptile1.png,
    column 3, row 12, counting from 0 - the 16 x 16 cell at x 48, y 192). The Adventure hides one in Lantern Hollow.

simple_battles/
    Simple Battles, drawn by Hyptosis (uploaded to OpenGameArt by speveril; "Released with permission").
    Licence: Creative Commons Attribution 3.0 (CC-BY 3.0), licenses/texts/CC-BY-3.0.txt.
    Page: https://opengameart.org/content/simple-battles
    Zip: https://opengameart.org/sites/default/files/simple_battles.zip
         SHA-256 95cb940450783eb01277ede26b29ee274b5f0c7f972545f1732bf5ece728c6f1 (downloaded 23 Oct 2026)
    Copied: all 19 pictures - 16 foes ("battlers") and 3 backdrops (scene_dungeon, scene_cave, scene_forest). The zip has no
            licence file of its own; the licence and credit are the page's.

Both packs are credited in Cog > Licenses and credits and in THIRD_PARTY_NOTICES.txt (licenses/NOTICES.json).
