"""Song identification + lyrics via shazamio (optional, unofficial).

shazamio is a reverse-engineered Shazam client: handy but unofficial and
fragile. Everything here degrades gracefully - if the package, network, or a
match is missing, the rest of the tool keeps working.

Shazam returns lyric *lines without timestamps*. Per-line timing is therefore
synthesised downstream from detected vocal/phrase onsets - it is approximate.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile

_CACHE_DIR = os.path.join(tempfile.gettempdir(), "mp3_to_osu_idcache")


def _cache_key(path: str) -> str:
    try:
        st = os.stat(path)
        sig = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = os.path.abspath(path)
    return hashlib.sha1(sig.encode()).hexdigest()[:16]


def _cached(path: str) -> dict | None:
    fp = os.path.join(_CACHE_DIR, _cache_key(path) + ".json")
    if os.path.isfile(fp):
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _store(path: str, data: dict) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    try:
        with open(os.path.join(_CACHE_DIR, _cache_key(path) + ".json"),
                  "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


def _to_wav_snippet(audio_path: str) -> str:
    """Shazam needs a decodable clip; we have no ffmpeg, so decode with
    librosa and write a temp 16 kHz mono WAV (no ffmpeg needed)."""
    import librosa
    import soundfile as sf

    y, sr = librosa.load(audio_path, sr=16000, mono=True, duration=30.0)
    tmp = os.path.join(tempfile.gettempdir(),
                       "m2o_id_" + _cache_key(audio_path) + ".wav")
    sf.write(tmp, y, sr)
    return tmp


def _extract(out: dict) -> dict:
    track = (out or {}).get("track") or {}
    artist = track.get("subtitle") or ""
    title = track.get("title") or ""
    lyrics: list[str] = []
    for sec in track.get("sections", []) or []:
        if sec.get("type") == "LYRICS" and sec.get("text"):
            lyrics = [ln for ln in sec["text"] if ln and ln.strip()]
            break
    genre = (track.get("genres") or {}).get("primary", "")
    return {"ok": bool(title or artist), "artist": artist, "title": title,
            "genre": genre, "lyrics": lyrics,
            "has_lyrics": bool(lyrics), "synced": False}


def identify(audio_path: str, *, use_cache: bool = True) -> dict:
    """Best-effort identify. Always returns a dict with an `ok` flag and,
    on failure, an `error` string - never raises."""
    if not os.path.isfile(audio_path):
        return {"ok": False, "error": "audio not found", "lyrics": []}
    if use_cache:
        c = _cached(audio_path)
        if c is not None:
            return c

    try:
        import asyncio

        from shazamio import Shazam
    except ImportError:
        return {"ok": False, "lyrics": [],
                "error": "shazamio not installed (pip install shazamio)"}

    async def _run() -> dict:
        shazam = Shazam()
        clip = _to_wav_snippet(audio_path)
        try:
            out = await shazam.recognize(clip)
        finally:
            try:
                os.remove(clip)
            except OSError:
                pass
        return _extract(out)

    try:
        try:
            res = asyncio.run(_run())
        except RuntimeError:                 # already inside a loop
            loop = asyncio.new_event_loop()
            try:
                res = loop.run_until_complete(_run())
            finally:
                loop.close()
    except Exception as e:                   # network / API / decode failure
        return {"ok": False, "lyrics": [],
                "error": f"recognition failed: {type(e).__name__}: {e}"}

    _store(audio_path, res)
    return res
