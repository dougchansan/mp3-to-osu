"""Audio analysis: tempo, beat grid, onsets, and per-beat energy.

Everything downstream works in milliseconds and on a single mono channel.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from .structure import Structure, analyze_structure

SR = 44100


@dataclass
class AudioAnalysis:
    duration_ms: float
    bpm: float
    # Milliseconds of the first detected beat -> used as the timing-point offset.
    offset_ms: float
    beat_times_ms: np.ndarray      # one entry per detected beat
    onset_times_ms: np.ndarray     # candidate hit times (note onsets)
    onset_strength: np.ndarray     # strength aligned with onset_times_ms, 0..1
    # RMS energy sampled at every beat, normalised 0..1. Drives density/breaks.
    beat_energy: np.ndarray
    # Song structure: sections, intensity/density, adaptive subdivisions.
    structure: Structure
    # Per-instrument-band note onsets (ms), offline-detected per frequency
    # band - far more accurate than realtime browser FFT, esp. for bass/sub.
    band_onsets: dict[str, np.ndarray]
    # Predominant-melody note onsets (ms) from pitch tracking - the "main
    # tune" a listener follows. Empty if no clear melodic line.
    melody_onsets: np.ndarray
    # Last genuinely musical moment (ms) - everything after is trailing
    # silence/padding; nothing should be mapped there.
    music_end_ms: float


# Frequency bands, aligned with the studio's instrument zones.
ONSET_BANDS = {
    "SUB": (20, 60),
    "BASS": (60, 250),
    "LOWMID": (250, 800),
    "MELODY": (800, 4000),
    "AIR": (4000, 16000),
}


def _normalise(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    lo, hi = float(np.min(x)), float(np.max(x))
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return (x - lo) / (hi - lo)


def _pick_peaks(x: np.ndarray, delta: float, wait: int,
                win: int = 4) -> np.ndarray:
    """Local-maxima peak picker (version-stable replacement for
    librosa.util.peak_pick). A frame is a peak if it's the max in ±win,
    exceeds the local mean by `delta`, and is >= `wait` frames after the
    previous peak."""
    n = len(x)
    if n == 0 or delta <= 0:
        return np.array([], dtype=int)
    peaks: list[int] = []
    last = -wait - 1
    for i in range(n):
        a, b = max(0, i - win), min(n, i + win + 1)
        if x[i] < x[a:b].max():
            continue
        if x[i] < x[a:b].mean() + delta:
            continue
        if i - last <= wait:
            if peaks and x[i] > x[peaks[-1]]:
                peaks[-1] = i
                last = i
            continue
        peaks.append(i)
        last = i
    return np.array(peaks, dtype=int)


def _local_normalise(times_ms: np.ndarray, vals: np.ndarray,
                     window_ms: float) -> np.ndarray:
    """Scale each value by the max within ±window of its time, so prominence
    is judged locally. Keeps a quiet section's melody readable."""
    if vals.size == 0:
        return vals
    out = np.zeros_like(vals, dtype=float)
    lo = 0
    hi = 0
    n = len(times_ms)
    for i, t in enumerate(times_ms):
        while lo < n and times_ms[lo] < t - window_ms:
            lo += 1
        if hi < i:
            hi = i
        while hi + 1 < n and times_ms[hi + 1] <= t + window_ms:
            hi += 1
        local_max = float(np.max(vals[lo:hi + 1])) if hi >= lo else 0.0
        out[i] = vals[i] / local_max if local_max > 1e-9 else 0.0
    return np.clip(out, 0.0, 1.0)


