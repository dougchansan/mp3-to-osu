"""Parse a .osu file into a structured, analysis-ready beatmap.

Handles the v3-v14 format well enough for osu! standard analysis: sections,
difficulty settings, inherited/uninherited timing points, and hit objects with
absolute time, position, type, and (for sliders) a correctly-derived duration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CIRCLE, SLIDER, SPINNER = "circle", "slider", "spinner"


@dataclass
class HitObject:
    time: float          # ms
    x: float
    y: float
    kind: str
    new_combo: bool
    duration: float = 0.0   # ms; sliders & spinners
    slider_len: float = 0.0  # osu! px (sliders)
    curve: str = ""         # L / P / B (sliders)
    hitsound: int = 0       # osu hitSound bitmask
    end_x: float = 0.0      # slider end anchor (for rendering)
    end_y: float = 0.0
    ctrl_x: float = 0.0     # slider first control point
    ctrl_y: float = 0.0
    tail_hs: int = 0        # slider tail edge hitsound


@dataclass
class ParsedMap:
    path: str
    mode: int
    creator: str
    title: str
    version: str          # difficulty name
    cs: float
    ar: float
    od: float
    hp: float
    slider_multiplier: float
    bpm: float            # main (first uninherited) tempo
    objects: list[HitObject] = field(default_factory=list)


def _f(s: str, default: float = 0.0) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def parse_osu(path: str) -> ParsedMap | None:
    """Return a ParsedMap, or None if the file is unreadable / not parseable."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return None

    section = ""
    meta = {"Creator": "", "Title": "", "Version": ""}
    diff = {"CircleSize": 4.0, "ApproachRate": 9.0, "OverallDifficulty": 7.0,
            "HPDrainRate": 5.0, "SliderMultiplier": 1.4}
    mode = 0
    timing: list[tuple[float, float, bool]] = []  # (time, beatLength, uninh)
    raw_objs: list[str] = []

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue

        if section == "General" and line.startswith("Mode:"):
            mode = int(_f(line.split(":", 1)[1]))
        elif section == "Metadata":
            k, _, v = line.partition(":")
            if k in meta:
                meta[k] = v.strip()
        elif section == "Difficulty":
            k, _, v = line.partition(":")
            if k in diff:
                diff[k] = _f(v)
        elif section == "TimingPoints":
            p = line.split(",")
            if len(p) >= 2:
                t, beat = _f(p[0]), _f(p[1])
                uninh = (int(_f(p[6])) == 1) if len(p) >= 7 else (beat > 0)
                timing.append((t, beat, uninh))
        elif section == "HitObjects":
            raw_objs.append(line)

    if not timing or not raw_objs:
        return None

    timing.sort(key=lambda r: r[0])
    main_beat = next((b for _, b, u in timing if u and b > 0), 0.0)
    bpm = 60000.0 / main_beat if main_beat > 0 else 0.0

    def timing_at(t: float) -> tuple[float, float]:
        """Active (beat_length_ms, slider_velocity_multiplier) at time t."""
        beat = main_beat or 500.0
        sv = 1.0
        for pt, val, uninh in timing:
            if pt > t + 1e-3:
                break
            if uninh and val > 0:
                beat = val
                sv = 1.0           # red line resets SV
            elif not uninh and val < 0:
                sv = -100.0 / val  # green line: -100/x => x-times speed
        return beat, sv

    sm = diff["SliderMultiplier"]
    objs: list[HitObject] = []
    for ln in raw_objs:
        p = ln.split(",")
        if len(p) < 4:
            continue
        x, y, t = _f(p[0]), _f(p[1]), _f(p[2])
        typ = int(_f(p[3]))
        nc = bool(typ & 4)
        hs = int(_f(p[4])) if len(p) > 4 else 0

        if typ & 2 and len(p) >= 8:                # slider
            curve = p[5].split("|", 1)[0] if p[5] else "B"
            slides = max(1, int(_f(p[6], 1)))
            length = _f(p[7])
            beat, sv = timing_at(t)
            denom = sm * 100.0 * sv
            dur = (length / denom * beat * slides) if denom > 0 else beat
            # curve points: "B|x:y|x:y|..." -> first ctrl + last anchor
            pts = p[5].split("|")[1:] if "|" in p[5] else []
            cx, cy, ex, ey = x, y, x, y
            if pts:
                a = pts[0].split(":")
                cx, cy = _f(a[0], x), _f(a[1] if len(a) > 1 else y, y)
                b = pts[-1].split(":")
                ex, ey = _f(b[0], x), _f(b[1] if len(b) > 1 else y, y)
            tail_hs = 0
            if len(p) >= 10 and p[9]:              # edgeSounds "h|h|..."
                es = p[9].split("|")
                tail_hs = int(_f(es[-1], 0))
            o = HitObject(t, x, y, SLIDER, nc, dur, length,
                          "L" if curve == "L" else "B", hs)
            o.end_x, o.end_y, o.ctrl_x, o.ctrl_y = ex, ey, cx, cy
            o.tail_hs = tail_hs
            objs.append(o)
        elif typ & 8 and len(p) >= 6:              # spinner
            end = _f(p[5])
            objs.append(HitObject(t, 256, 192, SPINNER, nc,
                                  max(0.0, end - t), hitsound=hs))
        elif typ & 1 or not (typ & (2 | 8)):       # circle (default)
            objs.append(HitObject(t, x, y, CIRCLE, nc, hitsound=hs))

    if not objs:
        return None
    objs.sort(key=lambda o: o.time)
    return ParsedMap(
        path=path, mode=mode, creator=meta["Creator"], title=meta["Title"],
        version=meta["Version"], cs=diff["CircleSize"],
        ar=diff["ApproachRate"], od=diff["OverallDifficulty"],
        hp=diff["HPDrainRate"], slider_multiplier=sm, bpm=bpm, objects=objs,
    )
