The Adventure's maps (Round RPG12, 23 Oct 2026)

Edit the layouts in src/ (one character a tile), then run:

    python tools\rpg_make_maps.py

That rebuilds every .tmx and dl_*.tsx file here from src/. Don't edit the .tmx files themselves: the next run overwrites them.
You can open a .tmx in the Tiled editor to look at it. To keep a change made in Tiled, copy it back into the layout in src/.

    python tools\rpg_make_maps.py --check      says whether the .tmx files are what src/ builds (the tests run it too)

The maps:
    lantern_hollow.txt     Lantern Hollow, the start town (40 x 30): the inn, the card shop, the pack shop, the duel hall
    ashfen_outskirts.txt   the Ashfen outskirts (60 x 40): the road, the river, the chapel, the old mill
    warrens_1.txt          the Warrens (40 x 30), under the mill
    warrens_2.txt          the Warrens, deeper (40 x 30): the Grave Priest

How a layout is written, and how the tool picks DawnLike's pieces, is at the top of tools/rpg_make_maps.py.
The pictures are DawnLike's (assets/rpg/dawnlike/, CC-BY 4.0, DragonDePlatino; credited in Licenses and credits).
The placeholder maps of Rounds RPG5-RPG11 are in tests/fixtures/rpg_maps/ (only the tests use them).
