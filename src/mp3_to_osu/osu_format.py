"""Serialize placed objects into a valid osu! (.osu) file, format v14."""

from __future__ import annotations

from .patterns import Placed
from .rhythm import SLIDER, SPINNER, Difficulty, Timeline

# Slider velocity. Bezier sliders carry their true integrated arc length as the
# `length` field, so visual end and timing end always agree.
SLIDER_MULTIPLIER = 1.4
SV = 1.0
DEFAULT_VOLUME = 70


def _general(audio_filename: str, preview_ms: float) -> str:
    return (
        "[General]\n"
        f"AudioFilename: {audio_filename}\n"
        "AudioLeadIn: 0\n"
        f"PreviewTime: {int(preview_ms)}\n"
        "Countdown: 0\n"
        "SampleSet: Soft\n"
        "StackLeniency: 0.5\n"
        "Mode: 0\n"
        "LetterboxInBreaks: 0\n"
        "WidescreenStoryboard: 1\n"
    )


def _metadata(artist: str, title: str, creator: str, version: str) -> str:
    return (
        "[Metadata]\n"
        f"Title:{title}\n"
        f"TitleUnicode:{title}\n"
        f"Artist:{artist}\n"
        f"ArtistUnicode:{artist}\n"
        f"Creator:{creator}\n"
        f"Version:{version}\n"
        "Source:\n"
        "Tags:mp3-to-osu auto-generated\n"
        "BeatmapID:0\n"
        "BeatmapSetID:-1\n"
    )


def _difficulty(d: Difficulty) -> str:
    return (
        "[Difficulty]\n"
        f"HPDrainRate:{d.hp}\n"
        f"CircleSize:{d.cs}\n"
        f"OverallDifficulty:{d.od}\n"
        f"ApproachRate:{d.ar}\n"
        f"SliderMultiplier:{SLIDER_MULTIPLIER}\n"
        "SliderTickRate:1\n"
    )


def _events(breaks: list[tuple[float, float]], bg: str | None) -> str:
    lines = ["[Events]"]
    if bg:
        lines.append(f'0,0,"{bg}",0,0')
    for start, end in breaks:
        lines.append(f"2,{int(start)},{int(end)}")
    return "\n".join(lines) + "\n"


def _timing_points(offset_ms: float, beat_len: float) -> str:
    # One uninherited (red) line: time, beatLength, meter, sampleSet,
    # sampleIndex, volume, uninherited=1, effects=0.
    return (
        "[TimingPoints]\n"
        f"{int(round(offset_ms))},{beat_len:.6f},4,2,0,{DEFAULT_VOLUME},1,0\n"
    )


def _hit_objects(placed: list[Placed], beat_len: float) -> str:
    lines = ["[HitObjects]"]
    for p in placed:
        x = int(round(p.x))
        y = int(round(p.y))
        t = int(round(p.note.time_ms))
        nc = 4 if p.note.new_combo else 0
        hs = p.note.hitsound

        if p.note.kind == SPINNER:
            end = int(round(p.note.time_ms + p.note.duration_ms))
            # x,y ignored for spinners; convention is centre.
            lines.append(f"256,192,{t},{8 | nc},{hs},{end},0:0:0:0:")
            continue

        if p.note.kind == SLIDER and p.slider_length >= 20:
            cx = int(round(p.ctrl_x))
            cy = int(round(p.ctrl_y))
            ex = int(round(p.end_x))
            ey = int(round(p.end_y))
            obj_type = 2 | nc
            if getattr(p, "curve", "B") == "L":
                # Linear slider: a single end anchor, length = chord so the
                # visual end and timing end coincide exactly.
                length = ((ex - x) ** 2 + (ey - y) ** 2) ** 0.5
                path = f"L|{ex}:{ey}"
            else:
                # Quadratic bezier: one control point then the end anchor;
                # length = integrated arc length so timing == visuals.
                length = p.slider_length
                path = f"B|{cx}:{cy}|{ex}:{ey}"
            if length < 20:
                lines.append(f"{x},{y},{t},{1 | nc},{hs},0:0:0:0:")
                continue
            # edgeSounds: head uses hs, tail normal. edgeSets default.
            lines.append(
                f"{x},{y},{t},{obj_type},{hs},{path},1,"
                f"{length:.3f},{hs}|0,0:0|0:0,0:0:0:0:"
            )
        else:
            lines.append(f"{x},{y},{t},{1 | nc},{hs},0:0:0:0:")
    return "\n".join(lines) + "\n"


def serialize(
    placed: list[Placed],
    timeline: Timeline,
    *,
    audio_filename: str,
    artist: str,
    title: str,
    creator: str,
    difficulty: Difficulty,
    background: str | None = None,
) -> str:
    preview = placed[len(placed) // 3].note.time_ms if placed else 0.0
    parts = [
        "osu file format v14\n",
        _general(audio_filename, preview),
        "[Editor]\nDistanceSpacing: 1.0\nBeatDivisor: 4\nGridSize: 8\n",
        _metadata(artist, title, creator, difficulty.name),
        _difficulty(difficulty),
        _events(timeline.breaks, background),
        _timing_points(timeline.offset_ms, timeline.beat_length_ms),
        _hit_objects(placed, timeline.beat_length_ms),
    ]
    return "\n".join(parts)
