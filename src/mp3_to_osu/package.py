"""Bundle one or more generated .osu difficulties + audio into an .osz."""

from __future__ import annotations

import os
import re
import shutil
import zipfile


def _safe(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", s).strip() or "track"


def build_osz(
    diffs: list[tuple[str, str]],   # (difficulty_name, osu_text)
    audio_src: str,
    out_dir: str,
    *,
    artist: str,
    title: str,
) -> str:
    """Write `<Artist> - <Title>.osz` (a full mapset) and an unzipped copy."""
    os.makedirs(out_dir, exist_ok=True)
    audio_name = os.path.basename(audio_src)
    set_name = _safe(f"{artist} - {title}")
    osz_path = os.path.join(out_dir, set_name + ".osz")

    with open(audio_src, "rb") as f:
        audio_bytes = f.read()

    with zipfile.ZipFile(osz_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(audio_name, audio_bytes)
        for diff_name, osu_text in diffs:
            z.writestr(_safe(f"{artist} - {title} ({diff_name}).osu"),
                       osu_text)

    folder = os.path.join(out_dir, set_name)
    os.makedirs(folder, exist_ok=True)
    for diff_name, osu_text in diffs:
        fn = _safe(f"{artist} - {title} ({diff_name}).osu")
        with open(os.path.join(folder, fn), "w", encoding="utf-8") as f:
            f.write(osu_text)
    dst_audio = os.path.join(folder, audio_name)
    if os.path.abspath(audio_src) != os.path.abspath(dst_audio):
        shutil.copy2(audio_src, dst_audio)

    return osz_path
