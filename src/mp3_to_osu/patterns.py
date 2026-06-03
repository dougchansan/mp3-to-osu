"""Flow-based placement.

Encodes a few Monstrata-flavoured principles rather than imitating a person:

  * Constant-velocity distance spacing  - spacing scales with the time gap, so
    the cursor moves at a steady, readable speed (osu! "distance snap").
  * Momentum-conserving wide angles      - spaced notes alternate left/right with
    wide angles so movement flows back and forth instead of jittering.
  * Smooth curved streams                - dense runs use small drifting angles.
  * Playfield bouncing                    - moves that would leave the screen are
    reflected, keeping flow continuous near the edges.

Sliders are quadratic-bezier arcs whose `length` field is the *actual*
numerically-integrated path length, so the visual end and the timing end always
agree (an invalid map otherwise).
"""

from __future__ import annotations

import math
import random

from .rhythm import SLIDER, SPINNER, Note, Timeline

FIELD_W, FIELD_H = 512.0, 384.0
MARGIN = 50.0
X_MIN, X_MAX = MARGIN, FIELD_W - MARGIN
Y_MIN, Y_MAX = MARGIN, FIELD_H - MARGIN

BASE_SPACING = 130.0
STREAM_SPACING = 60.0

# How far the slider's control point bows off the chord (fraction of chord).
SLIDER_BOW = 0.22


class Placed:
    """A positioned hit object ready for serialisation."""

    __slots__ = ("note", "x", "y", "ctrl_x", "ctrl_y", "end_x", "end_y",
                 "slider_length", "curve")

    def __init__(self, note: Note, x: float, y: float):
        self.note = note
        self.x = x
        self.y = y
        self.ctrl_x = 0.0
        self.ctrl_y = 0.0
        self.end_x = 0.0
        self.end_y = 0.0
        self.slider_length = 0.0   # actual path length, osu! px
        self.curve = "B"           # emitted curve type: L or B


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _bezier_len(p0, p1, p2, steps: int = 24) -> float:
    """Arc length of a quadratic bezier by trapezoidal integration of |B'|."""
    (x0, y0), (x1, y1), (x2, y2) = p0, p1, p2
    total = 0.0
    prev = None
    for i in range(steps + 1):
        t = i / steps
        # B'(t) = 2(1-t)(P1-P0) + 2t(P2-P1)
        dx = 2 * (1 - t) * (x1 - x0) + 2 * t * (x2 - x1)
        dy = 2 * (1 - t) * (y1 - y0) + 2 * t * (y2 - y1)
        speed = math.hypot(dx, dy)
        if prev is not None:
            total += (prev + speed) / 2.0 * (1.0 / steps)
        prev = speed
    return total


def _curved_slider(sx: float, sy: float, angle: float, target_len: float,
                   bow_sign: int, bow_scale: float = 1.0):
    """Build a bezier arc from (sx,sy) along `angle` whose path length is as
    close to `target_len` as the playfield allows. `bow_scale` controls
    curvature (0 ~ straight, ~2 ~ a 'P'-style round arc). Returns
    (ctrl, end, actual_length)."""
    ux, uy = math.cos(angle), math.sin(angle)
    vx, vy = -uy, ux  # left-hand perpendicular

    def geometry(chord: float):
        ex = _clamp(sx + ux * chord, X_MIN, X_MAX)
        ey = _clamp(sy + uy * chord, Y_MIN, Y_MAX)
        bow = bow_sign * SLIDER_BOW * bow_scale * chord
        cx = _clamp((sx + ex) / 2 + vx * bow, X_MIN, X_MAX)
        cy = _clamp((sy + ey) / 2 + vy * bow, Y_MIN, Y_MAX)
        length = _bezier_len((sx, sy), (cx, cy), (ex, ey))
        return (cx, cy), (ex, ey), length

    # Arc length grows monotonically with chord -> binary search the chord.
    lo, hi = 10.0, 230.0
    best = geometry(hi)
    if best[2] > target_len:
        for _ in range(18):
            mid = (lo + hi) / 2
            g = geometry(mid)
            if g[2] < target_len:
                lo = mid
            else:
                hi = mid
                best = g
    return best