def compute_band_onsets(y: np.ndarray, sr: int) -> dict[str, np.ndarray]:
    """Offline per-frequency-band onset detection.

    For each band we take the STFT magnitude restricted to that band, build a
    per-band onset envelope (positive spectral flux, log-compressed so quiet
    bass notes aren't crushed by loud ones), locally normalise it, then peak-
    pick with a low threshold. This catches sub/bass notes a realtime browser
    analyser misses (full resolution, full dynamic range, no smoothing).
    """
    n_fft = 2048
    hop = 512                                  # ~11.6 ms - fast, ample
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    times_ms = librosa.frames_to_time(
        np.arange(S.shape[1]), sr=sr, hop_length=hop) * 1000.0

    out: dict[str, np.ndarray] = {}
    for name, (lo, hi) in ONSET_BANDS.items():
        mask = (freqs >= lo) & (freqs < hi)
        if not mask.any():
            out[name] = np.array([])
            continue
        band = S[mask, :]
        # log-compress so a soft bass note still produces a clear flux peak
        env = np.log1p(band).sum(axis=0)
        flux = np.diff(env, prepend=env[0])
        flux = np.maximum(0.0, flux)
        # adaptive baseline removal (local mean over ~0.4 s)
        w = max(3, int(0.4 * sr / hop))
        kern = np.ones(w) / w
        base = np.convolve(flux, kern, mode="same")
        norm = np.maximum(0.0, flux - base)
        m = float(norm.max())
        if m <= 1e-9:
            out[name] = np.array([])
            continue
        norm /= m
        # low band -> allow faster notes (smaller wait); higher -> a bit more
        wait = 4 if name in ("SUB", "BASS") else 3
        peaks = _pick_peaks(norm, delta=0.05, wait=wait, win=3)
        out[name] = times_ms[peaks] if peaks.size else np.array([])
    return out


def compute_melody_onsets(y: np.ndarray, sr: int) -> np.ndarray:
    """Predominant-melody note onsets via pitch tracking.

    pYIN estimates the dominant fundamental of the harmonic signal over time;
    a melody note begins where the (voiced) pitch jumps >= ~0.7 semitone, or
    where voicing starts. This is the 'main rhythm at the forefront' a human
    hears - not the drums. Best-effort: monophonic-ish leads track cleanly,
    dense polyphony less so. Never raises.
    """
    try:
        sr2 = 22050                            # half-rate: faster, plenty
        y2 = (librosa.resample(y, orig_sr=sr, target_sr=sr2)
              if sr != sr2 else y)
        hop = 512
        # Deterministic YIN (no HMM) on the mix - ~100x faster than pYIN; an
        # extra HPSS pass here isn't worth its cost, YIN locks the lead well.
        f0 = librosa.yin(y2, sr=sr2, fmin=130.0, fmax=1000.0,
                         frame_length=2048, hop_length=hop)
        rms = librosa.feature.rms(
            y=y2, frame_length=2048, hop_length=hop)[0]
    except Exception:
        return np.array([])

    t_ms = librosa.frames_to_time(
        np.arange(len(f0)), sr=sr2, hop_length=hop) * 1000.0
    midi = librosa.hz_to_midi(np.clip(f0, 1.0, None))
    n = min(len(f0), len(rms))
    gate = max(1e-4, float(np.percentile(rms[:n], 55)) * 0.6)

    raw: list[float] = []
    prev = None
    for i in range(n):
        voiced = rms[i] >= gate and np.isfinite(midi[i])
        if not voiced:
            prev = None
            continue
        m = float(midi[i])
        if prev is None or abs(m - prev) >= 0.7:
            raw.append(float(t_ms[i]))
            prev = m
        else:
            prev = 0.6 * prev + 0.4 * m       # glide-track without spamming

    out: list[float] = []
    last = -1e9
    for t in raw:                              # min 90 ms between melody hits
        if t - last >= 90.0:
            out.append(t)
            last = t
    return np.asarray(out)


