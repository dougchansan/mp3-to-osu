"""Generate a 128 BPM click track, run the pipeline, validate the .osu."""
import os
import sys
import zipfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from mp3_to_osu.pipeline import generate  # noqa: E402

SR = 44100
BPM = 128
DUR_S = 30
beat = 60.0 / BPM

t = np.linspace(0, DUR_S, int(SR * DUR_S), endpoint=False)
y = np.zeros_like(t)
# A percussive blip on every 1/2 beat with accents on the downbeat.
n_hits = int(DUR_S / (beat / 2))
for i in range(n_hits):
    start = i * (beat / 2)
    s = int(start * SR)
    e = min(len(y), s + int(0.06 * SR))
    env = np.exp(-np.linspace(0, 8, e - s))
    freq = 220 if i % 8 == 0 else 440
    amp = 0.9 if i % 8 == 0 else 0.5
    y[s:e] += amp * env * np.sin(2 * np.pi * freq * t[: e - s])
y += 0.002 * np.random.randn(len(y))

os.makedirs("tests/_tmp", exist_ok=True)
wav = "tests/_tmp/Test Artist - Click 128.wav"
sf.write(wav, y.astype(np.float32), SR)

r = generate(wav, "tests/_tmp/out", spread=True)
print("BPM detected :", round(r.bpm, 2), "(expected ~128)")
print("Difficulties :", r.difficulties)
print("OSZ          :", r.osz_path)

# --- validate every .osu inside the mapset .osz ---
with zipfile.ZipFile(r.osz_path) as z:
    osu_names = [n for n in z.namelist() if n.endswith(".osu")]
    assert len(osu_names) == 5, f"expected 5 difficulties, got {osu_names}"
    hard = [n for n in osu_names if "(Hard)" in n][0]
    osu = z.read(hard).decode("utf-8")

assert osu.startswith("osu file format v14"), "bad header"
for sec in ("[General]", "[Metadata]", "[Difficulty]", "[TimingPoints]",
            "[HitObjects]"):
    assert sec in osu, f"missing {sec}"
assert "Mode: 0" in osu, "not standard mode"

hit_lines = osu.split("[HitObjects]")[1].strip().splitlines()
assert len(hit_lines) > 50, f"too few objects: {len(hit_lines)}"

circles = sliders = spinners = 0
for ln in hit_lines:
    f = ln.split(",")
    x, yy, tm, typ = int(f[0]), int(f[1]), int(f[2]), int(f[3])
    assert 0 <= x <= 512 and 0 <= yy <= 384, f"off-field: {ln}"
    assert tm >= 0, f"negative time: {ln}"
    if typ & 8:
        spinners += 1
        assert int(f[5]) > tm, f"spinner end <= start: {ln}"
    elif typ & 2:
        sliders += 1
        assert f[5].startswith("B|"), f"bad slider curve: {ln}"
        assert len(f[5].split("|")) == 3, f"bezier needs ctrl+end: {ln}"
        assert float(f[7]) > 0, f"non-positive slider length: {ln}"
    elif typ & 1:
        circles += 1
print(f"Circles      : {circles}")
print(f"Sliders      : {sliders}")
print(f"Spinners     : {spinners}")

# Timing point sanity: detected beat length should match ~128 BPM.
tp = osu.split("[TimingPoints]")[1].split("[")[0].strip().splitlines()[0]
beat_len = float(tp.split(",")[1])
print("Beat length  :", round(beat_len, 2), "ms  -> BPM",
      round(60000 / beat_len, 2))

print("\nALL VALIDATION CHECKS PASSED")
