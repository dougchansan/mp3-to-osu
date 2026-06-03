"""Parse an osu! replay (.osr): extract key-press times + cursor path.

Used to compare a player's real hit timing against a beatmap's object times
(their personal/system offset and consistency). Best-effort and never raises
fatally - returns {"ok": False, "error": ...} on any problem.

.osr layout (osu! wiki): a binary header, then an LZMA-compressed replay
data string of "w|x|y|z" frames (w = ms since previous frame; z = key
bitmask: 1 M1, 2 M2, 4 K1, 8 K2, 16 Smoke).
"""

from __future__ import annotations

import lzma
import struct

_PLAY_KEYS = 1 | 2 | 4 | 8          # ignore Smoke (16)
_MOD_DT = 64
_MOD_HT = 256
_MOD_NC = 512


def _read_string(buf: bytes, i: int) -> tuple[str, int]:
    kind = buf[i]
    i += 1
    if kind == 0x00:
        return "", i
    if kind != 0x0b:
        return "", i
    shift = 0
    n = 0
    while True:                                  # ULEB128 length
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    s = buf[i:i + n].decode("utf-8", "ignore")
    return s, i + n


def _decompress(blob: bytes) -> str:
    for fmt in (lzma.FORMAT_AUTO, lzma.FORMAT_ALONE):
        try:
            d = lzma.LZMADecompressor(format=fmt)
            return d.decompress(blob).decode("ascii", "ignore")
        except Exception:
            continue
    # last resort: raw LZMA1 with osu's typical properties
    try:
        filt = [{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 21}]
        d = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filt)
        return d.decompress(blob).decode("ascii", "ignore")
    except Exception as e:
        raise RuntimeError(f"replay LZMA decode failed: {e}")


def parse_osr(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            buf = fh.read()
    except OSError as e:
        return {"ok": False, "error": f"cannot read .osr: {e}"}

    try:
        i = 0
        mode = buf[i]
        i += 1
        i += 4                                   # game version (int)
        _bm_md5, i = _read_string(buf, i)
        player, i = _read_string(buf, i)
        _rep_md5, i = _read_string(buf, i)
        n300, n100, n50, ngeki, nkatu, nmiss = struct.unpack_from(
            "<6H", buf, i)
        i += 12
        i += 4                                   # score (int)
        max_combo = struct.unpack_from("<H", buf, i)[0]
        i += 2
        i += 1                                   # perfect (byte)
        mods = struct.unpack_from("<i", buf, i)[0]
        i += 4
        _life, i = _read_string(buf, i)
        i += 8                                   # timestamp (long)
        rlen = struct.unpack_from("<i", buf, i)[0]
        i += 4
        blob = buf[i:i + rlen]
    except (IndexError, struct.error) as e:
        return {"ok": False, "error": f"bad .osr header: {e}"}

    if mode != 0:
        return {"ok": False,
                "error": "replay is not osu! standard mode"}

    try:
        data = _decompress(blob)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}

    # Speed mods: replay frame deltas are real elapsed ms; divide by the
    # rate to land back in beatmap time.
    rate = 1.5 if (mods & (_MOD_DT | _MOD_NC)) else \
        (0.75 if (mods & _MOD_HT) else 1.0)

    t = 0.0
    prev_keys = 0
    presses: list[float] = []
    frames: list[tuple[float, float, float]] = []
    for fr in data.split(","):
        if not fr:
            continue
        parts = fr.split("|")
        if len(parts) != 4:
            continue
        try:
            w = float(parts[0])
            x = float(parts[1])
            y = float(parts[2])
            z = int(float(parts[3]))
        except ValueError:
            continue
        if w == -12345:                          # RNG seed frame, skip
            continue
        t += w
        bt = t / rate
        frames.append((round(bt, 1), round(x, 1), round(y, 1)))
        keys = z & _PLAY_KEYS
        # rising edge: a key not held last frame is now pressed = one tap
        if keys & ~prev_keys:
            presses.append(round(bt, 1))
        prev_keys = keys

    if not presses:
        return {"ok": False,
                "error": "no key presses found in replay"}

    return {
        "ok": True,
        "player": player,
        "mods": mods,
        "rate": rate,
        "counts": {"300": n300, "100": n100, "50": n50,
                   "miss": nmiss, "combo": max_combo},
        "presses": presses,                      # beatmap-time ms
        "frames": frames,                        # cursor path (bt, x, y)
    }
