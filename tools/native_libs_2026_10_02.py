# SPDX-License-Identifier: GPL-3.0-or-later
"""One-off: fill licenses/NOTICES.json native_libraries from the pygame-ce 2.5.8 Windows wheel (2026-10-02).

Every DLL in pygame_ce-2.5.8-cp314-cp314-win_amd64.whl (the wheel requirements.txt pins for Python 3.14 on Windows,
sha256 b4c2e28c201240199356952e6bd0221969d2eb8a1a15c3454d77db209bad7891) was matched BYTE FOR BYTE to an upstream
package, and its licence was taken from that package (or the source tree it was built from):
  * SDL2-devel-2.32.10-VC.zip, SDL2_mixer-devel-2.8.2-VC.zip, SDL2_ttf-devel-2.24.0-VC.zip (github.com/libsdl-org)
  * SDL2_image-devel-2.8.12-VCpgce.zip (github.com/pygame-community/SDL_image, release 2.8.12-pgce)
  * prebuilt-x64-pygame-2.1.4-20220319_2.zip (github.com/pygame-community/pygame-ce, release 2.1.3)
These are the URLs pygame-ce 2.5.8's own buildconfig/download_win_prebuilt.py downloads.
Run once from the project folder; it rewrites native_libraries, marks the AvQest entry verified and adds the
"Created with Grok" credit (both Karl, 2026-10-02), and leaves everything else as it was.
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(HERE, "licenses", "NOTICES.json")

SDL = "https://github.com/libsdl-org/"
PGCE_IMG = "https://github.com/pygame-community/SDL_image/releases/tag/2.8.12-pgce"
PGCE_PRE = "https://github.com/pygame-community/pygame-ce/releases/tag/2.1.3"


def lib(id_, name, file, version, license, copyright, url, source, used_for, notice, note=None):
    out = {"id": id_, "name": name, "file": file, "version": version, "license": license, "copyright": copyright,
           "url": url, "source": source, "used_for": used_for, "shipped": "always (inside pygame-ce)",
           "group": "Native libraries (pygame)", "notice_file": notice, "status": "verified"}
    if note:
        out["note"] = note
    return out


N = "notices/native/"
LIBS = [
    lib("sdl2", "SDL2", "SDL2.dll", "2.32.10", "Zlib", "Copyright (C) 1997-2025 Sam Lantinga",
        SDL + "SDL", "byte-identical to SDL2-devel-2.32.10-VC.zip lib/x64", "Window, input, audio output", N + "SDL2.txt"),
    lib("sdl2-image", "SDL2_image", "SDL2_image.dll", "2.8.12 (pygame-ce build 2.8.12-pgce)", "Zlib AND MIT",
        "Copyright (C) 1997-2025 Sam Lantinga", PGCE_IMG, "byte-identical to SDL2_image-devel-2.8.12-VCpgce.zip lib/x64",
        "Loading card pictures and art", [N + "SDL2_image.txt", N + "nanosvg.txt", N + "qoi.txt"],
        "Built in: nanosvg (Zlib) and qoi (MIT). The source tree also carries public-domain stb_image, miniz and "
        "tiny_jpeg (no notice required). JPEG, PNG, TIFF and WebP are decoded by the separate DLLs listed below."),
    lib("sdl2-mixer", "SDL2_mixer", "SDL2_mixer.dll", "2.8.2",
        "Zlib AND Artistic-1.0-Perl AND CC0-1.0 AND (MIT OR Unlicense) AND (Unlicense OR MIT-0)",
        "Copyright (C) 1997-2025 Sam Lantinga", SDL + "SDL_mixer", "byte-identical to SDL2_mixer-devel-2.8.2-VC.zip lib/x64",
        "Sound effects, ambience and music",
        [N + "SDL2_mixer.txt", N + "timidity-SDL_mixer.txt", N + "minimp3.txt", N + "stb_vorbis.txt", N + "dr_libs.txt"],
        "Built in (from SDL_mixer release-2.8.2 src/codecs): Timidity MIDI (Perl Artistic License, Tuukka Toivonen), "
        "minimp3 (CC0-1.0), stb_vorbis v1.22 (MIT or public domain), dr_flac from dr_libs (public domain or MIT-0). "
        "Manticore plays only Ogg Vorbis files; the MIDI code is present in the DLL but never used."),
    lib("sdl2-ttf", "SDL2_ttf", "SDL2_ttf.dll", "2.24.0", "Zlib AND FTL AND MIT-Modern-Variant",
        "Copyright (C) 1997-2025 Sam Lantinga", SDL + "SDL_ttf", "byte-identical to SDL2_ttf-devel-2.24.0-VC.zip lib/x64",
        "Drawing text", [N + "SDL2_ttf.txt", N + "freetype.txt", N + "harfbuzz.txt"],
        "Built in: FreeType 2.13.2 (used under the FreeType License, FTL; it is also offered under GPL-2.0) and HarfBuzz "
        "8.1.1 (\"Old MIT\" = MIT-Modern-Variant), the libsdl-org submodule commits pinned by SDL_ttf release-2.24.0. "
        "Portions of this software are copyright (C) The FreeType Project (www.freetype.org). All rights reserved."),
    lib("freetype", "FreeType", "freetype.dll", "2.11.1", "FTL",
        "Copyright (C) 2000-2021 The FreeType Project", "https://freetype.org", "byte-identical to pygame-ce "
        "prebuilt-x64-pygame-2.1.4-20220319_2.zip lib/", "pygame.freetype text drawing", N + "freetype.txt",
        "Used under the FreeType License (FTL); FreeType is also offered under GPL-2.0. Portions of this software are "
        "copyright (C) 2021 The FreeType Project (www.freetype.org). All rights reserved."),
    lib("libjpeg-turbo", "libjpeg-turbo", "libjpeg-62.dll", "libjpeg-turbo (2026 build, libjpeg API 6.2)",
        "IJG AND BSD-3-Clause AND Zlib", "Copyright (C) 1991-2026 The libjpeg-turbo Project and many others",
        "https://libjpeg-turbo.org", "byte-identical to SDL2_image-devel-2.8.12-VCpgce.zip lib/x64/optional",
        "JPEG card pictures", N + "libjpeg-turbo.txt",
        "This software is based in part on the work of the Independent JPEG Group."),
    lib("libogg", "libogg", "libogg-0.dll", "as shipped with SDL2_mixer 2.8.2", "BSD-3-Clause",
        "Copyright (c) 2002-2020 Xiph.org Foundation", "https://xiph.org/ogg/",
        "byte-identical to SDL2_mixer-devel-2.8.2-VC.zip lib/x64/optional", "Ogg container for Opus", N + "libogg.txt"),
    lib("libopus", "Opus", "libopus-0.dll", "1.4", "BSD-3-Clause",
        "Copyright 2001-2011 Xiph.Org, Skype Limited, Octasic, Jean-Marc Valin, Timothy B. Terriberry, CSIRO, Gregory "
        "Maxwell, Mark Borgerding, Erik de Castro Lopo", "https://opus-codec.org",
        "byte-identical to SDL2_mixer-devel-2.8.2-VC.zip lib/x64/optional", "Opus audio decoding", N + "opus.txt"),
    lib("libopusfile", "opusfile", "libopusfile-0.dll", "as shipped with SDL2_mixer 2.8.2", "BSD-3-Clause",
        "Copyright (c) 1994-2013 Xiph.Org Foundation and contributors", "https://opus-codec.org",
        "byte-identical to SDL2_mixer-devel-2.8.2-VC.zip lib/x64/optional", "Opus file reading", N + "opusfile.txt"),
    lib("libpng", "libpng", "libpng16-16.dll", "1.6.58 (with zlib 1.3.1 built in)", "libpng-2.0 AND Zlib",
        "Copyright (c) 1995-2026 The PNG Reference Library Authors, (c) 2018-2026 Cosmin Truta; zlib Copyright (C) 1995-2024 Jean-loup Gailly and "
        "Mark Adler", "http://www.libpng.org/pub/png/libpng.html",
        "byte-identical to SDL2_image-devel-2.8.12-VCpgce.zip lib/x64/optional", "PNG pictures", N + "libpng.txt",
        "The licence file is libpng's own LICENSE at tag v1.6.58 (github.com/pnggroup/libpng)."),
    lib("libtiff", "LibTIFF", "libtiff-5.dll", "4.2.0", "libtiff",
        "Copyright (c) 1988-1997 Sam Leffler; Copyright (c) 1991-1997 Silicon Graphics, Inc.", "https://libtiff.gitlab.io/libtiff/",
        "byte-identical to SDL2_image-devel-2.8.12-VC.zip lib/x64/optional", "TIFF pictures (not used by Manticore)",
        N + "libtiff.txt"),
    lib("libwavpack", "WavPack", "libwavpack-1.dll", "5.6.0", "BSD-3-Clause", "Copyright (c) 1998-2022 David Bryant",
        "https://www.wavpack.com", "byte-identical to SDL2_mixer-devel-2.8.2-VC.zip lib/x64/optional",
        "WavPack audio (not used by Manticore)", N + "wavpack.txt"),
    lib("libwebp", "libwebp", "libwebp-7.dll", "as shipped with SDL2_image 2.8.12", "BSD-3-Clause",
        "Copyright (c) 2010, Google Inc. All rights reserved.", "https://chromium.googlesource.com/webm/libwebp",
        "byte-identical to SDL2_image-devel-2.8.12-VC.zip lib/x64/optional", "WebP pictures (not used by Manticore)",
        N + "libwebp.txt"),
    lib("libwebpdemux", "libwebpdemux", "libwebpdemux-2.dll", "as shipped with SDL2_image 2.8.12", "BSD-3-Clause",
        "Copyright (c) 2010, Google Inc. All rights reserved.", "https://chromium.googlesource.com/webm/libwebp",
        "byte-identical to SDL2_image-devel-2.8.12-VC.zip lib/x64/optional", "Animated WebP (not used by Manticore)",
        N + "libwebp.txt"),
    lib("libxmp", "libxmp", "libxmp.dll", "4.6.3", "MIT", "Copyright (C) 1996-2024 Claudio Matsuoka and Hipolito Carraro Jr",
        "https://xmp.sourceforge.net", "byte-identical to SDL2_mixer-devel-2.8.2-VC.zip lib/x64/optional",
        "Tracker music modules (not used by Manticore)", N + "libxmp.txt"),
    lib("portmidi", "PortMidi", "portmidi.dll", "as shipped in pygame-ce's 2022-03-19 prebuilt", "MIT",
        "Copyright (c) 1999-2000 Ross Bencina and Phil Burk; Copyright (c) 2001-2006 Roger B. Dannenberg",
        "https://github.com/PortMidi/portmidi", "byte-identical to pygame-ce prebuilt-x64-pygame-2.1.4-20220319_2.zip lib/",
        "pygame.midi (not used by Manticore)", N + "portmidi.txt"),
]


GROK_CREDIT = {
    "id": "grok-art",
    "title": "Game art",
    "text": ("Some of Manticore's artwork was Created with Grok: the table backgrounds, the studio splash, the title screen "
             "and the window icon."),
}


def grok_attribution(manifest):
    """xAI's Brand Guidelines (x.ai/legal/brand-guidelines, 14 Feb 2025) ask for the words "Created with Grok" wherever
    Grok-generated material is distributed. Karl, 2026-10-02: put the line in Cog > Licenses and credits (and so in
    THIRD_PARTY_NOTICES.txt). Adds a credits entry after Wizards/Scryfall/card art and uses the same words on the
    art-backgrounds component. Running it twice changes nothing."""
    credits = [c for c in manifest.setdefault("credits", []) if c.get("id") != GROK_CREDIT["id"]]
    after = next((i + 1 for i, c in enumerate(credits) if c.get("id") == "artists"), len(credits))
    credits.insert(after, dict(GROK_CREDIT))
    manifest["credits"] = credits
    for comp in manifest.get("components", []):
        if comp.get("id") == "art-backgrounds":
            comp["copyright"] = "Created with Grok (xAI) for the Manticore project"
            comp["status"] = "attribution"          # Karl, 2026-10-02: release check exception, attribution satisfied
            comp["attribution"] = ("xAI's Brand Guidelines (x.ai/legal/brand-guidelines, 14 Feb 2025) ask for the words "
                                   "\"Created with Grok\" wherever Grok-generated material is distributed; they are in the "
                                   "\"Game art\" credit in Cog > Licenses and credits and in THIRD_PARTY_NOTICES.txt.")


# Checked 2026-10-02 against the sources on Maven Central (the artifact versions forge.jar's pom.properties list) and
# pygame-ce's own source at tag 2.5.8. "pom_says" is kept as it was; only the licence, status and note change.
JAVA = {
    "com.googlecode.soundlibs": dict(
        license="LGPL-2.0-or-later AND BSD-2-Clause", notice_file="notices/tritonus-bsd.txt",
        note="Checked in the sources jars: jlayer 1.0.1.4 (47 of 47 files), mp3spi 1.9.5.4 (17 of 17) and tritonus-share "
             "0.3.7.4 (58 of 60) say 'GNU Library General Public License ... version 2 of the License, or (at your option) "
             "any later version'. Tritonus' FloatInputStream.java and FloatSampleInput.java are BSD-2-Clause (Florian Bomers, "
             "2006); their notice is attached. The POM's 'LGPL 2.1' was not the whole story."),
    "javazoom": dict(
        license="LGPL-2.0-or-later",
        note="javazoom jlayer 1.0.1 ships no licence file and Maven Central's sources jar was not fetched (rate-limited). "
             "Its 47 classes are the same 47 that soundlibs' jlayer 1.0.1.4 repackages, whose source headers all say GNU "
             "Library General Public License version 2 or later, which matches JavaZOOM's own statement that JLayer is LGPL."),
    "com.miglayout": dict(
        note="Checked: every file in miglayout-core-4.2 and miglayout-swing-4.2 sources carries the 3-clause BSD header "
             "(Mikael Grev, MiG InfoCom AB, 2004), the same text as notices/miglayout.txt."),
    "io.github.x-stream": dict(
        note="notices/mxparser.txt is byte-identical to META-INF/LICENSE in mxparser-1.2.2.jar. Compared word by word with "
             "SPDX 'xpp': the conditions and disclaimer are the same; only the title line, the year (2003 vs 2002) and a "
             "spelling of 'University' differ, which SPDX's matching rules treat as the same licence."),
    "javax.servlet": dict(
        license="(CDDL-1.1 OR GPL-2.0-only WITH Classpath-exception-2.0) AND Apache-2.0", notice_file="notices/servlet-api.txt",
        note="Checked in javax.servlet-api-3.1.0-sources: all 72 files are 'GPL Version 2 only or CDDL' with the Classpath "
             "exception (glassfish CDDL+GPL_1_1); 40 of them are also 'Copyright 2004 The Apache Software Foundation' under "
             "Apache-2.0. We take the CDDL/GPL part under GPL-2.0 with the Classpath exception. The jar's own "
             "META-INF/LICENSE.txt (CDDL 1.0 + GPL 2 + Classpath exception) is attached."),
}


def java_and_pygame(manifest):
    for key, change in JAVA.items():
        comp = manifest.get("java_components", {}).get(key)
        if comp is not None:
            comp.update(change)
            comp["status"] = "verified"
    for comp in manifest.get("components", []):
        if comp.get("id") == "pygame-ce":
            comp["license"] = "LGPL-2.0-or-later"
            comp["status"] = "verified"
            comp["note"] = ("Checked in pygame-ce's source at tag 2.5.8: the files that carry a header say 'GNU Library General "
                            "Public License ... either version 2 of the License, or (at your option) any later version' "
                            "(Pete Shinners, 2000-2001); pyproject.toml says 'LGPL v2.1' and docs/LGPL.txt is the LGPL 2.1 text, "
                            "which 'or later' includes. Its bundled SDL_gfx primitives are LGPL too (A. Schiffler). The "
                            "Windows DLLs it ships are listed under Native libraries.")


def main():
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    for comp in manifest.get("components", []):
        if comp.get("id") == "font-display":         # Karl's decision, 2026-10-02
            comp["status"] = "verified"
            comp["note"] = ("Karl's decision on 2026-10-02: the author (GemFonts) lists AvQest as Freeware for personal and "
                            "commercial use on FontSpace (fontspace.com/gemfonts/avqest), which Karl takes as permission to ship it "
                            "in the app. The 1001fonts FFC text that came in the zip is kept below for reference. The embedding "
                            "flag (OS/2 fsType 1) is a deprecated legacy value, not a restriction. tools/export_public.py still "
                            "leaves the font out of the public source copy (the game falls back to Cinzel Decorative there).")
    grok_attribution(manifest)
    java_and_pygame(manifest)
    manifest["native_libraries"] = {
        "_source": ("pygame_ce-2.5.8-cp314-cp314-win_amd64.whl from PyPI (sha256 b4c2e28c201240199356952e6bd0221969d2eb8a1a15c3454d77db209bad7891); "
                    "each DLL matched byte for byte to an upstream package, see tools/native_libs_2026_10_02.py"),
        "libraries": LIBS,
    }
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    print(f"native_libraries: {len(LIBS)} DLLs written")


if __name__ == "__main__":
    main()