def analyze(path: str) -> AudioAnalysis:
    """Load `path` and extract the rhythmic skeleton of the track.

    Uses librosa's loader (soundfile/audioread backend) so mp3/ogg/wav all work.
    """
    y, sr = librosa.load(path, sr=SR, mono=True)
    duration_ms = len(y) / sr * 1000.0

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)

    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, trim=False
    )
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    beat_times_ms = beat_times * 1000.0
    offset_ms = float(beat_times_ms[0]) if beat_times_ms.size else 0.0

    # Tempo from the ACTUAL tracked beat spacing (robust median, outliers
    # from missed/doubled beats removed), not the onset-envelope estimate -
    # that estimate was ~3 BPM off here and drifted the whole map off-beat.
    if beat_times_ms.size >= 4:
        iv = np.diff(beat_times_ms)
        med = float(np.median(iv))
        good = iv[(iv > med * 0.55) & (iv < med * 1.8)]
        period = float(np.median(good)) if good.size else med
        bpm = 60000.0 / period if period > 0 else 120.0
    else:
        bpm = float(np.atleast_1d(tempo)[0])
        if not np.isfinite(bpm) or bpm <= 0:
            bpm = 120.0
    # Fold only truly out-of-range tempi into a playable octave.
    while bpm > 230:
        bpm /= 2.0
    while bpm < 65:
        bpm *= 2.0

    # Percussive/energy onsets (drums, attacks).
    perc_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, backtrack=True, units="frames"
    )
    # Melodic onsets: a smooth synth melody has weak transients, so energy
    # onset detection misses its note changes. Chroma (pitch) flux catches
    # them. Computed on a 22 kHz resample - a full-rate HPSS here was ~19 s
    # and the dominant cost of the whole analysis; this is ~1 s and just as
    # good for detecting note changes.
    perc_times = librosa.frames_to_time(perc_frames, sr=sr) * 1000.0
    env_n = onset_env / (onset_env.max() or 1.0)
    perc_str = env_n[np.clip(perc_frames, 0, len(env_n) - 1)]

    y22 = librosa.resample(y, orig_sr=sr, target_sr=22050)
    chroma = librosa.feature.chroma_cqt(y=y22, sr=22050, hop_length=512)
    cflux = np.concatenate(
        [[0.0], np.maximum(0.0, np.diff(chroma, axis=1)).sum(axis=0)])
    mel_frames = _pick_peaks(cflux, delta=0.06 * (cflux.max() or 1.0),
                             wait=3)
    mel_times = librosa.frames_to_time(
        mel_frames, sr=22050, hop_length=512) * 1000.0
    cfx_n = cflux / (cflux.max() or 1.0)
    mel_str = cfx_n[np.clip(mel_frames, 0, len(cfx_n) - 1)]

    # Merge percussive + melodic onsets in the TIME domain (different frame
    # rates), de-duping anything within ~40 ms, keeping the stronger.
    cand = sorted([(float(t), float(s)) for t, s in zip(perc_times, perc_str)]
                  + [(float(t), float(s))
                     for t, s in zip(mel_times, mel_str)])
    m_times: list[float] = []
    m_str: list[float] = []
    for t, s in cand:
        if m_times and t - m_times[-1] <= 40.0:
            if s > m_str[-1]:
                m_str[-1] = s
            continue
        m_times.append(t)
        m_str.append(s)
    onset_times_ms = np.asarray(m_times)
    raw_strength = np.asarray(m_str)
    # Local (windowed) normalisation: judge each onset against its ±2.5 s
    # neighbourhood, not the whole track. Otherwise loud drops crush quiet
    # melodic onsets toward zero and a melody entering mid-song never clears
    # the keep threshold (it'd be invisible relative to the climax).
    onset_strength = _local_normalise(onset_times_ms, raw_strength,
                                      window_ms=2500.0)

    # Per-beat RMS energy: lets the placer ease off in quiet sections and
    # treat near-silent stretches as breaks.
    rms = librosa.feature.rms(y=y)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr) * 1000.0
    if beat_times_ms.size:
        beat_energy = _normalise(np.interp(beat_times_ms, rms_times, rms))
    else:
        beat_energy = np.zeros(0)

    structure = analyze_structure(
        y, sr, beat_frames, onset_env, bpm, duration_ms)

    band_onsets = compute_band_onsets(y, sr)
    melody_onsets = compute_melody_onsets(y, sr)
    band_onsets["LEAD"] = melody_onsets        # carried to ruler/API too

    # Last real musical event: anything later is trailing silence/padding.
    # Use content sources only (SUB/AIR ripple into silence and would defeat
    # the trim). Tight pad so a padded outro isn't mapped.
    last_evt = 0.0
    srcs = [onset_times_ms, beat_times_ms,
            band_onsets.get("BASS"), band_onsets.get("LOWMID"),
            band_onsets.get("MELODY"), band_onsets.get("LEAD")]
    for arr in srcs:
        if arr is not None and len(arr):
            last_evt = max(last_evt, float(np.max(arr)))
    music_end_ms = (min(duration_ms, last_evt + 250.0)
                    if last_evt > 0 else duration_ms)

    return AudioAnalysis(
        duration_ms=duration_ms,
        bpm=round(bpm, 3),
        offset_ms=offset_ms,
        beat_times_ms=beat_times_ms,
        onset_times_ms=onset_times_ms,
        onset_strength=onset_strength,
        beat_energy=beat_energy,
        structure=structure,
        band_onsets=band_onsets,
        melody_onsets=melody_onsets,
        music_end_ms=music_end_ms,
    )
