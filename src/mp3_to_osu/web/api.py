"""Generation glue for the web tool: run the pipeline once and return both a
structured object list (for the canvas replay) and a downloadable .osz."""

from __future__ import annotations

import dataclasses
import os

from .. import audio, osu_format, package, patterns
from ..osu_format import SLIDER_MULTIPLIER, SV
from ..rhythm import PRESETS, SLIDER, SPINNER, build_timeline
from ..style import StyleParams


def list_profiles(profiles_dir: str) -> list[dict]:
    """Index of available profiles (from _index.json, else any *.json)."""
    idx = os.path.join(profiles_dir, "_index.json")
    if os.path.isfile(idx):
        import json
        with open(idx, "r", encoding="utf-8") as fh:
            return json.load(fh)
    out = []
    if os.path.isdir(profiles_dir):
        for fn in sorted(os.listdir(profiles_dir)):
            if fn.endswith(".json") and not fn.startswith("_"):
                out.append({"name": fn[:-5], "file": fn})
    return out


def profile_params(profiles_dir: str, name: str | None) -> StyleParams:
    if not name:
        return StyleParams()
    path = name if os.path.isfile(name) else os.path.join(
        profiles_dir, name if name.endswith(".json") else name + ".json")
    if os.path.isfile(path):
        return StyleParams.from_profile_json(path)
    return StyleParams()


def generate(audio_path: str, *, profiles_dir: str, profile: str | None,
             overrides: dict, difficulty: str, out_dir: str,
             lyrics: list[str] | None = None,
             lyrics_drive: bool = False,
             tempo: dict | None = None) -> dict:
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(audio_path)

    style = profile_params(profiles_dir, profile).with_overrides(overrides
                                                                 or {})
    key = difficulty.lower() if difficulty.lower() in PRESETS else "hard"
    diff = dataclasses.replace(PRESETS[key], cs=style.cs, ar=style.ar,
                               od=style.od, hp=style.hp)

    analysis = audio.analyze(audio_path)

    # User-tapped tempo override: replace the detected beat grid with a clean
    # constant grid from their BPM + first-beat offset. Onsets/structure/
    # melody still come from the audio; only the beat skeleton is theirs - so
    # the map is guaranteed on *their* beat.
    if tempo and tempo.get("bpm"):
        import numpy as np
        bpm = max(40.0, min(300.0, float(tempo["bpm"])))
        off = max(0.0, float(tempo.get("offset_ms", analysis.offset_ms)))
        per = 60000.0 / bpm
        n = int(max(1, (analysis.duration_ms - off) / per)) + 1
        new_bt = off + np.arange(n) * per
        old_bt, old_be = analysis.beat_times_ms, analysis.beat_energy
        if old_bt.size and old_be.size:        # keep energy aligned to beats
            analysis.beat_energy = np.interp(
                new_bt, old_bt, old_be,
                left=float(old_be[0]), right=float(old_be[-1]))
        else:
            analysis.beat_energy = np.ones(n)
        analysis.beat_times_ms = new_bt
        analysis.bpm = round(bpm, 3)
        analysis.offset_ms = off

    timeline = build_timeline(analysis, diff, style=style)

    timed_lyrics: list[dict] = []
    if lyrics:
        from .. import lyrics as lyr
        timed_lyrics = lyr.align_lines(
            lyrics, analysis.structure, analysis.onset_times_ms,
            analysis.duration_ms, timeline.beat_length_ms)
        if lyrics_drive and timed_lyrics:
            lyr.apply_lyric_combos(
                timeline.notes, [d["t"] for d in timed_lyrics],
                timeline.beat_length_ms)

    placed = patterns.place(timeline, diff.jump_scale, diff.stream_min,
                            SLIDER_MULTIPLIER, SV, style=style)
    if not placed:
        raise RuntimeError("no objects produced (track too quiet?)")

    stem = os.path.splitext(os.path.basename(audio_path))[0]
    artist, title = (stem.split(" - ", 1) if " - " in stem
                     else ("Unknown", stem))
    osu_text = osu_format.serialize(
        placed, timeline, audio_filename=os.path.basename(audio_path),
        artist=artist, title=title,
        creator=f"dougchansan (style: {style.name})",
        difficulty=diff)
    osz = package.build_osz([(diff.name, osu_text)], audio_path, out_dir,
                            artist=artist, title=title)

    objs = []
    for p in placed:
        n = p.note
        o = {"t": round(n.time_ms, 1), "x": round(p.x, 1),
             "y": round(p.y, 1), "nc": bool(n.new_combo), "hs": n.hitsound}
        if n.kind == SPINNER:
            o["kind"] = "spinner"
            o["end"] = round(n.time_ms + n.duration_ms, 1)
        elif n.kind == SLIDER and p.slider_length >= 20:
            o["kind"] = "slider"
            o["dur"] = round(n.duration_ms, 1)
            o["curve"] = p.curve
            o["cx"] = round(p.ctrl_x, 1)
            o["cy"] = round(p.ctrl_y, 1)
            o["ex"] = round(p.end_x, 1)
            o["ey"] = round(p.end_y, 1)
        else:
            o["kind"] = "circle"
        objs.append(o)

    return {
        "bpm": round(analysis.bpm, 2),
        "beat_ms": round(timeline.beat_length_ms, 3),
        "offset_ms": round(timeline.offset_ms, 1),
        "audio_name": os.path.basename(audio_path),
        "audio_path": os.path.abspath(audio_path),
        "osz": os.path.abspath(osz),
        "osz_name": os.path.basename(osz),
        "cs": diff.cs, "ar": diff.ar, "od": diff.od, "hp": diff.hp,
        "style": style.to_dict(),
        "duration_ms": round(analysis.duration_ms, 1),
        # Real librosa-tracked beats + detected onsets so the ruler shows the
        # actual music pulse (follows tempo drift), not an idealised grid.
        "beat_times": [int(round(t))
                       for t in analysis.beat_times_ms.tolist()],
        "onset_times": [int(round(t))
                        for t in analysis.onset_times_ms.tolist()],
        # Offline per-band note onsets (accurate bass/sub etc.).
        "band_onsets": {k: [int(round(x)) for x in v.tolist()]
                        for k, v in analysis.band_onsets.items()},
        "objects": objs,
        "breaks": [[round(a, 1), round(b, 1)] for a, b in timeline.breaks],
        "sections": [
            {"start": round(s.start_ms, 1), "end": round(s.end_ms, 1),
             "kind": s.kind, "intensity": s.intensity,
             "subdiv": s.max_subdiv}
            for s in analysis.structure.sections
        ],
        "lyrics": timed_lyrics,
    }


