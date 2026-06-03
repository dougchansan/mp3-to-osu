"""Command-line entry point: python -m mp3_to_osu <audio|--url> [options]."""

from __future__ import annotations

import argparse
import sys

from .pipeline import generate
from .rhythm import PRESETS


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mp3-to-osu",
        description="Auto-generate an osu! standard beatmap from an audio file.",
    )
    p.add_argument("audio", nargs="?",
                   help="Path to the song (mp3/ogg/wav/flac).")
    p.add_argument("--url",
                   help="Download audio from a YouTube/SoundCloud URL "
                        "(needs yt-dlp; not Spotify).")
    p.add_argument("-o", "--out", default="output",
                   help="Output directory (default: ./output).")
    p.add_argument("-d", "--difficulty", default="hard",
                   choices=sorted(PRESETS), help="Single-difficulty preset.")
    p.add_argument("--spread", action="store_true",
                   help="Generate a full Easy->Expert spread in one mapset.")
    p.add_argument("--artist", help="Override artist (else parsed from name).")
    p.add_argument("--title", help="Override title (else parsed from name).")
    p.add_argument("--creator", default="dougchansan", help="Mapper name.")
    p.add_argument("--background", help="Optional background image filename.")
    p.add_argument("--style",
                   help="Path to a learned StyleProfile JSON (or a name in "
                        "./profiles) to map in that mapper's style.")
    args = p.parse_args(argv)

    if not args.audio and not args.url:
        p.error("provide an audio file path or --url")

    style = None
    if args.style:
        import os

        from .style import StyleParams
        sp = args.style
        cand = sp if os.path.isfile(sp) else os.path.join(
            "profiles", sp if sp.endswith(".json") else sp + ".json")
        if not os.path.isfile(cand):
            print(f"error: style profile not found: {cand}", file=sys.stderr)
            return 1
        style = StyleParams.from_profile_json(cand)

    try:
        r = generate(
            args.audio,
            args.out,
            difficulty=args.difficulty,
            spread=args.spread,
            url=args.url,
            artist=args.artist,
            title=args.title,
            creator=args.creator,
            background=args.background,
            style=style,
        )
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"  BPM     : {r.bpm:.2f}")
    print(f"  Audio   : {r.audio_path}")
    for name, objs in r.difficulties:
        print(f"  {name:<8}: {objs} objects")
    print(f"  Beatmap : {r.osz_path}")
    print("\nDrag the .osz onto osu! (or into Songs/) to play.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
