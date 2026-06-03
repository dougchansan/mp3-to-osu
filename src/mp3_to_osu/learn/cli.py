"""Analyzer CLI:  python -m mp3_to_osu.learn <command>

  survey   list mappers in the library by map count (who can we model?)
  analyze  build + save + print a StyleProfile for a mapper (or whole library)
  show     print a previously saved profile JSON
"""

from __future__ import annotations

import argparse
import os
import sys

from .corpus import find_songs_dir, iter_maps, survey_mappers
from .features import extract
from .profile import StyleProfile, build_profile


def _resolve(songs: str | None) -> str:
    d = find_songs_dir(songs)
    if not d:
        print("error: could not find an osu! Songs folder; pass --songs DIR",
              file=sys.stderr)
        raise SystemExit(2)
    return d


def main(argv: list[str] | None = None) -> int:
    try:                       # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="mp3-to-osu.learn")
    ap.add_argument("command",
                    choices=["survey", "analyze", "analyze-all", "show",
                             "fetch"])
    ap.add_argument("--songs", help="osu! Songs directory (auto-detected).")
    ap.add_argument("--mapper", help="Creator to model (substring match). "
                    "For 'fetch': comma-separated exact osu! usernames.")
    ap.add_argument("--max-sets", type=int,
                    help="fetch: cap ranked sets scanned per mapper.")
    ap.add_argument("--min-maps", type=int, default=4,
                    help="analyze-all: min maps for a mapper profile.")
    ap.add_argument("--limit", type=int, help="Cap maps scanned.")
    ap.add_argument("--out", default="profiles",
                    help="Directory for profile JSON (default ./profiles).")
    ap.add_argument("--top", type=int, default=30,
                    help="survey: how many mappers to list.")
    a = ap.parse_args(argv)

    if a.command == "show":
        p = a.mapper or ""
        prof = StyleProfile.from_json(p if p.endswith(".json")
                                      else os.path.join(a.out, p + ".json"))
        print(prof.describe())
        return 0

    if a.command == "fetch":
        return _fetch(a)

    songs = _resolve(a.songs)

    if a.command == "survey":
        print(f"Scanning {songs} ...")
        rows = survey_mappers(songs)
        print(f"\n{len(rows)} mappers with standard maps. Top {a.top}:\n")
        for name, cnt in rows[:a.top]:
            print(f"  {cnt:4d}  {name}")
        print("\nPick one with >=3 maps and run:  analyze --mapper \"Name\"")
        return 0

    if a.command == "analyze-all":
        return _analyze_all(songs, a.out, a.min_maps, a.limit)

    # analyze (single mapper or whole library combined)
    feats = []
    label = a.mapper or "ALL"
    print(f"Analyzing maps for {label!r} in {songs} ...")
    for m in iter_maps(songs, creator=a.mapper, limit=a.limit):
        f = extract(m)
        if f:
            feats.append(f)
    if not feats:
        print(f"error: no usable standard maps matched {label!r}",
              file=sys.stderr)
        return 1

    prof = build_profile(label, feats)
    os.makedirs(a.out, exist_ok=True)
    out = os.path.join(a.out, _safe(label) + ".json")
    prof.to_json(out)
    print()
    print(prof.describe())
    print(f"\nSaved profile -> {out}")
    return 0


def _safe(label: str) -> str:
    return "".join(c if c.isalnum() or c in "-_ " else "_"
                   for c in label).strip() or "profile"


