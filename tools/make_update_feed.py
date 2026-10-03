# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/make_update_feed.py - Round 30: writes latest.json for a finished installer, and prints the commands to publish both.

    python tools\\make_update_feed.py installer_out\\Manticore-0.28.14-setup.exe --notes "Online play with a friend."

What an installed copy reads (updater.py): {"version", "url", "sha256", "size", "notes", "published"}. The url points at the
installer as a GitHub release asset: https://github.com/<repo>/releases/download/v<version>/<file name>. The repo comes from
update_config.json (or --repo). latest.json is written next to the installer; nothing is uploaded - Karl publishes himself.

Two rules the update check depends on:
  * the release must NOT be a pre-release (or a draft): installed copies ask GitHub for /releases/latest/download/latest.json,
    and "latest" skips pre-releases and drafts;
  * the repository must be public: a private repository's release files can't be downloaded without signing in.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import updater   # noqa: E402
import version   # noqa: E402


class FeedError(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_from_config(folder=BASE_DIR):
    repo = str(updater.read_config(folder).get("repo") or "").strip()
    return repo or None


def build_feed(installer, repo, notes="", ver=None, today=None):
    """The latest.json dictionary for this installer (checked with the same rules an installed copy uses)."""
    ver = ver or version.VERSION
    if not os.path.isfile(installer):
        raise FeedError(f"no such file: {installer}")
    name = os.path.basename(installer)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}\.exe", name):
        raise FeedError(f"the installer's file name must be letters, digits, '.', '_' or '-' and end in .exe: {name}")
    if ver not in name:
        raise FeedError(f"{name} doesn't carry this program's version ({ver}): build the installer from this copy first")
    if not repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise FeedError("no repository: put \"repo\": \"owner/name\" in update_config.json, or pass --repo owner/name")
    feed = {
        "version": ver,
        "url": f"https://github.com/{repo}/releases/download/v{ver}/{name}",
        "sha256": sha256_file(installer),
        "size": os.path.getsize(installer),
        "notes": " ".join(str(notes or "").split())[:updater.NOTES_MAX],
        "published": (today or datetime.date.today()).isoformat(),
    }
    info, why = updater.parse_feed(feed)
    if info is None:
        raise FeedError("the feed would be refused by an installed copy: " + why)
    return feed


def gh_commands(installer, feed_path, repo, ver, notes):
    """What Karl runs to publish (needs the GitHub CLI, signed in). NOT --prerelease: installed copies would never see it."""
    quoted = notes.replace('"', "'") or f"Manticore {ver}"
    return [
        f'gh release create v{ver} "{installer}" "{feed_path}" --repo {repo} --title "Manticore {ver}" '
        f'--notes "{quoted}" --latest',
        f"gh release view v{ver} --repo {repo}",
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write latest.json for a finished Manticore installer.")
    ap.add_argument("installer", help="the setup .exe from installer_out\\")
    ap.add_argument("--notes", default="", help="what's new, a line or two (shown on the update card)")
    ap.add_argument("--repo", help="owner/name of the public GitHub repository (default: update_config.json's repo)")
    ap.add_argument("--out", help="where to write latest.json (default: next to the installer)")
    args = ap.parse_args(argv)
    repo = args.repo or repo_from_config()
    try:
        feed = build_feed(args.installer, repo, args.notes)
    except FeedError as e:
        print("make_update_feed: " + str(e))
        return 2
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.installer)), "latest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(feed, f, indent=2)
        f.write("\n")
    print(f"Wrote {out}")
    print(f"  version {feed['version']}, {feed['size'] / 1e6:.0f} MB, sha256 {feed['sha256'][:16]}...")
    print(f"  installed copies will download {feed['url']}")
    print()
    print("To publish (the repository must be public; do NOT mark the release as a pre-release or a draft):")
    for line in gh_commands(args.installer, out, repo, feed["version"], feed["notes"]):
        print("  " + line)
    print()
    print("Then check: https://github.com/%s/releases/latest/download/latest.json shows this version." % repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
