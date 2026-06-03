#!/usr/bin/env python3
"""Download osu!'s default-skin hit-sound samples for local use.

These samples are NOT bundled in this repository — they are assets of
osu! / ppy Pty Ltd, fetched here for personal use only (see LICENSE). The
studio plays them when present and falls back to a synthesized click when
they are absent, so this step is optional but makes the hit-sounds authentic.

    python scripts/fetch_hitsounds.py
"""
from __future__ import annotations

import os
import urllib.request

BASE = ("https://raw.githubusercontent.com/ppy/osu-resources/master/"
        "osu.Game.Resources/Samples/Gameplay")
FILES = [
    "normal-hitnormal.wav",
    "normal-hitwhistle.wav",
    "normal-hitfinish.wav",
    "normal-hitclap.wav",
    "normal-slidertick.wav",
]
DEST = os.path.join(os.path.dirname(__file__), "..", "src", "mp3_to_osu",
                    "web", "static", "sounds")


def main() -> int:
    dest = os.path.abspath(DEST)
    os.makedirs(dest, exist_ok=True)
    print(f"Fetching {len(FILES)} osu! default-skin samples -> {dest}")
    for name in FILES:
        out = os.path.join(dest, name)
        try:
            urllib.request.urlretrieve(f"{BASE}/{name}", out)
            print(f"  OK  {name}  ({os.path.getsize(out)} bytes)")
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"  FAIL {name}: {e}")
    print("Done. These are osu!'s assets; for personal use only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
