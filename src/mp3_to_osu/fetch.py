"""Optional helper: download audio from a YouTube/SoundCloud URL via yt-dlp.

Not for Spotify (DRM). yt-dlp is an optional dependency - install with
`pip install yt-dlp` and have ffmpeg on PATH for best results.
"""

from __future__ import annotations

import os
import shutil


def fetch_audio(url: str, out_dir: str) -> str:
    """Download `url`'s audio into out_dir and return the saved file path."""
    try:
        import yt_dlp  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "yt-dlp is not installed. Run `pip install yt-dlp` (and install "
            "ffmpeg) to use --url, or download the audio manually."
        ) from e

    from yt_dlp import YoutubeDL

    os.makedirs(out_dir, exist_ok=True)
    have_ffmpeg = shutil.which("ffmpeg") is not None
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, "%(uploader)s - %(title)s.%(ext)s"),
        "quiet": True,
        "noplaylist": True,
    }
    if have_ffmpeg:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)

    if have_ffmpeg:
        path = os.path.splitext(path)[0] + ".mp3"
    if not os.path.isfile(path):
        raise RuntimeError(f"download finished but file not found: {path}")
    return path
