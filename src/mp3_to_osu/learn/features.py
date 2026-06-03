"""Extract 'how is this map built' features from a ParsedMap.

All features are scale-invariant where it matters (distances are normalised by
the circle radius implied by CS) so maps of different CS are comparable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .parse import CIRCLE, SLIDER, SPINNER, ParsedMap

# Rhythm gap buckets, in beats (nearest-snap classification).
RHYTHM_BUCKETS = [0.25, 0.333, 0.5, 0.667, 0.75, 1.0, 1.5, 2.0, 4.0]
RHYTHM_LABELS = ["1/4", "1/3", "1/2", "2/3", "3/4", "1/1", "3/2", "2/1", "2+"]


def circle_radius(cs: float) -> float:
    # osu! standard playfield circle radius in osu! pixels.
    return 54.4 - 4.48 * cs


@dataclass
class MapFeatures:
    creator: str
    version: str
    bpm: float
    cs: float
    ar: float
    od: float
    n_objects: int
    circle_ratio: float
    slider_ratio: float
    spinner_ratio: float
    rhythm_hist: dict[str, float]               # normalised gap distribution
    type_transitions: dict[str, float]          # "circle>slider" -> prob
    spacing_slope: float                        # norm-dist per beat of gap
    spacing_intercept: float
    jump_factor: float                          # mean norm spacing, ~1-beat
    mean_turn_deg: float                        # avg |flow turn| angle
    flow_ratio: float                           # frac of soft (<90deg) turns
    objects_per_combo: float
    beats_per_combo: float
    slider_dur_beats_med: float
    curve_freq: dict[str, float]                # L/P/B share
    stream_rate: float                          # frac objects in >=3 1/4 runs
    mean_stream_len: float
    extra: dict = field(default_factory=dict)


def _bucket(gap_beats: float) -> str:
    best, bi = 1e9, 0
    for i, b in enumerate(RHYTHM_BUCKETS):
        d = abs(gap_beats - b)
        if d < best:
            best, bi = d, i
    return RHYTHM_LABELS[bi]


def extract(m: ParsedMap) -> MapFeatures | None:
    objs = m.objects
    n = len(objs)
    if n < 10 or m.bpm <= 0:
        return None
    beat = 60000.0 / m.bpm
    r = max(8.0, circle_radius(m.cs))

    nc = sum(o.kind == CIRCLE for o in objs)
    ns = sum(o.kind == SLIDER for o in objs)
    nsp = sum(o.kind == SPINNER for o in objs)

    rhythm: dict[str, float] = {lbl: 0.0 for lbl in RHYTHM_LABELS}
    trans: dict[str, float] = {}
    xs_dt: list[float] = []
    xs_dist: list[float] = []
    turns: list[float] = []
    soft = 0
    combo_lens: list[int] = []
    cur_combo = 0
    combo_beats: list[float] = []
    combo_start_t = objs[0].time
    slider_durs: list[float] = []
    curves = {"L": 0, "P": 0, "B": 0}
    stream_objs = 0
    stream_runs: list[int] = []
    run = 1

    for i, o in enumerate(objs):
        if o.new_combo and i:
            combo_lens.append(cur_combo)
            combo_beats.append((o.time - combo_start_t) / beat)
            cur_combo = 0
            combo_start_t = o.time
        cur_combo += 1

        if o.kind == SLIDER:
            slider_durs.append(o.duration / beat)
            curves[o.curve if o.curve in curves else "B"] += 1

        if i + 1 < n:
            nxt = objs[i + 1]
            gap_b = (nxt.time - o.time) / beat
            if gap_b > 0:
                rhythm[_bucket(gap_b)] += 1
                trans_key = f"{o.kind}>{nxt.kind}"
                trans[trans_key] = trans.get(trans_key, 0.0) + 1
                if o.kind != SPINNER and nxt.kind != SPINNER:
                    dist = math.hypot(nxt.x - o.x, nxt.y - o.y) / r
                    if 0.05 <= gap_b <= 4.0:
                        xs_dt.append(gap_b)
                        xs_dist.append(dist)
                # stream run tracking (<= ~1/4 beat)
                if 0.0 < gap_b <= 0.30:
                    run += 1
                else:
                    if run >= 3:
                        stream_runs.append(run)
                        stream_objs += run
                    run = 1

        if 0 < i < n - 1:
            a = objs[i - 1]
            b = objs[i + 1]
            if CIRCLE in (a.kind, o.kind) or SLIDER in (a.kind, o.kind):
                v1 = (o.x - a.x, o.y - a.y)
                v2 = (b.x - o.x, b.y - o.y)
                m1 = math.hypot(*v1)
                m2 = math.hypot(*v2)
                if m1 > 1 and m2 > 1:
                    cosv = max(-1.0, min(1.0,
                               (v1[0] * v2[0] + v1[1] * v2[1]) / (m1 * m2)))
                    turn = math.degrees(math.acos(cosv))
                    turns.append(turn)
                    if turn < 90.0:
                        soft += 1
    if run >= 3:
        stream_runs.append(run)
        stream_objs += run
    combo_lens.append(cur_combo)

    tot_rh = sum(rhythm.values()) or 1.0
    rhythm = {k: round(v / tot_rh, 4) for k, v in rhythm.items()}
    tot_tr = sum(trans.values()) or 1.0
    trans = {k: round(v / tot_tr, 4) for k, v in sorted(trans.items())}

    slope, intercept = _lin_fit(xs_dt, xs_dist)
    jump_vals = [d for dt, d in zip(xs_dt, xs_dist) if 0.4 <= dt <= 1.2]
    tot_cv = sum(curves.values()) or 1
    n_turn = len(turns) or 1

    return MapFeatures(
        creator=m.creator, version=m.version, bpm=round(m.bpm, 2),
        cs=m.cs, ar=m.ar, od=m.od, n_objects=n,
        circle_ratio=round(nc / n, 4), slider_ratio=round(ns / n, 4),
        spinner_ratio=round(nsp / n, 4),
        rhythm_hist=rhythm, type_transitions=trans,
        spacing_slope=round(slope, 4), spacing_intercept=round(intercept, 4),
        jump_factor=round(sum(jump_vals) / len(jump_vals), 4)
        if jump_vals else 0.0,
        mean_turn_deg=round(sum(turns) / n_turn, 2),
        flow_ratio=round(soft / n_turn, 4),
        objects_per_combo=round(sum(combo_lens) / len(combo_lens), 2),
        beats_per_combo=round(sum(combo_beats) / len(combo_beats), 2)
        if combo_beats else 0.0,
        slider_dur_beats_med=round(_median(slider_durs), 3),
        curve_freq={k: round(v / tot_cv, 3) for k, v in curves.items()},
        stream_rate=round(stream_objs / n, 4),
        mean_stream_len=round(sum(stream_runs) / len(stream_runs), 2)
        if stream_runs else 0.0,
    )


def _lin_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    k = len(xs)
    if k < 3:
        return 0.0, 0.0
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = k * sxx - sx * sx
    if abs(denom) < 1e-9:
        return 0.0, sy / k
    slope = (k * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / k
    return slope, intercept


def _median(v: list[float]) -> float:
    if not v:
        return 0.0
    s = sorted(v)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0
