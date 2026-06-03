"""Sanity-check the .osu parser + feature extractor on a real library file."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from mp3_to_osu.learn.features import extract       # noqa: E402
from mp3_to_osu.learn.parse import parse_osu        # noqa: E402

SAMPLE = (r"F:\osu\Songs\10014 The Lonely Island ft Justin Timberlake "
          r"- Dick In A Box [no video]\The Lonely Island ft. Justin "
          r"Timberlake - Dick In A Box (Agent Spin Here) [Easy].osu")

m = parse_osu(SAMPLE)
assert m is not None, "parser returned None"
print("path     :", os.path.basename(m.path))
print("mode     :", m.mode, "(0 = standard)")
print("creator  :", repr(m.creator))
print("title    :", repr(m.title), "version", repr(m.version))
print("bpm      :", round(m.bpm, 2))
print("CS/AR/OD :", m.cs, m.ar, m.od, "SM", m.slider_multiplier)
print("objects  :", len(m.objects))

kinds = {}
for o in m.objects:
    kinds[o.kind] = kinds.get(o.kind, 0) + 1
print("by kind  :", kinds)

assert m.bpm > 0, "bpm not derived"
assert len(m.objects) > 0
times = [o.time for o in m.objects]
assert times == sorted(times), "objects not time-sorted"
sliders = [o for o in m.objects if o.kind == "slider"]
if sliders:
    s = sliders[0]
    assert s.duration > 0, "slider duration not computed"
    assert s.slider_len > 0, "slider length missing"
    print("slider[0]:", f"len={s.slider_len:.1f}px dur={s.duration:.1f}ms "
          f"curve={s.curve}")

f = extract(m)
assert f is not None, "feature extraction failed"
print("features :", f"circle={f.circle_ratio:.0%} slider={f.slider_ratio:.0%} "
      f"slope={f.spacing_slope:.2f} turn={f.mean_turn_deg:.0f}deg "
      f"opc={f.objects_per_combo:.1f}")
print("rhythm   :", {k: v for k, v in f.rhythm_hist.items() if v > 0})

print("\nPARSER + FEATURE EXTRACTION OK")
