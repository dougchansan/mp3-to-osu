"""End-to-end orchestration: audio (file or URL) in, .osz mapset out."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import audio, osu_format, package, patterns
from .osu_format import SLIDER_MULTIPLIER, SV
from .rhythm import PRESETS, Difficulty, build_timeline


@dataclass
class Result:
    osz_path: str
    bpm: float
    audio_path: str
    difficulties: list[tuple[str, int]] = field(default_factory=list)  # name,objs


def _guess_meta(path: str) -> tuple[str, str]:
    stem = os.path.splitext(os.path.basename(path))[0]
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "Unknown Artist", stem.strip()


def _build_one(analysis: audio.AudioAnalysis, diff: Difficulty,
               *, audio_filename: str, artist: str, title: str,
               creator: str, background: str | None,
               style=None) -> tuple[str, int] | None:
    if style is not None:                 # difficulty knobs follow the profile
        import dataclasses
        diff = dataclasses.replace(diff, cs=style.cs, ar=style.ar,
                                   od=style.od, hp=style.hp)
    timeline = build_timeline(analysis, diff, style=style)
    placed = patterns.place(timeline, diff.jump_scale, diff.stream_min,
                            SLIDER_MULTIPLIER, SV, style=style)
    if not placed:
        return None
    osu_text = osu_format.serialize(
        placed, timeline,
        audio_filename=audio_filename, artist=artist, title=title,
        creator=creator, difficulty=diff, background=background,
    )
    return osu_text, len(placed)


def generate(
    audio_path: str | None,
    out_dir: str,
    *,
    difficulty: str = "hard",
    spread: bool = False,
    url: str | None = None,
    artist: str | None = None,
    title: str | None = None,
    creator: str = "dougchansan",
    background: str | None = None,
    style=None,
) -> Result:
    if url:
        from .fetch import fetch_audio
        audio_path = fetch_audio(url, out_dir)
    if not audio_path or not os.path.isfile(audio_path):
        raise FileNotFoundError(audio_path or "<no audio>")

    g_artist, g_title = _guess_meta(audio_path)
    artist = artist or g_artist
    title = title or g_title

    analysis = audio.analyze(audio_path)

    if spread:
        order = ["easy", "normal", "hard", "insane", "expert"]
    else:
        order = [difficulty.lower() if difficulty.lower() in PRESETS else "hard"]

    diffs: list[tuple[str, str]] = []
    summary: list[tuple[str, int]] = []
    if style is not None and not spread:
        creator = creator if creator != "dougchansan" else \
            f"dougchansan (style: {style.name})"

    for key in order:
        d = PRESETS[key]
        built = _build_one(
            analysis, d, audio_filename=os.path.basename(audio_path),
            artist=artist, title=title, creator=creator,
            background=background, style=style,
        )
        if built:
            diffs.append((d.name, built[0]))
            summary.append((d.name, built[1]))

    if not diffs:
        raise RuntimeError(
            "No hit objects produced - track may be too quiet. Try a lower "
            "difficulty."
        )

    osz = package.build_osz(diffs, audio_path, out_dir,
                            artist=artist, title=title)
    return Result(osz_path=osz, bpm=analysis.bpm,
                  audio_path=audio_path, difficulties=summary)
