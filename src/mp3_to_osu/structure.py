"""Song structure profiler.

Light, dependency-free (librosa only) analysis of *how the song moves* so the
mapper can react like a human does: calmer sliders in intros/breaks, denser
jumps and finer subdivisions in drops/choruses, pattern + combo changes on
section boundaries.

It does NOT separate stems - no true vocal/adlib isolation. It models:
  * intensity   - loudness + percussive drive (beat-synced, 0..1)
  * density     - onset rate (how busy the rhythm is, 0..1)
  * sections    - structural segments via beat-synced timbre clustering
  * max_subdiv  - finest rhythm allowed there, auto-gated for playability
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

# Never let a fully-filled 1/d stream exceed this many hits/sec (≈ 1/4 @ 240
# BPM, around the top of comfortably playable for generated maps).
MAX_NOTES_PER_SEC = 15.0
SUBDIVS = (1, 2, 4, 8, 16)


@dataclass
class Section:
    start_ms: float
    end_ms: float
    kind: str          # intro / build / drop / verse / chorus / break / outro
    intensity: float   # 0..1 mean loudness+drive
    density: float     # 0..1 mean onset rate
    max_subdiv: int    # 1,2,4,8,16 - finest grid permitted here


@dataclass
class Structure:
    sections: list[Section]
    beat_times_ms: np.ndarray
    beat_intensity: np.ndarray   # 0..1, per beat
    beat_density: np.ndarray     # 0..1, per beat

    def section_at(self, ms: float) -> Section:
        for s in self.sections:
            if s.start_ms <= ms < s.end_ms:
                return s
        return self.sections[-1] if self.sections else Section(
            0, 0, "verse", 0.5, 0.4, 4)


def _norm(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    lo, hi = float(np.min(x)), float(np.max(x))
    return (x - lo) / (hi - lo) if hi - lo > 1e-9 else np.zeros_like(x)


def _subdiv_for(intensity: float, density: float, bpm: float) -> int:
    """Pick the finest subdivision the section earns, then clamp so a packed
    stream there stays under MAX_NOTES_PER_SEC (auto playability gate)."""
    # Almost any audible musical passage carries a 1/4 melody; reserve 1/2
    # only for genuinely sparse/quiet parts. Finer than 1/4 needs real
    # intensity. The onset-strength gate downstream still decides whether a
    # given 1/4 tick actually becomes a note, so this is just the ceiling.
    want = 2
    if intensity > 0.20:
        want = 4
    if intensity > 0.55 or density > 0.45:
        want = 8
    if intensity > 0.78 and density > 0.60:
        want = 16
    bps = bpm / 60.0
    cap = 1
    for d in SUBDIVS:
        if d * bps <= MAX_NOTES_PER_SEC:
            cap = d
    return min(want, max(2, cap))


def analyze_structure(
    y: np.ndarray, sr: int, beat_frames: np.ndarray,
    onset_env: np.ndarray, bpm: float, duration_ms: float,
) -> Structure:
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    beat_ms = beat_times * 1000.0
    n_beats = len(beat_frames)
    if n_beats < 4:
        return Structure(
            [Section(0.0, duration_ms, "verse", 0.5, 0.4,
                     _subdiv_for(0.5, 0.4, bpm))],
            beat_ms, np.full(max(1, n_beats), 0.5),
            np.full(max(1, n_beats), 0.4))

    # All spectral features at 22 kHz: a full-rate HPSS here was ~19 s and a
    # dominant cost; at half rate it's a few seconds and beat-synced stats
    # are unaffected. Beat frames are remapped to the 22 kHz / hop-512 grid.
    HOP = 512
    sr2 = 22050
    y2 = librosa.resample(y, orig_sr=sr, target_sr=sr2) if sr != sr2 else y
    bf2 = np.clip(np.round(beat_times * sr2 / HOP).astype(int),
                  0, max(0, len(y2) // HOP))

    y_perc = librosa.effects.percussive(y2, margin=3.0)
    rms = librosa.feature.rms(y=y2, hop_length=HOP)[0]
    prms = librosa.feature.rms(y=y_perc, hop_length=HOP)[0]
    cent = librosa.feature.spectral_centroid(
        y=y2, sr=sr2, hop_length=HOP)[0]
    oenv = librosa.onset.onset_strength(y=y2, sr=sr2, hop_length=HOP)

    b_rms = librosa.util.sync(rms, bf2, aggregate=np.mean)
    b_prms = librosa.util.sync(prms, bf2, aggregate=np.mean)
    b_cent = librosa.util.sync(cent, bf2, aggregate=np.mean)
    b_ons = librosa.util.sync(oenv, bf2, aggregate=np.mean)
    cut = min(n_beats, b_rms.shape[-1], b_ons.shape[-1])
    b_rms, b_prms = b_rms[:cut], b_prms[:cut]
    b_cent, b_ons = b_cent[:cut], b_ons[:cut]
    beat_ms = beat_ms[:cut]

    intensity = _norm(0.6 * _norm(b_rms) + 0.4 * _norm(b_prms))
    density = _norm(b_ons)

    # Structural segmentation: cluster beat-synced timbre (MFCC + chroma).
    mfcc = librosa.feature.mfcc(y=y2, sr=sr2, n_mfcc=13, hop_length=HOP)
    chroma = librosa.feature.chroma_cqt(y=y2, sr=sr2, hop_length=HOP)
    feat = np.vstack([
        librosa.util.sync(mfcc, bf2, aggregate=np.mean)[:, :cut],
        librosa.util.sync(chroma, bf2, aggregate=np.mean)[:, :cut],
    ])
    k = int(np.clip(round(duration_ms / 1000.0 / 18.0), 4, 10))
    try:
        bounds = librosa.segment.agglomerative(feat, k)
        bounds = sorted(set([0, *bounds.tolist(), cut]))
    except Exception:
        step = max(1, cut // k)
        bounds = list(range(0, cut, step)) + [cut]

    glob = float(np.mean(intensity)) if intensity.size else 0.5
    sections: list[Section] = []
    segs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)
            if bounds[i + 1] > bounds[i]]
    for si, (a, b) in enumerate(segs):
        inten = float(np.mean(intensity[a:b]))
        dens = float(np.mean(density[a:b]))
        start = float(beat_ms[a])
        end = float(beat_ms[b]) if b < len(beat_ms) else duration_ms

        prev_i = (float(np.mean(intensity[segs[si - 1][0]:segs[si - 1][1]]))
                  if si > 0 else inten)
        first, last = si == 0, si == len(segs) - 1
        if inten >= glob * 1.18 and inten > prev_i + 0.05:
            kind = "drop"
        elif inten >= glob * 1.08:
            kind = "chorus"
        elif inten > prev_i + 0.08 and not last:
            kind = "build"
        elif inten < glob * 0.6:
            kind = "intro" if first else ("outro" if last else "break")
        else:
            kind = "verse"

        sections.append(Section(
            start_ms=start, end_ms=end, kind=kind,
            intensity=round(inten, 3), density=round(dens, 3),
            max_subdiv=_subdiv_for(inten, dens, bpm)))

    if sections:
        sections[0].start_ms = 0.0
        sections[-1].end_ms = max(sections[-1].end_ms, duration_ms)
    return Structure(sections, beat_ms, intensity, density)