def _osu_audio_name(text: str) -> str:
    for line in text.splitlines():
        if line.strip().lower().startswith("audiofilename:"):
            return line.split(":", 1)[1].strip()
    return ""


def _osu_hs_to_blip(h: int) -> int:
    # osu! hitSound bits: 2=whistle 4=finish 8=clap -> our blip kinds.
    if h & 8:
        return 32
    if h & 4:
        return 8
    if h & 2:
        return 2
    return 0


_ANALYSIS_CACHE: dict = {}             # audio_path -> AudioAnalysis


def import_osz(osz_path: str, out_dir: str,
               version: str | None = None) -> dict:
    """Unzip a normal .osz and return the studio map JSON for the chosen
    standard difficulty (default: hardest) PLUS our own audio analysis, so
    the user can validate the rhythm bar against a known map. The list of
    available difficulties is returned so the UI can switch between them;
    audio analysis is cached so switching difficulty is instant.
    """
    import glob
    import zipfile

    from ..learn.parse import parse_osu

    if not os.path.isfile(osz_path):
        raise FileNotFoundError(osz_path)
    name = os.path.splitext(os.path.basename(osz_path))[0]
    work = os.path.join(out_dir, "imported",
                        "".join(c for c in name
                                if c.isalnum() or c in " ._-") or "map")
    os.makedirs(work, exist_ok=True)
    with zipfile.ZipFile(osz_path) as z:
        z.extractall(work)

    diffs = []                                       # all std difficulties
    for fp in glob.glob(os.path.join(work, "**", "*.osu"), recursive=True):
        m = parse_osu(fp)
        if m is None or m.mode != 0:
            continue
        diffs.append(m)
    if not diffs:
        raise RuntimeError("no osu! standard difficulty found in that .osz")
    diffs.sort(key=lambda m: len(m.objects))         # easy -> hard
    best = next((m for m in diffs if m.version == version), None) \
        or diffs[-1]

    with open(best.path, "r", encoding="utf-8-sig",
              errors="ignore") as fh:
        afile = _osu_audio_name(fh.read())
    audio_path = os.path.join(os.path.dirname(best.path), afile)
    if not os.path.isfile(audio_path):                # fallback: any audio
        cand = glob.glob(os.path.join(work, "**", "*.mp3"), recursive=True) \
            + glob.glob(os.path.join(work, "**", "*.ogg"), recursive=True)
        if not cand:
            raise RuntimeError("audio file missing inside the .osz")
        audio_path = cand[0]

    ap = os.path.abspath(audio_path)
    analysis = _ANALYSIS_CACHE.get(ap)
    if analysis is None:
        analysis = audio.analyze(audio_path)
        _ANALYSIS_CACHE[ap] = analysis

    objs = []
    for o in best.objects:
        d = {"t": round(o.time, 1), "x": round(o.x, 1),
             "y": round(o.y, 1), "nc": bool(o.new_combo),
             "hs": _osu_hs_to_blip(o.hitsound), "kind": o.kind}
        if o.kind == "slider":
            d["dur"] = round(o.duration, 1)
            d["curve"] = "L" if o.curve == "L" else "B"
            d["cx"] = round(o.ctrl_x, 1)
            d["cy"] = round(o.ctrl_y, 1)
            d["ex"] = round(o.end_x, 1)
            d["ey"] = round(o.end_y, 1)
        elif o.kind == "spinner":
            d["end"] = round(o.time + o.duration, 1)
        objs.append(d)

    return {
        "bpm": round(analysis.bpm, 2),
        "beat_ms": round(60000.0 / max(1.0, analysis.bpm), 3),
        "offset_ms": round(analysis.offset_ms, 1),
        "audio_name": os.path.basename(audio_path),
        "audio_path": os.path.abspath(audio_path),
        "osz": os.path.abspath(osz_path),
        "osz_name": os.path.basename(osz_path),
        "cs": best.cs, "ar": best.ar, "od": best.od, "hp": best.hp,
        "style": {"name": f"imported: {best.creator} [{best.version}]"},
        "duration_ms": round(analysis.duration_ms, 1),
        "beat_times": [int(round(t))
                       for t in analysis.beat_times_ms.tolist()],
        "onset_times": [int(round(t))
                        for t in analysis.onset_times_ms.tolist()],
        "band_onsets": {k: [int(round(x)) for x in v.tolist()]
                        for k, v in analysis.band_onsets.items()},
        "objects": objs,
        "breaks": [],
        "sections": [
            {"start": round(s.start_ms, 1), "end": round(s.end_ms, 1),
             "kind": s.kind, "intensity": s.intensity,
             "subdiv": s.max_subdiv}
            for s in analysis.structure.sections
        ],
        "lyrics": [],
        "imported": True,
        "version": best.version,
        "difficulties": [
            {"version": m.version, "objects": len(m.objects)}
            for m in diffs
        ],
    }
