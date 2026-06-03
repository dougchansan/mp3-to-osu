"""Walk an osu! Songs library and yield parsed standard-mode beatmaps."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterator

from .parse import ParsedMap, parse_osu

DEFAULT_SONGS_DIRS = (
    r"F:\osu\Songs",
    os.path.expandvars(r"%LOCALAPPDATA%\osu!\Songs"),
)


def find_songs_dir(explicit: str | None = None) -> str | None:
    for cand in ([explicit] if explicit else []) + list(DEFAULT_SONGS_DIRS):
        if cand and os.path.isdir(cand):
            return cand
    return None


def iter_osu_files(songs_dir: str) -> Iterator[str]:
    for root, _dirs, files in os.walk(songs_dir):
        for fn in files:
            if fn.lower().endswith(".osu"):
                yield os.path.join(root, fn)


def iter_maps(
    songs_dir: str,
    *,
    creator: str | None = None,
    std_only: bool = True,
    min_objects: int = 30,
    limit: int | None = None,
) -> Iterator[ParsedMap]:
    """Yield ParsedMaps, optionally filtered to one mapper (case-insensitive,
    substring) and to osu! standard with a minimum object count."""
    want = creator.lower().strip() if creator else None
    n = 0
    for path in iter_osu_files(songs_dir):
        m = parse_osu(path)
        if m is None:
            continue
        if std_only and m.mode != 0:
            continue
        if len(m.objects) < min_objects:
            continue
        if want and want not in m.creator.lower():
            continue
        yield m
        n += 1
        if limit and n >= limit:
            return


def survey_mappers(songs_dir: str, std_only: bool = True
                    ) -> list[tuple[str, int]]:
    """Return (creator, map_count) sorted by count desc - so the user can see
    which mappers have enough data to model."""
    c: Counter[str] = Counter()
    for path in iter_osu_files(songs_dir):
        m = parse_osu(path)
        if m is None or (std_only and m.mode != 0):
            continue
        if m.creator:
            c[m.creator] += 1
    return c.most_common()