def _analyze_all(songs: str, out_dir: str, min_maps: int,
                 limit: int | None) -> int:
    """One pass over the whole library -> a profile per mapper (>= min_maps),
    a combined ALL profile, and an index.json the web UI consumes."""
    from collections import defaultdict

    print(f"Scanning {songs} (one pass) ...")
    by_creator: dict[str, list] = defaultdict(list)
    all_feats: list = []
    for m in iter_maps(songs, limit=limit):
        f = extract(m)
        if not f:
            continue
        by_creator[m.creator or "Unknown"].append(f)
        all_feats.append(f)

    if not all_feats:
        print("error: no usable standard maps found", file=sys.stderr)
        return 1

    os.makedirs(out_dir, exist_ok=True)
    index = []

    combined = build_profile("ALL", all_feats)
    combined.to_json(os.path.join(out_dir, "ALL.json"))
    index.append({"name": "ALL", "file": "ALL.json",
                  "n_maps": combined.n_maps,
                  "slider_ratio": combined.slider_ratio,
                  "bpm_mean": combined.bpm_mean})

    # Case-insensitive merge so "Monstrata"/"monstrata" become one profile.
    merged: dict[str, list] = defaultdict(list)
    canonical: dict[str, str] = {}
    for creator, fs in by_creator.items():
        key = creator.lower()
        canonical.setdefault(key, creator)
        merged[key].extend(fs)

    made = 0
    for key, fs in sorted(merged.items(), key=lambda kv: -len(kv[1])):
        if len(fs) < min_maps:
            continue
        name = canonical[key]
        prof = build_profile(name, fs)
        fn = _safe(name) + ".json"
        prof.to_json(os.path.join(out_dir, fn))
        index.append({"name": name, "file": fn, "n_maps": prof.n_maps,
                      "slider_ratio": prof.slider_ratio,
                      "bpm_mean": prof.bpm_mean})
        made += 1

    import json
    with open(os.path.join(out_dir, "_index.json"), "w",
              encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)

    print(f"\nWrote ALL + {made} mapper profiles "
          f"(>= {min_maps} maps each) to {out_dir}\\")
    print(f"Total maps analysed: {len(all_feats)}")
    top = sorted((i for i in index if i["name"] != "ALL"),
                 key=lambda i: -i["n_maps"])[:12]
    for i in top:
        print(f"  {i['n_maps']:4d}  {i['name']}")
    return 0


def _fetch(a) -> int:
    cid = os.environ.get("OSU_CLIENT_ID")
    sec = os.environ.get("OSU_CLIENT_SECRET")
    if not cid or not sec:
        print("Set OSU_CLIENT_ID and OSU_CLIENT_SECRET first (create the "
              "OAuth app at https://osu.ppy.sh/home/account/edit).\n"
              "PowerShell:\n"
              '  $env:OSU_CLIENT_ID="12345"\n'
              '  $env:OSU_CLIENT_SECRET="xxxxxxxx"',
              file=sys.stderr)
        return 2
    names = [m.strip() for m in (a.mapper or "").split(",") if m.strip()]
    if not names:
        print("error: --mapper \"Monstrata,Sotarks\" required",
              file=sys.stderr)
        return 2

    from .features import extract
    from .fetch_osu import FetchError, fetch_mapper
    from .profile import build_profile

    corpus = os.path.abspath("corpus_fetch")
    local = find_songs_dir(a.songs)            # optional extra data
    os.makedirs(a.out, exist_ok=True)
    for name in names:
        print(f"Fetching {name} via official osu! API…")
        try:
            dest = fetch_mapper(cid, sec, name, corpus, a.max_sets)
        except FetchError as e:
            print(f"  {name}: fetch failed - {e}", file=sys.stderr)
            continue
        feats = []
        for m in iter_maps(dest, creator=name, std_only=True):
            f = extract(m)
            if f:
                feats.append(f)
        if local:                              # augment with local library
            for m in iter_maps(local, creator=name, limit=a.limit):
                f = extract(m)
                if f:
                    feats.append(f)
        if not feats:
            print(f"  {name}: no usable maps", file=sys.stderr)
            continue
        prof = build_profile(name, feats)
        out = os.path.join(a.out, _safe(name) + ".json")
        prof.to_json(out)
        print(f"\n{prof.describe()}\n-> {out} "
              f"(fetched + local = {len(feats)} maps)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
