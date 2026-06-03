"""Approximate lyric-line timing + combo influence.

Shazam gives lyric *lines with no timestamps*. We spread the lines across the
song's vocal-active sections (non intro/break/outro, decent intensity) and snap
each line start to a nearby strong onset / downbeat. This is deliberately
coarse - line-level and heuristic - so it nudges combos/emphasis, it does not
place individual notes.
"""

from __future__ import annotations

import numpy as np

VOCAL_KINDS = ("build", "verse", "chorus", "drop")


def align_lines(lines: list[str], structure, onset_times_ms: np.ndarray,
                duration_ms: float, beat_len: float) -> list[dict]:
    """Return [{'t': ms, 'text': str}] - one entry per non-empty line."""
    lines = [ln.strip() for ln in lines if ln and ln.strip()]
    if not lines:
        return []

    # Vocal-active span = union of sections that usually carry vocals.
    spans = [(s.start_ms, s.end_ms) for s in structure.sections
             if s.kind in VOCAL_KINDS and s.intensity > 0.30]
    if not spans:
        spans = [(duration_ms * 0.1, duration_ms * 0.92)]

    onsets = (np.asarray(onset_times_ms)
              if onset_times_ms is not None else np.array([]))

    def snap(t: float) -> float:
        if onsets.size:
            j = int(np.argmin(np.abs(onsets - t)))
            if abs(onsets[j] - t) <= beat_len:
                return float(onsets[j])
        return t

    # Lay every line out evenly across the concatenated vocal spans, then
    # snap each to the nearest onset within a beat.
    total = sum(b - a for a, b in spans) or 1.0
    n = len(lines)
    out: list[dict] = []
    for i, ln in enumerate(lines):
        pos = (i + 0.5) / n * total       # position along concatenated spans
        run, placed = 0.0, None
        for a, b in spans:
            seg = b - a
            if pos <= run + seg:
                placed = a + (pos - run)
                break
            run += seg
        if placed is None:
            placed = spans[-1][1]
        out.append({"t": round(snap(placed), 1), "text": ln})
    out.sort(key=lambda d: d["t"])
    return out


def apply_lyric_combos(notes, lyric_times_ms: list[float],
                       beat_len: float) -> int:
    """Force a new combo on the note nearest each lyric-line start and give
    it a small emphasis. Returns how many lines were anchored."""
    if not notes or not lyric_times_ms:
        return 0
    times = [n.time_ms for n in notes]
    anchored = 0
    for lt in lyric_times_ms:
        lo, hi = 0, len(times) - 1
        while lo < hi:                       # binary search nearest
            mid = (lo + hi) // 2
            if times[mid] < lt:
                lo = mid + 1
            else:
                hi = mid
        cands = [c for c in (lo - 1, lo, lo + 1) if 0 <= c < len(notes)]
        best = min(cands, key=lambda c: abs(times[c] - lt))
        if abs(times[best] - lt) <= beat_len:
            notes[best].new_combo = True
            notes[best].strength = min(1.0, notes[best].strength + 0.15)
            anchored += 1
    return anchored