def place(timeline: Timeline, jump_scale: float, stream_min: int,
          slider_mult: float, sv: float, style=None) -> list[Placed]:
    notes = timeline.notes
    if not notes:
        return []

    beat_len = timeline.beat_length_ms
    placed: list[Placed] = []
    rng = random.Random(20240518)

    # Style spacing is expressed in circle-radius units; convert to px.
    if style is not None:
        radius = max(8.0, 54.4 - 4.48 * style.cs)
        turn_mu = math.radians(style.mean_turn_deg)

    x, y = FIELD_W / 2.0, FIELD_H / 2.0
    angle = 0.0
    swing = 1

    for i, n in enumerate(notes):
        if n.kind == SPINNER:
            placed.append(Placed(n, FIELD_W / 2.0, FIELD_H / 2.0))
            continue

        if i == 0 or not placed:
            p = Placed(n, x, y)
        else:
            prev = notes[i - 1]
            dt_beats = max(1e-3, (n.time_ms - prev.time_ms) / beat_len)
            is_stream = dt_beats <= (1.0 / 3.0 + 1e-3)

            if style is not None:
                if is_stream:
                    spacing = max(0.55 * radius,
                                  style.spacing_intercept * radius * 0.5)
                    angle += swing * math.radians(16)
                    if i % 7 == 0:
                        swing = -swing
                else:
                    spacing = ((style.spacing_slope * dt_beats
                                + style.spacing_intercept) * radius)
                    spacing = _clamp(spacing, 0.6 * radius, 320.0)
                    soft = rng.random() < style.flow_ratio
                    base = turn_mu * (0.6 if soft else 1.15)
                    jitter = math.radians(rng.uniform(-18, 18))
                    angle = angle + math.pi - swing * (base + jitter)
                    swing = -swing
            else:
                if is_stream:
                    spacing = STREAM_SPACING * jump_scale
                    angle += swing * math.radians(18)
                    if i % 7 == 0:
                        swing = -swing
                else:
                    spacing = min(BASE_SPACING * dt_beats,
                                  240.0) * jump_scale
                    spread = 95.0 + 70.0 * n.strength \
                        + (25.0 if n.new_combo else 0)
                    angle = angle + math.pi - swing * math.radians(spread)
                    swing = -swing

            # React to the song: bigger, more aggressive movement in
            # high-intensity sections (drops/choruses); tighter, calmer
            # spacing in verses/breaks.
            inten = getattr(n, "seg_intensity", 0.5)
            spacing *= 0.75 + 0.85 * inten

            dx, dy = math.cos(angle), math.sin(angle)
            nx, ny = x + dx * spacing, y + dy * spacing
            if not (X_MIN <= nx <= X_MAX and Y_MIN <= ny <= Y_MAX):
                if nx < X_MIN or nx > X_MAX:
                    dx = -dx
                if ny < Y_MIN or ny > Y_MAX:
                    dy = -dy
                angle = math.atan2(dy, dx)
                nx, ny = x + dx * spacing, y + dy * spacing
            x = _clamp(nx, X_MIN, X_MAX)
            y = _clamp(ny, Y_MIN, Y_MAX)
            p = Placed(n, x, y)

        if n.kind == SLIDER and n.duration_ms > 0:
            beats = n.duration_ms / beat_len
            target = beats * slider_mult * 100.0 * sv
            # Curve: L -> straight, P -> rounded arc, B -> gentle bezier.
            ctype = getattr(n, "curve", "B")
            bow = {"L": 0.0, "P": 1.8, "B": 1.0}.get(ctype, 1.0)
            (cx, cy), (ex, ey), actual = _curved_slider(
                p.x, p.y, angle, target, swing, bow)
            if actual >= 20.0:
                p.ctrl_x, p.ctrl_y = cx, cy
                p.end_x, p.end_y = ex, ey
                p.slider_length = actual
                p.curve = "L" if ctype == "L" else "B"
            else:
                n.kind = "circle"  # too short to be a real slider
        placed.append(p)

    return placed
