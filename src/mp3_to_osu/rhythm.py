"""Turn audio analysis into a quantised, typed note timeline.

Design: the beat grid is primary, onsets augment it. Every audible beat gets a
note; subdivisions are added only where an onset actually fires. This mirrors
how hand-made osu! maps work and stays robust on sparse / ambient tracks where
pure onset detection would collapse.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import AudioAnalysis


@dataclass
class Difficulty:
    name: str
    cs: float          # circle size
    ar: float          # approach rate
    od: float          # overall difficulty
    hp: float          # hp drain
    divisor: int       # beat subdivision (1 = 1/1, 2 = 1/2, 4 = 1/4)
    onset_floor: float # min onset strength to add an off-beat subdivision
    jump_scale: float  # distance-spacing multiplier (bigger = harder)
    stream_min: int    # consecutive 1/4 notes before it reads as a stream


PRESETS: dict[str, Difficulty] = {
    "easy":   Difficulty("Easy",   3.0, 5.0, 3.0, 3.0, 1, 0.99, 0.55, 99),
    "normal": Difficulty("Normal", 3.5, 6.5, 5.0, 4.0, 2, 0.45, 0.85, 99),
    "hard":   Difficulty("Hard",   4.0, 8.0, 7.0, 5.0, 2, 0.28, 1.10, 5),
    "insane": Difficulty("Insane", 4.0, 9.0, 8.0, 6.0, 4, 0.22, 1.30, 4),
    "expert": Difficulty("Expert", 4.2, 9.4, 8.6, 6.0, 4, 0.16, 1.45, 3),
}

CIRCLE = "circle"
SLIDER = "slider"
SPINNER = "spinner"

# osu! hit-sound bit flags.
HS_NORMAL = 0
HS_WHISTLE = 2
HS_FINISH = 8
HS_CLAP = 32

# Beats whose energy is below this (normalised) count as silence -> a break.
SILENCE = 0.06


@dataclass
class Note:
    time_ms: float
    kind: str
    strength: float
    new_combo: bool = False
    duration_ms: float = 0.0   # sliders & spinners
    hitsound: int = HS_NORMAL
    curve: str = "B"           # slider curve type: L / P / B
    section: str = "verse"     # song-structure section this note lives in
    seg_intensity: float = 0.5  # 0..1 section intensity (drives patterns)


@dataclass
class Timeline:
    notes: list[Note]
    breaks: list[tuple[float, float]]
    beat_length_ms: float
    offset_ms: float


def build_timeline(a: AudioAnalysis, diff: Difficulty,
                   style=None) -> Timeline:
    beat_len = 60000.0 / a.bpm    # corrected tempo (median of real beats)

    # Fine grid built by INTERPOLATING the real librosa-tracked beats: each
    # beat interval is split into 1/16ths using that interval's own length, so
    # the grid follows the actual pulse (and any tempo drift) instead of an
    # idealised constant offset+k*beatlen. This is what makes the hits land
    # on-beat for the whole song.
    FINE = 16

    def lvl_of(j: int) -> int:
        for d in (1, 2, 4, 8, 16):
            if j % (FINE // d) == 0:
                return d
        return 16

    B = a.beat_times_ms
    times: list[float] = []
    lvls: list[int] = []
    bis: list[int] = []
    if B.size >= 2:
        iv = np.diff(B)
        med = float(np.median(iv))
        good = iv[(iv > med * 0.55) & (iv < med * 1.8)]
        per = float(np.median(good)) if good.size else med
        if per <= 1e-3:
            per = beat_len
        for i in range(B.size - 1):
            b0 = float(B[i])
            span = float(B[i + 1]) - b0
            if span <= 1e-3 or span > per * 2.2:   # gap/missed beat -> use per
                span = per
            for j in range(FINE):
                times.append(b0 + span * (j / FINE))
                lvls.append(lvl_of(j))
                bis.append(i)
        last = float(B[-1])
        bi = int(B.size - 1)
        while last < a.duration_ms:                 # extrapolate the tail
            for j in range(FINE):
                t = last + per * (j / FINE)
                if t > a.duration_ms:
                    break
                times.append(t)
                lvls.append(lvl_of(j))
                bis.append(bi)
            last += per
            bi += 1
    else:                                           # rare: no usable beats
        step = beat_len / FINE
        n = int(max(0, (a.duration_ms - a.offset_ms) / step)) + 1
        for i in range(n):
            times.append(a.offset_ms + i * step)
            lvls.append(lvl_of(i % FINE))
            bis.append(i // FINE)

    if not times:
        return Timeline([], [], beat_len, a.offset_ms)
    tick_times = np.asarray(times)
    n_ticks = len(times)

    # Per-band onsets drive the strength: each band contributes a weighted
    # vote to its nearest grid tick, and ticks where several instruments hit
    # together (kick+bass+melody) get an accent boost -> those become the
    # natural new-combos / jumps. Far more complete than the old single
    # detector (catches the bass rhythm it used to miss).
    def _snap(arr):
        pos = np.searchsorted(tick_times, arr)
        out = np.empty(arr.size, dtype=int)
        for k in range(arr.size):
            p = int(pos[k])
            ot = float(arr[k])
            c = p if p < n_ticks else n_ticks - 1
            if p - 1 >= 0 and abs(tick_times[p - 1] - ot) < \
                    abs(tick_times[c] - ot):
                c = p - 1
            out[k] = c
        return out

    # LEAD = the predominant-melody line; weighted highest so the map
    # foregrounds the main tune, not just the drums/bass.
    BAND_W = {"SUB": 0.5, "BASS": 1.0, "LOWMID": 0.9,
              "MELODY": 1.0, "AIR": 0.4, "LEAD": 1.6}
    tick_strength = np.zeros(n_ticks)
    bo = getattr(a, "band_onsets", None) or {}
    if any(v is not None and v.size for v in bo.values()):
        acc = np.zeros(n_ticks)
        ncontent = np.zeros(n_ticks)        # distinct rhythmic bands per tick
        for name, arr in bo.items():
            if arr is None or arr.size == 0:
                continue
            w = BAND_W.get(name, 0.6)
            idx = _snap(np.asarray(arr, dtype=float))
            np.add.at(acc, idx, w)
            if name in ("BASS", "LOWMID", "MELODY", "LEAD"):
                hit = np.zeros(n_ticks)
                hit[idx] = 1.0
                ncontent += hit
        acc *= 1.0 + 0.25 * np.maximum(0.0, ncontent - 1.0)
        pos_vals = acc[acc > 0]
        hi = float(np.percentile(pos_vals, 95)) if pos_vals.size else 1.0
        tick_strength = np.clip(acc / (hi if hi > 1e-9 else 1.0), 0.0, 1.0)
    elif a.onset_times_ms.size:             # fallback: old merged detector
        idx = _snap(a.onset_times_ms)
        for k in range(a.onset_times_ms.size):
            c = int(idx[k])
            st = float(a.onset_strength[k])
            if st > tick_strength[c]:
                tick_strength[c] = st

    if a.beat_energy.size and a.beat_times_ms.size:
        tick_energy = np.interp(tick_times, a.beat_times_ms, a.beat_energy,
                                left=a.beat_energy[0], right=a.beat_energy[-1])
    else:
        tick_energy = np.ones(n_ticks)

    # Difficulty acts as an upper ceiling on top of the section's gate.
    diff_ceiling = {1: 2, 2: 4, 4: 8}.get(diff.divisor, 8)
    struct = a.structure
    # Groove lock (0..1): how hard to enforce the song's MAIN pulse. Higher =
    # off-beat subdivisions need much stronger onsets to survive, so the map
    # stops catching ghost transients / reverb tails between the real hits.
    lock = float(getattr(style, "rhythm_lock", 0.0) or 0.0) if style else 0.0
    # Optional per-section-kind lock overrides ({"verse": 0.7, ...}). A listed
    # kind uses its value verbatim; unlisted kinds fall back to the global lock.
    lock_sec = (getattr(style, "rhythm_lock_sections", None) or {}) \
        if style else {}

    notes: list[Note] = []
    raw: list[float] = []
    metric: list[int] = []
    sect_of: list = []
    started = False
    for i in range(n_ticks):
        t = float(tick_times[i])
        # Stop at the last real musical moment, not the padded file end -
        # otherwise notes get mapped into trailing silence.
        if t > getattr(a, "music_end_ms", a.duration_ms - 30):
            break
        level = lvls[i]
        is_beat = (level == 1)
        s = float(tick_strength[i])
        e = float(tick_energy[i])

        if e < SILENCE:
            continue
        sec = struct.section_at(t)
        allowed = min(sec.max_subdiv, diff_ceiling)
        if level > allowed:
            continue
        # Section intensity sets how eagerly sub-beat ticks are kept: drops &
        # choruses ride a continuous 1/2 pulse with 1/4 bursts, calmer parts
        # thin out toward the beat. Finer levels always demand stronger onsets.
        sens = min(1.25, max(0.2, 1.25 - 1.05 * sec.intensity))
        hot = sec.kind in ("drop", "chorus")
        # Section-aware groove lock: calm sections (verses/intros/breaks) are
        # where stray off-beat notes read as "off the rhythm", so they lock
        # harder; drops/choruses keep their density. lock=0 -> lk=1 (the
        # original behaviour, unchanged).
        if sec.kind in lock_sec:           # explicit per-section dial
            sec_lock = float(lock_sec[sec.kind])
        else:
            sec_lock = lock * (0.45 if hot else
                               1.15 if sec.kind in ("break", "intro", "outro")
                               else 1.0)
        lk = 1.0 + 2.5 * sec_lock          # off-beat onset-threshold multiplier
        if is_beat:
            keep = True                    # the main pulse is always kept
        elif level == 2:
            # the drop/chorus free pass yields to a strong enough lock
            keep = (hot and sec_lock < 0.5) \
                or s >= diff.onset_floor * 0.6 * sens * lk
        else:
            # Looser so real melodic 1/4 onsets (e.g. a melody entering in a
            # 'build') are actually followed, not gated away.
            keep = s >= diff.onset_floor * (0.8 + 0.4 * (level // 2)) * sens * lk
        if not keep:
            continue
        if not started and e < 0.12:
            continue
        started = True

        beat_idx = bis[i]
        bar_pos = beat_idx % 4 if is_beat else -1
        accent = 1.0 if bar_pos == 0 else (0.6 if bar_pos == 2 else 0.0)
        # Section intensity feeds the rank so drops read as 'big' moments.
        raw.append((0.4 * s + 0.3 * accent + 0.15
                    + 0.15 * sec.intensity) * (0.3 + 0.7 * e))
        metric.append(bar_pos)
        nn = Note(time_ms=float(t), kind=CIRCLE, strength=0.0,
                  section=sec.kind, seg_intensity=sec.intensity)
        notes.append(nn)
        sect_of.append(sec)

    if not notes:
        return Timeline([], [], beat_len, a.offset_ms)

    # Rank-normalise strength across the whole track so every map has real
    # dynamic range (accents, sliders, combos) regardless of how compressed
    # the source audio is.
    order = np.argsort(np.argsort(np.asarray(raw)))
    n = len(notes)
    for k, note in enumerate(notes):
        note.strength = float(order[k]) / max(1, n - 1)

    _assign_sliders(notes, metric, beat_len, diff)
    _assign_combos(notes, metric, diff)
    _assign_hitsounds(notes)
    if style is not None:
        _apply_style(notes, metric, beat_len, style)
    breaks = _find_breaks(notes, beat_len)   # before spinner insertion
    _maybe_intro_spinner(notes, beat_len)
    return Timeline(notes, breaks, beat_len, a.offset_ms)


def _apply_style(notes: list[Note], metric: list[int], beat_len: float,
                 style) -> None:
    """Bend the heuristically-built timeline toward a learned StyleProfile:
    match its slider ratio, combo cadence, slider hold, and curve mix."""
    import random

    rng = random.Random(1337)
    nlen = len(notes)
    if not nlen:
        return

    cw = style.curve_weights
    ckinds = list(cw) or ["B"]
    cwts = [max(0.0, cw.get(k, 0.0)) for k in ckinds] or [1.0]

    # Anchors must stay clickable circles: a bar downbeat or a strong accent.
    def is_anchor(i):
        return metric[i] == 0 or notes[i].strength > 0.72

    # Run-merging sliders: on a dense timeline a slider HOLDS through a run of
    # weak consecutive notes (absorbing them) while accents stay circles - the
    # classic click/hold pulse. This both restores the profile's slider ratio
    # and tames the extra density the per-band onsets introduced.
    want_ms = max(beat_len * 0.5, style.slider_dur_beats * beat_len)
    p_slide = min(0.92, max(0.12, style.slider_ratio * 1.6))
    p_slide_after = p_slide * 0.5      # allow slider->slider, less often

    out: list[Note] = []
    i = 0
    just_slid = False
    while i < nlen:
        n = notes[i]
        if n.kind == SPINNER:
            out.append(n)
            i += 1
            just_slid = False
            continue
        make = (not is_anchor(i)
                and rng.random() < (p_slide_after if just_slid else p_slide))
        if make:
            j = i + 1
            while (j < nlen and notes[j].kind != SPINNER
                   and not is_anchor(j)
                   and (notes[j].time_ms - n.time_ms) <= want_ms + 1e-6):
                j += 1
            nxt = notes[j].time_ms if j < nlen else (n.time_ms + want_ms)
            n.kind = SLIDER
            n.duration_ms = _slider_hold(nxt - n.time_ms, beat_len, style)
            n.curve = rng.choices(ckinds, weights=cwts)[0]
            out.append(n)
            i = j                       # weak notes in the run are absorbed
            just_slid = True
        else:
            if n.kind == SLIDER:        # demote stray pre-marked sliders
                n.kind = CIRCLE
                n.duration_ms = 0.0
            out.append(n)
            i += 1
            just_slid = False
    notes[:] = out

    # Combos: metric no longer aligns after the merge, so drive purely by
    # cadence + strong accents.
    per = max(2, int(round(style.objects_per_combo)))
    since = 0
    for idx, nn in enumerate(notes):
        if nn.kind == SPINNER:
            nn.new_combo = True
            since = 0
            continue
        nn.new_combo = (idx == 0) or (since >= per) \
            or (nn.strength > 0.9 and since >= 2)
        since = 0 if nn.new_combo else since + 1

    _assign_hitsounds(notes)            # re-tag after kinds/runs changed


def _slider_hold(gap_ms: float, beat_len: float, style) -> float:
    """Style slider length, snapped to a clean fraction, always leaving >=0.5
    beat recovery before the next note (the earlier slider-timing fix)."""
    want = style.slider_dur_beats
    room = gap_ms / beat_len - 0.5
    beats = next((L for L in (2.0, 1.5, 1.0, 0.5)
                  if L <= room + 1e-6 and L <= want + 1e-6), None)
    if beats is None:
        beats = 0.5 if room >= 0.5 - 1e-6 else max(0.25, room)
    return max(beat_len * 0.25, beats * beat_len)


def _assign_hitsounds(notes: list[Note]) -> None:
    """Section-aware hitsounds, always landing on real (grid/onset-aligned)
    objects so they stay locked to the beat:

      * drop/chorus  - punchy: finish on accents, clap broadly
      * build/verse  - moderate: clap on accents, whistle on sliders
      * break/intro  - sparse: mostly normal, whistle on sliders
    """
    for n in notes:
        hot = n.section in ("drop", "chorus")
        calm = n.section in ("break", "intro", "outro")
        if hot:
            if n.new_combo or n.strength > 0.78:
                n.hitsound = HS_FINISH
            elif n.strength > 0.45:
                n.hitsound = HS_CLAP
            else:
                n.hitsound = HS_NORMAL
        elif calm:
            n.hitsound = HS_WHISTLE if n.kind == SLIDER else HS_NORMAL
        else:
            if n.new_combo and n.strength > 0.82:
                n.hitsound = HS_FINISH
            elif n.strength > 0.7:
                n.hitsound = HS_CLAP
            elif n.strength > 0.5 and n.kind == SLIDER:
                n.hitsound = HS_WHISTLE
            else:
                n.hitsound = HS_NORMAL


def _maybe_intro_spinner(notes: list[Note], beat_len: float) -> None:
    """If the track has a long empty lead-in, fill it with a spinner."""
    if not notes:
        return
    first = notes[0].time_ms
    start = beat_len * 2.0
    end = first - beat_len
    if end - start >= 1800:   # only worthwhile for a real intro gap
        sp = Note(time_ms=start, kind=SPINNER, strength=1.0,
                  new_combo=True, duration_ms=end - start, hitsound=HS_FINISH)
        notes[0].new_combo = True
        notes.insert(0, sp)


def _assign_sliders(notes: list[Note], metric: list[int], beat_len: float,
                    diff: Difficulty) -> None:
    """Phrasing-based circle/slider flow (the classic mapping idiom):

      * strong accents and downbeats stay clickable circles / jumps,
      * softer weak/off beats become sliders that lead into the next accent,
      * a skipped beat is always held by a slider so there's no dead air,
      * never two sliders back to back -> a readable click-hold-click pulse.
    """
    # A slider must end on the rhythm grid AND leave the player at least this
    # much idle time before the next object, or it feels like you're still
    # holding the slider when the next note arrives.
    RECOVERY_BEATS = 0.5
    LENGTHS = (2.0, 1.5, 1.0, 0.5)   # allowed slider durations, in beats

    prev_was_slider = False
    for i, n in enumerate(notes[:-1]):
        gap = notes[i + 1].time_ms - n.time_ms
        gap_beats = gap / beat_len
        m = metric[i]
        skipped_beat = gap_beats >= 1.5
        weak = (m in (1, 3) or m == -1) and n.strength < 0.55
        strong = (m == 0) or n.strength > 0.78

        # Largest clean fraction that still leaves a recovery gap before the
        # next note. If even a 1/2 slider wouldn't fit, stay a circle.
        room = gap_beats - RECOVERY_BEATS
        beats = next((L for L in LENGTHS if L <= room + 1e-6), None)

        make = (skipped_beat or weak) and not strong \
            and not prev_was_slider and beats is not None
        if make:
            n.kind = SLIDER
            n.duration_ms = beats * beat_len   # ends exactly on the grid
            prev_was_slider = True
        else:
            prev_was_slider = False


def _assign_combos(notes: list[Note], metric: list[int],
                   diff: Difficulty) -> None:
    """New combo on the first object, strong accents, and every bar's
    downbeat - so colour changes line up with the music's phrasing."""
    notes[0].new_combo = True
    since = 0
    for i, n in enumerate(notes):
        since += 1
        if i and n.strength > 0.86 and since >= 3:
            n.new_combo = True
        # Downbeat of a new bar, at a sensible cadence.
        if i and metric[i] == 0 and since >= diff.divisor * 3:
            n.new_combo = True
        if since >= diff.divisor * 8:      # safety cap (~8 beats)
            n.new_combo = True
        if n.new_combo:
            since = 0


def _find_breaks(notes: list[Note], beat_len: float
                 ) -> list[tuple[float, float]]:
    bar = beat_len * 4
    breaks: list[tuple[float, float]] = []
    for i in range(1, len(notes)):
        gap = notes[i].time_ms - notes[i - 1].time_ms
        if gap >= 2 * bar:
            breaks.append((notes[i - 1].time_ms + beat_len,
                           notes[i].time_ms - beat_len))
            notes[i].new_combo = True
    return breaks
